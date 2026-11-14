"""ISTG Studio 本机 FastAPI 入口，算法全部由现有 istg 包执行。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from io import StringIO
from pathlib import Path
from typing import Any
import csv
import json
import math
import os
import platform
import tempfile
from istg.media_limits import RESULT_BYTES

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .algorithms import algorithm_catalog
from .runtime import APIError, FileStore, MAX_UPLOAD, MAX_PIXELS, TaskManager, TTL_SECONDS, json_safe
from .schemas import (
    PayloadRequest, ExtractRequest, DetectRequest, BitplanesRequest, StatisticsRequest,
    QualityRequest, RecoveryRequest, BenchmarkRequest, RobustnessRequest, EvaluateRequest, CompareRequest,
)
from .services import StudioService
from .multimodal import router as multimodal_router
from .network_lab import router as network_lab_router
from .network_sessions import NetworkSessions
from .secret_video import Rooms, router as secret_video_router
from .restoration import router as restoration_router
from .copyright import CopyrightService, router as copyright_router


def request_body_limit(path: str) -> int:
    if path == '/api/copyright/verify':
        return 65 * 1024 * 1024
    if path == '/api/restoration/restore':
        return 97 * 1024 * 1024
    return (RESULT_BYTES if path == '/api/multimodal/uploads' else MAX_UPLOAD) + 1024 * 1024


class BodyLimitMiddleware:
    """在 multipart 解析前有界接收请求体，chunked 上传也受同样限制。"""

    def __init__(self, app, maximum: int = MAX_UPLOAD + 1024 * 1024):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        maximum = request_body_limit(scope.get('path', '')) if scope.get('path', '') in {'/api/multimodal/uploads', '/api/restoration/restore', '/api/copyright/verify'} else self.maximum
        # Large multipart requests spill to disk; never assemble a 512 MiB body in RAM.
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            size = 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                size += len(chunk)
                if size > maximum:
                    response = JSONResponse({'error': {'code': 'request_too_large',
                        'message': f'请求超过 {maximum // (1024 * 1024)} MiB 限制'}}, status_code=413)
                    return await response(scope, receive, send)
                body.write(chunk)
                if not message.get('more_body', False):
                    break
            body.seek(0)
            delivered = False
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    chunk = body.read(1024 * 1024)
                    more = body.tell() < size
                    delivered = not more
                    return {'type': 'http.request', 'body': chunk, 'more_body': more}
                return await receive()
            return await self.app(scope, bounded_receive, send)


def create_app(data_dir: str | Path | None = None, *, cleanup_interval: float = 60.0) -> FastAPI:
    """创建可独立测试的本地应用；临时目录由应用创建并在退出时清理。"""
    if isinstance(cleanup_interval, bool) or not isinstance(cleanup_interval, (int, float)) or not math.isfinite(cleanup_interval) or cleanup_interval <= 0:
        raise ValueError("cleanup_interval 必须是有限正数")
    parent = Path(data_dir) if data_dir is not None else Path(os.environ.get("ISTG_DATA_DIR", Path(__file__).resolve().parents[1] / ".studio-data"))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store = FileStore(parent)
        application.state.files = store
        application.state.tasks = TaskManager(store)
        application.state.service = StudioService(store)
        application.state.network_sessions = NetworkSessions(store)
        application.state.secret_video = Rooms()
        copyright_path = Path(os.environ.get('ISTG_COPYRIGHT_DIR') or (parent / 'copyright' if data_dir is not None else Path(__file__).resolve().parents[1] / '.copyright-data'))
        try:
            application.state.copyright = CopyrightService(copyright_path)
        except Exception:
            application.state.tasks.close()
            raise
        async def cleanup_periodically():
            while True:
                await asyncio.sleep(cleanup_interval)
                try:
                    await asyncio.to_thread(application.state.tasks.cleanup)
                except OSError:
                    # 外部手动删除过期文件不应永久停止后续周期清理。
                    continue

        sweeper = asyncio.create_task(cleanup_periodically(), name="istg-ttl-cleanup")
        try:
            yield
        finally:
            sweeper.cancel()
            try:
                await sweeper
            except asyncio.CancelledError:
                pass
            application.state.copyright.close()
            application.state.network_sessions.close()
            await application.state.secret_video.close()
            application.state.tasks.close()

    app = FastAPI(title="ISTG Studio", version="2.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"], allow_credentials=False)
    configured_hosts = [host.strip() for host in os.environ.get('ISTG_PUBLIC_HOSTS', '').split(',') if host.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver", *configured_hosts])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        trusted_origins = {"http://localhost:5173", "http://127.0.0.1:5173",
                           "http://localhost:8000", "http://127.0.0.1:8000"}
        # Python 启动入口允许自定义端口；仅增加当前本机服务自身的精确 Origin。
        if request.url.hostname in {"localhost", "127.0.0.1", *configured_hosts}:
            trusted_origins.add(f"{request.url.scheme}://{request.url.netloc}")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin is not None and origin not in trusted_origins:
            return JSONResponse({"error": {"code": "untrusted_origin", "message": "不允许来自其他网站的修改请求"}}, status_code=403)
        length = request.headers.get("content-length")
        if length is not None:
            try:
                too_large = int(length) > request_body_limit(request.url.path)
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse({"error": {"code": "request_too_large", "message": "请求超过此接口的上传大小限制"}}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # 不返回 Pydantic 的 input/body，防止密码或秘密文本被回显到日志/错误面板。
        fields = list(dict.fromkeys(".".join(str(part) for part in item["loc"] if part != "body") for item in exc.errors()))
        return JSONResponse({"error": {"code": "invalid_request", "message": "请求参数无效，请检查：" + "、".join(fields)}}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        message = str(exc)[:240]
        if hasattr(request.app.state, "tasks"):
            message = request.app.state.tasks.clean_message(exc)
        return JSONResponse({"error": {"code": "invalid_parameters", "message": message}}, status_code=422)

    @app.exception_handler(NotImplementedError)
    async def unsupported(request: Request, exc: NotImplementedError):
        return JSONResponse({"error": {"code": "unsupported", "message": "此算法或功能尚未实现"}}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return JSONResponse({"error": {"code": "internal_error", "message": "处理失败，请检查输入后重试。"}}, status_code=500)

    def submit(request: Request, kind: str, model: Any, operation: str):
        service = request.app.state.service
        def run(progress):
            try:
                return getattr(service, operation)(model, progress)
            finally:
                # 结果与导出从不保留口令；工作结束后主动释放模型中的秘密字段。
                for name in ("password", "confirm_password"):
                    if hasattr(model, name):
                        setattr(model, name, None)
                if hasattr(model, "params"):
                    model.params.key = None
        return request.app.state.tasks.submit(kind, run)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "name": "ISTG Studio", "version": "2.0", "python": platform.python_version(),
                "limits": {"upload_bytes": MAX_UPLOAD, "image_pixels": MAX_PIXELS,
                           "task_ttl_seconds": TTL_SECONDS, "concurrent_tasks": 1, "pending_tasks": 8,
                           "max_auto_attempts": 64}}

    @app.get("/api/algorithms")
    def algorithms():
        return {"algorithms": algorithm_catalog()}

    @app.post("/api/uploads")
    async def upload(request: Request, file: UploadFile = File(...), role: str = Form("cover")):
        if role not in {"cover", "secret", "stego", "reference", "dataset", "original"}:
            raise APIError("invalid_role", "上传文件用途无效", 422)
        content = bytearray()
        try:
            while chunk := await file.read(1024 * 1024):
                content.extend(chunk)
                if len(content) > MAX_UPLOAD:
                    raise APIError("file_too_large", "上传文件不能超过 16 MiB", 413)
            if not content:
                raise APIError("invalid_image", "上传图片不能为空", 422)
            return request.app.state.files.add(bytes(content), file.filename or "upload.png", "application/octet-stream", image=True).public()
        finally:
            await file.close()

    @app.get("/api/files/{identifier}")
    def download(identifier: str, request: Request, download: bool = Query(False)):
        item = request.app.state.files.get(identifier)
        return FileResponse(item.path, media_type=item.media_type, filename=item.name,
                            content_disposition_type="attachment" if download else "inline")

    @app.post("/api/capacity")
    def capacity(model: PayloadRequest, request: Request):
        return json_safe(request.app.state.service.capacity(model))

    @app.post("/api/embed", status_code=202)
    def embed(model: PayloadRequest, request: Request):
        return submit(request, "embed", model, "embed")

    @app.post("/api/extract", status_code=202)
    def extract(model: ExtractRequest, request: Request):
        return submit(request, "extract", model, "extract")

    @app.post("/api/analysis/auto-detect", status_code=202)
    def auto_detect(model: DetectRequest, request: Request):
        return submit(request, "auto-detect", model, "detect")

    @app.post("/api/analysis/bitplanes", status_code=202)
    def bitplanes(model: BitplanesRequest, request: Request):
        return submit(request, "bitplanes", model, "bitplanes")

    @app.post("/api/analysis/steganalysis", status_code=202)
    def steganalysis(model: StatisticsRequest, request: Request):
        return submit(request, "steganalysis", model, "statistics")

    @app.post("/api/analysis/quality", status_code=202)
    def quality(model: QualityRequest, request: Request):
        return submit(request, "quality", model, "quality")

    @app.post("/api/analysis/capacity", status_code=202)
    def analysis_capacity(model: PayloadRequest, request: Request):
        return request.app.state.tasks.submit("capacity", lambda progress: request.app.state.service.capacity(model))

    @app.post("/api/analysis/recovery", status_code=202)
    def recovery(model: RecoveryRequest, request: Request):
        return submit(request, "recovery", model, "recovery")

    @app.post("/api/analysis/benchmark", status_code=202)
    def benchmark(model: BenchmarkRequest, request: Request):
        return submit(request, "benchmark", model, "benchmark")

    @app.post("/api/analysis/robustness", status_code=202)
    def robustness(model: RobustnessRequest, request: Request):
        return submit(request, "robustness", model, "robustness")

    @app.post("/api/analysis/evaluate", status_code=202)
    def evaluate(model: EvaluateRequest, request: Request):
        return submit(request, "evaluate", model, "evaluate")

    @app.post("/api/compare", status_code=202)
    def compare(model: CompareRequest, request: Request):
        return submit(request, "compare", model, "compare")

    @app.get("/api/tasks/{identifier}")
    def task(identifier: str, request: Request):
        request.app.state.tasks.cleanup()
        return request.app.state.tasks.public(identifier)

    @app.delete("/api/tasks/{identifier}")
    def delete_task(identifier: str, request: Request):
        request.app.state.tasks.delete(identifier)
        return {"status": "deleted"}

    @app.get("/api/tasks/{identifier}/export")
    def export(identifier: str, request: Request, format: str = Query("json", pattern="^(json|csv)$")):
        task = request.app.state.tasks.public(identifier)
        if task["status"] != "completed":
            raise APIError("task_not_completed", "任务尚未完成，无法导出", 409)
        if format == "json":
            content = json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            return Response(content, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="istg_{identifier}.json"'})
        result = task["result"]
        rows = result.get("rows", result.get("results", [result]))
        output = StringIO(newline="")
        fields = ["algorithm", "status", "capacity_bytes", "packet_bytes", "mse", "psnr", "ssim", "embed_seconds", "extract_seconds", "exact_match", "ber", "error"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = {**row, **row.get("quality", {}), **row.get("timings", {}), **(row.get("recovery") or {}), "status": row.get("status")}
            writer.writerow({key: ("'" + str(value) if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value)
                             for key, value in flat.items() if key in fields})
        return Response(b"\xef\xbb\xbf" + output.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="istg_{identifier}.csv"'})

    app.include_router(multimodal_router)
    app.include_router(network_lab_router)
    app.include_router(secret_video_router)
    app.include_router(restoration_router)
    app.include_router(copyright_router)

    # API/docs 路由优先，构建后的前端可由同一个仅监听本机的进程提供。
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    @app.head("/{full_path:path}", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def static_frontend(full_path: str):
        if full_path.startswith("api/"):
            raise APIError("not_found", "接口不存在", 404)
        candidate = (frontend / full_path).resolve()
        if frontend.is_dir() and candidate.is_relative_to(frontend.resolve()) and candidate.is_file():
            # Windows 的文件关联可能将现代图片格式识别为 octet-stream。
            image_type = {".webp": "image/webp", ".avif": "image/avif"}.get(candidate.suffix.lower())
            return FileResponse(candidate, media_type=image_type)
        if (frontend / "index.html").is_file():
            return FileResponse(frontend / "index.html")
        return JSONResponse({"name": "ISTG Studio", "message": "前端开发入口：http://localhost:5173", "docs": "/docs"})

    return app


app = create_app()
