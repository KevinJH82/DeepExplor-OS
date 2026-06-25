"""便携 SWIR / XRF 数字岩芯解析（P2）—— 仪器导出 → 机读区间编录。

- SWIR（短波红外）：按深度识别的蚀变矿物 → 映射蚀变类型/岩性线索。
- XRF（X 射线荧光）：按深度的元素品位。
两者按深度分箱合并成与 corelog 同构的 `intervals`，喂给 judge 判见矿。
诚实：按常见导出表头自动识别列；矿物→蚀变用查找表；不臆造缺失数据。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# 蚀变矿物 → 蚀变类型（中英兼容，子串匹配）
_MINERAL_TO_ALT = [
    (("绢云母", "白云母", "伊利石", "sericite", "muscovite", "illite"), "绢英岩化"),
    (("高岭石", "地开石", "明矾石", "叶蜡石", "kaolinite", "dickite", "alunite", "pyrophyllite"), "泥化/高级泥化"),
    (("绿泥石", "绿帘石", "chlorite", "epidote"), "青磐岩化"),
    (("黑云母", "钾长石", "biotite", "k-feldspar", "kfeldspar"), "钾化"),
    (("蒙脱石", "蒙皂石", "montmorillonite", "smectite"), "泥化"),
    (("阳起石", "透闪石", "石榴子石", "actinolite", "tremolite", "garnet"), "矽卡岩化"),
    (("碳酸盐", "方解石", "白云石", "calcite", "dolomite", "carbonate"), "碳酸盐化"),
]


def _norm(c: str) -> str:
    return re.sub(r"[\s_\-\.]+", "", str(c).strip().lower())


def _col(df, *names):
    cols = {_norm(c): c for c in df.columns}
    for n in names:
        if n in cols:
            return cols[n]
    return None


def _mineral_to_alt(name: str) -> str:
    n = (name or "").strip().lower()
    for keys, alt in _MINERAL_TO_ALT:
        if any(k.lower() in n or k in name for k in keys):
            return alt
    return ""


def parse_swir(path: str) -> List[Dict]:
    """→ [{depth, mineral, alteration}]（按深度）。"""
    out: List[Dict] = []
    try:
        df = pd.read_csv(path)
    except Exception as e:
        logger.info(f"SWIR 读取失败: {e}")
        return out
    dep = _col(df, "depth", "深度", "from", "m")
    mc = _col(df, "mineral", "matchedmineral", "矿物", "mineral1", "interpretedmineral", "spectralmineral")
    if dep is None or mc is None:
        logger.info(f"SWIR 未识别列（需 depth + mineral）: {list(df.columns)[:10]}")
        return out
    for _, row in df.iterrows():
        d = pd.to_numeric(pd.Series([row[dep]]), errors="coerce").iloc[0]
        if pd.isna(d):
            continue
        m = str(row[mc]) if pd.notna(row[mc]) else ""
        out.append({"depth": float(d), "mineral": m, "alteration": _mineral_to_alt(m)})
    logger.info(f"parse_swir: {len(out)} 条矿物记录")
    return out


def parse_xrf(path: str) -> Dict:
    """→ {'depth': [...], 'elements': {El: [...]}}。"""
    res = {"depth": [], "elements": {}}
    try:
        df = pd.read_csv(path)
    except Exception as e:
        logger.info(f"XRF 读取失败: {e}")
        return res
    dep = _col(df, "depth", "深度", "from", "m", "sample")
    if dep is None:
        logger.info(f"XRF 未识别深度列: {list(df.columns)[:10]}")
        return res
    _reserved = {_norm(dep), "to", "from", "sampleid", "holeid", "孔号", "深度"}
    el_cols = []
    for c in df.columns:
        if _norm(c) in _reserved:
            continue
        if pd.to_numeric(df[c], errors="coerce").notna().any():   # 非保留的数值列=元素品位
            el_cols.append(c)
    res["depth"] = pd.to_numeric(df[dep], errors="coerce").tolist()
    for c in el_cols:
        res["elements"][str(c).strip()] = pd.to_numeric(df[c], errors="coerce").tolist()
    logger.info(f"parse_xrf: {len(res['depth'])} 深度点，元素列={list(res['elements'].keys())}")
    return res


def to_intervals(swir: List[Dict], xrf: Dict, bin_m: float = 2.0) -> List[Dict]:
    """SWIR + XRF 按深度分箱合并成 intervals（与 corelog 同构）。"""
    depths = [r["depth"] for r in swir] + [d for d in (xrf.get("depth") or []) if d is not None]
    depths = [d for d in depths if d is not None and not pd.isna(d)]
    if not depths:
        return []
    dmin, dmax = min(depths), max(depths)
    intervals: List[Dict] = []
    f = dmin
    while f < dmax + 1e-9:
        t = f + bin_m
        # SWIR：本箱内蚀变（取首个非空）
        alt = ""; litho = ""
        for r in swir:
            if f <= r["depth"] < t:
                if r.get("alteration") and not alt:
                    alt = r["alteration"]
                if r.get("mineral") and not litho:
                    litho = r["mineral"]
        # XRF：本箱内各元素最大品位
        grades: Dict[str, float] = {}
        xd = xrf.get("depth") or []
        for el, vals in (xrf.get("elements") or {}).items():
            best = None
            for d, v in zip(xd, vals):
                if d is None or pd.isna(d) or v is None or pd.isna(v):
                    continue
                if f <= d < t and (best is None or v > best):
                    best = float(v)
            if best is not None:
                grades[el] = best
        if alt or litho or grades:
            intervals.append({"from": round(f, 2), "to": round(t, 2),
                              "lithology": litho, "alteration": alt, "grades": grades})
        f = t
    return intervals
