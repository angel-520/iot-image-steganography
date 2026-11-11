"""调用真实注册算法测量嵌入、提取耗时。"""

from __future__ import annotations

from pathlib import Path
from statistics import mean, pstdev
import tempfile
from time import perf_counter
from typing import Any

from .metrics import calculate_recovery
from .utils import MAX_PACKET_BYTES, PathLike, load_rgb, positive_int


def _timing_summary(values: list[float], payload_bytes: int) -> dict[str, Any]:
    if not values:
        return {"samples": [], "mean_seconds": None, "std_seconds": None, "throughput_bytes_per_second": None}
    average = mean(values)
    return {
        "samples": values, "mean_seconds": average, "std_seconds": pstdev(values),
        "throughput_bytes_per_second": payload_bytes / average if average > 0 else None,
    }


def benchmark_algorithm(
    image_path: PathLike,
    payload: bytes,
    algorithm: str,
    algorithm_params: dict[str, Any],
    repeats: int = 3,
) -> dict[str, Any]:
    """真实执行 embed/extract 多次并测量耗时、总体标准差和吞吐量。

    每次使用独立临时输出文件，结束后清理；耗时包含算法自身的图像读写。
    只有实际完成的操作进入相应耗时统计，恢复失败单独记录，不伪造成功。
    repeats 为 1～100，payload 最多 16 MiB。算法缺失返回 unsupported；
    无效参数抛出明确异常，实际嵌入或提取错误则逐次保存在 runs 中。
    """
    repeats = positive_int(repeats, "repeats", maximum=100)
    if not isinstance(payload, bytes):
        raise TypeError("payload 必须是 bytes")
    if len(payload) > MAX_PACKET_BYTES:
        raise ValueError(f"payload 不能超过 {MAX_PACKET_BYTES} bytes")
    if not isinstance(algorithm_params, dict):
        raise TypeError("algorithm_params 必须是 dict")
    load_rgb(image_path)
    from .adapter import get_adapter

    try:
        adapter = get_adapter(algorithm)
    except NotImplementedError as exc:
        return {"status": "unsupported", "algorithm": algorithm, "error": str(exc), "runs": []}
    suffix = getattr(adapter, "output_suffix", ".png")
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        raise ValueError("适配器 output_suffix 必须是受支持的图像扩展名")
    runs: list[dict[str, Any]] = []
    embed_times: list[float] = []
    extract_times: list[float] = []
    with tempfile.TemporaryDirectory(prefix="istg_benchmark_") as temporary:
        directory = Path(temporary)
        for index in range(repeats):
            record: dict[str, Any] = {
                "repeat": index + 1, "embed_seconds": None, "extract_seconds": None,
                "recovery": None, "error": None,
            }
            output = directory / f"run_{index + 1}{suffix}"
            try:
                started = perf_counter()
                embedded = adapter.embed(image_path, payload, output, dict(algorithm_params))
                duration = perf_counter() - started
                record["embed_seconds"] = duration
                embed_times.append(duration)
                if not isinstance(embedded, dict):
                    raise ValueError("适配器 embed 必须返回含 output_path 的 dict")
                actual_output = Path(embedded.get("output_path", output))
                if not actual_output.is_file():
                    raise ValueError("真实嵌入没有生成输出图片")
            except NotImplementedError as exc:
                record.update(status="unsupported", error=str(exc))
                runs.append(record)
                continue
            except (ValueError, TypeError, OSError, RuntimeError) as exc:
                record.update(status="embedding_failed", error=str(exc))
                runs.append(record)
                continue
            try:
                started = perf_counter()
                extracted = adapter.extract(actual_output, dict(algorithm_params))
                duration = perf_counter() - started
                record["extract_seconds"] = duration
                extract_times.append(duration)
                recovery = calculate_recovery(payload, extracted)
                record.update(status=recovery["status"], recovery=recovery)
            except NotImplementedError as exc:
                record.update(status="unsupported", error=str(exc))
            except (ValueError, TypeError, OSError, RuntimeError) as exc:
                record.update(status="extraction_failed", error=str(exc))
            runs.append(record)
    successful = sum(record["status"] == "recovered" for record in runs)
    all_unsupported = all(record["status"] == "unsupported" for record in runs)
    return {
        "status": ("unsupported" if all_unsupported else
                   "ok" if successful == repeats else "partial_failure" if successful else "failed"),
        "algorithm": algorithm, "repeats": repeats, "payload_bytes": len(payload),
        "successful_repeats": successful, "all_recovered": successful == repeats,
        "embed": _timing_summary(embed_times, len(payload)),
        "extract": _timing_summary(extract_times, len(payload)),
        "standard_deviation_ddof": 0, "timing_includes_image_io": True,
        "runs": runs,
    }
