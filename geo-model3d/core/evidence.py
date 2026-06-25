"""证据三维化 —— "2D 证据融合 → 知识深度门控"。

地质依据：地表蚀变/构造是深部矿体的地表 footprint。因此 P1 用
  score3d(z,y,x) = F_xy(y,x) × DepthGate(z)
其中 F_xy = 各 2D 地表证据的知识加权融合（xy 模式由证据决定），
DepthGate = 知识深度带一致性（深度由矿床成因族的成矿深度带决定，软深度、非反演）。
构造层额外保留向深"尾部"（断裂作为通道向下延伸），使深部不至于完全由单一深度带决定。
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np


def depth_consistency_profile(grid, depth_km_band) -> np.ndarray:
    """(nz,) 体元深度对知识深度带的一致性：带内≈1，带外高斯衰减。"""
    dmin_m = float(depth_km_band[0]) * 1000.0
    dmax_m = float(depth_km_band[1]) * 1000.0
    d = np.abs(grid.depths())  # 正值米
    width = max(dmax_m - dmin_m, grid.dz_m)
    sigma = 0.5 * width + grid.dz_m
    prof = np.ones_like(d, dtype=np.float32)
    below = d < dmin_m
    above = d > dmax_m
    prof[below] = np.exp(-((dmin_m - d[below]) ** 2) / (2 * sigma ** 2))
    prof[above] = np.exp(-((d[above] - dmax_m) ** 2) / (2 * sigma ** 2))
    return prof.astype(np.float32)


def depth_preference_profile(grid, depth_km_band) -> np.ndarray:
    """(nz,) 成矿深度偏好：成矿带中心为峰(=1)、带边为半高(0.5)、带外继续衰减。

    与 depth_consistency_profile 的"平台"不同，此处在带内有唯一内部极大值，
    使 score=F_xy×门控 在深度方向有明确偏好——靶点落在地质预期成矿深度(带中心附近)
    而非被表层项打平到网格最顶层。仅供"打分"路径用；不确定性仍用平台型 consistency。
    """
    dmin_m = float(depth_km_band[0]) * 1000.0
    dmax_m = float(depth_km_band[1]) * 1000.0
    d = np.abs(grid.depths())
    center = 0.5 * (dmin_m + dmax_m)
    half = max(0.5 * (dmax_m - dmin_m), grid.dz_m)
    sigma = half / 1.1774          # 带边(center±half)恰为半高，即 FWHM=带宽
    prof = np.exp(-((d - center) ** 2) / (2 * sigma ** 2))
    return prof.astype(np.float32)


def structure_depth_tail(grid, depth_km_band) -> np.ndarray:
    """(nz,) 断裂向深尾部：从地表起缓慢衰减（断裂作为通道延伸至深部）。"""
    d = np.abs(grid.depths())
    scale = max(float(depth_km_band[1]) * 1000.0 * 1.5, 1000.0)
    return np.exp(-d / scale).astype(np.float32)


def structure_skeleton_volume(grid, structure2d: np.ndarray, strikes: List[float],
                              depth_km_band, dip_deg: float = 80.0) -> np.ndarray:
    """(nz,ny,nx) 断裂骨架向深三维投影（P2 特性A，替代纯垂直 structure_depth_tail）。

    地质模型：倾角 dip、走向 θ 的断裂，其迹线在深度 d 处沿倾向(θ±90°)横移
    offset = d / tan(dip)。逐层用 scipy.ndimage.shift 平移 2D 构造有利度，
    再乘 structure_depth_tail 的向深衰减包络（深部断裂通道几何偏移而非纯垂直）。

    strikes 为主断裂走向(度，地理方位，自北顺时针)；多走向各自投影后取 np.maximum
    （任一断裂族投到该体元即有利）。无走向→退化为 tail[z]*s2d（旧垂直行为，向后兼容）。
    """
    from scipy.ndimage import shift as nd_shift

    s2d = np.where(np.isfinite(structure2d), structure2d, 0.0).astype(np.float32)
    tail = structure_depth_tail(grid, depth_km_band)        # (nz,) 向深衰减包络
    nz = grid.nz
    depths_abs = np.abs(grid.depths())                       # (nz,) 正值米
    tan_dip = max(math.tan(math.radians(float(dip_deg))), 1e-3)

    # 走向→倾向(θ+90)单位位移向量(像素)：东=+col, 北=-row；方位自北顺时针 az
    strikes = [float(s) for s in (strikes or [])][:3]        # 封顶 3 个主走向
    dirs = []
    for theta in strikes:
        az = math.radians(theta + 90.0)
        dcol = math.sin(az)
        drow = -math.cos(az)
        dirs.append((drow, dcol))

    vol = np.empty((nz, grid.ny, grid.nx), dtype=np.float32)
    for z in range(nz):
        off_px = (depths_abs[z] / tan_dip) / grid.res_m
        if not dirs or off_px < 1e-3:
            layer = s2d
        else:
            layer = None
            for (drow, dcol) in dirs:
                shifted = nd_shift(s2d, (drow * off_px, dcol * off_px), order=1,
                                   mode="constant", cval=0.0, prefilter=False)
                layer = shifted if layer is None else np.maximum(layer, shifted)
        vol[z] = tail[z] * layer
    return np.clip(vol, 0.0, 1.0).astype(np.float32)


def build_surface_layers(es) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
    """从 EvidenceSet 取 2D 地表证据层 + 覆盖掩码。值 [0,1]，NaN=无覆盖。"""
    layers: Dict[str, np.ndarray] = {}
    coverage: Dict[str, np.ndarray] = {}
    for name in ("alteration", "structure", "deformation", "geochem", "slowvars"):
        arr = getattr(es, name, None)
        if arr is not None and np.isfinite(arr).any():
            layers[name] = arr.astype(np.float32)
            coverage[name] = np.isfinite(arr)
    return layers, coverage
