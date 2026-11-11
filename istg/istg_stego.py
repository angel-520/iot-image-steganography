"""ISTG 图像隐写嵌入与提取库。

本模块把任意 ``bytes``（通常是 ``encrypt_istg`` 返回的完整加密包）嵌入
载体图像，并在提取时逐字节恢复。所有输出都要求使用 PNG，以免有损重编码
破坏隐藏数据。

公开入口是 :func:`embed_bytes` / :func:`extract_bytes`，也提供每种算法的
同名便捷函数，以及图片文件字节、LSB-MSB 位平面替换两个图片接口。

``jsteg``（别名 ``jsteg_demo``）是使用 PNG 保存的 DCT 教学变体，
不提供经典 JPEG 系数域 JSteg 编解码。所有嵌入结果在保存前校验完整帧。
"""

from __future__ import annotations

from hashlib import sha256
import math
from numbers import Real
from pathlib import Path
import struct
from typing import Callable
import zlib

import numpy as np
from PIL import Image


_FRAME = struct.Struct(">4sBBII")
_FRAME_MAGIC = b"STG1"
_FRAME_VERSION = 1
_FRAME_BITS = _FRAME.size * 8

_IMAGE_FRAME = struct.Struct(">4sIIBI")
_IMAGE_MAGIC = b"IMG1"

_METHOD_IDS = {
    "sequential_lsb": 1,
    "scatter_lsb": 2,
    "lsb_matching": 3,
    "adaptive_edge_lsb": 4,
    "dct": 5,
    "dwt": 6,
    "jsteg": 7,
    "lsb_msb": 8,
}

_ALIASES = {
    "sequential": "sequential_lsb",
    "sequential_lsb": "sequential_lsb",
    "random_lsb": "scatter_lsb",
    "random": "scatter_lsb",
    "scatter": "scatter_lsb",
    "scatter_lsb": "scatter_lsb",
    "lsb_matching": "lsb_matching",
    "matching": "lsb_matching",
    "adaptive": "adaptive_edge_lsb",
    "adaptive_edge": "adaptive_edge_lsb",
    "adaptive_edge_lsb": "adaptive_edge_lsb",
    "dct": "dct",
    "dwt": "dwt",
    "jsteg": "jsteg",
    "j_steg": "jsteg",
    "jsteg_demo": "jsteg",
    "lsb_msb": "lsb_msb",
}

_DCT_POSITIONS = (
    (1, 2), (2, 1), (2, 2), (1, 3),
    (3, 1), (2, 3), (3, 2), (1, 4),
)
_JSTEG_POSITIONS = (
    (0, 2), (1, 1), (2, 0), (0, 3),
    (1, 2), (2, 1), (3, 0), (0, 4),
    (1, 3), (2, 2), (3, 1), (4, 0),
)


def _normalise_method(method: str) -> str:
    if not isinstance(method, str):
        raise TypeError("method 必须是 str")
    name = method.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return _ALIASES[name]
    except KeyError as exc:
        supported = ", ".join(_METHOD_IDS)
        raise ValueError(f"不支持的隐写算法 {method!r}；可选：{supported}") from exc


def _parse_channels(channels: str) -> np.ndarray:
    if not isinstance(channels, str):
        raise TypeError("channels 必须是 str")
    text = channels.upper()
    if not text or any(c not in "RGB" for c in text) or len(set(text)) != len(text):
        raise ValueError("channels 必须由不重复的 R、G、B 组成，例如 'RGB' 或 'B'")
    return np.asarray(["RGB".index(c) for c in text], dtype=np.int64)


def _check_bit_depth(bit_depth: int) -> None:
    if not isinstance(bit_depth, int) or isinstance(bit_depth, bool) or not 1 <= bit_depth <= 4:
        raise ValueError("bit_depth 必须是 1~4 的整数")


def _finite_number(value: Real, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} 必须是有限实数")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限实数") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限实数")
    return result


def _validate_strength(method: str, strength: float | None) -> float:
    defaults = {"dct": 16.0, "dwt": 8.0, "jsteg": 18.0}
    minimum = 4.0 if method == "dwt" else 8.0
    value = defaults[method] if strength is None else _finite_number(strength, "strength")
    if value < minimum:
        raise ValueError(f"{method} strength 必须不小于 {minimum:g}")
    return value


def _validate_parameters(method, bit_depth, channels, key, edge_threshold, strength):
    """容量计算、嵌入、提取使用同一套相关参数校验。"""
    _check_bit_depth(bit_depth)
    channel_ids = _parse_channels(channels)
    if method == "lsb_matching" and bit_depth != 1:
        raise ValueError("LSB Matching 仅支持 bit_depth=1")
    if method in {"scatter_lsb", "lsb_matching"}:
        _seed(key, required=method == "scatter_lsb")
    if method == "adaptive_edge_lsb":
        edge_threshold = _finite_number(edge_threshold, "edge_threshold")
        if edge_threshold < 0:
            raise ValueError("edge_threshold 必须是非负数")
    if method in {"dct", "dwt", "jsteg"}:
        strength = _validate_strength(method, strength)
    return channel_ids, edge_threshold, strength


def _check_distinct_paths(path: str | Path, source: str | Path) -> None:
    target, original = Path(path), Path(source)
    same_path = target.resolve() == original.resolve()
    same_file = target.is_file() and original.is_file() and target.samefile(original)
    if same_path or same_file:
        raise ValueError("输出路径必须与输入载体或秘密图片不同，避免覆盖原图")


def _validate_output_path(path: str | Path, source: str | Path | None = None) -> Path:
    target = Path(path)
    if target.suffix.lower() != ".png":
        raise ValueError("隐写图输出必须以 .png 结尾，JPEG 等有损格式会破坏隐藏数据")
    if source is not None:
        _check_distinct_paths(target, source)
    return target


def _load_rgb(path: str | Path) -> np.ndarray:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"图像不存在：{source}")
    with Image.open(source) as image:
        return np.array(image.convert("RGB"), dtype=np.uint8)


def _save_png(path: str | Path, array: np.ndarray) -> None:
    target = _validate_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array.astype(np.uint8)).save(target, format="PNG")


def _make_frame(data: bytes, method: str) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data 必须是 bytes")
    if len(data) > 0xFFFFFFFF:
        raise ValueError("隐藏数据不能超过 4 GiB")
    crc = zlib.crc32(data) & 0xFFFFFFFF
    return _FRAME.pack(
        _FRAME_MAGIC, _FRAME_VERSION, _METHOD_IDS[method], len(data), crc
    ) + data


def _bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")


def _header_from_bits(bits: np.ndarray, expected_method: str) -> tuple[int, int]:
    if bits.size < _FRAME_BITS:
        raise ValueError("载体容量不足，无法包含隐写帧头")
    raw = np.packbits(bits[:_FRAME_BITS], bitorder="big").tobytes()
    magic, version, method_id, length, crc = _FRAME.unpack(raw)
    if magic != _FRAME_MAGIC or version != _FRAME_VERSION:
        raise ValueError("未找到有效的 STG1 隐写帧；算法或参数可能不匹配")
    if method_id != _METHOD_IDS[expected_method]:
        raise ValueError("隐写帧中的算法与提取时指定的 method 不一致")
    return length, crc


def _payload_from_bits(bits: np.ndarray, method: str) -> bytes:
    length, expected_crc = _header_from_bits(bits, method)
    needed = (_FRAME.size + length) * 8
    if bits.size < needed:
        raise ValueError("隐写帧声明的长度超过载体可提取容量")
    raw = np.packbits(bits[:needed], bitorder="big").tobytes()
    payload = raw[_FRAME.size:_FRAME.size + length]
    if (zlib.crc32(payload) & 0xFFFFFFFF) != expected_crc:
        raise ValueError("隐藏数据 CRC32 校验失败；图像或提取参数已改变")
    return payload


def _seed(key: str | bytes | None, *, required: bool = False) -> int:
    if key is None:
        if required:
            raise ValueError("Scatter LSB 必须提供 key")
        key_bytes = b"ISTG-default-matching-seed"
    elif isinstance(key, str):
        if not key:
            raise ValueError("key 不能为空")
        key_bytes = key.encode("utf-8")
    elif isinstance(key, bytes):
        if not key:
            raise ValueError("key 不能为空")
        key_bytes = key
    else:
        raise TypeError("key 必须是 str、bytes 或 None")
    return int.from_bytes(sha256(b"ISTG-STEG\0" + key_bytes).digest()[:8], "big")


def _value_indices(shape: tuple[int, int, int], channels: np.ndarray) -> np.ndarray:
    pixels = shape[0] * shape[1]
    return (np.arange(pixels, dtype=np.int64)[:, None] * 3 + channels).reshape(-1)


def _lsb_slots(
    array: np.ndarray,
    channels: np.ndarray,
    bit_depth: int,
    method: str,
    key: str | bytes | None,
    edge_threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """返回每个可用 bit 槽对应的扁平通道索引和位平面编号。"""
    if method == "adaptive_edge_lsb":
        edge_threshold = _finite_number(edge_threshold, "edge_threshold")
        if edge_threshold < 0:
            raise ValueError("edge_threshold 必须是非负数")
        # 清除即将写入的低位后再计算边缘，保证嵌入前后排序完全一致。
        clean = array & np.uint8(0xFF ^ ((1 << bit_depth) - 1))
        gray = (
            clean[:, :, 0].astype(np.float32) * 0.299
            + clean[:, :, 1].astype(np.float32) * 0.587
            + clean[:, :, 2].astype(np.float32) * 0.114
        )
        gx = np.zeros_like(gray)
        gy = np.zeros_like(gray)
        gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
        gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
        score = np.hypot(gx, gy).reshape(-1)
        eligible = np.flatnonzero(score >= float(edge_threshold))
        # lexsort 的第二键是主键：先按分数降序，同分时按像素序号升序。
        order = np.lexsort((eligible, -score[eligible]))
        pixels = eligible[order]
        values = (pixels[:, None] * 3 + channels).reshape(-1)
    else:
        values = _value_indices(array.shape, channels)

    slot_values = np.repeat(values, bit_depth)
    slot_planes = np.tile(np.arange(bit_depth, dtype=np.uint8), values.size)
    if method == "scatter_lsb":
        rng = np.random.default_rng(_seed(key, required=True))
        order = rng.permutation(slot_values.size)
        slot_values = slot_values[order]
        slot_planes = slot_planes[order]
    return slot_values, slot_planes


def _embed_lsb(
    array: np.ndarray,
    frame_bits: np.ndarray,
    method: str,
    channels: np.ndarray,
    bit_depth: int,
    key: str | bytes | None,
    edge_threshold: float,
) -> np.ndarray:
    values, planes = _lsb_slots(
        array, channels, bit_depth, method, key, edge_threshold
    )
    if frame_bits.size > values.size:
        raise ValueError(
            f"载体容量不足：需要 {frame_bits.size} bit，可用 {values.size} bit"
        )
    result = array.copy()
    flat = result.reshape(-1)
    used_values = values[:frame_bits.size]
    used_planes = planes[:frame_bits.size]

    if method == "lsb_matching":
        if bit_depth != 1:
            raise ValueError("LSB Matching 仅支持 bit_depth=1")
        current = flat[used_values]
        mismatch = (current & 1) != frame_bits
        selected = used_values[mismatch]
        current = flat[selected].astype(np.int16)
        rng = np.random.default_rng(_seed(key))
        direction = rng.choice(np.asarray([-1, 1], dtype=np.int16), current.size)
        direction[current == 0] = 1
        direction[current == 255] = -1
        flat[selected] = (current + direction).astype(np.uint8)
    else:
        # 同一通道值在多位嵌入时会出现多次；按位平面分组赋值可避免 NumPy
        # 高级索引的重复下标“最后一次写入覆盖前一次写入”。
        for plane in range(bit_depth):
            selected = used_planes == plane
            indices = used_values[selected]
            mask = np.uint8(1 << plane)
            current = flat[indices]
            wanted = frame_bits[selected].astype(np.uint8) << np.uint8(plane)
            flat[indices] = (current & np.uint8(0xFF ^ int(mask))) | wanted
    return result


def _extract_lsb(
    array: np.ndarray,
    count: int,
    method: str,
    channels: np.ndarray,
    bit_depth: int,
    key: str | bytes | None,
    edge_threshold: float,
) -> np.ndarray:
    values, planes = _lsb_slots(
        array, channels, bit_depth, method, key, edge_threshold
    )
    if count > values.size:
        raise ValueError("请求提取的 bit 数超过载体容量")
    flat = array.reshape(-1)
    return ((flat[values[:count]] >> planes[:count]) & 1).astype(np.uint8)


def _dct_matrix() -> np.ndarray:
    matrix = np.empty((8, 8), dtype=np.float64)
    for u in range(8):
        alpha = np.sqrt(1 / 8) if u == 0 else np.sqrt(2 / 8)
        for x in range(8):
            matrix[u, x] = alpha * np.cos(np.pi * (2 * x + 1) * u / 16)
    return matrix


_DCT_C = _dct_matrix()


def _set_quantised_parity(value: float, bit: int, strength: float, minimum: int = 0) -> float:
    """选择最近的合法奇偶格点，避免始终向远离零的方向增大系数。"""
    centre = int(np.rint(value / strength))
    candidates = set(range(centre - 2, centre + 3))
    candidates.update((minimum, minimum + 1, -minimum, -minimum - 1))
    allowed = (q for q in candidates if abs(q) >= minimum and (q & 1) == int(bit))
    q = min(allowed, key=lambda q: (
        abs(float(q) * strength - value), abs(q), (q < 0) != (value < 0),
    ))
    return float(q) * strength


def _rebuild_stable_dct_block(
    original: np.ndarray,
    positions: tuple[tuple[int, int], ...],
    bits: np.ndarray,
    strength: float,
    minimum: int = 0,
    eligible: np.ndarray | None = None,
) -> np.ndarray:
    """在实际 uint8 块上验证量化格点；失败时压缩 AC 后有限重试。

    JSteg 同时固定候选和跳过位置的集合，提取不依赖原载体。整块平移
    只调整 DC；任何尝试都不通过裁剪来掩盖超出像素范围的结果。
    """
    original_q = np.asarray([int(np.rint(original[u, v] / strength)) for u, v in positions])
    for scale in (1.0, 0.9, 0.75, 0.6, 0.45, 0.3, 0.15, 0.05):
        coeff = original * scale
        coeff[0, 0] = original[0, 0]
        expected = []
        index = 0
        for slot, (u, v) in enumerate(positions):
            if eligible is not None and not eligible[slot]:
                q = int(np.clip(np.rint(coeff[u, v] / strength), -1, 1))
                coeff[u, v] = float(q) * strength
            elif index < bits.size:
                coeff[u, v] = _set_quantised_parity(
                    coeff[u, v], int(bits[index]), strength, minimum
                )
                q = int(np.rint(coeff[u, v] / strength))
                index += 1
            elif eligible is not None:
                coeff[u, v] = _set_quantised_parity(
                    coeff[u, v], int(original_q[slot] & 1), strength, minimum
                )
                q = int(np.rint(coeff[u, v] / strength))
            else:
                # 最后一块未承载数据的位置保持自由，不影响提取到的位流。
                continue
            expected.append((u, v, q))
        with np.errstate(over="ignore", invalid="ignore"):
            rebuilt = _DCT_C.T @ coeff @ _DCT_C + 128.0
        if not np.isfinite(rebuilt).all():
            continue
        low, high = float(rebuilt.min()), float(rebuilt.max())
        if high - low > 253.0:
            continue
        shift = float(np.clip(0.0, 1.0 - low, 254.0 - high))
        integer = np.rint(rebuilt + shift).astype(np.uint8)
        recovered = _DCT_C @ (integer.astype(np.float64) - 128.0) @ _DCT_C.T
        if all(int(np.rint(recovered[u, v] / strength)) == q for u, v, q in expected):
            return integer
    raise ValueError("当前 strength 无法在 8 位像素范围内稳定嵌入此 DCT 块；请降低强度或更换载体")


def _frequency_capacity(array: np.ndarray, channels: np.ndarray, positions: tuple) -> int:
    return (array.shape[0] // 8) * (array.shape[1] // 8) * channels.size * len(positions)


def _embed_dct_like(
    array: np.ndarray,
    frame_bits: np.ndarray,
    channels: np.ndarray,
    strength: float,
    positions: tuple[tuple[int, int], ...],
    minimum: int,
) -> np.ndarray:
    strength = _validate_strength("dct", strength)
    capacity = _frequency_capacity(array, channels, positions)
    if frame_bits.size > capacity:
        raise ValueError(f"载体容量不足：需要 {frame_bits.size} bit，可用 {capacity} bit")

    result = array.copy()
    bit_index = 0
    height = (array.shape[0] // 8) * 8
    width = (array.shape[1] // 8) * 8
    for channel in channels:
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                if bit_index >= frame_bits.size:
                    return result
                spatial = result[y:y + 8, x:x + 8, channel].astype(np.float64)
                block = spatial - 128.0
                coeff = _DCT_C @ block @ _DCT_C.T
                used = min(len(positions), frame_bits.size - bit_index)
                result[y:y + 8, x:x + 8, channel] = _rebuild_stable_dct_block(
                    coeff, positions, frame_bits[bit_index:bit_index + used], strength, minimum
                )
                bit_index += used
    return result


def _extract_dct_like(
    array: np.ndarray,
    count: int,
    channels: np.ndarray,
    strength: float,
    positions: tuple[tuple[int, int], ...],
) -> np.ndarray:
    strength = _validate_strength("dct", strength)
    capacity = _frequency_capacity(array, channels, positions)
    if count > capacity:
        raise ValueError("请求提取的 bit 数超过载体容量")
    output = np.empty(count, dtype=np.uint8)
    index = 0
    height = (array.shape[0] // 8) * 8
    width = (array.shape[1] // 8) * 8
    source = array.astype(np.float64)
    for channel in channels:
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                block = source[y:y + 8, x:x + 8, channel] - 128.0
                coeff = _DCT_C @ block @ _DCT_C.T
                for u, v in positions:
                    if index >= count:
                        return output
                    output[index] = int(np.rint(coeff[u, v] / float(strength))) & 1
                    index += 1
    return output


def _jsteg_spatial_block(
    array: np.ndarray, y: int, x: int, channel: int, *, precondition: bool = True
) -> np.ndarray:
    """嵌入及容量估计对极端块预处理；提取始终使用原始隐写像素。"""
    spatial = array[y:y + 8, x:x + 8, channel].astype(np.float64).copy()
    low, high = float(spatial.min()), float(spatial.max())
    if precondition and high - low > 240.0:
        # 初次压缩为量化留出余量；最终可恢复性由块重建函数实际验证。
        spatial = 48.0 + (spatial - low) * (160.0 / (high - low))
    return spatial


def _jsteg_capacity(array: np.ndarray, channels: np.ndarray, strength: float) -> int:
    """按 JSteg 规则统计非 0、非 ±1 的量化 AC 系数。"""
    strength = _validate_strength("jsteg", strength)
    count = 0
    height = (array.shape[0] // 8) * 8
    width = (array.shape[1] // 8) * 8
    for channel in channels:
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                spatial = _jsteg_spatial_block(array, y, x, int(channel))
                coeff = _DCT_C @ (spatial - 128.0) @ _DCT_C.T
                for u, v in _JSTEG_POSITIONS:
                    if abs(int(np.rint(coeff[u, v] / strength))) > 1:
                        count += 1
    return count


def _embed_jsteg(
    array: np.ndarray,
    frame_bits: np.ndarray,
    channels: np.ndarray,
    strength: float,
) -> np.ndarray:
    strength = _validate_strength("jsteg", strength)
    capacity = _jsteg_capacity(array, channels, strength)
    if frame_bits.size > capacity:
        raise ValueError(f"载体容量不足：需要 {frame_bits.size} bit，可用 {capacity} bit")
    result = array.copy()
    index = 0
    height = (array.shape[0] // 8) * 8
    width = (array.shape[1] // 8) * 8
    for channel in channels:
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                if index >= frame_bits.size:
                    return result
                spatial = _jsteg_spatial_block(result, y, x, int(channel))
                coeff = _DCT_C @ (spatial - 128.0) @ _DCT_C.T
                eligible = np.asarray([
                    abs(int(np.rint(coeff[u, v] / strength))) > 1
                    for u, v in _JSTEG_POSITIONS
                ])
                used = min(int(eligible.sum()), frame_bits.size - index)
                result[y:y + 8, x:x + 8, channel] = _rebuild_stable_dct_block(
                    coeff, _JSTEG_POSITIONS, frame_bits[index:index + used], strength, 2, eligible
                )
                index += used
    return result


def _extract_jsteg(
    array: np.ndarray,
    count: int,
    channels: np.ndarray,
    strength: float,
) -> np.ndarray:
    strength = _validate_strength("jsteg", strength)
    # count 来自不可信 STG1 长度，先检查物理上限，避免先申请巨型 bit 数组。
    capacity = _frequency_capacity(array, channels, _JSTEG_POSITIONS)
    if count < 0 or count > capacity:
        raise ValueError("请求提取的 bit 数超过 JSteg 载体容量")
    output = np.empty(count, dtype=np.uint8)
    index = 0
    height = (array.shape[0] // 8) * 8
    width = (array.shape[1] // 8) * 8
    source = array.astype(np.float64)
    for channel in channels:
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                spatial = _jsteg_spatial_block(
                    source, y, x, int(channel), precondition=False
                )
                coeff = _DCT_C @ (spatial - 128.0) @ _DCT_C.T
                for u, v in _JSTEG_POSITIONS:
                    q = int(np.rint(coeff[u, v] / strength))
                    if abs(q) <= 1:
                        continue
                    if index >= count:
                        return output
                    output[index] = q & 1
                    index += 1
    if index != count:
        raise ValueError("请求提取的 bit 数超过 JSteg 可用系数容量")
    return output


def _dwt_capacity(array: np.ndarray, channels: np.ndarray) -> int:
    return (array.shape[0] // 2) * (array.shape[1] // 2) * channels.size


def _dwt_embed_block(spatial: np.ndarray, bit: int, strength: float) -> np.ndarray:
    """在可表示的 HH 奇偶格点中就近选择，并验证最终整数像素。

    HH 以外的高频分量只在动态范围不足时缩小；整块平移只影响 LL。
    极端强度下使用精确整数 HH 的块，避免靠裁剪改变目标系数。
    """
    if strength >= 510.0:
        # 8 位像素的 |HH| 最大为 255；此时取整后只能得到偶数 0。
        raise ValueError("DWT strength 必须小于 510，8 位像素无法表示奇数隐藏位")
    a, b = spatial[0, 0], spatial[0, 1]
    c, d = spatial[1, 0], spatial[1, 1]
    hh = (a - b - c + d) / 2.0
    mean = float(spatial.mean())
    signs = np.asarray([[1.0, -1.0], [-1.0, 1.0]])
    nearest_q = 2 * int(np.rint((hh / strength - bit) / 2.0)) + bit
    candidates = []
    for q in {nearest_q - 2, nearest_q, nearest_q + 2, bit, -bit}:
        target_hh = float(q) * strength
        # 预留取整余量；任意该范围内的 HH 都可用纯对角分量表示。
        if abs(target_hh) <= 253.0:
            candidates.append((abs(target_hh - hh), target_hh))

    if candidates:
        target_hh = min(candidates)[1]
        diagonal = signs * (target_hh / 2.0)
        other = spatial - mean - signs * (hh / 2.0)
        combined = diagonal + other
        if float(np.ptp(combined)) > 253.0:
            # 对所有像素对解 d_i-d_j + t*(o_i-o_j) <=253，取最大的 t。
            diag_flat, other_flat = diagonal.reshape(-1), other.reshape(-1)
            other_diff = other_flat[:, None] - other_flat[None, :]
            diagonal_diff = diag_flat[:, None] - diag_flat[None, :]
            positive = other_diff > 0
            scale = min(1.0, float(np.min(
                (253.0 - diagonal_diff[positive]) / other_diff[positive]
            )))
            combined = diagonal + max(0.0, scale) * other
        lower_mean = 1.0 - float(combined.min())
        upper_mean = 254.0 - float(combined.max())
        shifted_mean = min(max(mean, lower_mean), upper_mean)
        output = np.rint(combined + shifted_mean).astype(np.uint8)
        values = output.astype(np.float64)
        actual_hh = (values[0, 0] - values[0, 1] - values[1, 0] + values[1, 1]) / 2.0
        if (int(np.rint(actual_hh / strength)) & 1) == bit:
            return output

    # 整数 HH 可通过两个对角线的整数像素差精确表示，不再受浮点取整影响。
    integer_hh = np.arange(-255, 256, dtype=np.float64)
    eligible = integer_hh[(np.rint(integer_hh / strength).astype(np.int64) & 1) == bit]
    if eligible.size == 0:
        raise ValueError("DWT strength 过大，8 位像素无法可靠表示目标隐藏位")
    target = int(eligible[np.argmin(np.abs(eligible - hh))])
    contrast = abs(target)
    low = int(np.clip(np.rint(mean - contrast / 2.0), 0, 255 - contrast))
    high = low + contrast
    if target >= 0:
        return np.asarray([[high, low], [low, high]], dtype=np.uint8)
    return np.asarray([[low, high], [high, low]], dtype=np.uint8)


def _embed_dwt(
    array: np.ndarray,
    frame_bits: np.ndarray,
    channels: np.ndarray,
    strength: float,
) -> np.ndarray:
    strength = _validate_strength("dwt", strength)
    capacity = _dwt_capacity(array, channels)
    if frame_bits.size > capacity:
        raise ValueError(f"载体容量不足：需要 {frame_bits.size} bit，可用 {capacity} bit")
    result = array.copy()
    index = 0
    height = (array.shape[0] // 2) * 2
    width = (array.shape[1] // 2) * 2
    for channel in channels:
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                if index >= frame_bits.size:
                    return result
                spatial = result[y:y + 2, x:x + 2, channel].astype(np.float64)
                result[y:y + 2, x:x + 2, channel] = _dwt_embed_block(
                    spatial, int(frame_bits[index]), strength
                )
                index += 1
    return result


def _extract_dwt(
    array: np.ndarray,
    count: int,
    channels: np.ndarray,
    strength: float,
) -> np.ndarray:
    strength = _validate_strength("dwt", strength)
    capacity = _dwt_capacity(array, channels)
    if count < 0 or count > capacity:
        raise ValueError("请求提取的 bit 数超过载体容量")
    output = np.empty(count, dtype=np.uint8)
    index = 0
    height = (array.shape[0] // 2) * 2
    width = (array.shape[1] // 2) * 2
    source = array.astype(np.float64)
    for channel in channels:
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                if index >= count:
                    return output
                a, b = source[y, x, channel], source[y, x + 1, channel]
                c, d = source[y + 1, x, channel], source[y + 1, x + 1, channel]
                hh = (a - b - c + d) / 2
                output[index] = int(np.rint(hh / float(strength))) & 1
                index += 1
    return output


def _read_embedded_bits(array, count, method, channels, bit_depth, key, edge_threshold, strength):
    """在内存像素上提取，供公开提取和保存前验证共用。"""
    if method in {"sequential_lsb", "scatter_lsb", "lsb_matching", "adaptive_edge_lsb", "lsb_msb"}:
        return _extract_lsb(array, count, method, channels, bit_depth, key, edge_threshold)
    if method == "dct":
        return _extract_dct_like(array, count, channels, strength, _DCT_POSITIONS)
    if method == "jsteg":
        return _extract_jsteg(array, count, channels, strength)
    return _extract_dwt(array, count, channels, strength)


def capacity_bytes(
    carrier_path: str | Path,
    method: str = "sequential_lsb",
    *,
    bit_depth: int = 1,
    channels: str = "RGB",
    key: str | bytes | None = None,
    edge_threshold: float = 20.0,
    strength: float | None = None,
) -> int:
    """返回扣除帧头后的数据容量；变换域仍需通过实际重建校验。

    返回 0 时也可能连帧头都放不下，空载荷仍可能被拒绝。
    """
    name = _normalise_method(method)
    channel_ids, edge_threshold, strength = _validate_parameters(
        name, bit_depth, channels, key, edge_threshold, strength
    )
    array = _load_rgb(carrier_path)
    if name in {"sequential_lsb", "scatter_lsb", "lsb_matching", "lsb_msb"}:
        bits_available = array.shape[0] * array.shape[1] * channel_ids.size * bit_depth
    elif name == "adaptive_edge_lsb":
        values, _ = _lsb_slots(array, channel_ids, bit_depth, name, key, edge_threshold)
        bits_available = values.size
    elif name == "dct":
        bits_available = _frequency_capacity(array, channel_ids, _DCT_POSITIONS)
    elif name == "jsteg":
        bits_available = _jsteg_capacity(array, channel_ids, strength)
    else:
        bits_available = _dwt_capacity(array, channel_ids)
    return max(0, bits_available // 8 - _FRAME.size)


def embed_bytes(
    carrier_path: str | Path,
    data: bytes,
    output_path: str | Path,
    method: str = "sequential_lsb",
    *,
    key: str | bytes | None = None,
    bit_depth: int = 1,
    channels: str = "RGB",
    edge_threshold: float = 20.0,
    strength: float | None = None,
) -> Path:
    """把完整字节串嵌入载体，返回生成的 PNG 路径。

    ``key`` 对 Scatter LSB 必填，对 LSB Matching 用于控制 ±1 方向。
    ``strength`` 仅供 DCT/DWT/JSteg 使用；默认分别为 16、8、18。
    转成最终 8 位像素后完整校验；无法可靠恢复时拒绝保存。
    """
    name = _normalise_method(method)
    channel_ids, edge_threshold, strength = _validate_parameters(
        name, bit_depth, channels, key, edge_threshold, strength
    )
    target = _validate_output_path(output_path, carrier_path)
    array = _load_rgb(carrier_path)
    frame_bits = _bits(_make_frame(data, name))

    if name in {"sequential_lsb", "scatter_lsb", "lsb_matching", "adaptive_edge_lsb", "lsb_msb"}:
        result = _embed_lsb(
            array, frame_bits, name, channel_ids, bit_depth, key, edge_threshold
        )
    elif name == "dct":
        result = _embed_dct_like(
            array, frame_bits, channel_ids, strength,
            _DCT_POSITIONS, 0,
        )
    elif name == "jsteg":
        # PNG 教学变体：在选定 DCT 系数上量化嵌入，不处理 JPEG 码流。
        result = _embed_jsteg(
            array, frame_bits, channel_ids, strength,
        )
    else:
        result = _embed_dwt(
            array, frame_bits, channel_ids, strength
        )
    if result.shape != array.shape or result.dtype != np.uint8:
        raise ValueError("嵌入结果必须是与载体同尺寸的 uint8 RGB 像素")
    try:
        extracted = _payload_from_bits(
            _read_embedded_bits(result, frame_bits.size, name, channel_ids,
                                bit_depth, key, edge_threshold, strength), name
        )
    except (ValueError, OverflowError) as exc:
        raise ValueError("嵌入后的完整帧校验失败，无法可靠恢复；未保存输出") from exc
    if extracted != data:
        raise ValueError("嵌入结果与原始数据不一致；未保存输出")
    _save_png(target, result)
    return target


def extract_bytes(
    stego_path: str | Path,
    method: str = "sequential_lsb",
    *,
    key: str | bytes | None = None,
    bit_depth: int = 1,
    channels: str = "RGB",
    edge_threshold: float = 20.0,
    strength: float | None = None,
) -> bytes:
    """从隐写图中提取并校验原始字节串。参数必须与嵌入时一致。"""
    name = _normalise_method(method)
    channel_ids, edge_threshold, strength = _validate_parameters(
        name, bit_depth, channels, key, edge_threshold, strength
    )
    array = _load_rgb(stego_path)

    def read(count: int) -> np.ndarray:
        return _read_embedded_bits(
            array, count, name, channel_ids, bit_depth, key, edge_threshold, strength
        )

    header_bits = read(_FRAME_BITS)
    length, _ = _header_from_bits(header_bits, name)
    all_bits = read((_FRAME.size + length) * 8)
    return _payload_from_bits(all_bits, name)


def embed_image_file(
    carrier_path: str | Path,
    secret_image_path: str | Path,
    output_path: str | Path,
    method: str = "sequential_lsb",
    **kwargs,
) -> Path:
    """读取秘密图片的完整文件字节并嵌入，可无损恢复原文件。"""
    _validate_output_path(output_path, secret_image_path)
    source = Path(secret_image_path)
    if not source.is_file():
        raise FileNotFoundError(f"秘密图片不存在：{source}")
    return embed_bytes(carrier_path, source.read_bytes(), output_path, method, **kwargs)


def extract_image_file(
    stego_path: str | Path,
    output_image_path: str | Path,
    method: str = "sequential_lsb",
    **kwargs,
) -> Path:
    """提取图片文件字节并写入指定文件。"""
    _check_distinct_paths(output_image_path, stego_path)
    data = extract_bytes(stego_path, method, **kwargs)
    target = Path(output_image_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _pack_image_planes(secret_image_path: str | Path, secret_bits: int) -> bytes:
    _check_bit_depth(secret_bits)
    source = Path(secret_image_path)
    if not source.is_file():
        raise FileNotFoundError(f"秘密图片不存在：{source}")
    with Image.open(source) as image:
        rgb = np.array(image.convert("RGB"), dtype=np.uint8)
    symbols = (rgb.reshape(-1) >> (8 - secret_bits)).astype(np.uint8)
    shifts = np.arange(secret_bits - 1, -1, -1, dtype=np.uint8)
    plane_bits = ((symbols[:, None] >> shifts) & 1).reshape(-1)
    packed = np.packbits(plane_bits, bitorder="big").tobytes()
    header = _IMAGE_FRAME.pack(
        _IMAGE_MAGIC, rgb.shape[1], rgb.shape[0], secret_bits, len(packed)
    )
    return header + packed


def embed_image_bitplanes(
    carrier_path: str | Path,
    secret_image_path: str | Path,
    output_path: str | Path,
    *,
    secret_bits: int = 2,
    carrier_bits: int = 2,
    channels: str = "RGB",
) -> Path:
    """用载体 LSB 保存秘密图片的 MSB 位平面（有损图片隐写）。"""
    _validate_output_path(output_path, secret_image_path)
    payload = _pack_image_planes(secret_image_path, secret_bits)
    return embed_bytes(
        carrier_path, payload, output_path, "lsb_msb",
        bit_depth=carrier_bits, channels=channels,
    )


def extract_image_bitplanes(
    stego_path: str | Path,
    output_image_path: str | Path,
    *,
    carrier_bits: int = 2,
    channels: str = "RGB",
) -> Path:
    """提取 LSB-MSB 位平面并重建低位补零的 RGB 图片。"""
    _validate_output_path(output_image_path, stego_path)
    payload = extract_bytes(
        stego_path, "lsb_msb", bit_depth=carrier_bits, channels=channels
    )
    if len(payload) < _IMAGE_FRAME.size:
        raise ValueError("位平面图片数据过短")
    magic, width, height, secret_bits, packed_length = _IMAGE_FRAME.unpack(
        payload[:_IMAGE_FRAME.size]
    )
    if magic != _IMAGE_MAGIC or not 1 <= secret_bits <= 4 or width == 0 or height == 0:
        raise ValueError("无效的 IMG1 位平面图片帧")
    packed = payload[_IMAGE_FRAME.size:]
    symbol_count = width * height * 3
    needed_bits = symbol_count * secret_bits
    if len(packed) != packed_length or packed_length != (needed_bits + 7) // 8:
        raise ValueError("位平面图片长度不匹配")
    padding = (-needed_bits) % 8
    if padding and packed[-1] & ((1 << padding) - 1):
        raise ValueError("位平面图片末尾补零无效")
    raw_bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8), bitorder="big")
    if raw_bits.size < needed_bits:
        raise ValueError("位平面图片数据不完整")
    groups = raw_bits[:needed_bits].reshape(symbol_count, secret_bits)
    weights = (1 << np.arange(secret_bits - 1, -1, -1)).astype(np.uint8)
    symbols = (groups * weights).sum(axis=1).astype(np.uint8)
    image_array = (symbols << (8 - secret_bits)).reshape(height, width, 3)
    _save_png(output_image_path, image_array)
    return Path(output_image_path)


def _embed_wrapper(method: str) -> Callable:
    def wrapper(carrier_path, data, output_path, **kwargs):
        return embed_bytes(carrier_path, data, output_path, method, **kwargs)
    return wrapper


def _extract_wrapper(method: str) -> Callable:
    def wrapper(stego_path, **kwargs):
        return extract_bytes(stego_path, method, **kwargs)
    return wrapper


# 明确命名的算法入口，便于教学代码逐个调用。
sequential_lsb_embed = _embed_wrapper("sequential_lsb")
sequential_lsb_extract = _extract_wrapper("sequential_lsb")
scatter_lsb_embed = _embed_wrapper("scatter_lsb")
scatter_lsb_extract = _extract_wrapper("scatter_lsb")
lsb_matching_embed = _embed_wrapper("lsb_matching")
lsb_matching_extract = _extract_wrapper("lsb_matching")
adaptive_edge_lsb_embed = _embed_wrapper("adaptive_edge_lsb")
adaptive_edge_lsb_extract = _extract_wrapper("adaptive_edge_lsb")
dct_embed = _embed_wrapper("dct")
dct_extract = _extract_wrapper("dct")
dwt_embed = _embed_wrapper("dwt")
dwt_extract = _extract_wrapper("dwt")
jsteg_embed = _embed_wrapper("jsteg")
jsteg_extract = _extract_wrapper("jsteg")


__all__ = [
    "capacity_bytes", "embed_bytes", "extract_bytes",
    "embed_image_file", "extract_image_file",
    "embed_image_bitplanes", "extract_image_bitplanes",
    "sequential_lsb_embed", "sequential_lsb_extract",
    "scatter_lsb_embed", "scatter_lsb_extract",
    "lsb_matching_embed", "lsb_matching_extract",
    "adaptive_edge_lsb_embed", "adaptive_edge_lsb_extract",
    "dct_embed", "dct_extract", "dwt_embed", "dwt_extract",
    "jsteg_embed", "jsteg_extract",
]
