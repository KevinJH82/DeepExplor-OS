"""取 geo-model3d 三维有利度体 —— 布孔的依据。

经 commons/model3d_broker 发现本 AOI 最新三维成矿预测产物，读 prospectivity_volume.nc
(prospectivity + uncertainty 体 + UTM 坐标 + epsg)。无产物 → 返回 None（上层拒绝布孔，不臆造）。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, Optional

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


def _import_commons():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for repo in (here, "/opt/deepexplor-services"):
        if repo and repo not in sys.path:
            sys.path.insert(0, repo)


def _commodity_consistent(mineral: str, hint: str) -> bool:
    """矿种是否一致：字符集相交（覆盖'铜钼'∋'铜'）。拿不到线索时不否决。"""
    if not hint:
        return True
    a = set((mineral or "").strip()); a.discard("矿")
    b = set(hint); b.discard("矿")
    return bool(a & b) if a else True


def load_model3d_favorability(bbox, roots: Dict[str, str], mineral: str = None) -> Optional[Dict]:
    """返回 {prospectivity(nz,ny,nx), uncertainty(nz,ny,nx), x(nx), y(ny), depth_m(nz),
    epsg, crs, res_m, run_id, model3d_dir, targets} 或 None（无产物）。"""
    _import_commons()
    try:
        from commons.model3d_broker import find_model3d_for_bbox, get_product_path
    except Exception as e:
        logger.info(f"model3d_broker 不可用: {e}")
        return None

    entries = find_model3d_for_bbox(tuple(bbox), roots.get("model3d", ""))
    if not entries:
        return None

    # 最佳 model3d 匹配（与 geo-model3d 选取一致）：bbox 覆盖度 → 矿种一致 → 最新(run_id)
    def _cov(e):
        a = e.get("aoi_bbox")
        if not a or len(a) < 4:
            return 0.0
        ix0, iy0 = max(a[0], bbox[0]), max(a[1], bbox[1])
        ix1, iy1 = min(a[2], bbox[2]), min(a[3], bbox[3])
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        ta = max((bbox[2]-bbox[0])*(bbox[3]-bbox[1]), 1e-12)
        return (ix1-ix0)*(iy1-iy0)/ta

    def _mineral_hint(e):
        ms = e.get("model_stats", {}) or {}
        return ms.get("mineral_type") or ms.get("deposit_type") or ""

    def _key(e):
        mm = 1 if (mineral and _commodity_consistent(mineral, _mineral_hint(e))) else 0
        return (round(_cov(e), 2), mm, e.get("run_id", ""))   # 覆盖度→矿种一致→最新(时间戳run_id)

    entries.sort(key=_key, reverse=True)
    entry = entries[0]
    sel_cov = round(_cov(entry), 3)
    sel_mm = bool(mineral and _commodity_consistent(mineral, _mineral_hint(entry)))
    logger.info(f"选用 model3d: aoi={entry.get('aoi_name')} run={entry.get('run_id')} "
                f"覆盖度={sel_cov} 矿种一致={sel_mm} 候选数={len(entries)}")

    nc = get_product_path(entry, "prospectivity_volume_nc")
    if not nc:
        logger.info("model3d 命中但无 prospectivity_volume_nc")
        return None
    try:
        import xarray as xr
        ds = xr.open_dataset(nc)
        prosp = np.asarray(ds["prospectivity"].values, dtype=np.float32)   # (z,y,x)
        unc = np.asarray(ds["uncertainty"].values, dtype=np.float32)
        x = np.asarray(ds["x"].values, dtype=np.float64)                   # (nx) UTM
        y = np.asarray(ds["y"].values, dtype=np.float64)                   # (ny) UTM
        depth_m = np.asarray(ds["depth_m"].values, dtype=np.float64)       # (nz) 负=地表下
        epsg = int(ds.attrs.get("epsg", 4326))
        crs = ds.attrs.get("crs", f"EPSG:{epsg}")
        res_m = float(ds.attrs.get("res_m", 30.0))
        ds.close()
    except Exception as e:
        logger.info(f"读取 prospectivity_volume 失败: {e}")
        return None

    # 顺带取 targets_3d（如有，供参考/对照）
    targets = []
    tp = get_product_path(entry, "targets_3d")
    if tp:
        try:
            import json
            targets = (json.load(open(tp, "r", encoding="utf-8")) or {}).get("targets", [])
        except Exception:
            pass

    return {"prospectivity": prosp, "uncertainty": unc, "x": x, "y": y, "depth_m": depth_m,
            "epsg": epsg, "crs": crs, "res_m": res_m,
            "run_id": entry.get("run_id"), "model3d_dir": entry.get("model3d_dir"),
            "aoi_name": entry.get("aoi_name"), "targets": targets,
            "deposit_type": (entry.get("model_stats", {}) or {}).get("deposit_type"),
            "coverage": sel_cov, "mineral_match": sel_mm,
            # 决策轨迹血缘：携带上游 model3d 的 trace_id，供 drill 继承（闭环金标签同源）
            "trace_id": entry.get("trace_id"),
            "linked_trace_ids": entry.get("linked_trace_ids", [])}


def _bbox_coverage(e, bbox) -> float:
    """候选 AOI 的 bbox 对目标 bbox 的覆盖度（交/目标面积）。无几何→0。"""
    a = e.get("aoi_bbox")
    if not a or len(a) < 4:
        return 0.0
    ix0, iy0 = max(a[0], bbox[0]), max(a[1], bbox[1])
    ix1, iy1 = min(a[2], bbox[2]), min(a[3], bbox[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    ta = max((bbox[2]-bbox[0])*(bbox[3]-bbox[1]), 1e-12)
    return (ix1-ix0)*(iy1-iy0)/ta


def load_slowvars_targets(bbox, roots: Dict[str, str], mineral: str = None,
                          trace_id: str = None) -> Optional[Dict]:
    """取 geo-7slow 慢变量靶区（target_zones.geojson, EPSG:4326）+ 逐靶属性，供布孔做软先验/排序解释。

    经 commons/slowvars_broker 按 bbox 发现本 AOI 最新慢变量产物，选取同 model3d（覆盖度→矿种一致→最新）。
    返回 {run_id, aoi_name, slowvars_dir, target_zones(features), model_stats, target_count,
    target_area_km2, deposit_type, trace_id} 或 None（broker 不可用/无产物）。
    有产物但平坦区无靶区 → 仍返回，target_zones=[]（标识已查、避免上层误判为缺数据）。
    """
    _import_commons()
    try:
        from commons.slowvars_broker import find_slowvars_for_bbox, load_target_zones
    except Exception as e:
        logger.info(f"slowvars_broker 不可用: {e}")
        return None

    try:
        entries = find_slowvars_for_bbox(tuple(bbox), roots.get("slowvars", ""), trace_id=trace_id)
    except Exception as e:
        logger.info(f"slowvars 查询失败: {e}")
        return None
    if not entries:
        return None

    def _hint(e):
        ms = e.get("model_stats", {}) or {}
        return ms.get("deposit_type") or ms.get("mineral") or ""

    def _key(e):
        mm = 1 if (mineral and _commodity_consistent(mineral, _hint(e))) else 0
        return (round(_bbox_coverage(e, bbox), 2), mm, e.get("run_id", ""))

    entries.sort(key=_key, reverse=True)
    entry = entries[0]
    feats = load_target_zones(entry) or []
    ms = entry.get("model_stats", {}) or {}
    logger.info(f"选用 slowvars: aoi={entry.get('aoi_name')} run={entry.get('run_id')} "
                f"靶区={ms.get('target_count')} 候选数={len(entries)}")
    return {"run_id": entry.get("run_id"), "aoi_name": entry.get("aoi_name"),
            "slowvars_dir": entry.get("slowvars_dir"), "target_zones": feats,
            "model_stats": ms, "target_count": ms.get("target_count"),
            "target_area_km2": ms.get("target_area_km2"),
            "deposit_type": ms.get("deposit_type"), "trace_id": entry.get("trace_id")}
