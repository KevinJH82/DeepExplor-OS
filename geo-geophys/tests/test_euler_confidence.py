#!/usr/bin/env python3
"""单元测试：欧拉置信度 + 磁源聚类。

可用 pytest 运行，也可直接 `python3 tests/test_euler_confidence.py`（无 pytest 时）。
- euler_deconvolution：解带 confidence∈[0,1]/misfit≥0；干净场置信度 ≥ 加噪场。
- cluster_euler_sources：两簇分离点 → 2 簇，质心/深度带/置信度合理；退化/空输入兼容。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.dirname(HERE)
sys.path.insert(0, SVC)

import numpy as np
from core import potential_field as pf


def _two_bump_field(ny=60, nx=60, sigma=5.0, amp=120.0):
    yy, xx = np.mgrid[0:ny, 0:nx]
    f = np.zeros((ny, nx))
    for cy, cx in [(18, 18), (42, 44)]:
        f += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2))
    return f


def test_confidence_range_and_fields():
    dx = dy = 500.0
    f = _two_bump_field()
    pts = pf.euler_deconvolution(f, dy, dx, 500000.0, 4000000.0, si=1.0, window=10,
                                 depth_min_m=100, depth_max_m=20000)
    assert pts, "应解出磁源点"
    for p in pts:
        assert 0.0 <= p["confidence"] <= 1.0
        assert p["misfit"] >= 0.0
        assert set(("x", "y", "depth_m", "si")) <= set(p)  # 旧字段保留


def test_noise_lowers_confidence():
    dx = dy = 500.0
    f = _two_bump_field()
    clean = pf.euler_deconvolution(f, dy, dx, 500000.0, 4000000.0, window=10,
                                   depth_min_m=100, depth_max_m=20000)
    rng = np.random.default_rng(0)
    noisy_field = f + 40.0 * rng.standard_normal(f.shape)
    noisy = pf.euler_deconvolution(noisy_field, dy, dx, 500000.0, 4000000.0, window=10,
                                   depth_min_m=100, depth_max_m=20000)
    mc_clean = float(np.mean([p["confidence"] for p in clean]))
    mc_noisy = float(np.mean([p["confidence"] for p in noisy])) if noisy else 0.0
    assert mc_clean >= mc_noisy, f"加噪应不提升置信度: clean={mc_clean:.3f} noisy={mc_noisy:.3f}"


def test_clustering_two_groups():
    # 两组明显分离的点（水平相距 ~50km，深度相差 ~3km）
    pts = []
    for i in range(5):
        pts.append({"x": 1000 + i * 60, "y": 1000 + i * 40, "depth_m": 2000 + i * 30, "confidence": 0.8})
    for i in range(5):
        pts.append({"x": 51000 + i * 60, "y": 51000 + i * 40, "depth_m": 5000 + i * 30, "confidence": 0.6})
    cl = pf.cluster_euler_sources(pts, dx=500.0, dy=500.0)
    assert len(cl) == 2, f"应聚成 2 簇，实际 {len(cl)}"
    # 每点被打上 cluster_id
    assert all("cluster_id" in p for p in pts)
    for c in cl:
        assert 0.0 <= c["confidence"] <= 1.0
        assert c["depth_sigma_m"] > 0
        assert c["n_members"] == 5
    # 深度排序：浅簇在前
    assert cl[0]["depth_m"] < cl[1]["depth_m"]
    # 高置信组(0.8)簇置信度应高于低置信组(0.6)
    assert cl[0]["confidence"] > cl[1]["confidence"]


def test_clustering_degenerate():
    assert pf.cluster_euler_sources([], 500.0, 500.0) == []
    two = [{"x": 0, "y": 0, "depth_m": 1000, "confidence": 0.5},
           {"x": 9e9, "y": 9e9, "depth_m": 9000, "confidence": 0.5}]
    cl = pf.cluster_euler_sources(two, 500.0, 500.0)  # n<3 → 每点成簇
    assert len(cl) == 2
    assert all(c["n_members"] == 1 for c in cl)
    assert all(c["depth_sigma_m"] == 300.0 for c in cl)  # 单点簇保守默认 σ


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n✅ {len(fns)} 个单元测试全部通过")


if __name__ == "__main__":
    _run_all()
