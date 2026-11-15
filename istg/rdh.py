"""Single-checkerboard prediction-error histogram shifting (PEHS), RGB8.

Four orthogonal neighbours and the image boundary are unchanged predictors.
Each channel independently opens an empty error bin and stores bits at a peak.
Overflow-prone boundary samples are skipped; only their compressed bit map,
small parameters and SHA-256 digests are placed in the private PNG rdHR chunk.
No source pixels, residuals, or secret payload bytes enter that chunk.
"""
from __future__ import annotations

from hashlib import sha256
from time import perf_counter
import zlib

import cbor2
import numpy as np

from .image_restoration import (
    MAX_SOURCE_BYTES, MAX_CONTAINER_BYTES, _bounded_bytes, _decode_rgb, _encode_png,
    _chunk, _read_container, _decompress_exact, _pixels_digest, _quality,
)
from .security_analysis.packet import decode_cbor_object

ALGORITHM = "checkerboard_pehs_rgb_v1"
RDH_CHUNK = b"rdHR"
MAX_AUX_BYTES = 8 * 1024 * 1024
MAX_PACKET_BYTES = 8 * 1024 * 1024
_ROOT_FIELDS = {"version", "algorithm", "width", "height", "payload_bytes", "packet_sha256",
                "original_rgb_sha256", "stego_rgb_sha256", "plans"}
_PLAN_FIELDS = {"channel", "peak", "zero", "capacity_bits", "used_bits", "skip_map"}


class RDHError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _indices(height: int, width: int) -> np.ndarray:
    if min(height, width) < 3:
        return np.empty(0, dtype=np.int64)
    row = np.arange(height - 2, dtype=np.uint8)[:, None]
    col = np.arange(width - 2, dtype=np.uint8)[None, :]
    return np.flatnonzero(((row + col) & 1).reshape(-1) == 0)


def _samples(pixels: np.ndarray, channel: int, indices: np.ndarray):
    plane = pixels[:, :, channel]
    prediction = (plane[:-2, 1:-1].astype(np.int16) + plane[2:, 1:-1]
                  + plane[1:-1, :-2] + plane[1:-1, 2:] + 2) // 4
    predicted = prediction.reshape(-1)[indices]
    values = plane[1:-1, 1:-1].reshape(-1)[indices].astype(np.int16)
    return values, predicted


def _write_samples(pixels, channel, indices, values):
    if np.any((values < 0) | (values > 255)):
        raise RDHError("invalid_pixels", "像素恢复超出 RGB8 范围。")
    interior = pixels[1:-1, 1:-1, channel].copy().reshape(-1)
    interior[indices] = values.astype(np.uint8)
    pixels[1:-1, 1:-1, channel] = interior.reshape(pixels.shape[0] - 2, pixels.shape[1] - 2)


def _plan(pixels, channel, indices):
    values, predicted = _samples(pixels, channel, indices)
    errors = values - predicted
    histogram = np.bincount(errors + 255, minlength=511)
    theoretical = int(histogram.max()) if values.size else 0
    choices = []
    for direction, boundary in ((1, 255), (-1, 0)):
        skip = values == boundary
        counts = np.bincount(errors[~skip] + 255, minlength=511)
        capacity = int(counts.max())
        if not capacity:
            continue
        zeros = np.flatnonzero(counts == 0)
        for peak_index in np.flatnonzero(counts == capacity):
            available = zeros[zeros > peak_index] if direction == 1 else zeros[zeros < peak_index]
            if not available.size:
                continue
            zero_index = int(available[0] if direction == 1 else available[-1])
            low, high = sorted((int(peak_index), zero_index))
            shift_count = int(counts[low + 1:high].sum())
            # Capacity first, then fewer modified pixels and less side information.
            score = (-capacity, shift_count, int(skip.sum()), abs(zero_index - peak_index), direction)
            choices.append((score, int(peak_index) - 255, zero_index - 255, skip))
    if not choices:
        return None, theoretical
    _, peak, zero, skip = min(choices, key=lambda item: item[0])
    location = zlib.compress(np.packbits(skip, bitorder="big").tobytes(), 9) if skip.any() else b""
    return {"channel": channel, "peak": peak, "zero": zero,
            "capacity_bits": -min(choices, key=lambda item: item[0])[0][0], "used_bits": 0,
            "skip_map": location}, theoretical


def _analyse(pixels):
    height, width = pixels.shape[:2]
    indices = _indices(height, width)
    plans, theoretical = [], 0
    if indices.size:
        for channel in range(3):
            plan, raw = _plan(pixels, channel, indices)
            theoretical += raw
            if plan:
                plans.append(plan)
    return indices, plans, theoretical


def _allocated(plans, bit_count):
    remaining, allocated = bit_count, []
    for plan in plans:
        if remaining <= 0:
            break
        used = min(remaining, plan["capacity_bits"])
        allocated.append({**plan, "used_bits": used})
        remaining -= used
    return allocated


def _metadata(pixels, plans, packet_bytes, original_hash, packet_hash, stego_hash):
    return {"version": 1, "algorithm": ALGORITHM, "width": pixels.shape[1], "height": pixels.shape[0],
            "payload_bytes": packet_bytes, "packet_sha256": packet_hash,
            "original_rgb_sha256": original_hash, "stego_rgb_sha256": stego_hash, "plans": plans}


def _aux_bytes(metadata):
    encoded = b"RDH1" + cbor2.dumps(metadata, canonical=True)
    if len(encoded) > MAX_AUX_BYTES:
        raise RDHError("auxiliary_too_large", "算法辅助信息超出大小限制。")
    return encoded


def capacity(data: bytes, packet_bytes: int = 0, secret_bytes: int = 0) -> dict:
    _bounded_bytes(data, MAX_SOURCE_BYTES)
    if (type(packet_bytes) is not int or not 0 <= packet_bytes <= MAX_PACKET_BYTES
            or type(secret_bytes) is not int or not 0 <= secret_bytes <= packet_bytes):
        raise RDHError("invalid_payload", "秘密数据包长度无效或超过限制。")
    pixels = _decode_rgb(data, source=True)
    _, plans, theoretical = _analyse(pixels)
    return _capacity_metrics(pixels, plans, theoretical, packet_bytes, secret_bytes)


def _capacity_metrics(pixels, plans, theoretical, packet_bytes, secret_bytes):
    safe_bits = sum(plan["capacity_bits"] for plan in plans)
    packet_capacity = safe_bits // 8
    allocated = _allocated(plans, packet_bytes * 8)
    metadata = _metadata(pixels, allocated, packet_bytes, "0" * 64, "0" * 64, "0" * 64)
    auxiliary = len(_aux_bytes(metadata)) + 12
    overhead = packet_bytes - secret_bytes
    return {"algorithm": ALGORITHM, "width": pixels.shape[1], "height": pixels.shape[0],
            "theoretical_capacity_bits": theoretical, "safe_capacity_bits": safe_bits,
            "packet_capacity_bytes": packet_capacity, "effective_capacity_bits": packet_capacity * 8,
            "secret_capacity_bytes": max(0, packet_capacity - overhead),
            "packet_bytes": packet_bytes, "secret_bytes": secret_bytes, "packet_overhead_bytes": overhead,
            "embedded_bits": packet_bytes * 8, "fits": packet_bytes <= packet_capacity,
            "usage_percent": packet_bytes * 100 / packet_capacity if packet_capacity else 0,
            "auxiliary_bytes": auxiliary, "location_map_bytes": sum(len(p["skip_map"]) for p in allocated),
            "auxiliary_in_pixel_bits": 0,
            "bpp": packet_bytes * 8 / (pixels.shape[0] * pixels.shape[1]),
            "capacity_bpp": packet_capacity * 8 / (pixels.shape[0] * pixels.shape[1])}


def _unpack_skip(plan, count):
    if not plan["skip_map"]:
        return np.zeros(count, dtype=bool)
    byte_count = (count + 7) // 8
    if len(plan["skip_map"]) > byte_count + 1024:
        raise RDHError("invalid_auxiliary", "位置图数据超过合理大小。")
    packed = _decompress_exact(plan["skip_map"], byte_count)
    bits = np.unpackbits(np.frombuffer(packed, np.uint8), bitorder="big")
    if np.any(bits[count:]):
        raise RDHError("invalid_auxiliary", "位置图尾部填充无效。")
    return bits[:count].astype(bool)


def embed_image(data: bytes, packet: bytes, *, secret_bytes: int | None = None, progress=None) -> dict:
    started = perf_counter()
    _bounded_bytes(data, MAX_SOURCE_BYTES)
    _bounded_bytes(packet, MAX_PACKET_BYTES)
    if secret_bytes is None:
        secret_bytes = len(packet)
    if type(secret_bytes) is not int or not 0 <= secret_bytes <= len(packet):
        raise RDHError("invalid_payload", "秘密数据长度无效。")
    original = _decode_rgb(data, source=True)
    if progress: progress(20, "正在计算预测误差直方图与安全容量")
    indices, plans, theoretical = _analyse(original)
    metrics = _capacity_metrics(original, plans, theoretical, len(packet), secret_bytes)
    if not metrics["fits"]:
        raise RDHError("capacity_exceeded", f"容量不足：实际 ISTG 数据包为 {len(packet):,} 字节，"
                       f"安全像素容量为 {metrics['packet_capacity_bytes']:,} 字节。请减少秘密信息或更换图像。")
    allocated = _allocated(plans, len(packet) * 8)
    bits = np.unpackbits(np.frombuffer(packet, np.uint8), bitorder="big")
    stego, offset = original.copy(), 0
    if progress: progress(40, "正在执行可逆预测误差直方图平移")
    for plan in allocated:
        values, predicted = _samples(stego, plan["channel"], indices)
        errors = values - predicted
        direction = 1 if plan["zero"] > plan["peak"] else -1
        skip = _unpack_skip(plan, values.size)
        low, high = sorted((plan["peak"], plan["zero"]))
        shifted = (~skip) & (errors > low) & (errors < high)
        values[shifted] += direction
        positions = np.flatnonzero((~skip) & (errors == plan["peak"]))[:plan["used_bits"]]
        if len(positions) != plan["used_bits"]:
            raise RDHError("verification_failed", "实际峰值容量与规划不一致。")
        values[positions] += direction * bits[offset:offset + len(positions)].astype(np.int16)
        _write_samples(stego, plan["channel"], indices, values)
        offset += len(positions)
    if offset != len(bits):
        raise RDHError("verification_failed", "未完整嵌入秘密数据。")
    metadata = _metadata(stego, allocated, len(packet), _pixels_digest(original),
                         sha256(packet).hexdigest(), _pixels_digest(stego))
    auxiliary = _aux_bytes(metadata)
    png = _encode_png(stego)
    encoded = png[:-12] + _chunk(RDH_CHUNK, auxiliary) + png[-12:]
    if len(encoded) > MAX_CONTAINER_BYTES:
        raise RDHError("file_too_large", "RDH PNG 超过输出大小限制。")
    embedding_seconds = perf_counter() - started
    if progress: progress(70, "正在重新读取输出 PNG，逐字节及逐像素验证")
    verification_started = perf_counter()
    verified = extract_image(encoded)
    restored = _decode_rgb(verified["restored"], source=False)
    if verified["packet"] != packet or not np.array_equal(original, restored):
        raise RDHError("verification_failed", "秘密数据或原始 RGB 验证失败，未发布 RDH 图像。")
    metrics.update(auxiliary_bytes=len(auxiliary) + 12, stego_bytes=len(encoded),
                   original_file_bytes=len(data), embedding_seconds=embedding_seconds,
                   verification_seconds=perf_counter() - verification_started,
                   packet_verified=True, rgb_verified=True,
                   original_rgb_sha256=metadata["original_rgb_sha256"],
                   restored_rgb_sha256=verified["metrics"]["restored_rgb_sha256"],
                   packet_sha256=metadata["packet_sha256"], quality=_quality(original, stego))
    metrics["total_seconds"] = perf_counter() - started
    return {"source": _encode_png(original), "stego": encoded, "metrics": metrics}


def _parse_aux(auxiliary, width, height, sample_count):
    if not auxiliary.startswith(b"RDH1"):
        raise RDHError("invalid_auxiliary", "缺少 RDH 版本标记。")
    meta = decode_cbor_object(auxiliary[4:], max_bytes=MAX_AUX_BYTES)
    if (set(meta) != _ROOT_FIELDS or type(meta["version"]) is not int or meta["version"] != 1
            or meta["algorithm"] != ALGORITHM or type(meta["width"]) is not int or meta["width"] != width
            or type(meta["height"]) is not int or meta["height"] != height
            or type(meta["payload_bytes"]) is not int or not 1 <= meta["payload_bytes"] <= MAX_PACKET_BYTES
            or not isinstance(meta["plans"], list) or not 1 <= len(meta["plans"]) <= 3):
        raise RDHError("invalid_auxiliary", "RDH 辅助参数无效。")
    for key in ("packet_sha256", "original_rgb_sha256", "stego_rgb_sha256"):
        value = meta[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise RDHError("invalid_auxiliary", "RDH 校验摘要格式无效。")
    previous, embedded = -1, 0
    for plan in meta["plans"]:
        if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS:
            raise RDHError("invalid_auxiliary", "RDH 通道描述无效。")
        if (any(type(plan[key]) is not int for key in ("channel", "peak", "zero", "capacity_bits", "used_bits"))
                or not previous < plan["channel"] < 3 or not -255 <= plan["peak"] <= 255
                or not -255 <= plan["zero"] <= 255 or plan["peak"] == plan["zero"]
                or not 1 <= plan["used_bits"] <= plan["capacity_bits"] <= sample_count
                or not isinstance(plan["skip_map"], bytes)):
            raise RDHError("invalid_auxiliary", "RDH 通道参数超出边界。")
        previous = plan["channel"]
        embedded += plan["used_bits"]
    if embedded != meta["payload_bytes"] * 8:
        raise RDHError("invalid_auxiliary", "嵌入位数与数据包长度不一致。")
    return meta


def extract_image(data: bytes) -> dict:
    started = perf_counter()
    clean, auxiliary, width, height = _read_container(data, layer_chunk=RDH_CHUNK, max_layer_bytes=MAX_AUX_BYTES)
    pixels = _decode_rgb(clean, source=False)
    indices = _indices(height, width)
    meta = _parse_aux(auxiliary, width, height, len(indices))
    if _pixels_digest(pixels) != meta["stego_rgb_sha256"]:
        raise RDHError("image_modified", "RDH 图像像素已被修改，校验未通过。")
    restored, extracted = pixels.copy(), []
    for plan in meta["plans"]:
        values, predicted = _samples(restored, plan["channel"], indices)
        errors = values - predicted
        direction = 1 if plan["zero"] > plan["peak"] else -1
        boundary = 255 if direction == 1 else 0
        skip = _unpack_skip(plan, len(values))
        if np.any(values[skip] != boundary):
            raise RDHError("invalid_auxiliary", "边界位置图与像素不匹配。")
        peak_mask = (~skip) & ((errors == plan["peak"]) | (errors == plan["peak"] + direction))
        positions = np.flatnonzero(peak_mask)
        if len(positions) != plan["capacity_bits"]:
            raise RDHError("invalid_auxiliary", "峰值像素数量与辅助数据不一致。")
        extracted.append((errors[positions[:plan["used_bits"]]] == plan["peak"] + direction).astype(np.uint8))
        errors[peak_mask] = plan["peak"]
        shifted = ((errors > plan["peak"] + 1) & (errors <= plan["zero"])) if direction == 1 else ((errors >= plan["zero"]) & (errors < plan["peak"] - 1))
        errors[(~skip) & shifted] -= direction
        values = predicted + errors
        if not np.array_equal(skip, values == boundary):
            raise RDHError("invalid_auxiliary", "恢复后的边界位置图不一致。")
        _write_samples(restored, plan["channel"], indices, values)
    packet = np.packbits(np.concatenate(extracted), bitorder="big").tobytes()
    packet_hash, rgb_hash = sha256(packet).hexdigest(), _pixels_digest(restored)
    if len(packet) != meta["payload_bytes"] or packet_hash != meta["packet_sha256"]:
        raise RDHError("packet_integrity_failed", "秘密数据 SHA-256 校验失败。")
    if rgb_hash != meta["original_rgb_sha256"]:
        raise RDHError("rgb_integrity_failed", "原始 RGB 像素 SHA-256 校验失败。")
    return {"packet": packet, "restored": _encode_png(restored), "metrics": {
        "algorithm": ALGORITHM, "width": width, "height": height,
        "packet_bytes": len(packet), "embedded_bits": len(packet) * 8,
        "auxiliary_bytes": len(auxiliary) + 12, "location_map_bytes": sum(len(p["skip_map"]) for p in meta["plans"]),
        "packet_verified": True, "rgb_verified": True, "packet_sha256": packet_hash,
        "original_rgb_sha256": meta["original_rgb_sha256"], "restored_rgb_sha256": rgb_hash,
        "extraction_seconds": perf_counter() - started,
    }}
