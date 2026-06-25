"""
Postprocess: 生成区域地形概览图 (Terrain Overview)
在交付目录根目录生成 terrain_overview.png，内容为：
  - DEM 山体阴影（Hillshade）底图
  - 高程色彩晕渲叠加（地貌直觉：绿低→黄→棕→白高）
  - KML 区域边界轮廓（红色）

数据来源（优先顺序）：
  1. 夏季目录 DEM.tif
  2. 冬季目录 DEM.tif
  3. downloads/{area}/dem/ 或 downloads/{area}/srtm/ 原始文件
"""

from pathlib import Path
from typing import Optional, Tuple
import numpy as np


# ── 高程色带（elevation → RGB），类似 QGIS 地形配色 ─────────────────
# 节点：[高程比例0-1, R, G, B]
_ELEVATION_COLORMAP = [
    (0.00,  68, 117,  55),   # 低洼 深绿
    (0.20, 134, 181,  73),   # 丘陵 草绿
    (0.40, 215, 194, 119),   # 平原 黄褐
    (0.60, 175, 131,  75),   # 山地 棕
    (0.80, 128,  90,  52),   # 高山 深棕
    (0.90, 210, 205, 195),   # 雪线 浅灰
    (1.00, 255, 255, 255),   # 峰顶 白
]


def _interp_color(ratio: float) -> Tuple[int, int, int]:
    """线性插值高程色带"""
    for i in range(len(_ELEVATION_COLORMAP) - 1):
        r0, g0, b0 = _ELEVATION_COLORMAP[i][1:]
        r1, g1, b1 = _ELEVATION_COLORMAP[i + 1][1:]
        t0, t1 = _ELEVATION_COLORMAP[i][0], _ELEVATION_COLORMAP[i + 1][0]
        if t0 <= ratio <= t1:
            t = (ratio - t0) / (t1 - t0) if t1 != t0 else 0
            return (
                int(r0 + t * (r1 - r0)),
                int(g0 + t * (g1 - g0)),
                int(b0 + t * (b1 - b0)),
            )
    return (255, 255, 255)


def _colorize_dem(dem: np.ndarray, nodata_mask: np.ndarray) -> np.ndarray:
    """将高程数组映射为 RGB 色彩图（H×W×3 uint8）"""
    h, w = dem.shape
    valid = dem[~nodata_mask]
    if len(valid) == 0:
        return np.zeros((h, w, 3), dtype=np.uint8)

    lo, hi = np.percentile(valid, 2), np.percentile(valid, 98)
    if hi == lo:
        hi = lo + 1

    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    ratio_arr = np.clip((dem - lo) / (hi - lo), 0, 1)

    # 向量化插值
    for i in range(len(_ELEVATION_COLORMAP) - 1):
        t0, r0, g0, b0 = _ELEVATION_COLORMAP[i]
        t1, r1, g1, b1 = _ELEVATION_COLORMAP[i + 1]
        seg = (ratio_arr >= t0) & (ratio_arr <= t1)
        t = np.where(t1 != t0, (ratio_arr - t0) / (t1 - t0), 0)
        rgb[:, :, 0] = np.where(seg, np.clip(r0 + t * (r1 - r0), 0, 255), rgb[:, :, 0])
        rgb[:, :, 1] = np.where(seg, np.clip(g0 + t * (g1 - g0), 0, 255), rgb[:, :, 1])
        rgb[:, :, 2] = np.where(seg, np.clip(b0 + t * (b1 - b0), 0, 255), rgb[:, :, 2])

    rgb[nodata_mask] = [200, 200, 200]  # nodata → 浅灰
    return rgb


def _hillshade(dem: np.ndarray, cell_size: float = 30.0,
               azimuth: float = 315.0, altitude: float = 45.0) -> np.ndarray:
    """
    计算山体阴影（0-255 float）。
    cell_size: 像素大小（米），用于计算坡度
    azimuth:   光源方位角（度，北=0，顺时针）
    altitude:  光源高度角（度）
    """
    az_rad = np.radians(360.0 - azimuth + 90.0)
    alt_rad = np.radians(altitude)

    # Sobel 核计算坡度分量
    from scipy.ndimage import uniform_filter
    dx = (np.roll(dem, -1, axis=1) - np.roll(dem, 1, axis=1)) / (2 * cell_size)
    dy = (np.roll(dem, -1, axis=0) - np.roll(dem, 1, axis=0)) / (2 * cell_size)

    slope = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(-dy, dx)

    hs = (np.cos(alt_rad) * np.cos(slope) +
          np.sin(alt_rad) * np.sin(slope) * np.cos(az_rad - aspect))
    hs = np.clip(hs, 0, 1)
    return (hs * 255).astype(np.float32)


def _blend_hillshade(color_rgb: np.ndarray, hs: np.ndarray,
                     alpha: float = 0.55) -> np.ndarray:
    """将山体阴影与色彩图叠加（Multiply 模式）"""
    hs_norm = hs[:, :, np.newaxis] / 255.0
    blended = color_rgb.astype(float) * (alpha + (1 - alpha) * hs_norm)
    return np.clip(blended, 0, 255).astype(np.uint8)


def _find_dem(delivery_dir: Path, raw_area_dir: Optional[Path] = None) -> Optional[Path]:
    """在交付目录或原始下载目录中找 DEM 文件"""
    # 优先从交付目录找已整理的 DEM.tif
    for subdir in delivery_dir.iterdir():
        if not subdir.is_dir():
            continue
        dem = subdir / "DEM.tif"
        if dem.exists():
            return dem

    # 回退到原始下载目录
    if raw_area_dir:
        for sensor in ("dem", "srtm"):
            d = raw_area_dir / sensor
            if d.exists():
                candidates = list(d.glob("*_clipped.tif")) + list(d.glob("*.tif"))
                if candidates:
                    return candidates[0]
    return None


def _reproject_geometry_to_crs(geometry, src_crs):
    """将 WGS84 几何体重投影到目标 CRS"""
    try:
        import pyproj
        from shapely.ops import transform as shp_transform
        wgs84 = pyproj.CRS("EPSG:4326")
        if src_crs.to_epsg() == 4326:
            return geometry
        transformer = pyproj.Transformer.from_crs(wgs84, src_crs, always_xy=True)
        return shp_transform(transformer.transform, geometry)
    except Exception:
        return geometry


def _draw_boundary(rgb: np.ndarray, geometry, transform, crs,
                   color=(220, 40, 40), line_width: int = 3) -> np.ndarray:
    """在 RGB 图上绘制 KML 边界"""
    try:
        from rasterio.features import rasterize
        from shapely.geometry import mapping

        geom_proj = _reproject_geometry_to_crs(geometry, crs)
        h, w = rgb.shape[:2]

        if geom_proj.geom_type in ("MultiPolygon", "GeometryCollection"):
            boundaries = [g.boundary for g in geom_proj.geoms if hasattr(g, "boundary")]
        elif hasattr(geom_proj, "boundary"):
            boundaries = [geom_proj.boundary]
        else:
            return rgb

        mask_arr = rasterize(
            [(mapping(b), 1) for b in boundaries],
            out_shape=(h, w),
            transform=transform,
            fill=0,
            dtype="uint8",
        )

        if line_width > 1:
            from scipy.ndimage import binary_dilation
            struct = np.ones((line_width, line_width), dtype=bool)
            mask_arr = binary_dilation(mask_arr.astype(bool), structure=struct).astype(np.uint8)

        result = rgb.copy()
        for c_idx, c_val in enumerate(color):
            result[:, :, c_idx][mask_arr == 1] = c_val
        return result
    except Exception:
        return rgb


def generate_terrain_overview(
    delivery_dir: Path,
    geometry=None,
    raw_area_dir: Optional[Path] = None,
    output_name: str = "terrain_overview.png",
    max_px: int = 1500,
) -> Optional[Path]:
    """
    生成地形晕渲概览图。

    Parameters
    ----------
    delivery_dir  : 交付目录路径
    geometry      : Shapely 几何体（WGS84），用于绘制边界
    raw_area_dir  : 原始下载目录（备用DEM来源）
    output_name   : 输出文件名
    max_px        : 输出图最大边长（像素）

    Returns
    -------
    生成的 PNG 路径，或 None
    """
    out_path = delivery_dir / output_name
    if out_path.exists():
        return out_path

    dem_path = _find_dem(delivery_dir, raw_area_dir)
    if dem_path is None:
        print("  [地形图] 未找到 DEM 文件，跳过生成")
        return None

    print(f"  [地形图] 使用 {dem_path.name} 生成地形晕渲图...")

    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds

        with rasterio.open(dem_path) as src:
            scale = min(1.0, max_px / max(src.width, src.height))
            out_w = max(1, int(src.width * scale))
            out_h = max(1, int(src.height * scale))

            dem = src.read(
                1,
                out_shape=(out_h, out_w),
                resampling=Resampling.bilinear,
            ).astype(float)

            nodata = src.nodata
            nodata_mask = (dem == nodata) if nodata is not None else np.zeros_like(dem, dtype=bool)
            dem[nodata_mask] = np.nan

            # 估算像素大小（米）：用中心纬度粗估
            bounds = src.bounds
            crs = src.crs
            transform = from_bounds(bounds.left, bounds.bottom, bounds.right, bounds.top, out_w, out_h)

            # WGS84 经纬度转米
            center_lat = (bounds.bottom + bounds.top) / 2
            deg_per_px_lon = (bounds.right - bounds.left) / out_w
            deg_per_px_lat = (bounds.top - bounds.bottom) / out_h
            import math
            m_per_px = (deg_per_px_lon * 111320 * math.cos(math.radians(center_lat)) +
                        deg_per_px_lat * 111320) / 2

        # 填充 nodata 为周围均值，避免边缘干扰坡度计算
        dem_filled = dem.copy()
        nan_mask = np.isnan(dem_filled)
        if nan_mask.any():
            from scipy.ndimage import generic_filter
            def nanmean(v):
                valid = v[~np.isnan(v)]
                return np.nanmean(valid) if len(valid) else 0
            dem_filled[nan_mask] = generic_filter(dem_filled, nanmean, size=5)[nan_mask]

        # 色彩晕渲
        color_rgb = _colorize_dem(dem_filled, nodata_mask)

        # 山体阴影
        hs = _hillshade(dem_filled, cell_size=max(m_per_px, 1.0))

        # 叠加
        terrain_rgb = _blend_hillshade(color_rgb, hs, alpha=0.5)

        # 叠加 KML 边界
        if geometry is not None:
            terrain_rgb = _draw_boundary(terrain_rgb, geometry, transform, crs)

        # 添加图例文字
        from PIL import Image, ImageDraw
        img = Image.fromarray(terrain_rgb, "RGB")
        try:
            draw = ImageDraw.Draw(img)
            src_label = dem_path.name.upper()
            dem_src = "Copernicus DEM GLO-30" if "dem" in str(dem_path.parent).lower() else "SRTM 30m"
            draw.text((10, 10), f"地形晕渲图  数据: {dem_src}", fill=(255, 255, 100))
        except Exception:
            pass

        img.save(out_path, "PNG", optimize=True)
        print(f"  [地形图] 已生成: {out_path.name}  ({out_w}×{out_h}px)")
        return out_path

    except Exception as e:
        print(f"  [警告] 地形图生成失败: {e}")
        return None
