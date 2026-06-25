"""
geo-stru 构造解译接入(D 部分)回归测试。

覆盖:
- slow_vars fault_activity 注入:构造非 None → 改变;None → 零回归;权重单调。
- _load_structural:招远(1 条断裂)CRS 重投影通过;本溪(0 条)稀疏门控;legacy zoom 路径。
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Python_Project" / "web_app"))

GS_ROOT = "/opt/deepexplor-services/geo-stru/results"


def _ctx(H=30, W=30):
    R = np.random.RandomState
    return {
        'inROI': np.ones((H, W), bool),
        'dem': (np.cumsum(np.ones((H, W)), 0) + R(0).rand(H, W)).astype(np.float64),
        'lan': (R(1).rand(H, W, 7) + 0.5).astype(np.float64),
        's2': (R(5).rand(H, W, 10) + 0.5).astype(np.float64),
        'ast': (R(2).rand(H, W, 14) + 0.5).astype(np.float64),
        'NIR': (R(3).rand(H, W) + 0.5).astype(np.float64),
        'Red': (R(4).rand(H, W) + 0.5).astype(np.float64),
    }


class TestFaultActivityInjection:
    def test_injection_changes_fault_activity(self):
        from core.detectors.slow_vars_detector import SlowVarsDetector
        det = SlowVarsDetector()
        ctx = _ctx()
        fa_off = det.calculate(dict(ctx)).debug_data['fault_activity'].copy()
        lin = np.zeros((30, 30)); lin[:, 10:14] = 1.0
        fa_on = det.calculate({**ctx, 'fault_lineament': lin,
                               'lineament_weight': 0.5}).debug_data['fault_activity']
        assert np.nanmax(np.abs(fa_on - fa_off)) > 0

    def test_none_is_zero_regression(self):
        from core.detectors.slow_vars_detector import SlowVarsDetector
        det = SlowVarsDetector()
        ctx = _ctx()
        fa_off = det.calculate(dict(ctx)).debug_data['fault_activity'].copy()
        fa_none = det.calculate({**ctx, 'fault_lineament': None}).debug_data['fault_activity']
        assert np.allclose(np.nan_to_num(fa_none), np.nan_to_num(fa_off))

    def test_weight_monotonic(self):
        from core.detectors.slow_vars_detector import SlowVarsDetector
        det = SlowVarsDetector()
        ctx = _ctx()
        fa_off = det.calculate(dict(ctx)).debug_data['fault_activity'].copy()
        lin = np.zeros((30, 30)); lin[:, 10:14] = 1.0
        d_lo = np.nanmax(np.abs(det.calculate({**ctx, 'fault_lineament': lin,
                        'lineament_weight': 0.5}).debug_data['fault_activity'] - fa_off))
        d_hi = np.nanmax(np.abs(det.calculate({**ctx, 'fault_lineament': lin,
                        'lineament_weight': 1.0}).debug_data['fault_activity'] - fa_off))
        assert d_hi >= d_lo - 1e-9


@pytest.mark.skipif(not os.path.isdir(GS_ROOT), reason="geo-stru results 不可用")
class TestLoadStructural:
    def _engine(self):
        pytest.importorskip("rasterio")
        from core.mineral_engine import MineralEngine
        return MineralEngine()

    def test_zhaoyuan_reproject_passes_gate(self):
        import rasterio
        eng = self._engine()
        zy = os.path.join(GS_ROOT, "山东招远庙山金矿_构造解译")
        if not os.path.isdir(zy):
            pytest.skip("招远 run 不存在")
        with rasterio.open(os.path.join(zy, "structural", "distance_to_lineament.tif")) as s:
            b = s.bounds
        lonG, latG = np.meshgrid(np.linspace(b.left, b.right, 52),
                                 np.linspace(b.bottom, b.top, 40))
        sc, den, meta = eng._load_structural(zy, (40, 52), lonGrid=lonG, latGrid=latG,
                                             scfg={'enabled': True, 'min_lineaments': 1})
        assert sc is not None and meta.get('n_lineaments') == 1
        assert np.isfinite(sc).any()

    def test_benxi_sparse_gated(self):
        eng = self._engine()
        bx = os.path.join(GS_ROOT, "辽宁本溪市铜钼矿")
        if not os.path.isdir(bx):
            pytest.skip("本溪 run 不存在")
        lonG, latG = np.meshgrid(np.linspace(125.43, 125.45, 52),
                                 np.linspace(41.15, 41.17, 40))
        sc, den, meta = eng._load_structural(bx, (40, 52), lonGrid=lonG, latGrid=latG,
                                             scfg={'enabled': True, 'min_lineaments': 1})
        assert sc is None and 'skip_reason' in (meta or {})

    def test_legacy_zoom_when_disabled(self):
        eng = self._engine()
        zy = os.path.join(GS_ROOT, "山东招远庙山金矿_构造解译")
        if not os.path.isdir(zy):
            pytest.skip("招远 run 不存在")
        sc, den, meta = eng._load_structural(zy, (40, 52), scfg={'enabled': False})
        assert sc is not None  # 现状行为:仍加载(legacy zoom)


@pytest.mark.skipif(not os.path.isdir(GS_ROOT), reason="geo-stru results 不可用")
class TestStructuralAutoDiscover:
    """跨系统自动匹配:从 geo-stru/results 按 ROI 找构造 run。"""

    def _engine(self):
        pytest.importorskip("rasterio")
        pytest.importorskip("shapely")
        from core.mineral_engine import MineralEngine
        return MineralEngine()

    def test_match_by_roi_overlap(self):
        import json
        eng = self._engine()
        zy = os.path.join(GS_ROOT, "山东招远庙山金矿_构造解译", "structural", "metadata.json")
        if not os.path.exists(zy):
            pytest.skip("招远 metadata 不存在")
        x0, y0, x1, y1 = json.load(open(zy, encoding="utf-8"))['aoi_bbox']
        poly = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        gdir, gov = eng._find_structural_dir(GS_ROOT, poly, 0.15)
        assert gdir is not None and '招远' in gdir and gov > 0.9

    def test_no_match_for_unrelated_roi(self):
        eng = self._engine()
        far = np.array([[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01]])
        gdir, gov = eng._find_structural_dir(GS_ROOT, far, 0.15)
        assert gdir is None

    def test_load_via_extra_candidates(self):
        import json, tempfile
        eng = self._engine()
        zy = os.path.join(GS_ROOT, "山东招远庙山金矿_构造解译", "structural", "metadata.json")
        if not os.path.exists(zy):
            pytest.skip("招远 metadata 不存在")
        x0, y0, x1, y1 = json.load(open(zy, encoding="utf-8"))['aoi_bbox']
        poly = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        gdir, _ = eng._find_structural_dir(GS_ROOT, poly, 0.15)
        lonG, latG = np.meshgrid(np.linspace(x0, x1, 52), np.linspace(y0, y1, 40))
        sc, den, meta = eng._load_structural(tempfile.mkdtemp(), (40, 52),
                                             lonGrid=lonG, latGrid=latG,
                                             scfg={'enabled': True, 'min_lineaments': 1},
                                             extra_candidates=[gdir])
        assert sc is not None and meta.get('n_lineaments') == 1
