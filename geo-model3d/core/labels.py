"""已知矿点标签层（方向四 P1）——为数据驱动 scorer 提供真实正样本。

三路取数、缺失只降级不报错：
1. USGS MRDS WFS 实时查询（**保留 geometry 经纬度**，按 bbox/矿种过滤）+ 本地缓存；
2. 用户上传已知矿点 CSV(lon,lat[,commodity]) / GeoJSON；
3. 二者皆缺 → 返回 []，由上层回退知识融合。

硬约束：**绝不**读 geo-exploration 的 prospecting_targets（预测靶点≠已知矿点，用作标签=循环论证）。
"""

from __future__ import annotations

import os
import csv
import json
import hashlib
from typing import Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

USGS_MRDS_ENDPOINT = os.environ.get(
    "USGS_MRDS_ENDPOINT", "https://mrdata.usgs.gov/services/wfs/mrds")
USGS_BUFFER_DEG = 0.25      # 查询向外扩展(度)；返回后按真实 bbox 裁剪
USGS_TIMEOUT = 10
USGS_MAX_FEATURES = 500

# 中文矿种 → MRDS commod1 代码集合（粗匹配；大小写不敏感）
_CN_TO_MRDS: Dict[str, set] = {
    "铜": {"CU"}, "铜钼": {"CU", "MO"}, "钼": {"MO"},
    "金": {"AU"}, "银": {"AG"}, "铅锌": {"PB", "ZN"},
    "铁": {"FE"}, "镍": {"NI"}, "铬": {"CR"}, "钛": {"TI"},
    "钨": {"W"}, "锡": {"SN"}, "钨锡": {"W", "SN"},
    "锂": {"LI"}, "稀土": {"REE", "CE", "LA", "Y", "ND"},
    "铀": {"U"}, "锰": {"MN"}, "铂族": {"PT", "PD"},
}


def _bbox_hash(bbox) -> str:
    s = ",".join(f"{round(float(v), 2)}" for v in bbox)
    return hashlib.md5(s.encode()).hexdigest()[:12]


def _commodity_match(commod: str, mineral: str) -> bool:
    """commod 是否匹配用户矿种。兼容 MRDS 英文代码(AU) 与中文矿种名(金)。
    无矿种/无 commod 时不否决（宽松）。"""
    m = (mineral or "").strip()
    c = (commod or "").strip()
    if not m or not c:
        return True
    # 中文直配：矿种名与 commod 互含（覆盖'金'∈'金矿'、'铜钼'∋'钼'等）
    if any(ch in c for ch in m) or m in c:
        return True
    codes = _CN_TO_MRDS.get(m)
    if not codes:
        return True
    return any(code in c.upper() for code in codes)


def _in_bbox(lon, lat, bbox) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


# 美国本土+阿拉斯加+夏威夷的粗包围盒（含缓冲）；AOI 完全在外则跳过 MRDS（USGS 仅覆盖美国）。
_US_BBOXES = (
    (-125.0, 24.0, -66.0, 50.0),     # 本土 48 州
    (-170.0, 51.0, -129.0, 72.0),    # 阿拉斯加
    (-161.0, 18.0, -154.0, 23.0),    # 夏威夷
)


def _aoi_in_us(bbox) -> bool:
    """AOI 是否与美国覆盖范围相交（相交才值得查 MRDS）。"""
    for (x0, y0, x1, y1) in _US_BBOXES:
        if not (bbox[2] < x0 or bbox[0] > x1 or bbox[3] < y0 or bbox[1] > y1):
            return True
    return False


def _fetch_mrds(bbox, cache_dir: Optional[str]) -> Tuple[List[Dict], str]:
    """查 USGS MRDS WFS（保留经纬度）。失败回退缓存。返回 (points, status)。"""
    if not _aoi_in_us(bbox):
        return [], "skipped_non_us"      # 中国等域外 AOI：跳过 WFS，避免 10s 挂起与噪声
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"mrds_{_bbox_hash(bbox)}.geojson")

    fc = None
    try:
        import requests
        q = {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
             "typeName": "mrds", "outputFormat": "application/json",
             "bbox": f"{bbox[0]-USGS_BUFFER_DEG},{bbox[1]-USGS_BUFFER_DEG},"
                     f"{bbox[2]+USGS_BUFFER_DEG},{bbox[3]+USGS_BUFFER_DEG},EPSG:4326",
             "maxFeatures": USGS_MAX_FEATURES}
        r = requests.get(USGS_MRDS_ENDPOINT, params=q, timeout=USGS_TIMEOUT)
        r.raise_for_status()
        fc = r.json()
        if cache_path:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(fc, f)
        status = "wfs"
    except Exception as e:
        logger.info(f"MRDS WFS 查询失败({e})，尝试缓存")
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    fc = json.load(f)
                status = "cache"
            except Exception:
                return [], "fetch_failed"
        else:
            return [], "fetch_failed"

    pts = []
    for ft in (fc or {}).get("features", []):
        geom = ft.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        props = ft.get("properties", {}) or {}
        pts.append({"lon": lon, "lat": lat,
                    "commodity": str(props.get("commod1", "") or ""),
                    "deposit_type": str(props.get("dep_type", "") or ""),
                    "name": str(props.get("site_name", "") or ""),
                    "source": "USGS_MRDS"})
    return pts, status


def _parse_upload(path: str) -> List[Dict]:
    """解析用户上传：CSV(lon,lat[,commodity]) 或 GeoJSON 点。"""
    lp = path.lower()
    out: List[Dict] = []
    try:
        if lp.endswith(".geojson") or lp.endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                fc = json.load(f)
            for ft in fc.get("features", []):
                g = ft.get("geometry") or {}
                if g.get("type") == "Point" and len(g.get("coordinates", [])) >= 2:
                    c = g["coordinates"]
                    pr = ft.get("properties", {}) or {}
                    out.append({"lon": float(c[0]), "lat": float(c[1]),
                                "commodity": str(pr.get("commodity", "") or ""),
                                "deposit_type": str(pr.get("deposit_type", "") or ""),
                                "name": str(pr.get("name", "") or ""), "source": "upload"})
        else:  # CSV
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rdr = csv.DictReader(f)
                cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
                lon_c = next((cols[k] for k in ("lon", "longitude", "x", "经度") if k in cols), None)
                lat_c = next((cols[k] for k in ("lat", "latitude", "y", "纬度") if k in cols), None)
                com_c = next((cols[k] for k in ("commodity", "mineral", "矿种", "commod") if k in cols), None)
                if lon_c and lat_c:
                    for row in rdr:
                        try:
                            lon = float(row[lon_c]); lat = float(row[lat_c])
                        except (ValueError, TypeError, KeyError):
                            continue
                        out.append({"lon": lon, "lat": lat,
                                    "commodity": str(row.get(com_c, "") if com_c else ""),
                                    "deposit_type": "", "name": "", "source": "upload"})
    except Exception as e:
        logger.info(f"已知矿点上传解析失败: {e}")
    return out


def _load_local_library(bbox, mineral: str, lib_dir: Optional[str]) -> List[Dict]:
    """扫描本地矿点库目录(.geojson/.csv)，按 bbox 裁剪 + 矿种粗过滤，返回正样本。

    用于补充 MRDS 未覆盖的区域(如中国)：把真实已知矿点 GeoJSON/CSV 放进该目录即可，
    无需每次上传。复用 _parse_upload 解析；source 标记为 local_library。
    """
    if not lib_dir or not os.path.isdir(lib_dir):
        return []
    out: List[Dict] = []
    for fn in sorted(os.listdir(lib_dir)):
        lp = fn.lower()
        if not (lp.endswith(".geojson") or lp.endswith(".json") or lp.endswith(".csv")):
            continue
        recs = _parse_upload(os.path.join(lib_dir, fn))
        for p in recs:
            p["source"] = "local_library"
            if _in_bbox(p["lon"], p["lat"], bbox) and _commodity_match(p.get("commodity", ""), mineral):
                out.append(p)
    return out


def load_known_deposits(bbox, mineral: str, roots: Dict[str, str] = None,
                        upload_path: Optional[str] = None,
                        cache_dir: Optional[str] = None,
                        local_lib_dir: Optional[str] = None) -> Tuple[List[Dict], Dict]:
    """
    返回 (points, provenance)。points=[{lon,lat,commodity,deposit_type,name,source}]，
    已按真实 bbox 裁剪、按矿种粗过滤。任何源缺失只降级；绝不读预测靶点。
    源优先级：用户上传 > 本地矿点库 > USGS MRDS。
    """
    prov: Dict = {"sources": []}
    pts: List[Dict] = []

    # 1) 用户上传（最高优先级）
    if upload_path and os.path.exists(upload_path):
        up = _parse_upload(upload_path)
        up = [p for p in up if _in_bbox(p["lon"], p["lat"], bbox)]
        if up:
            pts.extend(up)
            prov["sources"].append({"source": "upload", "n": len(up),
                                    "path": os.path.basename(upload_path)})

    # 2) 本地矿点库（覆盖中国等 MRDS 盲区）
    lib = _load_local_library(bbox, mineral, local_lib_dir)
    if lib:
        pts.extend(lib)
    prov["sources"].append({"source": "local_library", "n_after_filter": len(lib),
                            "dir": os.path.basename(local_lib_dir.rstrip("/")) if local_lib_dir else None})

    # 3) USGS MRDS（实时 + 缓存）
    mrds, mstatus = _fetch_mrds(bbox, cache_dir)
    mrds = [p for p in mrds if _in_bbox(p["lon"], p["lat"], bbox)
            and _commodity_match(p["commodity"], mineral)]
    if mrds:
        pts.extend(mrds)
    prov["sources"].append({"source": "USGS_MRDS", "status": mstatus,
                            "n_after_filter": len(mrds), "mineral": mineral})

    # 去重（同坐标四舍五入）
    seen = set(); uniq = []
    for p in pts:
        k = (round(p["lon"], 5), round(p["lat"], 5))
        if k not in seen:
            seen.add(k); uniq.append(p)
    prov["n_positive"] = len(uniq)
    logger.info(f"load_known_deposits: {len(uniq)} 个已知矿点（矿种={mineral}）")
    return uniq, prov


def load_drill_feedback(bbox, drill_path: Optional[str]) -> Tuple[List[Dict], List[Dict], Dict]:
    """钻孔回灌（方向四 P4，预测-验证-优化闭环入口）。

    读钻孔验证结果 CSV(lon,lat,outcome) / GeoJSON：
      outcome ∈ {ore, 见矿, hit, positive} → 确认见矿（正样本）
      outcome ∈ {barren, 无矿, miss, negative} → 确认无矿（**真负样本**，平台首个真负来源）
    返回 (confirmed_ore[], confirmed_barren[], provenance)。无文件/无方向五反馈 → 空(no-op)。
    """
    prov = {"status": "absent"}
    if not drill_path or not os.path.exists(drill_path):
        return [], [], prov
    ore_kw = {"ore", "见矿", "hit", "positive", "pos", "1", "true"}
    barren_kw = {"barren", "无矿", "miss", "negative", "neg", "0", "false"}
    raw = _parse_upload(drill_path)  # 复用点位解析（lon/lat），outcome 另读
    # _parse_upload 不取 outcome，这里单独再解析一遍取 outcome
    outcomes = _read_outcomes(drill_path)
    ore, barren = [], []
    for i, p in enumerate(raw):
        if not _in_bbox(p["lon"], p["lat"], bbox):
            continue
        oc = (outcomes[i] if i < len(outcomes) else "").strip().lower()
        rec = {"lon": p["lon"], "lat": p["lat"], "commodity": p.get("commodity", ""),
               "deposit_type": "", "name": p.get("name", ""), "source": "drill"}
        if oc in ore_kw:
            ore.append(rec)
        elif oc in barren_kw:
            barren.append(rec)
    prov = {"status": "ok", "n_ore": len(ore), "n_barren": len(barren),
            "path": os.path.basename(drill_path)}
    logger.info(f"load_drill_feedback: 见矿 {len(ore)}, 无矿(真负) {len(barren)}")
    return ore, barren, prov


def _read_outcomes(path: str) -> List[str]:
    """从钻孔文件按行序读 outcome 列（与 _parse_upload 同序）。"""
    lp = path.lower()
    out: List[str] = []
    try:
        if lp.endswith(".geojson") or lp.endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                fc = json.load(f)
            for ft in fc.get("features", []):
                g = ft.get("geometry") or {}
                if g.get("type") == "Point" and len(g.get("coordinates", [])) >= 2:
                    out.append(str((ft.get("properties", {}) or {}).get("outcome", "")))
        else:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rdr = csv.DictReader(f)
                cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
                oc_c = next((cols[k] for k in ("outcome", "result", "见矿", "验证", "status") if k in cols), None)
                lon_c = next((cols[k] for k in ("lon", "longitude", "x", "经度") if k in cols), None)
                lat_c = next((cols[k] for k in ("lat", "latitude", "y", "纬度") if k in cols), None)
                for row in rdr:
                    if lon_c and lat_c:
                        try:
                            float(row[lon_c]); float(row[lat_c])
                        except (ValueError, TypeError, KeyError):
                            continue
                    out.append(str(row.get(oc_c, "") if oc_c else ""))
    except Exception as e:
        logger.info(f"钻孔 outcome 解析跳过: {e}")
    return out


def rasterize_points(points: List[Dict], grid) -> List[Tuple[int, int]]:
    """已知矿点经纬度 → 网格 (row,col) 去重；落在网格外的点丢弃。"""
    rc = []
    seen = set()
    for p in points:
        loc = grid.lonlat_to_rowcol(p["lon"], p["lat"])
        if loc is not None and loc not in seen:
            seen.add(loc); rc.append(loc)
    return rc
