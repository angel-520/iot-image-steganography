"""图像隐写安全性分析后端；不依赖 GUI，不在导入时加载隐写实现。

所有图像统计均基于 uint8 RGB；灰度复制为 RGB，RGBA 丢弃透明通道。
检测分数是统计指标，不是隐写概率，自动提取仅搜索已知算法与参数。
"""

from .auto_extract import auto_detect, auto_extract
from .bitplane import compare_bit_planes, get_bit_planes
from .steganalysis import (
    chi_square_analysis, evaluate_detection, histogram_analysis, rs_analysis,
)
from .metrics import calculate_capacity, calculate_quality, calculate_recovery
from .benchmark import benchmark_algorithm
from .robustness import robustness_test
from .adapter import get_adapter, list_algorithms, register_adapter

__all__ = [
    "auto_detect", "auto_extract", "get_bit_planes", "compare_bit_planes",
    "chi_square_analysis", "rs_analysis", "histogram_analysis",
    "calculate_quality", "calculate_capacity", "calculate_recovery",
    "benchmark_algorithm", "robustness_test", "evaluate_detection",
    "get_adapter", "list_algorithms", "register_adapter",
]
