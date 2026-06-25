"""背景/异常分离 —— 分形 C-A（含量-面积）法自动求异常下限。

原理：在 log(含量)-log(面积≥含量) 图上，地球化学场常呈多段直线（分形/多重分形）；
背景与异常的转折点（最大曲率/分段拟合拐点）即异常下限。比固定 1.5/2/3 倍背景更客观。
点太少或无明显拐点时回退到百分位阈值（并标注 method）。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np


def _two_segment_break(logx: np.ndarray, logA: np.ndarray) -> Optional[int]:
    """在排序后的 (logx, logA) 上找两段线性拟合总残差最小的断点索引。"""
    n = logx.size
    if n < 8:
        return None
    best_i, best_err = None, np.inf
    # 两侧各至少留 3 点
    for i in range(3, n - 3):
        e = 0.0
        with np.errstate(all="ignore"):
            for sl in (slice(0, i + 1), slice(i, n)):
                xs, ys = logx[sl], logA[sl]
                if np.ptp(xs) < 1e-9:          # 近垂直段：跳过，避免病态拟合
                    e = np.inf; break
                A = np.vstack([xs, np.ones_like(xs)]).T
                coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
                pred = A @ coef
                e += float(np.sum((ys - pred) ** 2))
        if e < best_err:
            best_err, best_i = e, i
    return best_i


def ca_anomaly_separation(arr2d: np.ndarray, prior_threshold: Optional[float] = None,
                          fallback_pct: float = 85.0
                          ) -> Tuple[np.ndarray, float, Dict]:
    """
    arr2d: 元素含量网格 (ny,nx)，NaN=无数据。
    返回 (anomaly(ny,nx) [0,1], threshold, ca_curve)：
      anomaly = 高于异常下限部分按对数强度归一到 [0,1]，低于下限=0，无数据=NaN。
      ca_curve = {"log_conc":[...], "log_area":[...], "threshold":..., "method":...}
    """
    finite = arr2d[np.isfinite(arr2d)]
    finite = finite[finite > 0]
    if finite.size < 5:
        thr = float(prior_threshold) if prior_threshold else (
            float(np.nanpercentile(arr2d, fallback_pct)) if np.isfinite(arr2d).any() else 0.0)
        anom = _intensity(arr2d, thr)
        return anom, thr, {"method": "too_few_data", "threshold": thr}

    vals = np.sort(finite)
    # C-A：面积(≥v) ∝ 计数(≥v)
    uniq = np.unique(vals)
    area = np.array([(finite >= v).sum() for v in uniq], dtype=np.float64)
    logx = np.log10(uniq)
    logA = np.log10(area)

    method = "ca_fractal"
    bi = _two_segment_break(logx, logA)
    if bi is not None:
        thr = float(10 ** logx[bi])
    else:
        thr = float(np.percentile(finite, fallback_pct))
        method = "percentile_fallback"

    # 若给了先验背景且 C-A 结果明显偏低，取两者较大（更保守、少误报）
    if prior_threshold and np.isfinite(prior_threshold) and prior_threshold > thr:
        thr = float(prior_threshold)
        method += "+prior"

    anom = _intensity(arr2d, thr)
    return anom, thr, {
        "method": method, "threshold": round(thr, 6),
        "n_unique": int(uniq.size),
        "log_conc": [round(float(x), 4) for x in logx[::max(1, uniq.size // 60)]],
        "log_area": [round(float(a), 4) for a in logA[::max(1, uniq.size // 60)]],
    }


def _intensity(arr2d: np.ndarray, threshold: float) -> np.ndarray:
    """高于阈值部分按对数强度归一到 [0,1]；其余 0；无数据 NaN。"""
    out = np.full(arr2d.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(arr2d)
    out[valid] = 0.0
    if threshold <= 0:
        threshold = float(np.nanmax(arr2d)) if valid.any() else 1.0
    above = valid & (arr2d > threshold)
    if above.any():
        hi = float(np.nanmax(arr2d[above]))
        if hi > threshold:
            inten = (np.log10(arr2d[above]) - np.log10(threshold)) / (np.log10(hi) - np.log10(threshold))
            out[above] = np.clip(inten, 0.0, 1.0).astype(np.float32)
        else:
            out[above] = 1.0
    return out
