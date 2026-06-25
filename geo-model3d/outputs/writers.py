"""输出与契约 —— NetCDF 体 / 深度切片 GeoTIFF / targets_3d.json / metadata.json。

metadata.json 遵循平台 broker 契约：source/source_version/aoi_name/aoi_bbox/crs/created_at/products/model_stats。
"""

from __future__ import annotations

import os
import json
from typing import Dict, List

import numpy as np
import rasterio
from rasterio.crs import CRS as RioCRS


def write_volume_netcdf(path: str, score: np.ndarray, uncertainty: np.ndarray, grid) -> str:
    """有利度体 + 不确定性体 → NetCDF（dims z,y,x；coords depth_m/x/y UTM）。"""
    import xarray as xr
    nz, ny, nx = grid.shape
    xs = grid.xmin + (np.arange(nx) + 0.5) * grid.res_m
    ys = grid.ymax - (np.arange(ny) + 0.5) * grid.res_m
    depth_m = grid.depths()
    ds = xr.Dataset(
        data_vars={
            "prospectivity": (("z", "y", "x"), score.astype(np.float32)),
            "uncertainty": (("z", "y", "x"), uncertainty.astype(np.float32)),
        },
        coords={"depth_m": ("z", depth_m.astype(np.float32)),
                "x": ("x", xs.astype(np.float64)), "y": ("y", ys.astype(np.float64))},
        attrs={"crs": grid.crs.to_string(), "epsg": int(grid.epsg),
               "res_m": float(grid.res_m), "dz_m": float(grid.dz_m),
               "note": "prospectivity/uncertainty ∈ [0,1]; depth_m 地表下为负"},
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ds.to_netcdf(path)
    return path


def write_depth_slices(out_dir: str, score: np.ndarray, grid) -> List[str]:
    """每个深度层一张 GeoTIFF（带 UTM 地理参考），文件名含深度。"""
    os.makedirs(out_dir, exist_ok=True)
    transform, crs_str = grid.slice_transform()
    depths = grid.depths()
    paths: List[str] = []
    for z in range(grid.nz):
        dm = int(round(abs(depths[z])))
        fn = os.path.join(out_dir, f"depth_slice_-{dm:04d}m.tif")
        with rasterio.open(fn, "w", driver="GTiff", height=grid.ny, width=grid.nx,
                           count=1, dtype="float32", crs=RioCRS.from_string(crs_str),
                           transform=transform, nodata=np.nan) as dst:
            dst.write(score[z].astype(np.float32), 1)
        paths.append(fn)
    return paths


def _nms_targets_2d(best2d: np.ndarray, zidx2d: np.ndarray, grid, score: np.ndarray,
                    uncertainty: np.ndarray, top_n: int, min_sep_cells: int):
    """在 2D 最优评分图上做贪心非极大抑制，返回三维靶点列表。"""
    ny, nx = best2d.shape
    flat = best2d.copy()
    flat[~np.isfinite(flat)] = -1.0
    order = np.argsort(flat, axis=None)[::-1]
    chosen = []
    occupied = np.zeros((ny, nx), dtype=bool)
    depths = grid.depths()
    for idx in order:
        if len(chosen) >= top_n:
            break
        r, c = divmod(int(idx), nx)
        if flat[r, c] <= 0:
            break
        if occupied[r, c]:
            continue
        z = int(zidx2d[r, c])
        lon, lat = grid.colrow_to_lonlat(c, r)
        chosen.append({
            "rank": len(chosen) + 1,
            "lon": round(lon, 6), "lat": round(lat, 6),
            "depth_m": int(round(abs(depths[z]))),
            "score": round(float(score[z, r, c]), 4),
            "uncertainty": round(float(uncertainty[z, r, c]), 4),
        })
        r0, r1 = max(0, r - min_sep_cells), min(ny, r + min_sep_cells + 1)
        c0, c1 = max(0, c - min_sep_cells), min(nx, c + min_sep_cells + 1)
        occupied[r0:r1, c0:c1] = True
    return chosen


def write_targets_3d(path: str, score: np.ndarray, uncertainty: np.ndarray, grid,
                     top_n: int = 20, min_sep_cells: int = 2) -> List[Dict]:
    """三维靶点：每个 (y,x) 取深度方向最优体元，2D NMS 选 top_n。写 JSON 并返回列表。"""
    best2d = np.nanmax(score, axis=0)
    zidx2d = np.nanargmax(np.where(np.isfinite(score), score, -1.0), axis=0)
    targets = _nms_targets_2d(best2d, zidx2d, grid, score, uncertainty, top_n, min_sep_cells)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"targets": targets, "n": len(targets)}, f, ensure_ascii=False, indent=2)
    return targets


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def write_targets_kml(path: str, targets: List[Dict], aoi_name: str = "") -> str:
    """把三维靶点列表导出为 KML（每个靶点一个 Placemark，按 rank 命名）。

    坐标采用 经度,纬度,高程；高程 = 负深度(米)，altitudeMode=relativeToGround，
    便于在 Google Earth / QGIS 中按地下深度展示。返回写出的路径。
    """
    name = _xml_escape(aoi_name or "三维成矿靶点")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "  <Document>",
        f"    <name>{name} 三维成矿靶点</name>",
        f"    <description>geo-model3d 三维成矿预测靶点，共 {len(targets)} 个，按 rank 排序。"
        "坐标高程 = 负深度(米)，relativeToGround。</description>",
        "    <Style id=\"target\">",
        "      <IconStyle>",
        "        <color>ff00d7ff</color>",
        "        <scale>1.1</scale>",
        "        <Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon>",
        "      </IconStyle>",
        "      <LabelStyle><scale>0.9</scale></LabelStyle>",
        "    </Style>",
        "    <Folder>",
        f"      <name>三维靶点 (Top {len(targets)})</name>",
    ]
    for t in targets:
        rank = t.get("rank")
        depth = t.get("depth_m")
        desc = (f"rank={rank} | 深度={depth}m | 有利度={t.get('score')} | "
                f"不确定性={t.get('uncertainty')}")
        parts += [
            "      <Placemark>",
            f"        <name>{_xml_escape(rank)}</name>",
            f"        <description>{_xml_escape(desc)}</description>",
            "        <styleUrl>#target</styleUrl>",
            "        <Point><altitudeMode>relativeToGround</altitudeMode>"
            f"<coordinates>{t.get('lon')},{t.get('lat')},{-abs(depth) if depth is not None else 0}</coordinates></Point>",
            "      </Placemark>",
        ]
    parts += ["    </Folder>", "  </Document>", "</kml>", ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


def write_metadata(out_dir: str, aoi_name: str, bbox: List[float], grid,
                   products: Dict[str, str], model_stats: Dict,
                   created_at: str, source_version: str = "1.0",
                   trace_id: str = None, tenant_id: str = None, upstream_metadatas: List[Dict] = None) -> str:
    """平台 broker 契约 metadata.json。

    trace_id / upstream_metadatas（可选）：注入决策轨迹库的血缘三键
    （trace_id/linked_trace_ids/trace_origin）。model3d 是多源融合枢纽，
    传入各上游 broker 命中的 metadata 即可沿血缘继承 trace_id（见架构蓝图 §1）。
    """
    meta = {
        "source": "geo-model3d",
        "source_version": source_version,
        "aoi_name": aoi_name,
        "aoi_bbox": [float(v) for v in bbox],
        "crs": grid.crs.to_string(),
        "created_at": created_at,
        "grid": grid.summary(),
        "products": products,
        "model_stats": model_stats,
    }
    # 决策轨迹血缘：显式 trace_id 优先，否则从上游继承，否则自生成（容错，不影响产物）
    try:
        from commons.trace import stamp_metadata
        stamp_metadata(meta, explicit_trace_id=trace_id,
                       upstream_metadatas=upstream_metadatas, tenant_id=tenant_id)
    except Exception:
        pass
    path = os.path.join(out_dir, "metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return path
