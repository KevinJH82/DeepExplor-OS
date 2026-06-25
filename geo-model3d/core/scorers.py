"""立体成矿预测 scorer。

P1 主力：fuse_surface_2d（知识加权融合 2D 地表证据，零标签）+ gate_to_3d（知识深度门控）。
设计见 evidence.py：score3d = F_xy × DepthGate，深度由矿床族成矿深度带决定（软深度）。
标签驱动方法（woe/info）接口保留但默认关闭：预测靶点不可作标签（循环论证→假准）。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from core.evidence import (depth_consistency_profile, depth_preference_profile,
                           structure_depth_tail, structure_skeleton_volume)


def _select_layers(surface_layers: Dict[str, np.ndarray], weights: Dict[str, float]
                   ) -> Tuple[list, Dict[str, float]]:
    """选有正权重的存在层（无则取全部），返回 (layers, 重归一化 used 权重)。
    供 fuzzy/bayesian 融合复用，与 fuse_surface_2d 的层选择逻辑一致。"""
    layers = [k for k in surface_layers.keys() if weights.get(k, 0.0) > 0.0]
    if not layers:
        layers = list(surface_layers.keys())
    wsum = sum(weights.get(k, 0.0) for k in layers) or 1.0
    used = {k: weights.get(k, 0.0) / wsum for k in layers}
    return layers, used


def fuse_surface_2d(surface_layers: Dict[str, np.ndarray], weights: Dict[str, float]
                    ) -> Tuple[np.ndarray, Dict[str, float]]:
    """各 2D 地表证据按矿床族权重融合 → F_xy (ny,nx) ∈ [0,1]。仅用存在的层并重归一化。"""
    layers = [k for k in surface_layers.keys() if weights.get(k, 0.0) > 0.0]
    if not layers:
        layers = list(surface_layers.keys())
    wsum = sum(weights.get(k, 0.0) for k in layers) or 1.0
    used = {k: weights.get(k, 0.0) / wsum for k in layers}
    if not layers:
        # 无地表证据 → 全零（深度门控后仍为零，交由上层判处理）
        any_arr = next(iter(surface_layers.values()))
        return np.zeros_like(any_arr, dtype=np.float32), {}
    shape = surface_layers[layers[0]].shape
    F = np.zeros(shape, dtype=np.float32)
    for k in layers:
        v = np.where(np.isfinite(surface_layers[k]), surface_layers[k], 0.0)
        F += used[k] * np.clip(v.astype(np.float32), 0.0, 1.0)
    return np.clip(F, 0.0, 1.0).astype(np.float32), used


def gate_to_3d(F_xy: np.ndarray, surface_layers: Dict[str, np.ndarray],
               weights: Dict[str, float], grid, depth_km_band,
               strikes=None, dip_deg=None) -> np.ndarray:
    """知识深度门控：score3d = F_xy × DepthGate；构造层贡献向深尾部。

    dip_deg 给定时构造尾部用三维倾向投影骨架(structure_skeleton_volume)替代纯垂直尾部；
    dip_deg=None(默认)保持原标量向深尾部行为不变（向后兼容）。
    """
    dc = depth_preference_profile(grid, depth_km_band)            # (nz,) 带中心峰
    score3d = F_xy[None, :, :] * dc[:, None, None]

    # 断裂向深尾部：在构造高值处，为深部补一份不受深度带限制的通道favorability
    if "structure" in surface_layers and weights.get("structure", 0) > 0:
        wstruct = weights.get("structure", 0.0)
        wsum = sum(v for k, v in weights.items() if k in surface_layers and v > 0) or 1.0
        alpha = 0.5 * (wstruct / wsum)  # 尾部贡献占比(不喧宾夺主)
        if dip_deg is not None:
            struct_vol = structure_skeleton_volume(grid, surface_layers["structure"],
                                                   strikes or [], depth_km_band, dip_deg)
        else:
            tail = structure_depth_tail(grid, depth_km_band)      # (nz,)
            s2d = np.where(np.isfinite(surface_layers["structure"]), surface_layers["structure"], 0.0)
            struct_vol = (s2d[None, :, :] * tail[:, None, None]).astype(np.float32)
        score3d = (1 - alpha) * score3d + alpha * struct_vol

    return np.clip(score3d, 0.0, 1.0).astype(np.float32)


def knowledge_weighted_fusion(surface_layers: Dict[str, np.ndarray], weights: Dict[str, float],
                              grid, depth_km_band, mode: str = "gate",
                              strikes=None, dip_deg=None
                              ) -> Tuple[np.ndarray, Dict[str, float]]:
    """P1 主力入口：2D 融合 + 知识深度门控 → (score3d, used_weights)。"""
    F_xy, used = fuse_surface_2d(surface_layers, weights)
    score3d = gate_to_3d(F_xy, surface_layers, weights, grid, depth_km_band,
                         strikes=strikes, dip_deg=dip_deg)
    return score3d, used


def fuzzy_gamma_score(surface_layers: Dict[str, np.ndarray], weights: Dict[str, float],
                      gamma: float = 0.9) -> Tuple[np.ndarray, Dict[str, float]]:
    """模糊γ算子融合（P2 特性B）：层值已∈[0,1]即模糊隶属度 μ，权重作指数强调。

    μ_w = μ ** used[k]；fuzzy_prod = Π μ_w (AND类)，fuzzy_sum = 1-Π(1-μ_w) (OR类)；
    F = fuzzy_prod^(1-γ) * fuzzy_sum^γ ∈ [0,1]。γ 越大越偏增强(OR)，越小越偏约束(AND)。
    返回 (F_xy, used_weights)。
    """
    layers, used = _select_layers(surface_layers, weights)
    if not layers:
        any_arr = next(iter(surface_layers.values()))
        return np.zeros_like(any_arr, dtype=np.float32), {}
    shape = surface_layers[layers[0]].shape
    prod = np.ones(shape, dtype=np.float64)
    one_minus = np.ones(shape, dtype=np.float64)
    for k in layers:
        mu = np.clip(np.where(np.isfinite(surface_layers[k]), surface_layers[k], 0.0), 0.0, 1.0)
        w = float(used.get(k, 0.0))
        mu_w = np.power(mu, w) if w > 0 else np.ones(shape, dtype=np.float64)
        prod *= mu_w
        one_minus *= (1.0 - mu_w)
    fuzzy_sum = 1.0 - one_minus
    F = np.power(prod, 1.0 - gamma) * np.power(fuzzy_sum, gamma)
    return np.clip(F, 0.0, 1.0).astype(np.float32), used


def bayesian_fusion_score(surface_layers: Dict[str, np.ndarray], weights: Dict[str, float]
                          ) -> Tuple[np.ndarray, np.ndarray]:
    """贝叶斯后验融合（P2 特性B）：各归一证据作似然，log-odds 空间按族权(先验)叠加。

    p=clip(μ,eps,1-eps)；L=Σ used[k]·log(p/(1-p))；F=sigmoid(L)；
    后验方差 var = F(1-F)/(Σused+eps)（证据越多越一致→不确定性越低）。
    返回 (F_xy ∈[0,1], var2d ∈[0,1])。供 model3d_engine 把 var2d 并入不确定性体。
    """
    eps = 1e-4
    layers, used = _select_layers(surface_layers, weights)
    if not layers:
        any_arr = next(iter(surface_layers.values()))
        return (np.zeros_like(any_arr, dtype=np.float32),
                np.ones_like(any_arr, dtype=np.float32))
    shape = surface_layers[layers[0]].shape
    L = np.zeros(shape, dtype=np.float64)
    wsum = 0.0
    for k in layers:
        w = float(used.get(k, 0.0))
        if w <= 0:
            continue
        mu = np.clip(np.where(np.isfinite(surface_layers[k]), surface_layers[k], 0.5), eps, 1.0 - eps)
        L += w * np.log(mu / (1.0 - mu))
        wsum += w
    F = 1.0 / (1.0 + np.exp(-L))
    var = F * (1.0 - F) / (wsum + eps)
    return (np.clip(F, 0.0, 1.0).astype(np.float32),
            np.clip(var, 0.0, 1.0).astype(np.float32))


# ─────────────────────────────────────────────
# 标签驱动方法：P1 默认关闭（防循环论证）
# ─────────────────────────────────────────────
class ProxyLabelError(RuntimeError):
    """用预测靶点当训练标签会循环论证，禁止。"""


def _guard_proxy(allow_proxy: bool, label_source: str):
    if not allow_proxy and label_source in ("exploration_targets", "predicted", None):
        raise ProxyLabelError(
            "证据权/信息量需真实已知矿点；预测靶点不可作标签（用证据预测证据自身=假准）。"
            "P1 默认关闭，请用 knowledge_weighted_fusion；待真值到位再开（方向四）。")


def _deposit_mask(known_rowcol, shape, buffer: int = 0) -> np.ndarray:
    """已知矿点 (row,col) → (ny,nx) bool 矿点掩码（可选 ±buffer 格扩张）。"""
    ny, nx = shape
    m = np.zeros(shape, dtype=bool)
    for (r, c) in known_rowcol:
        if 0 <= r < ny and 0 <= c < nx:
            r0, r1 = max(0, r - buffer), min(ny, r + buffer + 1)
            c0, c1 = max(0, c - buffer), min(nx, c + buffer + 1)
            m[r0:r1, c0:c1] = True
    return m


def _binarize(layer: np.ndarray, pct: float = 70.0) -> Tuple[np.ndarray, np.ndarray, float]:
    """证据二值化：有利=高于分位阈值。返回 (favorable bool, valid bool, threshold)。"""
    valid = np.isfinite(layer)
    fin = layer[valid]
    if fin.size == 0:
        return np.zeros(layer.shape, bool), valid, float("nan")
    thr = float(np.percentile(fin, pct))
    fav = valid & (layer >= thr)
    return fav, valid, thr


def woe_score(surface_layers, known_rowcol, grid, label_source="known_deposits",
              allow_proxy: bool = False, fav_pct: float = 70.0):
    """证据权法 WofE（方向四）：真实已知矿点为正样本，证据二值化→W±→后验对数几率→[0,1]。

    返回 (score2d (ny,nx)∈[0,1], weights_table)。known_rowcol=已知矿点网格(row,col)列表。
    """
    _guard_proxy(allow_proxy, label_source)
    layers = [k for k in surface_layers if np.isfinite(surface_layers[k]).any()]
    if not layers:
        raise ValueError("WofE 无可用证据层")
    shape = surface_layers[layers[0]].shape
    D = _deposit_mask(known_rowcol, shape, buffer=0)
    n_D = int(D.sum())
    if n_D < 1:
        raise ValueError("WofE 需至少 1 个落入网格的已知矿点")

    # 公共有效域：所有层都有值
    valid_all = np.ones(shape, bool)
    for k in layers:
        valid_all &= np.isfinite(surface_layers[k])
    N = int(valid_all.sum())
    n_D_valid = int((D & valid_all).sum()) or n_D
    s = 0.5  # Laplace 平滑

    logit = np.full(shape, np.log((n_D_valid + s) / (N - n_D_valid + s)), dtype=np.float64)
    weights_table: Dict[str, dict] = {}
    for k in layers:
        fav, valid, thr = _binarize(surface_layers[k], fav_pct)
        B = fav & valid_all
        nB = int(B.sum())
        nBD = int((B & D).sum())
        # 条件概率（含平滑）
        p_B_D = (nBD + s) / (n_D_valid + 2 * s)
        p_B_nD = (nB - nBD + s) / (N - n_D_valid + 2 * s)
        p_nB_D = (n_D_valid - nBD + s) / (n_D_valid + 2 * s)
        p_nB_nD = (N - nB - (n_D_valid - nBD) + s) / (N - n_D_valid + 2 * s)
        Wp = float(np.log(p_B_D / p_B_nD))
        Wm = float(np.log(p_nB_D / p_nB_nD))
        logit += np.where(B, Wp, Wm)
        weights_table[k] = {"W_plus": round(Wp, 4), "W_minus": round(Wm, 4),
                            "contrast": round(Wp - Wm, 4), "threshold": round(thr, 4),
                            "n_favorable": nB, "n_fav_and_deposit": nBD}
    score = 1.0 / (1.0 + np.exp(-logit))           # sigmoid
    score = np.where(valid_all, score, np.nan).astype(np.float32)
    # 归一到 [0,1]（按有效值拉伸，便于与知识融合同尺度）
    fin = score[np.isfinite(score)]
    if fin.size:
        lo, hi = np.percentile(fin, [2, 98])
        if hi > lo:
            score = np.clip((score - lo) / (hi - lo), 0, 1).astype(np.float32)
    return score, weights_table


def information_value(surface_layers, known_rowcol, grid, label_source="known_deposits",
                      allow_proxy: bool = False, fav_pct: float = 70.0):
    """信息量法（方向四）：各证据类信息量 I=ln(P(class|D)/P(class)) 叠加 → [0,1]。

    返回 (score2d (ny,nx)∈[0,1], iv_table)。
    """
    _guard_proxy(allow_proxy, label_source)
    layers = [k for k in surface_layers if np.isfinite(surface_layers[k]).any()]
    if not layers:
        raise ValueError("信息量法无可用证据层")
    shape = surface_layers[layers[0]].shape
    valid_all = np.ones(shape, bool)
    for k in layers:
        valid_all &= np.isfinite(surface_layers[k])
    D = _deposit_mask(known_rowcol, shape, buffer=0)
    N = int(valid_all.sum())
    n_D = int((D & valid_all).sum())
    if n_D < 1:
        raise ValueError("信息量法需至少 1 个落入网格的已知矿点")
    s = 0.5

    acc = np.zeros(shape, dtype=np.float64)
    iv_table: Dict[str, dict] = {}
    for k in layers:
        fav, valid, thr = _binarize(surface_layers[k], fav_pct)
        B = fav & valid_all
        nB = int(B.sum()); nBD = int((B & D).sum())
        nNB = N - nB; nNBD = n_D - nBD
        # 各类信息量：I = ln( (n_class_D/n_D) / (n_class/N) )
        I_fav = np.log(((nBD + s) / (n_D + 2 * s)) / ((nB + s) / (N + 2 * s)))
        I_unf = np.log(((nNBD + s) / (n_D + 2 * s)) / ((nNB + s) / (N + 2 * s)))
        acc += np.where(B, I_fav, I_unf)
        iv_table[k] = {"I_favorable": round(float(I_fav), 4),
                       "I_unfavorable": round(float(I_unf), 4), "threshold": round(thr, 4)}
    acc = np.where(valid_all, acc, np.nan)
    fin = acc[np.isfinite(acc)]
    score = acc.astype(np.float32)
    if fin.size:
        lo, hi = np.percentile(fin, [2, 98])
        if hi > lo:
            score = np.clip((acc - lo) / (hi - lo), 0, 1).astype(np.float32)
    return np.where(valid_all, score, np.nan).astype(np.float32), iv_table
