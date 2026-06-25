"""工具函数：COG写入等"""
import numpy as np
import rasterio
from rasterio.enums import Resampling
from app.config import NODATA


def write_cog(
    data: np.ndarray,
    output_path: str,
    transform,
    crs: str,
    nodata: float = NODATA,
    dtype: str = "float32",
    overviews: list[int] = None,
):
    """将numpy数组写入Cloud Optimized GeoTIFF"""
    if overviews is None:
        overviews = [2, 4, 8, 16]

    profile = {
        "driver": "GTiff",
        "dtype": dtype,
        "width": data.shape[1],
        "height": data.shape[0],
        "count": 1,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
    }

    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)

        # 写入波段统计信息，帮助 GIS 软件自动拉伸显示
        valid = np.isfinite(data) & (data != nodata)
        if np.any(valid):
            vals = data[valid].astype(np.float64)
            dst.update_tags(1,
                STATISTICS_MINIMUM=str(float(np.min(vals))),
                STATISTICS_MAXIMUM=str(float(np.max(vals))),
                STATISTICS_MEAN=str(float(np.mean(vals))),
                STATISTICS_STDDEV=str(float(np.std(vals))),
                STATISTICS_VALID_PERCENT=str(float(100 * np.sum(valid) / data.size)),
            )

        # 构建内部概览（多尺度）
        valid_overviews = [o for o in overviews
                          if o <= min(data.shape[0], data.shape[1]) // 256]
        if valid_overviews:
            dst.build_overviews(valid_overviews, Resampling.average)
            dst.update_tags(ns="rio_overview", resampling="average")
