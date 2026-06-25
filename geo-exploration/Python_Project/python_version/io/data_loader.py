"""
数据加载器

支持多种遥感数据格式的加载与预处理
"""

import os
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
import pandas as pd
from loguru import logger

from config.config import Config
from utils.geo_utils import GeoUtils


class DataLoader:
    """
    数据加载器类

    支持加载 Sentinel-2, Landsat-8, ASTER, DEM 等遥感数据
    以及 ROI 文件和 KML/KMZ 文件
    """

    def __init__(self, data_dir: str):
        """
        初始化数据加载器

        Args:
            data_dir: 数据目录路径
        """
        self.data_dir = Path(data_dir)
        self.data_cache = {}
        self.metadata = {}

    def load_sentinel2(self, file_pattern: str = None,
                      bands: List[int] = None) -> Optional[np.ndarray]:
        """
        加载 Sentinel-2 数据

        Args:
            file_pattern: 文件匹配模式（可选）
            bands: 要加载的波段列表（可选）

        Returns:
            Sentinel-2 数据数组 [bands, height, width]
        """
        if file_pattern is None:
            # 默认搜索模式
            patterns = ['S2*.tif', 'Sentinel2*.tif', '*S2*.tif']
            files = []
            for pattern in patterns:
                files.extend(list(self.data_dir.glob(pattern)))
            if not files:
                logger.warning("未找到 Sentinel-2 数据文件")
                return None
            file_path = files[0]
        else:
            file_path = self.data_dir / file_pattern
            if not file_path.exists():
                logger.warning(f"Sentinel-2 文件不存在: {file_path}")
                return None

        try:
            with rasterio.open(file_path) as src:
                if bands is None:
                    data = src.read()
                else:
                    data = src.read(bands)

                self.metadata['sentinel2'] = {
                    'file': str(file_path),
                    'shape': data.shape,
                    'crs': str(src.crs),
                    'transform': src.transform,
                    'bounds': src.bounds
                }

                logger.info(f"Sentinel-2 数据加载完成: {data.shape}")
                return data

        except Exception as e:
            logger.error(f"加载 Sentinel-2 数据失败: {str(e)}")
            return None

    def load_landsat8(self, file_pattern: str = None,
                      bands: List[int] = None) -> Optional[np.ndarray]:
        """
        加载 Landsat-8 数据

        Args:
            file_pattern: 文件匹配模式（可选）
            bands: 要加载的波段列表（可选）

        Returns:
            Landsat-8 数据数组 [bands, height, width]
        """
        if file_pattern is None:
            patterns = ['L8*.tif', 'Landsat8*.tif', '*LC08*.tif']
            files = []
            for pattern in patterns:
                files.extend(list(self.data_dir.glob(pattern)))
            if not files:
                logger.warning("未找到 Landsat-8 数据文件")
                return None
            file_path = files[0]
        else:
            file_path = self.data_dir / file_pattern
            if not file_path.exists():
                logger.warning(f"Landsat-8 文件不存在: {file_path}")
                return None

        try:
            with rasterio.open(file_path) as src:
                if bands is None:
                    data = src.read()
                else:
                    data = src.read(bands)

                self.metadata['landsat8'] = {
                    'file': str(file_path),
                    'shape': data.shape,
                    'crs': str(src.crs),
                    'transform': src.transform,
                    'bounds': src.bounds
                }

                logger.info(f"Landsat-8 数据加载完成: {data.shape}")
                return data

        except Exception as e:
            logger.error(f"加载 Landsat-8 数据失败: {str(e)}")
            return None

    def load_aster(self, file_pattern: str = None,
                   bands: List[int] = None) -> Optional[np.ndarray]:
        """
        加载 ASTER 数据

        Args:
            file_pattern: 文件匹配模式（可选）
            bands: 要加载的波段列表（可选）

        Returns:
            ASTER 数据数组 [bands, height, width]
        """
        if file_pattern is None:
            patterns = ['AST*.tif', 'ASTER*.tif', '*AST_L1T*.tif']
            files = []
            for pattern in patterns:
                files.extend(list(self.data_dir.glob(pattern)))
            if not files:
                logger.warning("未找到 ASTER 数据文件")
                return None
            file_path = files[0]
        else:
            file_path = self.data_dir / file_pattern
            if not file_path.exists():
                logger.warning(f"ASTER 文件不存在: {file_path}")
                return None

        try:
            with rasterio.open(file_path) as src:
                if bands is None:
                    data = src.read()
                else:
                    data = src.read(bands)

                self.metadata['aster'] = {
                    'file': str(file_path),
                    'shape': data.shape,
                    'crs': str(src.crs),
                    'transform': src.transform,
                    'bounds': src.bounds
                }

                logger.info(f"ASTER 数据加载完成: {data.shape}")
                return data

        except Exception as e:
            logger.error(f"加载 ASTER 数据失败: {str(e)}")
            return None

    def load_dem(self, file_pattern: str = None) -> Optional[np.ndarray]:
        """
        加载 DEM 数据

        Args:
            file_pattern: 文件匹配模式（可选）

        Returns:
            DEM 数据数组 [height, width]
        """
        if file_pattern is None:
            patterns = ['DEM*.tif', 'dem*.tif', '*DEM*.tif', '*SRTM*.tif']
            files = []
            for pattern in patterns:
                files.extend(list(self.data_dir.glob(pattern)))
            if not files:
                logger.warning("未找到 DEM 数据文件")
                return None
            file_path = files[0]
        else:
            file_path = self.data_dir / file_pattern
            if not file_path.exists():
                logger.warning(f"DEM 文件不存在: {file_path}")
                return None

        try:
            with rasterio.open(file_path) as src:
                data = src.read(1)  # 读取第一个波段

                self.metadata['dem'] = {
                    'file': str(file_path),
                    'shape': data.shape,
                    'crs': str(src.crs),
                    'transform': src.transform,
                    'bounds': src.bounds
                }

                logger.info(f"DEM 数据加载完成: {data.shape}")
                return data

        except Exception as e:
            logger.error(f"加载 DEM 数据失败: {str(e)}")
            return None

    def load_roi(self, roi_file: str = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        加载 ROI 数据

        Args:
            roi_file: ROI 文件路径（可选，默认在数据目录中查找）

        Returns:
            (lonROI, latROI): 经纬度数组
        """
        if roi_file is None:
            # 在数据目录中查找 ROI 文件
            patterns = ['ROI.xlsx', 'ROI.csv', 'roi.xlsx', 'roi.csv',
                       '*ROI*.xlsx', '*ROI*.csv']
            files = []
            for pattern in patterns:
                files.extend(list(self.data_dir.glob(pattern)))
            if not files:
                raise FileNotFoundError("未找到 ROI 文件")
            roi_file = files[0]
        else:
            roi_file = Path(roi_file)
            if not roi_file.exists():
                raise FileNotFoundError(f"ROI 文件不存在: {roi_file}")

        try:
            if roi_file.suffix in ['.xlsx', '.xls']:
                df = pd.read_excel(roi_file)
            else:
                df = pd.read_csv(roi_file)

            # 智能识别经纬度列
            lon_col, lat_col = self._identify_coordinate_columns(df)

            if lon_col is None or lat_col is None:
                raise ValueError("无法识别经纬度列")

            lonROI = df[lon_col].values
            latROI = df[lat_col].values

            self.metadata['roi'] = {
                'file': str(roi_file),
                'points': len(lonROI),
                'lon_col': lon_col,
                'lat_col': lat_col
            }

            logger.info(f"ROI 数据加载完成: {len(lonROI)} 个点")
            return lonROI, latROI

        except Exception as e:
            logger.error(f"加载 ROI 数据失败: {str(e)}")
            raise

    def load_kml(self, kml_file: str) -> Dict[str, Any]:
        """
        加载 KML 文件

        Args:
            kml_file: KML 文件路径

        Returns:
            KML 数据字典
        """
        import xml.etree.ElementTree as ET
        import zipfile

        kml_path = Path(kml_file)

        # 如果是 KMZ 文件，先解压
        if kml_path.suffix.lower() == '.kmz':
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                with zipfile.ZipFile(kml_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                # 查找 KML 文件
                kml_files = list(Path(temp_dir).glob('**/*.kml'))
                if not kml_files:
                    raise ValueError("KMZ 文件中未找到 KML 文件")

                return self._parse_kml_file(kml_files[0])

        else:
            return self._parse_kml_file(kml_path)

    def _parse_kml_file(self, kml_file: Path) -> Dict[str, Any]:
        """解析 KML 文件"""
        import xml.etree.ElementTree as ET

        with open(kml_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 去除命名空间
        content = content.replace('kml:', '').replace('gx:', '')

        root = ET.fromstring(content)

        coordinates = []
        names = []
        descriptions = []

        # 查找所有 Placemark 元素
        for placemark in root.findall('.//Placemark'):
            name_elem = placemark.find('name')
            name = name_elem.text if name_elem is not None else ''

            desc_elem = placemark.find('description')
            description = desc_elem.text if desc_elem is not None else ''

            coords_elem = placemark.find('Point/coordinates')
            if coords_elem is not None:
                coords_text = coords_elem.text.strip()
                try:
                    lon, lat, _ = map(float, coords_text.split(','))
                    coordinates.append((lon, lat))
                    names.append(name)
                    descriptions.append(description)
                except ValueError:
                    continue

        self.metadata['kml'] = {
            'file': str(kml_file),
            'points': len(coordinates)
        }

        logger.info(f"KML 数据加载完成: {len(coordinates)} 个点")

        return {
            'points': coordinates,
            'names': names,
            'descriptions': descriptions
        }

    def _identify_coordinate_columns(self, df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
        """识别经纬度列"""
        candidates_lon = []
        candidates_lat = []

        for col in df.columns:
            col_lower = str(col).lower()

            # 检查列名中是否包含关键词
            if any(keyword in col_lower for keyword in ['lon', 'lng', 'longitude', '经度']):
                candidates_lon.append(col)
            elif any(keyword in col_lower for keyword in ['lat', 'latitude', '纬度']):
                candidates_lat.append(col)
            else:
                # 尝试判断是否为数值数据
                try:
                    values = pd.to_numeric(df[col], errors='coerce')
                    if values.notna().sum() > len(df) * 0.8:
                        mean_val = values.mean()
                        min_val = values.min()
                        max_val = values.max()

                        # 判断是经度还是纬度
                        if 60 < mean_val < 160 and (max_val - min_val) < 20:
                            candidates_lon.append(col)
                        elif 0 < mean_val < 60 and (max_val - min_val) < 20:
                            candidates_lat.append(col)
                except:
                    pass

        if candidates_lon and candidates_lat:
            return candidates_lon[0], candidates_lat[0]

        return None, None

    def get_metadata(self) -> Dict[str, Any]:
        """获取所有元数据"""
        return self.metadata.copy()

    def clear_cache(self):
        """清除缓存"""
        self.data_cache.clear()
        self.metadata.clear()
        logger.info("数据缓存已清除")
