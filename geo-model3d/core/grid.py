"""VoxelGrid — 体元网格与坐标系（geo-model3d 的地基）。

设计要点：
- 输入 bbox(EPSG:4326)，按形心自动选 UTM 投影；水平网格在 UTM 米制下规则化。
- Z 轴 = 地表下相对深度（0 至 −z_max，向下为负），P1 不依赖 DEM 绝对高程。
- reproject_to_grid：把任一 GeoTIFF 重投影/重采样到本网格的水平面 (ny,nx)。
- 体元属性数组约定 shape = (nz, ny, nx)，float32。
"""

from __future__ import annotations

import math
from typing import List, Tuple, Optional

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_origin
from pyproj import CRS, Transformer


def utm_epsg_for_lonlat(lon: float, lat: float) -> int:
    """按经纬度返回 UTM 带的 EPSG 整数（北半球 326xx / 南半球 327xx）。"""
    zone = int(math.floor((lon + 180.0) / 6.0) % 60) + 1
    return (32600 if lat >= 0 else 32700) + zone


class VoxelGrid:
    def __init__(self, bbox_wgs84: List[float], res_m: float = 30.0,
                 z_max_m: float = 2000.0, dz_m: float = 100.0,
                 max_cells: int = 8_000_000):
        """
        bbox_wgs84: [min_lon, min_lat, max_lon, max_lat]
        res_m: 水平分辨率(米); z_max_m: 地表下最大深度(米); dz_m: 深度步长(米)
        max_cells: 体元总数上限，超出则自动放大 res_m（防爆内存）。
        """
        self.bbox_wgs84 = [float(v) for v in bbox_wgs84]
        min_lon, min_lat, max_lon, max_lat = self.bbox_wgs84
        cen_lon = 0.5 * (min_lon + max_lon)
        cen_lat = 0.5 * (min_lat + max_lat)

        self.epsg = utm_epsg_for_lonlat(cen_lon, cen_lat)
        self.crs = CRS.from_epsg(self.epsg)

        # bbox → UTM 米制范围
        x0, y0, x1, y1 = transform_bounds("EPSG:4326", self.crs.to_string(),
                                          min_lon, min_lat, max_lon, max_lat, densify_pts=21)
        self.xmin, self.ymin, self.xmax, self.ymax = x0, y0, x1, y1
        width_m = max(self.xmax - self.xmin, 1.0)
        height_m = max(self.ymax - self.ymin, 1.0)

        self.res_m = float(res_m)
        self.dz_m = float(dz_m)
        self.z_max_m = float(z_max_m)

        # 体元数量预算：必要时放大水平分辨率
        nz0 = max(1, int(round(self.z_max_m / self.dz_m)))
        nx0 = max(1, int(math.ceil(width_m / self.res_m)))
        ny0 = max(1, int(math.ceil(height_m / self.res_m)))
        if nz0 * ny0 * nx0 > max_cells:
            scale = math.sqrt((nz0 * ny0 * nx0) / max_cells)
            self.res_m *= scale
            nx0 = max(1, int(math.ceil(width_m / self.res_m)))
            ny0 = max(1, int(math.ceil(height_m / self.res_m)))

        self.nx, self.ny, self.nz = nx0, ny0, nz0

        # 仿射变换（左上角原点，行向下）：UTM
        self.transform = from_origin(self.xmin, self.ymax, self.res_m, self.res_m)

        # 深度数组（米，向下为负）：层中心
        self._depths = -(np.arange(self.nz, dtype=np.float64) + 0.5) * self.dz_m

    # ── 基本属性 ──
    @property
    def shape(self) -> Tuple[int, int, int]:
        return (self.nz, self.ny, self.nx)

    @property
    def shape2d(self) -> Tuple[int, int]:
        return (self.ny, self.nx)

    def depths(self) -> np.ndarray:
        """(nz,) 层中心深度（米，负值=地表下）。"""
        return self._depths.copy()

    def n_cells(self) -> int:
        return int(self.nz * self.ny * self.nx)

    # ── 栅格重投影到本网格水平面 ──
    def reproject_to_grid(self, src_tif_path: str, resampling: str = 'bilinear',
                          band: int = 1) -> np.ndarray:
        """读取任一 GeoTIFF，重投影/重采样到本网格 (ny,nx)，返回 float32（无效=NaN）。"""
        rs = getattr(Resampling, resampling, Resampling.bilinear)
        dst = np.full(self.shape2d, np.nan, dtype=np.float32)
        with rasterio.open(src_tif_path) as src:
            src_arr = src.read(band).astype(np.float32)
            # 把 src nodata 转为 NaN
            if src.nodata is not None:
                src_arr = np.where(src_arr == np.float32(src.nodata), np.nan, src_arr)
            reproject(
                source=src_arr,
                destination=dst,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=self.transform,
                dst_crs=self.crs,
                resampling=rs,
                src_nodata=np.nan,
                dst_nodata=np.nan,
            )
        return dst

    # ── 经纬度 ↔ 网格行列 ──
    def lonlat_to_rowcol(self, lon: float, lat: float) -> Optional[Tuple[int, int]]:
        """经纬度 → (row, col)；落在网格外返回 None。"""
        tr = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        x, y = tr.transform(lon, lat)
        col = int((x - self.xmin) / self.res_m)
        row = int((self.ymax - y) / self.res_m)
        if 0 <= row < self.ny and 0 <= col < self.nx:
            return row, col
        return None

    def colrow_to_lonlat(self, col: int, row: int) -> Tuple[float, float]:
        """(col,row) 像元中心 → (lon,lat)。"""
        x = self.xmin + (col + 0.5) * self.res_m
        y = self.ymax - (row + 0.5) * self.res_m
        tr = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        lon, lat = tr.transform(x, y)
        return float(lon), float(lat)

    def depth_index(self, depth_m: float) -> int:
        """深度(米，负或正绝对值)→最近的 z 层索引。"""
        d = -abs(depth_m)
        idx = int(round((-d) / self.dz_m - 0.5))
        return int(np.clip(idx, 0, self.nz - 1))

    def slice_transform(self):
        """深度切片 GeoTIFF 用的仿射 + CRS。"""
        return self.transform, self.crs.to_string()

    def summary(self) -> dict:
        return {
            "epsg": self.epsg,
            "crs": self.crs.to_string(),
            "bbox_wgs84": self.bbox_wgs84,
            "shape_nz_ny_nx": [self.nz, self.ny, self.nx],
            "res_m": round(self.res_m, 2),
            "dz_m": self.dz_m,
            "z_max_m": self.z_max_m,
            "n_cells": self.n_cells(),
        }
