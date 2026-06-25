"""
KML Parser Module
解析KML文件，提取Polygon几何体和边界框（BBox），以及区块名称和地质背景描述。
支持Point、Polygon、MultiGeometry等类型。
使用 lxml 直接解析，兼容所有 fastkml 版本。
"""

import re
import zipfile
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional

try:
    from lxml import etree
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

try:
    from shapely.geometry import shape, MultiPolygon, Polygon, Point, LineString
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

# KML命名空间
_KML_NS = "http://www.opengis.net/kml/2.2"
_KML_NS_OLD = "http://earth.google.com/kml/2.1"

# 点坐标转多边形时的默认缓冲距离（约0.1度≈11km）
DEFAULT_POINT_BUFFER_DEG = 0.1


class KMLParseError(Exception):
    pass


def _check_dependencies():
    if not HAS_LXML:
        raise KMLParseError("缺少依赖: lxml\n请运行: pip3 install lxml")
    if not HAS_SHAPELY:
        raise KMLParseError("缺少依赖: shapely\n请运行: pip3 install shapely")


def _parse_coordinates(coord_text: str) -> list:
    """解析KML coordinates文本为 [(lon, lat), ...] 列表"""
    points = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                points.append((lon, lat))
            except ValueError:
                continue
    return points


def _find_tag(element, local_name: str, ns: str):
    """在元素下查找指定标签（兼容不同命名空间）"""
    for n in [ns, _KML_NS, _KML_NS_OLD, ""]:
        tag = f"{{{n}}}{local_name}" if n else local_name
        found = element.find(f".//{tag}")
        if found is not None:
            return found
    return None


def _find_all_tag(element, local_name: str, ns: str):
    """查找所有匹配标签"""
    results = []
    for n in [ns, _KML_NS, _KML_NS_OLD, ""]:
        tag = f"{{{n}}}{local_name}" if n else local_name
        results.extend(element.findall(f".//{tag}"))
    # 去重（不同命名空间可能重复）
    seen = set()
    unique = []
    for r in results:
        rid = id(r)
        if rid not in seen:
            seen.add(rid)
            unique.append(r)
    return unique


def _extract_polygon(element, ns: str) -> Optional[Polygon]:
    """从 <Polygon> 元素提取 Shapely Polygon"""
    outer = _find_tag(element, "outerBoundaryIs", ns)
    if outer is None:
        return None
    lr = _find_tag(outer, "LinearRing", ns)
    if lr is None:
        return None
    coord_el = _find_tag(lr, "coordinates", ns)
    if coord_el is None or not coord_el.text:
        return None
    outer_coords = _parse_coordinates(coord_el.text)
    if len(outer_coords) < 3:
        return None

    # 内环（岛屿/洞）
    holes = []
    for inner_tag in (f"{{{ns}}}innerBoundaryIs", "innerBoundaryIs"):
        for inner in element.findall(f".//{inner_tag}"):
            ilr = _find_tag(inner, "LinearRing", ns)
            if ilr is not None:
                ic = _find_tag(ilr, "coordinates", ns)
                if ic is not None and ic.text:
                    hole_coords = _parse_coordinates(ic.text)
                    if len(hole_coords) >= 3:
                        holes.append(hole_coords)

    try:
        return Polygon(outer_coords, holes)
    except Exception:
        return None


def _extract_geometries_recursive(element, ns: str) -> list:
    """递归提取元素中所有几何体，返回 Shapely 几何体列表"""
    geoms = []
    # 跳过注释节点、处理指令节点（tag 不是字符串）
    if not isinstance(element.tag, str):
        return geoms
    local = etree.QName(element.tag).localname if element.tag else ""

    if local == "Polygon":
        p = _extract_polygon(element, ns)
        if p and p.is_valid:
            geoms.append(p)

    elif local == "Point":
        coord_el = _find_tag(element, "coordinates", ns)
        if coord_el is not None and coord_el.text:
            pts = _parse_coordinates(coord_el.text)
            if pts:
                geoms.append(Point(pts[0]))

    elif local == "LineString":
        coord_el = _find_tag(element, "coordinates", ns)
        if coord_el is not None and coord_el.text:
            pts = _parse_coordinates(coord_el.text)
            if len(pts) >= 2:
                geoms.append(LineString(pts))

    elif local == "MultiGeometry":
        for child in element:
            geoms.extend(_extract_geometries_recursive(child, ns))

    # 递归处理子元素（Document/Folder/Placemark）
    if local in ("Document", "Folder", "kml", "KML"):
        for child in element:
            geoms.extend(_extract_geometries_recursive(child, ns))
    elif local == "Placemark":
        for child in element:
            geoms.extend(_extract_geometries_recursive(child, ns))

    return geoms


def _extract_placemark_metadata(element, ns: str) -> Tuple[str, str]:
    """从 Placemark 元素提取 <name> 和 <description>"""
    name = ""
    description = ""

    name_el = _find_tag(element, "name", ns)
    if name_el is not None and name_el.text:
        name = name_el.text.strip()
        # 规范化研究区名称中独立出现的 aoi → AOI
        name = re.sub(r"\baoi\b", "AOI", name, flags=re.IGNORECASE)

    desc_el = _find_tag(element, "description", ns)
    if desc_el is not None and desc_el.text:
        description = desc_el.text.strip()

    return name, description


def parse_kml(kml_path: str, point_buffer_deg: float = DEFAULT_POINT_BUFFER_DEG):
    """
    解析KML文件，返回合并后的Shapely几何体、BBox、文件名、区块名称和地质背景描述。

    Returns
    -------
    geometry : shapely.geometry  合并后的几何体
    bbox     : (min_lon, min_lat, max_lon, max_lat)
    name     : str  KML文件名（不含扩展名）
    area_name : str  KML 中 <Placemark><name> 元素值
    description : str  KML 中 <Placemark><description> 元素值
    """
    _check_dependencies()

    kml_path = Path(kml_path)
    if not kml_path.exists():
        raise KMLParseError(f"KML文件不存在: {kml_path}")

    suffix = kml_path.suffix.lower()

    if suffix not in (".kml", ".ovkml", ".kmz", ".ovkmz"):
        raise KMLParseError(f"不支持的文件格式: {kml_path.suffix}（仅支持 .kml / .ovkml / .kmz / .ovkmz）")

    name = kml_path.stem

    # KMZ/ovKMZ：解压后取 doc.kml
    if suffix in (".kmz", ".ovkmz"):
        try:
            with zipfile.ZipFile(kml_path, "r") as zf:
                kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
                if not kml_names:
                    raise KMLParseError(f"KMZ文件中未找到 .kml 文件: {kml_path}")
                # 优先取 doc.kml，否则取第一个
                inner = "doc.kml" if "doc.kml" in kml_names else kml_names[0]
                with tempfile.TemporaryDirectory() as tmpdir:
                    zf.extract(inner, tmpdir)
                    tmp_kml = Path(tmpdir) / inner
                    tree = etree.parse(str(tmp_kml))
                    root = tree.getroot()
        except zipfile.BadZipFile:
            raise KMLParseError(f"KMZ文件损坏或格式错误: {kml_path}")
        except KMLParseError:
            raise
        except Exception as e:
            raise KMLParseError(f"KMZ解析失败: {e}")
    else:
        try:
            tree = etree.parse(str(kml_path))
            root = tree.getroot()
        except Exception as e:
            raise KMLParseError(f"KML解析失败: {e}")

    # 检测命名空间
    ns = _KML_NS
    if root.tag and "{" in root.tag:
        ns = root.tag.split("}")[0].lstrip("{")

    # 提取 Placemark 元数据（名称、描述）
    area_name = ""
    description = ""
    placemark_els = _find_all_tag(root, "Placemark", ns)
    if placemark_els:
        area_name, description = _extract_placemark_metadata(placemark_els[0], ns)

    raw_geoms = _extract_geometries_recursive(root, ns)

    if not raw_geoms:
        raise KMLParseError(f"KML文件中未找到任何几何要素: {kml_path}")

    # Point/LineString 转多边形（缓冲）
    # 但如果 KML 中已有 Polygon，则 Point 仅作为标注，不参与裁剪几何
    has_polygon = any(
        g.geom_type in ("Polygon", "MultiPolygon") for g in raw_geoms
    )

    shapely_geoms = []
    for g in raw_geoms:
        if g.geom_type == "Point":
            if not has_polygon:
                # 仅当 KML 全是 Point 时才 buffer 生成裁剪区域
                shapely_geoms.append(g.buffer(point_buffer_deg))
        elif g.geom_type == "LineString":
            if not has_polygon:
                shapely_geoms.append(g.buffer(point_buffer_deg / 2))
        else:
            shapely_geoms.append(g)

    if not shapely_geoms:
        # 理论上不会到这里，但以防万一
        for g in raw_geoms:
            if g.geom_type == "Point":
                shapely_geoms.append(g.buffer(point_buffer_deg))
            elif g.geom_type == "LineString":
                shapely_geoms.append(g.buffer(point_buffer_deg / 2))

    # 用 MultiPolygon 保留各地块独立形状，避免 unary_union 将相邻/相交地块
    # 合并成凸包轮廓（导致裁剪结果变成圆形或矩形）
    if len(shapely_geoms) == 1:
        merged = shapely_geoms[0]
    else:
        # 收集所有独立多边形（展开 MultiPolygon/GeometryCollection）
        all_polys = []
        for g in shapely_geoms:
            if g.geom_type == "Polygon":
                all_polys.append(g)
            elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
                for sub in g.geoms:
                    if sub.geom_type == "Polygon":
                        all_polys.append(sub)
        merged = MultiPolygon(all_polys) if all_polys else shapely_geoms[0]

    bbox = merged.bounds  # (minx, miny, maxx, maxy)

    return merged, bbox, name, area_name, description
