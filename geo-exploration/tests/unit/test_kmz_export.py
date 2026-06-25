"""
KMZ 导出(无 simplekml 依赖)回归测试。

export_kmz_from_mat 改为手写 KML + zip,任何环境可出 KMZ。
覆盖:合法 zip(doc.kml + 叠加 PNG)、合法 KML XML、GroundOverlay/ROI/Top靶点齐全、
以及 numpy 2.x 下标量提取(此前 float(1元素数组) 会崩)。
"""
import os
import sys
import zipfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Python_Project" / "web_app"))

KNS = '{http://www.opengis.net/kml/2.2}'


class TestKmzExport:
    def setup_method(self):
        pytest.importorskip("scipy")
        pytest.importorskip("matplotlib")

    def _make_mat(self, d):
        from scipy.io import savemat
        H, W = 24, 28
        lonG, latG = np.meshgrid(np.linspace(107.6, 107.8, W), np.linspace(36.4, 36.5, H))
        mat = {
            'lonGrid': lonG, 'latGrid': latG,
            'Au_deep': np.clip(np.random.RandomState(0).rand(H, W), 0, 1),
            'mineral_type': 'petroleum', 'kmz_threshold': 0.6,
            'lonROI': np.array([107.6, 107.8, 107.8, 107.6]),
            'latROI': np.array([36.4, 36.4, 36.5, 36.5]),
            'lonTop': np.linspace(107.62, 107.78, 20),
            'latTop': np.linspace(36.41, 36.49, 20),
            'redIdx': np.arange(1, 21),
        }
        mf = os.path.join(d, 'petroleum_Result.mat')
        savemat(mf, mat)
        return mf

    def test_kmz_is_valid_and_dependency_free(self):
        import matplotlib
        matplotlib.use('Agg')
        from utils.geo_utils import export_kmz_from_mat

        d = tempfile.mkdtemp()
        export_kmz_from_mat(self._make_mat(d), d)

        kmz = [f for f in os.listdir(d) if f.endswith('.kmz')]
        assert kmz, "应产出 .kmz"
        with zipfile.ZipFile(os.path.join(d, kmz[0])) as z:
            names = z.namelist()
            assert 'doc.kml' in names
            assert any(n.endswith('.png') for n in names)
            root = ET.fromstring(z.read('doc.kml').decode('utf-8'))   # 合法 XML
        assert root.tag == KNS + 'kml'
        assert next(root.iter(KNS + 'GroundOverlay'), None) is not None
        # ROI 边界 + 20 个靶点 = 至少 21 个 Placemark
        assert len(list(root.iter(KNS + 'Placemark'))) >= 21
        box = root.find(f'.//{KNS}LatLonBox')
        assert abs(float(box.find(KNS + 'north').text) - 36.5) < 1e-6

    def test_scalar_extraction_numpy2(self):
        """loadmat 把标量存成数组;numpy 2.x 下 float(1元素数组) 会崩 —— 必须 ravel。"""
        import matplotlib
        matplotlib.use('Agg')
        from utils.geo_utils import export_kmz_from_mat
        d = tempfile.mkdtemp()
        # 不抛 TypeError 即通过
        export_kmz_from_mat(self._make_mat(d), d)
