"""图像质量、真实算法容量与字节恢复评价。"""

from __future__ import annotations

from hashlib import sha256
import math
from typing import Any

import numpy as np

from .utils import MAX_PACKET_BYTES, PathLike, load_rgb, require_same_shape


def _bounded_ssim(original: np.ndarray, stego: np.ndarray, window_size: int) -> float:
    """带窗口余量分块计算真实 SSIM 图，汇总全图有效区域而非平均块分数。"""
    from skimage.metrics import structural_similarity

    radius = window_size // 2
    height, width = original.shape[:2]
    total = 0.0
    samples = 0
    for y in range(radius, height - radius, 256):
        end_y = min(y + 256, height - radius)
        for x in range(radius, width - radius, 256):
            end_x = min(x + 256, width - radius)
            region = np.s_[y - radius:end_y + radius, x - radius:end_x + radius]
            _, similarity_map = structural_similarity(
                original[region], stego[region], data_range=255, channel_axis=-1,
                win_size=window_size, full=True,
            )
            valid = similarity_map[radius:radius + end_y - y, radius:radius + end_x - x]
            total += float(np.sum(valid, dtype=np.float64))
            samples += valid.size
    return total / samples


def calculate_quality(
    original_path: PathLike,
    stego_path: PathLike,
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """按相同的 0～255 RGB 数值范围计算 MSE、PSNR、SSIM。

    ``metrics`` 默认包含三个指标，名称不区分大小写。灰度复制为 RGB，
    RGBA 丢弃透明度，不改变 EXIF 方向。尺寸不一致或未知指标抛出 ValueError。
    SSIM 使用 scikit-image 的局部窗口，窗口为不超过 7 的最小边奇数；
    最小边不足 3 时无法计算 SSIM，但仍可单独请求 MSE、PSNR。
    SSIM 按带窗口余量的 256×256 有效区分块计算，保留全图相同的窗口定义，
    避免大型图片在 scikit-image 内创建过大的中间矩阵。
    MSE 为零时 PSNR 返回 float('inf')，不把该值替换成有限常量。
    """
    selected = ["mse", "psnr", "ssim"] if metrics is None else metrics
    if not isinstance(selected, (list, tuple)) or not selected:
        raise ValueError("metrics 必须是非空指标列表")
    if any(not isinstance(name, str) for name in selected):
        raise ValueError("metrics 只能包含 mse、psnr、ssim")
    names = [name.lower() for name in selected]
    if len(set(names)) != len(names) or any(name not in {"mse", "psnr", "ssim"} for name in names):
        raise ValueError("metrics 只能包含不重复的 mse、psnr、ssim")
    original, stego = load_rgb(original_path), load_rgb(stego_path)
    require_same_shape(original, stego)
    result: dict[str, Any] = {
        "status": "ok", "shape": tuple(original.shape), "data_range": 255,
        "color_conversion": "gray_replicated_to_RGB; RGBA_alpha_discarded",
    }
    if "mse" in names or "psnr" in names:
        first, second = original.reshape(-1), stego.reshape(-1)
        squared_sum = 0.0
        for start in range(0, first.size, 1_048_576):
            stop = min(first.size, start + 1_048_576)
            difference = first[start:stop].astype(np.float64) - second[start:stop].astype(np.float64)
            squared_sum += float(np.dot(difference, difference))
        mse = squared_sum / first.size
        if "mse" in names:
            result["mse"] = mse
        if "psnr" in names:
            result["psnr"] = math.inf if mse == 0 else 20 * math.log10(255.0) - 10 * math.log10(mse)
    if "ssim" in names:
        minimum_side = min(original.shape[:2])
        if minimum_side < 3:
            raise ValueError("SSIM 要求图像宽高至少为 3；可单独计算 MSE、PSNR")
        window_size = min(7, minimum_side)
        if window_size % 2 == 0:
            window_size -= 1
        result["ssim"] = _bounded_ssim(original, stego, window_size)
        result["ssim_window_size"] = window_size
    return result


def calculate_capacity(
    image_path: PathLike,
    algorithm: str,
    algorithm_params: dict[str, Any],
) -> dict[str, Any]:
    """调用注册算法的真实容量接口，返回物理容量、帧开销及有效字节数。

    容量单位约定为有效 bytes，即供 embed 接口传入的原始数据容量。
    内置算法从自己的槽布局计算物理 bit 数，STG1 开销为 14 bytes；
    lsb_msb 的 IMG1 描述额外占 17 bytes，另给图片数据的剩余容量。
    自定义适配器未提供 capacity_details 时，未知物理容量和开销返回 None，
    不按 RGB 各 1 bit 猜测。算法或容量接口缺失返回 unsupported 状态。
    """
    if not isinstance(algorithm_params, dict):
        raise TypeError("algorithm_params 必须是 dict")
    height, width = load_rgb(image_path).shape[:2]
    from .adapter import get_adapter

    try:
        adapter = get_adapter(algorithm)
        payload_bytes = adapter.capacity(image_path, dict(algorithm_params))
        if isinstance(payload_bytes, bool) or not isinstance(payload_bytes, (int, np.integer)) or payload_bytes < 0:
            raise ValueError("算法容量接口必须返回非负整数 bytes")
    except NotImplementedError as exc:
        return {"status": "unsupported", "algorithm": algorithm, "error": str(exc)}
    details_method = getattr(adapter, "capacity_details", None)
    try:
        details = details_method(image_path, dict(algorithm_params)) if callable(details_method) else {}
    except NotImplementedError:
        details = {}
    if not isinstance(details, dict):
        raise ValueError("capacity_details 必须返回 dict")
    pixels = height * width
    total_bits = details.get("total_capacity_bits")
    if total_bits is not None and (
        isinstance(total_bits, bool) or not isinstance(total_bits, (int, np.integer))
        or total_bits < int(payload_bytes) * 8
    ):
        raise ValueError("物理容量必须是至少包含有效载荷的非负整数 bit 数")
    result: dict[str, Any] = dict(details)
    result.update({
        "status": "ok", "algorithm": algorithm, "width": width, "height": height,
        "pixel_count": pixels, "payload_capacity_bytes": int(payload_bytes),
        "payload_capacity_bits": int(payload_bytes) * 8,
        "total_capacity_bits": total_bits,
        "total_capacity_bytes": None if total_bits is None else int(total_bits) // 8,
        "framing_overhead_bytes": details.get("framing_overhead_bytes"),
        "image_metadata_overhead_bytes": details.get("image_metadata_overhead_bytes"),
        "bpp": None if total_bits is None else float(total_bits) / pixels,
        "effective_payload_bpp": int(payload_bytes) * 8 / pixels,
        "capacity_details_available": bool(details),
        "istg_fixed_header_bytes": 20,
        "istg_variable_overhead_bytes": None,
        "payload_capacity_scope": "adapter.embed input bytes; a complete ISTG packet includes its own header, metadata and encryption overhead",
    })
    return result


def calculate_recovery(original_data: bytes, extracted_data: bytes) -> dict[str, Any]:
    """比较真实字节内容、SHA-256 和按明确长度规则计算的 BER。

    重叠字节按位置逐 bit 比较；长度不同时，每个缺失/多余字节的 8 bit
    全部记错，BER 分母为较长数据长度乘 8。另返回只比较重叠区域的 BER。
    两段都为空时 BER=0；无重叠且非同时为空时 overlap_ber=None。
    此规则不做插入/删除后的重新对齐。仅接受 bytes，每段最多 16 MiB；
    未提取到数据时调用方应返回 BER=None，不得传空数据冒充失败结果。
    """
    if not isinstance(original_data, bytes) or not isinstance(extracted_data, bytes):
        raise TypeError("original_data 和 extracted_data 必须是 bytes")
    if max(len(original_data), len(extracted_data)) > MAX_PACKET_BYTES:
        raise ValueError(f"恢复评价每段数据不能超过 {MAX_PACKET_BYTES} bytes")
    original_length, extracted_length = len(original_data), len(extracted_data)
    overlap = min(original_length, extracted_length)
    overlap_errors = 0
    # 分块向量化，避免为长数据同时生成多个大数组。
    lookup = np.asarray([value.bit_count() for value in range(256)], dtype=np.uint8)
    for start in range(0, overlap, 1_048_576):
        end = min(overlap, start + 1_048_576)
        first = np.frombuffer(original_data, dtype=np.uint8, count=end - start, offset=start)
        second = np.frombuffer(extracted_data, dtype=np.uint8, count=end - start, offset=start)
        overlap_errors += int(lookup[np.bitwise_xor(first, second)].sum(dtype=np.int64))
    missing_bits = abs(original_length - extracted_length) * 8
    total_bits = max(original_length, extracted_length) * 8
    bit_errors = overlap_errors + missing_bits
    exact = original_data == extracted_data
    return {
        "status": "recovered" if exact else ("length_mismatch" if original_length != extracted_length else "data_mismatch"),
        "exact_match": exact, "original_length": original_length,
        "extracted_length": extracted_length,
        "length_difference": extracted_length - original_length,
        "length_mismatch": original_length != extracted_length,
        "original_sha256": sha256(original_data).hexdigest(),
        "extracted_sha256": sha256(extracted_data).hexdigest(),
        "bit_errors": bit_errors, "compared_bits": total_bits,
        "ber": bit_errors / total_bits if total_bits else 0.0,
        "overlap_bit_errors": overlap_errors, "overlap_bits": overlap * 8,
        "overlap_ber": overlap_errors / (overlap * 8) if overlap else (0.0 if not total_bits else None),
        "length_penalty_bits": missing_bits,
        "ber_rule": "position_aligned_overlap; every missing/extra bit is an error; denominator=max(lengths)*8; no resynchronization",
    }
