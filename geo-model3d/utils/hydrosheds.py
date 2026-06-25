"""hydrosheds — HydroRIVERS 公开水系数据处理工具。

从 HydroSHEDS HydroRIVERS 矢量河网数据集中加载河流线段，
按研究区 bbox 裁剪、按河流等级过滤、生成缓冲区后栅格化为水体掩码。

数据源: https://www.hydrosheds.org/products/hydrorivers
许可: CC-BY 4.0 (免费商用)
引用: Lehner, B., Grill G. (2013). Global river hydrography and network routing.
       Hydrological Processes, 27(15): 2171–2186.

主要接口:
- load_rivers_for_bbox(): 加载研究区内河流线段
- rivers_to_water_mask(): 将河流矢量栅格化为布尔掩码
- build_water_mask_from_hydrosheds(): 一站式接口（加载+过滤+栅格化）
- ensure_data_available(): 检查/下载数据
"""

from __future__ import annotations

import os
import zipfile
from typing import Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import box, Polygon
from shapely.ops import unary_union

from utils.logger import get_logger

logger = get_logger(__name__)

# HydroRIVERS 亚洲数据下载 URL
_HYDRORIVERS_ASIA_URL = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip"
_HYDRORIVERS_ASIA_SIZE_MB = 91

# 河流等级 → 缓冲区宽度（米）的默认映射
# HydroRIVERS 的 order 字段：1=源头小溪, 7+=大河
DEFAULT_ORDER_BUFFER = {
    1: 0,       # 太小，不排除
    2: 0,       # 太小，不排除
    3: 60,      # 中型河流，±30m 缓冲
    4: 60,      # 中型河流
    5: 120,     # 主要河流，±60m 缓冲
    6: 120,     # 主要河流
    7: 200,     # 大河，±100m 缓冲
    8: 200,     # 大河
    9: 200,     # 大河
    10: 300,    # 特大河
}


def ensure_data_available(data_dir: str) -> Optional[str]:
    """检查 HydroRIVERS 数据是否已下载，返回 Shapefile 路径或 None。

    如果数据不存在，打印下载提示并返回 None。
    """
    shp_path = _find_shapefile(data_dir)
    if shp_path:
        return shp_path

    zip_path = os.path.join(data_dir, "HydroRIVERS_v10_as_shp.zip")
    if os.path.isfile(zip_path):
        # 有 zip 但未解压，自动解压
        logger.info(f"解压 HydroRIVERS 数据: {zip_path}")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(data_dir)
            shp_path = _find_shapefile(data_dir)
            if shp_path:
                return shp_path
        except Exception as e:
            logger.error(f"解压失败: {e}")

    # 数据不可用，提示下载
    logger.warning(
        f"HydroRIVERS 数据未找到。请运行以下命令下载亚洲数据({_HYDRORIVERS_ASIA_SIZE_MB}MB):\n"
        f"  mkdir -p {data_dir}\n"
        f"  cd {data_dir}\n"
        f"  curl -L -o HydroRIVERS_v10_as_shp.zip '{_HYDRORIVERS_ASIA_URL}'\n"
        f"  unzip HydroRIVERS_v10_as_shp.zip"
    )
    return None


def _find_shapefile(data_dir: str) -> Optional[str]:
    """在数据目录中查找 HydroRIVERS Shapefile。"""
    import glob
    # 递归查找 .shp 文件
    patterns = [
        os.path.join(data_dir, "**", "HydroRIVERS*.shp"),
        os.path.join(data_dir, "**", "*.shp"),
    ]
    for pat in patterns:
        matches = glob.glob(pat, recursive=True)
        if matches:
            return matches[0]
    return None


def load_rivers_for_bbox(bbox: List[float], data_dir: str,
                          min_order: int = 3,
                          max_order: int = 10) -> Optional['geopandas.GeoDataFrame']:
    """加载研究区 bbox 内的河流线段。

    Args:
        bbox: [min_lon, min_lat, max_lon, max_lat] (WGS84)
        data_dir: HydroRIVERS 数据目录
        min_order: 最小河流等级（默认 3，排除小溪）
        max_order: 最大河流等级

    Returns:
        GeoDataFrame 或 None（数据不可用时）
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.warning("geopandas 未安装，无法加载 HydroRIVERS 数据")
        return None

    shp_path = ensure_data_available(data_dir)
    if not shp_path:
        return None

    logger.info(f"加载 HydroRIVERS: {shp_path}")

    # 构建研究区 bbox 的 polygon 用于裁剪
    bbox_polygon = box(bbox[0], bbox[1], bbox[2], bbox[3])

    try:
        # 使用 bbox mask 读取，避免全量加载 850 万条记录
        gdf = gpd.read_file(shp_path, mask=bbox_polygon)
    except Exception as e:
        logger.warning(f"读取 HydroRIVERS 失败: {e}")
        # 回退：尝试不带 mask 读取（慢但兼容）
        try:
            gdf = gpd.read_file(shp_path, bbox=bbox)
        except Exception as e2:
            logger.error(f"读取 HydroRIVERS 完全失败: {e2}")
            return None

    if gdf.empty:
        logger.info(f"HydroRIVERS: bbox {bbox} 内无河流数据")
        return None

    # 按河流等级过滤
    order_col = _find_order_column(gdf)
    if order_col:
        gdf = gdf[gdf[order_col].between(min_order, max_order)]

    logger.info(f"HydroRIVERS: {len(gdf)} 条河段 (order >= {min_order})")
    return gdf


def _find_order_column(gdf) -> Optional[str]:
    """自动查找河流等级列名。HydroRIVERS 使用 'Ord' 或 'order' 或 'ORD_FLOW'。"""
    candidates = ["Ord", "ORD_FLOW", "order", "ORDER", "river_order", "strahler"]
    for col in candidates:
        if col in gdf.columns:
            return col
    # 尝试模糊匹配
    for col in gdf.columns:
        if "ord" in col.lower():
            return col
    return None


def rivers_to_water_mask(rivers_gdf, grid,
                          buffer_m: Optional[float] = None,
                          order_buffer: Optional[Dict[int, float]] = None) -> Optional[np.ndarray]:
    """将河流矢量 GeoDataFrame 栅格化为水体布尔掩码。

    策略：按河流等级给予不同缓冲区宽度 → 合并 → 按网格中心点判断是否在水体内。

    Args:
        rivers_gdf: 河流 GeoDataFrame（WGS84）
        grid: VoxelGrid 实例
        buffer_m: 统一缓冲区宽度（米），覆盖 order_buffer
        order_buffer: 河流等级 → 缓冲区宽度映射，默认 DEFAULT_ORDER_BUFFER

    Returns:
        (ny, nx) 布尔数组，True=水体。无河流返回 None。
    """
    if rivers_gdf is None or rivers_gdf.empty:
        return None

    ny, nx = grid.shape[1], grid.shape[2]
    order_col = _find_order_column(rivers_gdf)

    # 获取网格中心点坐标（WGS84）
    center_lons = np.array([grid.colrow_to_lonlat(c, 0)[0] for c in range(nx)])
    center_lats = np.array([grid.colrow_to_lonlat(0, r)[1] for r in range(ny)])
    lon_grid, lat_grid = np.meshgrid(center_lons, center_lats)

    # 构建缓冲区多边形集合
    if buffer_m is not None:
        # 统一缓冲区
        buf_m = float(buffer_m)
        all_polys = []
        for _, row in rivers_gdf.iterrows():
            geom = row.geometry
            if geom is not None and not geom.is_empty:
                # 将米转换为近似的度数（纬度相关）
                buf_deg = _meters_to_degrees(buf_m, row.geometry)
                buffered = geom.buffer(buf_deg)
                if not buffered.is_empty:
                    all_polys.append(buffered)
    elif order_col and order_buffer:
        # 按等级分缓冲区
        ob = order_buffer or DEFAULT_ORDER_BUFFER
        all_polys = []
        for _, row in rivers_gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            order = int(row[order_col]) if order_col else 3
            buf_m = ob.get(order, 0)
            if buf_m <= 0:
                continue
            buf_deg = _meters_to_degrees(buf_m, geom)
            buffered = geom.buffer(buf_deg)
            if not buffered.is_empty:
                all_polys.append(buffered)
    else:
        # 默认：按等级分缓冲区
        all_polys = []
        for _, row in rivers_gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            order = int(row[order_col]) if order_col else 3
            buf_m = DEFAULT_ORDER_BUFFER.get(order, 0)
            if buf_m <= 0:
                continue
            buf_deg = _meters_to_degrees(buf_m, geom)
            buffered = geom.buffer(buf_deg)
            if not buffered.is_empty:
                all_polys.append(buffered)

    if not all_polys:
        logger.info("无有效河流缓冲区")
        return None

    # 合并所有缓冲区
    logger.info(f"合并 {len(all_polys)} 个河流缓冲区...")
    try:
        merged = unary_union(all_polys)
    except Exception as e:
        logger.warning(f"合并缓冲区失败，尝试逐个判断: {e}")
        merged = None

    # 栅格化：判断每个网格中心是否在水体内
    water = np.zeros((ny, nx), dtype=bool)

    if merged is not None:
        # 向量化判断
        from shapely.geometry import Point
        points = [Point(lon, lat) for lon, lat in zip(lon_grid.ravel(), lat_grid.ravel())]
        # 批量 contains（可能较慢，但准确）
        try:
            # 使用 prepared geometry 加速
            from shapely.prepared import prep
            prepared = prep(merged)
            hits = [prepared.contains(p) for p in points]
            water = np.array(hits).reshape(ny, nx)
        except Exception:
            # 降级：逐行判断
            for r in range(ny):
                for c in range(nx):
                    if merged.contains(Point(center_lons[c], center_lats[r])):
                        water[r, c] = True
    else:
        # 逐个多边形判断
        for poly in all_polys:
            from shapely.prepared import prep
            prepared = prep(poly)
            for r in range(ny):
                for c in range(nx):
                    if prepared.contains(Point(center_lons[c], center_lats[r])):
                        water[r, c] = True

    n_water = int(water.sum())
    logger.info(f"HydroRIVERS 水体掩码: {n_water}/{water.size} 像元 ({n_water/water.size*100:.1f}%)")
    return water


def _meters_to_degrees(meters: float, geom) -> float:
    """将米近似转换为 WGS84 度数（根据几何体纬度调整）。"""
    # 取几何体中心纬度
    try:
        centroid = geom.centroid
        lat = centroid.y
    except Exception:
        lat = 45.0  # 默认中纬度

    # 经度方向: 1度 ≈ 111320 * cos(lat) 米
    # 纬度方向: 1度 ≈ 110540 米
    # 取平均作为缓冲区
    avg_deg_per_m = 1.0 / ((111320 * abs(np.cos(np.radians(lat))) + 110540) / 2)
    return meters * avg_deg_per_m


def build_water_mask_from_hydrosheds(grid, bbox: List[float],
                                      data_dir: str,
                                      min_order: int = 3,
                                      buffer_m: Optional[float] = None) -> Optional[np.ndarray]:
    """一站式接口：从 HydroRIVERS 构建水体掩码。

    Args:
        grid: VoxelGrid 实例
        bbox: [min_lon, min_lat, max_lon, max_lat]
        data_dir: HydroRIVERS 数据目录
        min_order: 最小河流等级
        buffer_m: 统一缓冲区宽度（米），None 则按等级自动设置

    Returns:
        (ny, nx) 布尔数组，True=水体。无数据返回 None。
    """
    # 扩大 bbox 以包含缓冲区边缘的河流
    buffer_deg = 0.02  # ~2km 缓冲
    expanded_bbox = [
        bbox[0] - buffer_deg,
        bbox[1] - buffer_deg,
        bbox[2] + buffer_deg,
        bbox[3] + buffer_deg,
    ]

    rivers = load_rivers_for_bbox(expanded_bbox, data_dir, min_order=min_order)
    if rivers is None or rivers.empty:
        return None

    mask = rivers_to_water_mask(rivers, grid, buffer_m=buffer_m)

    # 安全检查：水体比例不应超过 40%
    if mask is not None:
        ratio = mask.sum() / mask.size
        if ratio > 0.4:
            logger.warning(f"HydroRIVERS 水体比例 {ratio*100:.0f}% 过高，可能数据异常，不排除")
            return None

    return mask
