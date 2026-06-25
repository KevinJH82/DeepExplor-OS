"""
ROI 坐标文件四格式支持回归:.kml / .ovkml / .xlsx / .csv。

两套 ROI 解析器都必须支持(geo_utils.read_roi_robust 与 mineral_engine._read_roi_robust),
且白名单/前端 accept 覆盖四者。
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Python_Project" / "web_app"))

LON = [107.60, 107.80, 107.80, 107.60]
LAT = [36.40, 36.40, 36.50, 36.50]
KML = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><Polygon>'
       '<outerBoundaryIs><LinearRing><coordinates>'
       + ' '.join(f'{x},{y},0' for x, y in zip(LON, LAT)) +
       '</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>')


def _make_all(d):
    paths = {}
    for ext in ('.kml', '.ovkml'):
        p = os.path.join(d, 'roi' + ext)
        open(p, 'w', encoding='utf-8').write(KML)
        paths[ext] = p
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({'经度': LON, '纬度': LAT})
    df.to_csv(os.path.join(d, 'roi.csv'), index=False); paths['.csv'] = os.path.join(d, 'roi.csv')
    df.to_excel(os.path.join(d, 'roi.xlsx'), index=False); paths['.xlsx'] = os.path.join(d, 'roi.xlsx')
    return paths


def _check(roi):
    assert roi['lon_roi'].size >= 3
    assert abs(float(np.min(roi['lon_roi'])) - 107.6) < 1e-6
    assert abs(float(np.max(roi['lat_roi'])) - 36.5) < 1e-6


class TestRoiFourFormats:
    def test_geo_utils_parser_all_formats(self):
        from utils.geo_utils import read_roi_robust
        paths = _make_all(tempfile.mkdtemp())
        for ext, p in paths.items():
            _check(read_roi_robust(p))

    def test_engine_parser_all_formats(self):
        pytest.importorskip("pandas")
        from core.mineral_engine import MineralEngine
        eng = MineralEngine()
        paths = _make_all(tempfile.mkdtemp())
        for ext, p in paths.items():
            _check(eng._read_roi_robust(p))

    def test_whitelist_allows_all_formats(self):
        import app
        for ext in ('kml', 'ovkml', 'xlsx', 'csv'):
            assert app.allowed_file(f'roi.{ext}'), f'.{ext} 应在白名单'
            assert app.allowed_file(f'ROI.{ext.upper()}'), f'.{ext} 大写也应允许'
