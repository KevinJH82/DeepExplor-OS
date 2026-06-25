"""汇聚化探证据 —— 三路取数、缺失只降级不报错：

1. 用户上传 ICP-MS/XRF 点位 CSV（lon,lat,<元素...>）—— 真实异常主数据；
2. data-colle `geochem_thresholds`（datacolle_broker 已解析）—— 背景/异常下限先验；
3. prospector mineral_kb 的矿种 key_elements —— 元素筛选/组合先验。

无上传点位时退化为 status='prior_only'（仅给阈值先验，不臆造异常）。
"""

from __future__ import annotations

import os
import sys
import re
from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.interp import interpolate_to_grid
from utils.logger import get_logger

logger = get_logger(__name__)

# 常见元素列名 → 标准符号（容错大小写/含单位后缀如 Cu_ppm）
_ELEMENT_SYMBOLS = {
    "ag","al","as","au","b","ba","be","bi","br","ca","cd","ce","co","cr","cs","cu",
    "f","fe","ga","hg","i","k","la","li","mg","mn","mo","na","nb","ni","p","pb","pd",
    "pt","ra","rb","re","s","sb","se","si","sn","sr","ta","te","th","ti","tl","u","v",
    "w","y","zn","zr",
}
_LON_KEYS = {"lon", "longitude", "x", "经度", "lng"}
_LAT_KEYS = {"lat", "latitude", "y", "纬度"}


def _import_commons():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for repo in (here, "/opt/deepexplor-services"):
        if repo and repo not in sys.path:
            sys.path.insert(0, repo)


@dataclass
class GeochemSet:
    status: str                                              # measured | prior_only | empty
    elements: Dict[str, np.ndarray] = dc_field(default_factory=dict)   # 元素→(ny,nx) 含量(对齐网格)
    coverage: Dict[str, np.ndarray] = dc_field(default_factory=dict)   # 元素→(ny,nx) bool
    thresholds: Dict[str, dict] = dc_field(default_factory=dict)       # 背景/异常下限先验
    key_elements: List[str] = dc_field(default_factory=list)
    n_points: int = 0
    provenance: Dict = dc_field(default_factory=dict)

    def available_elements(self) -> List[str]:
        return sorted(self.elements.keys())


def _norm_colname(c: str) -> str:
    return re.sub(r"[\s_\-\.]+", "", str(c).strip().lower())


def _detect_columns(df: pd.DataFrame):
    """返回 (lon_col, lat_col, {元素符号: 原列名})。"""
    lon_col = lat_col = None
    elements: Dict[str, str] = {}
    for col in df.columns:
        n = _norm_colname(col)
        if lon_col is None and n in _LON_KEYS:
            lon_col = col; continue
        if lat_col is None and n in _LAT_KEYS:
            lat_col = col; continue
        # 元素列：列名以元素符号开头（容许 _ppm / _pct 等后缀）
        m = re.match(r"^([a-z]{1,2})", n)
        if m and m.group(1) in _ELEMENT_SYMBOLS:
            sym = m.group(1).capitalize()
            elements.setdefault(sym, col)
    return lon_col, lat_col, elements


def _load_thresholds(bbox, mineral, roots, prov) -> Dict[str, dict]:
    """从 data-colle 取 geochem_thresholds 背景先验（孤儿数据接通）。"""
    _import_commons()
    try:
        from commons.datacolle_broker import find_datacolle_for_bbox
        entries = find_datacolle_for_bbox(tuple(bbox), roots.get("datacolle", ""))
        if entries:
            th = entries[0].get("geochem_thresholds", {}) or {}
            prov["thresholds"] = {"status": "ok" if th else "empty",
                                  "source": "data-colle/prospector",
                                  "aoi": entries[0].get("aoi_name"), "n_elements": len(th)}
            return th
        prov["thresholds"] = {"status": "missing", "note": "data-colle 无相交成果"}
    except Exception as e:
        prov["thresholds"] = {"status": "error", "error": str(e)}
    return {}


def _resolve_columns(df: pd.DataFrame, columns_override: Optional[Dict]):
    """优先用注册表显式列映射（lon/lat/elements），否则回退自动识别。

    返回 (lon_col, lat_col, {元素符号: 原列名})，与 _detect_columns 同构。
    """
    if columns_override:
        lon_col = columns_override.get("lon")
        lat_col = columns_override.get("lat")
        el_map = columns_override.get("elements") or {}
        elements = {str(s).capitalize(): c for s, c in el_map.items() if c in df.columns}
        if lon_col in df.columns and lat_col in df.columns and elements:
            return lon_col, lat_col, elements
    return _detect_columns(df)


def _points_to_geochemset(df: pd.DataFrame, grid, key_elements: List[str],
                          thresholds: Dict[str, dict], prov: Dict, source_tag: str,
                          columns_override: Optional[Dict] = None) -> Optional[GeochemSet]:
    """把点位 DataFrame 插值到网格、组装 measured GeochemSet。无可用元素 → None。

    上传路与公开数据路共用；在 prov[source_tag] 记录取数细节。
    """
    from config.config import Config

    lon_col, lat_col, el_cols = _resolve_columns(df, columns_override)
    if lon_col is None or lat_col is None or not el_cols:
        prov[source_tag] = {"status": "bad_columns", "note": "未识别到经纬度或元素列",
                            "columns": list(map(str, df.columns))[:20]}
        return None

    lons = pd.to_numeric(df[lon_col], errors="coerce").to_numpy()
    lats = pd.to_numeric(df[lat_col], errors="coerce").to_numpy()
    n_points = int(np.isfinite(lons).sum())

    # 仅插值"矿致关键元素 ∩ 数据列"，无交集则用数据里全部元素
    use_syms = [s for s in key_elements if s in el_cols] or list(el_cols.keys())
    elements: Dict[str, np.ndarray] = {}
    coverage: Dict[str, np.ndarray] = {}
    for sym in use_syms:
        vals = pd.to_numeric(df[el_cols[sym]], errors="coerce").to_numpy()
        if np.isfinite(vals).sum() < 3:
            continue
        arr, cov = interpolate_to_grid(lons, lats, vals, grid,
                                       power=Config.IDW_POWER, k=Config.IDW_K)
        if np.isfinite(arr).any():
            elements[sym] = arr
            coverage[sym] = cov

    if not elements:
        prov[source_tag] = {"status": "no_usable_elements", "n_points": n_points,
                            "elements_in_file": sorted(el_cols.keys())}
        return None

    prov[source_tag] = {"status": "ok", "n_points": n_points,
                        "lon_col": str(lon_col), "lat_col": str(lat_col),
                        "elements_interpolated": sorted(elements.keys()),
                        "elements_in_file": sorted(el_cols.keys())}
    return GeochemSet(status="measured", elements=elements, coverage=coverage,
                      thresholds=thresholds, key_elements=key_elements,
                      n_points=n_points, provenance=prov)


def _try_public_geochem(bbox, grid, key_elements: List[str],
                        thresholds: Dict[str, dict], prov: Dict) -> Optional[GeochemSet]:
    """第二优先级：AOI 命中预置的公开化探数据集 → measured GeochemSet，否则 None。"""
    from config.config import Config
    if not getattr(Config, "PUBLIC_GEOCHEM_ENABLED", False):
        return None
    _import_commons()
    try:
        from commons.geochem_public_broker import (
            find_public_geochem_for_bbox, load_public_geochem_df)
        entries = find_public_geochem_for_bbox(tuple(bbox), Config.PUBLIC_GEOCHEM_ROOT)
    except Exception as e:
        prov["public"] = {"status": "error", "error": str(e)}
        return None
    if not entries:
        prov["public"] = {"status": "no_match"}
        return None
    for e in entries:
        df, cols = load_public_geochem_df(e)
        if df is None:
            continue
        gs = _points_to_geochemset(df, grid, key_elements, thresholds, prov,
                                   "public", columns_override=cols)
        if gs is not None:
            prov["public"].update({"dataset": e.get("name"), "source": e.get("source"),
                                   "license": e.get("license"), "scale": "regional",
                                   "scale_note": e.get("scale_note")})
            logger.info(f"gather_geochem: status=measured(public:{e.get('name')}), "
                        f"元素 {gs.available_elements()}")
            return gs
    prov.setdefault("public", {"status": "unusable"})
    return None


def gather_geochem(bbox, mineral, grid, roots: Dict[str, str],
                   upload_path: Optional[str] = None) -> GeochemSet:
    """主入口：返回对齐到 grid 的 GeochemSet（含 provenance 与降级标记）。

    优先级：① 用户上传点位 → ② 公开化探数据(AOI 命中) → ③ 背景阈值先验(prior_only) → ④ empty。
    只降级不报错、绝不臆造异常。
    """
    from config.config import Config
    prov: Dict = {}

    key_elements = Config.key_elements_for(mineral)
    prov["key_elements"] = {"mineral": mineral, "elements": key_elements}

    thresholds = _load_thresholds(bbox, mineral, roots, prov)

    # ① 用户上传点位（最高优先）
    if upload_path and os.path.exists(upload_path):
        try:
            df = pd.read_csv(upload_path)
        except Exception as e:
            prov["upload"] = {"status": "read_failed", "error": str(e)}
            df = None
        if df is not None:
            gs = _points_to_geochemset(df, grid, key_elements, thresholds, prov, "upload")
            if gs is not None:
                prov["upload"]["path"] = os.path.basename(upload_path)
                logger.info(f"gather_geochem: status=measured(upload), 点 {gs.n_points}, "
                            f"插值元素 {gs.available_elements()}")
                return gs
        logger.info("gather_geochem: 上传点位不可用 → 尝试公开数据 / 先验降级")
    else:
        prov["upload"] = {"status": "absent"}

    # ② 公开化探数据（AOI 命中预置数据集）
    gs = _try_public_geochem(bbox, grid, key_elements, thresholds, prov)
    if gs is not None:
        return gs

    # ③/④ 降级：仅背景阈值先验（不出臆造异常）
    status = "prior_only" if thresholds else "empty"
    logger.info(f"gather_geochem: status={status}, 阈值元素 {len(thresholds)}")
    return GeochemSet(status=status, thresholds=thresholds,
                      key_elements=key_elements, provenance=prov)
