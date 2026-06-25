"""几何工具：解析 KML/KMZ 区域文件 → 多边形坐标 + bbox。

自包含实现（不跨服务 import geo-stru），只取首个 Polygon 外环。
"""

import re
import zipfile
from typing import List, Tuple, Optional
from xml.etree import ElementTree as ET


def _strip_ns(tag: str) -> str:
    return tag.split('}', 1)[-1] if '}' in tag else tag


def _parse_coord_text(text: str) -> List[Tuple[float, float]]:
    """KML <coordinates> 文本 'lon,lat[,alt] lon,lat ...' → [(lon,lat), ...]"""
    coords: List[Tuple[float, float]] = []
    for tok in re.split(r'\s+', text.strip()):
        if not tok:
            continue
        parts = tok.split(',')
        if len(parts) >= 2:
            try:
                coords.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return coords


def parse_kml_polygon(kml_bytes: bytes) -> List[Tuple[float, float]]:
    """从 KML 字节流取首个 Polygon 外环坐标（无 Polygon 时回退首个 coordinates）。"""
    root = ET.fromstring(kml_bytes)
    # 优先找 LinearRing/coordinates（Polygon 外环）
    first_coords: Optional[str] = None
    for el in root.iter():
        if _strip_ns(el.tag) == 'coordinates' and el.text and el.text.strip():
            parent_is_ring = False
            # ElementTree 无父引用，简单按出现顺序：先记录第一个 coordinates
            if first_coords is None:
                first_coords = el.text
            # 若该 coordinates 在 LinearRing 下，直接用它
            # （通过再次遍历定位代价高，这里采用：取第一个含>=3点的环）
            pts = _parse_coord_text(el.text)
            if len(pts) >= 3:
                parent_is_ring = True
            if parent_is_ring:
                return pts
    if first_coords:
        return _parse_coord_text(first_coords)
    return []


def parse_polygon(file_path: str) -> List[Tuple[float, float]]:
    """解析 .kml/.kmz → [(lon,lat), ...]。"""
    lp = file_path.lower()
    if lp.endswith('.kmz'):
        with zipfile.ZipFile(file_path, 'r') as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith('.kml')), None)
            if name is None:
                raise ValueError('KMZ 内未找到 .kml')
            data = zf.read(name)
        return parse_kml_polygon(data)
    if lp.endswith('.kml') or lp.endswith('.ovkml'):
        with open(file_path, 'rb') as f:
            return parse_kml_polygon(f.read())
    raise ValueError(f'不支持的区域文件类型: {file_path}（仅 .kml/.kmz）')


def bbox_of(coords: List[Tuple[float, float]]) -> List[float]:
    """[(lon,lat),...] → [min_lon, min_lat, max_lon, max_lat]"""
    if not coords:
        raise ValueError('空多边形，无法计算 bbox')
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [min(lons), min(lats), max(lons), max(lats)]
