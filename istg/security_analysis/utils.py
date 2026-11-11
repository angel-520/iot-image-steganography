"""安全性分析共用的图像读取、参数校验与资源限制。"""

from __future__ import annotations

from numbers import Integral, Real
from pathlib import Path
import math
import warnings

import numpy as np
from PIL import Image, UnidentifiedImageError

PathLike = str | Path
MAX_IMAGE_PIXELS = 16_000_000
MAX_PACKET_BYTES = 16 * 1024 * 1024
MAX_RESULT_BYTES = 256 * 1024 * 1024


def positive_int(value: int, name: str, maximum: int | None = None) -> int:
    """校验正整数，拒绝布尔值，并按需限制最大值。"""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    result = int(value)
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} 不能超过 {maximum}")
    return result


def finite_float(
    value: float, name: str, minimum: float | None = None, maximum: float | None = None,
) -> float:
    """校验有限实数及闭区间范围，拒绝布尔值与字符串。"""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} 必须是有限实数")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 必须是有限实数") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限实数")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} 不能小于 {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} 不能大于 {maximum}")
    return result


def validate_channels(channels: list[str] | None = None) -> list[str]:
    """返回规范的 RGB 通道列表；None 表示三个通道，顺序按输入保留。"""
    if channels is None:
        return ["R", "G", "B"]
    if not isinstance(channels, (list, tuple)) or not channels:
        raise ValueError("channels 必须是非空 RGB 通道列表")
    if any(not isinstance(c, str) or c.upper() not in {"R", "G", "B"} for c in channels):
        raise ValueError("channels 只能包含 R、G、B")
    result = [c.upper() for c in channels]
    if len(set(result)) != len(result):
        raise ValueError("channels 不能重复")
    return result


def load_rgb(path: PathLike) -> np.ndarray:
    """读取为 uint8 RGB 数组；灰度复制到三通道，RGBA 丢弃 Alpha。

    在解码前限制尺寸为 1600 万像素，不改变 EXIF 方向、不写文件。
    路径不存在抛出 FileNotFoundError，损坏或超限图像抛出 ValueError。
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"图像不存在：{source}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError(f"图像尺寸必须为正且不能超过 {MAX_IMAGE_PIXELS} 像素")
                return np.array(image.convert("RGB"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError(f"图像损坏、无法解码或尺寸过大：{source}") from exc


def require_same_shape(first: np.ndarray, second: np.ndarray) -> None:
    """要求图像尺寸和通道形状一致，否则抛出 ValueError。"""
    if first.shape != second.shape:
        raise ValueError(f"两张图片尺寸不一致：{first.shape} 与 {second.shape}")
