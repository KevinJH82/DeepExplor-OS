"""斜孔/轨迹设计（P2）—— 在 geo-model3d 有利度体上为每个孔口优化方位/倾角。

对每个 (方位 azimuth, 倾角 dip) 候选，从孔口沿射线在体内按步长采样 prospectivity，
求**截获有利度积分**；取积分最大者。竖直(dip=-90)始终是候选 → 斜孔不会比竖直差。
诚实边界：仅按有利度截获优化，不含钻探地质力学/成本约束（P4）。
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np
from pyproj import Transformer


def _sample_value(prosp: np.ndarray, x: np.ndarray, y: np.ndarray, depth_m: np.ndarray,
                  ux: float, uy: float, dep: float) -> float:
    """最近体元查 prospectivity（ux,uy=UTM, dep=深度米>0）。越界返回 NaN。"""
    nz, ny, nx = prosp.shape
    c = int(round((ux - x[0]) / (x[1] - x[0]))) if nx > 1 else 0
    r = int(round((uy - y[0]) / (y[1] - y[0]))) if ny > 1 else 0
    # depth_m 递减(负)；|depth| 递增
    ad = np.abs(depth_m)
    zi = int(np.argmin(np.abs(ad - dep)))
    if 0 <= r < ny and 0 <= c < nx and 0 <= zi < nz:
        v = prosp[zi, r, c]
        return float(v) if np.isfinite(v) else np.nan
    return np.nan


def optimize_trajectory(fav: Dict, collar_lon: float, collar_lat: float,
                        max_depth_m: float = None, allow_incline: bool = True,
                        dips: Tuple[float, ...] = (-90.0, -75.0, -60.0),
                        az_step: float = 30.0, step_m: float = None) -> Dict:
    """返回 {azimuth_deg, dip_deg, target_depth_m, intercepted_score, trajectory[{lon,lat,depth_m}]}。"""
    prosp = fav["prospectivity"]
    x = np.asarray(fav["x"], float); y = np.asarray(fav["y"], float)
    depth_m = np.asarray(fav["depth_m"], float)
    res_m = float(fav.get("res_m", 30.0)); epsg = int(fav.get("epsg", 4326))
    if max_depth_m is None:
        max_depth_m = float(abs(depth_m[-1]))
    if step_m is None:
        step_m = max(res_m, 25.0)

    to_utm = Transformer.from_crs(4326, epsg, always_xy=True)
    to_ll = Transformer.from_crs(epsg, 4326, always_xy=True)
    cx, cy = to_utm.transform(collar_lon, collar_lat)

    dip_list = list(dips) if allow_incline else [-90.0]
    az_list = [0.0] if not allow_incline else list(np.arange(0.0, 360.0, az_step))

    best = {"azimuth_deg": 0.0, "dip_deg": -90.0, "score": -1.0, "depth": 0.0}
    n_steps = int(max_depth_m / step_m) + 1
    for dip in dip_list:
        dip_r = math.radians(dip)            # 负=向下
        vert = math.sin(-dip_r)              # 每步垂直分量(>0 向下)
        horiz = math.cos(-dip_r)             # 每步水平分量
        az_candidates = [0.0] if abs(dip + 90.0) < 1e-6 else az_list  # 竖直无需扫方位
        for az in az_candidates:
            az_r = math.radians(az)          # 0=北, 顺时针
            dx = horiz * math.sin(az_r); dy = horiz * math.cos(az_r)
            acc = 0.0; last_good_dep = 0.0
            for s in range(1, n_steps + 1):
                ux = cx + dx * step_m * s
                uy = cy + dy * step_m * s
                dep = vert * step_m * s
                if dep > max_depth_m:
                    break
                v = _sample_value(prosp, x, y, depth_m, ux, uy, dep)
                if np.isfinite(v):
                    acc += v
                    if v >= 0.5:
                        last_good_dep = dep
            if acc > best["score"]:
                best = {"azimuth_deg": float(az), "dip_deg": float(dip),
                        "score": float(acc), "depth": float(last_good_dep or max_depth_m)}

    # 轨迹折线（孔口 → 终点）
    dip_r = math.radians(best["dip_deg"]); az_r = math.radians(best["azimuth_deg"])
    vert = math.sin(-dip_r); horiz = math.cos(-dip_r)
    dx = horiz * math.sin(az_r); dy = horiz * math.cos(az_r)
    total = best["depth"] / max(vert, 1e-6)         # 沿杆长度
    traj = []
    for s in np.linspace(0, total, 6):
        ux = cx + dx * s; uy = cy + dy * s; dep = vert * s
        lon, lat = to_ll.transform(ux, uy)
        traj.append({"lon": round(float(lon), 6), "lat": round(float(lat), 6),
                     "depth_m": int(round(dep))})
    return {"azimuth_deg": round(best["azimuth_deg"], 1), "dip_deg": round(best["dip_deg"], 1),
            "target_depth_m": int(round(best["depth"])),
            "intercepted_score": round(best["score"], 3), "trajectory": traj}
