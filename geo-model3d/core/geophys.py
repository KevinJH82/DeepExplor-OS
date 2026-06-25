"""消费 geo-geophys 物探产物 —— 把磁源深度/速度体/磁边界变成 geo-model3d 的真实深度/三维证据。

这是方向二的回报点：让三维模型的深度从"纯知识软推断"升级为"知识 + 物探实测约束"。
- 欧拉磁源深度点 → 在对应平面位置用实测深度局部锐化/替换知识深度门控（实测处不确定性下降）。
- ANT/地震速度有利度体 → 平台第一份真三维证据，直接进融合。
- 磁解析信号(AS) → 附加 2D 构造/边界证据。
任一缺失只降级、不报错。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


def _import_commons():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for repo in (here, "/opt/deepexplor-services"):
        if repo and repo not in sys.path:
            sys.path.insert(0, repo)


def _norm01(arr):
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    lo, hi = np.percentile(finite, [2, 98])
    if hi <= lo:
        return np.where(np.isfinite(arr), 0.0, np.nan).astype(np.float32)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def load_geophys(bbox, grid, root: str, mineral: Optional[str] = None) -> Optional[Dict]:
    """经 geophys_broker 取本 AOI 物探产物，对齐到 grid。返回 dict 或 None。"""
    _import_commons()
    try:
        from commons.geophys_broker import find_geophys_for_bbox, get_product_path, load_euler_sources
    except Exception as e:
        logger.info(f"geophys_broker 不可用: {e}")
        return None
    entries = find_geophys_for_bbox(tuple(bbox), root)
    if not entries:
        return {"status": "missing", "root": root}
    from core.ingest import select_best_entry      # 最佳 AOI 匹配（重叠→矿种→最新）
    entry, sel = select_best_entry(entries, bbox, mineral)
    out: Dict = {"status": "ok", "run_id": entry.get("run_id"), "selection": sel,
                 "scale_note": (entry.get("model_stats") or {}).get("scale_note")}

    # 磁解析信号 → 2D 构造证据
    as_p = get_product_path(entry, "magnetic_analytic_signal")
    if as_p:
        out["magnetic"] = _norm01(grid.reproject_to_grid(as_p))

    # 欧拉磁源深度点
    out["euler_points"] = load_euler_sources(entry)

    # 速度有利度体 → 3D 证据
    vp = get_product_path(entry, "velocity_volume_nc")
    if vp:
        vol = _reproject_velocity_to_grid(vp, grid)
        if vol is not None:
            out["vs_favorability"] = vol
    return out


def _reproject_velocity_to_grid(nc_path: str, grid) -> Optional[np.ndarray]:
    """把 geo-geophys 速度体(NetCDF, coords x/y UTM + depth_m)插值到 geo-model3d grid。"""
    try:
        import xarray as xr
        from scipy.interpolate import RegularGridInterpolator
        ds = xr.open_dataset(nc_path)
        if "vs_favorability" not in ds:
            return None
        fav = np.asarray(ds["vs_favorability"].values, dtype=np.float32)  # (z,y,x)
        xs = np.asarray(ds["x"].values, dtype=np.float64)
        ys = np.asarray(ds["y"].values, dtype=np.float64)
        dz = np.abs(np.asarray(ds["depth_m"].values, dtype=np.float64))
        # 规整递增
        if ys[0] > ys[-1]:
            ys = ys[::-1]; fav = fav[:, ::-1, :]
        if xs[0] > xs[-1]:
            xs = xs[::-1]; fav = fav[:, :, ::-1]
        order = np.argsort(dz); dz = dz[order]; fav = fav[order]
        interp = RegularGridInterpolator((dz, ys, xs), fav, bounds_error=False, fill_value=np.nan)

        nz, ny, nx = grid.shape
        gx = grid.xmin + (np.arange(nx) + 0.5) * grid.res_m
        gy = grid.ymax - (np.arange(ny) + 0.5) * grid.res_m
        gd = np.abs(grid.depths())
        GD, GY, GX = np.meshgrid(gd, gy, gx, indexing="ij")
        out = interp(np.column_stack([GD.ravel(), GY.ravel(), GX.ravel()])).reshape(grid.shape)
        # 覆盖外保留 NaN（不要零填充——否则下游融合会把无速度数据处的分数误压低）
        return out.astype(np.float32)
    except Exception as e:
        logger.info(f"速度体重投影失败: {e}")
        return None


def measured_depth_gate(grid, euler_points: List[Dict], sigma_z_m: float = 300.0,
                        radius_cells: int = 2) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """由欧拉磁源点/簇(lon,lat,depth)建实测深度门控体 + 平面置信度覆盖。
    每个磁源用**自身深度带** depth_sigma_m 作 z 向高斯宽度（无则回退 sigma_z_m），
    平面权重再乘该源 confidence——稳/集中的磁源约束更强，飘的更弱。
    返回 (gate3d[nz,ny,nx] ∈[0,1], coverage2d[ny,nx] ∈[0,1])；无点返回 (None,None)。"""
    if not euler_points:
        return None, None
    nz, ny, nx = grid.shape
    gate = np.zeros(grid.shape, dtype=np.float32)
    cov = np.zeros((ny, nx), dtype=np.float32)
    depths = np.abs(grid.depths())
    for p in euler_points:
        lon, lat, dep = p.get("lon"), p.get("lat"), p.get("depth_m")
        if lon is None or lat is None or dep is None:
            continue
        rc = grid.lonlat_to_rowcol(float(lon), float(lat))
        if rc is None:
            continue
        r0, c0 = rc
        sz = p.get("depth_sigma_m")
        sz = max(float(sz), 1.0) if sz else float(sigma_z_m)
        conf = float(p.get("confidence", 1.0) or 1.0)
        zprof = np.exp(-((depths - abs(float(dep))) ** 2) / (2 * sz ** 2)).astype(np.float32)
        for dr in range(-radius_cells, radius_cells + 1):
            for dc in range(-radius_cells, radius_cells + 1):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < ny and 0 <= c < nx:
                    wxy = conf * np.exp(-(dr * dr + dc * dc) / (2 * (radius_cells or 1) ** 2))
                    gate[:, r, c] = np.maximum(gate[:, r, c], wxy * zprof)
                    cov[r, c] = max(cov[r, c], wxy)
    return gate, cov


def blend_depth_gate(knowledge_profile: np.ndarray, grid,
                     measured_gate: Optional[np.ndarray], coverage: Optional[np.ndarray],
                     w_measured: float = 0.7) -> np.ndarray:
    """把知识深度剖面(nz,)广播为(nz,ny,nx)，并在有实测磁源深度的列用实测局部混合。

    混合权重**逐列由置信度驱动**：coverage 已含磁源 confidence，故 w(列)=w_measured*coverage，
    高置信列实测占比接近 w_measured、低置信列回落到知识深度——闭合上游不确定度到三维门控。"""
    nz, ny, nx = grid.shape
    dc3d = np.broadcast_to(knowledge_profile[:, None, None], grid.shape).astype(np.float32).copy()
    if measured_gate is None or coverage is None:
        return dc3d
    cov3d = coverage[None, :, :]
    w_map = (w_measured * np.clip(coverage, 0.0, 1.0)).astype(np.float32)[None, :, :]
    blended = (1.0 - w_map) * dc3d + w_map * measured_gate
    # 仅在有覆盖处混合，无覆盖处保留知识
    dc3d = np.where(cov3d > 0.05, blended, dc3d).astype(np.float32)
    return dc3d
