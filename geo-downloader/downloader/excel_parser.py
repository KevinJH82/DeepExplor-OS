"""
Excel Coordinate Parser
解析包含经纬度坐标的 Excel (.xlsx/.xls) 文件，
自动识别经度/纬度列，每个点独立 buffer 生成裁剪区域。
返回与 parse_kml 一致的 (geometry, bbox, name) 格式。
"""

import re
from pathlib import Path
from typing import Tuple

from shapely.geometry import MultiPolygon, Point

# 默认缓冲距离（约 0.1 度 ≈ 11km），与 kml_parser 保持一致
DEFAULT_POINT_BUFFER_DEG = 0.1

# 自动识别列名的正则（不区分大小写）——精确匹配整个列名
_LON_PATTERNS = [
    re.compile(r"^lon(gitude)?$", re.I),
    re.compile(r"^经度$"),
    re.compile(r"^lng$", re.I),
    re.compile(r"^x$", re.I),
]
_LAT_PATTERNS = [
    re.compile(r"^lat(itude)?$", re.I),
    re.compile(r"^纬度$"),
    re.compile(r"^y$", re.I),
]

# 包含匹配：列名中含有以下子串时也接受（降级策略）
_LON_CONTAINS = re.compile(r"lon|lng|经度|longitude", re.I)
_LAT_CONTAINS = re.compile(r"lat|纬度|latitude", re.I)


def _clean_col(col) -> str:
    """清洗列名：去除首尾空白、BOM、全角空格、不可见控制字符。"""
    s = str(col)
    # 去除 BOM 和常见不可见字符
    s = s.replace("\ufeff", "").replace("\u3000", " ").replace("\xa0", " ")
    # 去除所有控制字符（含换行、tab）
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    return s.strip()


class ExcelParseError(Exception):
    pass


def _find_column(columns, patterns, contains_pat, label: str) -> str:
    """
    在 DataFrame 列名中查找匹配列，返回列名。
    策略1：精确匹配（整个列名符合 patterns）
    策略2：包含匹配（列名中含有 contains_pat 子串）
    """
    cleaned = [(col, _clean_col(col)) for col in columns]

    # 策略1：精确匹配
    for col, col_stripped in cleaned:
        for pat in patterns:
            if pat.match(col_stripped):
                return col

    # 策略2：包含匹配（降级）
    for col, col_stripped in cleaned:
        if contains_pat.search(col_stripped):
            return col

    raise ExcelParseError(
        f"未找到{label}列。请确保 Excel 中有名为 "
        f"lon/longitude/经度/lng/x 或 lat/latitude/纬度/y 的列"
    )


def parse_excel(
    excel_path: str,
    point_buffer_deg: float = DEFAULT_POINT_BUFFER_DEG,
) -> Tuple:
    """
    解析 Excel 文件中的经纬度坐标。

    Parameters
    ----------
    excel_path : str
        Excel 文件路径 (.xlsx / .xls)
    point_buffer_deg : float
        每个坐标点的缓冲距离（度）

    Returns
    -------
    geometry : shapely.geometry  合并后的几何体（MultiPolygon）
    bbox     : (min_lon, min_lat, max_lon, max_lat)
    name     : str  文件名（不含扩展名）
    """
    try:
        import pandas as pd
    except ImportError:
        raise ExcelParseError("需要 pandas 库来解析 Excel 文件，请安装: pip install pandas")

    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise ExcelParseError(f"文件不存在: {excel_path}")

    suffix = excel_path.suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        raise ExcelParseError(f"不支持的文件格式: {suffix}（仅支持 .xlsx / .xls）")

    name = excel_path.stem

    # 读取 Excel
    try:
        if suffix == ".xlsx":
            df = pd.read_excel(excel_path, engine="openpyxl")
        else:
            df = pd.read_excel(excel_path, engine="xlrd")
    except ImportError as e:
        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        raise ExcelParseError(f"需要 {engine} 库来读取 {suffix} 文件: pip install {engine}")
    except Exception as e:
        raise ExcelParseError(f"读取 Excel 失败: {e}")

    if df.empty:
        raise ExcelParseError(f"Excel 文件为空: {excel_path}")

    # 自动识别经度/纬度列（精确匹配 → 包含匹配降级）
    lon_col = _find_column(df.columns, _LON_PATTERNS, _LON_CONTAINS, "经度")
    lat_col = _find_column(df.columns, _LAT_PATTERNS, _LAT_CONTAINS, "纬度")

    # 清洗：去除空值行，转为数值
    df = df.dropna(subset=[lon_col, lat_col])
    try:
        lons = df[lon_col].astype(float)
        lats = df[lat_col].astype(float)
    except (ValueError, TypeError) as e:
        raise ExcelParseError(f"经纬度列包含非数值数据: {e}")

    if len(lons) == 0:
        raise ExcelParseError(f"Excel 中没有有效的坐标数据: {excel_path}")

    # 校验坐标范围
    if lons.min() < -180 or lons.max() > 180:
        raise ExcelParseError(f"经度超出范围 [-180, 180]: min={lons.min()}, max={lons.max()}")
    if lats.min() < -90 or lats.max() > 90:
        raise ExcelParseError(f"纬度超出范围 [-90, 90]: min={lats.min()}, max={lats.max()}")

    # 每个点独立 buffer 生成多边形
    polygons = []
    for lon, lat in zip(lons, lats):
        pt = Point(lon, lat)
        polygons.append(pt.buffer(point_buffer_deg))

    if len(polygons) == 1:
        merged = polygons[0]
    else:
        merged = MultiPolygon(polygons)

    bbox = merged.bounds  # (minx, miny, maxx, maxy)

    return merged, bbox, name
