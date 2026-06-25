"""地球物理资料获取器 — EMAG2 磁法 / WGM2012 重力 / ICGEM"""

import os
import threading
from pathlib import Path
from typing import Dict, Optional, Any
from urllib.parse import urlencode

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from shapely.geometry import box
import xarray as xr

from .logger import get_logger
from .http_client import download_file
from .roi_parser import get_bbox_tuple, shape_from_geojson
from config import (
    CACHE_DIR,
    EMAG2_GEOTIFF_URL,
    EMAG2_SEALEVEL_URL,
    WGM2012_BOUGUER_URL,
    ICGEM_CALC_URL,
    OPENTOPOGRAPHY_URL,
    OPENTOPOGRAPHY_API_KEY,
    EE_URL,
)

logger = get_logger("geophy")

# 全局文件锁，防止并发下载同一个全球文件
_download_locks = {
    "emag2_upcont": threading.Lock(),
    "emag2_sealevel": threading.Lock(),
    "wgm2012": threading.Lock(),
}


def _ensure_cache_dir() -> Path:
    d = Path(CACHE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_emag2(
    roi: Dict[str, Any],
    output_dir: Path,
    variant: str = "upcont",
) -> Optional[Dict[str, Any]]:
    """
    下载 EMAG2 v3 全球磁异常数据，并按 ROI bbox 裁剪

    Args:
        roi: parse_roi + expand_bbox 的输出
        output_dir: 输出目录
        variant: "upcont" (上延 4km) 或 "sealevel" (海平面)

    Returns:
        {"file": 裁剪后文件路径, "map": 分布图PNG路径, "url": 原始URL,
         "source": "NOAA EMAG2 v3", "resolution": "..."}
        如果下载失败返回 None
    """
    url = EMAG2_GEOTIFF_URL if variant == "upcont" else EMAG2_SEALEVEL_URL
    bbox = get_bbox_tuple(roi, use_expanded=True)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 全局缓存：所有任务共享同一份全球文件
    cache_dir = _ensure_cache_dir()
    cache_file = cache_dir / f"emag2_{variant}_global.tif"
    clipped_file = output_dir / f"emag2_{variant}_clipped.tif"
    lock = _download_locks[f"emag2_{variant}"]

    try:
        with lock:
            if not cache_file.exists():
                # 先检查常见手动下载位置
                manual_paths = [
                    Path.home() / "Downloads" / cache_file.name,
                    Path.home() / "Downloads" / "EMAG2_V3_20170530_UpCont.tif",
                    Path.home() / "Downloads" / "EMAG2_V3_20170530_Sealevel.tif",
                    cache_dir / "EMAG2_V3_20170530_UpCont.tif",
                    cache_dir / "EMAG2_V3_20170530_Sealevel.tif",
                ]
                found_manual = None
                for mp in manual_paths:
                    if mp.exists() and mp.stat().st_size > 1_000_000:
                        found_manual = mp
                        break

                if found_manual:
                    import shutil
                    logger.info("发现手动下载的 EMAG2 文件: %s → %s", found_manual, cache_file)
                    shutil.copy2(found_manual, cache_file)
                else:
                    # 快速检测 NOAA 是否可达（10秒），不可达立即跳过
                    logger.info("检测 NOAA 服务器连通性...")
                    import requests as _req
                    try:
                        _req.head(url, timeout=10, allow_redirects=True)
                    except (_req.ConnectionError, _req.Timeout, OSError) as e:
                        logger.warning("NOAA 服务器不可达 (%s)，跳过 EMAG2 下载", type(e).__name__)
                        logger.info("如需磁异常数据，请手动下载 %s 放入 %s", url, cache_dir)
                        return None

                    logger.info("下载 EMAG2 v3 (%s) 全球磁异常数据 (~175 MB)...", variant)
                    download_file(url, cache_file, timeout=600)

        # 裁剪
        logger.info("裁剪磁异常数据到 ROI 范围...")
        _clip_raster(cache_file, clipped_file, bbox)

        # 生成磁异常分布图
        map_file = None
        map_path = output_dir / f"emag2_{variant}_map.png"
        try:
            map_file = _generate_magnetic_map(clipped_file, map_path, roi)
            if map_file:
                logger.info("磁异常分布图已生成: %s", map_path)
        except Exception as e:
            logger.warning("磁异常分布图生成失败 (非致命): %s", e)

        logger.info("磁异常数据已裁剪: %s", clipped_file)
        return {
            "file": str(clipped_file),
            "map": map_file,
            "url": url,
            "source": f"NOAA EMAG2 v3 ({'上延4km' if variant == 'upcont' else '海平面'})",
            "resolution": "2 arc-minutes (~3.7 km)",
        }
    except Exception as e:
        logger.warning("EMAG2 下载裁剪失败: %s", e)
        return None


# ============================================================
# WGM2012 全球布格重力异常
# ============================================================



def download_wgm2012(
    roi: Dict[str, Any],
    output_dir: Path,
) -> Optional[Dict[str, Any]]:
    """
    下载 WGM2012 全球布格重力异常，并按 ROI bbox 裁剪

    Returns:
        {"file": 裁剪后文件路径, "url": 原始URL, "source": "BGI WGM2012"}
    """
    bbox = get_bbox_tuple(roi, use_expanded=True)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 全局缓存
    cache_dir = _ensure_cache_dir()
    cache_file = cache_dir / "wgm2012_bouguer_global.nc"
    clipped_file = output_dir / "wgm2012_bouguer_clipped.tif"
    lock = _download_locks["wgm2012"]

    try:
        with lock:
            if not cache_file.exists():
                logger.info("下载 WGM2012 全球布格重力异常 (~550 MB NetCDF)...")
                download_file(WGM2012_BOUGUER_URL, cache_file)

        # 用 xarray 打开并裁剪
        logger.info("裁剪重力数据到 ROI 范围...")
        ds = xr.open_dataset(cache_file)

        # WGM2012 NetCDF 的典型结构: lon, lat, bouguer
        lon_var = [v for v in ds.coords if 'lon' in v.lower()][0]
        lat_var = [v for v in ds.coords if 'lat' in v.lower()][0]
        data_var = [v for v in ds.data_vars][0]

        west, south, east, north = bbox

        # WGM2012 lon 通常是 0-360
        if ds[lon_var].max() > 180:
            west = west + 360 if west < 0 else west
            east = east + 360 if east < 0 else east

        ds_cropped = ds.sel(
            **{lon_var: slice(west, east)},
            **{lat_var: slice(south, north)},
        )

        # 保存为 GeoTIFF
        ds_cropped[data_var].rio.set_spatial_dims(
            x_dim=lon_var, y_dim=lat_var
        ).rio.write_crs("EPSG:4326").rio.to_raster(str(clipped_file))

        ds.close()

        logger.info("重力数据已裁剪: %s", clipped_file)

        # 生成布格重力异常分布图（复用通用栅格绘图，非对称顺序色阶 + mGal）
        map_path = output_dir / "wgm2012_bouguer_map.png"
        map_file = _generate_magnetic_map(
            clipped_file, map_path, roi,
            title="WGM2012 布格重力异常分布 (mGal)",
            cmap="viridis", unit="mGal", symmetric=False,
            value_name="Bouguer Gravity Anomaly",
        )

        return {
            "file": str(clipped_file),
            "map": map_file,
            "url": WGM2012_BOUGUER_URL,
            "source": "BGI WGM2012 (World Gravity Map)",
            "resolution": "2 arc-minutes (~4 km)",
        }
    except Exception as e:
        logger.warning("WGM2012 下载裁剪失败: %s", e)
        return None


# ============================================================
# ICGEM 在线重力计算链接生成
# ============================================================



def generate_icgem_link(roi: Dict[str, Any], mineral_info: Optional[Dict] = None) -> str:
    """
    生成 ICGEM 在线重力计算链接（用户点击后可自定义参数计算）

    ICGEM 支持: 选择重力场模型 → 选择功能量 (重力异常/布格异常/geoid等) → 自定义网格
    """
    b = roi['bbox']
    center = roi['center']

    # ICGEM 使用的是可视化界面，无法完全用 URL 参数预设
    # 生成说明和链接
    params = {
        "west": b['west'],
        "south": b['south'],
        "east": b['east'],
        "north": b['north'],
    }

    url = f"{ICGEM_CALC_URL}?{urlencode(params)}"

    return url


# ============================================================
# SRTM DEM (地形)
# ============================================================

def generate_dem_download_info(roi: Dict[str, Any]) -> Dict[str, Any]:
    """
    生成 DEM 数据下载信息（不直接下载，提供多个渠道和链接）

    使用 OpenTopography API 或指向 USGS 下载
    """
    bbox = get_bbox_tuple(roi, use_expanded=True)

    # OpenTopography API
    ot_url = (
        f"{OPENTOPOGRAPHY_URL}?"
        f"demtype=SRTMGL3&west={bbox[0]}&south={bbox[1]}"
        f"&east={bbox[2]}&north={bbox[3]}&outputFormat=GTiff"
    )

    return {
        "srtm_30m": {
            "source": "SRTM GL3 (30m)",
            "opentopography_url": ot_url,
            "opentopography_note": "需注册 OpenTopography 账号（免费）",
            "usgs_url": EE_URL,
            "gscloud_url": f"http://www.gscloud.cn/sources/?cdataid=302",
            "note": "地理空间数据云 (gscloud.cn) 国内下载速度最快，注册即可",
        }
    }


def download_dem(roi: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    """
    自动下载 ROI 范围 SRTM DEM（OpenTopography API）并出地形图。

    有 OPENTOPOGRAPHY_API_KEY 才下载+出图；否则降级为下载链接信息。

    Returns:
        downloaded=True: {source, resolution, file, map, stats, downloaded, links}
        downloaded=False: {source, downloaded, links}
    """
    bbox = get_bbox_tuple(roi, use_expanded=True)
    info = generate_dem_download_info(roi)["srtm_30m"]
    links = [
        {"label": "OpenTopography SRTM GL3", "url": info["opentopography_url"], "note": info["opentopography_note"]},
        {"label": "地理空间数据云 — DEM", "url": info["gscloud_url"], "note": info["note"]},
    ]

    if not OPENTOPOGRAPHY_API_KEY:
        logger.info("未配置 OPENTOPOGRAPHY_API_KEY，DEM 降级为下载链接")
        return {"source": "SRTM GL3 (30m)", "downloaded": False, "links": links}

    try:
        dem_dir = Path(output_dir) / "dem"
        dem_dir.mkdir(parents=True, exist_ok=True)
        tif = dem_dir / "srtm_dem.tif"
        url = (
            f"{OPENTOPOGRAPHY_URL}?demtype=SRTMGL3"
            f"&west={bbox[0]}&south={bbox[1]}&east={bbox[2]}&north={bbox[3]}"
            f"&outputFormat=GTiff&API_Key={OPENTOPOGRAPHY_API_KEY}"
        )
        logger.info("下载 SRTM DEM (OpenTopography)...")
        download_file(url, tif)

        # 高程统计
        stats = None
        with rasterio.open(tif) as src:
            arr = src.read(1).astype(np.float64)
            if src.nodata is not None:
                arr = np.ma.masked_equal(arr, src.nodata)
            stats = {
                "min": round(float(np.ma.min(arr)), 1),
                "max": round(float(np.ma.max(arr)), 1),
                "mean": round(float(np.ma.mean(arr)), 1),
            }

        # 出图（复用通用栅格绘图，地形色阶/米）
        png = dem_dir / "dem_map.png"
        map_file = _generate_magnetic_map(
            tif, png, roi,
            title="SRTM 地形高程分布 (m)",
            cmap="terrain", unit="m", symmetric=False, value_name="Elevation",
        )
        logger.info("DEM 已下载并出图: %s", tif)
        return {
            "source": "SRTM GL3 (30m, OpenTopography)",
            "resolution": "~30 m",
            "file": str(tif),
            "map": map_file,
            "stats": stats,
            "downloaded": True,
            "links": links,
        }
    except Exception as e:
        logger.warning("DEM 下载/出图失败，降级为链接: %s", e)
        return {"source": "SRTM GL3 (30m)", "downloaded": False, "links": links}


# ============================================================
# 工具函数
# ============================================================

def _clip_raster(
    input_path: Path,
    output_path: Path,
    bbox: tuple,
) -> None:
    """用 bbox 裁剪 raster 文件"""
    with rasterio.open(input_path) as src:
        # 转换 bbox 到 raster 的 CRS
        src_crs = src.crs
        if src_crs and src_crs.to_string() != "EPSG:4326":
            # bbox 是 WGS84，需要转换
            west, south, east, north = bbox
            transformed = transform_bounds("EPSG:4326", src_crs, west, south, east, north)
            window = from_bounds(*transformed, src.transform)
        else:
            window = from_bounds(*bbox, src.transform)

        window_data = src.read(1, window=window)

        # 计算新的 transform
        new_transform = src.window_transform(window)

        # 保存
        profile = src.profile.copy()
        profile.update({
            "height": window_data.shape[0],
            "width": window_data.shape[1],
            "transform": new_transform,
            "driver": "GTiff",
            "compress": "lzw",
        })

        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(window_data, 1)


def _generate_magnetic_map(
    raster_path: Path,
    output_path: Path,
    roi: Dict[str, Any],
    title: str = "EMAG2 v3 磁异常分布 (nT)",
    cmap: str = "coolwarm",
    unit: str = "nT",
    symmetric: bool = True,
    value_name: str = "Magnetic Anomaly",
) -> Optional[str]:
    """
    生成 ROI 区域内栅格（磁/重力等）分布热力图 PNG

    通用栅格热力图：默认参数适配 EMAG2 磁异常（coolwarm 对称色阶, nT）；
    重力等数据可传 cmap/unit/symmetric/value_name 复用本函数。

    从裁剪后的 EMAG2 GeoTIFF 读取数据，用 matplotlib 绘制：
    - coolwarm 色阶热力图（零值居中对称）
    - 原始 ROI 边界黑线叠加
    - 中心点黄色星标
    - 经纬度网格和色棒

    Args:
        raster_path: 裁剪后 EMAG2 GeoTIFF 的路径
        output_path: PNG 输出路径
        roi: parse_roi + expand_bbox 的输出（含 geometry / bbox / center）
        title: 图表标题

    Returns:
        PNG 绝对路径；失败时返回 None（不影响流水线）
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        # 配置 CJK 字体回退（macOS / Linux 均可用）
        matplotlib.rcParams['font.sans-serif'] = [
            'Hiragino Sans GB', 'Lantinghei SC', 'Heiti TC',
            'STHeiti', 'SimHei', 'Noto Sans CJK SC',
            'DejaVu Sans',
        ]
        matplotlib.rcParams['axes.unicode_minus'] = False
    except ImportError:
        logger.warning("matplotlib 未安装，无法生成磁异常分布图")
        return None

    try:
        # 1. 读取裁剪后的栅格数据
        with rasterio.open(raster_path) as src:
            data = src.read(1).astype(np.float64)
            rows, cols = data.shape
            lon = np.linspace(src.bounds.left, src.bounds.right, cols)
            lat = np.linspace(src.bounds.top, src.bounds.bottom, rows)
            nodata_val = src.nodata

        # 2. 处理 nodata
        if nodata_val is not None:
            mask = data == nodata_val
            data = np.ma.masked_where(mask, data)

        # 3. 全部为 nodata → 放弃
        if np.ma.is_masked(data) and data.mask.all():
            logger.warning("磁异常数据全部为 NODATA，跳过分布图")
            return None

        # 4. 极小 ROI（< 2x2 像素）→ 降级为文本标注
        if rows < 2 or cols < 2:
            fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
            val = float(data[0, 0]) if rows >= 1 and cols >= 1 else 0.0
            ax.text(
                0.5, 0.5,
                f"{value_name}\nat Center Point:\n{val:.1f} {unit}",
                transform=ax.transAxes, ha='center', va='center',
                fontsize=14, fontfamily='monospace',
            )
            ax.set_title(title)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(output_path, bbox_inches='tight', dpi=100)
            plt.close(fig)
            return str(output_path)

        # 5. 绘制热力图
        fig, ax = plt.subplots(figsize=(12, 9), dpi=150)

        vmin = float(np.ma.min(data))
        vmax = float(np.ma.max(data))
        if symmetric:
            # 对称色阶（磁异常，零值居中）
            vlim = max(abs(vmin), abs(vmax))
            if vlim < 0.1:
                vlim = 1.0  # 极小区间兜底
            lo, hi = -vlim, vlim
        else:
            # 顺序色阶（布格重力异常多为负值区间，按实际范围着色）
            if abs(vmax - vmin) < 0.1:
                vmin, vmax = vmin - 1.0, vmax + 1.0
            lo, hi = vmin, vmax

        im = ax.pcolormesh(lon, lat, data, cmap=cmap,
                           vmin=lo, vmax=hi, shading='auto')

        # 6. 叠加原始 ROI 边界
        try:
            roi_shape = shape_from_geojson(roi.get('geometry'))
            if roi_shape is not None:
                if roi_shape.geom_type == 'MultiPolygon':
                    polys = list(roi_shape.geoms)
                else:
                    polys = [roi_shape]
                for poly in polys:
                    x, y = poly.exterior.xy
                    ax.plot(list(x), list(y), 'k-', linewidth=1.5, label='ROI')
        except Exception:
            pass  # 边界叠加失败不影响主流程

        # 7. 标记中心点
        center = roi.get('center', {})
        if center.get('lon') is not None and center.get('lat') is not None:
            ax.plot(center['lon'], center['lat'],
                    marker='*', color='yellow', markersize=12,
                    markeredgecolor='black', markeredgewidth=0.8,
                    zorder=5, label='Center')

        # 8. 色棒 / 网格 / 标签
        cbar = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.02)
        cbar.set_label(f'{value_name} ({unit})', fontsize=10)
        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.set_title(title)
        ax.grid(True, linestyle=':', alpha=0.4)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc='upper right', fontsize=9)

        fig.tight_layout()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches='tight', dpi=150)
        plt.close(fig)

        return str(output_path)

    except Exception as e:
        logger.warning("磁异常分布图生成失败: %s", e)
        return None


def clip_external_emag2(
    emag2_path: Path,
    roi: Dict[str, Any],
    output_dir: Path,
    variant: str = "upcont",
) -> Optional[Dict[str, Any]]:
    """
    用本地已下载的 EMAG2 全球 GeoTIFF，裁剪到 ROI 并生成分布图

    当 NOAA 服务器不可达时，用户可从浏览器手动下载全球 EMAG2，
    然后用此函数进行本地裁剪和出图。

    Args:
        emag2_path: 本地 EMAG2 全球 GeoTIFF 路径
        roi: parse_roi + expand_bbox 的输出
        output_dir: 输出目录
        variant: "upcont" (上延 4km) 或 "sealevel" (海平面)

    Returns:
        {"file": ..., "map": ..., "source": ..., "resolution": ...}
        失败返回 None
    """
    bbox = get_bbox_tuple(roi, use_expanded=True)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    clipped_file = output_dir / f"emag2_{variant}_clipped.tif"
    map_path = output_dir / f"emag2_{variant}_map.png"

    try:
        # 裁剪全球文件到 ROI 范围
        logger.info("裁剪 EMAG2 全球文件到 ROI 范围...")
        _clip_raster(emag2_path, clipped_file, bbox)
        logger.info("磁异常数据已裁剪: %s", clipped_file)

        # 生成分布图
        map_file = None
        try:
            map_file = _generate_magnetic_map(clipped_file, map_path, roi)
            if map_file:
                logger.info("磁异常分布图已生成: %s", map_path)
        except Exception as e:
            logger.warning("分布图生成失败 (非致命): %s", e)

        return {
            "file": str(clipped_file),
            "map": map_file,
            "url": f"file://{emag2_path}",
            "source": (
                f"NOAA EMAG2 v3 (本地文件, "
                f"{'上延4km' if variant == 'upcont' else '海平面'})"
            ),
            "resolution": "2 arc-minutes (~3.7 km)",
        }
    except Exception as e:
        logger.warning("EMAG2 本地裁剪失败: %s", e)
        return None


def fetch_all_geophysical(
    roi: Dict[str, Any],
    output_dir: Path,
    mineral_info: Optional[Dict] = None,
    auto_download: bool = True,
) -> Dict[str, Any]:
    """
    获取所有地球物理数据

    Args:
        roi: parse_roi + expand_bbox 的输出
        output_dir: 输出目录
        mineral_info: 矿种知识库信息
        auto_download: 是否自动下载

    Returns:
        {
            "magnetic": {...},
            "gravity": {...},
            "icgem_link": "...",
            "dem": {...},
            "links": [...]
        }
    """
    results = {"magnetic": None, "gravity": None, "icgem": None, "dem": None, "links": []}

    geo_dir = output_dir / "02_地球物理资料"
    geo_dir.mkdir(parents=True, exist_ok=True)

    magnetic_dir = geo_dir / "magnetic"
    gravity_dir = geo_dir / "gravity"

    logger.info("获取地球物理数据...")
    logger.info("─" * 50)

    # EMAG2 磁法（全局缓存，175MB 下载一次，后续只裁剪）
    logger.info("[磁法数据]")
    mag = download_emag2(roi, magnetic_dir, variant="upcont")
    if mag:
        results["magnetic"] = mag
    else:
        results["links"].append({
            "label": "EMAG2 v3 磁异常手动下载",
            "url": EMAG2_GEOTIFF_URL,
            "note": "NOAA 全球磁异常网格，无需注册"
        })

    # WGM2012 重力（550MB，由 auto_download 开关控制）
    if auto_download:
        logger.info("[重力数据]")
        grav = download_wgm2012(roi, gravity_dir)
        if grav:
            results["gravity"] = grav
        else:
            results["links"].append({
                "label": "WGM2012 布格重力手动下载",
                "url": WGM2012_BOUGUER_URL,
                "note": "BGI 全球布格重力异常，无需注册"
            })
    else:
        results["links"].append({
            "label": "WGM2012 全球布格重力下载",
            "url": WGM2012_BOUGUER_URL,
            "note": "BGI 直接下载 NetCDF (~550 MB)，无注册门槛"
        })

    # ICGEM 本地重力场计算（不依赖 auto_download，纯本地计算）
    logger.info("[ICGEM 重力场计算]")
    icgem_dir = gravity_dir / "icgem"
    try:
        from .icgem_local import compute_icgem_gravity
        icgem = compute_icgem_gravity(roi, icgem_dir)
        if icgem:
            results["icgem"] = icgem
    except Exception as e:
        logger.warning("ICGEM 本地计算失败: %s", e)

    # ICGEM 链接（始终生成，作为补充或回退）
    icgem_url = generate_icgem_link(roi, mineral_info)
    if results.get("icgem"):
        results["links"].append({
            "label": "ICGEM 在线重力场计算（高级选项）",
            "url": icgem_url,
            "note": "可在线选择其他模型或更高阶次"
        })
    else:
        results["icgem_link"] = icgem_url
        results["links"].append({
            "label": "ICGEM 在线重力场计算",
            "url": icgem_url,
            "note": "GFZ Potsdam，可在线计算各类重力异常并下载"
        })

    # DEM（有 OpenTopography key 则自动下载+出图，否则降级为链接）
    results["dem"] = download_dem(roi, geo_dir)

    logger.info("─" * 50)
    return results
