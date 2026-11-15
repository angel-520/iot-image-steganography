"""Pixel-domain RDH API and legacy recovery compatibility, using the shared queue."""
from __future__ import annotations

from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

import istg
from istg import rdh

from istg.image_restoration import (
    MAX_CONTAINER_BYTES, MAX_SOURCE_BYTES, PNG_SIGNATURE, prepare_image, restore_image,
)
from .runtime import APIError

router = APIRouter(prefix="/api/restoration", tags=["Image restoration"])
rdh_router = APIRouter(prefix="/api/rdh", tags=["Reversible data hiding"])
MAX_SECRET_BYTES = 4 * 1024 * 1024


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    jpeg_quality: int = Field(default=70, strict=True, ge=1, le=95)


@router.post("/prepare", status_code=202)
def prepare(model: PrepareRequest, request: Request):
    store = request.app.state.files
    source = store.get(model.image_id)
    if source.size_bytes > MAX_SOURCE_BYTES:
        raise APIError("file_too_large", "原图不能超过 16 MiB。", 413)

    def run(progress):
        progress(10, "正在生成压缩预览")
        result = prepare_image(source.path.read_bytes(), model.jpeg_quality)
        progress(85, "正在校验恢复结果")
        output, created = {}, []
        try:
            for name, filename, media_type in (
                ("source", "source-rgb.png", "image/png"),
                ("preview", "compressed-preview.jpg", "image/jpeg"),
                ("carrier", "recoverable-image.png", "image/png"),
                ("restored", "restored-image.png", "image/png"),
            ):
                saved = store.add(result[name], filename, media_type, image=True,
                                  maximum=MAX_CONTAINER_BYTES)
                created.append(saved.id)
                output[name] = saved.public()
        except Exception:
            for identifier in reversed(created):
                store.remove(identifier)
            raise
        output["metrics"] = result["metrics"]
        return output

    return request.app.state.tasks.submit("image-restoration-prepare", run)


@router.post("/restore", status_code=202)
async def restore(request: Request, file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_CONTAINER_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_CONTAINER_BYTES:
        raise APIError("file_too_large", "恢复图像不能超过 96 MiB。", 413)
    if not data.startswith(PNG_SIGNATURE):
        raise APIError("invalid_image", "请上传包含恢复信息的 PNG 图像。", 422)

    def run(progress):
        progress(15, "正在读取恢复信息")
        result = restore_image(data)
        progress(85, "正在生成恢复图像")
        output = request.app.state.files.add(
            result["restored"], "restored-image.png", "image/png",
            image=True, maximum=MAX_CONTAINER_BYTES,
        )
        return {"restored": output.public(), "metrics": result["metrics"]}

    return request.app.state.tasks.submit("image-restoration-restore", run)


class RDHRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    text: str | None = Field(default=None, max_length=262144)
    secret_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    encryption: Literal["none", "AES-256-GCM", "ChaCha20-Poly1305"] = "none"
    password: SecretStr | None = Field(default=None, max_length=1024)
    confirm_password: SecretStr | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def one_secret(self):
        if (self.text is None) == (self.secret_id is None) or self.text == "":
            raise ValueError("请选择秘密文本或秘密文件，且不能同时提供两者。")
        return self


def _rdh_checked(operation):
    try:
        return operation()
    except rdh.RDHError as exc:
        raise APIError(exc.code, str(exc), 422) from exc


def _rdh_packet(model, store, *, probe=False):
    if model.text is not None:
        raw = model.text.encode("utf-8")
        packet = istg.pack_istg("text", model.text)
    else:
        secret = store.get(model.secret_id)
        if secret.size_bytes > MAX_SECRET_BYTES:
            raise APIError("file_too_large", "秘密文件不能超过 4 MiB。", 413)
        raw = secret.path.read_bytes()
        packet = istg.pack_binary(raw, secret.name, max_bytes=MAX_SECRET_BYTES)
    if model.encryption != "none":
        password = model.password.get_secret_value() if model.password else ""
        if not probe and (not password or not model.confirm_password or model.confirm_password.get_secret_value() != password):
            raise APIError("password_mismatch", "启用加密时请填写一致的非空密码。", 422)
        packet = istg.encrypt_istg(packet, "RDH-capacity-only-probe" if probe else password, model.encryption)
    if len(packet) > rdh.MAX_PACKET_BYTES:
        raise APIError("file_too_large", "封装后的秘密数据包超过 RDH 大小限制。", 413)
    return packet, raw


def _rdh_decode(packet, password=None):
    parsed = istg.parse_istg(packet)
    encrypted = parsed["encrypted"]
    if encrypted:
        if not password:
            raise APIError("password_required", "秘密数据已加密，请输入解密口令。", 422)
        try:
            parsed = istg.parse_istg(istg.decrypt_istg(packet, password))
        except ValueError as exc:
            raise APIError("authentication_failed", "解密失败：口令错误或认证数据损坏。", 422) from exc
    payload = parsed["payload_data"]
    if payload["kind"] not in {"text", "file"}:
        raise APIError("unsupported_payload", "RDH 数据包不是支持的文本或文件。", 422)
    return payload, encrypted


def _rdh_source(request, identifier):
    source = request.app.state.files.get(identifier)
    if source.size_bytes > MAX_SOURCE_BYTES or not source.format:
        raise APIError("invalid_image", "请选择不超过 16 MiB 的有效原始图像。", 422)
    return source


@rdh_router.post("/uploads")
async def upload_secret(request: Request, file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_SECRET_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_SECRET_BYTES:
        raise APIError("file_too_large", "秘密文件不能超过 4 MiB。", 413)
    return request.app.state.files.add(data, file.filename or "secret.bin", "application/octet-stream",
                                       maximum=MAX_SECRET_BYTES).public()


@rdh_router.post("/capacity", status_code=202)
def rdh_capacity(model: RDHRequest, request: Request):
    source = _rdh_source(request, model.image_id)
    def run(progress):
        try:
            progress(15, "正在计算真实 ISTG 开销与安全像素容量")
            packet, raw = _rdh_packet(model, request.app.state.files, probe=True)
            result = _rdh_checked(lambda: rdh.capacity(source.path.read_bytes(), len(packet), len(raw)))
            result["encryption"] = model.encryption
            return result
        finally:
            model.password = model.confirm_password = model.text = None
    return request.app.state.tasks.submit("rdh-capacity", run)


@rdh_router.post("/embed", status_code=202)
def rdh_embed(model: RDHRequest, request: Request):
    source = _rdh_source(request, model.image_id)
    if model.encryption != "none":
        password = model.password.get_secret_value() if model.password else ""
        if not password or not model.confirm_password or model.confirm_password.get_secret_value() != password:
            raise APIError("password_mismatch", "启用加密时请填写一致的非空密码。", 422)
    def run(progress):
        created = []
        store = request.app.state.files
        try:
            progress(10, "正在封装秘密数据")
            packet, raw = _rdh_packet(model, store)
            decoded, _ = _rdh_decode(packet, model.password.get_secret_value() if model.password else None)
            if decoded["data"] != raw:
                raise APIError("verification_failed", "秘密数据封装往返校验失败。", 422)
            result = _rdh_checked(lambda: rdh.embed_image(source.path.read_bytes(), packet, secret_bytes=len(raw), progress=progress))
            # The algorithm has reread the PNG and compared the extracted packet
            # byte-for-byte. The original packet was also decoded and compared
            # to the raw secret, so verification covers actual secret bytes.
            if not result["metrics"]["packet_verified"] or not result["metrics"]["rgb_verified"]:
                raise APIError("verification_failed", "RDH 完整验证未通过。", 422)
            output = {}
            for key, name in (("source", "original-rgb.png"), ("stego", "rdh-image.png")):
                saved = store.add(result[key], name, "image/png", image=True, maximum=MAX_CONTAINER_BYTES)
                created.append(saved.id); output[key] = saved.public()
            result["metrics"].update(secret_verified=True, secret_sha256=sha256(raw).hexdigest(), encryption=model.encryption)
            output["metrics"] = result["metrics"]
            return output
        except Exception:
            for identifier in reversed(created): store.remove(identifier)
            raise
        finally:
            model.password = model.confirm_password = model.text = None
    return request.app.state.tasks.submit("rdh-embed", run)


@rdh_router.post("/extract", status_code=202)
async def rdh_extract(request: Request, file: UploadFile = File(...), password: SecretStr | None = Form(None)):
    try:
        data = await file.read(MAX_CONTAINER_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_CONTAINER_BYTES:
        raise APIError("file_too_large", "RDH PNG 不能超过 96 MiB。", 413)
    if not data.startswith(PNG_SIGNATURE):
        raise APIError("invalid_image", "请上传包含完整辅助数据的 RDH PNG。", 422)
    secret = [password]
    def run(progress):
        store, created = request.app.state.files, []
        try:
            progress(15, "正在从像素提取数据并逆向恢复 RGB")
            result = _rdh_checked(lambda: rdh.extract_image(data))
            progress(75, "正在校验 ISTG 数据包及解密秘密数据")
            payload, encrypted = _rdh_decode(result["packet"], secret[0].get_secret_value() if secret[0] else None)
            raw, kind = payload["data"], payload["kind"]
            output = {}
            for key, content, name, mime, image in (
                ("restored", result["restored"], "rdh-restored-rgb.png", "image/png", True),
                ("secret", raw, "recovered-secret.txt" if kind == "text" else payload["filename"], "application/octet-stream", False),
            ):
                saved = store.add(content, name, mime, image=image, maximum=MAX_CONTAINER_BYTES)
                created.append(saved.id); output[key] = saved.public()
            metrics = result["metrics"]
            metrics.update(secret_verified=True, secret_sha256=sha256(raw).hexdigest(), secret_bytes=len(raw), encrypted=encrypted,
                           stego_bytes=len(data), restored_png_bytes=len(result["restored"]))
            output.update(metrics=metrics, data_type=kind)
            if kind == "text": output["text"] = raw.decode("utf-8")
            return output
        except Exception:
            for identifier in reversed(created): store.remove(identifier)
            raise
        finally:
            secret.clear()
    return request.app.state.tasks.submit("rdh-extract", run)
