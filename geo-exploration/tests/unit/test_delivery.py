"""
交付库 ROI 自动取数(delivery)回归测试。

覆盖:
- resolve_delivery_data_dir:按 ROI 空间重叠匹配项目 + 定位冬季季节目录;无关 ROI 拒绝。
- _find_season_dir:季节选择。
- mineral_engine._read_roi_robust 的 .ovkml 分支(交付 ROI 即 .ovkml)。
依赖真实交付库,缺失则 skip。
"""
import os
import sys
import glob
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Python_Project" / "web_app"))

from utils.delivery import DEFAULT_DELIVERY_ROOT  # noqa: E402

_HAS_DELIVERY = os.path.isdir(DEFAULT_DELIVERY_ROOT)


@pytest.mark.skipif(not _HAS_DELIVERY, reason="交付库不可访问")
class TestDeliveryResolve:
    def setup_method(self):
        pytest.importorskip("rasterio")
        pytest.importorskip("shapely")

    def _user_roi_from(self, keyword):
        from utils.geo_utils import _parse_kml_coordinates
        projs = [d for d in glob.glob(DEFAULT_DELIVERY_ROOT + '/*') if keyword in os.path.basename(d)]
        if not projs:
            pytest.skip(f"交付库无 {keyword} 项目")
        ovkml = glob.glob(projs[0] + '/*.ovkml') + glob.glob(projs[0] + '/*.kml')
        if not ovkml:
            pytest.skip(f"{keyword} 无 .ovkml")
        lon, lat = _parse_kml_coordinates(ovkml[0])
        return np.column_stack([lon, lat])

    def test_match_and_winter_season(self):
        from utils.delivery import resolve_delivery_data_dir
        poly = self._user_roi_from('庆阳')
        ddir, info = resolve_delivery_data_dir(poly, {'season': 'winter', 'min_roi_overlap': 0.10})
        assert ddir and os.path.isdir(ddir)
        assert info['overlap'] > 0.9 and '冬' in info['season']
        # 季节目录含 geo-exploration 读取器能识别的传感器子目录
        subs = os.listdir(ddir)
        assert any('ASTER' in s for s in subs)
        assert glob.glob(os.path.join(ddir, 'Sentinel*2 L2*'))   # read_sentinel2 glob 兼容

    def test_unrelated_roi_no_match(self):
        from utils.delivery import resolve_delivery_data_dir
        far = np.array([[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01]])
        ddir, info = resolve_delivery_data_dir(far, {'min_roi_overlap': 0.10})
        assert ddir is None


@pytest.mark.skipif(not _HAS_DELIVERY, reason="交付库不可访问")
class TestOvkmlRoiInEngine:
    """mineral_engine._read_roi_robust 必须能读交付库的 .ovkml(此前会误判为 CSV)。"""

    def test_engine_reads_ovkml(self):
        pytest.importorskip("pandas")
        from core.mineral_engine import MineralEngine
        projs = [d for d in glob.glob(DEFAULT_DELIVERY_ROOT + '/*') if '庆阳' in os.path.basename(d)]
        if not projs:
            pytest.skip("无庆阳项目")
        ovkml = glob.glob(projs[0] + '/*.ovkml')
        if not ovkml:
            pytest.skip("无 .ovkml")
        roi = MineralEngine()._read_roi_robust(ovkml[0])
        assert roi['lon_roi'].size >= 3 and roi['roi_poly'].shape[1] == 2
