"""
geo_bridge_common — 外部系统(geo-analyser 蚀变 / geo-stru 构造)结果接入的共享底层。

只放"与具体业务无关"的几何/坐标工具:
- build_dst_transform: 由 geo-exploration 的 lonGrid/latGrid 推导目标 GeoTIFF 仿射变换
- reproject_to_grid:  把任意 CRS 的外部栅格重投影/重采样到 geo-exploration 网格
- normalize_deposit_key / roi_overlap_frac: run 匹配用的字符串与空间工具

设计要点
--------
1. **朝向**:geo-exploration 的图像数组(ast/dem/inROI/Au_deep)是 row0=北
   (因 read_dem_and_roi 里 `inROI = np.flipud(...)`),而 lonGrid/latGrid 是 row0=南。
   目标 transform 必须北上(lat_max 在 row0),且像元尺寸沿用现有 DEM 重采样的
   `/W`、`/H` 约定(geo_utils.py 中 DEM resample),以保证与系统其它图层逐像素对齐。
2. **零硬崩溃**:rasterio/shapely 等重依赖一律在函数内 import,模块本身永远可导入;
   调用方负责 try/except 降级。
"""

import numpy as np


def build_dst_transform(lonGrid, latGrid, shape):
    """由 lonGrid/latGrid + (H,W) 构造北上目标仿射变换。

    返回 (transform, dst_crs)。约定与 geo_utils.read_dem_and_roi 的 DEM 重采样一致:
        px = (lon_max - lon_min) / W ,  py = (lat_max - lat_min) / H
        Affine(px, 0, lon_min, 0, -py, lat_max)   # row0 = lat_max = 北
    """
    from rasterio.transform import Affine
    from rasterio.crs import CRS

    lon_min = float(np.nanmin(lonGrid)); lon_max = float(np.nanmax(lonGrid))
    lat_min = float(np.nanmin(latGrid)); lat_max = float(np.nanmax(latGrid))
    H, W = int(shape[0]), int(shape[1])
    px = (lon_max - lon_min) / max(W, 1)
    py = (lat_max - lat_min) / max(H, 1)
    transform = Affine(px, 0.0, lon_min, 0.0, -py, lat_max)
    return transform, CRS.from_epsg(4326)


def reproject_to_grid(tif_path, lonGrid, latGrid, shape, inROI=None,
                      resampling='bilinear', band=1):
    """把外部 GeoTIFF 重投影/重采样到 geo-exploration 网格 (H,W),ROI 外置 NaN。

    源 CRS/transform 一律从 tif 实读(不同区块 CRS 不同,不可硬编码)。
    resampling: 'bilinear'(连续量:index/score/distance/density) 或 'nearest'(mask)。
    源 crs 为 None → 抛 ValueError,由调用方降级。
    """
    import rasterio
    from rasterio.warp import reproject, Resampling

    transform, dst_crs = build_dst_transform(lonGrid, latGrid, shape)
    H, W = int(shape[0]), int(shape[1])
    rs = Resampling.bilinear if resampling == 'bilinear' else Resampling.nearest

    with rasterio.open(tif_path) as src:
        if src.crs is None:
            raise ValueError(f"源栅格无 CRS,无法重投影: {tif_path}")
        dst = np.full((H, W), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, band),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=dst_crs,
            src_nodata=src.nodata,
            dst_nodata=np.nan,
            resampling=rs,
        )
    if inROI is not None:
        dst[~inROI] = np.nan
    return dst


def normalize_deposit_key(s):
    """归一化 deposit_type 键:全角括号→半角、去空白。

    已核实真实键存在全/半角混用:`常规油藏(微渗漏蚀变模式)` 半角、
    `BIF型铁矿（条带状铁建造）` 全角。匹配前两侧都过此函数。
    """
    if s is None:
        return ''
    return (str(s).strip()
            .replace('（', '(').replace('）', ')')
            .replace('〔', '(').replace('〕', ')')
            .replace(' ', '').replace('　', ''))


def roi_overlap_frac(exp_poly_lonlat, geom_geojson):
    """ROI 空间重叠占比 = (勘探 ROI ∩ 外部几何).area / 勘探 ROI.area。

    exp_poly_lonlat: (N,2) 经纬度多边形顶点;geom_geojson: 裸 geometry dict。
    shapely 缺失或几何非法 → 返回 0.0(由调用方据阈值降级)。
    """
    try:
        from shapely.geometry import shape, Polygon
    except Exception:
        return 0.0
    try:
        exp = Polygon(np.asarray(exp_poly_lonlat)[:, :2])
        alt = shape(geom_geojson)
        if not exp.is_valid:
            exp = exp.buffer(0)
        if not alt.is_valid:
            alt = alt.buffer(0)
        a = exp.area
        if a <= 0:
            return 0.0
        return float(exp.intersection(alt).area / a)
    except Exception:
        return 0.0
