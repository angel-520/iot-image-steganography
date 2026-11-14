"""Image recovery API; long-running work uses the shared task queue."""
from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from istg.image_restoration import (
    MAX_CONTAINER_BYTES, MAX_SOURCE_BYTES, PNG_SIGNATURE, prepare_image, restore_image,
)
from .runtime import APIError

router = APIRouter(prefix="/api/restoration", tags=["Image restoration"])


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
