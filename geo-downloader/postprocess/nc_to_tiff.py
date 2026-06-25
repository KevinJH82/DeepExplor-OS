"""
EMIT L2A NetCDF → GeoTIFF 转换工具

流程：
  1. 用 clip_emit_nc 将 .nc 裁剪到 bbox（如果 bbox 不为 None）
  2. 读取 /reflectance 变量（shape: downtrack × crosstrack × bands）
  3. 构造仿射变换（从 /location/lat、lon 的角点推算）
  4. 写出多波段 GeoTIFF，波长存入 TIFFTAG_IMAGEDESCRIPTION
  5. 压缩：deflate + predictor=2

依赖：pip install xarray netCDF4 rasterio numpy
"""

from pathlib import Path
from typing import Optional, Tuple

try:
    import numpy as np
    import xarray as xr
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def emit_nc_to_tiff(
    nc_path: Path,
    out_path: Path,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Path:
    """
    将 EMIT L2A .nc 转换为多波段 GeoTIFF。

    Parameters
    ----------
    nc_path  : EMIT_L2A_RFL_*.nc 文件路径
    out_path : 输出 .tif 路径
    bbox     : (min_lon, min_lat, max_lon, max_lat) 裁剪范围，None 则不裁剪

    Returns
    -------
    out_path
    """
    if not HAS_DEPS:
        raise ImportError(
            "缺少依赖: xarray netCDF4 rasterio numpy\n"
            "请运行: pip install xarray netCDF4 rasterio numpy"
        )

    if out_path.exists():
        return out_path

    # ── 可选：先裁剪 .nc ────────────────────────────────────────
    if bbox is not None:
        try:
            from postprocess.emit_clip import clip_emit_nc
            clip_emit_nc(nc_path, bbox)
        except ValueError as e:
            # bbox 与影像无交集，继续转换全景
            print(f"    [警告] {e}，转换全景")
        except Exception as e:
            print(f"    [警告] EMIT裁剪失败: {e}，转换全景")

    # ── 读取反射率数据 ──────────────────────────────────────────
    ds = xr.open_dataset(nc_path, engine="netcdf4")

    # 反射率变量名：EMIT 标准为 "reflectance"
    rfl_var = None
    for candidate in ("reflectance", "rfl", "Reflectance"):
        if candidate in ds:
            rfl_var = candidate
            break
    if rfl_var is None:
        ds.close()
        raise RuntimeError(
            f"{nc_path.name}: 找不到反射率变量（reflectance/rfl），"
            f"实际变量: {list(ds.data_vars)}"
        )

    rfl = ds[rfl_var].values   # (downtrack, crosstrack, bands) float32
    # fill_value → nan
    fill = ds[rfl_var].attrs.get("_FillValue", -9999)
    rfl = rfl.astype(np.float32)
    rfl[rfl == fill] = np.nan

    # 波长列表（存入 metadata）
    wavelengths = []
    if "wavelengths" in ds:
        wavelengths = ds["wavelengths"].values.tolist()
    elif "bands" in ds.coords:
        wavelengths = ds.coords["bands"].values.tolist()

    ds.close()

    n_rows, n_cols, n_bands = rfl.shape

    # ── 读取经纬度，构造仿射变换 ────────────────────────────────
    # 优先使用全局 geotransform 属性（裁剪后的 .nc 没有 location group）
    import netCDF4 as _nc4
    raw_nc = _nc4.Dataset(nc_path)
    gt_attr = getattr(raw_nc, "geotransform", None)
    raw_nc.close()

    if gt_attr is not None:
        # geotransform = [west, pixel_width, 0, north, 0, -pixel_height]
        import numpy as _np2
        gt = [float(x) for x in (gt_attr if hasattr(gt_attr, '__iter__') else str(gt_attr).split())]
        from rasterio.transform import Affine
        transform = Affine(gt[1], gt[2], gt[0], gt[4], gt[5], gt[3])
    else:
        try:
            loc_ds = xr.open_dataset(nc_path, engine="netcdf4", group="location")
            lat2d = loc_ds["lat"].values
            lon2d = loc_ds["lon"].values
            loc_ds.close()
            west  = float(lon2d[:, 0].min())
            east  = float(lon2d[:, -1].max())
            south = float(lat2d[-1, :].min())
            north = float(lat2d[0, :].max())
            transform = from_bounds(west, south, east, north, n_cols, n_rows)
        except Exception:
            # 最后回退：从 ds 坐标推算
            lon_vals = ds.coords.get("longitude", ds.coords.get("lon", None))
            lat_vals = ds.coords.get("latitude", ds.coords.get("lat", None))
            if lon_vals is not None and lat_vals is not None:
                west  = float(lon_vals.min())
                east  = float(lon_vals.max())
                south = float(lat_vals.min())
                north = float(lat_vals.max())
                transform = from_bounds(west, south, east, north, n_cols, n_rows)
            else:
                raise RuntimeError(f"{nc_path.name}: 无法获取地理坐标信息")

    # ── 写 GeoTIFF ──────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": n_cols,
        "height": n_rows,
        "count": n_bands,
        "crs": CRS.from_epsg(4326),
        "transform": transform,
        "compress": "deflate",
        "predictor": 3,          # float predictor
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }

    # rasterio 期望 (bands, rows, cols)
    data = np.moveaxis(rfl, -1, 0)

    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(data)
        if wavelengths:
            wl_str = ",".join(f"{w:.4f}" for w in wavelengths)
            dst.update_tags(wavelengths=wl_str, n_bands=str(n_bands))

    print(
        f"    [EMIT→TIFF] {nc_path.name} "
        f"→ {out_path.name} "
        f"({n_rows}×{n_cols}×{n_bands}波段)"
    )
    return out_path
