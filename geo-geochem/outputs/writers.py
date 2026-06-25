"""输出与契约 —— 元素/组合异常 GeoTIFF、异常浓集中心 GeoJSON、metadata.json。

metadata.json 遵循平台 broker 契约：source/source_version/aoi_name/aoi_bbox/crs/created_at/products/model_stats。
"""

from __future__ import annotations

import os
import json
from typing import Dict, List

import numpy as np
import rasterio
from rasterio.crs import CRS as RioCRS
from pyproj import Transformer
from scipy import ndimage


def write_grid_geotiff(path: str, arr2d: np.ndarray, grid) -> str:
    """把网格写成带 UTM 地理参考的 GeoTIFF（用 grid.transform / grid.epsg）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ny, nx = arr2d.shape
    with rasterio.open(path, "w", driver="GTiff", height=ny, width=nx, count=1,
                       dtype="float32", crs=RioCRS.from_epsg(grid.epsg),
                       transform=grid.transform, nodata=np.nan) as dst:
        dst.write(arr2d.astype(np.float32), 1)
    return path


def write_anomalies_geojson(path: str, combined: np.ndarray, grid,
                            min_intensity: float = 0.5, top_n: int = 30) -> str:
    """组合异常场 → 浓集中心 GeoJSON（连通域质心 + 强度/面积/衬度，经纬度）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    feats: List[Dict] = []
    mask = np.isfinite(combined) & (combined >= min_intensity)
    if mask.any():
        labels, n = ndimage.label(mask)
        cell_km2 = (grid.res_m / 1000.0) ** 2
        tr = Transformer.from_crs(grid.epsg, 4326, always_xy=True)
        bg = float(np.nanmedian(combined[np.isfinite(combined)]))
        regions = []
        for lab in range(1, n + 1):
            sel = labels == lab
            cnt = int(sel.sum())
            if cnt < 2:
                continue
            rows, cols = np.where(sel)
            r_c, c_c = rows.mean(), cols.mean()
            x = grid.xmin + (c_c + 0.5) * grid.res_m
            y = grid.ymax - (r_c + 0.5) * grid.res_m
            lon, lat = tr.transform(x, y)
            peak = float(np.nanmax(combined[sel]))
            mean = float(np.nanmean(combined[sel]))
            regions.append({"lon": float(lon), "lat": float(lat), "peak": peak, "mean": mean,
                            "area_km2": round(cnt * cell_km2, 4),
                            "contrast": round(mean / (bg + 1e-9), 2)})
        regions.sort(key=lambda r: r["peak"] * r["area_km2"], reverse=True)
        for i, r in enumerate(regions[:top_n]):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(r["lon"], 6), round(r["lat"], 6)]},
                "properties": {"rank": i + 1, "peak_intensity": round(r["peak"], 3),
                               "mean_intensity": round(r["mean"], 3),
                               "area_km2": r["area_km2"], "contrast": r["contrast"],
                               "lon": round(r["lon"], 6), "lat": round(r["lat"], 6)},
            })
    fc = {"type": "FeatureCollection", "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=1)
    return path


def write_metadata(out_dir: str, aoi_name: str, bbox: List[float], crs: str,
                   products: Dict, model_stats: Dict, created_at: str,
                   source_version: str = "1.0",
                   trace_id: str = None, tenant_id: str = None, upstream_metadatas: List[Dict] = None) -> str:
    meta = {
        "source": "geo-geochem",
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
