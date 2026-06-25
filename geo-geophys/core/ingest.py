"""取上游物探网格 —— 经 datacolle_broker 发现 prospector 已存的 EMAG2 磁 / ICGEM 重力 GeoTIFF，
重投影到 UTM 米制等间距网格、填 NaN，供位场处理。任一缺失只降级、不报错。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field as dc_field
from typing import Dict, Optional, Tuple

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_origin
from rasterio.crs import CRS as RioCRS

from core.grid import utm_epsg_for_lonlat
from utils.logger import get_logger

logger = get_logger(__name__)

_MAG_REL = "02_地球物理资料/magnetic/emag2_upcont_clipped.tif"
_GRAV_REL = "02_地球物理资料/gravity/icgem/gravity_disturbance.tif"
_GRAV_WGM_REL = "02_地球物理资料/gravity/wgm2012_bouguer_clipped.tif"


def _import_commons():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for repo in (here, "/opt/deepexplor-services"):
        if repo and repo not in sys.path:
            sys.path.insert(0, repo)


@dataclass
class FieldGrid:
    name: str
    field: np.ndarray            # (ny,nx) float32, 无 NaN（已填充）
    valid: np.ndarray            # (ny,nx) bool 原始有效掩码
    dx: float                    # 米
    dy: float                    # 米
    x_origin: float              # UTM xmin
    y_origin: float              # UTM ymin
    epsg: int
    bbox_wgs84: list
    source_path: str
    unit: str = ""


def _reproject_to_utm(tif_path: str, bbox_wgs84, target_res_m: float = None) -> Optional[FieldGrid]:
    """处理**源栅格完整范围**（prospector 已带 ~40km 区域缓冲），而非裁到小 AOI——
    区域位场处理需要区域数据。bbox 仅用于选 UTM 带；输出 bbox_wgs84 = 源范围(区域)。"""
    with rasterio.open(tif_path) as src:
        src_crs = src.crs or RioCRS.from_epsg(4326)   # EMAG2 clip 可能无 CRS，按度距判定为 4326
        arr = src.read(1).astype(np.float32)
        nodata = src.nodata
        if nodata is not None:
            arr = np.where(arr == np.float32(nodata), np.nan, arr)
        src_transform = src.transform
        sb = src.bounds
        # 源栅格原生分辨率(米)
        native_m = abs(src.res[0]) * 111000.0 * np.cos(np.radians(0.5 * (sb.bottom + sb.top))) \
            if not src_crs.is_projected else abs(src.res[0])

    # 区域范围(源栅格四至)转 4326，再选 UTM
    s_lon0, s_lat0, s_lon1, s_lat1 = transform_bounds(src_crs, "EPSG:4326",
                                                      sb.left, sb.bottom, sb.right, sb.top)
    cen_lon, cen_lat = 0.5 * (s_lon0 + s_lon1), 0.5 * (s_lat0 + s_lat1)
    epsg = utm_epsg_for_lonlat(cen_lon, cen_lat)
    dst_crs = RioCRS.from_epsg(epsg)

    if target_res_m is None:
        target_res_m = float(np.clip(native_m / 4.0, 250.0, 2000.0))   # 上采样4x,平滑FFT

    x0, y0, x1, y1 = transform_bounds("EPSG:4326", dst_crs.to_string(),
                                      s_lon0, s_lat0, s_lon1, s_lat1, densify_pts=21)
    width = max(x1 - x0, target_res_m)
    height = max(y1 - y0, target_res_m)
    nx = max(8, int(np.ceil(width / target_res_m)))
    ny = max(8, int(np.ceil(height / target_res_m)))
    dst_transform = from_origin(x0, y1, target_res_m, target_res_m)

    dst = np.full((ny, nx), np.nan, dtype=np.float32)
    reproject(source=arr, destination=dst,
              src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_transform, dst_crs=dst_crs,
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)

    valid = np.isfinite(dst)
    if valid.sum() < 9:
        return None
    filled = dst.copy()
    filled[~valid] = float(np.nanmean(dst))   # 填均值，避免 FFT NaN

    return FieldGrid(name="", field=filled.astype(np.float32), valid=valid,
                     dx=target_res_m, dy=target_res_m, x_origin=x0, y_origin=y0,
                     epsg=epsg, bbox_wgs84=[s_lon0, s_lat0, s_lon1, s_lat1],
                     source_path=tif_path)


def _target_res(bbox_wgs84) -> float:
    """按 AOI 跨度定目标分辨率（区域尺度），夹在 [250, 2000] m。"""
    min_lon, min_lat, max_lon, max_lat = bbox_wgs84
    span_m = max((max_lon - min_lon) * 111000 * np.cos(np.radians(0.5*(min_lat+max_lat))),
                 (max_lat - min_lat) * 111000)
    return float(np.clip(span_m / 80.0, 250.0, 2000.0))


def gather_potential_fields(bbox, roots: Dict[str, str]) -> Tuple[Dict[str, FieldGrid], Dict]:
    """返回 ({'magnetic':FieldGrid?, 'gravity':FieldGrid?}, provenance)。"""
    _import_commons()
    prov: Dict = {}
    out: Dict[str, FieldGrid] = {}
    tgt = None   # 自动按源栅格原生分辨率(区域)定

    # 经 datacolle_broker 找匹配本 AOI 的 prospector 输出目录
    out_dirs = []
    try:
        from commons.datacolle_broker import find_datacolle_for_bbox
        entries = find_datacolle_for_bbox(tuple(bbox), roots["datacolle"])
        out_dirs = [e.get("out_dir") for e in entries if e.get("out_dir")]
        prov["datacolle_matches"] = len(out_dirs)
    except Exception as e:
        prov["datacolle_broker"] = f"import/scan failed: {e}"

    def _first_existing(rel):
        for d in out_dirs:
            p = os.path.join(d, rel)
            if os.path.exists(p):
                return p
        return None

    # 磁
    mag_p = _first_existing(_MAG_REL)
    if mag_p:
        fg = _reproject_to_utm(mag_p, bbox, tgt)
        if fg:
            fg.name, fg.unit = "magnetic", "nT"
            out["magnetic"] = fg
            prov["magnetic"] = {"status": "ok", "source": "EMAG2_UpCont(prospector)",
                                "path": mag_p, "shape": list(fg.field.shape),
                                "res_m": fg.dx, "note": "全球~4km且已上延4km,区域尺度"}
        else:
            prov["magnetic"] = {"status": "too_few_pixels", "path": mag_p}
    else:
        prov["magnetic"] = {"status": "missing", "hint": "prospector 未存 emag2_upcont_clipped.tif"}

    # 重力（ICGEM 优先，回退 WGM2012）
    grav_p = _first_existing(_GRAV_REL) or _first_existing(_GRAV_WGM_REL)
    if grav_p:
        fg = _reproject_to_utm(grav_p, bbox, tgt)
        if fg:
            fg.name, fg.unit = "gravity", "mGal"
            out["gravity"] = fg
            prov["gravity"] = {"status": "ok", "source": "ICGEM/WGM(prospector)",
                               "path": grav_p, "shape": list(fg.field.shape),
                               "res_m": fg.dx, "note": "全球~14km,极粗,仅区域趋势"}
        else:
            prov["gravity"] = {"status": "too_few_pixels", "path": grav_p}
    else:
        prov["gravity"] = {"status": "missing"}

    return out, prov
