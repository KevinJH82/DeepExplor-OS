"""ROI 上下文分析器 — 解析 KML 并收集 ROI 的地理/地形/气候特征。

确定性模块（无 LLM），通过公开 API 和 broker 扫描获取 ROI 上下文。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from math import radians, sin, cos, sqrt, atan2
from typing import Dict, List, Optional, Tuple


@dataclass
class ROIContext:
    """ROI 上下文信息"""
    bbox: Tuple[float, float, float, float]       # (min_lon, min_lat, max_lon, max_lat)
    area_km2: float
    aoi_name: str
    elevation_range: Tuple[float, float]           # (min_m, max_m)
    mean_slope_deg: float
    climate_zone: str                              # 热带/亚热带/温带/寒带/干旱/半干旱
    vegetation_cover: str                          # 高/中/低/裸地
    cloud_coverage: str                            # 高/中/低
    tectonic_setting: str                          # 大地构造背景（初步推断）
    existing_products: Dict[str, bool] = field(default_factory=dict)
    existing_product_details: Dict[str, dict] = field(default_factory=dict)


def _haversine_km(lon1, lat1, lon2, lat2):
    """两点间球面距离(km)。"""
    R = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _parse_kml_bbox(kml_path: str) -> Optional[Tuple[float, float, float, float]]:
    """从 KML/KMZ/OVKML 解析 bbox (min_lon, min_lat, max_lon, max_lat)。"""
    ext = kml_path.rsplit('.', 1)[-1].lower()

    if ext == 'kmz':
        import zipfile
        with zipfile.ZipFile(kml_path) as zf:
            kml_name = next((n for n in zf.namelist() if n.endswith('.kml')), None)
            if not kml_name:
                return None
            with zf.open(kml_name) as f:
                xml_content = f.read()
    else:
        with open(kml_path, 'rb') as f:
            xml_content = f.read()

    root = ET.fromstring(xml_content)
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}

    coords_text = []
    for el in root.iter():
        tag = el.tag.split('}')[-1] if '}' in el.tag else el.tag
        if tag == 'coordinates' and el.text:
            coords_text.append(el.text.strip())

    if not coords_text:
        return None

    pts = []
    for block in coords_text:
        for pair in block.split():
            parts = pair.strip().split(',')
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue

    if not pts:
        return None

    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return (min(lons), min(lats), max(lons), max(lats))


def _fetch_elevation(bbox):
    """从 OpenTopoData SRTM30 获取高程范围。"""
    min_lon, min_lat, max_lon, max_lat = bbox
    try:
        lats = [min_lat, (min_lat + max_lat) / 2, max_lat]
        lons = [min_lon, (min_lon + max_lon) / 2, max_lon]
        locations = "|".join(f"{la},{lo}" for la in lats for lo in lons)
        url = f"https://api.opentopodata.org/v1/srtm30m?locations={locations}"
        req = urllib.request.Request(url, headers={"User-Agent": "geo-orchestrator/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        elevs = [r["elevation"] for r in data.get("results", []) if r.get("elevation") is not None]
        if elevs:
            return (round(min(elevs)), round(max(elevs)))
    except Exception:
        pass
    return (0, 1000)


def _fetch_climate(lat, lon):
    """从 Open-Meteo 获取气候数据，推断气候带和植被覆盖。"""
    try:
        params = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "start_date": "2020-01-01", "end_date": "2023-12-31",
            "daily": "temperature_2m_max,precipitation_sum,et0_fao_evapotranspiration",
            "timezone": "auto", "models": "ERA5"
        })
        url = f"https://archive.open-meteo.com/v1/archive?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "geo-orchestrator/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        daily = data.get("daily", {})
        temps = [t for t in daily.get("temperature_2m_max", []) if t is not None]
        precip = [p for p in daily.get("precipitation_sum", []) if p is not None]
        et0 = [e for e in daily.get("et0_fao_evapotranspiration", []) if e is not None]

        if not temps:
            return "温带", "中", "中"

        ann_temp = sum(temps) / len(temps)
        ann_precip = sum(precip) / max(len(precip) / 4, 1)  # 年均
        ann_et0 = sum(et0) / max(len(et0) / 4, 1)

        # 气候带推断
        if abs(lat) < 23.5:
            zone = "热带" if ann_temp > 22 else "亚热带"
        elif abs(lat) < 40:
            zone = "亚热带" if ann_temp > 14 else "温带"
        elif abs(lat) < 60:
            zone = "温带" if ann_temp > 0 else "寒带"
        else:
            zone = "寒带"

        if ann_precip < 250:
            zone += "/干旱"
        elif ann_precip < 500:
            zone += "/半干旱"

        # 植被覆盖（ET0/降水 比值的粗略代理）
        if ann_precip > 0:
            aridity = ann_et0 / ann_precip
            veg = "高" if aridity < 0.5 else "中" if aridity < 1.0 else "低" if aridity < 2.0 else "裸地"
        else:
            veg = "裸地"

        # 云覆盖推断（高降水+高纬 = 高云覆盖）
        cloud = "高" if ann_precip > 1200 else "中" if ann_precip > 500 else "低"

        return zone, veg, cloud

    except Exception:
        return "温带", "中", "中"


def _infer_tectonic(lat, lon, elevation_range):
    """基于位置和海拔初步推断大地构造背景。"""
    min_e, max_e = elevation_range
    if min_e > 3000:
        return "高原/造山带（青藏高原等）"
    if max_e - min_e > 1500:
        return "造山带（地形起伏大）"
    if abs(lat) < 30 and max_e < 500:
        return "被动陆缘/沉积盆地"
    if 30 <= abs(lat) <= 55 and max_e < 300:
        return "克拉通/稳定地台"
    return "待确定"


def _scan_existing_products(bbox, roots: dict) -> Tuple[Dict[str, bool], Dict[str, dict]]:
    """扫描各 broker 已有产物。"""
    import sys
    for _repo in (os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "/opt/deepexplor-services"):
        if _repo not in sys.path:
            sys.path.insert(0, _repo)

    existing = {}
    details = {}

    def _try_find(module_name, func_name, root_key, label):
        try:
            mod = __import__(module_name, fromlist=[func_name])
            func = getattr(mod, func_name)
            matches = func(bbox, roots.get(root_key, ''))
            existing[label] = bool(matches)
            if matches:
                details[label] = matches[0]
        except Exception:
            existing[label] = False

    _try_find('commons.analyser_broker', 'find_alteration_for_bbox', 'analyser', 'geo_analyser')
    _try_find('commons.structural_broker', 'find_structural_for_bbox', 'stru', 'geo_stru')
    _try_find('commons.exploration_broker', 'find_exploration_for_bbox', 'exploration', 'geo_exploration')
    _try_find('commons.datacolle_broker', 'find_datacolle_for_bbox', 'datacolle', 'data_colle')
    _try_find('commons.model3d_broker', 'find_model3d_for_bbox', 'model3d', 'geo_model3d')
    _try_find('commons.geophys_broker', 'find_geophys_for_bbox', 'geophys', 'geo_geophys')
    _try_find('commons.geochem_broker', 'find_geochem_for_bbox', 'geochem', 'geo_geochem')
    _try_find('commons.drill_broker', 'find_drill_for_bbox', 'drill', 'geo_drill')
    # InSAR：与其他服务一致，用 bbox 相交的形变证据契约（insar_metadata.json 的 aoi_bbox）判定。
    # 旧逻辑是 os.listdir(任意子目录即视为已有)，会把其它 ROI 的下载误判为本区已有产物。
    _try_find('commons.insar_broker', 'find_insar_for_bbox', 'insar', 'geo_insar')

    # Downloader：扫描 downloads 目录
    dl_root = roots.get('downloader', '')
    if dl_root and os.path.isdir(dl_root):
        existing['geo_downloader'] = bool(os.listdir(dl_root))
    else:
        existing['geo_downloader'] = False

    return existing, details


class ROIAnalyzer:
    """分析 ROI 的地理/地形/气候特征，扫描已有产物。"""

    @staticmethod
    def analyze(kml_path: str, roots: dict) -> ROIContext:
        bbox = _parse_kml_bbox(kml_path)
        if not bbox:
            raise ValueError("无法从文件中解析 ROI 边界，请确认 KML/KMZ 格式正确")

        min_lon, min_lat, max_lon, max_lat = bbox

        # 面积
        center_lat = (min_lat + max_lat) / 2
        width_km = _haversine_km(min_lon, center_lat, max_lon, center_lat)
        height_km = _haversine_km(center_lat, min_lat, center_lat, max_lat)
        area_km2 = round(width_km * height_km, 1)

        # AOI 名
        aoi_name = os.path.splitext(os.path.basename(kml_path))[0]
        aoi_name = re.sub(r'^\d+_', '', aoi_name)  # 去时间戳前缀

        # 高程
        elev_range = _fetch_elevation(bbox)

        # 气候
        center_lon = (min_lon + max_lon) / 2
        climate_zone, veg_cover, cloud = _fetch_climate(center_lat, center_lon)

        # 构造背景
        tectonic = _infer_tectonic(center_lat, center_lon, elev_range)

        # 已有产物
        existing, details = _scan_existing_products(bbox, roots)

        return ROIContext(
            bbox=bbox,
            area_km2=area_km2,
            aoi_name=aoi_name,
            elevation_range=elev_range,
            mean_slope_deg=0.0,  # 精确计算需 DEM，此处简化
            climate_zone=climate_zone,
            vegetation_cover=veg_cover,
            cloud_coverage=cloud,
            tectonic_setting=tectonic,
            existing_products=existing,
            existing_product_details=details,
        )

    @staticmethod
    def to_dict(ctx: ROIContext) -> dict:
        return {
            'bbox': list(ctx.bbox),
            'area_km2': ctx.area_km2,
            'aoi_name': ctx.aoi_name,
            'elevation_range': list(ctx.elevation_range),
            'climate_zone': ctx.climate_zone,
            'vegetation_cover': ctx.vegetation_cover,
            'cloud_coverage': ctx.cloud_coverage,
            'tectonic_setting': ctx.tectonic_setting,
            'existing_products': ctx.existing_products,
        }
