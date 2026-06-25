"""
EMIT L2A NetCDF 空间裁剪工具

EMIT .nc 使用非仿射的 2-D lat/lon 坐标网格（正交经纬度），
无法用 rasterio.mask 直接处理，需要用 xarray 按像素坐标做矩形子集。

裁剪策略：
  - 读取 /location/lat 和 /location/lon 变量（每像素中心坐标）
  - 按 bbox 选出行列范围（行索引 downtrack，列索引 crosstrack）
  - 对所有包含 (downtrack, crosstrack) 维度的变量做同样的切片
  - 写入新文件，保持所有属性（全局属性 + 变量属性）不变
  - 删除原文件，输出路径与输入相同（原地覆盖）

安装依赖：pip install xarray netCDF4
"""

from pathlib import Path
from typing import Tuple

try:
    import numpy as np
    import xarray as xr
    HAS_XARRAY = True
except ImportError:
    HAS_XARRAY = False


def clip_emit_nc(
    nc_path: Path,
    bbox: Tuple[float, float, float, float],
) -> Path:
    """
    将 EMIT L2A .nc 文件裁剪到给定 bbox 范围（就地覆盖）。

    Parameters
    ----------
    nc_path : Path
        输入 .nc 文件路径（EMIT_L2A_RFL / RFLUNCERT / MASK）
    bbox : (min_lon, min_lat, max_lon, max_lat)
        WGS-84 裁剪范围

    Returns
    -------
    裁剪后的文件路径（与输入相同）

    Raises
    ------
    ImportError   : 缺少 xarray / netCDF4
    RuntimeError  : 文件不包含 EMIT 标准经纬度变量
    ValueError    : bbox 与影像无交集
    """
    if not HAS_XARRAY:
        raise ImportError(
            "缺少依赖: xarray netCDF4\n请运行: pip install xarray netCDF4"
        )

    min_lon, min_lat, max_lon, max_lat = bbox

    ds = xr.open_dataset(nc_path, engine="netcdf4")

    # ── 定位经纬度变量 ──────────────────────────────────────────────
    # EMIT 标准：group=/location，变量名 lat / lon
    # open_dataset 默认不打开子组，需要单独读取
    try:
        loc_ds = xr.open_dataset(nc_path, engine="netcdf4", group="location")
        lat2d = loc_ds["lat"].values   # shape: (downtrack, crosstrack)
        lon2d = loc_ds["lon"].values
        loc_ds.close()
    except Exception:
        ds.close()
        raise RuntimeError(
            f"{nc_path.name}: 找不到 /location/lat 或 /location/lon，"
            "可能不是标准 EMIT L2A 产品"
        )

    # ── 按 bbox 找行列范围 ─────────────────────────────────────────
    # 空间掩码：True = 在 bbox 内
    in_bbox = (
        (lat2d >= min_lat) & (lat2d <= max_lat) &
        (lon2d >= min_lon) & (lon2d <= max_lon)
    )

    row_mask = in_bbox.any(axis=1)   # 每行是否有像素在 bbox 内
    col_mask = in_bbox.any(axis=0)   # 每列是否有像素在 bbox 内

    row_indices = np.where(row_mask)[0]
    col_indices = np.where(col_mask)[0]

    if row_indices.size == 0 or col_indices.size == 0:
        ds.close()
        raise ValueError(
            f"{nc_path.name}: bbox {bbox} 与影像无交集，跳过裁剪"
        )

    row_start, row_end = int(row_indices[0]), int(row_indices[-1]) + 1
    col_start, col_end = int(col_indices[0]), int(col_indices[-1]) + 1

    # 如果已经是全图范围（bbox 包含整景），无需写新文件
    n_rows, n_cols = lat2d.shape
    if row_start == 0 and row_end == n_rows and col_start == 0 and col_end == n_cols:
        ds.close()
        print(f"    [EMIT裁剪] {nc_path.name} bbox 覆盖全景，无需裁剪")
        return nc_path

    # ── 对主数据集中所有含 downtrack/crosstrack 的变量做切片 ────────
    dim_names = list(ds.dims)
    # EMIT 标准维名称：downtrack, crosstrack（也有用 y, x 的）
    row_dim = next((d for d in dim_names if "downtrack" in d or d == "y"), None)
    col_dim = next((d for d in dim_names if "crosstrack" in d or d == "x"), None)

    if row_dim is None or col_dim is None:
        ds.close()
        raise RuntimeError(
            f"{nc_path.name}: 找不到 downtrack/crosstrack（或 y/x）维度，"
            f"实际维度: {dim_names}"
        )

    ds_clip = ds.isel(
        {row_dim: slice(row_start, row_end),
         col_dim: slice(col_start, col_end)}
    )
    ds.close()

    # ── 写临时文件，再替换原文件 ────────────────────────────────────
    tmp_path = nc_path.with_suffix(".nc.tmp")
    try:
        ds_clip.to_netcdf(tmp_path, format="NETCDF4")
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"写入临时文件失败: {e}")

    nc_path.unlink()
    tmp_path.rename(nc_path)

    rows_clipped = row_end - row_start
    cols_clipped = col_end - col_start
    print(
        f"    [EMIT裁剪] {nc_path.name} "
        f"({n_rows}×{n_cols}) → ({rows_clipped}×{cols_clipped}) 像素"
    )
    return nc_path
