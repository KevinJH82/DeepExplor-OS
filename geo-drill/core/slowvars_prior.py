"""geo-7slow 慢变量靶区作为布孔软先验 —— 用 target_zones 多边形(EPSG:4326)为落入
有利靶区的孔位加分并解释（主控慢变量 dominant_driver / 突变判别式 mean_delta）。

设计取向：软先验，不做硬预过滤。空靶区(平坦区)或弱靶区时退化为仅标注、不改排序，
避免误杀 geo-model3d 三维有利度的主峰。孔位与靶区同为 EPSG:4326，直接做点在多边形内判断。
"""
from __future__ import annotations

from typing import Dict, List, Optional


# geo-7slow 7 个慢变量驱动名 → 中文标签（用于孔位解释/报告）
DRIVER_LABELS_CN = {
    "stress_gradient": "构造应力梯度",
    "redox_gradient": "氧化还原梯度",
    "fluid_overpressure": "流体超压",
    "fault_activity": "断裂活动性",
    "cap_rock_pressure": "盖层封闭",
    "temp_gradient": "温度梯度",
    "chem_potential": "化学势",
}


def _point_in_ring(lon: float, lat: float, ring: List) -> bool:
    """射线法：点是否在单个环内（ring 为 [[lon,lat],...]）。"""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, geom: Dict) -> bool:
    """支持 Polygon / MultiPolygon，并扣除内环(洞)。"""
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    polys = coords if t == "MultiPolygon" else [coords]
    for poly in polys:
        if not poly or not poly[0]:
            continue
        if _point_in_ring(lon, lat, poly[0]):
            in_hole = any(_point_in_ring(lon, lat, poly[k]) for k in range(1, len(poly)))
            if not in_hole:
                return True
    return False


def _zone_for_point(lon, lat, feats: List[Dict]) -> Optional[Dict]:
    if lon is None or lat is None:
        return None
    for f in feats:
        if _point_in_polygon(lon, lat, f.get("geometry") or {}):
            return f
    return None


def _retag_priority(holes: List[Dict], value_key: str):
    if not holes:
        return
    vals = [float(h.get(value_key, h.get("value", 0.0)) or 0.0) for h in holes]
    hi, lo = max(vals), min(vals)
    for h, v in zip(holes, vals):
        t = (v - lo) / (hi - lo + 1e-9)
        h["priority"] = "A" if t >= 0.66 else ("B" if t >= 0.33 else "C")


def apply_slowvars(holes: List[Dict], slowvars: Optional[Dict],
                   weight: float = 0.25) -> Dict:
    """逐孔标注所属 geo-7slow 靶区属性，并按靶区置信度做温和软重排。

    标注（始终执行，纯解释零风险）：slowvars_in_target / slowvars_dominant_driver(_cn) /
    slowvars_mean_delta / slowvars_zone_rank / slowvars_confidence / slowvars_prior。
    软重排：综合价值 = value × (1 + weight × prior)，prior = 靶区置信度归一化到 [0,1]；
    重排后重排 rank / hole_id / 优先级。weight<=0、无 slowvars 或无孔落入靶区 → 仅标注不重排。
    原地修改 holes，返回统计 dict。
    """
    feats = (slowvars or {}).get("target_zones") or []
    confs = [float((f.get("properties") or {}).get("confidence") or 0.0) for f in feats]
    max_conf = max(confs) if confs else 0.0

    n_in = 0
    driver_hist: Dict[str, int] = {}
    for h in holes:
        zone = _zone_for_point(h.get("lon"), h.get("lat"), feats)
        if zone is None:
            h["slowvars_in_target"] = False
            h["slowvars_prior"] = 0.0
            continue
        p = zone.get("properties") or {}
        drv = p.get("dominant_driver")
        h["slowvars_in_target"] = True
        h["slowvars_zone_rank"] = p.get("rank")
        h["slowvars_dominant_driver"] = drv
        h["slowvars_dominant_driver_cn"] = DRIVER_LABELS_CN.get(drv, drv)
        h["slowvars_mean_delta"] = p.get("mean_delta")
        h["slowvars_confidence"] = p.get("confidence")
        prior = (float(p.get("confidence") or 0.0) / max_conf) if max_conf > 0 else 0.0
        h["slowvars_prior"] = round(prior, 4)
        n_in += 1
        if drv:
            driver_hist[drv] = driver_hist.get(drv, 0) + 1

    reranked = False
    if weight > 0 and n_in > 0:
        for h in holes:
            base = float(h.get("value", 0.0) or 0.0)
            h["value_slowvars"] = round(base * (1.0 + weight * float(h.get("slowvars_prior", 0.0))), 4)
        holes.sort(key=lambda x: x.get("value_slowvars", x.get("value", 0.0)), reverse=True)
        for i, h in enumerate(holes):
            h["rank"] = i + 1
            h["hole_id"] = f"AIDH-{i+1:03d}"
        _retag_priority(holes, "value_slowvars")
        reranked = True

    return {"n_in_target": n_in, "n_holes": len(holes), "reranked": reranked,
            "weight": weight, "n_target_zones": len(feats),
            "dominant_driver_hist": driver_hist}
