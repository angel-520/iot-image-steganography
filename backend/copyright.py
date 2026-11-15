"""Persistent, authenticated copyright records and private image delivery."""
from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import RLock
import json
import math
import os
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from istg.copyright import canonical, embed_identifier, image_rgb, png_bytes, verify_identifier
from .runtime import APIError, MAX_UPLOAD

COOKIE = "istg_copyright_session"
COOKIE_PATH = "/api/copyright"
SESSION_SECONDS = 8 * 3600
MAX_VERIFY_BYTES = 64 * 1024 * 1024
MAX_PRIVATE_BYTES = 1024 * 1024 * 1024
router = APIRouter(prefix=COOKIE_PATH, tags=["copyright"])


def _clean(value: str, maximum: int, label: str, *, required: bool = True, multiline: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise APIError("invalid_input", f"{label}不能超过 {maximum} 个字符", 422)
    value = value.strip()
    if multiline:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    allowed = {"\n", "\t"} if multiline else set()
    if (required and not value) or any((ord(c) < 32 and c not in allowed) or ord(c) == 127 for c in value):
        raise APIError("invalid_input", f"请填写有效的{label}", 422)
    return value


def _username(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{3,32}", value):
        raise APIError("invalid_username", "用户名需为 3–32 位字母、数字或下划线", 422)
    return value.lower()


def _safe_name(value: str) -> str:
    value = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", value).strip(" .")[:100] or "image.png"
    if value.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    return value


class CopyrightService:
    """SQLite stores identities/policies; every original and export stays encrypted on disk."""
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RLock()
        if (self.directory / "copyright.sqlite3").exists() and any(
                not (self.directory / name).is_file() for name in ("content.key", "identifier.key")):
            raise RuntimeError("现有版权数据库缺少密钥文件，请恢复原有密钥")
        self.master = self._secret("content.key")
        self.signer = Ed25519PrivateKey.from_private_bytes(self._secret("identifier.key"))
        self.db = sqlite3.connect(self.directory / "copyright.sqlite3", check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
                expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS works(
                id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,
                author TEXT NOT NULL,rights TEXT NOT NULL,filename TEXT NOT NULL,mime TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,
                original_sha256 TEXT NOT NULL,stored_bytes INTEGER NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS marks(
                id TEXT PRIMARY KEY,work_id TEXT NOT NULL REFERENCES works(id),recipient TEXT NOT NULL,
                metadata TEXT NOT NULL,stored_bytes INTEGER NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(
                id TEXT PRIMARY KEY,work_id TEXT NOT NULL REFERENCES works(id),
                user_id TEXT NOT NULL REFERENCES users(id),expires_at REAL,can_download INTEGER NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,UNIQUE(work_id,user_id));
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,work_id TEXT NOT NULL REFERENCES works(id),
                actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,
                details TEXT NOT NULL,created_at REAL NOT NULL);
        """)
        self.db.commit()
        self.login_attempts = defaultdict(deque)

    def _secret(self, filename: str) -> bytes:
        path = self.directory / filename
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.is_symlink():
                raise RuntimeError("版权密钥文件无效")
            content = path.read_bytes()
        else:
            content = secrets.token_bytes(32)
            with os.fdopen(fd, "wb") as target:
                target.write(content)
        if len(content) != 32:
            raise RuntimeError("版权密钥文件无效")
        return content

    def close(self):
        with self.lock:
            self.db.close()

    def _event(self, user, work_id: str, action: str, details: str = ""):
        self.db.execute("INSERT INTO events(work_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?)",
                        (work_id, user["id"], action, details[:1000], time.time()))

    def _hash_password(self, password: str, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))

    def _session(self, user: dict) -> tuple[dict, str]:
        token = secrets.token_urlsafe(32)
        self.db.execute("DELETE FROM sessions WHERE expires_at<=?", (time.time(),))
        self.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash IN "
                        "(SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires_at DESC LIMIT -1 OFFSET 19)",
                        (user["id"], user["id"]))
        self.db.execute("INSERT INTO sessions VALUES(?,?,?)",
                        (sha256(token.encode("ascii")).hexdigest(), user["id"], time.time() + SESSION_SECONDS))
        return user, token

    def register(self, username: str, password: str) -> tuple[dict, str]:
        username = _username(username)
        if not isinstance(password, str) or not 10 <= len(password) <= 128 or not password.strip():
            raise APIError("invalid_password", "密码需为 10–128 个字符", 422)
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise APIError("username_taken", "此用户名已被使用", 409)
            if self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 500:
                raise APIError("account_limit", "账户数量已达上限", 429)
            salt = secrets.token_bytes(16)
            user = {"id": uuid4().hex, "username": username}
            self.db.execute("INSERT INTO users VALUES(?,?,?,?,?)",
                            (user["id"], username, salt, self._hash_password(password, salt), time.time()))
            return self._session(user)

    def login(self, username: str, password: str) -> tuple[dict, str]:
        username = _username(username)
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise APIError("authentication_failed", "用户名或密码不正确", 401)
        with self.lock, self.db:
            now = time.time()
            for name, failures in list(self.login_attempts.items()):
                while failures and now - failures[0] > 60:
                    failures.popleft()
                if not failures:
                    self.login_attempts.pop(name, None)
            if username not in self.login_attempts and len(self.login_attempts) >= 2048:
                oldest = min(self.login_attempts, key=lambda name: self.login_attempts[name][0])
                self.login_attempts.pop(oldest)
            attempts = self.login_attempts[username]
            if len(attempts) >= 5:
                raise APIError("login_limited", "尝试次数过多，请稍后再试", 429)
            row = self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            # Also derive for unknown accounts, keeping the main password path comparable.
            salt = bytes(row["salt"]) if row else b"\x00" * 16
            candidate = self._hash_password(password, salt)
            if row is None or not secrets.compare_digest(candidate, bytes(row["password_hash"])):
                attempts.append(now)
                raise APIError("authentication_failed", "用户名或密码不正确", 401)
            self.login_attempts.pop(username, None)
            return self._session({"id": row["id"], "username": row["username"]})

    def user(self, token: str | None, *, required: bool = True) -> dict | None:
        with self.lock, self.db:
            row = None
            if isinstance(token, str) and 32 <= len(token) <= 128:
                digest = sha256(token.encode("utf-8")).hexdigest()
                row = self.db.execute("SELECT users.id,users.username FROM sessions JOIN users ON users.id=sessions.user_id "
                                      "WHERE sessions.token_hash=? AND sessions.expires_at>?", (digest, time.time())).fetchone()
            if row:
                return dict(row)
        if required:
            raise APIError("login_required", "请先登录版权管理账户", 401)
        return None

    def logout(self, token: str | None):
        if isinstance(token, str) and len(token) <= 128:
            with self.lock, self.db:
                self.db.execute("DELETE FROM sessions WHERE token_hash=?", (sha256(token.encode("utf-8")).hexdigest(),))

    def _work(self, work_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", work_id):
            raise APIError("work_not_found", "作品不存在", 404)
        row = self.db.execute("SELECT works.*,users.username AS owner_username FROM works "
                              "JOIN users ON users.id=works.owner_id WHERE works.id=?", (work_id,)).fetchone()
        if row is None:
            raise APIError("work_not_found", "作品不存在", 404)
        return row

    def _access(self, row, user: dict) -> tuple[str, bool]:
        if row["owner_id"] == user["id"]:
            return "owner", True
        grant = self.db.execute("SELECT * FROM grants WHERE work_id=? AND user_id=?", (row["id"], user["id"])).fetchone()
        if grant is None:
            return "none", False
        if grant["revoked"]:
            return "revoked", False
        if grant["expires_at"] is not None and grant["expires_at"] <= time.time():
            return "expired", False
        return "active", bool(grant["can_download"])

    def _require(self, row, user: dict, *, manage: bool = False, download: bool = False):
        status, can_download = self._access(row, user)
        if (manage and status != "owner") or status not in {"owner", "active"} or (download and not can_download):
            raise APIError("access_denied", "当前账户没有此操作权限，或授权已失效", 403)

    def _public_work(self, row, user):
        access, can_download = self._access(row, user)
        return {key: row[key] for key in ("id", "title", "author", "rights", "filename", "mime", "size_bytes",
                                          "width", "height", "created_at", "original_sha256", "owner_username")} | {
            "can_manage": access == "owner", "can_download": can_download, "access_status": access,
            "content_url": f"{COOKIE_PATH}/works/{row['id']}/content"}

    def works(self, user: dict) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT DISTINCT works.*,users.username AS owner_username FROM works "
                                   "JOIN users ON users.id=works.owner_id LEFT JOIN grants ON grants.work_id=works.id "
                                   "WHERE works.owner_id=? OR grants.user_id=? ORDER BY works.created_at DESC",
                                   (user["id"], user["id"])).fetchall()
            return [self._public_work(row, user) for row in rows]

    def _quota(self, extra: int):
        used = self.db.execute("SELECT COALESCE(SUM(stored_bytes),0) FROM works").fetchone()[0]
        used += self.db.execute("SELECT COALESCE(SUM(stored_bytes),0) FROM marks").fetchone()[0]
        if used + extra > MAX_PRIVATE_BYTES:
            raise APIError("storage_full", "版权文件存储空间已满", 429)

    def _put(self, name: str, data: bytes, associated: bytes):
        nonce = secrets.token_bytes(12)
        content = nonce + AESGCM(self.master).encrypt(nonce, data, associated)
        path = self.directory / name
        target = path.open("xb")
        try:
            with target:
                if target.write(content) != len(content):
                    raise OSError("Incomplete encrypted file write")
        except BaseException:
            # open('xb') succeeded, so this call owns the newly created path.
            # Remove it even when writing or closing the file failed. An existing
            # path raises before this try block and must never be deleted here.
            path.unlink(missing_ok=True)
            raise
        return len(content)

    def _get(self, name: str, associated: bytes) -> bytes:
        try:
            content = (self.directory / name).read_bytes()
            return AESGCM(self.master).decrypt(content[:12], content[12:], associated)
        except (OSError, InvalidTag, ValueError):
            raise APIError("content_unavailable", "作品文件无法通过完整性校验", 422) from None

    def create_work(self, user: dict, data: bytes, filename: str, title: str, author: str, rights: str) -> dict:
        if not data or len(data) > MAX_UPLOAD:
            raise APIError("file_too_large", "原始图片不能超过 16 MiB", 413)
        title, author, rights = _clean(title, 100, "作品名称"), _clean(author, 100, "作者"), _clean(rights, 1000, "权利声明", multiline=True)
        try:
            array = image_rgb(data)
        except ValueError as exc:
            raise APIError("invalid_image", str(exc), 422) from exc
        preview = png_bytes(array)
        with Image.open(BytesIO(data)) as image:
            mime = Image.MIME.get(image.format, "application/octet-stream")
        identifier = uuid4().hex
        names = [identifier + ".source.bin", identifier + ".preview.bin"]
        with self.lock, self.db:
            if self.db.execute("SELECT COUNT(*) FROM works WHERE owner_id=?", (user["id"],)).fetchone()[0] >= 100:
                raise APIError("work_limit", "每个账户最多保存 100 个作品", 429)
            self._quota(len(data) + len(preview) + 56)
            written = []
            try:
                stored = self._put(names[0], data, (identifier + ":source").encode()); written.append(names[0])
                stored += self._put(names[1], preview, (identifier + ":preview").encode()); written.append(names[1])
                self.db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (identifier, user["id"], title, author, rights, _safe_name(filename), mime, len(data),
                                 array.shape[1], array.shape[0], sha256(data).hexdigest(), stored, time.time()))
                self._event(user, identifier, "create", title)
            except Exception:
                for name in written:
                    (self.directory / name).unlink(missing_ok=True)
                raise
            return self._public_work(self._work(identifier), user)

    def content(self, user: dict, work_id: str, *, download: bool = False) -> tuple[bytes, str, str]:
        with self.lock, self.db:
            row = self._work(work_id)
            self._require(row, user, download=download)
            suffix = "source" if download else "preview"
            data = self._get(work_id + "." + suffix + ".bin", (work_id + ":" + suffix).encode())
            self._event(user, work_id, "download" if download else "view")
            return data, row["mime"] if download else "image/png", row["filename"] if download else "preview.png"

    def _public_mark(self, row) -> dict:
        url = f"{COOKIE_PATH}/works/{row['work_id']}/marks/{row['id']}"
        return {"id": row["id"], "work_id": row["work_id"], "recipient": row["recipient"], "created_at": row["created_at"],
                "filename": f"copyright_{row['id'][:12]}.png", "url": url, "download_url": url + "?download=true",
                "metadata": json.loads(row["metadata"])}

    def marks(self, user, work_id):
        with self.lock:
            self._require(self._work(work_id), user, manage=True)
            return [self._public_mark(row) for row in self.db.execute("SELECT * FROM marks WHERE work_id=? ORDER BY created_at DESC", (work_id,))]

    def mark(self, user: dict, work_id: str, recipient: str = "") -> dict:
        recipient = _clean(recipient, 100, "接收者标识", required=False)
        with self.lock, self.db:
            row = self._work(work_id)
            self._require(row, user, manage=True)
            if self.db.execute("SELECT COUNT(*) FROM marks WHERE work_id=?", (work_id,)).fetchone()[0] >= 100:
                raise APIError("mark_limit", "每个作品最多生成 100 份标识文件", 429)
            original = self._get(work_id + ".preview.bin", (work_id + ":preview").encode())
            identifier = uuid4().hex
            details = {"work_id": work_id, "distribution_id": identifier, "title": row["title"],
                       "author": row["author"], "rights": row["rights"], "recipient": recipient, "issued_at": int(time.time())}
            try:
                marked, metadata = embed_identifier(original, details, self.signer)
            except ValueError as exc:
                raise APIError("embedding_failed", str(exc), 422) from exc
            checked = verify_identifier(marked, self.signer.public_key())
            if checked["status"] != "verified":
                raise APIError("embedding_failed", "版权标识校验失败", 422)
            self._quota(len(marked) + 28)
            name = identifier + ".mark.bin"
            self._put(name, marked, (identifier + ":mark:" + work_id).encode())
            try:
                self.db.execute("INSERT INTO marks VALUES(?,?,?,?,?,?)",
                                (identifier, work_id, recipient, canonical(metadata).decode(), len(marked) + 28, time.time()))
                self._event(user, work_id, "mark", recipient)
            except Exception:
                (self.directory / name).unlink(missing_ok=True)
                raise
            return self._public_mark(self.db.execute("SELECT * FROM marks WHERE id=?", (identifier,)).fetchone())

    def mark_content(self, user, work_id, mark_id, *, download=False):
        with self.lock, self.db:
            self._require(self._work(work_id), user, manage=True)
            if not re.fullmatch(r"[a-f0-9]{32}", mark_id):
                raise APIError("mark_not_found", "标识文件不存在", 404)
            row = self.db.execute("SELECT * FROM marks WHERE id=? AND work_id=?", (mark_id, work_id)).fetchone()
            if row is None:
                raise APIError("mark_not_found", "标识文件不存在", 404)
            data = self._get(mark_id + ".mark.bin", (mark_id + ":mark:" + work_id).encode())
            self._event(user, work_id, "export_mark" if download else "view_mark", mark_id)
            return data, "image/png", self._public_mark(row)["filename"]

    def verify(self, data: bytes) -> dict:
        try:
            result = verify_identifier(data, self.signer.public_key())
        except ValueError as exc:
            raise APIError("invalid_image", str(exc), 422) from exc
        if result["status"] == "verified":
            metadata = result["metadata"]
            with self.lock:
                row = self.db.execute("SELECT metadata FROM marks WHERE id=? AND work_id=?",
                                      (metadata["distribution_id"], metadata["work_id"])).fetchone()
                result["record_matched"] = row is not None and secrets.compare_digest(
                    row["metadata"].encode(), canonical(metadata))
            if not result["record_matched"]:
                result.update(status="invalid", message="未找到对应的分发记录")
            else:
                result["message"] = "版权标识、图像与分发记录匹配"
        return result

    def _public_grant(self, row) -> dict:
        return {"id": row["id"], "username": row["username"], "expires_at": row["expires_at"],
                "can_download": bool(row["can_download"]), "revoked": bool(row["revoked"]), "created_at": row["created_at"]}

    def grants(self, user, work_id) -> list[dict]:
        with self.lock:
            self._require(self._work(work_id), user, manage=True)
            return [self._public_grant(row) for row in self.db.execute(
                "SELECT grants.*,users.username FROM grants JOIN users ON users.id=grants.user_id WHERE work_id=? ORDER BY created_at DESC",
                (work_id,))]

    def grant(self, user, work_id, username, expires_at=None, can_download=False) -> dict:
        username = _username(username)
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                                      or not math.isfinite(expires_at) or not time.time() < expires_at <= time.time() + 366 * 86400):
            raise APIError("invalid_expiry", "有效期需晚于当前时间，且不超过一年", 422)
        if type(can_download) is not bool:
            raise APIError("invalid_permission", "下载权限参数无效", 422)
        with self.lock, self.db:
            row = self._work(work_id)
            self._require(row, user, manage=True)
            target = self.db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if target is None:
                raise APIError("user_not_found", "目标账户不存在，请先注册", 404)
            if target["id"] == user["id"]:
                raise APIError("invalid_grant", "作品所有者已拥有管理权限", 422)
            self.db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?,?) ON CONFLICT(work_id,user_id) DO UPDATE SET "
                            "expires_at=excluded.expires_at,can_download=excluded.can_download,revoked=0,created_at=excluded.created_at",
                            (uuid4().hex, work_id, target["id"], expires_at, int(can_download), 0, time.time()))
            self._event(user, work_id, "grant", username)
            return self._public_grant(self.db.execute(
                "SELECT grants.*,users.username FROM grants JOIN users ON users.id=grants.user_id WHERE work_id=? AND user_id=?",
                (work_id, target["id"])).fetchone())

    def revoke(self, user, work_id, grant_id):
        with self.lock, self.db:
            self._require(self._work(work_id), user, manage=True)
            row = self.db.execute("SELECT * FROM grants WHERE id=? AND work_id=?", (grant_id, work_id)).fetchone()
            if row is None:
                raise APIError("grant_not_found", "授权不存在", 404)
            self.db.execute("UPDATE grants SET revoked=1 WHERE id=?", (grant_id,))
            self._event(user, work_id, "revoke", grant_id)

    def audit(self, user, work_id):
        with self.lock:
            self._require(self._work(work_id), user, manage=True)
            return [dict(row) for row in self.db.execute(
                "SELECT events.id,events.action,events.details,events.created_at,users.username FROM events "
                "JOIN users ON users.id=events.actor_id WHERE events.work_id=? ORDER BY events.id DESC LIMIT 100", (work_id,))]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class MarkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(default="", max_length=100)


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    username: str = Field(min_length=3, max_length=32)
    expires_at: float | None = None
    can_download: bool = False


def _service(request: Request) -> CopyrightService:
    return request.app.state.copyright


def _user(request: Request):
    return _service(request).user(request.cookies.get(COOKIE))


def _cookie(request, response, token):
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path=COOKIE_PATH)


@router.post("/auth/register", status_code=201)
def register(model: Credentials, request: Request, response: Response):
    user, token = _service(request).register(model.username, model.password)
    _cookie(request, response, token)
    return {"user": user}


@router.post("/auth/login")
def login(model: Credentials, request: Request, response: Response):
    user, token = _service(request).login(model.username, model.password)
    _cookie(request, response, token)
    return {"user": user}


@router.get("/auth/me")
def me(request: Request):
    return {"user": _service(request).user(request.cookies.get(COOKIE), required=False)}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    _service(request).logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict",
                           secure=request.url.scheme == "https")
    return {"ok": True}


@router.get("/works")
def works(request: Request):
    return {"works": _service(request).works(_user(request))}


@router.post("/works", status_code=201)
def create_work(request: Request, file: UploadFile = File(...), title: str = Form(...),
                author: str = Form(...), rights: str = Form(...)):
    user = _user(request)
    data = file.file.read(MAX_UPLOAD + 1)
    return {"work": _service(request).create_work(user, data, file.filename or "image.png", title, author, rights)}


def _binary(data, mime, name, download=False):
    from urllib.parse import quote
    disposition = "attachment" if download else "inline"
    return BinaryResponse(data, media_type=mime, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name, safe='')}"})


@router.get("/works/{work_id}/content")
def content(work_id: str, request: Request, download: bool = False):
    return _binary(*_service(request).content(_user(request), work_id, download=download), download=download)


@router.get("/works/{work_id}/marks")
def marks(work_id: str, request: Request):
    return {"marks": _service(request).marks(_user(request), work_id)}


@router.post("/works/{work_id}/mark")
def mark(work_id: str, model: MarkInput, request: Request):
    return {"mark": _service(request).mark(_user(request), work_id, model.recipient)}


@router.get("/works/{work_id}/marks/{mark_id}")
def mark_content(work_id: str, mark_id: str, request: Request, download: bool = False):
    return _binary(*_service(request).mark_content(_user(request), work_id, mark_id, download=download), download=download)


@router.post("/verify")
def verify(request: Request, file: UploadFile = File(...)):
    data = file.file.read(MAX_VERIFY_BYTES + 1)
    if len(data) > MAX_VERIFY_BYTES:
        raise APIError("file_too_large", "标识图片不能超过 64 MiB", 413)
    return _service(request).verify(data)


@router.get("/works/{work_id}/grants")
def grants(work_id: str, request: Request):
    return {"grants": _service(request).grants(_user(request), work_id)}


@router.post("/works/{work_id}/grants")
def grant(work_id: str, model: GrantInput, request: Request):
    return {"grant": _service(request).grant(_user(request), work_id, model.username, model.expires_at, model.can_download)}


@router.post("/works/{work_id}/grants/{grant_id}/revoke")
def revoke(work_id: str, grant_id: str, request: Request):
    _service(request).revoke(_user(request), work_id, grant_id)
    return {"ok": True}


@router.get("/works/{work_id}/audit")
def audit(work_id: str, request: Request):
    return {"events": _service(request).audit(_user(request), work_id)}
