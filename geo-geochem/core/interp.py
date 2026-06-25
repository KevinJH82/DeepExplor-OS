"""点位插值 —— 把化探采样点(lon,lat,元素含量)用 IDW 插到 VoxelGrid 的水平网格 (ny,nx)。

设计：
- 点位经纬度先投到网格 UTM；用 cKDTree 取每个网格中心最近 K 个点做反距离加权。
- 远离所有采样点的网格（最近点距离 > max_dist_m）置 NaN——不外推臆造。
- 返回 (ny,nx) float32（无数据=NaN）+ 覆盖掩码。
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree


def _grid_centers_utm(grid):
    """返回网格各像元中心的 UTM (X,Y) 两个 (ny,nx) 数组。"""
    xs = grid.xmin + (np.arange(grid.nx) + 0.5) * grid.res_m
    ys = grid.ymax - (np.arange(grid.ny) + 0.5) * grid.res_m
    gx, gy = np.meshgrid(xs, ys)   # (ny,nx)
    return gx, gy


def interpolate_to_grid(lons, lats, values, grid,
                        power: float = 2.0, k: int = 12,
                        max_dist_m: Optional[float] = None
                        ) -> Tuple[np.ndarray, np.ndarray]:
    """
    lons/lats/values: 等长 1D 序列（同一元素的采样点）。
    返回 (interp(ny,nx) float32 NaN填空, coverage(ny,nx) bool)。
    max_dist_m 缺省取 3×网格分辨率与点平均间距的较大者，避免在无样区外推。
    """
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    ok = np.isfinite(lons) & np.isfinite(lats) & np.isfinite(values)
    lons, lats, values = lons[ok], lats[ok], values[ok]

    out = np.full(grid.shape2d, np.nan, dtype=np.float32)
    cov = np.zeros(grid.shape2d, dtype=bool)
    if values.size == 0:
        return out, cov

    tr = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    px, py = tr.transform(lons, lats)
    pts = np.column_stack([px, py])

    if max_dist_m is None:
        # 点平均间距估计（按点密度）：sqrt(面积/点数)
        area = max(grid.xmax - grid.xmin, 1.0) * max(grid.ymax - grid.ymin, 1.0)
        mean_spacing = np.sqrt(area / max(values.size, 1))
        max_dist_m = float(max(3.0 * grid.res_m, 1.5 * mean_spacing))

    tree = cKDTree(pts)
    gx, gy = _grid_centers_utm(grid)
    q = np.column_stack([gx.ravel(), gy.ravel()])
    kk = int(min(max(1, k), values.size))
    dist, idx = tree.query(q, k=kk)
    if kk == 1:
        dist = dist[:, None]
        idx = idx[:, None]

    flat = np.full(q.shape[0], np.nan, dtype=np.float64)
    nearest = dist[:, 0]
    within = nearest <= max_dist_m
    # 精确命中（dist≈0）直接取该点值，避免除零
    exact = dist[:, 0] < 1e-6
    flat[exact] = values[idx[exact, 0]]

    use = within & ~exact
    if use.any():
        d = dist[use]
        w = 1.0 / np.power(np.maximum(d, 1e-6), power)
        # 超过 max_dist 的邻点不参与
        w[d > max_dist_m] = 0.0
        wsum = w.sum(axis=1)
        vals = values[idx[use]]
        num = (w * vals).sum(axis=1)
        good = wsum > 0
        res = np.full(use.sum(), np.nan)
        res[good] = num[good] / wsum[good]
        flat[use] = res

    out = flat.reshape(grid.shape2d).astype(np.float32)
    cov = np.isfinite(out)
    return out, cov
