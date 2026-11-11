"""Additive multimodal API; image endpoints and their request schemas stay compatible."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal
import secrets
import subprocess
import tempfile
import uuid
import asyncio

import numpy as np
from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

import istg
from istg.istg_pack import pack_binary
from istg.security_analysis.packet import decode_cbor_object
from istg import multimodal as mm
from istg import media_stream as stream
from istg import media_limits as limits
import cbor2

from .runtime import APIError, MAX_UPLOAD

Modality = Literal["image", "audio", "video", "text"]
Algorithm = Literal["image_lsb", "audio_lsb", "audio_dwt", "video_lsb", "text_zero_width"]
DEFAULTS = {"image": "image_lsb", "audio": "audio_lsb", "video": "video_lsb", "text": "text_zero_width"}
MIMES = {"image": "image/png", "audio": "audio/wav", "video": "video/x-matroska", "text": "text/plain; charset=utf-8"}
SUFFIXES = {"image": ".png", "audio": ".wav", "video": ".mkv", "text": ".txt"}


class Carrier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=32, max_length=32)
    modality: Modality
    algorithm: Algorithm | None = None
    bit_depth: int = Field(default=1, ge=1, le=12, strict=True)

    @model_validator(mode="after")
    def algorithm_matches(self):
        if self.algorithm is None:
            self.algorithm = DEFAULTS[self.modality]
        if not self.algorithm.startswith(self.modality + "_"):
            raise ValueError("算法与载体类型不匹配")
        maximum = 12 if self.algorithm == 'audio_lsb' else 4 if self.modality in {'audio', 'video'} else 1
        if self.bit_depth > maximum:
            raise ValueError(f'当前算法嵌入位数应为 1–{maximum}')
        return self


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=262144)
    secret_id: str | None = Field(default=None, min_length=32, max_length=32)
    encryption: Literal["none", "AES-256-GCM", "ChaCha20-Poly1305"] = "none"
    password: SecretStr | None = Field(default=None, max_length=1024)
    confirm_password: SecretStr | None = Field(default=None, max_length=1024)
    compression: Literal['none', 'auto'] = 'none'

    @model_validator(mode="after")
    def one_payload(self):
        if (self.text is None) == (self.secret_id is None):
            raise ValueError("秘密文本与秘密文件必须且只能选择一个")
        if self.text == "":
            raise ValueError("秘密文本不能为空")
        return self


class EmbedRequest(Payload):
    carrier: Carrier


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrier: Carrier
    password: SecretStr | None = Field(default=None, max_length=1024)


class SplitRequest(Payload):
    carriers: list[Carrier] = Field(min_length=2, max_length=3)


class CombineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carriers: list[Carrier] = Field(min_length=2, max_length=3)
    password: SecretStr | None = Field(default=None, max_length=1024)


class AttackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrier: Carrier
    attack: Literal["noise", "resample", "compression", "crop", "strip_zero_width"]
    password: SecretStr | None = Field(default=None, max_length=1024)


class MultimodalService:
    def __init__(self, store):
        self.store = store

    def packet(self, model: Payload, *, probe=False):
        if model.text is not None:
            packet = istg.pack_istg("text", model.text)
        else:
            item = self.store.get(model.secret_id)
            if item.size_bytes > limits.SECRET_BYTES:
                raise ValueError('秘密文件不能超过 64 MiB')
            packet = pack_binary(item.path.read_bytes(), item.name, max_bytes=limits.SECRET_BYTES, compress=model.compression == 'auto')
        if model.encryption != "none":
            password = model.password.get_secret_value() if model.password else ""
            if not probe and (not password or not model.confirm_password or model.confirm_password.get_secret_value() != password):
                raise ValueError("启用加密时请填写一致的非空密码")
            packet = istg.encrypt_istg(packet, "capacity-probe" if probe else password, model.encryption)
        return packet

    def source(self, carrier):
        item = self.store.get(carrier.file_id)
        if carrier.modality == "image":
            if not item.format:
                raise ValueError("请选择通过验证的图像载体")
            return item, None, None
        if carrier.modality == "audio":
            info = stream.audio_info(item.path)
            return item, info, info['rate']
        if carrier.modality == "video":
            info = getattr(item, '_video_info', None)
            if info is None:
                info = stream.video_info(item.path)
            return item, info, info['fps']
        try:
            text = item.path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("文本载体需为 UTF-8 编码") from exc
        if not text:
            raise ValueError("文本载体不能为空")
        return item, text, None

    def capacity(self, carrier):
        item, data, _ = self.source(carrier)
        if carrier.modality == "image":
            return istg.get_adapter("sequential_lsb").capacity(item.path, {})
        if carrier.modality == "audio":
            slots = (data['count'] if carrier.algorithm == 'audio_lsb' else data['count'] // 2) * data['channels']
            return max(0, slots * carrier.bit_depth // 8 - mm.HEADER.size)
        if carrier.modality == "video":
            if 'count' not in data:
                data = stream.video_info(item.path, count=True)
                item._video_info = data
            return max(0, data['count'] * data['width'] * data['height'] * 3 * carrier.bit_depth // 8 - mm.HEADER.size)
        # Each byte becomes 8 three-byte Unicode codepoints, plus delimiters and cover.
        return max(0, (limits.TEXT_OUTPUT - len(data.encode("utf-8")) - 18) // 24 - mm.HEADER.size)

    def extract_packet(self, carrier):
        item, data, _ = self.source(carrier)
        if carrier.modality == "image":
            return istg.get_adapter("sequential_lsb").extract(item.path, {})
        if carrier.modality == 'audio':
            return stream.extract(stream.audio_slot_chunks(item.path, carrier.algorithm), carrier.bit_depth)
        if carrier.modality == 'video':
            return stream.extract(stream.video_chunks(item.path), carrier.bit_depth)
        return mm.extract_bits(mm.text_slots(data))

    def recovered(self, packet, password=None):
        parsed = istg.parse_istg(packet, max_packet_bytes=limits.PACKET_BYTES)
        encrypted = parsed["encrypted"]
        if encrypted:
            if not password:
                raise ValueError("数据已加密，请提供密码后提取")
            packet = istg.decrypt_istg(packet, password.get_secret_value())
            parsed = istg.parse_istg(packet, max_packet_bytes=limits.PACKET_BYTES)
        inner = parsed["payload_data"]
        data = inner["data"]
        name = inner.get("filename", "recovered_text.txt")
        # Recovered arbitrary files are always attachments; never execute/render HTML/SVG.
        item = self.store.add(data, name, "text/plain; charset=utf-8" if inner["kind"] == "text" else "application/octet-stream", maximum=limits.SECRET_BYTES)
        return {"status": "ok", "file": item.public(), "text": data.decode("utf-8") if inner["kind"] == "text" else None,
                "sha256": sha256(data).hexdigest(), "size_bytes": len(data), "encrypted": encrypted,
                "authenticated": True if encrypted else None, "integrity_verified": True}

    def embed_packet(self, carrier, packet):
        available = self.capacity(carrier)
        if len(packet) > min(available, limits.PACKET_BYTES):
            raise ValueError(f'容量不足：封装需要 {len(packet)} 字节，载体可用 {available} 字节；请压缩秘密文件、增加嵌入位数或更换载体')
        item, data, rate = self.source(carrier)
        analysis, preview, thumbnails = {}, None, []
        with tempfile.TemporaryDirectory(prefix="multimodal_", dir=self.store.root) as work:
            output = Path(work) / ("stego" + SUFFIXES[carrier.modality])
            if carrier.modality == "image":
                istg.get_adapter("sequential_lsb").embed(item.path, packet, output, {})
                analysis = istg.calculate_quality(item.path, output)
            elif carrier.modality == "audio":
                analysis = stream.embed_audio(item.path, output, packet, carrier.algorithm, carrier.bit_depth)
            elif carrier.modality == "video":
                preview_path = Path(work) / "preview.mp4"
                def thumbnail(index, original, diff, fps):
                    from PIL import Image
                    size = (min(640, original.shape[1]), max(1, int(original.shape[0] * min(1, 640 / original.shape[1]))))
                    original = np.asarray(Image.fromarray(original).resize(size))
                    diff = np.asarray(Image.fromarray(np.minimum(diff.astype(int) * 255, 255).astype(np.uint8)).resize(size))
                    return {'frame': int(index), 'time': index / fps,
                        'original': self.store.add_image(original, f'frame_{index}.png').public(),
                        'difference': self.store.add_image(diff, f'difference_{index}.png').public()}
                analysis, thumbnails = stream.embed_video(item.path, output, preview_path, packet, carrier.bit_depth, data, thumbnail)
                preview = self.store.add_path(preview_path, 'preview_only.mp4', 'video/mp4', maximum=limits.RESULT_BYTES).public()
            else:
                result = mm.embed_text(data, packet)
                output.write_text(result, encoding="utf-8")
                hidden_count = len(mm.text_slots(result))
                analysis = {"visible_characters": len(data), "hidden_bits": hidden_count,
                    "zero_count": result.count(mm.ZERO), "one_count": result.count(mm.ONE),
                    "visualization": result[:result.find(mm.START)] + "[START]" +
                        "".join("1" if c == mm.ONE else "0" for c in result[result.find(mm.START)+3:result.find(mm.END)][:1024]) +
                        ("…" if hidden_count > 1024 else "") + "[END]" + result[result.find(mm.END)+3:],
                    "visible_text": data}
            if carrier.modality == 'image':
                stego = self.store.add(output.read_bytes(), 'istg_multimodal.png', MIMES['image'], image=True)
            else:
                stego = self.store.add_path(output, 'istg_multimodal' + SUFFIXES[carrier.modality], MIMES[carrier.modality], maximum=limits.RESULT_BYTES)
        output_carrier = Carrier(file_id=stego.id, modality=carrier.modality, algorithm=carrier.algorithm, bit_depth=carrier.bit_depth)
        restored = self.extract_packet(output_carrier)
        if restored != packet:
            self.store.remove(stego.id)
            raise ValueError("嵌入后提取校验失败，未保留隐写主文件")
        return {"status": "ok", "carrier": output_carrier.model_dump(), "cover": item.public(), "stego": stego.public(),
                "preview": preview, "analysis": analysis, "thumbnails": thumbnails, "packet_bytes": len(packet),
                "recovery": {"exact_match": True, "packet_sha256": sha256(packet).hexdigest()}}

    def embed(self, model, progress):
        progress(10, "正在准备信息")
        packet = self.packet(model)
        progress(30, "正在嵌入并校验信息")
        return self.embed_packet(model.carrier, packet)

    def extract(self, model, progress):
        progress(20, "正在提取信息")
        return self.recovered(self.extract_packet(model.carrier), model.password)

    def split(self, model, progress):
        if len({c.file_id for c in model.carriers}) != len(model.carriers) or len({c.modality for c in model.carriers}) != len(model.carriers):
            raise ValueError("协同载体必须使用不同文件和不同模态")
        packet = self.packet(model)
        shares = [secrets.token_bytes(len(packet)) for _ in range(len(model.carriers) - 1)]
        last = np.frombuffer(packet, dtype=np.uint8).copy()
        for share in shares:
            last ^= np.frombuffer(share, dtype=np.uint8)
        shares.append(last.tobytes())
        group = uuid.uuid4().hex
        wrapped = [cbor2.dumps({"kind": "istg_xor_share_v1", "group": group, "index": index,
                    "total": len(shares), "sha256": sha256(packet).hexdigest(), "data": share}) for index, share in enumerate(shares)]
        for carrier, share in zip(model.carriers, wrapped):
            if self.capacity(carrier) < len(share):
                raise ValueError("至少一个载体容量不足；每个 n/n XOR 份额均需容纳完整包长度及份额元数据")
        results = []
        for index, (carrier, share) in enumerate(zip(model.carriers, wrapped)):
            progress(10 + index * 80 // len(wrapped), f"嵌入第 {index+1} 个份额")
            results.append(self.embed_packet(carrier, share))
        return {"status": "ok", "scheme": "XOR n-of-n", "required": len(shares), "group": group, "results": results}

    def combine(self, model, progress):
        shares = []
        for carrier in model.carriers:
            progress(20, "提取并核对载体份额")
            shares.append(decode_cbor_object(self.extract_packet(carrier), limits.PACKET_BYTES))
        first = shares[0]
        if (first.get("kind") != "istg_xor_share_v1" or first.get("total") != len(shares)
                or not isinstance(first.get("group"), str) or not isinstance(first.get("sha256"), str)):
            raise ValueError("份额类型不匹配或数量不足，需提供全部 n 个份额")
        if (any(any(s.get(k) != first.get(k) for k in ("kind", "group", "total", "sha256")) for s in shares)
                or any(type(s.get("index")) is not int for s in shares)
                or {s["index"] for s in shares} != set(range(len(shares)))
                or any(not isinstance(s.get("data"), bytes) or len(s["data"]) != len(first["data"]) for s in shares)):
            raise ValueError("份额缺失、重复、损坏或来自不同协同任务")
        packet = np.zeros(len(first["data"]), dtype=np.uint8)
        for share in shares:
            packet ^= np.frombuffer(share["data"], dtype=np.uint8)
        packet = packet.tobytes()
        if sha256(packet).hexdigest() != first["sha256"]:
            raise ValueError("组合数据 SHA-256 校验失败")
        return {**self.recovered(packet, model.password), "scheme": "XOR n-of-n", "shares_used": len(shares)}

    def attack(self, model, progress):
        carrier = model.carrier
        if carrier.modality == "image":
            raise ValueError("图像攻击请使用原有安全性分析入口")
        allowed = {"audio": {"noise", "resample", "compression"}, "video": {"compression", "crop"}, "text": {"strip_zero_width"}}
        if model.attack not in allowed[carrier.modality]:
            raise ValueError("攻击操作不适用于此载体类型")
        reference = self.extract_packet(carrier)
        item, data, rate = self.source(carrier)
        progress(25, "正在处理载体并测试恢复效果")
        with tempfile.TemporaryDirectory(prefix="attack_", dir=self.store.root) as work:
            output = Path(work) / ("attacked" + SUFFIXES[carrier.modality])
            if carrier.modality == "audio":
                if model.attack == "noise":
                    import wave
                    rng = np.random.default_rng(2026)
                    with wave.open(str(output), 'wb') as writer:
                        writer.setnchannels(data['channels']); writer.setsampwidth(2); writer.setframerate(rate)
                        for block in stream.audio_chunks(item.path):
                            attacked = np.clip(np.rint(block.astype(float) + rng.normal(0, 8, block.shape)), -32768, 32767).astype('<i2')
                            writer.writeframes(attacked.tobytes())
                elif model.attack == "resample":
                    self.transcode(['-i', str(item.path), '-af', f'aresample={rate // 2},aresample={rate}', '-c:a', 'pcm_s16le', str(output)])
                else:
                    compressed = Path(work) / "attack.m4a"
                    self.transcode(["-i", str(item.path), "-c:a", "aac", "-b:a", "64k", str(compressed)])
                    self.transcode(["-i", str(compressed), "-c:a", "pcm_s16le", str(output)])
            elif carrier.modality == "video":
                if model.attack == "compression":
                    compressed = Path(work) / "attack.mp4"
                    self.transcode(['-i', str(item.path), '-an', '-c:v', 'libx264', '-crf', '22',
                        '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-pix_fmt', 'yuv420p', str(compressed)])
                    self.transcode(['-i', str(compressed), '-an', '-c:v', 'ffv1', '-pix_fmt', 'bgr0', str(output)])
                else:
                    # Real temporal crop: remove the first 20% of decoded frames.
                    self.capacity(carrier)
                    data = getattr(item, '_video_info')
                    if data['count'] < 2:
                        raise ValueError("裁剪攻击至少需要两帧")
                    first = max(1, data['count'] // 5)
                    self.transcode(['-i', str(item.path), '-an', '-vf', f'trim=start_frame={first},setpts=PTS-STARTPTS',
                                    '-c:v', 'ffv1', '-pix_fmt', 'bgr0', str(output)])
            else:
                attacked = "".join(c for c in data if c not in (mm.ZERO, mm.ONE, "\u2063", "\u2064"))
                output.write_text(attacked, encoding="utf-8")
            result = self.store.add_path(output, output.name, MIMES[carrier.modality], maximum=limits.RESULT_BYTES)
            def extract_attacked(verify=True):
                options = {} if verify else {'verify': False, 'expected': len(reference)}
                if carrier.modality == 'audio':
                    return stream.extract(stream.audio_slot_chunks(result.path, carrier.algorithm), carrier.bit_depth, **options)
                if carrier.modality == 'video':
                    return stream.extract(stream.video_chunks(result.path), carrier.bit_depth, **options)
                return mm.extract_bits(np.empty(0, dtype=np.uint8), **options)
            raw = extract_attacked(False)
            expected = np.frombuffer(reference, dtype=np.uint8)
            actual = np.frombuffer(raw, dtype=np.uint8)
            count = min(len(expected), len(actual))
            wrong = sum(int(np.unpackbits(expected[i:min(i+1048576, count)] ^ actual[i:min(i+1048576, count)]).sum())
                        for i in range(0, count, 1048576)) + (len(expected) - count) * 8
            correct_bytes = int(np.count_nonzero(expected[:count] == actual[:count]))
            valid = False
            try:
                valid = extract_attacked() == reference
            except ValueError:
                pass
            recovered = None
            if valid:
                # Authenticated payload recovery is a separate outcome from bit accuracy.
                try:
                    recovered = self.recovered(reference, model.password)
                except ValueError:
                    pass
        return {"status": "ok", "attack": model.attack, "file": result.public(),
                "parameters": {"noise": "Gaussian σ=8 PCM units; seed=2026", "resample": "rate/2 then original rate", "compression": "AAC 64k / H.264 CRF22", "crop": "remove first 20% frames (at least 1)", "strip_zero_width": "remove U+200B/U+200C/U+2063/U+2064"}[model.attack],
                "packet_bytes": len(reference), "compared_bytes": count, "ber": wrong / (len(reference) * 8),
                "bit_recovery_rate": 1 - wrong / (len(reference) * 8), "byte_recovery_rate": correct_bytes / len(reference),
                "exact_match": valid, "payload_recoverable": recovered is not None, "recovered": recovered}

    @staticmethod
    def transcode(arguments):
        import imageio_ffmpeg
        try:
            result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-v", "error",
                '-threads', '1', '-protocol_whitelist', 'file,pipe', *arguments[:-1], '-threads', '1',
                '-fs', str(limits.RESULT_BYTES + 1), arguments[-1]],
                capture_output=True, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("媒体转码失败或超过 30 分钟") from exc
        if result.returncode:
            raise ValueError("音频压缩编码失败，请检查 FFmpeg AAC 编码器")


router = APIRouter(prefix="/api/multimodal", tags=["multimodal"])


@router.post("/uploads")
async def upload(request: Request, file: UploadFile = File(...), modality: Literal["image", "audio", "video", "text", "file"] = Form("file"),
                 purpose: Literal['cover', 'stego'] = Form('cover')):
    maximum = {'image': MAX_UPLOAD, 'audio': limits.AUDIO_UPLOAD, 'video': limits.VIDEO_UPLOAD,
               'text': MAX_UPLOAD, 'file': limits.SECRET_BYTES}[modality]
    if purpose == 'stego' and modality in {'audio', 'video', 'text'}:
        maximum = limits.TEXT_OUTPUT if modality == 'text' else limits.RESULT_BYTES
    store = request.app.state.files
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=store.root, suffix=Path(file.filename or '').suffix[:12], delete=False) as target:
            temporary = Path(target.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise APIError('file_too_large', f'此类文件上传上限为 {maximum // 1048576} MiB', 413)
                await asyncio.to_thread(target.write, chunk)
        if not size:
            raise ValueError('上传文件不能为空')
        if modality == 'image':
            item = store.add(temporary.read_bytes(), file.filename or 'upload.png', 'application/octet-stream', image=True)
        else:
            item = store.add_path(temporary, file.filename or 'upload.bin', 'application/octet-stream', maximum=maximum)
        try:
            if modality != "file":
                service = MultimodalService(request.app.state.files)
                await asyncio.to_thread(service.source, Carrier(file_id=item.id, modality=modality))
                item.media_type = MIMES[modality] if modality != "image" else item.media_type
                if modality == "video":
                    # Input may be MP4; preserve its real MIME for browser previews.
                    item.media_type = {".mp4": "video/mp4", ".webm": "video/webm", ".avi": "video/x-msvideo"}.get(item.path.suffix.lower(), "video/x-matroska")
        except Exception:
            request.app.state.files.remove(item.id)
            raise
        return {**item.public(), "modality": modality}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        await file.close()


def submit(request, name, model):
    service = MultimodalService(request.app.state.files)
    def run(progress):
        try:
            return getattr(service, name)(model, progress)
        finally:
            for field in ("password", "confirm_password"):
                if hasattr(model, field):
                    setattr(model, field, None)
    return request.app.state.tasks.submit("multimodal-" + name, run)


@router.get("/capabilities")
def capabilities():
    import imageio_ffmpeg
    return {"algorithms": DEFAULTS, "audio_algorithms": ["audio_lsb", "audio_dwt"],
            "ffmpeg": bool(imageio_ffmpeg.get_ffmpeg_exe()), "video_output": "FFV1 / MKV", "video_preview": "H.264 / MP4 (preview only)",
            'limits': {'upload_bytes': MAX_UPLOAD, 'secret_bytes': limits.SECRET_BYTES, 'audio_upload_bytes': limits.AUDIO_UPLOAD,
                'video_upload_bytes': limits.VIDEO_UPLOAD, 'result_bytes': limits.RESULT_BYTES,
                'text_output_bytes': limits.TEXT_OUTPUT, 'video_frames': limits.VIDEO_FRAMES,
                'video_seconds': limits.VIDEO_SECONDS, 'video_pixels': limits.VIDEO_PIXELS,
                'video_fps': limits.VIDEO_FPS, 'video_work_bytes': limits.VIDEO_WORK_BYTES},
            'bit_depths': {'audio_lsb': [1, 12], 'audio_dwt': [1, 4], 'video_lsb': [1, 4]}, 'sharing': 'XOR n-of-n'}


@router.post("/capacity")
def capacity(model: EmbedRequest, request: Request):
    service = MultimodalService(request.app.state.files)
    size = len(service.packet(model, probe=True))
    available = min(service.capacity(model.carrier), limits.PACKET_BYTES)
    raw_size = service.store.get(model.secret_id).size_bytes if model.secret_id else len(model.text.encode('utf-8'))
    suggestion = None
    if model.carrier.modality in {'audio', 'video'}:
        single = (service.capacity(model.carrier) + mm.HEADER.size) * 8 // model.carrier.bit_depth
        needed = ((size + mm.HEADER.size) * 8 + max(1, single) - 1) // max(1, single)
        maximum = 12 if model.carrier.algorithm == 'audio_lsb' else 4
        if needed <= maximum:
            suggestion = max(1, needed)
    return {'capacity_bytes': available, 'packet_bytes': size, 'fits': size <= available,
            'usage_percent': size * 100 / available if available else 100,
            'original_bytes': raw_size, 'compression': model.compression, 'suggested_bit_depth': suggestion}


@router.post("/embed", status_code=202)
def embed(model: EmbedRequest, request: Request):
    return submit(request, "embed", model)


@router.post("/extract", status_code=202)
def extract(model: ExtractRequest, request: Request):
    return submit(request, "extract", model)


@router.post("/split", status_code=202)
def split(model: SplitRequest, request: Request):
    return submit(request, "split", model)


@router.post("/combine", status_code=202)
def combine(model: CombineRequest, request: Request):
    return submit(request, "combine", model)


@router.post("/attack", status_code=202)
def attack(model: AttackRequest, request: Request):
    return submit(request, "attack", model)
