#!/usr/bin/env python3
"""单元测试：geo-model3d 消费物探置信度（闭环回报点）+ 旧产物回归兼容。

可直接 `python3 tests/test_geophys_confidence.py`（无 pytest 时）。
- measured_depth_gate：高置信磁源 → 更高覆盖；逐点 depth_sigma_m 生效。
- blend_depth_gate：高置信列实测占比 > 低置信列。
- 回归：旧式点（无 confidence/depth_sigma_m）→ 默认兜底，不报错。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.dirname(HERE)
sys.path.insert(0, SVC)

import numpy as np
from core.grid import VoxelGrid
from core import geophys as gmod


def _grid():
    # 小网格，覆盖足够多平面单元以放置两个分离磁源
    return VoxelGrid([120.0, 37.0, 120.2, 37.2], res_m=400.0, z_max_m=3000.0, dz_m=200.0)


def _two_sources(grid):
    # 取两个相距足够远的单元，换算回经纬度作磁源位置
    (ny, nx) = grid.shape2d if hasattr(grid, "shape2d") else grid.shape[1:]
    lonA, latA = grid.colrow_to_lonlat(int(nx * 0.25), int(ny * 0.5))
    lonB, latB = grid.colrow_to_lonlat(int(nx * 0.75), int(ny * 0.5))
    high = {"lon": lonA, "lat": latA, "depth_m": 1500.0, "confidence": 0.9, "depth_sigma_m": 200.0}
    low = {"lon": lonB, "lat": latB, "depth_m": 1500.0, "confidence": 0.2, "depth_sigma_m": 1500.0}
    return high, low, grid.lonlat_to_rowcol(lonA, latA), grid.lonlat_to_rowcol(lonB, latB)


def test_confidence_drives_coverage():
    grid = _grid()
    high, low, rcA, rcB = _two_sources(grid)
    gate, cov = gmod.measured_depth_gate(grid, [high, low])
    assert gate is not None and cov is not None
    rA, cA = rcA
    rB, cB = rcB
    assert cov[rA, cA] > cov[rB, cB], f"高置信覆盖应更大: {cov[rA,cA]:.3f} vs {cov[rB,cB]:.3f}"


def test_blend_weight_confidence_driven():
    grid = _grid()
    high, low, rcA, rcB = _two_sources(grid)
    gate, cov = gmod.measured_depth_gate(grid, [high, low])
    nz = grid.shape[0]
    knowledge = np.full(nz, 0.3, dtype=np.float32)  # 知识剖面常数，便于看实测拉动
    blended = gmod.blend_depth_gate(knowledge, grid, gate, cov)
    rA, cA = rcA
    rB, cB = rcB
    # 实测拉动量 = |blended - knowledge|，在源深附近最大；高置信列拉动更强
    pullA = float(np.max(np.abs(blended[:, rA, cA] - knowledge)))
    pullB = float(np.max(np.abs(blended[:, rB, cB] - knowledge)))
    assert pullA > pullB, f"高置信列实测拉动应更强: {pullA:.3f} vs {pullB:.3f}"


def test_legacy_points_regression():
    """旧式欧拉点（无 confidence/depth_sigma_m）→ 默认兜底，不报错。"""
    grid = _grid()
    (ny, nx) = grid.shape[1:]
    lon, lat = grid.colrow_to_lonlat(int(nx * 0.5), int(ny * 0.5))
    legacy = [{"lon": lon, "lat": lat, "depth_m": 1200.0, "si": 1.0}]  # P1 旧格式
    gate, cov = gmod.measured_depth_gate(grid, legacy)
    assert gate is not None and cov is not None
    r, c = grid.lonlat_to_rowcol(lon, lat)
    assert cov[r, c] > 0.0  # 无 confidence → 默认 1.0
    knowledge = np.full(grid.shape[0], 0.3, dtype=np.float32)
    blended = gmod.blend_depth_gate(knowledge, grid, gate, cov)
    assert np.all(np.isfinite(blended))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n✅ {len(fns)} 个闭环/回归测试全部通过")


if __name__ == "__main__":
    _run_all()
