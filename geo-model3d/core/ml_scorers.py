"""数据驱动 ML scorer（方向四 P2）—— 随机森林成矿预测 + 特征重要性。

正样本=真实已知矿点（labels.py，绝非预测靶点）；负样本=远离已知点的随机背景采样。
特征=各 2D 地表证据层（蚀变/构造/化探/磁/形变…）。输出有利度 [0,1] + 可解释特征重要性。
sklearn 不可用时由上层回退 WofE/知识融合。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

try:
    from sklearn.ensemble import RandomForestClassifier
    _HAS_SK = True
except Exception:                     # pragma: no cover
    _HAS_SK = False


def has_sklearn() -> bool:
    return _HAS_SK


def _dilate(mask: np.ndarray, buffer: int) -> np.ndarray:
    if buffer <= 0:
        return mask
    try:
        from scipy.ndimage import binary_dilation
        return binary_dilation(mask, iterations=buffer)
    except Exception:
        return mask


def rf_score(surface_layers: Dict[str, np.ndarray], known_rowcol: List[Tuple[int, int]], grid,
             n_estimators: int = 300, neg_ratio: int = 10, neg_buffer: int = 2,
             random_state: int = 42, barren_rowcol: List[Tuple[int, int]] = None
             ) -> Tuple[np.ndarray, Dict]:
    """随机森林成矿有利度。返回 (score2d (ny,nx)∈[0,1] NaN填空, stats{importances,oob,...})。

    known_rowcol: 已知矿点网格 (row,col)。barren_rowcol: 钻孔确认无矿(真负样本，可选)。
    需 sklearn；调用前应确认 has_sklearn()。
    """
    if not _HAS_SK:
        raise RuntimeError("scikit-learn 不可用，无法运行随机森林（请回退 WofE/知识融合）")
    layers = [k for k in surface_layers if np.isfinite(surface_layers[k]).any()]
    if len(layers) < 1:
        raise ValueError("RF 无可用证据层")
    shape = surface_layers[layers[0]].shape
    ny, nx = shape

    # 公共有效域（所有特征都有值）
    valid = np.ones(shape, bool)
    for k in layers:
        valid &= np.isfinite(surface_layers[k])
    if valid.sum() < 50:
        raise ValueError("RF 有效像元过少")

    # 特征矩阵（仅有效像元）
    feat_full = np.stack([np.nan_to_num(surface_layers[k], nan=0.0) for k in layers], axis=-1)  # (ny,nx,F)
    X_all = feat_full[valid]                       # (n_valid, F)
    flat_idx = np.flatnonzero(valid.ravel())       # 有效像元在 ravel 中的位置

    # 正样本：已知矿点（落在有效域内）
    pos_mask = np.zeros(shape, bool)
    for (r, c) in known_rowcol:
        if 0 <= r < ny and 0 <= c < nx:
            pos_mask[r, c] = True
    pos_mask &= valid
    n_pos = int(pos_mask.sum())
    if n_pos < 1:
        raise ValueError("RF 需至少 1 个落入有效域的已知矿点")

    # 负样本：远离正样本（dilate buffer）的随机有效像元
    excl = _dilate(pos_mask, neg_buffer)
    neg_pool = valid & ~excl
    neg_lin = np.flatnonzero(neg_pool.ravel())
    rng = np.random.default_rng(random_state)
    n_neg = min(len(neg_lin), max(n_pos * neg_ratio, n_pos))
    if n_neg < 1:
        raise ValueError("RF 无可用负样本")
    neg_sel = rng.choice(neg_lin, size=n_neg, replace=False)

    pos_lin = np.flatnonzero(pos_mask.ravel())
    # ravel 索引 → 有效像元矩阵行号
    lin_to_row = -np.ones(ny * nx, dtype=np.int64)
    lin_to_row[flat_idx] = np.arange(flat_idx.size)
    pos_rows = lin_to_row[pos_lin]
    neg_rows = lin_to_row[neg_sel]
    pos_rows = pos_rows[pos_rows >= 0]; neg_rows = neg_rows[neg_rows >= 0]

    # 钻孔确认无矿 → 真负样本（闭环，方向四 P4）
    n_barren = 0
    if barren_rowcol:
        b_mask = np.zeros(shape, bool)
        for (r, c) in barren_rowcol:
            if 0 <= r < ny and 0 <= c < nx:
                b_mask[r, c] = True
        b_mask &= valid
        b_rows = lin_to_row[np.flatnonzero(b_mask.ravel())]
        b_rows = b_rows[b_rows >= 0]
        if b_rows.size:
            neg_rows = np.unique(np.concatenate([neg_rows, b_rows]))
            n_barren = int(b_rows.size)

    Xtr = np.vstack([X_all[pos_rows], X_all[neg_rows]])
    ytr = np.concatenate([np.ones(len(pos_rows)), np.zeros(len(neg_rows))])

    clf = RandomForestClassifier(
        n_estimators=n_estimators, class_weight="balanced",
        oob_score=True, bootstrap=True, n_jobs=-1, random_state=random_state,
        max_features="sqrt", min_samples_leaf=2)
    clf.fit(Xtr, ytr)

    proba_all = clf.predict_proba(X_all)[:, list(clf.classes_).index(1.0)]
    score = np.full(shape, np.nan, dtype=np.float32)
    score[valid] = proba_all.astype(np.float32)
    # 拉伸 [0,1]（按有效值 2–98 百分位）
    fin = score[np.isfinite(score)]
    if fin.size:
        lo, hi = np.percentile(fin, [2, 98])
        if hi > lo:
            score = np.clip((score - lo) / (hi - lo), 0, 1).astype(np.float32)

    importances = {k: round(float(imp), 4) for k, imp in zip(layers, clf.feature_importances_)}
    importances = dict(sorted(importances.items(), key=lambda kv: kv[1], reverse=True))
    stats = {"method": "random_forest", "features": layers, "importances": importances,
             "oob_score": round(float(getattr(clf, "oob_score_", float("nan"))), 4),
             "n_positive": n_pos, "n_negative": int(len(neg_rows)), "n_barren": n_barren,
             "n_estimators": n_estimators, "neg_ratio": neg_ratio}
    return score, stats


def pu_bagging_score(surface_layers: Dict[str, np.ndarray], known_rowcol: List[Tuple[int, int]], grid,
                     n_bags: int = 40, n_estimators: int = 80, random_state: int = 42,
                     barren_rowcol: List[Tuple[int, int]] = None
                     ) -> Tuple[np.ndarray, np.ndarray, Dict]:
    """PU-learning（Mordelet–Vert 装袋）：正样本=已知矿点，未标注≠负样本。

    每袋随机抽 |P| 个未标注作伪负、训 RF、对**未入袋(OOB)**的未标注打分；多袋聚合：
      score = OOB 均值（有利度），uncertainty = OOB 标准差（集成离散度→预测不确定性）。
    适配"已知矿点极少、未标注极大"，比 rf_score 的"远点即负"更诚实。
    返回 (score2d∈[0,1] NaN填空, uncertainty2d∈[0,1] NaN填空, stats)。需 sklearn。
    """
    if not _HAS_SK:
        raise RuntimeError("scikit-learn 不可用，无法运行 PU-learning")
    layers = [k for k in surface_layers if np.isfinite(surface_layers[k]).any()]
    if len(layers) < 1:
        raise ValueError("PU 无可用证据层")
    shape = surface_layers[layers[0]].shape
    ny, nx = shape
    valid = np.ones(shape, bool)
    for k in layers:
        valid &= np.isfinite(surface_layers[k])
    if valid.sum() < 50:
        raise ValueError("PU 有效像元过少")

    feat = np.stack([np.nan_to_num(surface_layers[k], nan=0.0) for k in layers], axis=-1)
    X_all = feat[valid]                                   # (n_valid, F)
    flat_idx = np.flatnonzero(valid.ravel())
    lin_to_row = -np.ones(ny * nx, dtype=np.int64)
    lin_to_row[flat_idx] = np.arange(flat_idx.size)

    pos_mask = np.zeros(shape, bool)
    for (r, c) in known_rowcol:
        if 0 <= r < ny and 0 <= c < nx:
            pos_mask[r, c] = True
    pos_mask &= valid
    pos_rows = lin_to_row[np.flatnonzero(pos_mask.ravel())]
    pos_rows = pos_rows[pos_rows >= 0]
    n_pos = int(pos_rows.size)
    if n_pos < 1:
        raise ValueError("PU 需至少 1 个落入有效域的已知矿点")

    n_valid = X_all.shape[0]
    is_pos = np.zeros(n_valid, bool); is_pos[pos_rows] = True
    # 钻孔确认无矿 → 真负样本：每袋恒为负、且不计入未标注 OOB 评分池（闭环，方向四 P4）
    barren_rows = np.array([], dtype=np.int64)
    if barren_rowcol:
        b_mask = np.zeros(shape, bool)
        for (r, c) in barren_rowcol:
            if 0 <= r < ny and 0 <= c < nx:
                b_mask[r, c] = True
        b_mask &= valid
        br = lin_to_row[np.flatnonzero(b_mask.ravel())]
        barren_rows = br[br >= 0]
    is_barren = np.zeros(n_valid, bool)
    if barren_rows.size:
        is_barren[barren_rows] = True
    unl_rows = np.flatnonzero(~is_pos & ~is_barren)       # 未标注像元行号(排除真负)
    Xpos = X_all[pos_rows]
    Xbar = X_all[barren_rows] if barren_rows.size else np.empty((0, X_all.shape[1]))

    rng = np.random.default_rng(random_state)
    ssum = np.zeros(n_valid); ssq = np.zeros(n_valid); cnt = np.zeros(n_valid)
    imp_acc = np.zeros(len(layers))
    bag_neg = min(len(unl_rows), max(n_pos, 1))           # 每袋伪负数≈|P|

    for b in range(n_bags):
        draw = rng.choice(unl_rows, size=bag_neg, replace=False)
        Xtr = np.vstack([Xpos, Xbar, X_all[draw]])
        ytr = np.concatenate([np.ones(n_pos), np.zeros(len(Xbar)), np.zeros(len(draw))])
        clf = RandomForestClassifier(n_estimators=n_estimators, max_features="sqrt",
                                     min_samples_leaf=2, n_jobs=-1,
                                     random_state=random_state + b)
        clf.fit(Xtr, ytr)
        imp_acc += clf.feature_importances_
        # OOB = 未入本袋伪负的未标注像元（排除正样本与钻孔真负）
        in_bag = np.zeros(n_valid, bool); in_bag[draw] = True
        oob = (~is_pos) & (~is_barren) & (~in_bag)
        if not oob.any():
            continue
        p = clf.predict_proba(X_all[oob])[:, list(clf.classes_).index(1.0)]
        ssum[oob] += p; ssq[oob] += p * p; cnt[oob] += 1

    has = cnt > 0
    mean = np.zeros(n_valid); std = np.zeros(n_valid)
    mean[has] = ssum[has] / cnt[has]
    var = np.zeros(n_valid)
    var[has] = np.maximum(ssq[has] / cnt[has] - mean[has] ** 2, 0.0)
    std[has] = np.sqrt(var[has])
    mean[is_pos] = 1.0           # 已知矿点有利度=1
    std[is_pos] = 0.0

    score = np.full(shape, np.nan, dtype=np.float32)
    unc = np.full(shape, np.nan, dtype=np.float32)
    sflat = score.ravel(); uflat = unc.ravel()
    sflat[flat_idx] = mean.astype(np.float32)
    uflat[flat_idx] = std.astype(np.float32)
    score = sflat.reshape(shape); unc = uflat.reshape(shape)

    # 拉伸有利度到 [0,1]
    fin = score[np.isfinite(score)]
    if fin.size:
        lo, hi = np.percentile(fin, [2, 98])
        if hi > lo:
            score = np.clip((score - lo) / (hi - lo), 0, 1).astype(np.float32)
    # 不确定性归一到 [0,1]（按 95 分位）
    ufin = unc[np.isfinite(unc)]
    if ufin.size:
        u95 = float(np.percentile(ufin, 95)) or 1.0
        unc = np.clip(unc / (u95 + 1e-9), 0, 1).astype(np.float32)

    importances = {k: round(float(v / max(n_bags, 1)), 4) for k, v in zip(layers, imp_acc)}
    importances = dict(sorted(importances.items(), key=lambda kv: kv[1], reverse=True))
    # 标签频率粗估（已知正样本占有利度高位的比例提示，仅记录）
    stats = {"method": "pu_bagging", "features": layers, "importances": importances,
             "n_positive": n_pos, "n_bags": n_bags, "bag_negatives": int(bag_neg),
             "n_barren": int(barren_rows.size),
             "mean_uncertainty": round(float(np.nanmean(unc)), 4),
             "n_estimators": n_estimators}
    return score, unc, stats
