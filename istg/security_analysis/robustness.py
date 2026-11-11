"""对真实隐写图施加扰动，再调用同一算法提取，不使用原图回退。"""

from __future__ import annotations

from numbers import Integral
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .metrics import calculate_recovery
from .utils import MAX_IMAGE_PIXELS, MAX_PACKET_BYTES, PathLike, finite_float, load_rgb


def _prepare_attacks(attacks: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    if not isinstance(attacks, list) or len(attacks) > 100:
        raise ValueError("attacks 必须是最多 100 项的字典列表")
    aliases = {
        "jpeg": "jpeg", "jpeg_recompression": "jpeg", "jpeg_compression": "jpeg",
        "noise": "gaussian_noise", "gaussian_noise": "gaussian_noise",
        "crop": "crop", "resize": "resize", "scale": "resize",
    }
    prepared = []
    for attack in attacks:
        if not isinstance(attack, dict) or not isinstance(attack.get("type"), str):
            raise ValueError("每个攻击须为包含字符串 type 的 dict")
        name = aliases.get(attack["type"].lower())
        if name is None:
            raise ValueError(f"不支持的图像扰动：{attack['type']}")
        nested = attack.get("params", {})
        if not isinstance(nested, dict):
            raise ValueError("攻击 params 必须是 dict")
        parameters = {**nested, **{key: value for key, value in attack.items() if key not in {"type", "params"}}}
        allowed = {
            "jpeg": {"quality", "subsampling"}, "gaussian_noise": {"sigma", "seed"},
            "crop": {"ratio"}, "resize": {"scale"},
        }[name]
        unknown = set(parameters) - allowed
        if unknown:
            raise ValueError(f"{name} 不支持参数：{', '.join(sorted(unknown))}")
        config: dict[str, Any] = {"type": name}
        if name == "jpeg":
            quality = parameters.get("quality", 75)
            if isinstance(quality, (bool, np.bool_)) or not isinstance(quality, Integral) or not 1 <= quality <= 100:
                raise ValueError("JPEG quality 必须是 1～100 的整数")
            subsampling = parameters.get("subsampling", 2)
            if isinstance(subsampling, (bool, np.bool_)) or not isinstance(subsampling, Integral) or subsampling not in {0, 1, 2}:
                raise ValueError("JPEG subsampling 必须是 0、1 或 2")
            config.update(quality=int(quality), subsampling=int(subsampling))
        elif name == "gaussian_noise":
            sigma = finite_float(parameters.get("sigma", 5.0), "sigma", minimum=0, maximum=255)
            seed = parameters.get("seed", 0)
            if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral) or not 0 <= seed < 2**64:
                raise ValueError("noise seed 必须是 0～2**64-1 的整数")
            config.update(sigma=sigma, seed=int(seed))
        elif name == "crop":
            ratio = finite_float(parameters.get("ratio", 0.9), "ratio", minimum=0, maximum=1)
            if ratio == 0:
                raise ValueError("裁剪 ratio 必须大于 0")
            config.update(ratio=ratio, width=max(1, round(width * ratio)), height=max(1, round(height * ratio)))
        else:
            scale = finite_float(parameters.get("scale", 0.5), "scale", minimum=0, maximum=4)
            if scale == 0:
                raise ValueError("缩放 scale 必须大于 0")
            new_width, new_height = max(1, round(width * scale)), max(1, round(height * scale))
            if new_width * new_height > MAX_IMAGE_PIXELS:
                raise ValueError("缩放输出超过最大像素数限制")
            config.update(scale=scale, width=new_width, height=new_height)
        prepared.append(config)
    return prepared


def _apply_attack(array: np.ndarray, attack: dict[str, Any], output: Path) -> tuple[int, int]:
    height, width = array.shape[:2]
    kind = attack["type"]
    if kind == "jpeg":
        Image.fromarray(array).save(output, format="JPEG", quality=attack["quality"], subsampling=attack["subsampling"])
        return width, height
    if kind == "gaussian_noise":
        rng = np.random.default_rng(attack["seed"])
        result = np.empty_like(array)
        source, destination = array.reshape(-1), result.reshape(-1)
        for start in range(0, source.size, 1_048_576):
            end = min(source.size, start + 1_048_576)
            values = source[start:end].astype(np.float32)
            values += rng.standard_normal(end - start, dtype=np.float32) * attack["sigma"]
            destination[start:end] = np.clip(np.rint(values), 0, 255).astype(np.uint8)
        Image.fromarray(result).save(output, format="PNG")
        return width, height
    if kind == "crop":
        new_width, new_height = attack["width"], attack["height"]
        left, top = (width - new_width) // 2, (height - new_height) // 2
        result = Image.fromarray(array).crop((left, top, left + new_width, top + new_height))
    else:
        new_width, new_height = attack["width"], attack["height"]
        result = Image.fromarray(array).resize((new_width, new_height), resample=Image.Resampling.LANCZOS)
    result.save(output, format="PNG")
    return new_width, new_height


def robustness_test(
    stego_path: PathLike,
    original_payload: bytes,
    algorithm: str,
    algorithm_params: dict[str, Any],
    attacks: list[dict[str, Any]],
    output_dir: PathLike,
) -> dict[str, Any]:
    """逐项真实扰动、保存图像并提取，返回恢复结果及明确失败状态。

    attacks 支持 type=jpeg(quality 1～100)、gaussian_noise(sigma, seed)、
    crop(ratio 为宽高保留比例，中心裁剪且不补回原尺寸)、resize(scale)。
    参数可平铺或放在 params 字典中。每个攻击独立从输入隐写图开始，不串联，
    不回退到未扰动原图。噪声固定默认 seed=0，便于复现。
    提取失败时 ber=None；真实提取长度不同时按 calculate_recovery 的规则计 BER。
    最多 100 个攻击，缩放不超过 4 倍且输出最多 1600 万像素；输出不可覆盖源图。
    """
    if not isinstance(original_payload, bytes):
        raise TypeError("original_payload 必须是 bytes")
    if len(original_payload) > MAX_PACKET_BYTES:
        raise ValueError(f"original_payload 不能超过 {MAX_PACKET_BYTES} bytes")
    if not isinstance(algorithm_params, dict):
        raise TypeError("algorithm_params 必须是 dict")
    array = load_rgb(stego_path)
    height, width = array.shape[:2]
    prepared = _prepare_attacks(attacks, width, height)
    from .adapter import get_adapter

    try:
        adapter = get_adapter(algorithm)
    except NotImplementedError as exc:
        return {"status": "unsupported", "algorithm": algorithm, "error": str(exc), "results": []}
    destination = Path(output_dir)
    source_path = Path(stego_path).resolve()
    outputs = []
    for index, attack in enumerate(prepared, 1):
        suffix = ".jpg" if attack["type"] == "jpeg" else ".png"
        base = destination / f"attack_{index:03d}_{attack['type']}{suffix}"
        output = base
        if output.resolve() == source_path or (output.is_file() and output.samefile(stego_path)):
            raise ValueError("扰动输出不能覆盖输入隐写图")
        serial = 1
        while output.exists():
            output = base.with_name(f"{base.stem}_{serial}{suffix}")
            serial += 1
        outputs.append(output)
    if prepared:
        destination.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for attack, output in zip(prepared, outputs):
        record: dict[str, Any] = {
            "attack": dict(attack), "output_path": str(output), "status": None,
            "extraction_success": False, "exact_match": False, "ber": None,
            "recovery": None, "error": None,
        }
        try:
            size = _apply_attack(array, attack, output)
            record["attacked_size"] = size
        except (OSError, ValueError, RuntimeError) as exc:
            record.update(status="attack_failed", error=str(exc))
            results.append(record)
            continue
        try:
            extracted = adapter.extract(output, dict(algorithm_params))
            recovery = calculate_recovery(original_payload, extracted)
            record.update(
                status=recovery["status"], extraction_success=True,
                exact_match=recovery["exact_match"], ber=recovery["ber"], recovery=recovery,
            )
        except NotImplementedError as exc:
            record.update(status="unsupported", error=str(exc))
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            record.update(status="extraction_failed", error=str(exc))
        results.append(record)
    return {
        "status": "completed", "algorithm": algorithm, "source_size": (width, height),
        "attack_count": len(results), "recovered_count": sum(item["exact_match"] for item in results),
        "extraction_failed_count": sum(item["status"] == "extraction_failed" for item in results),
        "length_mismatch_count": sum(item["status"] == "length_mismatch" for item in results),
        "attacks_are_independent": True, "results": results,
    }
