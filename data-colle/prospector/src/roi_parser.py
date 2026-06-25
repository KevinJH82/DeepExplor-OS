"""ROI 解析引擎 — 支持 .kml / .ovkml / .xlsx 格式输入"""

import json
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd
import numpy as np
from shapely.geometry import Polygon, MultiPolygon, Point, box, mapping
from shapely.ops import unary_union

from .logger import get_logger

logger = get_logger("roi")


# 常见经纬度/投影坐标列名模式
LON_PATTERNS = [
    r'^经度$', r'^lon$', r'^lng$', r'^longitude$', r'^x$', r'^东经$',
    r'^LON$', r'^LNG$', r'^LONGITUDE$', r'^X$',
    r'.*经度.*', r'.*lon.*', r'.*longitude.*',
]
LAT_PATTERNS = [
    r'^纬度$', r'^lat$', r'^latitude$', r'^y$', r'^北纬$',
    r'^LAT$', r'^LATITUDE$', r'^Y$',
    r'.*纬度.*', r'.*lat.*', r'.*latitude.*',
]

# 命名检测：含"投影"或 XY 坐标列名
PROJ_X_PATTERNS = [r'^x$', r'^X$', r'.*投影.*x.*', r'.*proj.*x.*', r'^easting$', r'^Easting$']
PROJ_Y_PATTERNS = [r'^y$', r'^Y$', r'.*投影.*y.*', r'.*proj.*y.*', r'^northing$', r'^Northing$']


def detect_columns(df: pd.DataFrame) -> Tuple[str, str, bool]:
    """自动检测经纬度列名，返回 (lon_col, lat_col, is_projected)"""
    cols = df.columns.tolist()

    lon_col, lat_col = None, None
    is_projected = False

    # 先试经纬度
    for pat in LON_PATTERNS:
        for col in cols:
            if re.match(pat, str(col)):
                lon_col = col
                break
        if lon_col:
            break

    for pat in LAT_PATTERNS:
        for col in cols:
            if re.match(pat, str(col)):
                lat_col = col
                break
        if lat_col:
            break

    if lon_col and lat_col:
        return lon_col, lat_col, False

    # 再试投影坐标
    for pat in PROJ_X_PATTERNS:
        for col in cols:
            if re.match(pat, str(col)):
                lon_col = col
                is_projected = True
                break
        if lon_col:
            break

    for pat in PROJ_Y_PATTERNS:
        for col in cols:
            if re.match(pat, str(col)):
                lat_col = col
                is_projected = True
                break
        if lat_col:
            break

    if not (lon_col and lat_col):
        # 兜底：取前两列数值列
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) >= 2:
            lon_col, lat_col = numeric_cols[0], numeric_cols[1]
            print(f"⚠️  未识别到标准坐标列名，使用数值列: x={lon_col}, y={lat_col}")  # noqa: T201

    return lon_col, lat_col, is_projected


def parse_xlsx(filepath: str) -> Dict[str, Any]:
    """解析 .xlsx 文件中的坐标点/多边形"""
    xl = pd.ExcelFile(filepath)
    all_points = []

    for sheet_name in xl.sheet_names:
        df = pd.read_excel(filepath, sheet_name=sheet_name)
        lon_col, lat_col, is_projected = detect_columns(df)

        if lon_col is None or lat_col is None:
            logger.warning("Sheet '%s': 未检测到坐标列，跳过", sheet_name)
            continue

        points = []
        for _, row in df.iterrows():
            try:
                x, y = float(row[lon_col]), float(row[lat_col])
                if pd.notna(x) and pd.notna(y):
                    points.append((x, y))
            except (ValueError, TypeError):
                continue

        if points:
            all_points.extend(points)

    return _points_to_geojson(all_points)


def parse_kml(filepath: str) -> Dict[str, Any]:
    """解析 .kml / .ovkml 文件（使用 lxml，无 fiona KML driver 依赖）"""
    from lxml import etree
    import re

    with open(filepath, 'rb') as f:
        tree = etree.parse(f)

    root = tree.getroot()
    # KML 命名空间
    nsmap = root.nsmap

    # 查找所有坐标字符串
    coordinate_elements = []
    for elem in root.iter():
        tag = etree.QName(elem).localname
        if tag == 'coordinates':
            coordinate_elements.append(elem)

    if not coordinate_elements:
        # 尝试不带命名空间
        coordinate_elements = root.findall('.//{http://www.opengis.net/kml/2.2}coordinates')
        if not coordinate_elements:
            coordinate_elements = root.findall('.//{*}coordinates')

    all_points = []
    properties_list = []

    for coord_elem in coordinate_elements:
        text = coord_elem.text
        if not text:
            continue

        # 解析坐标: "lon,lat,alt lon,lat,alt ..."
        points = []
        for coord_str in text.strip().split():
            parts = coord_str.strip().split(',')
            if len(parts) >= 2:
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                    if -180 <= lon <= 180 and -90 <= lat <= 90:
                        points.append((lon, lat))
                except ValueError:
                    continue

        if len(points) >= 3:
            # 找父元素的 name
            parent = coord_elem.getparent()
            name_elem = parent.find('{*}name') if parent is not None else None
            name = name_elem.text if name_elem is not None and name_elem.text else None
            properties_list.append({'name': name} if name else {})

            all_points.append(points)

    if not all_points:
        raise ValueError("KML 文件中未找到任何坐标要素（<coordinates>）")

    # 将各组坐标点转为 Polygon
    polygons = []
    for points in all_points:
        try:
            poly = Polygon(points)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                polygons.append(poly)
        except Exception:
            continue

    if not polygons:
        raise ValueError("无法从坐标点构建有效多边形")

    combined = unary_union(polygons)
    return _geoms_to_geojson(combined, properties_list)


def _to_shapely(geom_dict: Dict) -> Any:
    """将 GeoJSON-like geometry dict 转为 shapely geometry"""
    from shapely.geometry import shape
    return shape(geom_dict)


def _points_to_geojson(points: List[Tuple[float, float]]) -> Dict[str, Any]:
    """点列表转为 GeoJSON"""
    if len(points) == 1:
        geom = Point(points[0])
    elif len(points) == 2:
        # 两点构成矩形
        geom = box(min(p[0] for p in points), min(p[1] for p in points),
                    max(p[0] for p in points), max(p[1] for p in points))
    else:
        polygon = Polygon(points)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        geom = polygon

    return _geoms_to_geojson(geom, [{}])


def _geoms_to_geojson(geom, properties_list: List[Dict]) -> Dict[str, Any]:
    """Shapely geometry → 标准化 GeoJSON dict"""
    if isinstance(geom, (Polygon, Point)):
        geom = MultiPolygon([geom]) if isinstance(geom, Polygon) else geom

    geojson_geom = mapping(geom)
    bbox_bounds = geom.bounds  # (minx, miny, maxx, maxy)

    centroid = geom.centroid
    area_deg2 = geom.area  # 近似面积（度数单位下）

    # 估算实际面积（度数 → km²，粗略换算，适用中纬度）
    center_lat = centroid.y
    deg_to_km_lon = 111.32 * np.cos(np.radians(center_lat))
    deg_to_km_lat = 110.574
    area_km2 = area_deg2 * deg_to_km_lon * deg_to_km_lat

    return {
        "geometry": geojson_geom,
        "bbox": {
            "west": bbox_bounds[0],
            "south": bbox_bounds[1],
            "east": bbox_bounds[2],
            "north": bbox_bounds[3],
        },
        "center": {
            "lon": round(centroid.x, 6),
            "lat": round(centroid.y, 6),
        },
        "area_km2": round(area_km2, 2),
        "crs": "EPSG:4326",
        "properties": properties_list[0] if properties_list else {},
    }


def parse_roi(filepath: str) -> Dict[str, Any]:
    """
    统一入口：解析 ROI 文件

    Args:
        filepath: .kml / .ovkml / .xlsx 文件路径

    Returns:
        {
            "geometry": GeoJSON geometry,
            "bbox": {"west", "south", "east", "north"},
            "center": {"lon", "lat"},
            "area_km2": float,
            "crs": "EPSG:4326",
            "filename": str
        }
    """
    path = Path(filepath)
    ext = path.suffix.lower()

    if ext in ('.kml', '.ovkml', '.kmz'):
        result = parse_kml(filepath)
    elif ext in ('.xlsx', '.xls'):
        result = parse_xlsx(filepath)
    else:
        raise ValueError(f"不支持的文件格式: {ext}，支持 .kml / .ovkml / .xlsx")

    result["filename"] = path.name
    result["filepath"] = str(path.absolute())
    return result


def expand_bbox(roi: Dict[str, Any], buffer_km: float = 20.0) -> Dict[str, Any]:
    """
    对 ROI bbox 进行外扩（用于数据下载时留出边距）

    Args:
        roi: parse_roi 的输出
        buffer_km: 外扩距离 (km)

    Returns:
        带 expanded_bbox 的 roi dict
    """
    b = roi['bbox']
    center_lat = (b['north'] + b['south']) / 2
    deg_per_km_lat = 1.0 / 110.574
    deg_per_km_lon = 1.0 / (111.32 * np.cos(np.radians(center_lat)))

    dlat = buffer_km * deg_per_km_lat
    dlon = buffer_km * deg_per_km_lon

    roi['expanded_bbox'] = {
        "west": max(-180, b['west'] - dlon),
        "south": max(-90, b['south'] - dlat),
        "east": min(180, b['east'] + dlon),
        "north": min(90, b['north'] + dlat),
        "buffer_km": buffer_km,
    }
    return roi


def roi_to_wkt(roi: Dict[str, Any]) -> str:
    """将 ROI geometry 输出为 WKT 字符串"""
    geom = shape_from_geojson(roi['geometry'])
    return geom.wkt


def shape_from_geojson(geojson: Dict) -> Any:
    """GeoJSON geometry → shapely geometry"""
    from shapely.geometry import shape
    return shape(geojson)


def get_bbox_str(roi: Dict[str, Any], use_expanded: bool = True) -> str:
    """获取 bbox 字符串，用于 API 调用"""
    b = roi.get('expanded_bbox', roi['bbox']) if use_expanded else roi['bbox']
    return f"{b['west']},{b['south']},{b['east']},{b['north']}"


def get_bbox_tuple(roi: Dict[str, Any], use_expanded: bool = True) -> Tuple[float, float, float, float]:
    """获取 bbox 元组 (minx, miny, maxx, maxy)"""
    b = roi.get('expanded_bbox', roi['bbox']) if use_expanded else roi['bbox']
    return (b['west'], b['south'], b['east'], b['north'])


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        f = sys.argv[1]
        result = parse_roi(f)
        result = expand_bbox(result)
        print(json.dumps({k: v for k, v in result.items() if k != 'geometry'},
                         indent=2, ensure_ascii=False))
        print(f"\nGeometry type: {result['geometry']['type']}")
        print(f"Area: {result['area_km2']} km²")
