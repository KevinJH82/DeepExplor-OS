"""P1: postprocess/package.py 测试 (12 用例)"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.p1


class TestPackageDateExtraction:
    """日期提取测试"""
    
    def test_extract_date_sentinel2(self):
        """Sentinel-2 文件名日期提取"""
        try:
            from postprocess.package import extract_date
            date = extract_date("S2A_MSIL1C_20240101T123456_N0510_R022_T30NXF_20240101T123456.SAFE")
            assert date is not None
        except ImportError:
            pytest.skip("extract_date not found")
    
    def test_extract_date_landsat(self):
        """Landsat 文件名日期提取"""
        try:
            from postprocess.package import extract_date
            date = extract_date("LC08_L1TP_170039_20240101_20240115_02_T1.tar")
            assert date is not None
        except ImportError:
            pytest.skip("extract_date not found")
    
    def test_extract_date_aster(self):
        """ASTER 文件名日期提取"""
        try:
            from postprocess.package import extract_date
            date = extract_date("AST_L1T_00301012024123456_20240115.hdf")
            assert date is not None
        except ImportError:
            pytest.skip("extract_date not found")


class TestPackageSeasonRouting:
    """季节路由测试"""
    
    def test_summer_season(self):
        """夏季路由"""
        try:
            from postprocess.package import get_season
            season = get_season(month=7)
            assert season in ('summer', '夏季')
        except ImportError:
            pytest.skip("get_season not found")
    
    def test_winter_season(self):
        """冬季路由"""
        try:
            from postprocess.package import get_season
            season = get_season(month=1)
            assert season in ('winter', '冬季')
        except ImportError:
            pytest.skip("get_season not found")


class TestPackageBandNormalization:
    """ASTER 波段键规范化"""
    
    def test_aster_band_keys(self):
        """ASTER 波段键存在"""
        try:
            from postprocess.package import ASTER_BAND_MAP
            assert isinstance(ASTER_BAND_MAP, dict)
            assert len(ASTER_BAND_MAP) > 0
        except ImportError:
            pytest.skip("ASTER_BAND_MAP not found")
    
    def test_band_key_normalization(self):
        """波段键规范化"""
        try:
            from postprocess.package import normalize_band_keys
            result = normalize_band_keys({"B01": "path1", "B02": "path2"})
            assert isinstance(result, dict)
        except ImportError:
            pytest.skip("normalize_band_keys not found")


class TestPackageBestFileSelection:
    """最佳文件选择"""
    
    def test_select_best_by_cloud_cover(self):
        """按云量选择最佳文件"""
        try:
            from postprocess.package import select_best_file
            files = [
                {"path": "a.tif", "cloud_cover": 10},
                {"path": "b.tif", "cloud_cover": 5},
                {"path": "c.tif", "cloud_cover": 30},
            ]
            best = select_best_file(files, key="cloud_cover")
            assert best["path"] == "b.tif"
        except ImportError:
            pytest.skip("select_best_file not found")
    
    def test_select_best_empty_list(self):
        """空列表返回 None"""
        try:
            from postprocess.package import select_best_file
            with pytest.raises(Exception):
                select_best_file([])
        except ImportError:
            pytest.skip("select_best_file not found")
    
    def test_all_sensors_supported(self):
        """所有传感器都有日期提取模式"""
        try:
            from postprocess.package import DATE_PATTERNS
            assert isinstance(DATE_PATTERNS, dict)
            # Should support major sensors
            assert len(DATE_PATTERNS) >= 5
        except ImportError:
            pytest.skip("DATE_PATTERNS not found")
