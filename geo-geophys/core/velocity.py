"""ANT/被动源地震 3D 速度体接入适配器。

ExoSphere 等 ANT 流程产出 3D 剪切波速度 Vs(x,y,z) 体；本模块把外部 Vs 体重投影到
统一体元网格(复用 geo-model3d VoxelGrid 约定)，并按"低 Vs → 有利(断层/破碎/蚀变/矿化)"
生成有利度体 [0,1]。无数据时返回 None，由上层标注 absent、不报错。

支持输入：
- NetCDF：含 vs 变量 + 经纬(lon/lat 或 x/y) + 深度(depth/z) 坐标。
- CSV：列 lon,lat,depth_m,vs（或 x,y,z,vs），散点 → 最近邻/反距离插值到网格。
"""

from __future__ import annotations

import os
from typing import Optional, Tuple, Dict

import numpy as np

from core.grid import VoxelGrid
from utils.logger import get_logger

logger = get_logger(__name__)


def _norm_low_is_good(vs_vol: np.ndarray) -> np.ndarray:
    """低 Vs→高有利度：对有限值做 2–98 百分位归一并反向。"""
    finite = vs_vol[np.isfinite(vs_vol)]
    if finite.size == 0:
        return np.zeros_like(vs_vol)
    lo, hi = np.percentile(finite, [2, 98])
    if hi <= lo:
        return np.zeros_like(vs_vol)
    fav = 1.0 - np.clip((vs_vol - lo) / (hi - lo), 0.0, 1.0)
    return np.where(np.isfinite(vs_vol), fav, 0.0).astype(np.float32)


def _load_netcdf(path: str) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """返回 (vs[nz,ny,nx], lon[nx], lat[ny], depth_m[nz])。失败 None。"""
    try:
        import xarray as xr
        ds = xr.open_dataset(path)
        vname = next((v for v in ds.data_vars if str(v).lower() in
                      ("vs", "velocity", "vsv", "v_s", "shear_velocity")), None)
        if vname is None:
            vname = list(ds.data_vars)[0]
        da = ds[vname]
        # 找坐标
        def _find(names):
            for n in da.dims:
                if str(n).lower() in names:
                    return n
            return None
        zname = _find(("depth", "z", "depth_m", "lev"))
        yname = _find(("lat", "latitude", "y"))
        xname = _find(("lon", "longitude", "x"))
        if not (zname and yname and xname):
            logger.info(f"NetCDF 维度无法识别: {da.dims}")
            return None
        da = da.transpose(zname, yname, xname)
        vs = np.asarray(da.values, dtype=np.float32)
        lon = np.asarray(ds[xname].values, dtype=np.float64)
        lat = np.asarray(ds[yname].values, dtype=np.float64)
        depth = np.abs(np.asarray(ds[zname].values, dtype=np.float64))
        return vs, lon, lat, depth
    except Exception as e:
        logger.info(f"NetCDF 读取失败: {e}")
        return None


def _load_csv(path: str) -> Optional[np.ndarray]:
    """返回 Nx4 数组 [lon,lat,depth_m,vs]。"""
    try:
        import csv
        rows = []
        with open(path, newline="", encoding="utf-8") as f:
            rdr = csv.DictReader(f)
            cols = {c.lower(): c for c in (rdr.fieldnames or [])}
            lonc = cols.get("lon") or cols.get("longitude") or cols.get("x")
            latc = cols.get("lat") or cols.get("latitude") or cols.get("y")
            depc = cols.get("depth_m") or cols.get("depth") or cols.get("z")
            vsc = cols.get("vs") or cols.get("velocity")
            if not all([lonc, latc, depc, vsc]):
                return None
            for r in rdr:
                rows.append([float(r[lonc]), float(r[latc]),
                             abs(float(r[depc])), float(r[vsc])])
        return np.asarray(rows, dtype=np.float64) if rows else None
    except Exception as e:
        logger.info(f"CSV 读取失败: {e}")
        return None


def ingest_velocity_model(path: str, grid: VoxelGrid) -> Optional[Dict]:
    """把外部 Vs 体重采样到 grid，返回 {'vs': vol, 'favorability': vol, 'source': ...}。
    失败/不支持返回 None。"""
    if not path or not os.path.exists(path):
        return None
    from scipy.interpolate import RegularGridInterpolator, griddata

    nz, ny, nx = grid.shape
    depths = np.abs(grid.depths())                      # (nz,) 正深度
    # 网格体元中心经纬度
    cols = np.arange(nx); rows = np.arange(ny)
    lon2d = np.zeros((ny, nx)); lat2d = np.zeros((ny, nx))
    for r in rows:
        for c in cols:
            lon2d[r, c], lat2d[r, c] = grid.colrow_to_lonlat(int(c), int(r))

    lp = path.lower()
    vs_vol = np.full(grid.shape, np.nan, dtype=np.float32)
    src = None

    if lp.endswith(".nc"):
        loaded = _load_netcdf(path)
        if loaded is None:
            return None
        vs, lon, lat, depth = loaded
        # 规整坐标递增
        if lat[0] > lat[-1]:
            lat = lat[::-1]; vs = vs[:, ::-1, :]
        if lon[0] > lon[-1]:
            lon = lon[::-1]; vs = vs[:, :, ::-1]
        order = np.argsort(depth); depth = depth[order]; vs = vs[order]
        interp = RegularGridInterpolator((depth, lat, lon), vs,
                                         bounds_error=False, fill_value=np.nan)
        for k, dm in enumerate(depths):
            pts = np.column_stack([np.full(ny*nx, dm), lat2d.ravel(), lon2d.ravel()])
            vs_vol[k] = interp(pts).reshape(ny, nx)
        src = "netcdf"
    elif lp.endswith(".csv"):
        pc = _load_csv(path)
        if pc is None:
            return None
        for k, dm in enumerate(depths):
            # 取邻近深度层散点，平面插值
            band = pc[np.abs(pc[:, 2] - dm) <= max(grid.dz_m, 1.0)]
            use = band if len(band) >= 4 else pc
            try:
                vs_vol[k] = griddata(use[:, :2], use[:, 3],
                                     (lon2d, lat2d), method="linear")
            except Exception:
                pass
        src = "csv"
    else:
        return None

    if not np.isfinite(vs_vol).any():
        return None
    fav = _norm_low_is_good(vs_vol)
    return {"vs": vs_vol.astype(np.float32), "favorability": fav,
            "source": src, "coverage": float(np.isfinite(vs_vol).mean())}
