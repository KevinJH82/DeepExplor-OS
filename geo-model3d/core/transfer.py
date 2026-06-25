"""领域自适应 / 跨区迁移（方向四 P4）—— 数据富矿区训练的模型迁移到数据稀缺新区。

服务平台"换个 ROI 就能用"：标签充足的 AOI 训练并持久化"源模型"；标签稀缺的新区
按同成因族/矿种调用源模型，先做特征分布对齐（z-score 校正协变量偏移）再预测。

诚实约束：源/目标特征分布差异越大 → 迁移置信度越低、不确定性越高、并告警；
不强行迁移；无同族源模型 → 上层回退知识融合。源模型训练同样**绝不**用预测靶点。
"""

from __future__ import annotations

import os
import json
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    _HAS = True
except Exception:                       # pragma: no cover
    _HAS = False


def has_deps() -> bool:
    return _HAS


def _feature_matrix(surface_layers: Dict[str, np.ndarray], layers: List[str]):
    feat = np.stack([np.nan_to_num(surface_layers[k], nan=0.0) for k in layers], axis=-1)
    valid = np.ones(surface_layers[layers[0]].shape, bool)
    for k in layers:
        valid &= np.isfinite(surface_layers[k])
    return feat, valid


def train_transferable(surface_layers: Dict[str, np.ndarray], known_rowcol, grid,
                       neg_ratio: int = 10, random_state: int = 42) -> Optional[Dict]:
    """训一个可迁移 RF（正样本 vs 采样未标注伪负）+ 记源特征分布(mean/std)。返回 bundle 或 None。"""
    if not _HAS:
        return None
    layers = [k for k in surface_layers if np.isfinite(surface_layers[k]).any()]
    if not layers:
        return None
    feat, valid = _feature_matrix(surface_layers, layers)
    ny, nx = valid.shape
    pos = np.zeros(valid.shape, bool)
    for (r, c) in known_rowcol:
        if 0 <= r < ny and 0 <= c < nx:
            pos[r, c] = True
    pos &= valid
    if int(pos.sum()) < 1:
        return None
    X_all = feat[valid]
    src_mean = X_all.mean(axis=0); src_std = X_all.std(axis=0) + 1e-9

    pos_X = feat[pos]
    neg_pool = feat[valid & ~pos]
    rng = np.random.default_rng(random_state)
    n_neg = min(len(neg_pool), max(int(pos.sum()) * neg_ratio, int(pos.sum())))
    idx = rng.choice(len(neg_pool), size=n_neg, replace=False)
    Xtr = np.vstack([pos_X, neg_pool[idx]])
    ytr = np.concatenate([np.ones(len(pos_X)), np.zeros(n_neg)])
    clf = RandomForestClassifier(n_estimators=200, class_weight="balanced",
                                 max_features="sqrt", min_samples_leaf=2,
                                 n_jobs=-1, random_state=random_state)
    clf.fit(Xtr, ytr)
    return {"clf": clf, "feature_names": layers,
            "feat_mean": src_mean.tolist(), "feat_std": src_std.tolist(),
            "n_positive": int(pos.sum())}


def save_source_model(reg_dir: str, family: str, mineral: str, bundle: Dict,
                      aoi_name: str = "") -> Optional[str]:
    """持久化源模型到 registry（按 family 命名，最新覆盖同名 + 时间戳留档）。"""
    if not _HAS or not bundle:
        return None
    os.makedirs(reg_dir, exist_ok=True)
    safe_fam = (family or "unknown").replace("/", "_")
    path = os.path.join(reg_dir, f"src_{safe_fam}.joblib")
    meta = {"family": family, "mineral": mineral, "aoi_name": aoi_name,
            "feature_names": bundle["feature_names"], "feat_mean": bundle["feat_mean"],
            "feat_std": bundle["feat_std"], "n_positive": bundle["n_positive"],
            "saved_at": int(time.time())}
    joblib.dump({"clf": bundle["clf"], "meta": meta}, path)
    with open(os.path.join(reg_dir, f"src_{safe_fam}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return path


def find_source_model(reg_dir: str, family: str, mineral: str = "") -> Optional[Dict]:
    """按 family 找源模型；返回 {clf, meta} 或 None。"""
    if not _HAS or not reg_dir or not os.path.isdir(reg_dir):
        return None
    safe_fam = (family or "unknown").replace("/", "_")
    path = os.path.join(reg_dir, f"src_{safe_fam}.joblib")
    if not os.path.exists(path):
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


def domain_adapt_score(surface_layers: Dict[str, np.ndarray], source: Dict, grid
                       ) -> Tuple[np.ndarray, np.ndarray, Dict]:
    """用源模型预测目标区：目标特征按源分布 z-score 对齐(协变量偏移校正)→预测。
    返回 (score2d∈[0,1] NaN填空, uncertainty2d∈[0,1], stats{transfer_confidence,feature_shift,...})。
    """
    if not _HAS:
        raise RuntimeError("scikit-learn/joblib 不可用，无法域自适应")
    clf = source["clf"]; meta = source["meta"]
    src_layers = meta["feature_names"]
    # 仅用源/目标共有的特征层；目标缺失的层补 0、不可用层报告
    common = [k for k in src_layers if k in surface_layers and np.isfinite(surface_layers[k]).any()]
    if not common:
        raise ValueError("目标区无与源模型共有的证据层，无法迁移")
    shape = surface_layers[common[0]].shape
    valid = np.ones(shape, bool)
    for k in common:
        valid &= np.isfinite(surface_layers[k])

    src_mean = np.array(meta["feat_mean"]); src_std = np.array(meta["feat_std"])
    # 按源特征顺序构造目标矩阵（缺失层→源均值，等价"无信息"）
    cols = []
    tgt_means = []; shift_terms = []
    for j, k in enumerate(src_layers):
        if k in common:
            arr = np.nan_to_num(surface_layers[k], nan=0.0)[valid]
            tmean, tstd = arr.mean(), arr.std() + 1e-9
            # z-score 对齐到源分布
            aligned = (arr - tmean) / tstd * src_std[j] + src_mean[j]
            cols.append(aligned)
            tgt_means.append(float(tmean))
            shift_terms.append(abs(tmean - src_mean[j]) / (src_std[j]))
        else:
            cols.append(np.full(int(valid.sum()), src_mean[j], dtype=float))
            shift_terms.append(1.0)   # 缺失层记为较大偏移
    X = np.column_stack(cols)
    proba = clf.predict_proba(X)[:, list(clf.classes_).index(1.0)]

    score = np.full(shape, np.nan, dtype=np.float32)
    score[valid] = proba.astype(np.float32)
    fin = score[np.isfinite(score)]
    if fin.size:
        lo, hi = np.percentile(fin, [2, 98])
        if hi > lo:
            score = np.clip((score - lo) / (hi - lo), 0, 1).astype(np.float32)

    # 迁移置信度：特征标准化均值漂移越大→置信越低
    feat_shift = float(np.mean(shift_terms)) if shift_terms else 1.0
    transfer_confidence = float(np.clip(1.0 - feat_shift / 2.0, 0.0, 1.0))
    # 不确定性：迁移置信低→整体不确定性高（叠加分布偏移底噪）
    base_unc = 1.0 - transfer_confidence
    unc = np.full(shape, np.nan, dtype=np.float32)
    unc[valid] = np.clip(base_unc + 0.3 * np.abs(proba - 0.5) * 0 + 0.0, 0, 1).astype(np.float32)
    # 让模糊区(接近0.5)略增不确定性
    unc[valid] = np.clip(base_unc + 0.4 * (1.0 - np.abs(proba - 0.5) * 2.0), 0, 1).astype(np.float32)

    stats = {"method": "domain_adapt", "source_aoi": meta.get("aoi_name"),
             "source_family": meta.get("family"), "source_n_positive": meta.get("n_positive"),
             "features_used": common, "features_missing": [k for k in src_layers if k not in common],
             "feature_shift": round(feat_shift, 3),
             "transfer_confidence": round(transfer_confidence, 3)}
    return score, unc, stats
