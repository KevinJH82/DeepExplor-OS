"""
光谱吸收特征参数 SASP (Spectral Absorption Signature Parameters)  —— 路线图 P1-a

书《遥感图像处理技术及应用》(张晔, 2024) §7.2.2。在连续统去除吸收深度(calc_band_depth 给单点深度)
基础上,对高光谱(EnMAP/PRISMA/EMIT)在诊断吸收窗口内提取一组刻画"吸收形态"的参数:

  - 位置 P  (position)   : 吸收质心波长(µm)。**种属判别核心** —— Al-OH 吸收位置随
                            白云母(2.20)→伊利石→高岭石(2.21)系统移动,Mg-OH 区分绿泥石/绿帘石。
  - 深度 D  (depth)      : 窗口内最大连续统去除吸收深度(丰度代理)。
  - 宽度 W  (width)      : 吸收带 FWHM(µm),由深度加权方差换算(W=2.3548·σ)。
  - 不对称 A (asymmetry) : 吸收剖面偏度(吸收两翼不对称,矿物混合/晶格畸变指示)。

实现要点:在窗口内做端点连续统去除得到逐波段吸收深度剖面,再以"深度加权矩"一次性
向量化算出 P/W/A(质心/方差/偏度),D 取峰值。比逐像元拟合稳健且快。

依赖: 仅 numpy。本模块为叶子模块,不反向 import alteration_analysis(由后者 import 本模块)。
"""

from __future__ import annotations

import numpy as np
from typing import Dict, Optional, Tuple


def _continuum_removed_profile(sub: np.ndarray, wl: np.ndarray) -> np.ndarray:
    """窗口内端点直线连续统去除。sub:(n,H,W) 反射率, wl:(n,) 波长。
    返回逐波段吸收深度 depth=1-R/continuum (n,H,W);连续统非正处置 NaN。"""
    n = sub.shape[0]
    w0, w1 = float(wl[0]), float(wl[-1])
    span = (w1 - w0) or 1e-9
    frac = ((wl - w0) / span).reshape(n, 1, 1)         # 端点线性插值系数
    cont = sub[0][None] + (sub[-1] - sub[0])[None] * frac
    with np.errstate(divide="ignore", invalid="ignore"):
        cr = sub / cont
    depth = 1.0 - cr
    depth = np.where(np.isfinite(cont) & (cont > 1e-4), depth, np.nan)
    return depth.astype(np.float32)


def extract_sasp(
    cube: np.ndarray,
    wavelengths_um: np.ndarray,
    feature_um: float,
    shoulder_um: Tuple[float, float],
    roi_mask: Optional[np.ndarray] = None,
    min_bands: int = 5,
) -> Tuple[Dict[str, np.ndarray], bool]:
    """提取 SASP 参数图。返回 ({position,depth,width,asymmetry}, ok)。
    窗口内波段数 < min_bands(多光谱不适用)时 ok=False,各图为 NaN。"""
    wl = np.asarray(wavelengths_um, dtype=np.float64)
    left, right = float(shoulder_um[0]), float(shoulder_um[1])
    idx = np.where((wl >= left) & (wl <= right))[0]
    H, W = cube.shape[1], cube.shape[2]
    nan_map = lambda: np.full((H, W), np.nan, dtype=np.float32)

    if idx.size < min_bands:
        return ({"position": nan_map(), "depth": nan_map(),
                 "width": nan_map(), "asymmetry": nan_map()}, False)

    sub = cube[idx].astype(np.float32)
    wls = wl[idx]
    depth = _continuum_removed_profile(sub, wls)        # (n,H,W)

    # 深度加权矩(仅取正深度;负值=噪声越过连续统)
    d = np.clip(np.nan_to_num(depth, nan=0.0), 0.0, None)   # (n,H,W)
    wl_b = wls.reshape(-1, 1, 1)
    m0 = d.sum(axis=0)                                       # 总吸收量
    valid = m0 > 1e-6

    with np.errstate(divide="ignore", invalid="ignore"):
        P = (d * wl_b).sum(axis=0) / m0                     # 质心位置
        var = (d * (wl_b - P[None]) ** 2).sum(axis=0) / m0  # 二阶矩
        sigma = np.sqrt(np.clip(var, 0.0, None))
        Wmap = 2.3548 * sigma                               # FWHM
        skew = (d * (wl_b - P[None]) ** 3).sum(axis=0) / (m0 * np.clip(sigma, 1e-6, None) ** 3)

    D = np.nanmax(depth, axis=0)                            # 峰值深度

    out = {}
    for name, arr in (("position", P), ("depth", D), ("width", Wmap), ("asymmetry", skew)):
        a = np.where(valid, arr, np.nan).astype(np.float32)
        if roi_mask is not None:
            a = np.where(roi_mask, a, np.nan).astype(np.float32)
        out[name] = a
    return out, True


def sasp_index(
    cube: np.ndarray,
    wavelengths_um: np.ndarray,
    feature_um: float,
    shoulder_um: Tuple[float, float],
    roi_mask: Optional[np.ndarray] = None,
    pos_tol_um: float = 0.015,
    min_bands: int = 5,
) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """种属匹配吸收指数 = 深度 D × 位置匹配高斯权重 exp(-((P-feature)/tol)²)。
    既要"吸收深"(丰度)又要"吸收位置落在该矿物诊断位"(种属),比单纯 band_depth 更具种属特异性。
    返回 (index_map, sasp_maps)。窗口波段不足时 index 全 NaN。"""
    maps, ok = extract_sasp(cube, wavelengths_um, feature_um, shoulder_um, roi_mask, min_bands)
    if not ok:
        return maps["depth"], maps   # 全 NaN
    P, D = maps["position"], maps["depth"]
    with np.errstate(invalid="ignore"):
        w_pos = np.exp(-(((P - float(feature_um)) / float(pos_tol_um)) ** 2))
    index = (D * w_pos).astype(np.float32)
    if roi_mask is not None:
        index = np.where(roi_mask, index, np.nan).astype(np.float32)
    return index, maps
