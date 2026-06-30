"""geo-portal BFF 入口。

职责:统一认证 + tenant/RBAC 上下文 + 项目/运行(trace_id)托管 + 反向代理 + 服务清单。
前端经 vite proxy 同源访问 /api 与 /svc。
"""
import csv
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
import httpx
from fastapi import FastAPI, Depends, HTTPException, Body, UploadFile, File, Form, Cookie, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import store, auth, services
from .config import get_settings
from .proxy import router as proxy_router
from .admin import router as admin_router
from .public import router as public_router
from .runstages import summarize_progress

_settings = get_settings()

_SAMPLE_KML = Path(__file__).resolve().parent / "sample_aoi.kml"
_KML_MIME = "application/vnd.google-earth.kml+xml"
_KML_DIR = store.DATA_DIR / "kml"
_KML_DIR.mkdir(parents=True, exist_ok=True)


def _kml_for_project(project_id: str):
    """项目有上传的 KML 则用之,否则回退样例 KML。"""
    f = _KML_DIR / f"{project_id}.kml"
    if f.exists():
        return {"file": (f"{project_id}.kml", f.read_bytes(), _KML_MIME)}
    return {"file": ("aoi.kml", _SAMPLE_KML.read_bytes(), _KML_MIME)}


def _parse_bbox(raw: bytes):
    """从 KML 文本提取 bbox(失败返回 None)。"""
    pts = _coords_from_kml_text(_safe_text(raw))
    if not pts:
        return None
    lons = [p[0] for p in pts]; lats = [p[1] for p in pts]
    return [min(lons), min(lats), max(lons), max(lats)]


def _safe_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig", "ignore")
    except Exception:
        return raw.decode("utf-8", "ignore")


def _coords_from_kml_text(text: str):
    """KML/ovKML/XML:抓所有 <coordinates> 内的 lon,lat。"""
    pts = []
    for block in re.findall(r"<coordinates[^>]*>(.*?)</coordinates>", text, re.S | re.I):
        for tok in block.replace("\n", " ").split():
            parts = tok.split(",")
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
    return pts


def _coords_from_rows(rows):
    """从表格行提取 (lon,lat):优先表头关键字,否则按数值范围推断。"""
    if not rows:
        return []
    def norm(s):
        return str(s).strip().lower() if s is not None else ""
    hdr = [norm(h) for h in rows[0]]
    lon_i = lat_i = None
    for i, h in enumerate(hdr):
        if lon_i is None and ("lon" in h or "lng" in h or "经" in h or h == "x"):
            lon_i = i
        if lat_i is None and ("lat" in h or "纬" in h or h == "y"):
            lat_i = i
    pts = []
    if lon_i is not None and lat_i is not None:
        for r in rows[1:]:
            try:
                pts.append((float(r[lon_i]), float(r[lat_i])))
            except (ValueError, TypeError, IndexError):
                pass
    else:  # 无表头:每行取首个落在经度区间的数为 lon、纬度区间的数为 lat
        for r in rows:
            nums = []
            for c in r:
                try:
                    nums.append(float(c))
                except (ValueError, TypeError):
                    pass
            lon = lat = None
            for x in nums:
                if lon is None and -180 <= x <= 180:
                    lon = x
                elif lat is None and -90 <= x <= 90:
                    lat = x
            if lon is not None and lat is not None:
                pts.append((lon, lat))
    return pts


def _coords_from_csv(raw: bytes):
    return _coords_from_rows(list(csv.reader(io.StringIO(_safe_text(raw)))))


def _coords_from_xlsx(raw: bytes):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
        return _coords_from_rows(rows)
    except Exception:
        return []


def _extract_coords(raw: bytes, ext: str):
    ext = ext.lower()
    if ext in ("kml", "ovkml", "xml"):
        return _coords_from_kml_text(_safe_text(raw))
    if ext == "kmz":
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
            name = next((n for n in zf.namelist() if n.lower().endswith(".kml")), None)
            if name:
                return _coords_from_kml_text(zf.read(name).decode("utf-8", "ignore"))
        except Exception:
            pass
        return []
    if ext == "csv":
        return _coords_from_csv(raw)
    if ext == "xlsx":
        return _coords_from_xlsx(raw)
    return []


def _kml_from_coords(pts) -> str:
    """坐标序列 -> 规范 KML 多边形。"""
    lons = [float(lo) for lo, _ in pts]
    lats = [float(la) for _, la in pts]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    if min_lon == max_lon:
        min_lon -= 0.01
        max_lon += 0.01
    if min_lat == max_lat:
        min_lat -= 0.01
        max_lat += 0.01
    ring_pts = [
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
        (min_lon, min_lat),
    ]
    ring = " ".join(f"{lo},{la},0" for lo, la in ring_pts)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>portal_aoi</name>'
            '<Placemark><name>aoi</name><Polygon><outerBoundaryIs><LinearRing>'
            f'<coordinates>{ring}</coordinates>'
            '</LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>')


# ─── 证据服务适配器(analyser/stru 接口异构,需多步 + 交付库定位)──────────
_ADAPTER_TASKS = {}   # bff task_id -> {status, progress, error}


def _adapter_thread(fn, *args):
    """跑适配器任务(fn 首参为 bff task_id),结束时把终态(含栅格 layer)落库,
    供 BFF 重启后 adapter-raster/svcstatus 从库恢复(内存态丢失不再'未取到栅格'/卡住)。"""
    tid = args[0]
    try:
        fn(*args)
    finally:
        try:
            store.save_adapter_task(tid, _ADAPTER_TASKS.get(tid))
        except Exception:
            pass


def _evidence_summary(service: str, meta: dict):
    """从各证据服务的 manifest/metadata 提炼已有量化指标 → 归一 metrics(仅转发,不算法)。
    供证据链叙事"事实层"挂真实数;字段缺失则省略,不编造。返回 {service, metrics} 或 None。"""
    meta = meta or {}
    m = {}
    if service == "analyser":
        res = meta.get("results") or []
        # 仅用顶层聚合 anomaly_ratio(0-1 真分数);results 内的 anomaly_ratio 量纲不一致(可能>1),不做兜底以免误显
        ar = meta.get("anomaly_ratio")
        m["anomaly_ratio"] = ar if isinstance(ar, (int, float)) and 0 <= ar <= 1 else None
        m["high_confidence_pixels"] = meta.get("high_confidence_total_pixels") or meta.get("high_confidence_pixels")
        m["deposit_type"] = meta.get("deposit_type")
        minerals = {r.get("mineral") for r in res if isinstance(r, dict) and r.get("mineral")}
        m["n_minerals"] = len(minerals) or None
    elif service == "stru":
        ss = meta.get("structural_stats") or meta
        for k in ("n_lineaments", "total_lineament_length_km", "lineament_density_mean",
                  "dominant_strikes_deg", "elevation_range_m"):
            m[k] = ss.get(k)
    elif service == "geophys":
        ms = meta.get("model_stats") or meta
        eu = ms.get("euler") or {}
        m["n_sources"] = eu.get("n_sources")
        m["source_depth_km_min"] = eu.get("source_depth_km_min")
        m["source_depth_km_max"] = eu.get("source_depth_km_max")
        ig = ms.get("igrf") or {}
        m["inclination_deg"] = ig.get("inclination_deg")
        m["declination_deg"] = ig.get("declination_deg")
        m["mineral_type"] = ms.get("mineral_type")
    elif service == "insar":
        st = meta.get("stats") or meta
        for k in ("deformation_rate_abs_mean_mm_yr", "deformation_rate_abs_max_mm_yr",
                  "deformation_rate_abs_p95_mm_yr", "coverage_ratio", "n_bursts"):
            m[k] = st.get(k)
    elif service == "geochem":
        an = meta.get("anomaly_stats") or {}
        for k in ("n_anomalies", "n_background", "n_transition", "median_ca_log_ratio"):
            m[k] = an.get(k)
        m["key_elements"] = meta.get("key_elements")
    metrics = {k: v for k, v in m.items() if v is not None and v != []}
    return {"service": service, "metrics": metrics} if metrics else None


_MINERAL_ZH = {
    "copper": "铜", "gold": "金", "iron": "铁", "leadzinc": "铅锌",
    "silver": "银", "molybdenum": "钼", "tungsten": "钨", "tin": "锡",
    "nickel": "镍", "lead": "铅锌", "zinc": "铅锌",
    "platinum_group": "铂族金属", "cobalt": "钴", "antimony": "锑",
    "mercury": "汞", "bismuth": "铋", "manganese": "锰", "chromium": "铬",
    "titanium": "钛", "vanadium": "钒", "rare_earth": "稀土",
    "lithium": "锂", "beryllium": "铍", "niobium_tantalum": "铌钽",
    "zirconium_hafnium": "锆铪", "rubidium_cesium": "铷铯",
    "gallium": "镓", "germanium": "锗", "indium": "铟",
    "oil_gas": "油气", "oil": "石油", "gas": "天然气",
    "shale_gas": "页岩气", "coalbed_methane": "煤层气",
    "coal": "煤", "uranium": "铀", "geothermal": "地热",
    "phosphate": "磷", "potash": "钾盐", "salt": "岩盐",
    "fluorite": "萤石", "barite": "重晶石", "graphite": "石墨",
    "quartz": "石英", "limestone": "石灰岩", "dolomite": "白云岩",
    "gypsum": "石膏", "kaolin": "高岭土", "bauxite": "铝土矿",
    "diamond": "金刚石", "gemstone": "宝玉石", "multi_mineral": "多金属",
}

_EVIDENCE_DEFS = [
    {"key": "analyser", "label": "蚀变", "service": "analyser"},
    {"key": "stru", "label": "构造", "service": "stru"},
    {"key": "geophys", "label": "物探", "service": "geophys"},
    {"key": "geochem", "label": "化探", "service": "geochem"},
    {"key": "insar", "label": "形变", "service": "insar"},
]

_REMOTE_SOURCE_KEYS = {"sentinel2", "landsat8", "sentinel1", "aster", "dem"}
_GEOPHYS_SOURCE_KEYS = {"emag2", "gravity"}
_GEOCHEM_SOURCE_KEYS = {"geochem_bg"}
_DEFAULT_SOURCE_KEYS = ["sentinel2", "landsat8", "sentinel1", "dem", "emag2", "gravity", "geochem_bg", "mineral_kb"]
# 矿种 → 成矿模型族(与前端 geologyNarrative.mineralKey 对齐;按 mineral key 精确归类)
_FAMILY_MINERALS = {
    "porphyry":    {"copper", "molybdenum"},
    "epithermal":  {"gold", "silver", "antimony", "mercury"},
    "skarn":       {"tungsten", "tin", "bismuth"},
    "magmatic":    {"nickel", "cobalt", "chromium", "platinum_group", "titanium", "vanadium"},
    "iron":        {"iron"},
    "leadzinc":    {"leadzinc", "lead", "zinc"},
    "pegmatite":   {"lithium", "beryllium", "niobium_tantalum", "zirconium_hafnium", "rubidium_cesium"},
    "ree":         {"rare_earth", "gallium", "germanium", "indium"},
    "uranium":     {"uranium"},
    "energy":      {"oil_gas", "oil", "gas", "shale_gas", "coalbed_methane", "coal", "geothermal"},
    "sedimentary": {"phosphate", "potash", "salt", "fluorite", "barite", "graphite", "quartz",
                    "limestone", "dolomite", "gypsum", "kaolin", "bauxite", "manganese"},
    "kimberlite":  {"diamond", "gemstone"},
}


def _mineral_family(mineral: str) -> str:
    m = str(mineral or "").strip().lower()
    for fam, keys in _FAMILY_MINERALS.items():
        if m in keys:
            return fam
    return "comprehensive"   # multi_mineral / 未识别 → 综合多源


def _plan_sources(run: dict) -> list:
    plan = run.get("plan") or {}
    if isinstance(plan.get("sources"), list):
        return [str(x) for x in plan.get("sources") if x]
    sources = []
    for st in plan.get("stages") or []:
        for key in ("sensors", "datacolle"):
            sources.extend([str(x) for x in st.get(key) or [] if x])
    return sources or list(_DEFAULT_SOURCE_KEYS)


# 各成矿模型族的证据门控/权重/级别。weights 每套和约为 1.0;5 类证据:analyser蚀变/stru构造/geophys物探/geochem化探/insar形变。
_PROFILES = {
    "porphyry": {
        "model": "斑岩型铜钼证据模型",
        "gate": {"min_completed": 3, "required_any": ["analyser", "stru"]},
        "weights": {"analyser": 0.30, "stru": 0.26, "geophys": 0.20, "geochem": 0.18, "insar": 0.06},
        "levels": {"analyser": "required", "stru": "required", "geophys": "recommended", "geochem": "recommended", "insar": "optional"},
        "rationale": "斑岩型铜钼以蚀变分带(钾化-绢英岩化-青磐岩化)、侵入体边界与裂隙构造、磁/激电物探和 Cu-Mo 化探异常的叠合为核心证据。",
    },
    "epithermal": {
        "model": "浅成低温热液金银证据模型",
        "gate": {"min_completed": 3, "required_any": ["analyser", "stru"]},
        "weights": {"analyser": 0.30, "stru": 0.28, "geochem": 0.20, "geophys": 0.14, "insar": 0.08},
        "levels": {"analyser": "required", "stru": "required", "geochem": "recommended", "geophys": "recommended", "insar": "optional"},
        "rationale": "浅成低温热液金银以硅化/泥化蚀变、控矿断裂构造和 Au-Ag-As-Sb-Hg 化探异常为主要约束, 物探辅助圈定隐伏构造。",
    },
    "skarn": {
        "model": "矽卡岩型钨锡多金属证据模型",
        "gate": {"min_completed": 3, "required_any": ["stru", "geophys"]},
        "weights": {"stru": 0.28, "analyser": 0.24, "geophys": 0.24, "geochem": 0.18, "insar": 0.06},
        "levels": {"stru": "required", "geophys": "required", "analyser": "recommended", "geochem": "recommended", "insar": "optional"},
        "rationale": "矽卡岩型矿床以岩体-碳酸盐岩接触带构造、矽卡岩化蚀变、磁/重物探异常边界和 W-Sn 化探异常的叠合为核心证据。",
    },
    "magmatic": {
        "model": "岩浆型铜镍铬铂证据模型",
        "gate": {"min_completed": 3, "required_any": ["geophys", "stru"]},
        "weights": {"geophys": 0.34, "stru": 0.26, "geochem": 0.20, "analyser": 0.12, "insar": 0.08},
        "levels": {"geophys": "required", "stru": "required", "geochem": "recommended", "analyser": "optional", "insar": "optional"},
        "rationale": "岩浆型铜镍(铬铂)以强磁/高密度物探异常、镁铁质岩体与岩浆通道构造、Ni-Cu-PGE 化探线索为主要约束; 热液蚀变不发育, 仅作辅助。",
    },
    "iron": {
        "model": "铁/IOCG 证据模型",
        "gate": {"min_completed": 3, "required_any": ["geophys", "stru"]},
        "weights": {"geophys": 0.34, "stru": 0.26, "analyser": 0.18, "geochem": 0.14, "insar": 0.08},
        "levels": {"geophys": "required", "stru": "required", "analyser": "recommended", "geochem": "recommended", "insar": "optional"},
        "rationale": "铁/IOCG 以强磁与重力异常、深大断裂构造为核心约束, 钠钙质蚀变与 Cu-Au-Co-REE 化探作辅助识别。",
    },
    "leadzinc": {
        "model": "SEDEX/MVT 铅锌证据模型",
        "gate": {"min_completed": 3, "required_any": ["geochem", "stru"]},
        "weights": {"geochem": 0.30, "stru": 0.26, "geophys": 0.18, "analyser": 0.18, "insar": 0.08},
        "levels": {"geochem": "required", "stru": "required", "geophys": "recommended", "analyser": "recommended", "insar": "optional"},
        "rationale": "层控铅锌以 Pb-Zn-Ag-Ba 化探异常、有利层位与同沉积断裂构造为主要约束, 物探辅助圈定盆地边界与隐伏体。",
    },
    "pegmatite": {
        "model": "伟晶岩稀有金属证据模型",
        "gate": {"min_completed": 3, "required_any": ["geochem", "stru"]},
        "weights": {"geochem": 0.28, "analyser": 0.26, "stru": 0.24, "geophys": 0.14, "insar": 0.08},
        "levels": {"geochem": "required", "stru": "required", "analyser": "recommended", "geophys": "optional", "insar": "optional"},
        "rationale": "伟晶岩/稀有金属以 Li-Be-Nb-Ta-Cs 化探异常、云英岩化/钠长石化蚀变和高分异岩体外接触带构造为主要约束。",
    },
    "ree": {
        "model": "稀土/稀散证据模型",
        "gate": {"min_completed": 3, "required_any": ["geochem", "geophys"]},
        "weights": {"geochem": 0.30, "geophys": 0.24, "stru": 0.22, "analyser": 0.16, "insar": 0.08},
        "levels": {"geochem": "required", "geophys": "recommended", "stru": "recommended", "analyser": "optional", "insar": "optional"},
        "rationale": "稀土/稀散以 REE 化探异常、碱性-碳酸岩体的磁/重物探响应和断裂控岩构造为主要约束; 离子吸附型还需结合风化壳条件。",
    },
    "uranium": {
        "model": "铀矿证据模型",
        "gate": {"min_completed": 3, "required_any": ["stru", "geochem"]},
        "weights": {"stru": 0.30, "geochem": 0.26, "geophys": 0.18, "analyser": 0.16, "insar": 0.10},
        "levels": {"stru": "required", "geochem": "required", "geophys": "recommended", "analyser": "optional", "insar": "recommended"},
        "rationale": "铀矿以盆地构造格架与层间氧化-还原过渡带、U-Mo-Se 化探异常为主要约束; 注:本系统物探为磁重位场不含放射性, 放射性测量资料需另行补充。",
    },
    "energy": {
        "model": "能源/油气证据模型",
        "gate": {"min_completed": 3, "required_any": ["stru", "geophys"]},
        "weights": {"stru": 0.34, "geophys": 0.30, "insar": 0.16, "geochem": 0.12, "analyser": 0.08},
        "levels": {"stru": "required", "geophys": "required", "insar": "recommended", "geochem": "recommended", "analyser": "optional"},
        "rationale": "油气/能源类目标以构造圈闭、盆地/储层边界、物探响应和地表形变背景为主要约束; 蚀变只作为辅助遥感异常, 不作为核心门控证据。",
    },
    "sedimentary": {
        "model": "非金属/层控矿产证据模型",
        "gate": {"min_completed": 3, "required_any": ["stru", "geochem"]},
        "weights": {"geochem": 0.28, "stru": 0.24, "analyser": 0.22, "geophys": 0.16, "insar": 0.10},
        "levels": {"geochem": "required", "stru": "recommended", "analyser": "recommended", "geophys": "recommended", "insar": "optional"},
        "rationale": "非金属/层控矿产更依赖有利岩性层位、化探背景和盆地构造边界约束; 物探与形变作为空间边界和稳定性辅助证据。",
    },
    "kimberlite": {
        "model": "金伯利岩型金刚石证据模型",
        "gate": {"min_completed": 2, "required_any": ["geophys", "stru"]},
        "weights": {"geophys": 0.36, "stru": 0.28, "geochem": 0.18, "analyser": 0.10, "insar": 0.08},
        "levels": {"geophys": "required", "stru": "required", "geochem": "recommended", "analyser": "optional", "insar": "optional"},
        "rationale": "金伯利岩型金刚石以岩管/岩脉磁异常、克拉通深大断裂构造和指示矿物分散晕为主要约束; 蚀变不作为核心证据。",
    },
    "comprehensive": {
        "model": "综合多源证据模型",
        "gate": {"min_completed": 3, "required_any": ["analyser", "stru"]},
        "weights": {"analyser": 0.26, "stru": 0.26, "geophys": 0.20, "geochem": 0.20, "insar": 0.08},
        "levels": {"analyser": "recommended", "stru": "required", "geophys": "recommended", "geochem": "recommended", "insar": "optional"},
        "rationale": "未限定单一矿床类型时, 以构造、蚀变、物探与化探异常的空间叠合作为综合约束, 圈定多源证据一致的远景区。",
    },
}


_LEVEL_ZH = {"required": "必需", "recommended": "推荐", "optional": "可选"}


def _evidence_profile(mineral: str) -> dict:
    return _PROFILES[_mineral_family(mineral)]


def _source_support(key: str, sources: list) -> tuple:
    src = set(sources or [])
    if key in ("analyser", "stru", "insar"):
        ok = bool(src & _REMOTE_SOURCE_KEYS) or not src
        return ok, ["Sentinel-2", "Landsat-8", "Sentinel-1", "ASTER", "DEM"]
    if key == "geophys":
        ok = bool(src & _GEOPHYS_SOURCE_KEYS)
        return ok, ["EMAG2 磁", "WGM/ICGEM 重力"]
    if key == "geochem":
        ok = bool(src & _GEOCHEM_SOURCE_KEYS)
        return ok, ["化探背景值", "矿种知识库"]
    return True, []


_TASK_TERMINAL = {"completed", "failed", "skipped", "degraded"}


def _task_enabled(task: dict) -> bool:
    return task.get("enabled") is not False


def _normalize_evidence_plan(plan: dict) -> dict:
    """Refresh derived evidence-plan status without dropping user edits."""
    plan = dict(plan or {})
    tasks = []
    for task in plan.get("evidence_tasks") or []:
        t = dict(task)
        status = str(t.get("status") or "pending")
        if t.get("degraded"):
            status = "degraded"
        if status == "running" and not t.get("task_id"):
            status = "pending"
        t["status"] = status
        if status in ("completed", "degraded", "skipped", "failed"):
            t["progress"] = 100
        elif status == "running":
            t["progress"] = int(t.get("progress") or 0)
        else:
            t["progress"] = int(t.get("progress") or 0)
        tasks.append(t)
    plan["evidence_tasks"] = tasks

    enabled = [t for t in tasks if _task_enabled(t)]
    if not enabled:
        plan["status"] = "completed"
    elif any(t.get("status") == "running" for t in enabled):
        plan["status"] = "executing"
    elif all(t.get("status") in ("completed", "degraded", "skipped") for t in enabled):
        plan["status"] = "degraded" if any(t.get("status") == "degraded" for t in enabled) else "completed"
    elif any(t.get("status") == "failed" for t in enabled):
        plan["status"] = "failed"
    else:
        plan["status"] = plan.get("status") or "draft"
    plan["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return plan


def _build_evidence_plan(run: dict, proj: dict, existing: dict = None) -> dict:
    sources = _plan_sources(run)
    profile = _evidence_profile(proj.get("mineral"))
    existing_tasks = {t.get("key"): t for t in (existing or {}).get("evidence_tasks", []) if t.get("key")}
    stage_sub_tasks = ((run.get("stages") or {}).get("evidence") or {}).get("sub_tasks") or {}
    data_sub_tasks = ((run.get("stages") or {}).get("data") or {}).get("sub_tasks") or {}
    tasks = []
    for idx, ev in enumerate(_EVIDENCE_DEFS):
        key = ev["key"]
        support_ok, deps = _source_support(key, sources)
        level = profile["levels"].get(key, "optional")
        recommended = level in ("required", "recommended") and support_ok
        old = existing_tasks.get(key) or {}
        if key not in existing_tasks:
            seeded = stage_sub_tasks.get(ev["service"]) or {}
            if key == "insar" and not seeded:
                seeded = data_sub_tasks.get("insar") or {}
            if seeded.get("task_id"):
                old = {
                    "status": seeded.get("status") or "running",
                    "progress": seeded.get("progress") or 0,
                    "task_id": seeded.get("task_id"),
                    "error": seeded.get("error") or "",
                }
        old_status = old.get("status") or "pending"
        has_old = key in existing_tasks
        task = {
            "key": key,
            "label": ev["label"],
            "service": ev["service"],
            "recommended": recommended,
            "enabled": bool(old.get("enabled")) if has_old else recommended,
            "required_level": level,
            "status": old_status,
            "progress": int(old.get("progress") or (100 if old_status in _TASK_TERMINAL else 0)),
            "weight": float(old.get("weight") if has_old and old.get("weight") is not None else profile["weights"].get(key, 0.1)),
            "priority": int(old.get("priority", idx + 1)),
            "dependencies": deps,
            "fallback": old.get("fallback") or "失败后允许重试; 若仍失败, 可降级为低置信约束或跳过并记录原因。",
            "task_id": old.get("task_id") or "",
            "error": old.get("error") or "",
            "degraded": bool(old.get("degraded") or old_status == "degraded"),
            "skip_reason": old.get("skip_reason") or "",
            "fallback_action": old.get("fallback_action") or "",
            "reason": (
                f"{ev['label']}证据被设为{_LEVEL_ZH.get(level, level)}; " +
                ("当前数据源可支持该分析。" if support_ok else "当前数据源不足, 建议补充对应资料后再运行。")
            ),
        }
        tasks.append(task)
    return _normalize_evidence_plan({
        "trace_id": run["trace_id"],
        "project_id": run["project_id"],
        "mineral": proj.get("mineral"),
        "mineral_label": proj.get("mineral_label") or proj.get("mineral"),
        "model": profile["model"],
        "available_sources": sources,
        "gate": profile["gate"],
        "rationale": profile["rationale"],
        "status": (existing or {}).get("status") or "draft",
        "evidence_tasks": tasks,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def _internal_headers(tenant_id: str = None, delivery_id: str = None) -> dict:
    """BFF 直连下游时的标准头:内部密钥(防绕过) + 租户上下文(产物隔离) + 交付绑定。

    /svc 反代由 proxy.py 注入;此处用于 /api/runs/start 等 BFF 直接 httpx 调用路径,
    使两条路径对下游一致。值缺失则不带对应头(向后兼容)。
    """
    h = {}
    if _settings.internal_key:
        h["X-Internal-Key"] = _settings.internal_key
    if tenant_id:
        h["X-Tenant-Id"] = tenant_id
    if delivery_id:
        h["X-Delivery-Id"] = delivery_id   # 门户绑定的交付,下游优先按此 ID 取数据
    return h


def _resolve_delivery(pts, kml_name: str = "") -> dict:
    """按 ROI 几何(+文件名)解析绑定的交付库 → {delivery_id, delivery_name, method}。

    交付绑定:门户侧一次性把 ROI 解析成稳定 delivery_id 存项目,下游凭 ID 取数据,
    不再每次按名字猜。commons.delivery 不可用/无交付根时静默返回空(不阻断上传)。
    """
    try:
        import sys as _sys
        if "/opt/deepexplor-services" not in _sys.path:
            _sys.path.insert(0, "/opt/deepexplor-services")
        from commons.delivery import resolve as _resolve
        geom = None
        if pts:
            ring = [list(p) for p in pts]
            geom = {"type": "Polygon", "coordinates": [ring]}
        r = _resolve(name=kml_name or "", roi_geojson=geom)
        d = r.get("dir")
        return {"delivery_id": r.get("delivery_id"),
                "delivery_name": d.name if d else None, "method": r.get("method"),
                "candidates": r.get("candidates") or []}
    except Exception:
        return {"delivery_id": None, "delivery_name": None, "method": "none", "candidates": []}


def _kml_bytes_and_name(project_id: str):
    """返回(项目缓存 KML 字节, 原始文件名)。证据服务按文件名主名在交付库定位项目。"""
    proj = store.get_project(project_id) or {}
    f = _KML_DIR / f"{project_id}.kml"
    data = f.read_bytes() if f.exists() else _SAMPLE_KML.read_bytes()
    name = proj.get("kml_name") or (f"{proj.get('name')}.kml" if proj.get("name") else "aoi.kml")
    return data, name


def _run_analyser(task_id, kml_bytes, kml_name, mineral, tenant_id=None, delivery_id=None, aoi_bbox=None):
    t = _ADAPTER_TASKS[task_id]

    def _try_reuse():
        """项目自身交付数据缺失时,按 AOI bbox+矿种复用同区现成蚀变产物(避免重算/卡死)。"""
        rt, rdir, dep = _find_alteration_by_bbox(aoi_bbox, mineral)
        if rt:
            t["layer"] = {"kind": "file", "path": rt}
            t.update(status="completed", progress=100,
                     warning=f"项目交付数据缺失,已复用同区现成蚀变产物({rdir})")
            # 复用分支同样采量化指标(读复用产物的 manifest),否则证据链/研判看不到蚀变数值
            try:
                for cand in (os.path.join(os.path.dirname(os.path.dirname(rt)), "manifest.json"),
                             os.path.join(os.path.dirname(rt), "manifest.json")):
                    if os.path.isfile(cand):
                        summ = _evidence_summary("analyser", json.load(open(cand, encoding="utf-8")))
                        if summ:
                            summ["metrics"].setdefault("deposit_type", dep)
                            summ["reused"] = True
                            t["summary"] = summ
                        break
            except Exception:
                pass
            return True
        return False

    try:
        # 同一 AOI+矿种已算过蚀变 → 直接复用现成产物,避免重复读上百 MB 光学波段(SMB 慢/超时,
        # analyze_batch 会爬在 Sentinel-2/Landsat 大文件上、最终 600s 超时)。新 AOI 才真跑。
        if _try_reuse():
            t["warning"] = f"同区已算过蚀变,直接复用现成产物(免重复读 SMB 光学波段)"
            return
        base = services.base_url("analyser")
        with httpx.Client(timeout=600.0, headers=_internal_headers(tenant_id, delivery_id)) as c:
            up = c.post(f"{base}/api/upload_roi",
                        files={"file": (kml_name, kml_bytes, _KML_MIME)}).json()
            if up.get("error") or not up.get("project_name"):
                if _try_reuse():
                    return
                if str(mineral or "").lower() in ("oil", "oil_gas", "hydrocarbon", "gas"):
                    t.update(status="completed", progress=100, no_layer=True,
                             warning=up.get("error") or "油气项目缺少遥感交付数据,蚀变辅助证据跳过")
                    return
                raise RuntimeError(up.get("error") or "交付库未匹配到项目(KML 文件名需=交付目录名)")
            pname = up["project_name"]
            roi_geojson = up.get("roi_geojson")   # 必传:否则 manifest.roi_geojson=None,broker 无法按 bbox 发现
            sensors = [s["key"] for s in (up.get("available_sensors") or [])
                       if isinstance(s, dict) and s.get("key")]
            # 交付目录无可用传感器(常因项目名≠数据目录名,目录为空) → 先复用同区现成产物,否则快速失败
            # (不再带空 sensors 去 analyze_batch 导致卡死在 92%)
            if not sensors:
                if _try_reuse():
                    return
                raise RuntimeError(f"项目 {pname} 交付目录无可用传感器数据(数据可能在别名目录);"
                                   f"且未找到同区现成蚀变产物可复用")
            t["progress"] = 25
            deposit_type = ""
            commodity = _MINERAL_ZH.get(mineral, "")
            if commodity:
                dts = c.post(f"{base}/api/deposit_types", json={"commodity": commodity}).json().get("deposit_types", [])
                if dts:
                    deposit_type = dts[0].get("type_name") if isinstance(dts[0], dict) else dts[0]
            # 油气/煤层气等矿种不在标准 commodity 蚀变库(它们是独立的微渗漏体系),
            # 改采纳 geo-stru 构造推理的矿床类型(经 structural_deposit 映射到微渗漏蚀变类型)。
            if not deposit_type and roi_geojson:
                try:
                    sc = c.post(f"{base}/api/structural_deposit_type",
                                json={"roi_geojson": roi_geojson}).json()
                    cands = sc.get("candidates") or []
                    if cands:
                        deposit_type = cands[0].get("deposit_type") or ""
                except Exception:
                    pass
            t["progress"] = 40
            body = {"project_name": pname, "deposit_type": deposit_type}
            if roi_geojson:
                body["roi_geojson"] = roi_geojson
            if sensors:
                body["available_sensors"] = sensors
            rb = c.post(f"{base}/api/analyze_batch", json=body).json()
            if rb.get("error"):
                raise RuntimeError(rb["error"])
        # 记录一张代表性蚀变栅格供 2D 叠图(优先 composite/score)
        run_dir = (rb.get("saved") or {}).get("run_dir")
        if run_dir and os.path.isdir(run_dir):
            tifs = glob.glob(os.path.join(run_dir, "**", "*.tif"), recursive=True)
            def _sc(p):
                s = os.path.basename(p).lower()
                for i, k in enumerate(("composite", "score", "intersection")):
                    if k in s:
                        return 10 - i
                return 0
            if tifs:
                t["layer"] = {"kind": "file", "path": sorted(tifs, key=_sc, reverse=True)[0]}
            # 接通蚀变量化指标(manifest.json)供叙事事实层
            try:
                mf = os.path.join(run_dir, "manifest.json")
                manifest = json.load(open(mf, encoding="utf-8")) if os.path.isfile(mf) else dict(rb)
                summ = _evidence_summary("analyser", manifest)
                if summ:
                    summ["metrics"].setdefault("deposit_type", deposit_type)
                    t["summary"] = summ
            except Exception:
                pass
        t.update(status="completed", progress=100)
    except Exception as e:
        t.update(status="failed", error=str(e))


def _run_stru(task_id, kml_bytes, kml_name, bbox=None, tenant_id=None, delivery_id=None):
    t = _ADAPTER_TASKS[task_id]
    try:
        base = services.base_url("stru")
        with httpx.Client(timeout=600.0, headers=_internal_headers(tenant_id, delivery_id)) as c:
            ua = c.post(f"{base}/api/upload_area",
                        files={"file": (kml_name, kml_bytes, _KML_MIME)}).json()
            if not ua.get("success"):
                raise RuntimeError(ua.get("message", "upload_area 失败"))
            resolved = ua.get("resolved") or {}
            pname = resolved.get("project_name")
            if not pname:
                pname = _safe_stem(kml_name)
                if not pname:
                    raise RuntimeError("交付库未匹配到项目(KML 文件名需=交付目录名)")
                t["progress"] = 10
                t["note"] = "交付库缺项目目录,自动创建并补 DEM(Copernicus GLO-30)…"
                try:
                    _acquire_dem(bbox, pname)
                except Exception as e:
                    t.update(status="failed", error=f"构造缺交付项目/DEM,自动补失败:{e}"); return
                resolved = {"project_name": pname, "dem_available": True}
            # 缺 DEM → 自动下 Copernicus GLO-30 写入交付库冬季子目录,再跑
            if not resolved.get("dem_available"):
                t["progress"] = 10
                t["note"] = "自动补 DEM(Copernicus GLO-30)…"
                try:
                    _acquire_dem(bbox, pname)
                except Exception as e:
                    t.update(status="failed", error=f"构造缺 DEM,自动补失败:{e}"); return
            t["progress"] = 25
            st = c.post(f"{base}/api/start", json={"file_path": ua["file_path"], "project_name": pname}).json()
            if not st.get("success"):
                raise RuntimeError(st.get("message", "stru 启动失败"))
            sid = st.get("task_id")
            for _ in range(150):
                time.sleep(2)
                tk = c.get(f"{base}/api/status/{sid}").json()
                tk = tk.get("task") if isinstance(tk.get("task"), dict) else tk
                stt = str(tk.get("status", "")).lower()
                t["progress"] = max(20, min(95, int(tk.get("progress", 0) or t["progress"])))
                if stt in ("completed", "done", "success"):
                    break
                if stt in ("failed", "error"):
                    raise RuntimeError(tk.get("error") or "stru 失败")
            # 记录构造代表栅格(优先 distance/density),经 stru /api/result 代理取
            try:
                meta = c.get(f"{base}/api/result/{sid}/metadata.json").json()
                prods = meta.get("products", {})
                rel = None
                for pref in ("distance", "density"):
                    for k, v in prods.items():
                        if isinstance(v, str) and v.endswith(".tif") and pref in (k + v).lower():
                            rel = v; break
                    if rel:
                        break
                if not rel:
                    rel = next((v for v in prods.values() if isinstance(v, str) and v.endswith(".tif")), None)
                if rel:
                    t["layer"] = {"kind": "proxy", "service": "stru", "task": sid, "rel": rel}
                summ = _evidence_summary("stru", meta)
                if summ:
                    t["summary"] = summ
            except Exception:
                pass
        t.update(status="completed", progress=100)
    except Exception as e:
        t.update(status="failed", error=str(e))


_GEO_INSAR_DOWNLOADS = os.environ.get("GEO_INSAR_DOWNLOADS", "/opt/deepexplor-services/geo-insar/downloads")
# InSAR 触发参数:SAR 搜索窗(默认最近 12 个月)、max_pairs(控 HyP3 配额)、dry_run(调试不耗配额)
_INSAR_SEARCH_DAYS = int(os.environ.get("INSAR_SEARCH_DAYS", "365"))
_INSAR_MAX_PAIRS = int(os.environ.get("INSAR_MAX_PAIRS", "20"))
_INSAR_DRY_RUN = os.environ.get("INSAR_DRY_RUN", "") in ("1", "true", "True")
_DC_DIR = os.environ.get("DATACOLLE_DIR", "/opt/deepexplor-services/data-colle/prospector")
_EMAG2_LOCAL = os.path.join(_DC_DIR, "cache", "emag2_upcont_global.tif")
# data-colle 依赖(rasterio 等)在系统 python,而非 BFF venv;用系统解释器跑
_DC_PYTHON = os.environ.get("DATACOLLE_PYTHON", "/usr/bin/python3")
_DEFAULT_DELIVERY_ROOT = "/Volumes/大硬盘可劲用/DeepExplor/数据留存/交付数据"
_DELIVERY_ROOT = os.environ.get("DELIVERY_ROOT", _DEFAULT_DELIVERY_ROOT)
_DELIVERY_FALLBACK_ROOT = os.environ.get("DELIVERY_FALLBACK_ROOT", str(store.DATA_DIR / "delivery"))
_FETCH_DEM = str(Path(__file__).resolve().parent.parent / "scripts" / "fetch_dem.py")
_FETCH_BASEMAP = str(Path(__file__).resolve().parent.parent / "scripts" / "fetch_basemap.py")
_FETCH_TERRAIN = str(Path(__file__).resolve().parent.parent / "scripts" / "fetch_terrain.py")
_PORTAL_CACHE = Path(os.environ.get("PORTAL_CACHE", str(Path(tempfile.gettempdir()) / "geo-portal-cache")))
_MODEL3D_RESULT_ROOTS = [
    os.environ.get("GEO_MODEL3D_RESULTS", ""),
    "/opt/Project/deepexplor-services/geo-model3d/results",
    "/opt/deepexplor-services/geo-model3d/results",
]
_DRILL_RESULT_ROOTS = [
    os.environ.get("GEO_DRILL_RESULTS", ""),
    "/opt/Project/deepexplor-services/geo-drill/results",
    "/opt/deepexplor-services/geo-drill/results",
]


def _sys_env():
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME")}
    env["PATH"] = "/usr/bin:/usr/local/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def _can_write_dir(path: str) -> bool:
    if not path:
        return False
    try:
        root = Path(path).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".portal-write-", dir=str(root), delete=True) as f:
            f.write(b"ok")
        return True
    except Exception:
        return False


def _delivery_root() -> str:
    """返回可写交付库根目录;默认移动硬盘不可写时落到门户本地交付缓存。"""
    candidates = []
    for raw in (_DELIVERY_ROOT, _DELIVERY_FALLBACK_ROOT):
        if raw and raw not in candidates:
            candidates.append(raw)
    for raw in candidates:
        if _can_write_dir(raw):
            return str(Path(raw).expanduser())
    raise RuntimeError(f"无可写交付库目录: {', '.join(candidates) or '未配置'}")


def _acquire_dem(bbox, project_name):
    """缺 DEM 时:下 Copernicus GLO-30 → 镶嵌 → 写交付库冬季子目录 dem.tif(供 stru 读)。"""
    if not bbox or len(bbox) < 4:
        raise RuntimeError("缺 AOI bbox,无法下 DEM")
    root = _delivery_root()
    proj_dir = os.path.join(root, project_name)
    os.makedirs(proj_dir, exist_ok=True)
    cmd = [_DC_PYTHON, _FETCH_DEM, str(bbox[0]), str(bbox[1]), str(bbox[2]), str(bbox[3]), proj_dir]
    env = _sys_env()
    env["DELIVERY_ROOT"] = root
    r = subprocess.run(cmd, env=env, timeout=600, capture_output=True)
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "ignore")[-300:]
        raise RuntimeError(f"DEM 获取失败(码 {r.returncode}): {tail or 'NO_TILES(可能海域/无覆盖)'}")


def _acquire_datacolle(kml_bytes, kml_name, mineral):
    """缺物探数据时:拉 data-colle 离线裁剪 EMAG2 磁 + ICGEM 重力(本地全球 EMAG2,快)。
    产物落 data-colle/prospector/output,geophys 经 datacolle_broker 按 bbox 发现。"""
    zh = _MINERAL_ZH.get(mineral, "金")
    tmp = os.path.join(tempfile.gettempdir(), kml_name or "aoi.kml")
    with open(tmp, "wb") as f:
        f.write(kml_bytes)
    cmd = [_DC_PYTHON, "prospector.py", "--roi", tmp, "--mineral", zh, "--download"]
    if os.path.isfile(_EMAG2_LOCAL):
        cmd += ["--emag2-file", _EMAG2_LOCAL]
    r = subprocess.run(cmd, cwd=_DC_DIR, env=_sys_env(), timeout=420, capture_output=True)
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "ignore")[-300:]
        raise RuntimeError(f"data-colle 退出码 {r.returncode}: {tail}")


def _safe_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    return re.sub(r'[\\/:*?"<>|]+', "_", stem).strip()


def _bbox_contains_pt(bbox, lon, lat, margin=0.02):
    b = _as_bbox(bbox)
    if not b or lon is None or lat is None:
        return False
    return (b[0] - margin) <= lon <= (b[2] + margin) and (b[1] - margin) <= lat <= (b[3] + margin)


def _model3d_run_in_bbox(run_dir, bbox):
    """该 model3d 产物的靶点是否落在项目 ROI 内(过半即算同区)。
    用于名称不匹配时按 bbox 复用——避免误取相距上千 km 的别项目靶点。"""
    try:
        d = json.loads((run_dir / "targets_3d.json").read_text(encoding="utf-8"))
        tg = d.get("targets", []) if isinstance(d, dict) else d
    except Exception:
        return False
    if not tg:
        return False
    hits = sum(1 for t in tg if isinstance(t, dict) and _bbox_contains_pt(bbox, t.get("lon"), t.get("lat")))
    return hits >= max(1, len(tg) // 2)


def _mineral_main_zh(mineral: str) -> str:
    """项目矿种 → 匹配产物矿床类型的主字;多金属/未识别返回 '' 表示不设矿种门控。"""
    if not mineral or mineral == "multi_mineral":
        return ""
    zh = _MINERAL_ZH.get(mineral, "")
    return zh[0] if zh else ""


def _product_mineral_ok(run_dir, mineral_main: str) -> bool:
    """产物 model_stats.deposit_type 是否含本项目矿种主字。
    用于 bbox 复用的矿种门控:同一 ROI 不同矿种时,避免借用别矿种的三维/布孔产物。
    mineral_main 为空(多金属/未知)→ 放行;deposit_type 缺失 → 无法确认矿种,保守不复用。"""
    if not mineral_main:
        return True
    try:
        meta = json.loads((Path(run_dir) / "metadata.json").read_text(encoding="utf-8"))
        dep = str((meta.get("model_stats") or {}).get("deposit_type") or "")
    except Exception:
        dep = ""
    return mineral_main in dep


def _find_existing_model3d(project: dict):
    """按项目 KML/aoi 名称发现最新 model3d 靶点产物。"""
    names = [_safe_stem(project.get("kml_name")), _safe_stem(project.get("name"))]
    mineral_main = _mineral_main_zh(project.get("mineral"))
    roots = [Path(p) for p in _MODEL3D_RESULT_ROOTS if p]
    for root in roots:
      if not root.is_dir():
          continue
      candidates = []
      for name in [n for n in names if n]:
          mdir = root / name / "model3d"
          if mdir.is_dir():
              candidates.extend([d for d in mdir.iterdir() if d.is_dir()])
      if not candidates and project.get("aoi_bbox"):
          # 名称不匹配 → 按 bbox 复用:仅取靶点落在本 ROI 内、且矿种(成因族)一致的产物
          # (此前不校验矿种 → 同一 ROI 不同矿种会借到别矿种的靶点/成因族/权重)
          bbox = project.get("aoi_bbox")
          candidates.extend(d for d in root.glob("*/model3d/*")
                            if d.is_dir() and _model3d_run_in_bbox(d, bbox) and _product_mineral_ok(d, mineral_main))
      candidates = sorted([d for d in candidates if (d / "targets_3d.json").exists()],
                          key=lambda d: d.name, reverse=True)
      for run_dir in candidates:
          try:
              targets = json.loads((run_dir / "targets_3d.json").read_text(encoding="utf-8"))
              metadata = {}
              if (run_dir / "metadata.json").exists():
                  metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
              return {
                  "ok": True,
                  "aoi_name": run_dir.parent.parent.name,
                  "run_id": run_dir.name,
                  "targets": targets.get("targets", []) if isinstance(targets, dict) else [],
                  "stats": metadata.get("model_stats", {}) if isinstance(metadata, dict) else {},
                  "products": metadata.get("products", {}) if isinstance(metadata, dict) else {},
              }
          except Exception:
              continue
    return {"ok": False, "reason": "未发现三维靶点产物"}


def _drill_run_in_bbox(run_dir, bbox):
    """该布孔产物的钻孔是否落在项目 ROI 内(外包 bbox 与 ROI 相交即算同区)。"""
    try:
        gj = json.loads((run_dir / "planned_holes.geojson").read_text(encoding="utf-8"))
    except Exception:
        return False
    hb = _bbox_from_geojson(gj)
    return bool(hb) and _bbox_cover_frac(hb, bbox) > 0


def _latest_drill_run_dir(project: dict):
    """按项目 KML/aoi 名称定位最新 AI 布孔产物目录(含 planned_holes.geojson)。无则 None。"""
    names = [_safe_stem(project.get("kml_name")), _safe_stem(project.get("name"))]
    mineral_main = _mineral_main_zh(project.get("mineral"))
    for root in [Path(p) for p in _DRILL_RESULT_ROOTS if p]:
        if not root.is_dir():
            continue
        candidates = []
        for name in [n for n in names if n]:
            ddir = root / name / "drill"
            if ddir.is_dir():
                candidates.extend([d for d in ddir.iterdir() if d.is_dir()])
        if not candidates and project.get("aoi_bbox"):
            # 名称不匹配 → 按 bbox 复用:仅取钻孔落在本 ROI 内、且矿种一致的产物(否则借到别区/别矿种钻孔)
            bbox = project.get("aoi_bbox")
            candidates.extend(d for d in root.glob("*/drill/*")
                              if d.is_dir() and _drill_run_in_bbox(d, bbox) and _product_mineral_ok(d, mineral_main))
        candidates = sorted([d for d in candidates if (d / "planned_holes.geojson").exists()],
                            key=lambda d: d.name, reverse=True)
        if candidates:
            return candidates[0]
    return None


def _find_existing_drill(project: dict):
    """按项目 KML/aoi 名称发现最新 AI 布孔产物(planned_holes.geojson)。
    供前端刷新后恢复钻孔(否则只有 model3d 靶点恢复、钻孔丢失或陈旧)。"""
    run_dir = _latest_drill_run_dir(project)
    if run_dir is None:
        return {"ok": False, "reason": "未发现布孔产物"}
    try:
        holes = json.loads((run_dir / "planned_holes.geojson").read_text(encoding="utf-8"))
        fb = {"features": []}
        fbp = run_dir / "drill_feedback.geojson"
        if fbp.exists():
            fb = json.loads(fbp.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "aoi_name": run_dir.parent.parent.name,
            "run_id": run_dir.name,
            "holes": holes.get("features", []) if isinstance(holes, dict) else [],
            "feedback": fb.get("features", []) if isinstance(fb, dict) else [],
        }
    except Exception:
        return {"ok": False, "reason": "布孔产物解析失败"}


def _geophys_once(c, kml_bytes, kml_name, mineral, t):
    """跑一次 geophys /api/start + 轮询;返回 (status, sid, task)。"""
    j = c.post(f"{services.base_url('geophys')}/api/start",
               files={"file": (kml_name, kml_bytes, _KML_MIME)},
               data={"mineral": mineral}).json()
    sid = j.get("task_id")
    if not sid:
        return ("fail", None, {"error": j.get("message", "geophys 启动失败")})
    for _ in range(150):
        time.sleep(2)
        tk = c.get(f"{services.base_url('geophys')}/api/status/{sid}").json()
        tk = tk.get("task") if isinstance(tk.get("task"), dict) else tk
        st = str(tk.get("status", "")).lower()
        t["progress"] = max(20, min(95, int(tk.get("progress", 0) or t["progress"])))
        if st in ("completed", "done", "success"):
            return ("ok", sid, tk)
        if st in ("failed", "error"):
            return ("fail", sid, tk)
    return ("timeout", sid, {})


def _geophys_layer_rel(c, sid):
    """从 geophys 结果 metadata.products 挑一张代表栅格供 2D 叠图。
    优先磁 RTP/解析信号,其次重力,再退任意 .tif。该区可能只有重力(如西非无 EMAG2 磁覆盖),
    故不能写死 magnetic_rtp.tif。"""
    try:
        meta = c.get(f"{services.base_url('geophys')}/api/result/{sid}/metadata.json").json()
        prods = meta.get("products", {}) or {}
    except Exception:
        return None
    tifs = [v for v in prods.values() if isinstance(v, str) and v.endswith(".tif")]
    _PREF = ("magnetic_rtp", "rtp", "analytic", "tilt_derivative", "euler", "magnetic",
             "gravity_tilt", "gravity", "tilt")
    def _rank(rel):
        s = rel.lower()
        for i, k in enumerate(_PREF):
            if k in s:
                return len(_PREF) - i
        return 0
    tifs.sort(key=_rank, reverse=True)
    return tifs[0] if tifs else None


def _run_geophys(task_id, kml_bytes, kml_name, mineral, tenant_id=None):
    """物探:先跑 geophys;若因缺磁/重失败 → 自动拉 data-colle 补数据 → 重跑。"""
    t = _ADAPTER_TASKS[task_id]
    try:
        with httpx.Client(timeout=600.0, headers=_internal_headers(tenant_id)) as c:
            status, sid, tk = _geophys_once(c, kml_bytes, kml_name, mineral, t)
            if status == "fail":
                t["progress"] = 8
                t["note"] = "自动补充物探数据(data-colle 裁剪 EMAG2/重力)…"
                try:
                    _acquire_datacolle(kml_bytes, kml_name, mineral)
                except Exception as e:
                    t.update(status="failed", error=f"补数据失败:{e}"); return
                status, sid, tk = _geophys_once(c, kml_bytes, kml_name, mineral, t)
            if status == "ok":
                # 按实际产物挑代表栅格(磁/重哪个有挑哪个),不写死 magnetic_rtp.tif
                rel = _geophys_layer_rel(c, sid)
                if rel:
                    t["layer"] = {"kind": "proxy", "service": "geophys", "task": sid, "rel": rel}
                try:
                    gmeta = c.get(f"{services.base_url('geophys')}/api/result/{sid}/metadata.json").json()
                    summ = _evidence_summary("geophys", gmeta)
                    if summ:
                        t["summary"] = summ
                except Exception:
                    pass
                t.update(status="completed", progress=100)
            else:
                t.update(status="failed", error=(tk or {}).get("error") or "物探失败(补数据后仍无磁/重)")
    except Exception as e:
        t.update(status="failed", error=str(e))


def _norm_lookup_name(name: str) -> str:
    return re.sub(r"[\s_\-（）()【】\[\]·.]+", "", _safe_stem(name).lower())


# ── bbox 工具:同一 AOI 的产物按 bbox 复用(项目名≠数据目录名时不再重算/重下) ──
def _as_bbox(v):
    if isinstance(v, (list, tuple)) and len(v) == 4:
        try:
            x0, y0, x1, y1 = (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
            return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        except (TypeError, ValueError):
            return None
    return None


def _bbox_from_geojson(gj):
    """从 geojson 几何/Feature 取所有坐标的外包 bbox(纯 JSON,无需 rasterio)。"""
    xs, ys = [], []

    def walk(o):
        if isinstance(o, (list, tuple)):
            if len(o) == 2 and all(isinstance(n, (int, float)) for n in o):
                xs.append(o[0]); ys.append(o[1])
            else:
                for e in o:
                    walk(e)
        elif isinstance(o, dict):
            for k in ("geometry", "coordinates", "features", "geometries"):
                if k in o:
                    walk(o[k])
    walk(gj)
    return [min(xs), min(ys), max(xs), max(ys)] if xs and ys else None


def _bbox_cover_frac(outer, target):
    """outer 覆盖 target 的面积比例(交 / target 面积)。同一 AOI≈1。"""
    o, t = _as_bbox(outer), _as_bbox(target)
    if not o or not t:
        return 0.0
    ix0, iy0 = max(o[0], t[0]), max(o[1], t[1])
    ix1, iy1 = min(o[2], t[2]), min(o[3], t[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    tarea = (t[2] - t[0]) * (t[3] - t[1])
    return inter / tarea if tarea > 0 else 0.0


_INSAR_DL_ROOTS = None


def _insar_download_roots():
    global _INSAR_DL_ROOTS
    if _INSAR_DL_ROOTS is None:
        roots, seen = [], set()
        for r in (_GEO_INSAR_DOWNLOADS,
                  "/opt/Project/deepexplor-services/geo-insar/downloads",
                  "/opt/deepexplor-services/geo-insar/downloads"):
            if r and str(r) not in seen:
                seen.add(str(r)); roots.append(Path(r))
        _INSAR_DL_ROOTS = roots
    return _INSAR_DL_ROOTS


def _find_insar_evidence_by_bbox(project, min_frac=0.7, min_reverse=0.25):
    """名字匹配失败时,按 AOI bbox 复用同一区现成形变图(读 insar_metadata.json 的 aoi_bbox)。

    双向门控:
    - forward(产物覆盖 ROI ≥ min_frac):产物足迹要盖住本 ROI;
    - reverse(ROI 覆盖产物 ≥ min_reverse):产物足迹不能比 ROI 大太多(≈ ≤4× ROI 面积)。
    避免拆分后的小子区(如铜钼)误复用横跨多块的合并区产物——那种 reverse≈0,
    会被拒,从而逼出该 ROI 的独立 InSAR 处理(数据阶段),而非区域级复用。"""
    bbox = _as_bbox((project or {}).get("aoi_bbox"))
    if not bbox:
        return None, None
    best = None
    for root in _insar_download_roots():
        if not root.is_dir():
            continue
        for tif in root.glob("*/deformation_evidence.tif"):
            meta = tif.parent / "insar_metadata.json"
            try:
                ab = _as_bbox(json.loads(meta.read_text(encoding="utf-8")).get("aoi_bbox")) if meta.is_file() else None
            except Exception:
                ab = None
            if not ab:
                continue
            frac = _bbox_cover_frac(ab, bbox)        # 产物 → 盖住 ROI
            rev = _bbox_cover_frac(bbox, ab)         # ROI → 盖住产物(产物是否过大/错位)
            score = min(frac, rev)
            if frac >= min_frac and rev >= min_reverse and (best is None or score > best[2]):
                best = (str(tif), tif.parent.name, score)
    return (best[0], best[1]) if best else (None, None)


_ANALYSER_RESULT_ROOTS = [
    os.environ.get("GEO_ANALYSER_RESULTS", ""),
    "/opt/Project/deepexplor-services/geo-analyser/results",
    "/opt/deepexplor-services/geo-analyser/results",
]


def _alteration_repr_tif(run_dir):
    """从蚀变产物目录挑代表性栅格(优先 composite/score/intersection)。"""
    tifs = glob.glob(os.path.join(str(run_dir), "**", "*.tif"), recursive=True)

    def _sc(p):
        s = os.path.basename(p).lower()
        for i, k in enumerate(("composite", "score", "intersection")):
            if k in s:
                return 10 - i
        return 0
    return sorted(tifs, key=_sc, reverse=True)[0] if tifs else None


def _find_alteration_by_bbox(aoi_bbox, mineral, min_frac=0.7):
    """按 AOI bbox + 矿种复用现成蚀变产物(读 manifest.roi_geojson 的外包 bbox)。
    返回 (代表性栅格路径, run_dir名, deposit_type) 或 (None,None,None)。
    项目名≠数据目录名、但同一 AOI 已算过蚀变时,直接复用,不再重算/卡死。"""
    bbox = _as_bbox(aoi_bbox)
    if not bbox:
        return None, None, None
    zh = _MINERAL_ZH.get(mineral, "")
    best = None
    for root in [Path(p) for p in _ANALYSER_RESULT_ROOTS if p]:
        if not root.is_dir():
            continue
        for mani in root.glob("*/*/manifest.json"):
            try:
                m = json.loads(mani.read_text(encoding="utf-8"))
            except Exception:
                continue
            ab = _bbox_from_geojson(m.get("roi_geojson"))
            if not ab or _bbox_cover_frac(ab, bbox) < min_frac:
                continue
            dep = str(m.get("deposit_type") or "")
            if zh and zh[0] not in dep:   # 矿种安全:矿床类型须含本项目矿种主字,避免跨矿种误用
                continue
            frac = _bbox_cover_frac(ab, bbox)
            if best is None or frac > best[1]:
                rt = _alteration_repr_tif(mani.parent)
                if rt:
                    best = (rt, frac, mani.parent.name, dep)
    return (best[0], best[2], best[3]) if best else (None, None, None)


def _find_insar_evidence(kml_name: str = "", project: dict = None):
    """按原始 KML 名/项目名发现现成 deformation_evidence.tif。"""
    project = project or {}
    names = [_safe_stem(kml_name), _safe_stem(project.get("kml_name")), _safe_stem(project.get("name"))]
    roots = [
        Path(_GEO_INSAR_DOWNLOADS),
        Path("/opt/Project/deepexplor-services/geo-insar/downloads"),
        Path("/opt/deepexplor-services/geo-insar/downloads"),
    ]
    seen = set()
    roots = [r for r in roots if not (str(r) in seen or seen.add(str(r)))]

    for root in roots:
        if not root.is_dir():
            continue
        for name in [n for n in names if n]:
            direct = root / name / "deformation_evidence.tif"
            if direct.is_file():
                return str(direct), name

        wanted = [n for n in {_norm_lookup_name(n) for n in names if n}]
        if not wanted:
            continue
        for tif in root.glob("*/deformation_evidence.tif"):
            folder = _norm_lookup_name(tif.parent.name)
            if any(folder == w for w in wanted):
                return str(tif), tif.parent.name
    # 名字都不匹配 → 按 AOI bbox 复用同一区现成形变图(新项目=旧 AOI 时不再重下)
    return _find_insar_evidence_by_bbox(project)


def _find_insar_fusion_evidence(project: dict = None):
    """发现 geo-stru insar_fusion 形变层(同区构造-形变融合,含活动断裂+采空沉降)。

    经 commons.insar_fusion_broker 按项目 aoi_bbox 发现,返回 georeferenced 形变栅格
    (los_velocity_mm_yr.tif,缺则 velocity_gradient.tif)+ 采空/活动断裂摘要。
    作为 insar 证据层的优先来源(比 geo-insar 原始 LOS 更富:叠加构造耦合与采空识别)。
    返回 (tif_path, aoi_name, summary) 或 (None, None, None);任何缺失/失败均降级。
    """
    project = project or {}
    try:
        import sys as _sys
        for repo in ("/opt/Project/deepexplor-services", "/opt/deepexplor-services"):
            if repo not in _sys.path:
                _sys.path.insert(0, repo)
        from commons.insar_fusion_broker import find_insar_fusion_for_bbox, get_product_path
    except Exception:
        return None, None, None
    bbox = _as_bbox(project.get("aoi_bbox"))
    if not bbox:
        return None, None, None
    try:
        matches = find_insar_fusion_for_bbox(tuple(bbox))
    except Exception:
        return None, None, None
    if not matches:
        return None, None, None
    entry = matches[0]
    path = (get_product_path(entry, "los_velocity_mm_yr")
            or get_product_path(entry, "velocity_gradient"))
    if not path:
        return None, None, None
    fs = entry.get("fusion_stats") or {}
    summary = {
        "n_subsidence_clusters": fs.get("n_subsidence_clusters"),
        "n_active_lineaments": fs.get("n_active_consistent_lineaments"),
        "signal_quality": fs.get("signal_quality"),
    }
    return str(path), entry.get("aoi_name"), summary


def _find_insar_evidence_by_aoi(aoi_name):
    """按 geo-insar 输出目录名(aoi)直接找 deformation_evidence.tif。"""
    if not aoi_name:
        return None
    for root in (Path(_GEO_INSAR_DOWNLOADS),
                 Path("/opt/Project/deepexplor-services/geo-insar/downloads"),
                 Path("/opt/deepexplor-services/geo-insar/downloads")):
        p = root / aoi_name / "deformation_evidence.tif"
        if p.is_file():
            return str(p)
    return None


def _clip_tif_to_project_bytes(src_bytes: bytes, project_id: str):
    """按项目 bbox 裁剪 GeoTIFF,返回 (bytes, meta)。BFF venv 不带 rasterio,调系统 python。

    meta = {scope, bounds}:
    - scope='aoi'    :正常裁到 AOI(bounds=AOI bbox);
    - scope='regional':裁到 AOI 后退化(全 nodata 或 ≤1px,如区域级粗网格/本区无信号)→ 回退
      输出整幅原生栅格,bounds=该栅格的 WGS84 范围,供前端按真实范围摆放并标注"区域级"。
    """
    proj = store.get_project(project_id) if project_id else None
    bbox = _as_bbox((proj or {}).get("aoi_bbox"))
    if not bbox:
        return src_bytes, {"scope": "native", "bounds": None}
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as src:
        src.write(src_bytes)
        src_path = src.name
    out_path = src_path + ".clip.tif"
    script = r'''
import json, sys
import rasterio
from rasterio.windows import from_bounds, Window
from rasterio.warp import transform_bounds

src_path, out_path, bbox_json = sys.argv[1], sys.argv[2], sys.argv[3]
bbox = json.loads(bbox_json)
with rasterio.open(src_path) as ds:
    if not ds.crs:
        raise SystemExit(4)
    left, bottom, right, top = transform_bounds("EPSG:4326", ds.crs, *bbox, densify_pts=21)
    win = from_bounds(left, bottom, right, top, ds.transform)
    full = Window(0, 0, ds.width, ds.height)
    cwin = None
    try:
        cwin = win.intersection(full).round_offsets().round_lengths()
    except Exception:
        cwin = None
    degenerate = cwin is None or cwin.width <= 1 or cwin.height <= 1
    if not degenerate:
        sub = ds.read(window=cwin, masked=True)
        try:
            degenerate = int(sub.count()) == 0   # 裁出来全是 nodata
        except Exception:
            degenerate = False
    if not degenerate:
        data = ds.read(window=cwin)
        profile = ds.profile.copy()
        profile.update({"height": int(cwin.height), "width": int(cwin.width),
                        "transform": ds.window_transform(cwin)})
        with rasterio.open(out_path, "w", **profile) as out:
            out.write(data)
        print(json.dumps({"scope": "aoi", "bounds": bbox}))
    else:
        # 回退:输出整幅原生栅格 + 其 WGS84 范围
        with rasterio.open(out_path, "w", **ds.profile) as out:
            out.write(ds.read())
        l, b, r, t = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21)
        print(json.dumps({"scope": "regional", "bounds": [l, b, r, t]}))
'''
    try:
        r = subprocess.run(["python3", "-c", script, src_path, out_path, json.dumps(bbox)],
                           timeout=60, capture_output=True)
        if r.returncode != 0 or not os.path.isfile(out_path):
            return src_bytes, {"scope": "native", "bounds": None}
        try:
            meta = json.loads((r.stdout or b"").decode("utf-8", "ignore").strip().splitlines()[-1])
        except Exception:
            meta = {"scope": "aoi", "bounds": bbox}
        with open(out_path, "rb") as f:
            return f.read(), meta
    finally:
        for p in (src_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


_INSAR_POSTPROCESS_GRACE_SECONDS = int(os.environ.get("INSAR_POSTPROCESS_GRACE_SECONDS", "900"))


def _epoch_from_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _insar_artifact_ready_without_layer(task):
    """geo-insar 已完成但没有 AOI 级形变证据栅格时,判断是否应停止等待。

    task=done 后 geo-insar 会异步跑 postprocess/SBAS/deformation_evidence。若已有 stack/SBAS
    产物且超过宽限期仍无 deformation_evidence.tif,通常代表 ROI 内无有效形变像元或 SBAS
    无法生成 AOI 级证据,此时 Portal 应记录 no_layer,不要让前端长期卡在 92%。
    """
    artifacts = task.get("artifacts") or {}
    if artifacts.get("sbas"):
        return True
    updated_at = _epoch_from_iso(task.get("updated_at") or task.get("created_at"))
    if updated_at and time.time() - updated_at >= _INSAR_POSTPROCESS_GRACE_SECONDS:
        return bool(artifacts.get("stack_index"))
    return False


def _inspect_insar_aoi(project):
    if not project or not project.get("id"):
        return None
    base = services.base_url("insar")
    kml_bytes, fname = _kml_bytes_and_name(project["id"])
    with httpx.Client(timeout=120.0, headers=_internal_headers()) as c:
        insp = c.post(f"{base}/api/aoi/inspect", files={"file": (fname, kml_bytes, _KML_MIME)}).json()
    if insp.get("error") or not insp.get("kml_path"):
        raise RuntimeError(insp.get("error") or "geo-insar 无法解析 KML")
    return insp


def _reuse_completed_insar_result(t, project):
    """同 AOI 的 InSAR 已有终态时直接复用,避免重复提交 HyP3。"""
    try:
        insp = _inspect_insar_aoi(project)
    except Exception:
        return False
    aoi = (insp or {}).get("aoi_name")
    if not aoi:
        return False
    for row in store.find_adapter_tasks({"insar_aoi": aoi}, limit=20):
        data = dict(row.get("data") or {})
        if data.get("status") != "completed":
            continue
        layer = data.get("layer")
        if layer:
            t["layer"] = layer
        for key in ("raw", "warning", "no_layer", "matched_aoi", "insar_stack_index", "insar_task_id", "insar_aoi"):
            if key in data:
                t[key] = data[key]
        t.update(status="completed", progress=100, note=None, error=None, reused_from=row.get("tid"))
        return True
    return False


def _trigger_insar_processing(t, project):
    """缺现成产物时发起 geo-insar 真实处理(Sentinel-1 + HyP3 云,异步数小时)。
    用原始 KML 名上传以对齐产物目录;记 geo-insar task_id,状态后续由 svcstatus 代理。"""
    if not project or not project.get("id"):
        t.update(status="completed", progress=100, no_layer=True,
                 warning="无项目信息,无法发起 InSAR 处理")
        return
    base = services.base_url("insar")
    insp = _inspect_insar_aoi(project)
    with httpx.Client(timeout=120.0, headers=_internal_headers()) as c:
        now = time.time()
        body = {
            "kml_path": insp["kml_path"],
            "start": time.strftime("%Y-%m-%d", time.gmtime(now - _INSAR_SEARCH_DAYS * 86400)),
            "end": time.strftime("%Y-%m-%d", time.gmtime(now)),
            "backend": insp.get("backend_hint", "INSAR_ISCE_BURST"),
            "max_pairs": _INSAR_MAX_PAIRS,
        }
        if _INSAR_DRY_RUN:
            body["dry_run"] = True
        run = c.post(f"{base}/api/run", json=body).json()
    itid = run.get("task_id")
    if not itid:
        raise RuntimeError(run.get("error") or "geo-insar 未返回 task_id")
    t["insar_task_id"] = itid
    t["insar_aoi"] = insp.get("aoi_name")
    t.update(status="running", progress=10, raw="cloud_submitted",
             note="已提交 InSAR 云处理(Sentinel-1 + HyP3),预计数小时,可离开稍后回来")


def _refresh_insar(task_id, t):
    """代理 geo-insar 任务状态 → 映射到适配器 t(状态/进度/产物 layer)并持久化。"""
    itid = t.get("insar_task_id")
    if not itid:
        return t
    try:
        with httpx.Client(timeout=20.0, headers=_internal_headers()) as c:
            task = c.get(f"{services.base_url('insar')}/api/tasks/{itid}").json()
    except Exception:
        return t   # 瞬时网络错误,保持原状继续等
    st = str(task.get("status", "")).lower()
    prog = task.get("progress") or {}
    total, done = prog.get("total") or 0, prog.get("done") or 0
    if st == "error":
        t.update(status="failed", error=task.get("error_msg") or "InSAR 处理失败")
    elif st == "done":
        path = _find_insar_evidence_by_aoi(t.get("insar_aoi"))
        if path:
            t["layer"] = {"kind": "file", "path": path}
            t.update(status="completed", progress=100, note=None, no_layer=False, warning=None)
        elif _insar_artifact_ready_without_layer(task):
            artifacts = task.get("artifacts") or {}
            if artifacts.get("stack_index"):
                t["insar_stack_index"] = artifacts.get("stack_index")
            t.update(status="completed", progress=100, no_layer=True, note=None,
                     warning="HyP3 已完成,但该 AOI 未生成可叠加的形变证据栅格;已保留 InSAR 堆栈产物")
        else:
            t.update(status="running", progress=92, note="HyP3 处理完成,正在后处理生成形变图…")
    else:   # pending/running
        pct = 10 + int(done / total * 80) if total else 15
        t.update(status="running", progress=min(90, pct),
                 note="InSAR 云处理中(HyP3),预计数小时,可离开稍后回来")
    try:
        store.save_adapter_task(task_id, t)
    except Exception:
        pass
    return t


def _reuse_data_stage_insar_result(t: dict, data_task: dict = None) -> bool:
    data_task = data_task or {}
    tid = data_task.get("task_id") or data_task.get("taskId")
    data = None
    if tid:
        data = _ADAPTER_TASKS.get(tid)
        if data is None:
            try:
                data = store.get_adapter_task(tid)
            except Exception:
                data = None
    data = data or data_task
    st = str((data or {}).get("status") or "").lower()
    if st != "completed":
        return False
    for key in ("layer", "raw", "warning", "no_layer", "matched_aoi", "insar_stack_index",
                "insar_task_id", "insar_aoi", "insar_source", "insar_fusion"):
        if key in data:
            t[key] = data[key]
    t.update(status="completed", progress=100, note=None, error=None)
    if tid:
        t["reused_from"] = tid
    return True


def _run_insar(task_id, kml_name, project=None, allow_trigger=False, data_task=None):
    """形变/InSAR:数据阶段可触发 HyP3;证据阶段仅复用已就绪产物,绝不再提交云处理。"""
    t = _ADAPTER_TASKS[task_id]
    try:
        if _reuse_data_stage_insar_result(t, data_task):
            return
        # 优先 geo-stru insar_fusion 融合形变(同区含活动断裂+采空沉降),缺失才回退 geo-insar 原始形变
        fpath, faoi, fsum = _find_insar_fusion_evidence(project)
        if fpath:
            t["layer"] = {"kind": "file", "path": fpath}
            t.update(status="completed", progress=100, warning=None, matched_aoi=faoi,
                     insar_source="geo-stru-insar-fusion", insar_fusion=fsum)
            return
        path, matched = _find_insar_evidence(kml_name, project)
        if path:
            t["layer"] = {"kind": "file", "path": path}
            t.update(status="completed", progress=100, warning=None, matched_aoi=matched)
            try:
                mp = os.path.join(os.path.dirname(path), "insar_metadata.json")
                summ = _evidence_summary("insar", json.load(open(mp, encoding="utf-8"))) if os.path.isfile(mp) else None
                if summ:
                    t["summary"] = summ
            except Exception:
                pass
        elif _reuse_completed_insar_result(t, project):
            return
        elif not allow_trigger:
            st = str((data_task or {}).get("status") or "").lower()
            if st in ("running", "pending"):
                raise RuntimeError("InSAR 数据尚未准备完成,请先完成数据阶段")
            if st == "failed":
                raise RuntimeError((data_task or {}).get("error") or "InSAR 数据准备失败,请在数据阶段重试")
            raise RuntimeError("本 ROI 没有专属形变产物(同区只有覆盖范围差异过大的产物,已按 ROI 收紧匹配不复用);"
                               "请在『数据准备』阶段为本项目单独发起 InSAR(Sentinel-1 + HyP3,数小时),完成后证据阶段会复用该专属产物。")
        else:
            _trigger_insar_processing(t, project)   # 发起真实 InSAR 处理(不阻塞,状态由 svcstatus 代理)
    except Exception as e:
        t.update(status="failed", error=str(e))


def _run_reporter(task_id, kml_bytes, kml_name, mineral, tenant_id=None, econ_params=None):
    """geo-reporter 原生接口是 upload-kml + SSE run;BFF 消费 SSE 并归一为门户任务状态。
    econ_params(经济参数表,来自项目上传)随 upload-kml 透传,供新版报告价值评估章。"""
    t = _ADAPTER_TASKS[task_id]
    _econ_form = {"econ_params": json.dumps(econ_params)} if econ_params else None
    try:
        base = services.base_url("reporter")
        with httpx.Client(timeout=1800.0, headers=_internal_headers(tenant_id)) as c:
            up = c.post(
                f"{base}/api/upload-kml",
                files={"file": (kml_name or "aoi.kml", kml_bytes, _KML_MIME)},
                data=_econ_form,
            )
            if up.status_code >= 400:
                raise RuntimeError(up.text[:300] or "reporter upload-kml 失败")
            uj = up.json()
            rid = uj.get("task_id")
            if not rid:
                raise RuntimeError(uj.get("error") or "reporter 未返回 task_id")
            t.update(progress=12, reporter_task_id=rid, raw="kml_uploaded")

            with c.stream("GET", f"{base}/api/run/{rid}", params={"mineral": mineral or ""}) as r:
                if r.status_code >= 400:
                    raise RuntimeError(r.text[:300] or "reporter run 失败")
                for line in r.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", "ignore")
                    line = str(line)
                    if not line.startswith("data:"):
                        continue
                    try:
                        evt = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if evt.get("error"):
                        raise RuntimeError(evt["error"])
                    if evt.get("warning"):
                        t["warning"] = evt["warning"]
                    step = evt.get("step")
                    msg = evt.get("message") or evt.get("cat_name") or str(step or "")
                    if step == "search_progress":
                        idx = int(evt.get("idx") or 0)
                        total = max(int(evt.get("total") or 1), 1)
                        prog = 25 + int((idx / total) * 35)
                    elif step == "synthesis":
                        prog = 68
                    elif step == "confidence":
                        prog = 76
                    elif step == 4:
                        prog = 84
                    elif step == 5:
                        prog = 100
                    elif isinstance(step, int):
                        prog = min(82, 12 + step * 10)
                    else:
                        prog = min(90, int(t.get("progress") or 12) + 3)
                    t.update(progress=max(int(t.get("progress") or 0), prog), raw=msg)
                    if step == 5:
                        t["download"] = _reporter_download_links(rid)
                        break

            sj = c.get(f"{base}/api/status/{rid}").json()
            if sj.get("status") == "completed" or sj.get("has_report"):
                t.update(status="completed", progress=100, raw="completed")
                t.setdefault("download", _reporter_download_links(rid))
            else:
                raise RuntimeError(f"reporter 未完成: {sj}")
    except Exception as e:
        t.update(status="failed", error=str(e), raw=str(e))


def _norm_status(j: dict) -> dict:
    """把各服务异构的状态响应归一为 {status, progress}。"""
    src = j.get("task") if isinstance(j.get("task"), dict) else j
    raw = str(src.get("status", "")).lower()
    prog = src.get("progress", 0)
    try:
        prog = int(float(prog))
    except (TypeError, ValueError):
        prog = 0
    if raw in ("completed", "done", "success", "succeeded", "finished", "ok"):
        status = "completed"
    elif raw in ("failed", "error", "failure", "aborted"):
        status = "failed"
    elif prog >= 100:
        status = "completed"
    else:
        status = "running"
    return {
        "status": status,
        "progress": prog,
        "raw": raw,
        "step": src.get("step"),
        "trace_id": src.get("trace_id"),
        "error": src.get("error") or src.get("message"),
    }


def _split_csv(value) -> list:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").split(",")
    return [str(x).strip() for x in items if str(x).strip()]


def _downloader_sensors(params: dict) -> list:
    aliases = {"landsat8": "landsat", "landsat9": "landsat", "copernicus_dem": "dem"}
    sensors = _split_csv((params or {}).get("sensors") or (params or {}).get("sensor"))
    out = []
    for sensor in sensors or ["sentinel2", "dem"]:
        mapped = aliases.get(sensor, sensor)
        if mapped not in out:
            out.append(mapped)
    return out


def _default_date_range(years: int = 2) -> tuple:
    end = datetime.utcnow()
    start = end - timedelta(days=365 * years)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _start_downloader(project_id: str, params: dict) -> dict:
    base = services.base_url("downloader")
    kml_bytes, kml_name = _kml_bytes_and_name(project_id)
    with httpx.Client(timeout=90.0, headers=_internal_headers()) as c:
        up = c.post(
            f"{base}/api/upload-kml",
            files={"file": (kml_name, kml_bytes, _KML_MIME)},
        )
        up.raise_for_status()
        uj = up.json()
        if uj.get("error") or not uj.get("path"):
            raise HTTPException(status_code=502, detail=uj.get("error") or "downloader 未返回 KML 路径")
        start, end = (params or {}).get("start"), (params or {}).get("end")
        if not (start and end):
            start, end = _default_date_range()
        task = {
            "kml": uj["path"],
            "sensor": _downloader_sensors(params or {}),
            "start": start,
            "end": end,
        }
        if (params or {}).get("cloud") is not None:
            task["cloud"] = int(params["cloud"])
        r = c.post(f"{base}/api/run", json={"task": task})
        if r.status_code >= 400:
            try:
                detail = r.json().get("error") or r.text
            except Exception:
                detail = r.text
            raise HTTPException(status_code=502, detail=detail or "downloader 启动失败")
        j = r.json()
    task_id = j.get("task_id")
    if not task_id:
        raise HTTPException(status_code=502, detail="downloader 未返回 task_id")
    return {"service": "downloader", "task_id": task_id, "adapter": True, "sensors": task["sensor"]}


_SENSOR_ZH = {"sentinel2": "Sentinel-2 光学", "sentinel1": "Sentinel-1 雷达",
              "landsat": "Landsat", "dem": "DEM 高程", "aster": "ASTER"}


def _downloader_status(task_id: str) -> dict:
    base = services.base_url("downloader")
    try:
        with httpx.Client(timeout=30.0, headers=_internal_headers()) as c:
            j = c.get(f"{base}/api/status").json()
    except (httpx.ConnectError, httpx.ReadTimeout, ValueError):
        raise HTTPException(status_code=503, detail="服务 downloader 不可达")
    task = next((t for t in j.get("tasks") or [] if t.get("task_id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="downloader 任务不存在")
    raw = str(task.get("status") or "").lower()
    prog_map = task.get("progress") or {}
    # 分传感器明细:供前端 HUD 显示"正在下哪个传感器"(光学快、Sentinel-1 雷达单景上 GB 很慢)
    sensors = []
    for name, item in prog_map.items():
        item = item or {}
        ph = str(item.get("phase") or "")
        sensors.append({"sensor": name, "label": _SENSOR_ZH.get(name, name),
                        "phase": ph, "done": item.get("done"), "target": item.get("target"),
                        "finished": ph == "done"})
    active = next((s["label"] for s in sensors if not s["finished"]), "")
    if raw == "done":
        status, progress = "completed", 100
    elif raw in ("error", "failed", "stopped"):
        status, progress = "failed", 100
    else:
        # 按"已完成传感器数 / 传感器总数"估算,running 封顶 95 —— 避免某些传感器
        # target=0 或尚未登记目标(如 Sentinel-1 phase=start)时,被错算成 100% 的假象。
        total = len(sensors)
        done_cnt = sum(1 for s in sensors if s["finished"])
        progress = min(95, int(done_cnt / total * 100)) if total else 5
        status = "running"
    return {"status": status, "progress": progress, "raw": raw, "error": task.get("error"),
            "sensors": sensors, "active_sensor": active}

app = FastAPI(title="DeepExplor Portal BFF", version="0.1.0")

# 开发期允许 vite dev server;生产由同源/网关收敛
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Raster-Scope", "X-Raster-Bounds", "X-Raster-Note"],
)

app.include_router(proxy_router)
app.include_router(admin_router)
app.include_router(public_router)


def _new_trace_id() -> str:
    """复刻 commons.trace 的 trace_id 形态:tr_<UTC分钟>_<hex6>。"""
    ts = time.strftime("%Y%m%dT%H%M", time.gmtime())
    return f"tr_{ts}_{uuid.uuid4().hex[:6]}"


# ─── 认证 ──────────────────────────────────────────────────
class LoginIn(BaseModel):
    username: str
    password: str


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    # refresh:仅 /api 回传,缩小暴露面
    response.set_cookie(
        key=_settings.refresh_cookie_name, value=refresh,
        max_age=_settings.refresh_ttl_seconds, httponly=True,
        secure=_settings.cookie_secure, samesite=_settings.cookie_samesite,
        path="/api",
    )
    # access:全站(/svc 子资源需要),HttpOnly + 短时;axios 仍优先用 Bearer 头
    response.set_cookie(
        key=_settings.access_cookie_name, value=access,
        max_age=_settings.access_ttl_seconds, httponly=True,
        secure=_settings.cookie_secure, samesite=_settings.cookie_samesite,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=_settings.refresh_cookie_name, path="/api")
    response.delete_cookie(key=_settings.access_cookie_name, path="/")


@app.post("/api/login")
def login(body: LoginIn, response: Response, request: Request):
    ip = request.client.host if request.client else ""
    user = auth.authenticate(body.username, body.password)
    if not user:
        store.write_audit("login.fail", target_type="username", target_id=body.username, ip=ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    access, refresh = auth.issue_pair(user)
    store.touch_last_login(user["id"])
    store.write_audit("login.ok", actor_user_id=user["id"], tenant_id=user["tenant_id"], ip=ip)
    _set_auth_cookies(response, access, refresh)
    return {"token": access, "user": _pub_user(user)}


@app.post("/api/refresh")
def refresh(response: Response, portal_refresh: Optional[str] = Cookie(default=None)):
    """用 HttpOnly refresh cookie 换新 access(并旋转 refresh)。"""
    if not portal_refresh:
        raise HTTPException(status_code=401, detail="未登录")
    rotated = auth.rotate_refresh(portal_refresh)
    if not rotated:
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="登录已失效,请重新登录")
    user, access, new_refresh = rotated
    _set_auth_cookies(response, access, new_refresh)
    return {"token": access, "user": _pub_user(user)}


@app.post("/api/logout")
def logout(response: Response, portal_refresh: Optional[str] = Cookie(default=None)):
    if portal_refresh:
        auth.revoke_refresh(portal_refresh)
    _clear_auth_cookies(response)
    return {"ok": True}


def _pub_user(u):
    t = store.get_tenant(u["tenant_id"]) or {}
    return {"id": u["id"], "username": u["username"], "display": u["display"],
            "tenant_id": u["tenant_id"], "tenant_name": t.get("name", ""),
            "tenant_role": u["tenant_role"]}


@app.get("/api/me")
def me(user=Depends(auth.current_user)):
    return _pub_user(user)


@app.get("/api/services")
def list_services(user=Depends(auth.current_user)):
    """工具箱/独立模式:服务清单(不含内网地址,前端经 /svc 访问)。"""
    out = {}
    for name, meta in services.all_services().items():
        out[name] = {k: v for k, v in meta.items() if k != "base_url"}
    return out


# ─── 项目 ──────────────────────────────────────────────────
class ProjectIn(BaseModel):
    name: str
    mineral: str
    mineral_label: str = ""
    aoi_bbox: Optional[List[float]] = None


@app.get("/api/projects")
def get_projects(user=Depends(auth.current_user)):
    projects = store.list_projects(user["tenant_id"], user["id"])
    for p in projects:
        # 卡片进度=当前在用 run(current_run)的真实进度(=你正在做的这次);
        # 若历史有更靠前的 run,附 best_percent/best_done 供前端标注"历史最远 N%"。
        cr = p.get("current_run")
        runs = store.runs_for_project(p["id"]) if cr else []
        cur_run = next((r for r in runs if r.get("trace_id") == cr), None) or (runs[0] if runs else None)
        prog = summarize_progress((cur_run or {}).get("stages") or {}) if cur_run else None
        if prog:
            best = prog
            for run in runs:
                q = summarize_progress(run.get("stages") or {})
                if (q["done"], q["percent"]) > (best["done"], best["percent"]):
                    best = q
            if (best["done"], best["percent"]) > (prog["done"], prog["percent"]):
                prog["best_percent"] = best["percent"]
                prog["best_done"] = best["done"]
        p["progress"] = prog
    return projects


@app.post("/api/projects")
def post_project(body: ProjectIn, user=Depends(auth.current_user)):
    return store.create_project(user["tenant_id"], user["id"], body.name,
                                body.mineral, body.mineral_label, body.aoi_bbox)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user=Depends(auth.current_user)):
    proj, role = auth.require_project_read(user, project_id)
    return {**proj, "my_role": role or user["tenant_role"]}


@app.get("/api/projects/{project_id}/model3d-existing")
def get_existing_model3d(project_id: str, user=Depends(auth.current_user)):
    proj, _ = auth.require_project_read(user, project_id)
    return _find_existing_model3d(proj)


@app.get("/api/projects/{project_id}/drill-existing")
def get_existing_drill(project_id: str, user=Depends(auth.current_user)):
    proj, _ = auth.require_project_read(user, project_id)
    return _find_existing_drill(proj)


@app.get("/api/projects/{project_id}/drill-data")
def download_drill_data(project_id: str, user=Depends(auth.current_user)):
    """下载设计好的 AI 布孔数据表(holes_table.csv) —— 孔号/经纬/深度/方位/倾角/得分/优先级。"""
    proj, _ = auth.require_project_read(user, project_id)
    run_dir = _latest_drill_run_dir(proj)
    if run_dir is None:
        raise HTTPException(status_code=404, detail="未发现布孔产物,请先在布孔环节运行 AI 布孔")
    csv = run_dir / "holes_table.csv"
    if not csv.is_file():
        raise HTTPException(status_code=404, detail="布孔产物缺少 holes_table.csv")
    safe = _safe_stem(proj.get("name") or proj.get("kml_name") or "AI布孔") or "AI布孔"
    fname = urllib.parse.quote(f"AI布孔数据_{safe}.csv")
    return FileResponse(
        str(csv), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"drill_holes.csv\"; filename*=UTF-8''{fname}"},
    )


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user=Depends(auth.current_user)):
    auth.require_project_admin(user, project_id)
    store.delete_project(project_id)
    store.write_audit("project.delete", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="project", target_id=project_id)
    kml = _KML_DIR / f"{project_id}.kml"
    if kml.exists():
        kml.unlink()
    return {"ok": True}


# ─── 项目成员管理(创建者或租户管理员) ──────────────────────
def _enrich_member(m: dict) -> dict:
    u = store.get_user(m["user_id"]) or {}
    return {**m, "username": u.get("username", ""), "display": u.get("display", "")}


@app.get("/api/projects/{project_id}/members")
def list_members(project_id: str, user=Depends(auth.current_user)):
    auth.require_project_read(user, project_id)
    return [_enrich_member(m) for m in store.members_for_project(project_id)]


class MemberIn(BaseModel):
    user_id: str
    role: str = "geologist"
    expires_at: Optional[str] = None


@app.post("/api/projects/{project_id}/members")
def add_member(project_id: str, body: MemberIn, request: Request, user=Depends(auth.current_user)):
    auth.require_project_admin(user, project_id)
    if body.role not in auth.PROJECT_ROLES:
        raise HTTPException(status_code=400, detail=f"非法项目角色: {body.role}")
    target = store.get_user(body.user_id)
    if not target or target["tenant_id"] != user["tenant_id"]:
        raise HTTPException(status_code=404, detail="用户不存在或不同租户")
    m = store.add_member(body.user_id, project_id, body.role, body.expires_at)
    store.write_audit("member.add", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="project", target_id=project_id,
                      details={"user_id": body.user_id, "role": body.role},
                      ip=request.client.host if request.client else "")
    return _enrich_member(m)


@app.patch("/api/projects/{project_id}/members/{uid}")
def change_member(project_id: str, uid: str, body: MemberIn, user=Depends(auth.current_user)):
    auth.require_project_admin(user, project_id)
    if body.role not in auth.PROJECT_ROLES:
        raise HTTPException(status_code=400, detail=f"非法项目角色: {body.role}")
    m = store.update_member_role(uid, project_id, body.role)
    if not m:
        raise HTTPException(status_code=404, detail="成员不存在")
    store.write_audit("member.role", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="project", target_id=project_id,
                      details={"user_id": uid, "role": body.role})
    return _enrich_member(m)


@app.delete("/api/projects/{project_id}/members/{uid}")
def remove_member(project_id: str, uid: str, user=Depends(auth.current_user)):
    proj, _ = auth.require_project_admin(user, project_id)
    if proj["creator_id"] == uid:
        raise HTTPException(status_code=400, detail="不能移除项目创建者")
    if not store.remove_member(uid, project_id):
        raise HTTPException(status_code=404, detail="成员不存在")
    store.write_audit("member.remove", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="project", target_id=project_id, details={"user_id": uid})
    return {"ok": True}


@app.post("/api/projects/{project_id}/kml")
async def upload_kml(project_id: str, file: UploadFile = File(...), user=Depends(auth.current_user)):
    """上传项目 ROI(支持 kml/kmz/ovkml/csv/xlsx),BFF 统一解析坐标 → 规范 KML 缓存;
    后续 plan/start 一律转发干净 KML(下游只认 .kml/.kmz)。"""
    auth.require_project_write(user, project_id)
    raw = await file.read()
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    target = _KML_DIR / f"{project_id}.kml"
    pts = _extract_coords(raw, ext)
    if pts:
        target.write_text(_kml_from_coords(pts), encoding="utf-8")
        lons = [p[0] for p in pts]; lats = [p[1] for p in pts]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
    elif ext in ("kml", "ovkml", "xml"):
        target.write_bytes(raw)           # 已是 KML 文本,原样转发
        bbox = _parse_bbox(raw)
    else:
        raise HTTPException(status_code=400,
                            detail=f"无法从 .{ext} 解析坐标:CSV/XLSX 需含经度/纬度列,KMZ 需含 KML 几何")
    patch = {"kml_name": file.filename}
    if bbox:
        patch["aoi_bbox"] = bbox
    # 交付绑定:按 ROI 几何一次性解析 delivery_id 存项目(命名一次,之后按 ID 引用)
    deliv = _resolve_delivery(pts, file.filename)
    if deliv.get("delivery_id"):
        patch["delivery_id"] = deliv["delivery_id"]
        patch["delivery_name"] = deliv.get("delivery_name")
    store.update_project(project_id, patch)
    return {"ok": True, "filename": file.filename, "bbox": bbox, "points": len(pts),
            "delivery": {"id": deliv.get("delivery_id"), "name": deliv.get("delivery_name"),
                         "method": deliv.get("method"), "candidates": deliv.get("candidates") or []}}


# ─── 手工提交证据(地质图/电法/磁法/航磁/重力/历史钻孔/靶向超弱核磁) ─────
# 预留入口:本期仅留存 + 证据链标注"待融合",不进 evidence_plan / 不参与融合算法。
_MANUAL_EV_CATS = {
    "geological_map": "地质图", "electrical": "电法", "magnetic": "磁法",
    "aeromagnetic": "航磁", "gravity": "重力", "historical_drill": "历史钻孔数据",
    "nmr_weak": "靶向超弱核磁",  # 预留·未来强支撑
}
_MANUAL_DIR = store.DATA_DIR / "manual"
_MANUAL_MAX_BYTES = 200 * 1024 * 1024   # 单文件 200MB 上限


def _safe_name(name: str) -> str:
    """去掉路径分隔与控制字符,防目录穿越;空则给默认名。"""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^\w.\-()一-鿿 ]", "_", base)
    return base[:160] or "file"


def _pub_me(m: dict) -> dict:
    """对外去掉绝对 path(仅内部用)。"""
    return {k: v for k, v in (m or {}).items() if k != "path"}


@app.post("/api/projects/{project_id}/manual-evidence")
async def manual_evidence_upload(project_id: str, file: UploadFile = File(...),
                                 category: str = Form(...), note: str = Form(""),
                                 request: Request = None, user=Depends(auth.current_user)):
    auth.require_project_write(user, project_id)
    if category not in _MANUAL_EV_CATS:
        raise HTTPException(status_code=400, detail=f"未知证据类别: {category}")
    raw = await file.read()
    if len(raw) > _MANUAL_MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件过大(上限 200MB)")
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    fname = _safe_name(file.filename)
    out_dir = _MANUAL_DIR / project_id / category
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}_{fname}"
    out_path.write_bytes(raw)
    rec = store.add_manual_evidence(project_id, category, _MANUAL_EV_CATS[category],
                                    fname, str(out_path), len(raw),
                                    (note or "").strip()[:500], user["id"])
    store.write_audit("manual_evidence.add", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="manual_evidence", target_id=rec["id"],
                      details={"project_id": project_id, "category": category, "filename": fname},
                      ip=(request.client.host if request and request.client else ""))
    return _pub_me(rec)


@app.get("/api/projects/{project_id}/manual-evidence")
def manual_evidence_list(project_id: str, user=Depends(auth.current_user)):
    auth.require_project_read(user, project_id)
    return [_pub_me(m) for m in store.list_manual_evidence(project_id)]


def _parse_econ_table(raw: bytes, ext: str) -> dict:
    """解析上传的经济参数表(CSV 两列「参数,值」或 JSON)→ dict;供新版报告价值评估章。"""
    text = raw.decode("utf-8-sig", "ignore")
    if ext == "json":
        try:
            d = json.loads(text)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    import csv as _csv
    out = {}
    for row in _csv.reader(text.splitlines()):
        if len(row) >= 2 and str(row[0]).strip() and str(row[0]).strip().lower() not in ("参数", "param", "key"):
            out[str(row[0]).strip()] = str(row[1]).strip()
    return out


@app.post("/api/projects/{project_id}/econ-params")
async def upload_econ_params(project_id: str, file: UploadFile = File(...), user=Depends(auth.current_user)):
    """上传经济参数表(CSV/JSON)→ 解析存到项目;报告生成时透传给 reporter 的价值评估章(Phase C)。"""
    auth.require_project_write(user, project_id)
    raw = await file.read()
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv", "json", "txt"):
        raise HTTPException(status_code=400, detail="仅支持 CSV / JSON 参数表")
    econ = _parse_econ_table(raw, ext)
    if not econ:
        raise HTTPException(status_code=400, detail="参数表为空或无法解析(CSV 需两列「参数,值」)")
    store.update_project(project_id, {"econ_params": econ})
    return {"ok": True, "econ_params": econ, "keys": list(econ.keys())}


@app.get("/api/projects/{project_id}/econ-params")
def get_econ_params(project_id: str, user=Depends(auth.current_user)):
    auth.require_project_read(user, project_id)
    proj = store.get_project(project_id) or {}
    return {"econ_params": proj.get("econ_params")}


@app.delete("/api/projects/{project_id}/manual-evidence/{me_id}")
def manual_evidence_delete(project_id: str, me_id: str, request: Request = None,
                           user=Depends(auth.current_user)):
    auth.require_project_write(user, project_id)
    rec = store.get_manual_evidence(me_id)
    if not rec or rec.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="证据不存在")
    store.delete_manual_evidence(me_id)
    try:
        os.unlink(rec.get("path"))
    except OSError:
        pass
    store.write_audit("manual_evidence.delete", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="manual_evidence", target_id=me_id,
                      details={"project_id": project_id}, ip=(request.client.host if request and request.client else ""))
    return {"ok": True}


@app.get("/api/projects/{project_id}/manual-evidence/{me_id}/file")
def manual_evidence_file(project_id: str, me_id: str, user=Depends(auth.current_user)):
    auth.require_project_read(user, project_id)
    rec = store.get_manual_evidence(me_id)
    if not rec or rec.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="证据不存在")
    path = rec.get("path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件已不存在")
    fn = urllib.parse.quote(rec.get("filename") or "evidence")
    return FileResponse(path, filename=rec.get("filename") or "evidence",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})


class DeliveryBindIn(BaseModel):
    delivery_id: str


@app.get("/api/deliveries")
def list_deliveries(user=Depends(auth.current_user)):
    """列出交付库所有交付(供前端手动选/消歧)。无交付根则空。"""
    try:
        import sys as _s
        if "/opt/deepexplor-services" not in _s.path:
            _s.path.insert(0, "/opt/deepexplor-services")
        from commons.delivery import build_index
        idx = build_index()
        return sorted(
            [{"delivery_id": e.get("delivery_id"), "name": n, "bbox": e.get("bbox")}
             for n, e in idx.items()],
            key=lambda x: x["name"])
    except Exception:
        return []


@app.post("/api/projects/{project_id}/delivery")
def bind_delivery(project_id: str, body: DeliveryBindIn, user=Depends(auth.current_user)):
    """手动把项目绑定到指定交付(消歧:用户从候选里选定)。"""
    auth.require_project_write(user, project_id)
    try:
        import sys as _s
        if "/opt/deepexplor-services" not in _s.path:
            _s.path.insert(0, "/opt/deepexplor-services")
        from commons.delivery import resolve as _resolve
        r = _resolve(delivery_id=body.delivery_id)
    except Exception:
        r = {"dir": None}
    d = r.get("dir")
    if not d:
        raise HTTPException(status_code=404, detail=f"交付不存在: {body.delivery_id}")
    store.update_project(project_id, {"delivery_id": body.delivery_id, "delivery_name": d.name})
    store.write_audit("project.delivery_bind", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="project", target_id=project_id,
                      details={"delivery_id": body.delivery_id, "name": d.name})
    return {"ok": True, "delivery": {"id": body.delivery_id, "name": d.name}}


# ─── 运行(trace_id 主线)───────────────────────────────────
class RunIn(BaseModel):
    plan: dict = {}
    trace_id: Optional[str] = None  # 若上游(orchestrator)已返回则沿用,否则 BFF 生成


@app.post("/api/projects/{project_id}/runs")
def create_run(project_id: str, body: RunIn, user=Depends(auth.current_user)):
    auth.require_project_write(user, project_id)
    trace_id = body.trace_id or _new_trace_id()
    run = store.create_run(project_id, trace_id, body.plan)
    return run


@app.get("/api/runs/{trace_id}")
def get_run(trace_id: str, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_read(user, run["project_id"])
    return run


def _smooth_citation(text: str) -> str:
    """把文献要点里生硬的"摘要"口吻平滑成正常文献引述,避免读者跳出感。
    data-colle 的论文要点常用"摘要"作元指代(摘要未提供X/摘要缺失/摘要[7]中指出),
    统一改为"文献",并去掉"摘要引用"标签;保留 [n] 引用编号以便对照文献清单。"""
    t = str(text or "")
    t = re.sub(r"摘要引用[:：]?\s*", "", t)                # 去"摘要引用"标签
    t = t.replace("论文摘要", "文献").replace("文献标题与摘要", "文献")
    t = t.replace("摘要", "文献")                          # 余下"摘要"统一为"文献"
    return re.sub(r"\s+", " ", t).strip()


def _datacolle_literature_points(literature_md: str, limit: int = 8) -> list:
    """从 data-colle「论文要点提炼」markdown 抽取要点条目(保留 [n] 引用、剥离 markdown 噪声)。
    取「### 论文清单」之前的 `- ` 项;纯字符串、空安全返回 []。"""
    if not literature_md:
        return []
    head = literature_md.split("### 论文清单", 1)[0]
    pts = []
    for raw in head.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(">") or line.startswith("---"):
            continue
        if not (line.startswith("- ") or line.startswith("· ") or line.startswith("* ")):
            continue
        line = _smooth_citation(line.lstrip("-·* ").replace("**", "").strip())
        if line:
            pts.append(line)
        if len(pts) >= limit:
            break
    return pts


@app.get("/api/runs/{trace_id}/datacolle-evidence")
def get_datacolle_evidence(trace_id: str, user=Depends(auth.current_user)):
    """证据链叙事面板用:本 ROI 的 data-colle 成矿模型+文献佐证
    (best_model / pathfinder / 论文要点[n] / 文献清单)。经 datacolle_broker 按 bbox
    (+trace/tenant)发现;无产物则 available=False(面板静默不出该卡)。"""
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_read(user, run["project_id"])
    proj = store.get_project(run["project_id"]) or {}
    bbox = _as_bbox(proj.get("aoi_bbox"))
    if not bbox:
        return {"available": False}
    try:
        import sys as _sys
        if "/opt/deepexplor-services" not in _sys.path:
            _sys.path.insert(0, "/opt/deepexplor-services")
        from commons.datacolle_broker import find_datacolle_for_bbox
        entries = find_datacolle_for_bbox(tuple(bbox), _DC_DIR + "/output",
                                          trace_id=trace_id, tenant_id=run.get("tenant_id"))
    except Exception:
        return {"available": False}
    # 矿种门控:同一 ROI 不同矿种时,bbox 回退可能命中别矿种的 data-colle,过滤掉(避免文献佐证串矿种)
    pm = str(proj.get("mineral") or "").strip().lower()
    if pm and pm != "multi_mineral":
        pm_zh = _MINERAL_ZH.get(pm, "")[:1]

        def _dc_mineral_ok(em):
            ea = str(em or "").strip().lower()
            if not ea:
                return False
            return ea == pm or (pm_zh and _MINERAL_ZH.get(ea, ea)[:1] == pm_zh)
        entries = [x for x in entries if _dc_mineral_ok(x.get("mineral"))]
    if not entries:
        return {"available": False}
    e = entries[0]
    met = e.get("metallogenic") or {}
    papers = []
    for i, p in enumerate((e.get("papers") or [])[:12], 1):
        if not isinstance(p, dict):
            continue
        authors = p.get("authors") or []
        author = (authors[0] + (" 等" if len(authors) > 1 else "")) if authors else ""
        papers.append({"n": i, "author": author, "year": p.get("year"),
                       "title": p.get("title") or "", "cited_by": p.get("cited_by"),
                       "doi": p.get("doi") or p.get("url") or ""})
    lit = (e.get("sections") or {}).get("literature", "")
    return {
        "available": bool(met.get("best_model") or papers or lit),
        "aoi_name": e.get("aoi_name"),
        "best_model": met.get("best_model"),
        "model_count": met.get("model_count"),
        "pathfinder_elements": met.get("pathfinder_elements") or [],
        "papers": papers,
        "literature_points": _datacolle_literature_points(lit),
    }


class StagePatch(BaseModel):
    stage: str
    patch: dict


class EvidencePlanPatch(BaseModel):
    evidence_plan: dict


@app.patch("/api/runs/{trace_id}/stage")
def patch_stage(trace_id: str, body: StagePatch, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_write(user, run["project_id"])
    updated = store.update_stage(trace_id, body.stage, body.patch)
    if (
        body.stage == "data"
        and str((body.patch or {}).get("status", "")).lower() == "completed"
        and not (updated or {}).get("evidence_plan")
    ):
        proj = store.get_project(run["project_id"]) or {}
        plan = _build_evidence_plan(updated, proj)
        updated = store.update_evidence_plan(trace_id, plan)
    return updated


@app.post("/api/runs/{trace_id}/evidence-plan")
def make_evidence_plan(trace_id: str, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    proj, _ = auth.require_project_write(user, run["project_id"])
    plan = _build_evidence_plan(run, proj, run.get("evidence_plan") or {})
    updated = store.update_evidence_plan(trace_id, plan)
    return updated["evidence_plan"]


@app.patch("/api/runs/{trace_id}/evidence-plan")
def patch_evidence_plan(trace_id: str, body: EvidencePlanPatch, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_write(user, run["project_id"])
    plan = body.evidence_plan or {}
    plan["trace_id"] = trace_id
    plan["project_id"] = run["project_id"]
    plan = _normalize_evidence_plan(plan)
    updated = store.update_evidence_plan(trace_id, plan)
    return updated["evidence_plan"]


@app.post("/api/runs/{trace_id}/evidence-plan/execute")
def execute_evidence_plan(trace_id: str, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_write(user, run["project_id"])
    plan = run.get("evidence_plan") or {}
    if not plan:
        proj = store.get_project(run["project_id"]) or {}
        plan = _build_evidence_plan(run, proj)
    tasks = sorted(
        [t for t in plan.get("evidence_tasks", [])
         if _task_enabled(t) and t.get("status") in ("pending", "failed")],
        key=lambda t: int(t.get("priority") or 999),
    )
    plan["status"] = "executing"
    for t in plan.get("evidence_tasks", []):
        if any(x.get("key") == t.get("key") for x in tasks):
            t["status"] = "pending"
            t["progress"] = 0
            t["error"] = ""
    plan = _normalize_evidence_plan(plan)
    if tasks:
        plan["status"] = "executing"
    store.update_evidence_plan(trace_id, plan)
    store.update_stage(trace_id, "evidence", {"status": "running", "progress": 5})
    return {"ok": True, "evidence_plan": plan, "tasks": tasks}


@app.get("/api/projects/{project_id}/runs")
def list_runs(project_id: str, user=Depends(auth.current_user)):
    auth.require_project_read(user, project_id)
    return store.runs_for_project(project_id)


@app.delete("/api/runs/{trace_id}")
def delete_run(trace_id: str, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_write(user, run["project_id"])
    store.delete_run(trace_id)
    return {"ok": True}


# ─── 真实服务调用(BFF 承载 KML multipart 转发)──────────────
@app.post("/api/projects/{project_id}/plan")
def make_plan(project_id: str, user=Depends(auth.current_user)):
    """调真实 orchestrator /api/plan 生成编排单 + 真实 trace_id;不可达则返回 ok=False。"""
    proj, _ = auth.require_project_write(user, project_id)
    _ihdr = _internal_headers(user["tenant_id"])
    url = f"{services.base_url('orchestrator')}/api/plan"
    try:
        with httpx.Client(timeout=60.0, headers=_ihdr) as c:
            r = c.post(url, files=_kml_for_project(project_id), data={"mineral": proj["mineral"], "aoi_name": proj["name"]})
        j = r.json()
    except (httpx.ConnectError, httpx.ReadTimeout, ValueError):
        return {"ok": False, "reason": "orchestrator 不可达"}
    if not j.get("success", True):
        return {"ok": False, "reason": j.get("message", "编排失败")}
    task_id = j.get("task_id") or (j.get("task") or {}).get("id")
    trace_id, plan = j.get("trace_id"), j.get("plan")
    # 轮询取 trace_id + plan(最多 ~15s)
    for _ in range(15):
        try:
            with httpx.Client(timeout=15.0, headers=_ihdr) as c:
                s = c.get(f"{services.base_url('orchestrator')}/api/status/{task_id}").json()
        except Exception:
            break
        task = s.get("task") if isinstance(s.get("task"), dict) else s
        trace_id = task.get("trace_id") or trace_id
        plan = task.get("plan") or plan
        st = str(task.get("status", "")).lower()
        if plan or st in ("completed", "done", "success", "failed", "error"):
            break
        time.sleep(1)
    return {"ok": True, "task_id": task_id, "trace_id": trace_id, "plan": plan or {}}


class StartIn(BaseModel):
    service: str
    params: dict = {}


@app.post("/api/runs/{trace_id}/start")
def start_service(trace_id: str, body: StartIn, user=Depends(auth.current_user)):
    """调某服务 /api/start(BFF 注入样例 KML + 项目矿种),返回其 task_id。"""
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_write(user, run["project_id"])
    if not services.is_known(body.service):
        raise HTTPException(status_code=404, detail=f"未知服务: {body.service}")
    # 服务自检:调下游前确保它已启动,未就绪则按既定命令拉起(避免"服务不可达"导致阶段失败)
    try:
        from . import service_launcher
        service_launcher.ensure_up(body.service,
                                   on_log=lambda m, lvl="INFO": print(f"[svc-ensure][{lvl}] {m}", flush=True))
    except Exception as _e:  # 自检不应阻断:拉不起就按原逻辑继续,由下游调用报真实错误
        print(f"[svc-ensure] {body.service} 自检异常(忽略): {_e}", flush=True)
    proj = store.get_project(run["project_id"])
    tenant_id = user["tenant_id"]   # 注入下游:产物按租户打标 / 隔离
    delivery_id = (proj or {}).get("delivery_id")   # 门户绑定的交付,下游优先按此取数据

    if body.service == "downloader":
        try:
            return _start_downloader(run["project_id"], body.params or {})
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="服务 downloader 不可达")

    if body.service == "datacolle":
        data = {"mineral": proj["mineral"], "auto_download": "true", "trace_id": trace_id}
        if (body.params or {}).get("buffer") is not None:
            data["buffer"] = str(body.params["buffer"])
        try:
            with httpx.Client(timeout=90.0, headers=_internal_headers(tenant_id)) as c:
                r = c.post(f"{services.base_url('datacolle')}/api/upload",
                           files=_kml_for_project(run["project_id"]), data=data)
            if r.status_code >= 400:
                raise HTTPException(status_code=502, detail=r.text[:300] or "datacolle 启动失败")
            j = r.json()
        except (httpx.ConnectError, httpx.ReadTimeout, ValueError):
            raise HTTPException(status_code=503, detail="服务 datacolle 不可达")
        task_id = j.get("task_id")
        if not task_id:
            raise HTTPException(status_code=502, detail=j.get("error") or "datacolle 未返回 task_id")
        return {"service": body.service, "task_id": task_id, "adapter": True}

    # analyser/stru/insar/geophys/reporter:接口异构或需自动补数据,走 BFF 适配器(后台线程)
    if body.service in ("analyser", "stru", "insar", "geophys", "reporter"):
        kml_bytes, kml_name = _kml_bytes_and_name(run["project_id"])
        tid = f"{body.service}_bff_{uuid.uuid4().hex[:8]}"
        _ADAPTER_TASKS[tid] = {"status": "running", "progress": 5, "error": None}
        # 经 _adapter_thread 包装:任务结束自动把终态(含栅格 layer)落库,重启可恢复
        if body.service == "analyser":
            th = threading.Thread(target=_adapter_thread, args=(_run_analyser, tid, kml_bytes, kml_name, proj["mineral"], tenant_id, delivery_id, proj.get("aoi_bbox")), daemon=True)
        elif body.service == "stru":
            th = threading.Thread(target=_adapter_thread, args=(_run_stru, tid, kml_bytes, kml_name, proj.get("aoi_bbox"), tenant_id, delivery_id), daemon=True)
        elif body.service == "geophys":
            th = threading.Thread(target=_adapter_thread, args=(_run_geophys, tid, kml_bytes, kml_name, proj["mineral"], tenant_id), daemon=True)
        elif body.service == "insar":
            params = body.params or {}
            phase = str(params.get("phase") or params.get("stage") or "").lower()
            allow_trigger = phase == "data"
            _ADAPTER_TASKS[tid]["phase"] = "data" if allow_trigger else "evidence"
            data_task = (((run.get("stages") or {}).get("data") or {}).get("sub_tasks") or {}).get("insar") or {}
            th = threading.Thread(target=_adapter_thread, args=(_run_insar, tid, kml_name, proj, allow_trigger, data_task), daemon=True)
        else:
            th = threading.Thread(target=_adapter_thread, args=(_run_reporter, tid, kml_bytes, kml_name, proj.get("mineral_label") or proj["mineral"], tenant_id, proj.get("econ_params")), daemon=True)
        th.start()
        return {"service": body.service, "task_id": tid, "adapter": True}

    data = {"mineral": proj["mineral"], "aoi_name": proj["name"], "trace_id": trace_id}
    data.update({k: str(v) for k, v in (body.params or {}).items()})
    url = f"{services.base_url(body.service)}/api/start"
    try:
        with httpx.Client(timeout=90.0, headers=_internal_headers(tenant_id, delivery_id)) as c:
            r = c.post(url, files=_kml_for_project(run["project_id"]), data=data)
        j = r.json()
    except (httpx.ConnectError, httpx.ReadTimeout, ValueError):
        raise HTTPException(status_code=503, detail=f"服务 {body.service} 不可达")
    task_id = j.get("task_id") or (j.get("task") or {}).get("id")
    if not task_id:
        raise HTTPException(status_code=502, detail=j.get("message", "服务未返回 task_id"))
    return {"service": body.service, "task_id": task_id, "bbox": j.get("bbox")}


# 数据阶段的必需服务。InSAR 一旦被编排选中,必须在数据阶段完成或明确无可叠加栅格。
_DATA_SYNC_SERVICES = {"downloader", "datacolle", "preprocess", "insar"}


def _reconcile_data_stage(trace_id: str, run: dict, service: str, result: dict) -> None:
    """后端兜底:把真实数据服务状态写回 stages.data。

    InSAR 在这里被当作数据准备任务处理:未完成不放行,完成但 no_layer 也算数据已给出终态。
    """
    try:
        status = str((result or {}).get("status") or "").lower()
        if service not in _DATA_SYNC_SERVICES or status not in ("running", "completed", "failed", "skipped"):
            return
        data = (run.get("stages") or {}).get("data") or {}
        subs = dict(data.get("sub_tasks") or {})
        cur = dict(subs.get(service) or {})
        cur["status"] = status
        cur["progress"] = int((result or {}).get("progress") or (100 if status in ("completed", "skipped") else cur.get("progress") or 0))
        cur["required"] = True
        if (result or {}).get("task_id") or cur.get("task_id"):
            cur["task_id"] = (result or {}).get("task_id") or cur.get("task_id")
        for key in ("error", "warning", "note", "no_layer", "layer", "raw", "sensors", "active_sensor", "insar_task_id", "insar_aoi", "insar_stack_index"):
            if key in (result or {}):
                cur[key] = result.get(key)
        subs[service] = cur
        planned = set(data.get("services") or [])
        required = (planned & _DATA_SYNC_SERVICES) or {service}
        patch = {"sub_tasks": subs}
        if status == "failed":
            patch["status"] = "failed"
            patch["progress"] = cur.get("progress") or 0
            patch["error"] = cur.get("error") or result.get("error") or f"{service} 数据准备失败"
        elif any((subs.get(s) or {}).get("status") == "failed" for s in required):
            patch["status"] = "failed"
            patch["progress"] = min(99, int(data.get("progress") or cur.get("progress") or 0))
        elif all((subs.get(s) or {}).get("status") in ("completed", "skipped") for s in required):
            patch["status"] = "completed"
            patch["progress"] = 100
            patch["error"] = ""
        else:
            patch["status"] = "running"
            vals = [int((subs.get(s) or {}).get("progress") or 0) for s in required if (subs.get(s) or {}).get("status") != "skipped"]
            patch["progress"] = min(99, int(sum(vals) / len(vals))) if vals else int(data.get("progress") or 0)
        store.update_stage(trace_id, "data", patch)
    except Exception:
        pass  # 兜底逻辑不得影响 svcstatus 正常返回


# ─── 证据链综合研判(LLM,复用 claude CLI;喂真实数据避免幻觉,按 run 缓存)──
import shutil as _shutil
_CLAUDE_BIN = _shutil.which("claude") or "/opt/homebrew/bin/claude"


class SynthIn(BaseModel):
    facts: dict = {}      # 前端组装的本 run 真实数据(证据 summary/靶点/钻孔/知识库/矿种模型)
    refresh: bool = False


def _synthesis_prompt(facts: dict) -> str:
    return (
        "你是资深矿产勘查地质专家。下面是某勘查项目【本次运行的真实数据】(JSON)。"
        "请只基于这些真实数字做证据链综合研判,严禁编造数据中没有的数值/坐标/占比(如 metrics 缺失就不要写具体数)。"
        "某证据 status=completed 但无 metrics 时,如实写'已完成,但本次未采到量化指标',不得臆造比例或元素;"
        "不要把'证据已完成'与'是否进入三维融合层(fusion_layers)'混为一谈,二者分别陈述。"
        "若提供 model3d_family(三维建模据实判定的成因族),以它为【权威成因族】;字段 model/rationale 仅为建模前按矿种定的先验证据模型,"
        "不得用其矿床类型名(如与 model3d_family 不一致的'IOCG'等)冒充权威成因族,避免口径自相矛盾。"
        "【语言规范】这是正式中文文案:输出全程使用规范中文,严禁出现任何英文字段名/状态/枚举/数据键"
        "(如 analyser/stru/geophys/geochem/insar、completed、knowledge/data_driven、alteration/structure/magnetic/curvature、"
        "rank、datacolle、skarn/porphyry/iocg 等);证据一律称 蚀变/构造/物探/化探/形变,靶点称'1号靶点'(不要写 rank1),"
        "状态称'已完成'。所给数据已是中文键,照用中文即可。\n"
        "输出【纯 JSON】(不要任何解释/markdown 代码块),schema:\n"
        '{"grade":"A|B|C|D(综合成矿置信)","summary":"3-5句中文综合研判,需引用真实数(构造条数/蚀变占比/靶点评分深度/形变速率等)并说明证据如何相互印证",'
        '"dimensions":[{"name":"构造|蚀变|物探|化探|形变","level":"高|中|低|缺","evidence":"一句中文,引用该证据真实指标"}],'
        '"target_assessment":[{"rank":1,"grade":"A|B|C|D","reason":"一句中文,说明该靶点由哪些证据叠合支撑、评分与深度"}]}\n'
        "真实数据:\n" + json.dumps(facts, ensure_ascii=False)
    )


def _run_synthesis_llm(prompt: str):
    try:
        env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"}
        r = subprocess.run([_CLAUDE_BIN, "-p", "--dangerously-skip-permissions", prompt],
                           capture_output=True, text=True, timeout=240, env=env)
        if r.returncode != 0:
            return None
        s = (r.stdout or "").strip()
        mt = re.search(r"\{.*\}", s, re.S)
        return json.loads(mt.group(0)) if mt else None
    except Exception:
        return None


@app.post("/api/runs/{trace_id}/synthesis")
def run_synthesis(trace_id: str, body: SynthIn, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_read(user, run["project_id"])
    fp = hashlib.sha1(json.dumps(body.facts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    cache = None
    try:
        cache = store.get_adapter_task(f"synth_{trace_id}")
    except Exception:
        cache = None
    if cache and cache.get("fingerprint") == fp and not body.refresh and cache.get("result"):
        return cache["result"]
    if not (os.path.exists(_CLAUDE_BIN) or _shutil.which("claude")):
        return {"available": False, "reason": "未启用 LLM(claude CLI 不可用)"}
    res = _run_synthesis_llm(_synthesis_prompt(body.facts))
    if not res:
        return {"available": False, "reason": "研判生成失败,可点刷新重试"}
    out = {"available": True, **res}
    try:
        store.save_adapter_task(f"synth_{trace_id}", {"fingerprint": fp, "result": out})
    except Exception:
        pass
    return out


@app.get("/api/runs/{trace_id}/svcstatus")
def service_status(trace_id: str, service: str, task_id: str, user=Depends(auth.current_user)):
    run = store.get_run(trace_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    auth.require_project_read(user, run["project_id"])
    # 适配器任务:优先内存,缺失则从库恢复终态(BFF 重启后)并回填内存
    t = _ADAPTER_TASKS.get(task_id)
    if t is None and "_bff_" in task_id:
        try:
            t = store.get_adapter_task(task_id)
            if t is not None:
                _ADAPTER_TASKS[task_id] = t   # 回填内存,后续刷新/轮询用
        except Exception:
            t = None
    # InSAR 异步云任务:实时代理 geo-insar 任务状态(排队/云处理/后处理/完成)
    if t and t.get("insar_task_id") and t.get("status") not in ("completed", "failed"):
        t = _refresh_insar(task_id, t)
    if t:
        if service == "insar" and t.get("phase") == "data":
            rec = dict(t)
            rec["task_id"] = task_id
            _reconcile_data_stage(trace_id, run, service, rec)
        return {
            "status": t.get("status"),
            "progress": t.get("progress"),
            "raw": t.get("raw") or t.get("error") or t.get("status"),
            "error": t.get("error"),
            "download": t.get("download"),
            "warning": t.get("warning"),
            "note": t.get("note"),
            "no_layer": t.get("no_layer"),
            "summary": t.get("summary"),   # 证据量化指标(叙事事实层)
        }
    # 仍找不到且是适配器 id → BFF 重启时正在跑、未落库的孤儿任务 → 报 failed,前端给重试入口。
    if "_bff_" in task_id:
        return {"status": "failed", "progress": 0, "raw": "orphaned",
                "error": "证据任务因服务重启中断,请重试"}
    if not services.is_known(service):
        raise HTTPException(status_code=404, detail=f"未知服务: {service}")
    if service == "downloader":
        res = _downloader_status(task_id)
        res["task_id"] = task_id   # 确保 task_id 落库,供重开后续接轮询
        _reconcile_data_stage(trace_id, run, service, res)   # 后端兜底:真完成则持久化 stages.data
        return res
    url = f"{services.base_url(service)}/api/status/{task_id}"
    try:
        with httpx.Client(timeout=30.0, headers=_internal_headers()) as c:
            res = _norm_status(c.get(url).json())
            # geochem 非 adapter:完成时现读其 metadata 提炼量化指标(化探异常样/C-A 阈值/关键元素)
            if service == "geochem" and res.get("status") == "completed":
                try:
                    gm = c.get(f"{services.base_url('geochem')}/api/result/{task_id}/metadata.json").json()
                    summ = _evidence_summary("geochem", gm)
                    if summ:
                        res["summary"] = summ
                except Exception:
                    pass
        res["task_id"] = task_id   # 确保 task_id 落库,供重开后续接轮询
        _reconcile_data_stage(trace_id, run, service, res)   # 后端兜底:datacolle/preprocess 完成同理
        return res
    except (httpx.ConnectError, httpx.ReadTimeout, ValueError):
        raise HTTPException(status_code=503, detail=f"服务 {service} 不可达")


@app.get("/api/adapter-raster/{task_id}")
def adapter_raster(task_id: str, project_id: str = "", user=Depends(auth.current_user)):
    """适配器(analyser/stru)产出的代表性栅格,供前端 2D 叠图。"""
    if project_id:
        auth.require_project_read(user, project_id)
    t = _ADAPTER_TASKS.get(task_id)
    if t is None:   # BFF 重启后内存丢失 → 从库恢复 layer 引用
        try:
            t = store.get_adapter_task(task_id)
        except Exception:
            t = None
    layer = (t or {}).get("layer")
    if not layer:
        raise HTTPException(status_code=404, detail="无可叠加栅格")
    if layer["kind"] == "file":
        if not os.path.isfile(layer["path"]):
            raise HTTPException(status_code=404, detail="栅格文件不存在")
        if project_id:
            with open(layer["path"], "rb") as f:
                content, meta = _clip_tif_to_project_bytes(f.read(), project_id)
            return Response(content=content, media_type="image/tiff", headers=_raster_scope_headers(meta))
        return FileResponse(layer["path"], media_type="image/tiff")
    if layer["kind"] == "proxy":
        url = f"{services.base_url(layer['service'])}/api/result/{layer['task']}/{layer['rel']}"
        try:
            with httpx.Client(timeout=60.0, headers=_internal_headers()) as c:
                r = c.get(url)
            if project_id:
                content, meta = _clip_tif_to_project_bytes(r.content, project_id)
            else:
                content, meta = r.content, {"scope": "native", "bounds": None}
            return Response(content=content, media_type="image/tiff", headers=_raster_scope_headers(meta))
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="服务不可达")
    raise HTTPException(status_code=404, detail="未知栅格类型")


def _raster_scope_headers(meta: dict) -> dict:
    """把裁剪结果的 scope/bounds 放进响应头,供前端按真实范围摆放 + 标注"区域级"。"""
    meta = meta or {}
    # 注意:HTTP 头必须 latin-1,不能放中文。文案由前端按 scope 自行渲染。
    h = {"X-Raster-Scope": str(meta.get("scope") or "aoi")}
    b = meta.get("bounds")
    if b and len(b) == 4:
        h["X-Raster-Bounds"] = ",".join(f"{float(x):.6f}" for x in b)
    return h


def _reporter_norm_kml_name(name: str) -> str:
    base = os.path.basename(str(name or "").strip())
    return re.sub(r"^[0-9a-fA-F]{6,16}_", "", base)


def _reporter_find_completed_download(c: httpx.Client, base: str, rid: str, fmt: str) -> Optional[str]:
    """历史任务可能把 upload-kml 的 task_id 当作完成报告;按同 KML/区域寻找真实完成任务。"""
    try:
        status_resp = c.get(f"{base}/api/status/{rid}")
        stale = status_resp.json() if status_resp.status_code < 500 else {}
    except Exception:
        stale = {}
    try:
        tasks_resp = c.get(f"{base}/api/tasks")
        payload = tasks_resp.json()
    except Exception:
        return None
    tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(tasks, list):
        return None
    need_key = "has_pptx" if "pptx" in fmt else "has_report"
    stale_kml = _reporter_norm_kml_name((stale or {}).get("kml_name"))
    stale_area = str((stale or {}).get("area_name") or "").strip()
    ranked = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("task_id") or "").strip()
        if not tid or tid == rid:
            continue
        if item.get("status") != "completed" and not item.get(need_key):
            continue
        if not item.get(need_key):
            continue
        score = 0
        cand_kml = _reporter_norm_kml_name(item.get("kml_name"))
        cand_area = str(item.get("area_name") or "").strip()
        if stale_kml and cand_kml == stale_kml:
            score += 80
        if stale_area and cand_area == stale_area:
            score += 40
        elif stale_area and cand_area and (stale_area in cand_area or cand_area in stale_area):
            score += 12
        if score <= 0:
            continue
        ranked.append((score, str(item.get("completed_at") or item.get("created_at") or ""), tid))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][2]


def _reporter_download_links(rid: str) -> dict:
    """报告下载链接(新旧并存,4 文件:旧版 docx/pptx + 新版 docx/pptx)。"""
    return {
        "service": "reporter",
        "task": rid,
        "docx": f"api/download/{rid}",
        "pptx": f"api/download/{rid}?format=pptx",
        "docx_v2": f"api/download/{rid}?version=v2",
        "pptx_v2": f"api/download/{rid}?format=pptx&version=v2",
    }


def _remember_reporter_download(task_id: str, task: dict, rid: str) -> None:
    download = _reporter_download_links(rid)
    task["download"] = download
    task["reporter_task_id"] = rid
    _ADAPTER_TASKS[task_id] = task
    try:
        store.save_adapter_task(task_id, task)
    except Exception:
        pass


@app.get("/api/adapter-report/{task_id}")
def adapter_report(task_id: str, fmt: str = "docx", user=Depends(auth.current_user)):
    """适配器(reporter)产出的报告下载代理。"""
    t = _ADAPTER_TASKS.get(task_id)
    try:
        persisted = store.get_adapter_task(task_id)
        if persisted is not None:
            # 下载链接可能被运维修正到磁盘可恢复的 reporter 历史任务。
            # 优先使用持久化记录，避免进程内旧 _ADAPTER_TASKS 指向已失效的 upload-only task。
            t = persisted
            _ADAPTER_TASKS[task_id] = persisted
    except Exception:
        pass
    dl = (t or {}).get("download") or {}
    rid = dl.get("task")
    if not rid:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    is_pptx = "pptx" in fmt          # fmt ∈ docx|pptx|docx_v2|pptx_v2
    is_v2 = fmt.endswith("_v2")
    # 兼容旧记录(可能只有 docx/pptx 键):新版键缺失时按版本拼 reporter 查询串
    rel = dl.get(fmt)
    if not rel:
        q = []
        if is_pptx: q.append("format=pptx")
        if is_v2: q.append("version=v2")
        rel = f"api/download/{rid}" + ("?" + "&".join(q) if q else "")
    base = services.base_url("reporter")
    url = f"{base}/{rel}"
    try:
        with httpx.Client(timeout=300.0, headers=_internal_headers()) as c:
            r = c.get(url)
            if r.status_code >= 400:
                fallback_rid = _reporter_find_completed_download(c, base, str(rid), fmt)
                if fallback_rid:
                    _remember_reporter_download(task_id, t or {}, fallback_rid)
                    q = []
                    if is_pptx: q.append("format=pptx")
                    if is_v2: q.append("version=v2")
                    rel = f"api/download/{fallback_rid}" + ("?" + "&".join(q) if q else "")
                    r = c.get(f"{base}/{rel}")
        if r.status_code >= 400:
            # 走到这里:原任务产物 404 且未在 reporter 找到可恢复的同区完成任务。
            # 多半是 reporter 重启丢了已生成报告(任务退回 kml_uploaded),给出可操作提示。
            if r.status_code == 404:
                raise HTTPException(status_code=404,
                                    detail="报告产物已丢失(reporter 服务可能已重启),请在报告环节点击「重新生成报告」后再下载")
            raise HTTPException(status_code=r.status_code, detail=r.text[:300] or "报告下载失败")
        media = (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if is_pptx
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        ext = "pptx" if is_pptx else "docx"
        return Response(
            content=r.content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="report.{ext}"'},
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="reporter 服务不可达")


def _resolve_bbox(project_id: Optional[str], bbox: Optional[str], user) -> List[float]:
    """门控+解析 bbox:优先 project_id(走项目读权限)→ aoi_bbox;否则解析裸 bbox 字符串。"""
    if project_id:
        proj, _ = auth.require_project_read(user, project_id)
        bb = proj.get("aoi_bbox")
        if not bb or len(bb) < 4:
            raise HTTPException(status_code=400, detail="项目无 AOI bbox,请先上传 KML")
        return [float(x) for x in bb[:4]]
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox 格式应为 minLon,minLat,maxLon,maxLat")
        if len(parts) < 4 or parts[2] <= parts[0] or parts[3] <= parts[1]:
            raise HTTPException(status_code=400, detail="bbox 非法")
        return parts[:4]
    raise HTTPException(status_code=400, detail="需提供 project_id 或 bbox")


def _run_raster_script(script: str, args: List[str], timeout: float) -> int:
    """系统 python 子进程跑栅格脚本(沿用 _acquire_dem 模式)。返回退出码。"""
    r = subprocess.run([_DC_PYTHON, script, *args], env=_sys_env(),
                       timeout=timeout, capture_output=True)
    return r.returncode


@app.get("/api/basemap")
def basemap(project_id: Optional[str] = None, bbox: Optional[str] = None,
            nocache: int = 0, user=Depends(auth.current_user)):
    """ROI 卫星底图 PNG(Esri World Imagery,服务端拉图+缓存)。供前端 3D 地形贴纹理。"""
    bb = _resolve_bbox(project_id, bbox, user)
    key = hashlib.sha1((",".join(f"{v:.6f}" for v in bb)).encode()).hexdigest()[:16]
    cache_dir = _PORTAL_CACHE / "basemap"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{key}.png"
    if nocache or not out.is_file():
        try:
            rc = _run_raster_script(_FETCH_BASEMAP, [*(str(v) for v in bb), str(out)], 120.0)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="卫星底图获取超时")
        if rc != 0 or not out.is_file():
            raise HTTPException(status_code=503, detail="卫星底图不可用(海域/无覆盖/外网不可达)")
    return FileResponse(str(out), media_type="image/png")


@app.get("/api/terrain")
def terrain(project_id: Optional[str] = None, bbox: Optional[str] = None,
            size: int = 128, nocache: int = 0, user=Depends(auth.current_user)):
    """ROI 高程网格 JSON(Copernicus DEM,重采样 size×size)。供前端 Three.js 顶点位移。"""
    bb = _resolve_bbox(project_id, bbox, user)
    size = max(16, min(256, size))
    key = hashlib.sha1((",".join(f"{v:.6f}" for v in bb)).encode()).hexdigest()[:16]
    cache_dir = _PORTAL_CACHE / "terrain"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{key}_{size}.json"
    if nocache or not out.is_file():
        try:
            rc = _run_raster_script(_FETCH_TERRAIN, [*(str(v) for v in bb), str(size), str(out)], 600.0)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="DEM 获取超时")
        if rc == 2:
            # 无瓦片(海域/无覆盖)→ 前端退化为平面
            return {"flat": True, "bbox": bb, "size": size, "min_m": 0, "max_m": 0, "heights": []}
        if rc != 0 or not out.is_file():
            raise HTTPException(status_code=503, detail="DEM 不可用(外网不可达)")
    return FileResponse(str(out), media_type="application/json")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "geo-portal-bff"}
