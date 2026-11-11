"""Explainable statistics and a synthetic-reference Isolation Forest detector."""
from __future__ import annotations
from functools import lru_cache
import numpy as np
from scipy.stats import entropy
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, precision_score, recall_score, roc_auc_score

FEATURES = ["mean_length", "length_cv", "length_entropy", "length_two_level_mass", "gap_mean_ms", "gap_cv", "gap_entropy", "gap_two_cluster_balance", "id_delta_entropy", "source_port_unique_ratio", "tcp_seq_delta_entropy"]


def discrete_entropy(values, bins=16):
    values = np.asarray(values, dtype=float)
    if not len(values) or np.ptp(values) < 1e-9:
        return 0.
    counts = np.histogram(values, bins=bins)[0]
    return float(entropy(counts[counts > 0], base=2))


def two_mass(values):
    _, counts = np.unique(values, return_counts=True)
    if len(counts) < 2:
        return 0.
    top = np.sort(counts)[-2:]
    return float(top.sum() / len(values)) if top[0] / top.sum() >= .1 else 0.


def two_clusters(values):
    if len(values) < 8 or np.ptp(values) < .1:
        return 0.
    centers = np.percentile(values, [20, 80])
    for _ in range(12):
        labels = np.argmin(abs(values[:, None] - centers), axis=1)
        if min(np.bincount(labels, minlength=2)) < len(values) * .1:
            return 0.
        updated = np.array([values[labels == j].mean() for j in (0, 1)])
        if np.allclose(centers, updated):
            break
        centers = updated
    within = np.mean((values - centers[labels]) ** 2)
    spread = float(abs(centers[1] - centers[0]))
    # Two tight clusters, not just an arbitrary split of a broad continuous distribution.
    return float(1 - np.sqrt(within) / spread) if spread > 0 and np.sqrt(within) / spread < .15 else 0.


def features(rows):
    lengths = np.array([r["length"] for r in rows], dtype=float)
    gaps = np.maximum(0., np.diff([r["t"] for r in rows]) * 1000)
    ids = np.diff([r["ip_id"] for r in rows]) % 65536
    seqs = np.diff([r["tcp_seq"] for r in rows if r.get("tcp_seq") is not None]) % 2**32
    mean = lambda v: float(np.mean(v)) if len(v) else 0.
    cv = lambda v: float(np.std(v) / max(mean(v), 1e-9)) if len(v) else 0.
    return np.array([mean(lengths), cv(lengths), discrete_entropy(lengths), two_mass(lengths),
        mean(gaps), cv(gaps), discrete_entropy(gaps), two_clusters(gaps), discrete_entropy(ids),
        len({r["sport"] for r in rows}) / max(1, len(rows)), discrete_entropy(seqs)])


def synthetic_trace(rng, *, channel=None, count=64):
    if channel == "length":
        lengths = rng.choice([92, 220], count)
    else:
        lengths = rng.integers(70, 1450, count)
    gaps = rng.choice([8., 24.], count) + rng.normal(0, .1, count) if channel == "timing" else rng.lognormal(2.5, .55, count)
    # Vary normal field features between windows: constant training columns cannot
    # teach Isolation Forest anything about novel values in those columns.
    id_steps = rng.integers(1, int(rng.integers(2, 6)), count)
    ids = rng.integers(0, 65536, count) if channel == "ip_id" else (rng.integers(0, 65000) + np.cumsum(id_steps)) % 65536
    ports = rng.integers(0, 65536, count) if channel == "udp_sport" else 42000 + rng.integers(0, int(rng.integers(1, 5)), count)
    seq_steps = rng.choice([128, 256, 512, 1024][:int(rng.integers(1, 5))], count)
    seqs = rng.integers(0, 2**32, count) if channel == "tcp_seq" else (rng.integers(0, 2**31) + np.cumsum(seq_steps)) % 2**32
    times = np.cumsum(gaps / 1000)
    return [{"t": float(times[i]), "length": int(lengths[i]), "ip_id": int(ids[i]), "sport": int(ports[i]), "tcp_seq": int(seqs[i])} for i in range(count)]


@lru_cache(maxsize=1)
def model_and_evaluation():
    # Separate RNG streams keep fit, thresholding, and held-out evaluation independent.
    training = np.random.default_rng(211)
    x_train = np.stack([features(synthetic_trace(training)) for _ in range(256)])
    model = IsolationForest(n_estimators=80, contamination=.05, random_state=211, n_jobs=1)
    model.fit(x_train)
    testing = np.random.default_rng(917)
    normal = [synthetic_trace(testing) for _ in range(60)]
    covert = [synthetic_trace(testing, channel=channel) for channel in ("timing", "length", "ip_id", "tcp_seq", "udp_sport") for _ in range(12)]
    x = np.stack([features(rows) for rows in normal + covert])
    y = np.array([0] * len(normal) + [1] * len(covert))
    prediction = (model.predict(x) == -1).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, prediction, labels=[0, 1]).ravel()
    report = {"dataset": "独立种子的合成教学流量；不代表真实业务流量上的泛化性能", "training_windows": 256,
        "test_normal_windows": 60, "test_covert_windows": 60, "window_packets": 64,
        "TP": int(tp), "FP": int(fp), "TN": int(tn), "FN": int(fn),
        "precision": float(precision_score(y, prediction, zero_division=0)), "recall": float(recall_score(y, prediction, zero_division=0)),
        "false_positive_rate": float(fp / max(1, fp + tn)), "roc_auc": float(roc_auc_score(y, -model.score_samples(x)))}
    return model, report


def analyze(rows):
    count = len(rows)
    if count < 8:
        return {"status": "insufficient_data", "packet_count": count, "reason": "至少需要 8 个支持的数据包，机器学习窗口需要 32 包"}
    vector = features(rows)
    values = dict(zip(FEATURES, map(float, vector)))
    findings = []
    if values["length_two_level_mass"] > .9:
        findings.append({"feature": "length_two_level_mass", "value": values["length_two_level_mass"], "message": "包长集中于两个离散取值，可能存在长度编码"})
    if values["gap_two_cluster_balance"] > .85:
        findings.append({"feature": "gap_two_cluster_balance", "value": values["gap_two_cluster_balance"], "message": "包间隔呈紧密双簇，可能存在时间编码"})
    if values["source_port_unique_ratio"] > .15:
        findings.append({"feature": "source_port_unique_ratio", "value": values["source_port_unique_ratio"], "message": "源端口变化频繁；也可能由多流混合引起"})
    if values["id_delta_entropy"] > 3.2:
        findings.append({"feature": "id_delta_entropy", "value": values["id_delta_entropy"], "message": "IP ID 增量熵较高；随机 ID 实现也可能产生此现象"})
    if values["tcp_seq_delta_entropy"] > 3.2:
        findings.append({"feature": "tcp_seq_delta_entropy", "value": values["tcp_seq_delta_entropy"], "message": "TCP 序列增量熵较高；需结合完整会话及重传情况判断"})
    windows = []
    model, evaluation = model_and_evaluation()
    for start in range(0, count, 64):
        chunk = rows[start:start + 64]
        if len(chunk) < 32:
            continue
        x = features(chunk).reshape(1, -1)
        decision = float(model.decision_function(x)[0])
        windows.append({"start": start, "count": len(chunk), "time": chunk[0]["t"],
                        "decision": decision, "anomalous": decision < 0})
    return {"status": "ok", "packet_count": count, "features": values, "findings": findings,
        "statistical_suspicious": bool(findings), "model": "Isolation Forest", "windows": windows,
        "anomaly_ratio": sum(w["anomalous"] for w in windows) / len(windows) if windows else None,
        "evaluation": evaluation, "scope": "当前记录中的 IPv4 TCP/UDP 包；聚合统计，不按五元组分流",
        "note": "异常分数不是隐写概率；合成参考模型与统计告警均不能证明存在秘密信息。无足够窗口时不输出 ML 结论。"}


def evaluate_impairment(parameters):
    """Evaluate the same held-out cohort before/after impairments; abstain below 32 packets."""
    from .network_stego import impair
    model, baseline = model_and_evaluation()
    testing = np.random.default_rng(917)
    cohort = [(0, synthetic_trace(testing)) for _ in range(60)]
    cohort += [(1, synthetic_trace(testing, channel=channel)) for channel in ("timing", "length", "ip_id", "tcp_seq", "udp_sport") for _ in range(12)]
    values, labels = [], []
    for index, (label, rows) in enumerate(cohort):
        settings = {**parameters, "seed": (parameters.get("seed", 42) + index) % 2**32}
        changed = impair(rows, **settings)
        if len(changed) >= 32:
            labels.append(label); values.append(features(changed))
    scored = len(labels)
    metrics = {"TP": 0, "FP": 0, "TN": 0, "FN": 0, "precision": None, "recall": None, "false_positive_rate": None, "roc_auc": None}
    if scored:
        x, y = np.stack(values), np.array(labels)
        predictions = (model.predict(x) == -1).astype(int)
        tn, fp, fn, tp = confusion_matrix(y, predictions, labels=[0, 1]).ravel()
        metrics = {"TP": int(tp), "FP": int(fp), "TN": int(tn), "FN": int(fn),
            "precision": float(precision_score(y, predictions, zero_division=0)),
            "recall": float(recall_score(y, predictions, zero_division=0)) if (y == 1).any() else None,
            "false_positive_rate": float(fp / (fp + tn)) if fp + tn else None,
            "roc_auc": float(roc_auc_score(y, -model.score_samples(x))) if len(set(labels)) == 2 else None}
    return {"baseline": {**baseline, "scored_windows": 120, "undetermined_windows": 0},
        "impaired": {**metrics, "scored_windows": scored, "undetermined_windows": 120 - scored},
        "note": "对同一独立合成测试集施加所选扰动，使用原模型重测；不足 32 包的窗口计为无法判定，不纳入评分。不是当前文件上的准确率。"}
