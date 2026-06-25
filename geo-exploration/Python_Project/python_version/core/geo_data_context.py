"""
地理数据上下文模块

统一管理所有输入数据，包括遥感数据、ROI 数据、DEM 数据等
"""

import os
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple, List, Union
from pathlib import Path
import rasterio
from rasterio.transform import from_bounds
import geopandas as gpd
from shapely.geometry import Point, Polygon, box
import logging
from loguru import logger
import warnings
warnings.filterwarnings('ignore')

from .base_classes import BaseProcessor
from config.config import Config


class GeoDataContext:
    """
    地理数据上下文类

    负责统一管理所有输入数据，包括：
    - 遥感数据（Sentinel-2, Landsat-8, ASTER）
    - ROI 数据（Excel 坐标）
    - DEM 数据
    - KML/KMZ 已知异常数据
    """

    def __init__(self, data_dir: str, roi_file: str, mineral_type: str = 'gold'):
        """
        初始化地理数据上下文

        Args:
            data_dir: 数据目录路径
            roi_file: ROI 文件路径
            mineral_type: 目标矿种
        """
        self.data_dir = Path(data_dir)
        self.roi_file = Path(roi_file)
        self.mineral_type = mineral_type
        self.config = Config.get_mineral_config(mineral_type)

        # 核心数据
        self.s2_data = None      # Sentinel-2 数据
        self.l8_data = None       # Landsat-8 数据
        self.ast_data = None      # ASTER 数据
        self.dem_data = None      # DEM 数据
        self.kml_data = None      # KML 数据（可选）

        # 地理信息
        self.roi_polygon = None   # ROI 多边形
        self.roi_points = None    # ROI 点
        self.inROI = None        # ROI 掩码
        self.lonGrid = None       # 经度网格
        self.latGrid = None       # 纬度网格
        self.coord_transformer = None

        # 元数据
        self.metadata = {}
        self._setup_logging()

    def _setup_logging(self):
        """设置日志"""
        logger.add(
            Config.LOGS_DIR / "geo_data_context.log",
            level=Config.LOGGING['level'],
            format=Config.LOGGING['format'],
            rotation=Config.LOGGING['rotation'],
            retention=Config.LOGGING['retention']
        )

    def load_all_data(self):
        """加载所有输入数据"""
        logger.info("开始加载数据...")

        # 1. 加载遥感数据
        self._load_remote_sensing_data()

        # 2. 加载 ROI 数据
        self._load_roi_data()

        # 3. 加载 DEM 数据
        self._load_dem_data()

        # 4. 计算地理网格
        self._calculate_grids()

        # 5. 生成 ROI 掩码
        self._generate_roi_mask()

        logger.info("数据加载完成")

    def _load_remote_sensing_data(self):
        """加载遥感数据"""
        logger.info("加载遥感数据...")

        # 加载 Sentinel-2 数据
        s2_path = self.data_dir / "s2_data.tif"
        if s2_path.exists():
            self.s2_data = self._load_raster_file(str(s2_path))
            self.metadata['s2_data'] = {
                'path': str(s2_path),
                'shape': self.s2_data.shape,
                'dtype': str(self.s2_data.dtype)
            }
            logger.info(f"Sentinel-2 数据加载完成: {self.s2_data.shape}")

        # 加载 Landsat-8 数据
        l8_path = self.data_dir / "l8_data.tif"
        if l8_path.exists():
            self.l8_data = self._load_raster_file(str(l8_path))
            self.metadata['l8_data'] = {
                'path': str(l8_path),
                'shape': self.l8_data.shape,
                'dtype': str(self.l8_data.dtype)
            }
            logger.info(f"Landsat-8 数据加载完成: {self.l8_data.shape}")

        # 加载 ASTER 数据
        ast_path = self.data_dir / "aster_data.tif"
        if ast_path.exists():
            self.ast_data = self._load_raster_file(str(ast_path))
            self.metadata['ast_data'] = {
                'path': str(ast_path),
                'shape': self.ast_data.shape,
                'dtype': str(self.ast_data.dtype)
            }
            logger.info(f"ASTER 数据加载完成: {self.ast_data.shape}")

    def _load_roi_data(self):
        """加载 ROI 数据"""
        logger.info("加载 ROI 数据...")

        if not self.roi_file.exists():
            raise FileNotFoundError(f"ROI 文件不存在: {self.roi_file}")

        # 读取 Excel 文件
        try:
            roi_df = pd.read_excel(self.roi_file)
        except Exception as e:
            # 如果不是 Excel，尝试读取 CSV
            try:
                roi_df = pd.read_csv(self.roi_file)
            except Exception as e2:
                raise ValueError(f"无法读取 ROI 文件: {e2}")

        # 智能识别经纬度列
        lon_col, lat_col = self._identify_coordinate_columns(roi_df)
        roi_df['经度'] = roi_df[lon_col]
        roi_df['纬度'] = roi_df[lat_col]

        # 保存 ROI 点
        self.roi_points = roi_df[['经度', '纬度']].values

        # 创建 ROI 多边形
        if len(self.roi_points) >= 3:
            self.roi_polygon = Polygon(self.roi_points)
        else:
            # 如果点数不足，创建缓冲区
            center = np.mean(self.roi_points, axis=0)
            self.roi_polygon = Point(center).buffer(0.01)

        self.metadata['roi_data'] = {
            'file': str(self.roi_file),
            'point_count': len(self.roi_points),
            'bounds': self.roi_polygon.bounds
        }

        logger.info(f"ROI 数据加载完成: {len(self.roi_points)} 个点")

    def _identify_coordinate_columns(self, df: pd.DataFrame) -> Tuple[str, str]:
        """
        智能识别经纬度列

        Args:
            df: 数据框

        Returns:
            (经度列名, 纬度列名)
        """
        candidates_lon = []
        candidates_lat = []

        for col in df.columns:
            # 尝试转换为数值
            try:
                values = pd.to_numeric(df[col], errors='coerce')
                if values.notna().sum() > len(df) * 0.8:  # 80%以上是数值
                    mean_val = values.mean()
                    min_val = values.min()
                    max_val = values.max()

                    # 判断是经度还是纬度
                    if 60 < mean_val < 160 and (max_val - min_val) < 20:
                        candidates_lon.append(col)
                    elif 0 < mean_val < 60 and (max_val - min_val) < 20:
                        candidates_lat.append(col)
            except:
                continue

        if not candidates_lon or not candidates_lat:
            raise ValueError("无法识别经纬度列，请检查数据格式")

        # 选择最合适的列
        lon_col = candidates_lon[0]
        lat_col = candidates_lat[0]

        return lon_col, lat_col

    def _load_dem_data(self):
        """加载 DEM 数据"""
        logger.info("加载 DEM 数据...")

        dem_path = self.data_dir / "dem_data.tif"
        if dem_path.exists():
            self.dem_data = self._load_raster_file(str(dem_path))
            self.metadata['dem_data'] = {
                'path': str(dem_path),
                'shape': self.dem_data.shape,
                'dtype': str(self.dem_data.dtype)
            }
            logger.info(f"DEM 数据加载完成: {self.dem_data.shape}")

    def _calculate_grids(self):
        """计算地理网格"""
        logger.info("计算地理网格...")

        # 使用 ASTER 数据的边界作为基准
        if hasattr(self, '_get_raster_bounds'):
            bounds = self._get_raster_bounds(self.ast_data if self.ast_data is not None else self.s2_data)
        else:
            # 如果没有数据，使用 ROI 的边界
            bounds = self.roi_polygon.bounds

        # 创建网格
        lat_min, lon_min, lat_max, lon_max = bounds

        # 确定网格分辨率（基于 ASTER 的 15m 分辨率）
        if self.ast_data is not None:
            lat_step = -15 / 111000  # 转换为度
            lon_step = 15 / (111000 * np.cos(np.radians((lat_min + lat_max) / 2)))
        else:
            # 默认分辨率
            lat_step = -0.0001
            lon_step = 0.0001

        self.latGrid = np.arange(lat_max, lat_min, lat_step)
        self.lonGrid = np.arange(lon_min, lon_max, lon_step)

        # 确保网格尺寸与数据匹配
        if self.ast_data is not None:
            self.latGrid = self.latGrid[:self.ast_data.shape[0]]
            self.lonGrid = self.lonGrid[:self.ast_data.shape[1]]

        self.metadata['grids'] = {
            'lat_range': [self.latGrid.min(), self.latGrid.max()],
            'lon_range': [self.lonGrid.min(), self.lonGrid.max()],
            'shape': (len(self.latGrid), len(self.lonGrid))
        }

    def _generate_roi_mask(self):
        """生成 ROI 掩码"""
        logger.info("生成 ROI 掩码...")

        rows = len(self.latGrid)
        cols = len(self.lonGrid)
        self.inROI = np.zeros((rows, cols), dtype=bool)

        # 创建坐标网格
        lon_grid, lat_grid = np.meshgrid(self.lonGrid, self.latGrid)

        # 检查每个网格点是否在 ROI 内
        for i in range(rows):
            for j in range(cols):
                point = Point(lon_grid[i, j], lat_grid[i, j])
                self.inROI[i, j] = self.roi_polygon.contains(point)

        # 填充 NaN 值
        if self.s2_data is not None:
            self.s2_data[~self.inROI] = np.nan
        if self.ast_data is not None:
            self.ast_data[~self.inROI] = np.nan
        if self.dem_data is not None:
            self.dem_data[~self.inROI] = np.nan

        logger.info(f"ROI 掩码生成完成: {np.sum(self.inROI)} 个有效像素")

    def _load_raster_file(self, file_path: str) -> np.ndarray:
        """
        加载栅格文件

        Args:
            file_path: 文件路径

        Returns:
            栅格数据数组
        """
        try:
            with rasterio.open(file_path) as src:
                # 读取第一个波段
                band = src.read(1)

                # 如果需要，重采样到目标分辨率
                if hasattr(self, '_resample_data'):
                    band = self._resample_data(band)

                return band
        except Exception as e:
            logger.error(f"加载栅格文件失败: {file_path} - {str(e)}")
            raise

    def get_band_data(self, data_source: str, band_index: int) -> np.ndarray:
        """
        获取指定波段的原始数据（不考虑 ROI）

        Args:
            data_source: 数据源 ('s2', 'l8', 'aster')
            band_index: 波段索引

        Returns:
            波段数据
        """
        if data_source == 's2' and self.s2_data is not None:
            return self.s2_data
        elif data_source == 'l8' and self.l8_data is not None:
            return self.l8_data
        elif data_source == 'aster' and self.ast_data is not None:
            return self.ast_data
        else:
            raise ValueError(f"不支持的数据源: {data_source}")

    def get_band_roi(self, data_source: str, band_index: int) -> np.ndarray:
        """
        获取指定波段的 ROI 数据

        Args:
            data_source: 数据源 ('s2', 'l8', 'aster')
            band_index: 波段索引

        Returns:
            ROI 数据
        """
        raw_data = self.get_band_data(data_source, band_index)
        return np.where(self.inROI, raw_data, np.nan)

    def get_roi_polygon(self) -> Polygon:
        """获取 ROI 多边形"""
        return self.roi_polygon

    def get_roi_points(self) -> np.ndarray:
        """获取 ROI 点"""
        return self.roi_points

    def get_coordinates(self) -> Tuple[np.ndarray, np.ndarray]:
        """获取坐标网格"""
        return self.lonGrid, self.latGrid

    def get_mineral_config(self) -> Dict[str, Any]:
        """获取矿物配置"""
        return self.config

    def validate_data(self) -> Dict[str, bool]:
        """
        验证数据完整性

        Returns:
            验证结果
        """
        validation = {
            's2_data': self.s2_data is not None,
            'l8_data': self.l8_data is not None,
            'aster_data': self.ast_data is not None,
            'dem_data': self.dem_data is not None,
            'roi_points': self.roi_points is not None,
            'roi_polygon': self.roi_polygon is not None,
            'grids': self.lonGrid is not None and self.latGrid is not None,
            'roi_mask': self.inROI is not None
        }

        # 检查数据一致性
        if validation['aster_data']:
            validation['shape_match'] = (
                self.ast_data.shape[0] == len(self.latGrid) and
                self.ast_data.shape[1] == len(self.lonGrid)
            )

        return validation

    def get_statistics(self) -> Dict[str, Any]:
        """获取数据统计信息"""
        stats = {
            'mineral_type': self.mineral_type,
            'data_dir': str(self.data_dir),
            'roi_file': str(self.roi_file),
            'grids': {
                'lat_range': [self.latGrid.min(), self.latGrid.max()],
                'lon_range': [self.lonGrid.min(), self.lonGrid.max()],
                'shape': (len(self.latGrid), len(self.lonGrid))
            },
            'roi': {
                'point_count': len(self.roi_points),
                'polygon_area': self.roi_polygon.area,
                'valid_pixels': int(np.sum(self.inROI))
            }
        }

        # 各数据源的统计
        if self.s2_data is not None:
            stats['s2_data'] = {
                'shape': self.s2_data.shape,
                'nan_count': int(np.sum(np.isnan(self.s2_data))),
                'valid_pixels': int(np.sum(~np.isnan(self.s2_data)))
            }
        if self.ast_data is not None:
            stats['ast_data'] = {
                'shape': self.ast_data.shape,
                'nan_count': int(np.sum(np.isnan(self.ast_data))),
                'valid_pixels': int(np.sum(~np.isnan(self.ast_data)))
            }
        if self.dem_data is not None:
            stats['dem_data'] = {
                'shape': self.dem_data.shape,
                'nan_count': int(np.sum(np.isnan(self.dem_data))),
                'valid_pixels': int(np.sum(~np.isnan(self.dem_data)))
            }

        return stats

    def save_metadata(self, save_path: str):
        """保存元数据"""
        import json

        metadata = {
            'statistics': self.get_statistics(),
            'metadata': self.metadata,
            'config': self.config
        }

        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"元数据已保存到: {save_path}")

    def __str__(self) -> str:
        """字符串表示"""
        return f"GeoDataContext(mineral_type={self.mineral_type}, " \
               f"data_dir={self.data_dir}, roi_points={len(self.roi_points)})"

    def __repr__(self) -> str:
        """字符串表示"""
        return self.__str__()