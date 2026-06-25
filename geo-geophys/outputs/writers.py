"""输出与契约 —— 处理网格 GeoTIFF / 欧拉磁源点 GeoJSON / 速度体 NetCDF / metadata。

metadata.json 遵循平台 broker 契约：source/source_version/aoi_name/aoi_bbox/crs/created_at/products/model_stats。
"""

from __future__ import annotations

import os
import json
from typing import Dict, List, Optional

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.crs import CRS as RioCRS
from pyproj import Transformer


def write_grid_geotiff(path: str, arr2d: np.ndarray, fg) -> str:
    """把处理后的网格写成带 UTM 地理参考的 GeoTIFF。fg 提供 dx/dy/x_origin/y_origin/epsg。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ny, nx = arr2d.shape
    y_max = fg.y_origin + ny * fg.dy
    transform = from_origin(fg.x_origin, y_max, fg.dx, fg.dy)
    with rasterio.open(path, "w", driver="GTiff", height=ny, width=nx, count=1,
                       dtype="float32", crs=RioCRS.from_epsg(fg.epsg),
                       transform=transform, nodata=np.nan) as dst:
        dst.write(arr2d.astype(np.float32), 1)
    return path


def write_euler_geojson(path: str, euler_points: List[Dict], epsg: int) -> str:
    """欧拉磁源点(UTM x,y,depth) → GeoJSON(经纬度 + depth_m/si/confidence/misfit/cluster_id)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)
    feats = []
    for p in euler_points:
        lon, lat = tr.transform(p["x"], p["y"])
        props = {"depth_m": round(p["depth_m"], 1), "si": p.get("si"),
                 "lon": round(lon, 6), "lat": round(lat, 6)}
        if p.get("confidence") is not None:
            props["confidence"] = round(float(p["confidence"]), 3)
        if p.get("misfit") is not None:
            props["misfit"] = round(float(p["misfit"]), 4)
        if p.get("cluster_id") is not None:
            props["cluster_id"] = int(p["cluster_id"])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": props,
        })
    fc = {"type": "FeatureCollection", "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=1)
    return path


def write_euler_clusters_geojson(path: str, clusters: List[Dict], epsg: int) -> str:
    """欧拉磁源簇(UTM 质心) → GeoJSON(经纬度 + depth_m/depth_sigma_m/confidence/n_members)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)
    feats = []
    for ci, c in enumerate(clusters):
        lon, lat = tr.transform(c["x"], c["y"])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {"cluster_id": ci,
                           "depth_m": round(c["depth_m"], 1),
                           "depth_sigma_m": round(c["depth_sigma_m"], 1),
                           "confidence": round(float(c["confidence"]), 3),
                           "n_members": int(c["n_members"]),
                           "lon": round(lon, 6), "lat": round(lat, 6)},
        })
    fc = {"type": "FeatureCollection", "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=1)
    return path


def write_velocity_nc(path: str, vs: np.ndarray, fav: np.ndarray, grid) -> str:
    """ANT Vs 体 + 有利度体 → NetCDF（dims z,y,x）。"""
    import xarray as xr
    nz, ny, nx = grid.shape
    xs = grid.xmin + (np.arange(nx) + 0.5) * grid.res_m
    ys = grid.ymax - (np.arange(ny) + 0.5) * grid.res_m
    ds = xr.Dataset(
        {"vs": (("z", "y", "x"), vs.astype(np.float32)),
         "vs_favorability": (("z", "y", "x"), fav.astype(np.float32))},
        coords={"depth_m": ("z", grid.depths().astype(np.float32)),
                "x": ("x", xs.astype(np.float64)), "y": ("y", ys.astype(np.float64))},
        attrs={"crs": grid.crs.to_string(), "epsg": int(grid.epsg),
               "note": "vs=剪切波速度; favorability=低Vs→高(断层/蚀变/矿化); depth_m地表下为负"},
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ds.to_netcdf(path)
    return path


def write_metadata(out_dir: str, aoi_name: str, bbox: List[float], crs: str,
                   products: Dict, model_stats: Dict, created_at: str,
                   source_version: str = "1.0",
                   trace_id: str = None, tenant_id: str = None, upstream_metadatas: List[Dict] = None) -> str:
    meta = {
        "source": "geo-geophys",
        "source_version": source_version,
        "aoi_name": aoi_name,
        "aoi_bbox": [float(v) for v in bbox],
        "crs": crs,
        "created_at": created_at,
        "products": products,
        "model_stats": model_stats,
    }
    # 决策轨迹血缘三键（容错，不影响产物）：显式 trace_id 优先 → 上游继承 → 自生成
    try:
        from commons.trace import stamp_metadata
        stamp_metadata(meta, explicit_trace_id=trace_id, upstream_metadatas=upstream_metadatas, tenant_id=tenant_id)
    except Exception:
        pass
    path = os.path.join(out_dir, "metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return path
