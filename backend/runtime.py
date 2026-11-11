"""短期文件与任务存储；标识独立于服务器路径，不落盘保存任务配置。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, RLock
from time import monotonic
from typing import Any, Callable
from uuid import uuid4
import json
import math
import os
import re
import shutil
import stat
import tempfile
import warnings

import numpy as np
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000
TTL_SECONDS = 30 * 60
MAX_STORED_BYTES = 4 * 1024 * 1024 * 1024
MAX_FILES = 192
_TASK_OWNER: ContextVar[str | None] = ContextVar("istg_task_owner", default=None)
OWNER_MARKER = ".owner.json"


def _process_alive(pid: int) -> bool:
    """确认 PID 是否仍活跃；无权限或状态未知时按活跃处理以保留目录。"""
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: PID 不存在。
        try:
            status = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
                return True
            return status.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_reparse(path: Path) -> bool:
    information = path.lstat()
    return stat.S_ISLNK(information.st_mode) or bool(
        getattr(information, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _safe_orphan_tree(root: Path) -> bool:
    """有界检查整个待删除子树，遇到链接或重解析点就保留。"""
    pending, entries = [root], 0
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as children:
            for child in children:
                entries += 1
                if entries > 10000:
                    return False
                path = Path(child.path)
                if _is_reparse(path):
                    return False
                if child.is_dir(follow_symlinks=False):
                    pending.append(path)
    return True


def cleanup_orphaned_stores(parent: Path) -> None:
    """启动时仅删除配置父目录中有有效 owner 标记且进程已死亡的私有目录。"""
    base = parent.resolve()
    for candidate in parent.iterdir():
        try:
            if not re.fullmatch(r"istg_studio_[a-zA-Z0-9_-]{6,32}", candidate.name):
                continue
            if not candidate.is_dir() or _is_reparse(candidate):
                continue
            resolved = candidate.resolve()
            if resolved.parent != base or not resolved.is_relative_to(base):
                continue
            marker = candidate / OWNER_MARKER
            if not marker.is_file() or _is_reparse(marker) or marker.stat().st_size > 4096:
                continue
            owner = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(owner, dict) or owner.get("application") != "ISTG Studio":
                continue
            pid = owner.get("pid")
            if owner.get("schema") != 1 or isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 2**32:
                continue
            if not _process_alive(pid) and _safe_orphan_tree(resolved):
                shutil.rmtree(resolved)
        except (OSError, ValueError, TypeError):
            # 不可验证的标记、目录和进程状态不构成删除授权。
            continue


class APIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity" if value < 0 else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items() if key not in {"password", "confirm_password", "key"}}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, bytes):
        return {"byte_length": len(value)}
    return value


@dataclass
class StoredFile:
    id: str
    path: Path
    name: str
    media_type: str
    size_bytes: int
    created: float
    width: int | None = None
    height: int | None = None
    format: str | None = None
    task_id: str | None = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "url": f"/api/files/{self.id}",
                "download_url": f"/api/files/{self.id}?download=true", "name": self.name,
                "media_type": self.media_type, "size_bytes": self.size_bytes,
                "width": self.width, "height": self.height, "format": self.format}


class FileStore:
    def __init__(self, parent: Path | None = None):
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
            cleanup_orphaned_stores(parent)
        self.root = Path(tempfile.mkdtemp(prefix="istg_studio_", dir=parent)).resolve()
        (self.root / OWNER_MARKER).write_text(json.dumps({"application": "ISTG Studio", "schema": 1, "pid": os.getpid()}), encoding="utf-8")
        self.files: dict[str, StoredFile] = {}
        self.lock = RLock()

    def safe_name(self, name: str) -> str:
        base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
        base = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", base).strip(" .")
        device = base.split(".", 1)[0].strip(" .").upper()
        if device in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            base = "_" + base
        return base[:120] or "result.bin"

    def add(self, content: bytes, name: str, media_type: str, *, image: bool = False, maximum: int = MAX_UPLOAD * 2) -> StoredFile:
        if len(content) > maximum:
            raise APIError("file_too_large", f"文件超过 {maximum // (1024 * 1024)} MiB 限制", 413)
        width = height = None
        fmt = None
        if image:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(BytesIO(content)) as picture:
                        width, height = picture.size
                        fmt = picture.format
                        if fmt not in {"PNG", "JPEG", "BMP", "WEBP", "TIFF"}:
                            raise ValueError("不支持此图片格式")
                        if width * height > MAX_PIXELS or min(width, height) <= 0:
                            raise ValueError("图片不能超过 1600 万像素")
                        if getattr(picture, "n_frames", 1) != 1:
                            raise ValueError("仅支持单帧图片")
                        picture.load()
                        media_type = Image.MIME.get(fmt, "application/octet-stream")
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
                    Image.DecompressionBombError) as exc:
                raise APIError("invalid_image", "图片损坏、格式不支持或超过像素限制", 422) from exc
        with self.lock:
            self.cleanup()
            if len(self.files) >= MAX_FILES or sum(item.size_bytes for item in self.files.values()) + len(content) > MAX_STORED_BYTES:
                raise APIError("storage_full", "临时结果过多，请清理任务或重启服务", 429)
            identifier = uuid4().hex
            directory = self.root / identifier
            directory.mkdir()
            safe = self.safe_name(name)
            path = directory / safe
            path.write_bytes(content)
            result = StoredFile(identifier, path, safe, media_type, len(content), monotonic(), width, height, fmt, _TASK_OWNER.get())
            self.files[identifier] = result
            return result

    def add_image(self, array: np.ndarray, name: str) -> StoredFile:
        buffer = BytesIO()
        Image.fromarray(array.astype(np.uint8)).save(buffer, format="PNG")
        return self.add(buffer.getvalue(), name, "image/png", image=True)

    def add_path(self, source: Path, name: str, media_type: str, *, maximum: int) -> StoredFile:
        """Move a server-owned temporary file into storage without loading it into RAM."""
        source = Path(source).resolve()
        if not source.is_relative_to(self.root) or not source.is_file() or source.is_symlink():
            raise APIError('invalid_file', '只能保存当前会话的临时文件', 422)
        size = source.stat().st_size
        if size > maximum:
            raise APIError('file_too_large', f'文件超过 {maximum // (1024 * 1024)} MiB 限制', 413)
        with self.lock:
            self.cleanup()
            if len(self.files) >= MAX_FILES or sum(item.size_bytes for item in self.files.values()) + size > MAX_STORED_BYTES:
                raise APIError('storage_full', '临时存储空间不足，请清理任务或重启服务', 429)
            identifier = uuid4().hex
            directory = self.root / identifier
            directory.mkdir()
            safe = self.safe_name(name)
            path = directory / safe
            try:
                source.replace(path)
            except OSError:
                directory.rmdir()
                raise
            result = StoredFile(identifier, path, safe, media_type, size, monotonic(), task_id=_TASK_OWNER.get())
            self.files[identifier] = result
            return result

    def get(self, identifier: str) -> StoredFile:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise APIError("file_not_found", "文件标识无效或已过期", 404)
        with self.lock:
            item = self.files.get(identifier)
            if item is None or not item.path.is_file() or monotonic() - item.created > TTL_SECONDS:
                raise APIError("file_not_found", "文件不存在或已过期，请重新上传", 404)
            if not item.path.resolve().is_relative_to(self.root):
                raise APIError("file_not_found", "文件标识无效", 404)
            item.created = monotonic()
            return item

    def remove(self, identifier: str) -> None:
        with self.lock:
            item = self.files.pop(identifier, None)
            if item and item.path.resolve().is_relative_to(self.root):
                item.path.unlink(missing_ok=True)
                item.path.parent.rmdir()

    def cleanup(self) -> None:
        with self.lock:
            expired = [key for key, item in self.files.items() if monotonic() - item.created > TTL_SECONDS]
            for key in expired:
                self.remove(key)

    def close(self) -> None:
        # root 是本实例随机创建且已解析的目录，绝不接受请求中的路径。
        with self.lock:
            if self.root.name.startswith("istg_studio_"):
                shutil.rmtree(self.root, ignore_errors=True)
            self.files.clear()

    def remove_owned(self, task_id: str) -> None:
        with self.lock:
            for identifier in [key for key, item in self.files.items() if item.task_id == task_id]:
                self.remove(identifier)


class TaskManager:
    def __init__(self, store: FileStore):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="istg-work")
        self.pending = BoundedSemaphore(8)
        self.tasks: dict[str, dict[str, Any]] = {}
        self.lock = RLock()

    def clean_message(self, exc: Exception) -> str:
        if isinstance(exc, (FileNotFoundError, OSError)):
            return "文件无法读取或已过期，请重新上传"
        text = str(exc).replace(str(self.store.root), "[临时目录]")
        return text[:240] or "处理失败，请检查图片和参数"

    def submit(self, kind: str, operation: Callable[[Callable[[int, str], None]], Any]) -> dict[str, Any]:
        if not self.pending.acquire(blocking=False):
            raise APIError("queue_full", "任务队列已满，请稍后重试", 429)
        identifier = uuid4().hex
        with self.lock:
            self.cleanup()
            self.tasks[identifier] = {"id": identifier, "kind": kind, "status": "queued",
                                      "progress": 0, "message": "等待处理", "result": None,
                                      "error": None, "created": monotonic()}
        self.executor.submit(self._run, identifier, operation)
        return self.public(identifier)

    def _run(self, identifier: str, operation: Callable) -> None:
        owner_token = _TASK_OWNER.set(identifier)
        def progress(value: int, message: str) -> None:
            with self.lock:
                self.tasks[identifier].update(progress=value, message=message)
        with self.lock:
            self.tasks[identifier].update(status="running", progress=5, message="正在处理")
        try:
            result = json_safe(operation(progress))
            with self.lock:
                self.tasks[identifier].update(status="completed", progress=100, message="处理完成", result=result)
        except Exception as exc:
            code = exc.code if isinstance(exc, APIError) else "unsupported" if isinstance(exc, NotImplementedError) else "processing_failed"
            message = exc.message if isinstance(exc, APIError) else self.clean_message(exc)
            with self.lock:
                self.tasks[identifier].update(status="failed", progress=100, message=message,
                                              error={"code": code, "message": message})
        finally:
            with self.lock:
                self.tasks[identifier]["created"] = monotonic()
            self.pending.release()
            _TASK_OWNER.reset(owner_token)

    def delete(self, identifier: str) -> None:
        with self.lock:
            item = self.tasks.get(identifier)
            if item is None:
                raise APIError("task_not_found", "任务不存在或已过期", 404)
            if item["status"] in {"queued", "running"}:
                raise APIError("task_busy", "正在运行的任务不能删除，请等待完成", 409)
            self.tasks.pop(identifier)
        self.store.remove_owned(identifier)

    def public(self, identifier: str) -> dict[str, Any]:
        self.cleanup()
        with self.lock:
            item = self.tasks.get(identifier)
            if item is None:
                raise APIError("task_not_found", "任务不存在或已过期", 404)
            return {**{key: value for key, value in item.items() if key != "created"},
                    "export_url": f"/api/tasks/{identifier}/export"}

    def cleanup(self) -> None:
        with self.lock:
            for key in [key for key, item in self.tasks.items()
                        if item["status"] in {"completed", "failed"} and monotonic() - item["created"] > TTL_SECONDS]:
                self.tasks.pop(key, None)
        self.store.cleanup()

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
        self.tasks.clear()
        self.store.close()
