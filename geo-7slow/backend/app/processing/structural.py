"""结构地质:地形应力集中代理 + 线性构造(断裂)检测。

复用 geo-stru 的成熟实现(importlib 零 sys.path 污染加载其 core/ 两个自洽模块):
  - terrain_utils.TerrainProcessor: 坡度、曲率(拉普拉斯)、多方位山体阴影
  - lineament.extract_lineaments: 多方位山体阴影 → 百分位归一 → 自适应分位 Canny
      (use_quantiles, low=0.8/high=0.92,自适应,替代原硬编码 0.05/0.25 与逐瓦片 min-max)
      → 骨架化 → 概率 Hough 线段 → 线性体密度/走向

InSAR 速度场在交付库不可用(同 AST_08,跨服务),故 ④ 当前以 DEM 线性体密度为主;
insar_velocity 传入时保留接口(由 geo-stru 的 deformation 思路扩展,此处暂以 DEM 为准)。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np

from app.config import NODATA

_GEOSTRU_CORE = Path(os.environ.get(
    "GEOSTRU_CORE", "/opt/deepexplor-services/geo-stru/core",
))
_terrain = None
_lineament = None


def _load():
    """importlib 加载 geo-stru 的 terrain_utils 与 lineament(两者自洽,无包内相对导入)。"""
    global _terrain, _lineament
    if _terrain is not None:
        return _terrain, _lineament

    def _imp(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    _terrain = _imp("geostru_terrain_utils", _GEOSTRU_CORE / "terrain_utils.py")
    _lineament = _imp("geostru_lineament", _GEOSTRU_CORE / "lineament.py")
    return _terrain, _lineament


def available() -> bool:
    try:
        return (_GEOSTRU_CORE / "terrain_utils.py").exists() and (_GEOSTRU_CORE / "lineament.py").exists()
    except Exception:
        return False


def _valid(a: np.ndarray) -> np.ndarray:
    return (a != NODATA) & np.isfinite(a)


def _zmask(data: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.zeros_like(data, dtype=np.float64)
    v = data[mask]
    if v.size and float(v.std()) > 1e-10:
        out[mask] = (data[mask] - float(v.mean())) / float(v.std())
    return out


def _filled(dem: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """无效像元用 ROI 内均值填充,避免坡度/曲率在 NODATA 边界产生伪梯度。"""
    fill = float(dem[mask].mean()) if np.any(mask) else 0.0
    return np.where(mask, dem.astype(np.float64), fill)


def topographic_stress(dem: np.ndarray, pixel_size_m=(30.0, 30.0)):
    """
    地形应力集中代理 = z(坡度) + z(|曲率|)。

    坡度反映起伏;曲率(坡度的二阶导)在坡折/脊谷处放大,正是应力集中带。
    比原"单纯 DEM 梯度幅值"更贴近构造应力。返回 (stress, mask)。
    """
    tu, _ = _load()
    TP = tu.TerrainProcessor
    mask = _valid(dem)
    if not np.any(mask):
        return np.full(dem.shape, NODATA, dtype=np.float64), mask

    demf = _filled(dem, mask)
    slope = TP.compute_slope(demf, pixel_size_m)        # 度
    curv = TP.compute_curvature(demf, pixel_size_m)     # 1/100m

    m = mask & np.isfinite(slope) & np.isfinite(curv)
    stress_vals = _zmask(np.nan_to_num(slope), m) + _zmask(np.abs(np.nan_to_num(curv)), m)
    out = np.full(dem.shape, NODATA, dtype=np.float64)
    out[m] = stress_vals[m]
    return out, m


def fault_lineament_density(dem: np.ndarray, transform, pixel_size_m=(30.0, 30.0),
                            valid_mask: np.ndarray = None, seed: int = 42) -> np.ndarray:
    """
    线性构造(断裂)密度场:多方位山体阴影 + 自适应 Canny + 概率 Hough(复用 geo-stru)。

    返回线性体密度 (H,W),无效区 NODATA。失败时返回全 NODATA(让 ④ 自然退化)。
    """
    mask = valid_mask if valid_mask is not None else _valid(dem)
    out = np.full(dem.shape, NODATA, dtype=np.float64)
    if not np.any(mask):
        return out
    try:
        tu, lin = _load()
        TP = tu.TerrainProcessor
        demf = _filled(dem, mask)
        multidir = TP.compute_multidirectional_hillshade(demf, pixel_size_m)
        slope = TP.compute_slope(demf, pixel_size_m)
        res = lin.extract_lineaments(
            multidir_hillshade=multidir,
            slope=np.nan_to_num(slope),
            pixel_size_m=pixel_size_m,
            transform=transform,
            valid_mask=mask,
            rng_seed=seed,
        )
        density = res.get("density")
        if density is None:
            return out
        out[mask] = np.nan_to_num(density)[mask]
    except Exception:
        # 任何失败都退回全 NODATA,不阻断流水线
        return np.full(dem.shape, NODATA, dtype=np.float64)
    return out
