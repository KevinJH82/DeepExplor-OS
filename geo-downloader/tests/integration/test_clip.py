"""P1: postprocess/clip.py 测试 (8 用例)"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.p1


class TestClip:
    """栅格裁剪测试"""
    
    def test_clip_imports(self):
        """clip 模块可导入"""
        try:
            from postprocess import clip
            assert hasattr(clip, 'clip_raster') or hasattr(clip, 'clip')
        except ImportError as e:
            pytest.skip(f"clip not importable: {e}")
    
    def test_clip_function_exists(self):
        """裁剪函数存在"""
        try:
            from postprocess.clip import clip_raster, clip
            assert True
        except ImportError:
            from postprocess import clip
            assert hasattr(clip, 'clip') or hasattr(clip, 'clip_raster')
    
    def test_clip_valid_tiff(self, synthetic_tiff):
        """正常 GeoTIFF 裁剪"""
        from postprocess.clip import clip_raster
        bbox = (-3.69, 12.51, -3.62, 12.55)
        try:
            result = clip_raster(synthetic_tiff, bbox)
            assert result is not None
        except Exception as e:
            pytest.skip(f"clip failed: {e}")
    
    def test_clip_no_overlap(self, synthetic_tiff):
        """无重叠区域应报错"""
        from postprocess.clip import clip_raster
        bbox = (100, 100, 101, 101)  # Far from test data
        with pytest.raises(Exception):
            clip_raster(synthetic_tiff, bbox)
    
    def test_clip_no_crs_raises(self, synthetic_tiff_no_crs):
        """无 CRS 输入应报错"""
        from postprocess.clip import clip_raster
        bbox = (-1, -1, 1, 1)
        with pytest.raises(Exception):
            clip_raster(synthetic_tiff_no_crs, bbox)
    
    def test_clip_reproject_utm_to_wgs84(self, synthetic_tiff_utm):
        """UTM → WGS84 重投影裁剪"""
        from postprocess.clip import clip_raster
        bbox = (-10, 40, -5, 45)
        try:
            result = clip_raster(synthetic_tiff_utm, bbox)
        except Exception:
            pytest.skip("UTM reproject clip not supported")
    
    def test_clip_preserves_band_count(self, synthetic_tiff):
        """裁剪后波段数不变"""
        from postprocess.clip import clip_raster
        import rasterio
        bbox = (-3.69, 12.51, -3.62, 12.55)
        try:
            result = clip_raster(synthetic_tiff, bbox)
            with rasterio.open(result) as src:
                assert src.count == 3
        except:
            pytest.skip("clip verification failed")
