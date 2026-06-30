"""
多源像元级配准 (multi-source co-registration)  —— 路线图 P2-c

书《遥感图像处理技术及应用》(张晔, 2024) §10.5.2:多源融合前必须把各源对齐到统一格网,
配准精度直接决定后续定量分析(融合/解混)的正确性。本模块把不同分辨率/不同来源的栅格
(光学、ASTER、InSAR、物探位场等)重采样到同一参考格网,并用相位相关估计/校正残余错位。

两步:
  1. 重采样到参考格网(以指定层或最高分辨率层为参考)。
  2. 相位相关(FFT)估计各层相对参考的整数像元平移并校正残余错位(同名地物对齐)。

依赖: numpy + scipy.ndimage。I/O 与 CRS/transform 级严格重投影由调用方(rasterio)负责;
本模块聚焦"同范围、不同采样"的像元对齐(与 geo-analyser load_sensor_data 的近似对齐同源,
但额外做相位相关精配准)。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from scipy import ndimage


def resample_to_shape(arr: np.ndarray, shape: Tuple[int, int], order: int = 1) -> np.ndarray:
    """把 2D 数组重采样到目标 (H,W)。order=1 双线性。NaN 先填充再重采样避免扩散。"""
    h, w = arr.shape
    th, tw = shape
    if (h, w) == (th, tw):
        return arr.astype(np.float32)
    filled = np.where(np.isfinite(arr), arr, np.nanmean(arr[np.isfinite(arr)]) if np.isfinite(arr).any() else 0.0)
    zoom = (th / h, tw / w)
    out = ndimage.zoom(filled.astype(np.float32), zoom, order=order)
    # 尺寸兜底(zoom 可能差 1 像元)
    out = out[:th, :tw]
    if out.shape != (th, tw):
        pad = np.zeros((th, tw), np.float32)
        pad[:out.shape[0], :out.shape[1]] = out
        out = pad
    return out.astype(np.float32)


def estimate_shift(ref: np.ndarray, mov: np.ndarray) -> Tuple[int, int]:
    """相位相关估计 mov 相对 ref 的整数像元平移 (dy,dx)。两图须同尺寸。
    返回的 (dy,dx) 为"把 mov 平移 (dy,dx) 后与 ref 对齐"。"""
    a = np.where(np.isfinite(ref), ref, 0.0).astype(np.float64)
    b = np.where(np.isfinite(mov), mov, 0.0).astype(np.float64)
    a = a - a.mean()
    b = b - b.mean()
    F0 = np.fft.fft2(a)
    F1 = np.fft.fft2(b)
    cross = F0 * np.conj(F1)
    cross /= np.abs(cross) + 1e-12
    corr = np.fft.ifft2(cross).real
    H, W = corr.shape
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy = py - H if py > H // 2 else py
    dx = px - W if px > W // 2 else px
    return int(dy), int(dx)


def apply_shift(arr: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """整数平移 arr (dy 行, dx 列),边界以 NaN 填充。"""
    return ndimage.shift(arr.astype(np.float32), (dy, dx), order=1, cval=np.nan, mode="constant")


@dataclass
class CoregResult:
    reference: str
    grid_hw: Tuple[int, int]
    layers: Dict[str, np.ndarray]              # 对齐后各层 (grid_hw)
    shifts: Dict[str, Tuple[int, int]] = field(default_factory=dict)   # 各层相对参考的平移


def coregister(
    layers: Dict[str, np.ndarray],
    reference: Optional[str] = None,
    refine: bool = True,
) -> CoregResult:
    """把多层栅格对齐到统一格网。

    layers    : {名称: 2D 数组}(尺寸可不同,假定覆盖同一范围)
    reference : 参考层名;缺省取像元数最多(最高分辨率)的层
    refine    : 是否用相位相关校正残余整数像元错位
    """
    if not layers:
        return CoregResult(reference="", grid_hw=(0, 0), layers={})
    if reference is None or reference not in layers:
        reference = max(layers, key=lambda k: layers[k].size)
    th, tw = layers[reference].shape
    ref_grid = layers[reference].astype(np.float32)

    out: Dict[str, np.ndarray] = {}
    shifts: Dict[str, Tuple[int, int]] = {}
    for name, arr in layers.items():
        r = resample_to_shape(arr, (th, tw))
        if name == reference:
            out[name] = r
            shifts[name] = (0, 0)
            continue
        if refine:
            dy, dx = estimate_shift(ref_grid, r)
            # 仅在错位不过大时校正(过大多为内容差异,不强行平移)
            if abs(dy) <= th // 8 and abs(dx) <= tw // 8 and (dy or dx):
                r = apply_shift(r, dy, dx)
                shifts[name] = (dy, dx)
            else:
                shifts[name] = (0, 0)
        else:
            shifts[name] = (0, 0)
        out[name] = r.astype(np.float32)

    return CoregResult(reference=reference, grid_hw=(th, tw), layers=out, shifts=shifts)
