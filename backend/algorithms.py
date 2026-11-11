"""前端可查询的真实算法能力及参数定义。"""
from __future__ import annotations
from typing import Any
import istg
from .runtime import APIError
from .schemas import AlgorithmParams


def algorithm_catalog() -> list[dict[str, Any]]:
    channels = {"name": "channels", "label": "颜色通道", "type": "select", "default": "RGB",
                "options": [{"label": value, "value": value} for value in ("RGB", "R", "G", "B", "RG", "RB", "GB", "BGR")]}
    depth = {"name": "bit_depth", "label": "每通道低位数", "type": "number", "default": 1, "min": 1, "max": 4, "step": 1}
    key = {"name": "key", "label": "散布位置密钥", "type": "password", "required": True, "default": ""}
    rows = [
        ("sequential_lsb", "Sequential LSB", "顺序替换低位，容量直观、便于完整实验。", [channels, depth], "逐像素、逐通道顺序嵌入。", "general"),
        ("scatter_lsb", "Random / Scatter LSB", "根据明确给定的密钥散布位置。", [channels, depth, key], "提取必须使用同一位置密钥；不自动猜测密钥。", "general"),
        ("lsb_matching", "LSB Matching（±1）", "以 ±1 调整匹配目标位，处理黑白边界。", [channels], "仅支持每通道 1 位；±1 策略由已验证算法固定。", "general"),
        ("adaptive_edge_lsb", "Adaptive Edge LSB", "在可复现的高位边缘排序中嵌入。", [channels, depth, {"name": "edge_threshold", "label": "边缘阈值", "type": "number", "default": 20, "min": 0, "max": 1000, "step": 1}], "清除待写低位后计算灰度梯度；不支持任意替换边缘算子。", "general"),
        ("dct", "DCT", "8×8 分块 DCT 系数奇偶嵌入，输出无损 PNG。", [channels, {"name": "strength", "label": "量化强度", "type": "number", "default": 16, "min": 8, "max": 128, "step": 1}], "8×8 块与 AC 位置固定；超出可稳定重建范围会明确拒绝。", "general"),
        ("dwt", "DWT", "一级 Haar 小波 HH 分量奇偶嵌入。", [channels, {"name": "strength", "label": "量化强度", "type": "number", "default": 8, "min": 4, "max": 128, "step": 1}], "Haar、一级分解、HH 频带为实际实现的固定配置。", "general"),
        ("jsteg_demo", "JSteg 教学变体（PNG）", "选定 DCT AC 系数的量化奇偶教学实验。", [channels, {"name": "strength", "label": "量化强度", "type": "number", "default": 18, "min": 8, "max": 64, "step": 1}], "这是 PNG 教学变体，不是经典 JPEG 系数域 JSteg。", "general"),
        ("lsb_msb", "LSB-MSB 位平面替换", "载体低位携带秘密图片高位，仅用于独立图片模式。", [channels, {**depth, "default": 2}], "使用 ISTG 像素高位 Payload，可认证加密；恢复低位补零，有损。", "bitplanes"),
    ]
    result = []
    for identifier, name, description, parameters, notes, mode in rows:
        available = True
        try:
            istg.get_adapter(identifier)._module()
        except (ImportError, NotImplementedError):
            available = False
        result.append({"id": identifier, "name": name, "description": description,
                       "available": available, "supports_embed": available, "supports_extract": available,
                       "supports_capacity": available, "supported_formats": ["PNG", "JPEG", "BMP", "WEBP", "TIFF"],
                       "output_formats": ["PNG"], "parameters": parameters, "notes": notes, "mode": mode})
    result.append({"id": "jpeg_jsteg", "name": "JSteg（经典 JPEG）", "description": "JPEG 量化 DCT 系数域实现尚未提供。",
                   "available": False, "supports_embed": False, "supports_extract": False, "supports_capacity": False,
                   "supported_formats": ["JPEG"], "output_formats": ["JPEG"], "parameters": [],
                   "notes": "待实现；不会用像素 LSB 或 PNG 教学变体冒充。", "mode": "general"})
    return result


def validate_algorithm(identifier: str, params: AlgorithmParams) -> dict[str, Any]:
    catalog = {row["id"]: row for row in algorithm_catalog()}
    if identifier not in catalog or not catalog[identifier]["available"]:
        raise APIError("unsupported", "所选算法尚未实现或不可用", 422)
    values = params.native()
    permitted = {field["name"] for field in catalog[identifier]["parameters"]}
    if identifier == "lsb_matching":
        permitted |= {"bit_depth", "key"}
    unknown = set(values) - permitted
    if unknown:
        raise APIError("invalid_parameters", "所选算法不支持这些参数：" + "、".join(sorted(unknown)), 422)
    native = istg.get_adapter(identifier)
    native._module()._validate_parameters(native.native_method, values.get("bit_depth", 1), values.get("channels", "RGB"),
                                           values.get("key"), values.get("edge_threshold", 20), values.get("strength"))
    return values
