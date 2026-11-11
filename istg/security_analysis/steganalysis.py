"""经典 LSB 的 PoV 卡方、RS 分组、直方图及检测器评价。

统计分数只反映给定模型的特征，不是存在隐写的概率，也不输出伪造嵌入率。
"""

from __future__ import annotations

from numbers import Integral
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import chi2, rankdata

from .utils import finite_float, load_rgb, positive_int, validate_channels

_MAX_BLOCKS = 8192
_RS_CHUNK_GROUPS = 65536


def _single_channel(channel: str) -> str:
    return validate_channels([channel])[0]


def _chi_square_sample(values: np.ndarray) -> dict[str, Any]:
    counts = np.bincount(values.reshape(-1), minlength=256).astype(np.int64)
    pairs = counts.reshape(128, 2)
    # Westfeld-Pfitzmann 单侧 PoV 类别：只取偶数的实测频数，期望为配对均值。
    observed: list[float] = []
    expected: list[float] = []
    pending_observed, pending_expected = 0.0, 0.0
    active_pairs = int(np.count_nonzero(pairs.sum(axis=1)))
    for even, odd in pairs:
        if even + odd == 0:
            continue
        pending_observed += float(even)
        pending_expected += (float(even) + float(odd)) / 2.0
        if pending_expected >= 5.0:
            observed.append(pending_observed)
            expected.append(pending_expected)
            pending_observed, pending_expected = 0.0, 0.0
    if pending_expected:
        if expected:
            observed[-1] += pending_observed
            expected[-1] += pending_expected
        else:
            observed.append(pending_observed)
            expected.append(pending_expected)
    observed_array = np.asarray(observed, dtype=np.float64)
    expected_array = np.asarray(expected, dtype=np.float64)
    statistic = float(np.sum((observed_array - expected_array) ** 2 / expected_array)) if expected else 0.0
    degrees = max(0, len(expected) - 1)
    valid = degrees > 0 and bool(np.all(expected_array >= 5.0))
    p_value = float(chi2.sf(statistic, degrees)) if valid else None
    return {
        "status": "ok" if valid else "degenerate",
        "sample_count": int(values.size), "statistic": statistic,
        "degrees_of_freedom": degrees, "p_value": p_value,
        "active_pairs": active_pairs, "merged_categories": len(expected),
        "observed": observed_array, "expected": expected_array,
        "minimum_expected_count": float(expected_array.min()) if expected else None,
    }


def chi_square_analysis(
    image_path: str | Path, channel: str = "R", block_size: int = 4096,
) -> dict[str, Any]:
    """执行经典 LSB 替换的 Westfeld-Pfitzmann 配对频数卡方分析。

    channel 指定 RGB 通道，block_size 为行优先展开后的非重叠分块像素数。
    整图及每块使用偶数频数 n、配对均值 e，计算 sum((n-e)^2/e)，自由度
    为合并后的有效类别数减一；零频类别省略，相邻稀疏类别合并至 e>=5。
    少于两类时返回 p_value=None、status="degenerate"，不虚构 p 值。
    score 等于可用 p 值，高分表示更符合配对均衡模型；它不是隐写概率。
    非法参数、损坏图片、超过分块数量限制抛出 ValueError，缺失文件抛出
    FileNotFoundError。仅适用于经典 LSB 替换线索，不作通用隐写判定。
    """
    selected = _single_channel(channel)
    block_size = positive_int(block_size, "block_size")
    array = load_rgb(image_path)
    values = array[:, :, "RGB".index(selected)].reshape(-1)
    if (values.size + block_size - 1) // block_size > _MAX_BLOCKS:
        raise ValueError(f"分块数不能超过 {_MAX_BLOCKS}，请增大 block_size")
    global_result = _chi_square_sample(values)
    blocks: list[dict[str, Any]] = []
    for start in range(0, values.size, block_size):
        stop = min(start + block_size, values.size)
        block = _chi_square_sample(values[start:stop])
        block.update({"start": start, "stop": stop})
        blocks.append(block)
    return {
        **global_result, "channel": selected, "block_size": block_size,
        "blocks": blocks, "score": global_result["p_value"],
        "score_direction": "higher_is_more_pair_equalization",
        "interpretation": "p值是配对均衡模型下的卡方右尾值，不是含有隐写的概率",
        "formula": "sum((even_count-pair_mean)^2/pair_mean); df=merged_categories-1",
        "category_policy": "omit_zero_pairs; merge_adjacent_sparse_categories_until_expected>=5",
    }


def _flip_groups(groups: np.ndarray, mask: np.ndarray) -> np.ndarray:
    flipped = groups.copy()
    parity = groups & 1
    positive = mask == 1
    negative = mask == -1
    flipped[:, positive] += 1 - 2 * parity[:, positive]
    # F_-1(0)=-1、F_-1(255)=256；保持有符号整数，不能截断或回绕。
    flipped[:, negative] += 2 * parity[:, negative] - 1
    return flipped


def _rs_classification(groups: np.ndarray, mask: np.ndarray, baseline: np.ndarray) -> tuple[int, int, int]:
    changed = np.abs(np.diff(_flip_groups(groups, mask), axis=1)).sum(axis=1)
    return (
        int(np.count_nonzero(changed > baseline)),
        int(np.count_nonzero(changed < baseline)),
        int(np.count_nonzero(changed == baseline)),
    )


def rs_analysis(
    image_path: str | Path,
    channel: str = "R",
    group_size: int = 4,
    mask: tuple[int, ...] = (1, 0, 0, 1),
) -> dict[str, Any]:
    """返回标准 RS 正、负翻转掩码下的 Regular/Singular/Unusable 分组。

    通道按行优先展开为非重叠组，末尾不足 group_size 的像素忽略并报告。
    判别函数为相邻像素绝对差之和；掩码元素取 -1、0、1，长度须等于组长，
    且至少一个非零。负翻转允许结果为 -1 或 256，不裁剪、不 uint8 回绕。
    score 为两掩码 R/S 数量差的归一化启发分数，越高表示偏离对称性越强；
    不提供嵌入率估计。没有完整组或全部不可用时 score=None。
    非法参数和损坏图像抛出 ValueError，缺失文件抛出 FileNotFoundError。
    """
    selected = _single_channel(channel)
    group_size = positive_int(group_size, "group_size", maximum=1024)
    if group_size < 2:
        raise ValueError("group_size 至少为 2")
    if not isinstance(mask, (tuple, list)) or len(mask) != group_size:
        raise ValueError("mask 长度必须等于 group_size")
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral)
           or value not in {-1, 0, 1} for value in mask) or not any(mask):
        raise ValueError("mask 只能由 -1、0、1 组成且至少一个非零")
    mask_array = np.asarray(mask, dtype=np.int16)
    values = load_rgb(image_path)[:, :, "RGB".index(selected)].reshape(-1)
    group_count = values.size // group_size
    grouped = values[:group_count * group_size].reshape(group_count, group_size)
    positive = np.zeros(3, dtype=np.int64)
    negative = np.zeros(3, dtype=np.int64)
    for start in range(0, group_count, _RS_CHUNK_GROUPS):
        groups = grouped[start:start + _RS_CHUNK_GROUPS].astype(np.int16)
        baseline = np.abs(np.diff(groups, axis=1)).sum(axis=1)
        positive += _rs_classification(groups, mask_array, baseline)
        negative += _rs_classification(groups, -mask_array, baseline)

    def counts(values_: np.ndarray) -> dict[str, int | float | None]:
        return {
            "regular": int(values_[0]), "singular": int(values_[1]), "unusable": int(values_[2]),
            "regular_ratio": int(values_[0]) / group_count if group_count else None,
            "singular_ratio": int(values_[1]) / group_count if group_count else None,
            "unusable_ratio": int(values_[2]) / group_count if group_count else None,
        }

    usable = int(positive[:2].sum() + negative[:2].sum())
    score = float(np.abs(positive[:2] - negative[:2]).sum()) / (2 * group_count) if usable else None
    return {
        "status": "ok" if usable else "degenerate", "channel": selected,
        "group_size": group_size, "mask": tuple(int(value) for value in mask),
        "groups": group_count, "discarded_pixels": int(values.size - group_count * group_size),
        "positive": counts(positive), "negative": counts(negative),
        "score": score, "score_direction": "higher_is_more_rs_asymmetry",
        "score_formula": "(|R_M-R_-M|+|S_M-S_-M|)/(2*group_count)",
        "score_interpretation": "RS掩码对称性差异的启发分数，不是隐写概率或嵌入率",
        "embedding_rate": None, "embedding_rate_status": "not_estimated",
    }


def histogram_analysis(
    image_path: str | Path,
    reference_path: str | Path | None = None,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """统计 RGB 通道 256 桶直方图，可与参考图比较分布差异。

    返回各通道原始计数、归一化频率；提供参考图时还返回计数差、频率差、
    总变差距离和以 2 为底的 Jensen-Shannon 距离。参考图可以尺寸不同，
    距离基于各自归一化分布；不把差异直接判断成隐写。灰度复制为 RGB，
    RGBA 丢弃 Alpha。非法参数或损坏图像抛出 ValueError，文件缺失抛出
    FileNotFoundError，不写文件。
    """
    selected = validate_channels(channels)
    array = load_rgb(image_path)
    reference = load_rgb(reference_path) if reference_path is not None else None
    result: dict[str, Any] = {}
    for channel in selected:
        index = "RGB".index(channel)
        histogram = np.bincount(array[:, :, index].reshape(-1), minlength=256).astype(np.int64)
        normalized = histogram / histogram.sum()
        entry: dict[str, Any] = {"histogram": histogram, "normalized": normalized}
        if reference is not None:
            reference_histogram = np.bincount(reference[:, :, index].reshape(-1), minlength=256).astype(np.int64)
            reference_normalized = reference_histogram / reference_histogram.sum()
            difference = normalized - reference_normalized
            entry.update({
                "reference_histogram": reference_histogram,
                "reference_normalized": reference_normalized,
                "difference": histogram - reference_histogram,
                "normalized_difference": difference,
                "total_variation_distance": float(np.abs(difference).sum() / 2.0),
                "jensen_shannon_distance": float(jensenshannon(normalized, reference_normalized, base=2)),
            })
        result[channel] = entry
    return {
        "pixel_count": int(array.shape[0] * array.shape[1]),
        "reference_pixel_count": int(reference.shape[0] * reference.shape[1]) if reference is not None else None,
        "channels": result, "interpretation": "直方图差异不直接证明存在隐写",
    }


def evaluate_detection(
    original_images: list[str | Path],
    stego_images: list[str | Path],
    detector: str,
    threshold: float,
) -> dict[str, Any]:
    """用真实图像的连续检测分数计算混淆矩阵、评价指标及 ROC-AUC。

    原图为负样本、隐写图为正样本；detector 支持 chi_square 与 rs，均按
    score>=threshold 判正。卡方使用配对均衡的 p 值作为模型匹配分数，RS
    使用掩码对称性差异启发分数，均不解释成隐写概率。无法评分或读取失败
    的样本保留详细状态并排除指标计算；分母为零或缺少类别时相应值为 None。
    AUC 使用含并列平均秩的 Mann-Whitney 公式。非法参数抛出 ValueError，
    未实现的检测器抛出 NotImplementedError；函数不保存文件。
    """
    if not isinstance(detector, str):
        raise TypeError("detector 必须是 str")
    name = detector.lower().replace("-", "_").strip()
    if name in {"chisquare", "chi_square_analysis"}:
        name = "chi_square"
    if name == "rs_analysis":
        name = "rs"
    if name not in {"chi_square", "rs"}:
        raise NotImplementedError(f"尚未实现检测器：{detector}")
    threshold = finite_float(threshold, "threshold")
    if not isinstance(original_images, (list, tuple)) or not isinstance(stego_images, (list, tuple)):
        raise ValueError("original_images 和 stego_images 必须为路径列表")
    total = len(original_images) + len(stego_images)
    if total == 0 or total > 10000:
        raise ValueError("样本总数必须为 1～10000")
    details: list[dict[str, Any]] = []
    scores: list[float] = []
    labels: list[int] = []
    for label, paths in ((0, original_images), (1, stego_images)):
        for path in paths:
            entry: dict[str, Any] = {"path": str(path), "label": label, "score": None, "prediction": None}
            try:
                analysis = chi_square_analysis(path) if name == "chi_square" else rs_analysis(path)
                score = analysis["score"]
                if score is None:
                    entry.update({"status": "undetermined", "error": "统计退化，无法评分"})
                else:
                    entry.update({"status": "ok", "score": score, "prediction": int(score >= threshold)})
                    scores.append(score)
                    labels.append(label)
            except (FileNotFoundError, ValueError, OSError, TypeError) as exc:
                entry.update({"status": "error", "error": str(exc)})
            details.append(entry)
    scores_array = np.asarray(scores, dtype=np.float64)
    labels_array = np.asarray(labels, dtype=np.int8)
    predictions = scores_array >= threshold
    tp = int(np.count_nonzero(predictions & (labels_array == 1)))
    fp = int(np.count_nonzero(predictions & (labels_array == 0)))
    tn = int(np.count_nonzero(~predictions & (labels_array == 0)))
    fn = int(np.count_nonzero(~predictions & (labels_array == 1)))

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    positives, negatives = int(labels_array.sum()), int((labels_array == 0).sum())
    auc: float | None = None
    if positives and negatives:
        ranks = rankdata(scores_array, method="average")
        auc = float((ranks[labels_array == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))
    scored = len(scores)
    return {
        "status": "ok" if scored == total else ("partial" if scored else "no_scores"),
        "detector": name, "threshold": threshold, "score_direction": "higher_is_more_suspicious",
        "score_interpretation": "模型匹配或RS对称性差异的启发分数，不是隐写概率",
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "TPR": ratio(tp, tp + fn), "FPR": ratio(fp, fp + tn),
        "Precision": ratio(tp, tp + fp), "Recall": ratio(tp, tp + fn),
        "Accuracy": ratio(tp + tn, scored), "F1": ratio(2 * tp, 2 * tp + fp + fn),
        "ROC_AUC": auc, "total_samples": total, "scored_samples": scored,
        "undetermined_samples": sum(item["status"] == "undetermined" for item in details),
        "error_samples": sum(item["status"] == "error" for item in details),
        "results": details,
    }
