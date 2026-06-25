"""AI 辅助布孔 —— 在 geo-model3d 三维有利度体上选最优孔位 + 优先级。

价值函数 = prospectivity + explore_weight × uncertainty：
  - 有利度高（exploitation）：直接奔高成矿概率；
  - 不确定性高（exploration / value-of-information）：信息增益大，钻它能最大化"减少不确定"。
每个 (y,x) 取深度方向最优体元 → 价值 2D 图 → 最小孔距 NMS 选 top_n。
P1 直孔（方位/倾角=竖直）；斜孔/轨迹设计留 P2。
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
from pyproj import Transformer


def propose_holes(fav: Dict, top_n: int = 20, min_sep_m: float = 200.0,
                  explore_weight: float = 0.3) -> List[Dict]:
    """fav=load_model3d_favorability 的返回。返回计划孔列表（按价值降序，已 NMS 去重）。"""
    prosp = fav["prospectivity"]          # (nz,ny,nx)
    unc = fav["uncertainty"]
    depth_m = fav["depth_m"]              # (nz) 负
    x = fav["x"]; y = fav["y"]            # UTM
    res_m = float(fav.get("res_m", 30.0))
    epsg = int(fav.get("epsg", 4326))
    nz, ny, nx = prosp.shape

    # 价值体 = 有利度 + w×不确定性
    value = prosp + explore_weight * np.where(np.isfinite(unc), unc, 0.0)
    value = np.where(np.isfinite(value), value, -1.0)
    # 每个 (y,x) 深度方向最优体元
    best2d = np.nanmax(np.where(np.isfinite(prosp), value, -1.0), axis=0)
    zidx2d = np.argmax(np.where(np.isfinite(prosp), value, -1.0), axis=0)

    min_sep_cells = max(1, int(round(min_sep_m / max(res_m, 1e-6))))
    flat = best2d.copy()
    flat[~np.isfinite(flat)] = -1.0
    order = np.argsort(flat, axis=None)[::-1]
    occupied = np.zeros((ny, nx), dtype=bool)
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)

    holes: List[Dict] = []
    for idx in order:
        if len(holes) >= top_n:
            break
        r, c = divmod(int(idx), nx)
        if flat[r, c] <= 0:
            break
        if occupied[r, c]:
            continue
        z = int(zidx2d[r, c])
        lon, lat = tr.transform(float(x[c]), float(y[r]))
        sc = float(prosp[z, r, c]); uc = float(unc[z, r, c]) if np.isfinite(unc[z, r, c]) else 0.0
        holes.append({
            "rank": len(holes) + 1,
            "hole_id": f"AIDH-{len(holes)+1:03d}",
            "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            "target_depth_m": int(round(abs(float(depth_m[z])))),
            "azimuth_deg": 0.0, "dip_deg": -90.0,     # P1 直孔
            "score": round(sc, 4), "uncertainty": round(uc, 4),
            "value": round(float(best2d[r, c]), 4),
        })
        r0, r1 = max(0, r - min_sep_cells), min(ny, r + min_sep_cells + 1)
        c0, c1 = max(0, c - min_sep_cells), min(nx, c + min_sep_cells + 1)
        occupied[r0:r1, c0:c1] = True

    _tag_priority(holes)
    return holes


def propose_holes_at_targets(fav: Dict, top_n: int = 20,
                             min_sep_m: float = 200.0) -> List[Dict]:
    """在 geo-model3d 预测靶点(targets_3d)上直接布孔 —— 钻孔与 3D 靶点一一对应、视觉对齐。

    靶点已是 WGS84 lon/lat + depth_m + score;按 score 降序取,min_sep_m 内做轻量 NMS
    去重(同簇靶点只留最高分,避免重叠孔)。无靶点 → 返回 []，由调用方回退贪心/VOI。
    """
    targets = fav.get("targets") or []
    if not targets:
        return []

    def _score(t):
        return float(t.get("score", t.get("favorability", 0)) or 0)

    ts = sorted(targets, key=_score, reverse=True)
    min_sep_deg = max(min_sep_m, 1.0) / 111000.0     # 纬度方向近似;ROI 小,够用
    kept: List[Dict] = []
    for t in ts:
        try:
            lon = float(t.get("lon")); lat = float(t.get("lat"))
        except (TypeError, ValueError):
            continue
        if any(((lon - k["lon"]) ** 2 + (lat - k["lat"]) ** 2) ** 0.5 < min_sep_deg
               for k in kept):
            continue
        kept.append({"lon": lon, "lat": lat, "t": t})
        if len(kept) >= top_n:
            break

    holes: List[Dict] = []
    for k in kept:
        t = k["t"]; sc = _score(t)
        depth = t.get("depth_m") or t.get("depth") or 0
        unc = float(t.get("uncertainty", 0) or 0)
        holes.append({
            "rank": len(holes) + 1, "hole_id": f"AIDH-{len(holes)+1:03d}",
            "lon": round(k["lon"], 6), "lat": round(k["lat"], 6),
            "target_depth_m": int(round(abs(float(depth)))) if depth else None,
            "azimuth_deg": 0.0, "dip_deg": -90.0,
            "score": round(sc, 4), "uncertainty": round(unc, 4),
            "value": round(sc, 4),
        })
    _tag_priority(holes)
    return holes


def _tag_priority(holes: List[Dict]):
    if not holes:
        return
    vals = [h["value"] for h in holes]
    hi, lo = max(vals), min(vals)
    for h in holes:
        t = (h["value"] - lo) / (hi - lo + 1e-9)
        h["priority"] = "A" if t >= 0.66 else ("B" if t >= 0.33 else "C")


def propose_holes_voi(fav: Dict, top_n: int = 20, min_sep_m: float = 200.0,
                      tau: float = 0.5, beta: float = 0.6, info_radius_m: float = None,
                      allow_incline: bool = True) -> List[Dict]:
    """VOI 期望信息增益布孔（P2）。

    价值 = prosp_best(exploitation) + beta×info_gain，
    info_gain = unc_best × boundary(prosp_best)，boundary 为以决策阈值 tau 为中心的高斯——
    在"可能翻转见矿/不见矿判断"处信息增益最大（高确定区低）。
    序贯贪心：每选一孔，在 info_radius 内对价值场做高斯软衰减（该孔已告知邻域，避免冗余近邻孔）。
    诚实：用 model3d uncertainty 作方差代理 + 决策边界加权，是信息增益启发式，非完整贝叶斯序贯最优设计。
    """
    prosp = fav["prospectivity"]; unc = fav["uncertainty"]
    depth_m = fav["depth_m"]; x = fav["x"]; y = fav["y"]
    res_m = float(fav.get("res_m", 30.0)); epsg = int(fav.get("epsg", 4326))
    nz, ny, nx = prosp.shape
    if info_radius_m is None:
        info_radius_m = 1.8 * min_sep_m

    pb = np.nanmax(np.where(np.isfinite(prosp), prosp, np.nan), axis=0)        # (ny,nx) 最优有利度
    zidx = np.argmax(np.where(np.isfinite(prosp), prosp, -1.0), axis=0)
    ub = np.take_along_axis(np.where(np.isfinite(unc), unc, 0.0), zidx[None], axis=0)[0]
    valid = np.isfinite(pb)
    boundary = np.exp(-((pb - tau) ** 2) / (2 * (0.18 ** 2)))                  # 决策边界高斯
    info_gain = np.where(valid, ub * boundary, 0.0)
    value = np.where(valid, pb + beta * info_gain, -1.0).astype(np.float64)

    # 网格行列 → 米坐标，用于信息半径衰减
    rr, cc = np.mgrid[0:ny, 0:nx]
    sig_cells = max(info_radius_m / max(res_m, 1e-6), 1.0)
    min_sep_cells = max(1, int(round(min_sep_m / max(res_m, 1e-6))))
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)

    holes: List[Dict] = []
    vfield = value.copy()
    for _ in range(top_n):
        idx = int(np.argmax(vfield))
        r, c = divmod(idx, nx)
        if vfield[r, c] <= 0 or not valid[r, c]:
            break
        z = int(zidx[r, c])
        lon, lat = tr.transform(float(x[c]), float(y[r]))
        holes.append({
            "rank": len(holes) + 1, "hole_id": f"AIDH-{len(holes)+1:03d}",
            "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            "target_depth_m": int(round(abs(float(depth_m[z])))),
            "azimuth_deg": 0.0, "dip_deg": -90.0,
            "score": round(float(prosp[z, r, c]), 4),
            "uncertainty": round(float(ub[r, c]), 4),
            "info_gain": round(float(info_gain[r, c]), 4),
            "value": round(float(value[r, c]), 4),
        })
        # 序贯软衰减：信息半径内价值乘 (1 - exp(-d²/2σ²))；硬下限内直接清零
        d2 = (rr - r) ** 2 + (cc - c) ** 2
        decay = 1.0 - np.exp(-d2 / (2.0 * sig_cells ** 2))
        vfield = vfield * decay
        vfield[d2 <= min_sep_cells ** 2] = -1.0

    _tag_priority(holes)
    return holes
