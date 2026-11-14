"""Self-contained RGB recovery from a JPEG preview and embedded residual layer.

The PNG private ancillary chunk isTR follows PNG naming and CRC rules.
The layer depends on the displayed pixels and is unsafe to copy after edits.
Recovery is lossless for EXIF-normalized RGB8 pixels, not source file bytes.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import math
import struct
import warnings
import zlib

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .security_analysis.metrics import _bounded_ssim

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_CONTAINER_BYTES = 96 * 1024 * 1024
MAX_LAYER_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_METADATA_BYTES = 4096
MAX_CHUNKS = 4096
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LAYER_CHUNK = b"isTR"
LAYER_MAGIC = b"ISTR\x01"
_LAYER_PREFIX = struct.Struct(">5sI")
_METADATA_FIELDS = {
    "version", "width", "height", "mode", "jpeg_quality",
    "original_file_bytes", "preview_jpeg_bytes",
    "base_pixel_sha256", "original_pixel_sha256", "residual_bytes",
}


def _bounded_bytes(data: bytes, maximum: int) -> bytes:
    if not isinstance(data, bytes) or not data:
        raise ValueError("文件内容为空或格式无效。")
    if len(data) > maximum:
        raise ValueError(f"文件不能超过 {maximum // (1024 * 1024)} MiB。")
    return data


def _dimensions(width: int, height: int) -> None:
    if (type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0 or width * height > MAX_PIXELS):
        raise ValueError("图像尺寸无效或超过 1600 万像素。")


def _pixels_digest(pixels: np.ndarray) -> str:
    return sha256(pixels.tobytes()).hexdigest()


def _decode_rgb(data: bytes, *, source: bool) -> np.ndarray:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                _dimensions(*image.size)
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("请选择单帧图像。")
                if image.format not in {"PNG", "JPEG", "BMP", "WEBP", "TIFF"}:
                    raise ValueError("图像格式不支持。")
                if source:
                    image = ImageOps.exif_transpose(image)
                return np.array(image.convert("RGB"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("图像损坏或无法解码。") from exc


def _encode_png(pixels: np.ndarray) -> bytes:
    target = BytesIO()
    Image.fromarray(pixels).save(target, format="PNG")
    encoded = target.getvalue()
    if len(encoded) > MAX_CONTAINER_BYTES:
        raise ValueError("生成的图像超过输出大小限制。")
    return encoded


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xffffffff
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _read_container(data: bytes) -> tuple[bytes, bytes, int, int]:
    """Validate PNG structure, returning critical chunks and one recovery layer.

    Ancillary chunks are omitted before Pillow decoding, so arbitrary metadata
    is never decompressed by the image decoder. The layer is parsed separately.
    """
    _bounded_bytes(data, MAX_CONTAINER_BYTES)
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("请上传包含恢复信息的 PNG 图像。")
    offset, count = 8, 0
    width = height = 0
    critical = [PNG_SIGNATURE]
    layer = None
    seen_idat = False
    idat_ended = False
    ended = False
    while offset < len(data):
        count += 1
        if count > MAX_CHUNKS or offset + 12 > len(data):
            raise ValueError("PNG 数据结构无效。")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        if (length > 0x7fffffff or offset + 12 + length > len(data)
                or len(kind) != 4 or any(not (65 <= c <= 90 or 97 <= c <= 122) for c in kind)
                or kind[2] & 32):
            raise ValueError("PNG 数据块无效。")
        if kind == LAYER_CHUNK and length > MAX_LAYER_BYTES:
            raise ValueError("恢复信息超过大小限制。")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xffffffff
        if checksum != expected_crc:
            raise ValueError("PNG 校验失败，文件可能已损坏。")
        if count == 1:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG 图像头无效。")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            _dimensions(width, height)
            if (depth, color, compression, filtering, interlace) != (8, 2, 0, 0, 0):
                raise ValueError("恢复文件的图像格式不匹配。")
        elif kind == b"IHDR":
            raise ValueError("PNG 含有重复图像头。")
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise ValueError("恢复文件必须为单帧图像。")
        if kind == b"IDAT":
            if idat_ended:
                raise ValueError("PNG 图像数据顺序无效。")
            seen_idat = True
        elif seen_idat:
            idat_ended = True
        if kind == LAYER_CHUNK:
            if layer is not None or not seen_idat:
                raise ValueError("恢复信息重复或位置无效。")
            layer = payload
        if not kind[0] & 32:
            if kind not in {b"IHDR", b"IDAT", b"IEND"}:
                raise ValueError("恢复文件含不支持的图像数据。")
            critical.append(data[offset:offset + length + 12])
        offset += length + 12
        if kind == b"IEND":
            if length != 0 or not seen_idat or offset != len(data):
                raise ValueError("PNG 结束标记无效。")
            ended = True
            break
    if not ended:
        raise ValueError("PNG 文件不完整。")
    if layer is None:
        raise ValueError("未找到恢复信息，请选择原始恢复文件。")
    return b"".join(critical), layer, width, height


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("恢复信息含重复字段。")
        result[key] = value
    return result


def _parse_layer(layer: bytes) -> tuple[dict, bytes]:
    if len(layer) < _LAYER_PREFIX.size or len(layer) > MAX_LAYER_BYTES:
        raise ValueError("恢复信息不完整。")
    magic, length = _LAYER_PREFIX.unpack_from(layer)
    if (magic != LAYER_MAGIC or not 0 < length <= MAX_METADATA_BYTES
            or _LAYER_PREFIX.size + length >= len(layer)):
        raise ValueError("恢复信息格式无效。")
    try:
        metadata = json.loads(
            layer[_LAYER_PREFIX.size:_LAYER_PREFIX.size + length].decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("恢复信息格式无效。") from exc
    if not isinstance(metadata, dict) or set(metadata) != _METADATA_FIELDS:
        raise ValueError("恢复信息字段无效。")
    _dimensions(metadata["width"], metadata["height"])
    if (type(metadata["version"]) is not int or metadata["version"] != 1
            or metadata["mode"] != "RGB"
            or type(metadata["jpeg_quality"]) is not int or not 1 <= metadata["jpeg_quality"] <= 95
            or type(metadata["original_file_bytes"]) is not int
            or not 1 <= metadata["original_file_bytes"] <= MAX_SOURCE_BYTES
            or type(metadata["preview_jpeg_bytes"]) is not int
            or not 1 <= metadata["preview_jpeg_bytes"] <= MAX_CONTAINER_BYTES
            or type(metadata["residual_bytes"]) is not int
            or metadata["residual_bytes"] != metadata["width"] * metadata["height"] * 3):
        raise ValueError("恢复信息参数无效。")
    for name in ("base_pixel_sha256", "original_pixel_sha256"):
        value = metadata[name]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("恢复信息校验值无效。")
    return metadata, layer[_LAYER_PREFIX.size + length:]


def _decompress_exact(data: bytes, expected: int) -> bytes:
    decoder = zlib.decompressobj()
    try:
        result = decoder.decompress(data, expected + 1)
    except zlib.error as exc:
        raise ValueError("恢复数据损坏。") from exc
    if (len(result) != expected or not decoder.eof
            or decoder.unconsumed_tail or decoder.unused_data):
        raise ValueError("恢复数据长度或压缩流无效。")
    return result


def _quality(original: np.ndarray, other: np.ndarray) -> dict:
    first, second = original.reshape(-1), other.reshape(-1)
    squared = 0
    for start in range(0, first.size, 1_048_576):
        difference = first[start:start + 1_048_576].astype(np.int16) - second[start:start + 1_048_576].astype(np.int16)
        squared += int(np.multiply(difference, difference, dtype=np.int32).sum(dtype=np.int64))
    mse = squared / first.size
    minimum = min(original.shape[:2])
    window = min(7, minimum if minimum % 2 else minimum - 1)
    if minimum < 3:
        ssim = None
    elif squared == 0:
        ssim = 1.0
    else:
        ssim = _bounded_ssim(original, other, window)
    return {"mse": mse, "psnr": math.inf if mse == 0 else 10 * math.log10(255 ** 2 / mse),
            "ssim": ssim}


def restore_image(data: bytes) -> dict:
    """Restore RGB8 pixels using only a carrier PNG, without a source-image lookup."""
    clean_png, layer, width, height = _read_container(data)
    metadata, compressed = _parse_layer(layer)
    if (metadata["width"], metadata["height"]) != (width, height):
        raise ValueError("图像尺寸与恢复信息不匹配。")
    base = _decode_rgb(clean_png, source=False)
    if base.shape != (height, width, 3) or _pixels_digest(base) != metadata["base_pixel_sha256"]:
        raise ValueError("图像已被修改，无法使用此恢复信息。")
    residual = np.frombuffer(_decompress_exact(compressed, metadata["residual_bytes"]), dtype=np.uint8).reshape(base.shape)
    original = np.add(base, residual, dtype=np.uint8)
    recovered_digest = _pixels_digest(original)
    if recovered_digest != metadata["original_pixel_sha256"]:
        raise ValueError("恢复结果校验失败。")
    restored_png = _encode_png(original)
    metrics = {
        "width": width, "height": height, "mode": "RGB", "jpeg_quality": metadata["jpeg_quality"],
        "original_file_bytes": metadata["original_file_bytes"],
        "preview_jpeg_bytes": metadata["preview_jpeg_bytes"],
        "carrier_bytes": len(data), "recovery_layer_bytes": len(layer),
        "residual_compressed_bytes": len(compressed), "restored_png_bytes": len(restored_png),
        "original_pixel_sha256": metadata["original_pixel_sha256"],
        "restored_pixel_sha256": recovered_digest,
        "base_pixel_sha256": metadata["base_pixel_sha256"],
        "exact_match": True, "preview_quality": _quality(original, base),
        "restored_quality": _quality(original, original),
        "container": "PNG", "embedding": "private_ancillary_chunk",
        "size_ratio": len(data) / metadata["original_file_bytes"],
    }
    return {"restored": restored_png, "metrics": metrics}


def prepare_image(data: bytes, quality: int = 70) -> dict:
    """Build a JPEG preview plus a private lossless residual layer in one PNG."""
    _bounded_bytes(data, MAX_SOURCE_BYTES)
    if type(quality) is not int or not 1 <= quality <= 95:
        raise ValueError("JPEG 质量应为 1–95 的整数。")
    original = _decode_rgb(data, source=True)
    height, width = original.shape[:2]
    jpeg_buffer = BytesIO()
    Image.fromarray(original).save(jpeg_buffer, format="JPEG", quality=quality, subsampling=2)
    preview = jpeg_buffer.getvalue()
    base = _decode_rgb(preview, source=False)
    residual = np.subtract(original, base, dtype=np.uint8).tobytes()
    compressed = zlib.compress(residual, level=9)
    metadata = {
        "version": 1, "width": width, "height": height, "mode": "RGB", "jpeg_quality": quality,
        "original_file_bytes": len(data), "preview_jpeg_bytes": len(preview),
        "base_pixel_sha256": _pixels_digest(base),
        "original_pixel_sha256": _pixels_digest(original), "residual_bytes": len(residual),
    }
    description = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    layer = _LAYER_PREFIX.pack(LAYER_MAGIC, len(description)) + description + compressed
    if len(layer) > MAX_LAYER_BYTES:
        raise ValueError("恢复信息超过大小限制。")
    base_png = _encode_png(base)
    carrier = base_png[:-12] + _chunk(LAYER_CHUNK, layer) + base_png[-12:]
    if len(carrier) > MAX_CONTAINER_BYTES:
        raise ValueError("恢复文件超过 96 MiB 输出限制。")
    verified = restore_image(carrier)
    if verified["metrics"]["restored_pixel_sha256"] != _pixels_digest(original):
        raise ValueError("恢复验证失败。")
    return {"source": _encode_png(original), "preview": preview,
            "carrier": carrier, "restored": verified["restored"], "metrics": verified["metrics"]}
