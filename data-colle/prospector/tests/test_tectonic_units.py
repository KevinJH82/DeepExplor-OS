"""构造单元定位测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tectonic_units import find_tectonic_unit, find_petroleum_basin, analyze_roi_location
from src.roi_parser import parse_roi, expand_bbox


TEST_DIR = Path(__file__).parent

# 已知测试点: (lon, lat) → 期望构造单元关键词
TECTONIC_TEST_POINTS = [
    (117.9, 31.0, "扬子"),     # 铜陵 — 扬子克拉通
    (116.4, 39.9, "华北"),     # 北京 — 华北克拉通
    (104.0, 30.7, "扬子"),     # 成都 — 扬子克拉通西缘
    (87.6, 43.8, "天山"),      # 乌鲁木齐 — 天山-兴蒙
]

# 含油气盆地测试点
BASIN_TEST_POINTS = [
    (121.0, 46.5, "松辽"),
    (117.0, 38.5, "渤海湾"),
    (109.0, 37.5, "鄂尔多斯"),
    (104.5, 30.5, "四川"),
]


def test_find_tectonic_known_points():
    for lon, lat, expected in TECTONIC_TEST_POINTS:
        tu = find_tectonic_unit(lon, lat)
        assert tu is not None, f"({lon}, {lat}) 未识别到构造单元"
        assert expected in tu["name"], f"({lon}, {lat}) 期望包含'{expected}'，实际'{tu['name']}'"


def test_find_basin_known_points():
    for lon, lat, expected in BASIN_TEST_POINTS:
        pb = find_petroleum_basin(lon, lat)
        assert pb is not None, f"({lon}, {lat}) 未识别到含油气盆地"
        assert expected in pb["name"], f"({lon}, {lat}) 期望包含'{expected}'，实际'{pb['name']}'"


def test_basin_fields():
    pb = find_petroleum_basin(121.0, 46.5)
    assert pb is not None
    assert "area_km2" in pb
    assert "main_plays" in pb
    assert "max_well_depth" in pb
    assert pb["area_km2"] > 0


def test_tectonic_fields():
    tu = find_tectonic_unit(117.9, 31.0)
    assert tu is not None
    assert "name" in tu
    assert "name_en" in tu
    assert "features" in tu
    assert "major_minerals" in tu
    assert len(tu["major_minerals"]) > 0


def test_analyze_roi_location():
    roi = parse_roi(str(TEST_DIR / "test_roi.kml"))
    roi = expand_bbox(roi, 20)
    location = analyze_roi_location(roi)
    assert "center_tectonic" in location
    assert location["center_tectonic"] is not None


def test_ocean_returns_none():
    tu = find_tectonic_unit(0, 0)
    assert tu is None
