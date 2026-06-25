"""数据对齐：CRS重投影、分辨率重采样、ROI裁剪"""
import logging
import numpy as np
import rasterio
from rasterio.warp import (
    calculate_default_transform, reproject, Resampling,
    transform_bounds,
)
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds
import fiona
from shapely.geometry import shape, box
from shapely.ops import unary_union

from app.config import NODATA, UPLOAD_SLOTS

logger = logging.getLogger(__name__)

# 必需栅格槽(以 config.UPLOAD_SLOTS 的 required 标记为准,去掉非栅格的 kml)。
# 其余一律视为可选:扩展蚀变波段(B02/B11/B12/B1/B3N/B9)、InSAR(速度场+相干性)、
# 以及 P4 季节差分的夏季同名波段(_summer,不在 UPLOAD_SLOTS 中)。可选槽缺 CRS/
# 损坏时优雅跳过而非中断整套7变量分析——下游均通过 aligned.get() 读取并自带退化守卫。
REQUIRED_SLOTS = {
    name for name, spec in UPLOAD_SLOTS.items()
    if spec.get("required") and name != "kml"
}


def parse_kml_roi(kml_path: str):
    """解析KML文件，返回ROI多边形和边界框"""
    fiona.drvsupport.supported_drivers["KML"] = "rw"
    with fiona.open(kml_path, driver="KML") as src:
        geometries = [shape(feat["geometry"]) for feat in src]

    if not geometries:
        raise ValueError("KML文件中未找到任何几何图形")

    roi = unary_union(geometries) if len(geometries) > 1 else geometries[0]
    return roi, roi.bounds


def detect_utm_crs(bounds: tuple) -> str:
    """根据中心经度自动检测UTM区域"""
    center_lon = (bounds[0] + bounds[2]) / 2
    center_lat = (bounds[1] + bounds[3]) / 2
    zone_number = int((center_lon + 180) / 6) + 1
    hemisphere = "north" if center_lat >= 0 else "south"
    return f"EPSG:326{zone_number:02d}" if hemisphere == "north" else f"EPSG:327{zone_number:02d}"


def align_to_common_grid(
    raster_paths: dict[str, str],
    target_crs: str,
    target_resolution: float,
    roi_bounds_4326: tuple,
) -> tuple[dict[str, np.ndarray], dict]:
    """
    将所有输入栅格重投影并重采样到公共网格。

    roi_bounds_4326: EPSG:4326 下的 (xmin, ymin, xmax, ymax)
    """
    # 将 ROI bounds 从 EPSG:4326 转换到目标 CRS（UTM，米制单位）
    roi_bounds = transform_bounds("EPSG:4326", target_crs, *roi_bounds_4326)
    xmin, ymin, xmax, ymax = roi_bounds

    if any(np.isnan(v) for v in roi_bounds):
        raise ValueError(
            f"ROI边界坐标异常(EPSG:4326): {roi_bounds_4326}。"
            "请检查KML/Excel文件中经纬度是否正确（经度±180，纬度±90）"
        )
    width = int(np.ceil((xmax - xmin) / target_resolution))
    height = int(np.ceil((ymax - ymin) / target_resolution))
    dst_transform = from_bounds(xmin, ymin, xmax, ymax, width, height)

    aligned = {}
    for slot_name, path in raster_paths.items():
        optional = slot_name not in REQUIRED_SLOTS
        try:
            with rasterio.open(path) as src:
                if src.crs is None:
                    raise ValueError(f"{slot_name}: 栅格缺少CRS信息")

                data = np.full((height, width), NODATA, dtype=np.float64)

                reproject(
                    source=rasterio.band(src, 1),
                    destination=data,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=target_crs,
                    resampling=Resampling.bilinear if "dem" not in slot_name else Resampling.nearest,
                    src_nodata=src.nodata,  # 源栅格nodata(S2/ASTER为0),避免重采样把0渗入ROI
                    dst_nodata=NODATA,
                )
        except Exception as exc:
            # 可选栅格(扩展蚀变/InSAR/夏季波段)缺CRS或损坏时跳过,不拖垮主分析;
            # 必需波段仍硬失败。下游用 aligned.get() 读取,相关诊断层自动退化。
            if optional:
                logger.warning("可选栅格 %s 对齐失败，已跳过(相关诊断层将退化): %s", slot_name, exc)
                continue
            raise
        aligned[slot_name] = data

    meta = {
        "transform": dst_transform,
        "crs": target_crs,
        "width": width,
        "height": height,
        "resolution": target_resolution,
    }
    return aligned, meta


def apply_roi_mask(
    data: np.ndarray,
    transform,
    roi_geom,
) -> np.ndarray:
    """用ROI多边形掩膜栅格数据，外部设为NODATA"""
    mask = geometry_mask(
        [roi_geom],
        (data.shape[0], data.shape[1]),
        transform,
        invert=True,
    )
    result = data.copy()
    result[~mask] = NODATA
    return result


def prepare_data(upload_dir: str) -> tuple[dict, dict, object]:
    """
    完整的数据准备流程：解析KML → 对齐栅格 → ROI裁剪

    返回 (aligned_data, metadata, roi_geometry)
    """
    from pathlib import Path
    upload_path = Path(upload_dir)

    # 1. 解析KML
    kml_files = list(upload_path.glob("*.kml"))
    if not kml_files:
        raise FileNotFoundError("未找到KML文件")
    roi, roi_bounds = parse_kml_roi(str(kml_files[0]))

    # 2. 确定目标CRS
    # KML通常在EPSG:4326，转换为UTM
    target_crs = detect_utm_crs(roi_bounds)

    # 3. 收集所有栅格路径
    raster_paths = {}
    for f in upload_path.iterdir():
        if f.suffix.lower() in (".tif", ".tiff"):
            raster_paths[f.stem] = str(f)

    # 4. 对齐到公共网格（默认30m分辨率）
    aligned, meta = align_to_common_grid(
        raster_paths,
        target_crs,
        target_resolution=30.0,
        roi_bounds_4326=roi_bounds,
    )

    # 5. ROI裁剪 — 需要把 ROI 几何体投影到目标 CRS
    from shapely.ops import transform as shapely_transform
    import pyproj
    project = pyproj.Transformer.from_crs("EPSG:4326", target_crs, always_xy=True).transform
    roi_projected = shapely_transform(project, roi)

    for name in aligned:
        aligned[name] = apply_roi_mask(aligned[name], meta["transform"], roi_projected)

    return aligned, meta, roi
