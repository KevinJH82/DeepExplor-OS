"""
Tabular File Parser Module
解析 CSV / Excel 文件，从表格数据中提取坐标、区块名称和描述。

期望的表格格式（列名不区分大小写、支持中英文）：
  经度/longitude/lon/x  纬度/latitude/lat/y  名称/name  描述/description
至少需要经度和纬度两列。
"""

import re
from pathlib import Path
from typing import Tuple, Optional, List

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    from shapely.geometry import MultiPolygon, Polygon, Point
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


class TabularParseError(Exception):
    pass


# 列名别名映射
_LON_ALIASES = {"经度", "longitude", "lon", "long", "x", "经", "lng"}
_LAT_ALIASES = {"纬度", "latitude", "lat", "y", "纬"}
_NAME_ALIASES = {"名称", "name", "区块名", "区块名称", "项目名", "项目名称", "title"}
_DESC_ALIASES = {"描述", "description", "desc", "地质背景", "备注", "remark", "notes"}

# 点缓冲半径（度，约 10km）
DEFAULT_POINT_BUFFER_DEG = 0.1


def _find_col(columns: List[str], aliases: set) -> Optional[str]:
    """在列名列表中查找匹配别名的第一个列（不区分大小写，支持子串匹配）"""
    for col in columns:
        col_lower = col.strip().lower()
        for alias in aliases:
            if alias in col_lower:
                return col
    return None


def parse_tabular(file_path: str, point_buffer_deg: float = DEFAULT_POINT_BUFFER_DEG):
    """
    解析 CSV 或 Excel 文件，提取坐标构成几何体。

    Returns
    -------
    geometry : shapely.geometry
    bbox     : (min_lon, min_lat, max_lon, max_lat)
    name     : str  文件名（不含扩展名）
    area_name : str  从名称列读取，若无则用文件名
    description : str  从描述列读取，若无则为空字符串
    """
    if not HAS_PANDAS:
        raise TabularParseError("缺少依赖: pandas\n请运行: pip3 install pandas openpyxl")
    if not HAS_SHAPELY:
        raise TabularParseError("缺少依赖: shapely\n请运行: pip3 install shapely")

    path = Path(file_path)
    if not path.exists():
        raise TabularParseError(f"文件不存在: {path}")

    suffix = path.suffix.lower()

    try:
        if suffix == ".csv":
            # 尝试常见编码
            for enc in ("utf-8-sig", "utf-8", "gbk", "gb2312"):
                try:
                    df = pd.read_csv(file_path, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise TabularParseError("CSV 文件编码无法识别，请转换为 UTF-8 后重试")
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, engine="openpyxl" if suffix == ".xlsx" else None)
        else:
            raise TabularParseError(f"不支持的表格格式: {suffix}")
    except TabularParseError:
        raise
    except Exception as e:
        raise TabularParseError(f"文件读取失败: {e}")

    if df.empty:
        raise TabularParseError("文件内容为空")

    # 列名小写化用于匹配
    col_map = {col: col.strip().lower() for col in df.columns}
    lower_cols = list(col_map.values())

    lon_col = _find_col(list(col_map.keys()), _LON_ALIASES)
    lat_col = _find_col(list(col_map.keys()), _LAT_ALIASES)

    if lon_col is None or lat_col is None:
        raise TabularParseError(
            f"未找到经纬度列。当前列名：{list(df.columns)}\n"
            "请确保表格包含经度列（longitude/lon/x/经度）和纬度列（latitude/lat/y/纬度）"
        )

    name_col = _find_col(list(col_map.keys()), _NAME_ALIASES)
    desc_col = _find_col(list(col_map.keys()), _DESC_ALIASES)

    # 清洗坐标列
    df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
    df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
    df = df.dropna(subset=[lon_col, lat_col])

    if df.empty:
        raise TabularParseError("经纬度列中未找到有效数值，请检查数据格式")

    # 验证坐标范围
    lons = df[lon_col].tolist()
    lats = df[lat_col].tolist()

    invalid_lon = [v for v in lons if not (-180 <= v <= 180)]
    invalid_lat = [v for v in lats if not (-90 <= v <= 90)]
    if invalid_lon or invalid_lat:
        raise TabularParseError(
            f"坐标值超出范围（经度应在 -180~180，纬度应在 -90~90）\n"
            f"异常经度示例：{invalid_lon[:3]}，异常纬度示例：{invalid_lat[:3]}"
        )

    # 构建几何体：若只有 1 行则缓冲成圆形，多行则构成多边形
    points = [Point(lon, lat) for lon, lat in zip(lons, lats)]

    if len(points) == 1:
        geometry = points[0].buffer(point_buffer_deg)
    elif len(points) >= 3:
        # 尝试将点集构成多边形（假设点已按顺序排列构成边界）
        try:
            poly = Polygon([(lon, lat) for lon, lat in zip(lons, lats)])
            if poly.is_valid and poly.area > 0:
                geometry = poly
            else:
                # 退化为凸包
                mp = unary_union(points)
                geometry = mp.convex_hull.buffer(0.01) if mp.convex_hull.geom_type == "LineString" else mp.convex_hull
        except Exception:
            geometry = unary_union([p.buffer(point_buffer_deg / 4) for p in points])
    else:
        # 2 个点：缓冲后合并
        geometry = unary_union([p.buffer(point_buffer_deg / 2) for p in points])

    bbox = geometry.bounds  # (minx, miny, maxx, maxy)
    file_name = path.stem

    # 区块名称：优先取名称列第一行，否则用文件名
    area_name = file_name
    if name_col and not df[name_col].dropna().empty:
        area_name = str(df[name_col].dropna().iloc[0]).strip() or file_name
    # 规范化研究区名称中独立出现的 aoi → AOI
    area_name = re.sub(r"\baoi\b", "AOI", area_name, flags=re.IGNORECASE)

    # 描述
    description = ""
    if desc_col and not df[desc_col].dropna().empty:
        description = str(df[desc_col].dropna().iloc[0]).strip()

    return geometry, bbox, file_name, area_name, description
