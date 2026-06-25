"""ROI 解析器测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.roi_parser import parse_roi, expand_bbox


TEST_DIR = Path(__file__).parent


def test_parse_kml():
    roi = parse_roi(str(TEST_DIR / "test_roi.kml"))
    assert roi is not None
    assert "center" in roi
    assert "bbox" in roi
    assert roi["center"]["lon"] > 117 and roi["center"]["lon"] < 119
    assert roi["center"]["lat"] > 30 and roi["center"]["lat"] < 32
    assert roi["area_km2"] > 0


def test_parse_xlsx():
    roi = parse_roi(str(TEST_DIR / "test_roi.xlsx"))
    assert roi is not None
    assert "center" in roi
    assert roi["center"]["lon"] != 0
    assert roi["center"]["lat"] != 0


def test_expand_bbox():
    roi = parse_roi(str(TEST_DIR / "test_roi.kml"))
    original = roi["bbox"]
    expanded = expand_bbox(roi, 20)
    assert expanded["bbox"]["west"] <= original["west"]
    assert expanded["bbox"]["east"] >= original["east"]
    assert expanded["bbox"]["south"] <= original["south"]
    assert expanded["bbox"]["north"] >= original["north"]


def test_area_calculation():
    roi = parse_roi(str(TEST_DIR / "test_roi.kml"))
    # 铜陵矿区约 0.35° x 0.2° ≈ 700 km²
    assert 100 < roi["area_km2"] < 2000


def test_geometry_type():
    roi = parse_roi(str(TEST_DIR / "test_roi.kml"))
    assert roi["geometry"]["type"] == "Polygon"
