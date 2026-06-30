"""
ASTER 热红外发射率 / 硅化指数  —— 路线图 P2-b

书《遥感图像处理技术及应用》(张晔, 2024) §10.2.3 + 综述(王生礼2023)。ASTER 5 个热红外波段
(B10≈8.29 / B11≈8.63 / B12≈9.08 / B13≈10.66 / B14≈11.29 µm)对硅酸盐"reststrahlen"
发射率特征敏感:石英在 8.6–9.3µm 发射率显著降低。据此构建硅化(石英富集)指数 ——
硅化是斑岩/浅成低温热液金的核心蚀变向量,光学 SWIR 无法直接测,需热红外。

前置: geo-analyser P0-b 已让 load_sensor_data 把 ASTER TIR(90m) 重采样并入 bn_map(B10-B14)。

提供:
  - 发射率归一化(参考通道法,弱化温度影响)
  - 石英指数 QI = B11²/(B10·B12)  (Rockwell 2012),石英↑
  - SiO₂ wt% 回归 = 2.76·log10[6.56·B13·B14/(B10·B12)]  (陈江, 综述引)
  - 碳酸盐 TIR 指数 CI = B13/B14  (Ninomiya)

依赖: 仅 numpy。叶子模块。
"""

from __future__ import annotations

import numpy as np
from typing import Dict, Optional

_TIR_BANDS = ("B10", "B11", "B12", "B13", "B14")


def _bands(image: np.ndarray, bn_map: Dict[str, int], need) -> Dict[str, np.ndarray]:
    """按 bn_map 取所需 TIR 波段(float64)。缺任一则报错(说明该景未加载 TIR)。"""
    miss = [b for b in need if b not in bn_map]
    if miss:
        raise ValueError(f"影像缺 ASTER 热红外波段 {miss}(需 P0-b 加载 TIR);现有: {sorted(bn_map)}")
    return {b: image[bn_map[b]].astype(np.float64) for b in need}


def emissivity_normalize(image: np.ndarray, bn_map: Dict[str, int]) -> Dict[str, np.ndarray]:
    """参考通道发射率归一化:各 TIR 波段除以 5 波段均值,弱化温度、突出发射率形态。
    返回 {Bn: normalized}(B10-B14)。"""
    b = _bands(image, bn_map, _TIR_BANDS)
    stack = np.stack([b[k] for k in _TIR_BANDS], axis=0)
    mean = np.nanmean(stack, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return {k: np.where(mean > 1e-6, b[k] / mean, np.nan) for k in _TIR_BANDS}


def _ratio_safe(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        r = num / den
    return np.where(np.isfinite(den) & (np.abs(den) > 1e-9), r, np.nan)


def calc_quartz_index(image: np.ndarray, bn_map: Dict[str, int],
                      roi_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """石英(硅化)指数 QI = B11²/(B10·B12)。比值消去温度;石英 reststrahlen 使该值升高。"""
    b = _bands(image, bn_map, ("B10", "B11", "B12"))
    qi = _ratio_safe(b["B11"] * b["B11"], b["B10"] * b["B12"]).astype(np.float32)
    if roi_mask is not None:
        qi = np.where(roi_mask, qi, np.nan).astype(np.float32)
    return qi


def calc_sio2_percent(image: np.ndarray, bn_map: Dict[str, int],
                      roi_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """SiO₂ 含量(wt%)回归 = 2.76·log10[6.56·B13·B14/(B10·B12)]  (陈江, 综述引)。"""
    b = _bands(image, bn_map, ("B10", "B12", "B13", "B14"))
    inner = _ratio_safe(6.56 * b["B13"] * b["B14"], b["B10"] * b["B12"])
    with np.errstate(divide="ignore", invalid="ignore"):
        sio2 = 2.76 * np.log10(np.where(inner > 0, inner, np.nan))
    sio2 = sio2.astype(np.float32)
    if roi_mask is not None:
        sio2 = np.where(roi_mask, sio2, np.nan).astype(np.float32)
    return sio2


def calc_carbonate_index_tir(image: np.ndarray, bn_map: Dict[str, int],
                             roi_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """碳酸盐 TIR 指数 CI = B13/B14 (Ninomiya)。碳酸盐化↑。"""
    b = _bands(image, bn_map, ("B13", "B14"))
    ci = _ratio_safe(b["B13"], b["B14"]).astype(np.float32)
    if roi_mask is not None:
        ci = np.where(roi_mask, ci, np.nan).astype(np.float32)
    return ci


def calc_silica_index(image: np.ndarray, bn_map: Dict[str, int],
                      roi_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """硅化主指数(默认走石英指数 QI,作为蚀变证据层的索引图)。"""
    return calc_quartz_index(image, bn_map, roi_mask)
