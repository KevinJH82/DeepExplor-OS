"""gather_evidence — 经 commons broker 拉齐上游 2D 证据，对齐到体元网格水平面。

硬约束遵循：
- 显式传入上游真实根路径（不依赖 broker 的 DEFAULT_*）。
- 数值栅格从 manifest 解析（composites.score_tif / results[].index_tif），不是 figures 的 PNG。
- 任何上游缺失只降级、不报错；provenance 如实记录用到/缺失的来源。
"""

from __future__ import annotations

import os
import sys
import json
import glob
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


def _import_commons():
    """把仓库根加入 sys.path 以导入 commons.* broker（兼容 /opt/Project 与 /opt 两种部署）。"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
    for repo in (here, "/opt/deepexplor-services"):
        if repo and repo not in sys.path:
            sys.path.insert(0, repo)


@dataclass
class EvidenceSet:
    """对齐到网格水平面 (ny,nx) 的 2D 证据 + 元信息。值约定 [0,1]，越高越有利。"""
    alteration: Optional[np.ndarray] = None          # 蚀变综合评分
    structure: Optional[np.ndarray] = None           # 断裂邻近/构造有利度
    deformation: Optional[np.ndarray] = None         # 地表形变(常缺)
    geochem: Optional[np.ndarray] = None             # 地球化学多元素组合异常(geo-geochem)
    slowvars: Optional[np.ndarray] = None            # 七慢变量 Δ 判别式反向有利度(geo-7slow)
    depth_map: Optional[np.ndarray] = None           # 经验深度(米，可选精修)
    strikes: List[float] = field(default_factory=list)        # 主断裂走向
    targets: List[Dict] = field(default_factory=list)         # 预测靶点(仅一致性校验)
    deposit_type: Optional[str] = None               # 来自 geo-analyser manifest
    deposit_type_trusted: bool = False               # deposit_type 与用户矿种是否一致(可用于定族)
    tectonic_setting: Optional[str] = None           # 构造背景(金/银族自动切换用)
    known_deposits: List[Dict] = field(default_factory=list)   # 真实已知矿点(方向四标签;≠targets预测)
    known_barren: List[Dict] = field(default_factory=list)     # 钻孔确认无矿(真负样本;方向四P4闭环)
    provenance: Dict[str, dict] = field(default_factory=dict)  # 各源用到/缺失

    def available_layers(self) -> List[str]:
        out = []
        for name in ("alteration", "structure", "deformation", "geochem", "slowvars"):
            if getattr(self, name) is not None and np.isfinite(getattr(self, name)).any():
                out.append(name)
        return out


def _norm01(arr: np.ndarray, lo_pct: float = 2.0, hi_pct: float = 98.0) -> np.ndarray:
    """鲁棒 min-max 归一化到 [0,1]（用百分位裁尾），保留 NaN。"""
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    lo, hi = np.percentile(finite, [lo_pct, hi_pct])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
        if hi <= lo:
            return np.where(np.isfinite(arr), 0.0, np.nan).astype(np.float32)
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


# ─────────────────────────────────────────────
# geo-analyser：蚀变综合评分（地表证据主层）
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# 上游成果择一：最佳 AOI 匹配（重叠度优先→矿种一致→最新），替代"任意相交取最新"
# ─────────────────────────────────────────────
def _entry_bbox(e: Dict):
    return e.get("bbox") or e.get("aoi_bbox")


def _overlap_quality(cand, target) -> Tuple[float, float]:
    """返回 (target_coverage, iou)：目标被候选覆盖的面积比 + 交并比。无效/不相交→(0,0)。"""
    if not cand or not target or len(cand) < 4 or len(target) < 4:
        return 0.0, 0.0
    ix0, iy0 = max(cand[0], target[0]), max(cand[1], target[1])
    ix1, iy1 = min(cand[2], target[2]), min(cand[3], target[3])
    iw, ih = ix1 - ix0, iy1 - iy0
    if iw <= 0 or ih <= 0:
        return 0.0, 0.0
    inter = iw * ih
    ta = max((target[2] - target[0]) * (target[3] - target[1]), 1e-12)
    ca = max((cand[2] - cand[0]) * (cand[3] - cand[1]), 1e-12)
    coverage = inter / ta
    iou = inter / (ta + ca - inter)
    return float(coverage), float(iou)


def _entry_mineral_hint(e: Dict) -> str:
    return (e.get("deposit_type") or (e.get("model_stats") or {}).get("mineral_type")
            or e.get("mineral") or "")


def select_best_entry(entries: List[Dict], target_bbox, mineral: Optional[str] = None):
    """从相交候选中按 (重叠覆盖→IoU→矿种一致→created_at最新) 择一。
    返回 (entry, sel_info)；entries 空→(None, {})。重叠按 0.01 分桶，覆盖相当时由矿种+最新决定。"""
    if not entries:
        return None, {}
    scored = []
    for e in entries:
        cov, iou = _overlap_quality(_entry_bbox(e), target_bbox)
        mm = 1 if (mineral and _commodity_consistent(mineral, _entry_mineral_hint(e))) else 0
        scored.append((round(cov, 2), round(iou, 2), mm, e.get("created_at", ""), e))
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]), reverse=True)
    best = scored[0]
    info = {"n_candidates": len(entries), "selected_coverage": best[0],
            "selected_iou": best[1], "selected_mineral_match": bool(best[2])}
    return best[4], info


def _load_alteration(bbox, grid, root, prov, mineral: Optional[str] = None) -> Tuple[Optional[np.ndarray], Optional[str]]:
    _import_commons()
    try:
        from commons.analyser_broker import find_alteration_for_bbox
    except Exception as e:
        prov['alteration'] = {"status": "broker_import_failed", "error": str(e)}
        return None, None

    entries = find_alteration_for_bbox(tuple(bbox), root)
    if not entries:
        prov['alteration'] = {"status": "missing", "root": root}
        return None, None

    entry, sel = select_best_entry(entries, bbox, mineral)  # 最佳 AOI 匹配
    prov['alteration_selection'] = sel
    run_dir = entry.get("run_dir")
    deposit_type = entry.get("deposit_type") or None
    manifest = {}
    mp = os.path.join(run_dir, "manifest.json")
    try:
        with open(mp, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        prov['alteration'] = {"status": "manifest_unreadable", "run_dir": run_dir, "error": str(e)}
        return None, deposit_type

    # 优先 composites.<sensor>.score_tif；回退 results[] 最高优先级的 index_tif
    score_rel = None
    for sensor, comp in (manifest.get("composites") or {}).items():
        if comp.get("score_tif"):
            score_rel = comp["score_tif"]
            break
    used = "composite"
    if score_rel is None:
        results = sorted(manifest.get("results", []) or [],
                         key=lambda r: r.get("priority", 9))
        for r in results:
            if r.get("index_tif"):
                score_rel = r["index_tif"]
                used = f"single:{r.get('mineral')}"
                break

    if not score_rel:
        prov['alteration'] = {"status": "no_raster", "run_dir": run_dir, "deposit_type": deposit_type}
        return None, deposit_type

    tif = os.path.join(run_dir, score_rel)
    if not os.path.exists(tif):
        prov['alteration'] = {"status": "raster_missing", "path": tif}
        return None, deposit_type

    arr = _norm01(grid.reproject_to_grid(tif))
    prov['alteration'] = {"status": "ok", "source": "geo-analyser", "raster": score_rel,
                          "kind": used, "deposit_type": deposit_type,
                          "run_dir": run_dir, "created_at": entry.get("created_at")}
    return arr, deposit_type


# ─────────────────────────────────────────────
# geo-geochem：多元素组合异常（地表化探证据层）
# ─────────────────────────────────────────────
def _load_geochem(bbox, grid, root, prov, mineral: Optional[str] = None) -> Optional[np.ndarray]:
    """取 geo-geochem 多元素组合异常 GeoTIFF、对齐到网格水平面。无则降级返回 None。"""
    _import_commons()
    try:
        from commons.geochem_broker import find_geochem_for_bbox, get_product_path
    except Exception as e:
        prov['geochem'] = {"status": "broker_import_failed", "error": str(e)}
        return None

    entries = find_geochem_for_bbox(tuple(bbox), root)
    if not entries:
        prov['geochem'] = {"status": "missing", "root": root}
        return None

    entry, sel = select_best_entry(entries, bbox, mineral)  # 最佳 AOI 匹配
    prov['geochem_selection'] = sel
    run_dir = entry.get("geochem_dir")
    # 组合异常 GeoTIFF（仅 geochem 算出实测/公开异常时存在；降级先验态无此产物）
    tif = get_product_path(entry, "multi_element_factor")
    if not tif:
        prov['geochem'] = {"status": "no_raster", "geochem_dir": run_dir,
                           "note": "geochem 无组合异常栅格(多为无实测点位的背景先验降级态)"}
        return None

    arr = _norm01(grid.reproject_to_grid(tif))
    prov['geochem'] = {"status": "ok", "source": "geo-geochem",
                       "raster": "multi_element_factor",
                       "mineral_type": (entry.get("model_stats") or {}).get("mineral_type"),
                       "geochem_dir": run_dir, "created_at": entry.get("created_at")}
    return arr


# ─────────────────────────────────────────────
# geo-insar：地表形变证据层（SBAS 速率活动度，第 6 层）
# ─────────────────────────────────────────────
def _load_deformation(bbox, grid, root, prov, mineral: Optional[str] = None) -> Optional[np.ndarray]:
    """取 geo-insar AOI 级形变证据 GeoTIFF（|velocity| 镶嵌）、对齐到网格水平面。无则降级返回 None。"""
    _import_commons()
    try:
        from commons.insar_broker import find_insar_for_bbox, get_product_path
    except Exception as e:
        prov['deformation'] = {"status": "broker_import_failed", "error": str(e)}
        return None

    entries = find_insar_for_bbox(tuple(bbox), root)
    if not entries:
        prov['deformation'] = {"status": "missing", "root": root}
        return None

    entry, sel = select_best_entry(entries, bbox, mineral)  # 最佳 AOI 匹配
    prov['deformation_selection'] = sel
    insar_dir = entry.get("insar_dir")
    # 形变证据 GeoTIFF（仅 AOI 跑过 SBAS 反演时存在；否则降级无此产物）
    tif = get_product_path(entry, "deformation_evidence")
    if not tif:
        prov['deformation'] = {"status": "no_raster", "insar_dir": insar_dir,
                               "note": "geo-insar 无形变证据栅格(该 AOI 未做 SBAS 时序反演)"}
        return None

    arr = _norm01(grid.reproject_to_grid(tif))
    prov['deformation'] = {"status": "ok", "source": "geo-insar",
                           "raster": "deformation_evidence",
                           "stats": entry.get("stats", {}),
                           "insar_dir": insar_dir, "created_at": entry.get("created_at")}
    return arr


# ─────────────────────────────────────────────
# geo-7slow：七慢变量 Δ 判别式（机制综合证据层）
# ─────────────────────────────────────────────
def _load_slowvars(bbox, grid, root, prov, mineral: Optional[str] = None) -> Optional[np.ndarray]:
    """Load geo-7slow delta_discriminant as a favorability layer.

    geo-7slow uses lower Δ as more favorable, so model3d consumes the inverted
    normalized score: 1 - norm01(delta).
    """
    _import_commons()
    try:
        from commons.slowvars_broker import find_slowvars_for_bbox, get_product_path, load_target_zones
    except Exception as e:
        prov['slowvars'] = {"status": "broker_import_failed", "error": str(e)}
        return None

    entries = find_slowvars_for_bbox(tuple(bbox), root)
    if not entries:
        prov['slowvars'] = {"status": "missing", "root": root}
        return None

    entry, sel = select_best_entry(entries, bbox, mineral)
    prov['slowvars_selection'] = sel
    tif = get_product_path(entry, "delta_discriminant")
    if not tif:
        prov['slowvars'] = {"status": "no_raster", "slowvars_dir": entry.get("slowvars_dir")}
        return None

    delta = grid.reproject_to_grid(tif)
    arr = 1.0 - _norm01(delta)
    prov['slowvars'] = {
        "status": "ok",
        "source": "geo-7slow",
        "raster": "delta_discriminant",
        "run_id": entry.get("run_id"),
        "slowvars_dir": entry.get("slowvars_dir"),
        "target_count": (entry.get("model_stats") or {}).get("target_count"),
        "target_area_km2": (entry.get("model_stats") or {}).get("target_area_km2"),
        "targets": load_target_zones(entry)[:20],
    }
    return arr


# ─────────────────────────────────────────────
# geo-stru：断裂邻近/构造有利度（线性体优先，回退曲率代理）
# ─────────────────────────────────────────────
def _load_structure(bbox, grid, root, prov, mineral: Optional[str] = None) -> Tuple[Optional[np.ndarray], List[float]]:
    _import_commons()
    try:
        from commons.structural_broker import find_structural_for_bbox, get_product_path
    except Exception as e:
        prov['structure'] = {"status": "broker_import_failed", "error": str(e)}
        return None, []

    entries = find_structural_for_bbox(tuple(bbox), root)
    if not entries:
        prov['structure'] = {"status": "missing", "root": root}
        return None, []

    entry, sel = select_best_entry(entries, bbox, mineral)  # 最佳 AOI 匹配（构造无矿种线索→中性）
    prov['structure_selection'] = sel
    strikes = list((entry.get("structural_stats") or {}).get("dominant_strikes_deg") or [])

    # 1) 距断裂距离 → 邻近度 exp(-d/scale)
    dist_p = get_product_path(entry, "distance_to_lineament")
    if dist_p:
        d = grid.reproject_to_grid(dist_p)
        scale = 500.0  # 米
        prox = np.exp(-np.abs(d) / scale).astype(np.float32)
        prox = np.where(np.isfinite(d), prox, np.nan)
        prov['structure'] = {"status": "ok", "source": "geo-stru", "kind": "distance_to_lineament",
                             "run_id": entry.get("run_id")}
        return _norm01(prox), strikes

    # 2) 线性体密度
    dens_p = get_product_path(entry, "lineament_density")
    if dens_p:
        dens = grid.reproject_to_grid(dens_p)
        prov['structure'] = {"status": "ok", "source": "geo-stru", "kind": "lineament_density",
                             "run_id": entry.get("run_id")}
        return _norm01(dens), strikes

    # 3) 回退：曲率代理（|curvature| 高 ≈ 构造不连续）—— 弱证据，标记 weak
    curv_p = get_product_path(entry, "curvature")
    if curv_p:
        curv = grid.reproject_to_grid(curv_p)
        proxy = _norm01(np.abs(curv))
        prov['structure'] = {"status": "weak", "source": "geo-stru", "kind": "curvature_proxy",
                             "note": "该AOI无线性体产物，用曲率作弱构造代理", "run_id": entry.get("run_id")}
        return proxy, strikes

    prov['structure'] = {"status": "no_raster", "run_id": entry.get("run_id")}
    return None, strikes


# ─────────────────────────────────────────────
# geo-exploration：经验深度 + 预测靶点（均可选）
# ─────────────────────────────────────────────
def _load_exploration(bbox, grid, root, mineral, prov) -> Tuple[Optional[np.ndarray], List[Dict]]:
    _import_commons()
    try:
        from commons.exploration_broker import find_exploration_for_bbox
    except Exception as e:
        prov['exploration'] = {"status": "broker_import_failed", "error": str(e)}
        return None, []

    entries = find_exploration_for_bbox(tuple(bbox), root)
    if not entries:
        prov['exploration'] = {"status": "missing", "root": root}
        return None, []

    entry, sel = select_best_entry(entries, bbox, mineral)  # 最佳 AOI 匹配
    prov['exploration_selection'] = sel
    run_dir = entry.get("run_dir")
    targets = entry.get("prospecting_targets", []) or []

    # 经验深度：从 <矿种>_Result.mat 读 depth_map（可选精修，非必需）
    depth_arr = None
    try:
        from scipy.io import loadmat
        mats = glob.glob(os.path.join(run_dir, "*_Result.mat"))
        if mats:
            md = loadmat(mats[0])
            if "depth_map" in md:
                dm = np.asarray(md["depth_map"], dtype=np.float32)
                # .mat 是该 run 自身网格的 2D，无法直接对齐本网格(无地理参考)；
                # P1 仅取其统计量(中位深度)作标量先验提示，不做逐像元对齐。
                prov['exploration'] = {"status": "ok", "source": "geo-exploration",
                                       "depth_map_median_km": float(np.nanmedian(dm)),
                                       "n_targets": len(targets), "run_dir": run_dir,
                                       "note": "depth_map 无地理参考，仅作标量提示，不逐像元对齐"}
                depth_arr = None  # P1 不逐像元用，知识深度带为主
                return depth_arr, targets
    except Exception as e:
        logger.info(f"exploration depth_map 读取跳过: {e}")

    prov['exploration'] = {"status": "ok" if targets else "no_depth",
                           "source": "geo-exploration", "n_targets": len(targets), "run_dir": run_dir}
    return depth_arr, targets


# ─────────────────────────────────────────────
# 构造背景（金/银族自动切换用）：尽力从 geo-analyser 矿床类型元信息取
# ─────────────────────────────────────────────
def _deposit_meta(deposit_type: Optional[str], analyser_root: str) -> Tuple[Optional[str], Optional[str]]:
    """返回 (tectonic_setting, commodity)；取自 geo-analyser 的矿床类型知识库。"""
    if not deposit_type:
        return None, None
    try:
        ga_dir = os.path.dirname(analyser_root.rstrip("/"))   # .../geo-analyser
        if ga_dir not in sys.path:
            sys.path.insert(0, ga_dir)
        from alteration_db import get_deposit_type_meta  # type: ignore
        meta = get_deposit_type_meta(deposit_type)
        if meta:
            return meta.get("tectonic_setting"), meta.get("commodity")
    except Exception:
        return None, None
    return None, None


def _commodity_consistent(user_mineral: str, dt_commodity: Optional[str]) -> bool:
    """用户矿种与 deposit_type 的矿种是否一致（共享任一非空字符即视为一致，覆盖'铜钼'∋'钼'）。"""
    if not dt_commodity:
        return True   # 拿不到矿种信息时不否决 deposit_type
    a = set((user_mineral or "").strip())
    b = set(dt_commodity.strip())
    a.discard("矿"); b.discard("矿")
    return bool(a & b)


def gather_evidence(bbox: List[float], mineral: str, grid, roots: Dict[str, str],
                    known_deposits_upload: Optional[str] = None,
                    deposits_cache: Optional[str] = None,
                    drill_feedback_path: Optional[str] = None) -> EvidenceSet:
    """主入口：返回对齐到 grid 的 EvidenceSet（含 provenance 与降级信息）。"""
    prov: Dict[str, dict] = {}

    alteration, deposit_type = _load_alteration(bbox, grid, roots["analyser"], prov, mineral)
    structure, strikes = _load_structure(bbox, grid, roots["stru"], prov, mineral)
    depth_map, targets = _load_exploration(bbox, grid, roots["exploration"], mineral, prov)
    geochem = _load_geochem(bbox, grid, roots.get("geochem", ""), prov, mineral) if roots.get("geochem") else None
    deformation = _load_deformation(bbox, grid, roots.get("insar", ""), prov, mineral) if roots.get("insar") else None
    slowvars = _load_slowvars(bbox, grid, roots.get("slowvars", ""), prov, mineral) if roots.get("slowvars") else None

    # 方向四：真实已知矿点标签（MRDS + 上传；绝不读预测靶点）
    from core import labels
    known_deposits, label_prov = labels.load_known_deposits(
        bbox, mineral, roots, upload_path=known_deposits_upload, cache_dir=deposits_cache,
        local_lib_dir=(roots or {}).get("deposits_library"))
    # 方向四 P4：钻孔回灌（见矿→并入正样本；无矿→真负样本，闭环优化）
    drill_ore, known_barren, drill_prov = labels.load_drill_feedback(bbox, drill_feedback_path)
    if drill_ore:
        have = {(round(p["lon"], 5), round(p["lat"], 5)) for p in known_deposits}
        for p in drill_ore:
            if (round(p["lon"], 5), round(p["lat"], 5)) not in have:
                known_deposits.append(p)
    prov["labels"] = label_prov
    prov["drill_feedback"] = drill_prov

    tect, dt_commodity = _deposit_meta(deposit_type, roots["analyser"])
    trusted = bool(deposit_type) and _commodity_consistent(mineral, dt_commodity)
    if deposit_type and not trusted:
        prov.setdefault("alteration", {})["deposit_type_commodity_mismatch"] = (
            f"AOI 蚀变矿床类型({deposit_type}/{dt_commodity}) 与用户矿种({mineral})不一致，定族改用矿种默认")

    es = EvidenceSet(
        alteration=alteration, structure=structure, deformation=deformation, geochem=geochem,
        slowvars=slowvars,
        depth_map=depth_map, strikes=strikes, targets=targets,
        deposit_type=deposit_type, deposit_type_trusted=trusted,
        tectonic_setting=tect, known_deposits=known_deposits, known_barren=known_barren,
        provenance=prov,
    )
    logger.info(f"gather_evidence: layers={es.available_layers()} deposit_type={deposit_type} "
                f"trusted={trusted} strikes={strikes[:3]} n_targets={len(targets)} "
                f"n_known_deposits={len(known_deposits)}")
    return es
