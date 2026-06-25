"""数字岩芯编录 —— 结构化钻孔库（collar/survey/intervals），可长期复用、可机读。

CSV 约定（列名大小写/中英不敏感）：
- collar:    hole_id, lon, lat[, elevation]
- survey:    hole_id, depth, azimuth, dip          （可选；缺则视为直孔）
- intervals: hole_id, from, to[, lithology, alteration] + 元素品位列（如 Cu, Au, Mo…）
便携 SWIR/XRF 自动编录留 P2；P1 接收人工/仪器导出的区间 CSV。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

_RESERVED = {"holeid", "hole", "孔号", "from", "to", "深度", "fromm", "tom",
             "lithology", "岩性", "alteration", "蚀变", "depth", "azimuth", "dip",
             "lon", "lat", "longitude", "latitude", "经度", "纬度", "elevation", "标高", "elev"}


def _norm(c: str) -> str:
    return re.sub(r"[\s_\-\.]+", "", str(c).strip().lower())


def _col(df, *names):
    cols = {_norm(c): c for c in df.columns}
    for n in names:
        if n in cols:
            return cols[n]
    return None


def _read(path: Optional[str]):
    if not path:
        return None
    try:
        return pd.read_csv(path)
    except Exception as e:
        logger.info(f"读取 {path} 失败: {e}")
        return None


def ingest_core_logs(collar_path: Optional[str], survey_path: Optional[str] = None,
                     intervals_path: Optional[str] = None, swir_path: Optional[str] = None,
                     xrf_path: Optional[str] = None, instr_hole_id: str = "DH-INSTR-1") -> Dict:
    """→ {holes:{hole_id:{collar,survey,intervals}}, grade_elements:[...]}。
    swir_path/xrf_path：便携仪器导出 → 数字岩芯区间，合并到 instr_hole_id（若 collar 已有该孔则附其上）。"""
    holes: Dict[str, Dict] = {}

    cdf = _read(collar_path)
    if cdf is not None:
        hid = _col(cdf, "holeid", "hole", "孔号")
        lon = _col(cdf, "lon", "longitude", "经度"); lat = _col(cdf, "lat", "latitude", "纬度")
        elev = _col(cdf, "elevation", "标高", "elev")
        if hid and lon and lat:
            for _, row in cdf.iterrows():
                h = str(row[hid]).strip()
                if not h:
                    continue
                try:
                    holes[h] = {"collar": {"lon": float(row[lon]), "lat": float(row[lat]),
                                           "elev": (float(row[elev]) if elev and pd.notna(row[elev]) else None)},
                                "survey": [], "intervals": []}
                except (ValueError, TypeError):
                    continue

    sdf = _read(survey_path)
    if sdf is not None:
        hid = _col(sdf, "holeid", "hole", "孔号"); dep = _col(sdf, "depth", "深度")
        az = _col(sdf, "azimuth"); dip = _col(sdf, "dip")
        if hid and dep:
            for _, row in sdf.iterrows():
                h = str(row[hid]).strip()
                if h in holes:
                    holes[h]["survey"].append({
                        "depth": float(row[dep]) if pd.notna(row[dep]) else None,
                        "azimuth": float(row[az]) if az and pd.notna(row[az]) else None,
                        "dip": float(row[dip]) if dip and pd.notna(row[dip]) else None})

    idf = _read(intervals_path)
    grade_cols: List[str] = []
    if idf is not None:
        hid = _col(idf, "holeid", "hole", "孔号")
        f = _col(idf, "from", "fromm", "深度"); t = _col(idf, "to", "tom")
        litho = _col(idf, "lithology", "岩性"); alt = _col(idf, "alteration", "蚀变")
        # 品位列 = 既非保留列、又能转数值的列
        for c in idf.columns:
            if _norm(c) in _RESERVED:
                continue
            if pd.to_numeric(idf[c], errors="coerce").notna().any():
                grade_cols.append(c)
        if hid and f and t:
            for _, row in idf.iterrows():
                h = str(row[hid]).strip()
                if h not in holes:
                    holes[h] = {"collar": {"lon": None, "lat": None, "elev": None},
                                "survey": [], "intervals": []}
                grades = {}
                for gc in grade_cols:
                    v = pd.to_numeric(pd.Series([row[gc]]), errors="coerce").iloc[0]
                    if pd.notna(v):
                        grades[str(gc).strip()] = float(v)
                holes[h]["intervals"].append({
                    "from": float(row[f]) if pd.notna(row[f]) else None,
                    "to": float(row[t]) if pd.notna(row[t]) else None,
                    "lithology": str(row[litho]) if litho and pd.notna(row[litho]) else "",
                    "alteration": str(row[alt]) if alt and pd.notna(row[alt]) else "",
                    "grades": grades})

    # SWIR/XRF 数字岩芯（P2）→ 合并到 instr_hole_id（collar 已有则附其上，否则新建无坐标孔）
    if swir_path or xrf_path:
        from core.instruments import parse_swir, parse_xrf, to_intervals
        ivs = to_intervals(parse_swir(swir_path) if swir_path else [],
                           parse_xrf(xrf_path) if xrf_path else {})
        if ivs:
            if instr_hole_id not in holes:
                holes[instr_hole_id] = {"collar": {"lon": None, "lat": None, "elev": None},
                                        "survey": [], "intervals": []}
            holes[instr_hole_id]["intervals"].extend(ivs)
            for iv in ivs:
                for el in iv.get("grades", {}):
                    if el not in grade_cols:
                        grade_cols.append(el)
            logger.info(f"SWIR/XRF → {instr_hole_id} 合并 {len(ivs)} 区间")

    logger.info(f"ingest_core_logs: {len(holes)} 孔，品位元素列={grade_cols}")
    return {"holes": holes, "grade_elements": grade_cols}
