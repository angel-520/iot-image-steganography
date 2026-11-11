"""真实隐写算法的惰性适配层与可扩展注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, runtime_checkable

from .utils import MAX_PACKET_BYTES, PathLike, load_rgb, positive_int


@runtime_checkable
class StegoAdapter(Protocol):
    """第三方算法应遵守的接口；extract 必须在分配前限制最大提取量。"""

    def embed(self, cover_path: PathLike, payload: bytes, output_path: PathLike,
              params: dict[str, Any]) -> dict[str, Any]:
        """将真实载荷嵌入文件，返回至少包含 output_path 的结构。"""
        ...

    def extract(self, stego_path: PathLike, params: dict[str, Any]) -> bytes:
        """提取真实载荷；遵守 params 中的 max_packet_bytes 限制。"""
        ...

    def capacity(self, cover_path: PathLike, params: dict[str, Any]) -> int:
        """返回扣除算法自身帧头后的有效字节容量。"""
        ...


_BUILTINS = (
    "sequential_lsb", "scatter_lsb", "lsb_matching", "adaptive_edge_lsb",
    "dct", "dwt", "jsteg_demo", "lsb_msb",
)
_ALIASES = {
    "sequential": "sequential_lsb", "random": "scatter_lsb",
    "random_lsb": "scatter_lsb", "scatter": "scatter_lsb",
    "matching": "lsb_matching", "adaptive": "adaptive_edge_lsb",
    "adaptive_edge": "adaptive_edge_lsb", "jsteg": "jsteg_demo",
    "j_steg": "jsteg_demo",
}
_SPATIAL = {"sequential_lsb", "scatter_lsb", "lsb_matching", "adaptive_edge_lsb", "lsb_msb"}
_OPTIONS = {"bit_depth", "channels", "key", "edge_threshold", "strength"}
MAX_NATIVE_LSB_SLOTS = 8_000_000
_REGISTRY: dict[str, StegoAdapter] = {}


def canonical_algorithm(name: str) -> str:
    """规范算法名称并保留经典 JPEG JSteg 与 PNG 教学变体的区别。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("algorithm 必须是非空字符串")
    normal = name.strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(normal, normal)


def packet_limit(value: int = MAX_PACKET_BYTES) -> int:
    """校验单次包大小限制；最多 16 MiB，调用方可进一步降低。"""
    return positive_int(value, "max_packet_bytes", MAX_PACKET_BYTES)


@dataclass(frozen=True)
class BuiltinAdapter:
    """适配现有 istg_stego，不修改原模块的算法或帧格式。"""

    name: str
    output_suffix: str = ".png"

    @property
    def native_method(self) -> str:
        """返回原库实际方法名称，JSteg 兼容名称仍为 jsteg。"""
        return "jsteg" if self.name == "jsteg_demo" else self.name

    def _module(self) -> Any:
        try:
            return import_module("..istg_stego", __package__)
        except ImportError as exc:
            raise NotImplementedError("未安装或无法导入现有 istg_stego 实现") from exc

    def _context(self, path: PathLike, params: dict[str, Any]) -> tuple[Any, Any, dict[str, Any], int, Any, float, float | None]:
        if not isinstance(params, dict):
            raise TypeError("algorithm_params 必须是字典")
        unknown = set(params) - _OPTIONS - {"max_packet_bytes"}
        if unknown:
            raise ValueError(f"未知隐写参数：{sorted(unknown)}")
        limit = packet_limit(params.get("max_packet_bytes", MAX_PACKET_BYTES))
        options = {key: value for key, value in params.items() if key in _OPTIONS}
        module = self._module()
        channel_ids, threshold, strength = module._validate_parameters(
            self.native_method, options.get("bit_depth", 1), options.get("channels", "RGB"),
            options.get("key"), options.get("edge_threshold", 20.0), options.get("strength"),
        )
        array = load_rgb(path)
        return module, array, options, limit, channel_ids, threshold, strength

    def _guard_native_work(self, array: Any, channel_ids: Any, depth: int) -> None:
        if self.native_method in _SPATIAL:
            slots = int(array.shape[0]) * int(array.shape[1]) * int(channel_ids.size) * depth
            if slots > MAX_NATIVE_LSB_SLOTS:
                raise ValueError(
                    f"原空间域实现会展开全部槽位，分析适配器限制为 {MAX_NATIVE_LSB_SLOTS} bit 槽"
                )

    def embed(self, cover_path: PathLike, payload: bytes, output_path: PathLike,
              params: dict[str, Any]) -> dict[str, Any]:
        """调用现有嵌入实现；限制包大小及空间域工作量，返回真实输出路径。"""
        if not isinstance(payload, bytes):
            raise TypeError("payload 必须是 bytes")
        module, array, options, limit, ids, _, _ = self._context(cover_path, params)
        if len(payload) > limit:
            raise ValueError("载荷超过 max_packet_bytes 限制")
        self._guard_native_work(array, ids, options.get("bit_depth", 1))
        del array
        result = module.embed_bytes(cover_path, payload, output_path, self.native_method, **options)
        return {"status": "ok", "algorithm": self.name, "output_path": str(result), "payload_bytes": len(payload)}

    def extract(self, stego_path: PathLike, params: dict[str, Any]) -> bytes:
        """先读取并校验真实 STG1 头，再按限定长度提取，禁止先全量提取后限长。"""
        module, array, options, limit, ids, threshold, strength = self._context(stego_path, params)
        depth = options.get("bit_depth", 1)
        self._guard_native_work(array, ids, depth)

        def read(count: int) -> Any:
            return module._read_embedded_bits(
                array, count, self.native_method, ids, depth, options.get("key"), threshold, strength
            )

        header = read(module._FRAME_BITS)
        length, _ = module._header_from_bits(header, self.native_method)
        if length > limit:
            raise ValueError("STG1 长度超过 max_packet_bytes 限制")
        needed = (module._FRAME.size + length) * 8
        # 在所有算法读取完整载荷前额外检查按尺寸计算的物理上限。
        if self.native_method in _SPATIAL:
            upper = array.shape[0] * array.shape[1] * ids.size * depth
        elif self.native_method == "dwt":
            upper = module._dwt_capacity(array, ids)
        else:
            positions = module._JSTEG_POSITIONS if self.native_method == "jsteg" else module._DCT_POSITIONS
            upper = module._frequency_capacity(array, ids, positions)
        if needed > upper:
            raise ValueError("STG1 长度超过载体物理容量")
        return module._payload_from_bits(read(needed), self.native_method)

    def capacity_details(self, cover_path: PathLike, params: dict[str, Any]) -> dict[str, Any]:
        """返回真实候选槽数、STG1 开销和有效字节容量；变换域仍需重建验证。"""
        module, array, options, limit, ids, threshold, strength = self._context(cover_path, params)
        method = self.native_method
        if method in _SPATIAL - {"adaptive_edge_lsb"}:
            total = array.shape[0] * array.shape[1] * ids.size * options.get("bit_depth", 1)
        elif method == "adaptive_edge_lsb":
            self._guard_native_work(array, ids, options.get("bit_depth", 1))
            values, _ = module._lsb_slots(array, ids, options.get("bit_depth", 1), method,
                                         options.get("key"), threshold)
            total = values.size
        elif method == "dwt":
            total = module._dwt_capacity(array, ids)
        elif method == "jsteg":
            total = module._jsteg_capacity(array, ids, strength)
        else:
            total = module._frequency_capacity(array, ids, module._DCT_POSITIONS)
        capacity = max(0, int(total) // 8 - module._FRAME.size)
        image_overhead = module._IMAGE_FRAME.size if method == "lsb_msb" else 0
        return {
            "algorithm": self.name, "total_capacity_bits": int(total),
            "framing_overhead_bytes": module._FRAME.size,
            "image_metadata_overhead_bytes": image_overhead,
            "payload_capacity_bytes": capacity,
            "usable_image_data_bytes": max(0, capacity - image_overhead) if image_overhead else None,
            "capacity_is_upper_bound": method in {"dct", "dwt", "jsteg"},
            "max_packet_bytes": limit, "max_native_lsb_slots": MAX_NATIVE_LSB_SLOTS,
        }

    def capacity(self, cover_path: PathLike, params: dict[str, Any]) -> int:
        """返回扣除 STG1 帧头后的有效字节容量，不假定所有算法每通道一位。"""
        return self.capacity_details(cover_path, params)["payload_capacity_bytes"]


def register_adapter(name: str, adapter: StegoAdapter, *, replace: bool = False) -> None:
    """注册可信的真实算法适配器，默认拒绝覆盖已有实现。

    第三方 extract 必须遵守 max_packet_bytes 并在分配前限长；注册表不执行
    插件沙箱。仅检查接口存在，不创建任何虚假算法实现。
    """
    normal = canonical_algorithm(name)
    if any(not callable(getattr(adapter, method, None)) for method in ("embed", "extract", "capacity")):
        raise TypeError("adapter 必须实现 embed、extract、capacity")
    if not isinstance(replace, bool):
        raise TypeError("replace 必须是 bool")
    if not replace and (normal in _REGISTRY or normal in _BUILTINS):
        raise ValueError(f"算法已注册：{normal}")
    _REGISTRY[normal] = adapter


def get_adapter(algorithm: str) -> StegoAdapter:
    """获取真实适配器；未知或尚未实现算法抛出 NotImplementedError。"""
    normal = canonical_algorithm(algorithm)
    if normal in _REGISTRY:
        return _REGISTRY[normal]
    if normal in _BUILTINS:
        return BuiltinAdapter(normal)
    raise NotImplementedError(f"尚未实现的隐写算法：{algorithm}")


def list_algorithms() -> list[str]:
    """列出内置及已注册名称；内置模块是否可导入在首次调用时确认。"""
    return list(dict.fromkeys((*_BUILTINS, *_REGISTRY)))
