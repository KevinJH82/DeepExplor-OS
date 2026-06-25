"""多元素组合异常 —— 把各元素异常强度场综合成"矿致组合异常"。

P1 用主成分(PCA via numpy SVD)：对齐各元素异常 → 标准化 → PC1 作组合异常。
PC1 反映元素共同变化（矿致组合通常同步增强）；不足 2 元素时退化为均值。
不依赖 sklearn。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np


def multi_element_factor(element_anoms: Dict[str, np.ndarray],
                         key_elements: List[str] = None
                         ) -> Tuple[np.ndarray, Dict]:
    """
    element_anoms: 元素→异常强度场 (ny,nx) [0,1]，NaN=无数据。
    返回 (combined(ny,nx) [0,1], stats)。
    """
    syms = [s for s in (key_elements or list(element_anoms.keys())) if s in element_anoms]
    syms = syms or list(element_anoms.keys())
    if not syms:
        return np.array([[]], dtype=np.float32), {"method": "none", "elements": []}

    shape = element_anoms[syms[0]].shape
    stack = np.stack([element_anoms[s] for s in syms], axis=0)  # (m,ny,nx)
    valid = np.all(np.isfinite(stack), axis=0)                  # 所有元素都有值的像元

    out = np.full(shape, np.nan, dtype=np.float32)
    if valid.sum() < 5 or len(syms) < 2:
        # 退化：可用元素的均值（按像元忽略 NaN）
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(stack, axis=0)
        out = mean.astype(np.float32)
        return _renorm(out), {"method": "mean", "elements": syms,
                              "n_valid_px": int(np.isfinite(out).sum())}

    # PCA：在公共有效像元上做
    X = stack[:, valid].T          # (npx, m)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-6, 1.0, sd)   # 近常数列(如背景元素全0)→不放大，标准化后为0
    Xs = (X - mu) / sd
    # SVD：主成分方向
    with np.errstate(all="ignore"):
        U, S, Vt = np.linalg.svd(Xs, full_matrices=False)
    pc1 = Vt[0]
    # 约定：让多数载荷为正（组合异常=元素同步增强）
    if np.sum(pc1) < 0:
        pc1 = -pc1
    with np.errstate(all="ignore"):
        scores = Xs @ pc1
    flat = out[valid]
    flat = scores.astype(np.float32)
    out[valid] = flat
    var_explained = float((S[0] ** 2) / np.sum(S ** 2)) if S.size else 0.0
    loadings = {s: round(float(w), 3) for s, w in zip(syms, pc1)}
    return _renorm(out), {"method": "pca_pc1", "elements": syms,
                          "variance_explained": round(var_explained, 3),
                          "loadings": loadings, "n_valid_px": int(valid.sum())}


def _renorm(arr: np.ndarray) -> np.ndarray:
    """把含 NaN 的数组按有效值的 2–98 百分位线性拉伸到 [0,1]。"""
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return arr.astype(np.float32)
    lo, hi = np.percentile(v, [2, 98])
    if hi <= lo:
        lo, hi = float(v.min()), float(v.max() if v.max() > v.min() else v.min() + 1.0)
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0).astype(np.float32)
