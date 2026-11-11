"""图像信息隐藏的基础函数库：封装、加密、隐写、提取和安全性分析。

从本包直接导入常用函数即可组合流程，无命令行入口，不在导入时写文件。
各算法的便捷入口还可从 istg.istg_stego 导入。
"""

from .istg_pack import pack_binary, pack_istg
from .istg_crypto import (
    decrypt_istg, decrypt_txt, encrypt_istg, encrypt_txt, read_hex_txt, write_hex_txt,
)
from .istg_stego import (
    capacity_bytes, embed_bytes, extract_bytes, embed_image_file, extract_image_file,
    embed_image_bitplanes, extract_image_bitplanes,
)
from .security_analysis import (
    auto_detect, auto_extract, get_bit_planes, compare_bit_planes,
    chi_square_analysis, rs_analysis, histogram_analysis,
    calculate_quality, calculate_capacity, calculate_recovery,
    benchmark_algorithm, robustness_test, evaluate_detection,
    get_adapter, list_algorithms, register_adapter,
)
from .security_analysis.packet import parse_istg

__all__ = [
    "pack_istg", "pack_binary", "encrypt_istg", "decrypt_istg", "parse_istg",
    "read_hex_txt", "write_hex_txt", "encrypt_txt", "decrypt_txt",
    "capacity_bytes", "embed_bytes", "extract_bytes",
    "embed_image_file", "extract_image_file", "embed_image_bitplanes", "extract_image_bitplanes",
    "auto_detect", "auto_extract", "get_bit_planes", "compare_bit_planes",
    "chi_square_analysis", "rs_analysis", "histogram_analysis",
    "calculate_quality", "calculate_capacity", "calculate_recovery",
    "benchmark_algorithm", "robustness_test", "evaluate_detection",
    "get_adapter", "list_algorithms", "register_adapter",
]
