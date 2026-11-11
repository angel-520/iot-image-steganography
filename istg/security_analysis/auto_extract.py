"""在已知算法及显式候选范围内检测、提取 ISTG 包。"""

from __future__ import annotations

from importlib import import_module
from itertools import product
from typing import Any, Iterator

from .adapter import canonical_algorithm, get_adapter, list_algorithms, packet_limit
from .packet import parse_istg
from .utils import MAX_PACKET_BYTES, PathLike, load_rgb, positive_int


def _defaults(name: str) -> Iterator[dict[str, Any]]:
    if name == "scatter_lsb":
        # 不猜测密码或随机种子，用户必须明确提供候选 key。
        return
    if name in {"dct", "dwt", "jsteg_demo", "lsb_matching"}:
        for channels in ("RGB", "R", "G", "B"):
            yield {"channels": channels}
    elif name in {"sequential_lsb", "adaptive_edge_lsb", "lsb_msb"}:
        for channels in ("RGB", "R", "G", "B"):
            yield {"channels": channels, "bit_depth": 1}
        for depth in (2, 3, 4):
            yield {"channels": "RGB", "bit_depth": depth}
    else:
        # 插件参数由插件自行定义，不向未知算法擅自注入 LSB 参数。
        yield {}


def _candidate_params(specification: Any, name: str) -> Iterator[dict[str, Any]]:
    if specification is None:
        yield from _defaults(name)
    elif isinstance(specification, list):
        if len(specification) > 1000 or any(not isinstance(item, dict) for item in specification):
            raise ValueError("参数候选必须是最多 1000 个字典")
        for item in specification:
            yield dict(item)
    elif isinstance(specification, dict):
        keys = list(specification)
        values = []
        for key in keys:
            value = specification[key]
            choices = value if isinstance(value, (list, tuple)) else [value]
            if not choices or len(choices) > 1000:
                raise ValueError("每个参数的候选数量必须为 1～1000")
            values.append(choices)
        for combination in product(*values):
            yield dict(zip(keys, combination))
    else:
        raise TypeError("参数候选应为字典或字典列表")


def auto_detect(
    image_path: PathLike,
    algorithms: list[str] | None = None,
    parameter_candidates: dict[str, Any] | None = None,
    max_attempts: int = 100,
) -> list[dict[str, Any]]:
    """有界尝试算法和参数，返回通过严格 ISTG 校验的匹配列表。

    参数候选按算法名映射到字典列表或参数候选列表的笛卡尔积，惰性枚举。
    顶层 max_packet_bytes 可降低默认 16 MiB 限制。最多 1000 次尝试；Scatter
    仅尝试用户显式给出的 key。没有匹配时返回 []；明确请求未知算法时抛出
    NotImplementedError。有效加密包只是结构匹配，尚未完成 AEAD 认证。
    """
    limit_attempts = positive_int(max_attempts, "max_attempts", 1000)
    if parameter_candidates is not None and not isinstance(parameter_candidates, dict):
        raise TypeError("parameter_candidates 必须是字典或 None")
    specifications = parameter_candidates or {}
    limit = packet_limit(specifications.get("max_packet_bytes", MAX_PACKET_BYTES))
    if algorithms is not None and (not isinstance(algorithms, list) or not algorithms
                                    or any(not isinstance(name, str) for name in algorithms)):
        raise ValueError("algorithms 必须是非空算法名称列表或 None")
    names = list(dict.fromkeys(canonical_algorithm(name) for name in (algorithms or list_algorithms())))
    adapters = {name: get_adapter(name) for name in names}
    # 即使算法不存在或候选为空，也不把损坏图片误报为普通无匹配。
    load_rgb(image_path)
    normalized = {canonical_algorithm(key): value for key, value in specifications.items()
                  if key != "max_packet_bytes"}
    results: list[dict[str, Any]] = []
    attempts = 0
    for name in names:
        for params in _candidate_params(normalized.get(name), name):
            if attempts >= limit_attempts:
                return results
            attempts += 1
            params["max_packet_bytes"] = min(limit, packet_limit(params.get("max_packet_bytes", limit)))
            if name == "scatter_lsb" and not params.get("key"):
                continue
            try:
                data = adapters[name].extract(image_path, params)
                parsed = parse_istg(data, params["max_packet_bytes"])
            except NotImplementedError:
                if algorithms is not None:
                    raise
                continue
            except (ValueError, TypeError, OSError, OverflowError):
                continue
            results.append({
                "algorithm": name, "params": params, "valid": True,
                "encrypted": parsed["encrypted"], "packet_length": parsed["packet_length"],
                "payload_length": parsed["payload_length"],
                "authenticated": False if parsed["encrypted"] else None,
                "attempt": attempts,
            })
    return results


def auto_extract(
    image_path: PathLike,
    detection_result: dict[str, Any],
    password: str | None = None,
) -> dict[str, Any]:
    """根据检测参数重新提取、校验并按需认证解密，保持二进制数据为 bytes。

    返回 status、data_type、data、metadata、authenticated 和 error。密码缺失
    返回 password_required；认证失败时 data 始终为 None。不会相信检测字典
    中缓存的数据、有效标记或加密标记，也不会向磁盘保存秘密内容。
    """
    if not isinstance(detection_result, dict):
        raise TypeError("detection_result 必须是字典")
    name = canonical_algorithm(detection_result.get("algorithm"))
    params = detection_result.get("params", {})
    if not isinstance(params, dict):
        raise TypeError("检测结果的 params 必须是字典")
    if password is not None and not isinstance(password, str):
        raise TypeError("password 必须是 str 或 None")
    limit = packet_limit(params.get("max_packet_bytes", MAX_PACKET_BYTES))
    result: dict[str, Any] = {
        "status": "extraction_failed", "algorithm": name, "data_type": None,
        "data": None, "metadata": None, "authenticated": None,
        "packet_length": None, "payload_length": None, "error": None,
    }
    try:
        adapter = get_adapter(name)
        packet = adapter.extract(image_path, {**params, "max_packet_bytes": limit})
    except NotImplementedError as exc:
        return {**result, "status": "unsupported", "error": str(exc)}
    except (ValueError, TypeError, OSError, OverflowError) as exc:
        return {**result, "error": str(exc)}
    try:
        parsed = parse_istg(packet, limit)
    except (ValueError, TypeError) as exc:
        return {**result, "status": "invalid_packet", "error": str(exc)}
    result.update(packet_length=parsed["packet_length"], payload_length=parsed["payload_length"])
    if parsed["encrypted"]:
        result["authenticated"] = False
        if password is None:
            return {**result, "status": "password_required", "error": "该 ISTG 包需要密码"}
        try:
            crypto = import_module("..istg_crypto", __package__)
        except ImportError as exc:
            return {**result, "status": "unsupported", "error": f"无法加载现有解密模块：{exc}"}
        try:
            plaintext_packet = crypto.decrypt_istg(packet, password)
        except (ValueError, TypeError) as exc:
            return {**result, "status": "authentication_failed", "error": str(exc)}
        result["authenticated"] = True
        try:
            parsed = parse_istg(plaintext_packet, limit)
        except (ValueError, TypeError) as exc:
            return {**result, "status": "invalid_packet", "error": str(exc)}
    inner = parsed["payload_data"]
    if inner is None:
        return {**result, "status": "invalid_packet", "error": "未获得明文 Payload"}
    result.update(status="ok", data_type=inner["kind"], data=inner["data"],
                  metadata={key: value for key, value in inner.items() if key != "data"},
                  error=None, header_crc_verified=True)
    if inner["kind"] == "text":
        result["text"] = inner["data"].decode("utf-8")
    return result
