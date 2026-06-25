"""P1 后处理管道 — conftest.py（合成 GeoTIFF 生成）"""
import pytest
import numpy as np
from pathlib import Path

try:
    import rasterio
    from rasterio.transform import from_origin
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False


@pytest.fixture
def has_rasterio():
    return HAS_RASTERIO


@pytest.fixture
def synthetic_tiff(tmp_path):
    """合成 3 波段 GeoTIFF (100x100, EPSG:4326)，模拟 Sentinel-2 裁剪结果"""
    if not HAS_RASTERIO:
        pytest.skip("rasterio not available")
    
    data = np.random.randint(0, 255, (3, 100, 100), dtype=np.uint8)
    transform = from_origin(-3.70, 12.56, 0.0001, 0.0001)
    
    path = str(tmp_path / "synthetic_s2.tif")
    with rasterio.open(
        path, 'w',
        driver='GTiff', height=100, width=100, count=3,
        dtype='uint8', crs='EPSG:4326', transform=transform
    ) as dst:
        for i in range(3):
            dst.write(data[i], i+1)
    return path


@pytest.fixture
def synthetic_tiff_no_crs(tmp_path):
    """无 CRS 的 GeoTIFF（用于错误处理测试）"""
    if not HAS_RASTERIO:
        pytest.skip("rasterio not available")
    
    data = np.zeros((1, 50, 50), dtype=np.uint8)
    path = str(tmp_path / "no_crs.tif")
    with rasterio.open(
        path, 'w',
        driver='GTiff', height=50, width=50, count=1,
        dtype='uint8', transform=from_origin(0, 0, 0.001, 0.001)
    ) as dst:
        dst.write(data[0], 1)
    return path


@pytest.fixture
def synthetic_tiff_utm(tmp_path):
    """UTM 投影 GeoTIFF (EPSG:32630)，用于重投影测试"""
    if not HAS_RASTERIO:
        pytest.skip("rasterio not available")
    
    data = np.random.randint(0, 255, (1, 80, 80), dtype=np.uint8)
    path = str(tmp_path / "utm.tif")
    with rasterio.open(
        path, 'w',
        driver='GTiff', height=80, width=80, count=1,
        dtype='uint8', crs='EPSG:32630', transform=from_origin(500000, 4640000, 10, 10)
    ) as dst:
        dst.write(data[0], 1)
    return path


@pytest.fixture
def multi_tiff_dir(tmp_path, synthetic_tiff):
    """包含多个 GeoTIFF 的目录（用于 mosaic 测试）"""
    import shutil
    base = Path(synthetic_tiff)
    for i in range(3):
        shutil.copy(synthetic_tiff, tmp_path / f"scene_{i}.tif")
    return str(tmp_path)
