"""RGB 位平面提取与差异统计，不在核心函数中写入文件。"""

from __future__ import annotations

from numbers import Integral
from pathlib import Path
from typing import Any

import numpy as np

from .utils import MAX_RESULT_BYTES, load_rgb, require_same_shape, validate_channels


def _validate_bits(bits: list[int] | None) -> list[int]:
    if bits is None:
        return list(range(8))
    if not isinstance(bits, (list, tuple)) or not bits:
        raise ValueError("bits 必须是非空的 0～7 位编号列表")
    if any(isinstance(bit, (bool, np.bool_)) or not isinstance(bit, Integral)
           or not 0 <= bit <= 7 for bit in bits):
        raise ValueError("bits 只能包含 0～7 的整数，Bit 0 为 LSB")
    result = [int(bit) for bit in bits]
    if len(set(result)) != len(result):
        raise ValueError("bits 不能重复")
    return result


def _check_budget(array: np.ndarray, count: int, copies: int) -> None:
    if array.shape[0] * array.shape[1] * count * copies > MAX_RESULT_BYTES:
        raise ValueError("位平面结果超过内存预算，请减少 channels 或 bits")


def get_bit_planes(
    image_path: str | Path,
    channels: list[str] | None = None,
    bits: list[int] | None = None,
) -> dict[str, dict[int, np.ndarray]]:
    """按通道和位编号返回黑白位平面，Bit 0 为最低有效位。

    参数 channels 默认 RGB，bits 默认 0～7。返回 ``结果["R"][0]`` 形式的
    二维 uint8 数组，像素值为 0 或 255，可直接供 GUI 显示，不保存文件。
    灰度图复制为 RGB，RGBA 丢弃 Alpha，不进行 Alpha 合成或 EXIF 转向。
    非法选择、损坏图像及结果超出内存预算抛出 ValueError；路径缺失抛出
    FileNotFoundError。
    """
    selected_channels, selected_bits = validate_channels(channels), _validate_bits(bits)
    array = load_rgb(image_path)
    _check_budget(array, len(selected_channels) * len(selected_bits), 1)
    return {
        channel: {
            bit: (((array[:, :, "RGB".index(channel)] >> bit) & 1) * 255).astype(np.uint8)
            for bit in selected_bits
        }
        for channel in selected_channels
    }


def compare_bit_planes(
    original_path: str | Path,
    stego_path: str | Path,
    channels: list[str] | None = None,
    bits: list[int] | None = None,
) -> dict[str, dict[int, dict[str, Any]]]:
    """比较同尺寸图像的位平面，返回显示数组、布尔差异矩阵及变化率。

    返回 ``结果[通道][位编号]`` 中的 original/stego 为 0/255 uint8 数组，
    difference 为 bool 数组，changed_pixels 是不同像素数，change_ratio 为
    该数量除以图像像素数。转换规则与 get_bit_planes 一致，不写文件。
    尺寸不一致、非法参数或结果超限抛出 ValueError，路径缺失抛出
    FileNotFoundError。
    """
    selected_channels, selected_bits = validate_channels(channels), _validate_bits(bits)
    original, stego = load_rgb(original_path), load_rgb(stego_path)
    require_same_shape(original, stego)
    _check_budget(original, len(selected_channels) * len(selected_bits), 3)
    pixels = original.shape[0] * original.shape[1]
    result: dict[str, dict[int, dict[str, Any]]] = {}
    for channel in selected_channels:
        result[channel] = {}
        index = "RGB".index(channel)
        for bit in selected_bits:
            first = (((original[:, :, index] >> bit) & 1) * 255).astype(np.uint8)
            second = (((stego[:, :, index] >> bit) & 1) * 255).astype(np.uint8)
            difference = first != second
            changed = int(np.count_nonzero(difference))
            result[channel][bit] = {
                "original": first, "stego": second, "difference": difference,
                "changed_pixels": changed, "change_ratio": changed / pixels,
            }
    return result
