"""
地理工具函数

包含遥感数据处理、坐标转换、空间分析等工具函数
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple, List, Union
from pathlib import Path
import rasterio
from rasterio.transform import from_bounds, xy
from rasterio.warp import calculate_default_transform, reproject, Resampling
import geopandas as gpd
from shapely.geometry import Point, Polygon, box
import logging
from loguru import logger

from config.config import Config


class GeoUtils:
    """地理工具类"""

    @staticmethod
    def load_sentinel2_data(file_path: str) -> np.ndarray:
        """
        加载 Sentinel-2 数据

        Args:
            file_path: 文件路径

        Returns:
            Sentinel-2 数据数组
        """
        try:
            with rasterio.open(file_path) as src:
                # 读取所有波段
                data = src.read()
                return data
        except Exception as e:
            logger.error(f"加载 Sentinel-2 数据失败: {str(e)}")
            raise

    @staticmethod
    def load_landsat8_data(file_path: str) -> np.ndarray:
        """
        加载 Landsat-8 数据

        Args:
            file_path: 文件路径

        Returns:
            Landsat-8 数据数组
        """
        try:
            with rasterio.open(file_path) as src:
                data = src.read()
                return data
        except Exception as e:
            logger.error(f"加载 Landsat-8 数据失败: {str(e)}")
            raise

    @staticmethod
    def load_aster_data(file_path: str) -> np.ndarray:
        """
        加载 ASTER 数据

        Args:
            file_path: 文件路径

        Returns:
            ASTER 数据数组
        """
        try:
            with rasterio.open(file_path) as src:
                data = src.read()
                return data
        except Exception as e:
            logger.error(f"加载 ASTER 数据失败: {str(e)}")
            raise

    @staticmethod
    def load_dem_data(file_path: str) -> np.ndarray:
        """
        加载 DEM 数据

        Args:
            file_path: 文件路径

        Returns:
            DEM 数据数组
        """
        try:
            with rasterio.open(file_path) as src:
                data = src.read(1)  # 读取第一个波段
                return data
        except Exception as e:
            logger.error(f"加载 DEM 数据失败: {str(e)}")
            raise

    @staticmethod
    def calculate_S2REP_from_DN(B4: np.ndarray, B5: np.ndarray,
                              B6: np.ndarray, B7: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        从 DN 值计算 S2REP

        Args:
            B4: Red 波段
            B5: Red Edge 1 波段
            B6: Red Edge 2 波段
            B7: NIR 波段

        Returns:
            (S2REP, 置信度)
        """
        # 创建坐标网格
        x = np.array([4, 5, 6, 7])

        S2REP = np.zeros_like(B4)
        confidence = np.zeros_like(B4)

        # 对每个像素进行线性回归
        rows, cols = B4.shape
        for i in range(rows):
            for j in range(cols):
                if not (np.isnan(B4[i,j]) or np.isnan(B5[i,j]) or
                       np.isnan(B6[i,j]) or np.isnan(B7[i,j])):
                    y = np.array([B4[i,j], B5[i,j], B6[i,j], B7[i,j]])

                    # 去除 NaN 值
                    valid_mask = ~np.isnan(y)
                    if np.sum(valid_mask) >= 2:
                        x_valid = x[valid_mask]
                        y_valid = y[valid_mask]

                        # 线性回归
                        coefficients = np.polyfit(x_valid, y_valid, 1)
                        slope = coefficients[0]
                        intercept = coefficients[1]

                        # 计算红边位置
                        if slope != 0:
                            S2REP[i,j] = -intercept / slope
                        else:
                            S2REP[i,j] = 5.0  # 默认值

                        # 计算拟合优度 R²
                        y_pred = slope * x_valid + intercept
                        ss_tot = np.sum((y_valid - np.mean(y_valid))**2)
                        ss_res = np.sum((y_valid - y_pred)**2)

                        if ss_tot > 0:
                            confidence[i,j] = 1 - (ss_res / ss_tot)
                        else:
                            confidence[i,j] = 1.0
                else:
                    S2REP[i,j] = np.nan
                    confidence[i,j] = np.nan

        # 使用插值填充 NaN 值
        if np.any(np.isnan(S2REP)):
            S2REP = GeoUtils._interpolate_nans(S2REP)
            confidence = GeoUtils._interpolate_nans(confidence)

        return S2REP, confidence

    @staticmethod
    def computeIntrinsicAbsorption(ast: np.ndarray, mineral_type: str) -> np.ndarray:
        """
        计算本征吸收强度

        Args:
            ast: ASTER 数据
            mineral_type: 矿物类型

        Returns:
            本征吸收强度
        """
        mineral_config = Config.get_mineral_config(mineral_type)
        intrinsic_bands = mineral_config.get('intrinsic_bands', [])
        aster_bands = Config.REMOTE_SENSING['aster_bands']

        # 初始化吸收强度
        F_abs = np.zeros(ast.shape[1:])  # 假设 ast 是 (bands, height, width)

        for band_idx, role in intrinsic_bands:
            if band_idx < ast.shape[0]:
                band_data = ast[band_idx, :, :]

                if role == 'absorption':
                    # 吸收带：深度越小，吸收越强
                    F_abs += 1.0 / (band_data + Config.ALGORITHM['eps_value'])
                elif role == 'reflection':
                    # 反射带：反射率越高，吸收越强
                    F_abs += band_data
                elif role == 'continuum':
                    # 连续统：用于归一化
                    pass

        # 特定矿物的特殊处理
        if mineral_type == 'gold':
            # 黄铁矿 (Fe-S:0.8-0.9um) + Al-OH(2.2um)
            cont = (ast[aster_bands['B3']] + ast[aster_bands['B5']]) / 2
            target = ast[aster_bands['B3']]
            F_abs = (cont - target) / (cont + Config.ALGORITHM['eps_value'])
            F_abs += 0.5 * (ast[aster_bands['B6']] / (ast[aster_bands['B5']] + Config.ALGORITHM['eps_value']))

        elif mineral_type == 'copper':
            # Cu²⁺(0.8-0.9um) + OH(2.2um)
            cont = (ast[aster_bands['B3']] + ast[aster_bands['B5']]) / 2
            target = ast[aster_bands['B4']]
            F_abs = (cont - target) / (cont + Config.ALGORITHM['eps_value'])
            F_abs += 0.6 * (ast[aster_bands['B6']] / (ast[aster_bands['B5']] + Config.ALGORITHM['eps_value']))

        # 防止除零
        F_abs = np.nan_to_num(F_abs, nan=0.0)

        return F_abs

    @staticmethod
    def calc_local_sum_with_nan(Z: np.ndarray, window_size: int = 3) -> np.ndarray:
        """
        计算 3x3 邻域加权求和（忽略 NaN）

        Args:
            Z: 输入数据
            window_size: 窗口大小

        Returns:
            局部和
        """
        rows, cols = Z.shape
        local_sum = np.zeros_like(Z)

        # 创建权重矩阵
        w = np.ones((window_size, window_size))
        center = window_size // 2
        w[center, center] = 0  # 中心点权重为 0

        # 填充边界
        padded = np.pad(Z, center, mode='constant', constant_values=np.nan)

        for i in range(rows):
            for j in range(cols):
                if not np.isnan(Z[i, j]):
                    # 提取邻域
                    neighborhood = padded[i:i+window_size, j:j+window_size]
                    mask = ~np.isnan(neighborhood)

                    # 计算加权平均
                    if np.any(mask):
                        w_mask = w * mask
                        w_sum = np.sum(w_mask)
                        if w_sum > 0:
                            w_mask = w_mask / w_sum
                            local_sum[i, j] = np.sum(neighborhood * w_mask)

        return local_sum

    @staticmethod
    def getYakymchukParams(mineral_type: str) -> Dict[str, float]:
        """
        获取 Yakymchuk 模型参数

        Args:
            mineral_type: 矿物类型

        Returns:
            参数字典
        """
        mineral_config = Config.get_mineral_config(mineral_type)
        return mineral_config.get('yakymchuk_params', {'a': 10, 'b': 20, 'c': 0.1})

    @staticmethod
    def mat2gray_roi(data: np.ndarray, inROI: np.ndarray) -> np.ndarray:
        """
        ROI 内归一化到 [0, 1]

        Args:
            data: 输入数据
            inROI: ROI 掩码

        Returns:
            归一化后的数据
        """
        roi_data = np.where(inROI, data, np.nan)

        # 计算最小值和最大值
        valid_data = roi_data[~np.isnan(roi_data)]
        if len(valid_data) == 0:
            return np.zeros_like(data)

        min_val = np.min(valid_data)
        max_val = np.max(valid_data)

        if max_val - min_val < Config.ALGORITHM['eps_value']:
            return np.zeros_like(data)

        # 归一化
        normalized = (roi_data - min_val) / (max_val - min_val)

        # 恢复原始尺寸
        result = np.zeros_like(data)
        result[inROI] = normalized[inROI]

        return result

    @staticmethod
    def readROI_Robust(file_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray,
                                                np.ndarray, np.ndarray, List[str]]:
        """
        智能读取 ROI 文件

        Args:
            file_path: ROI 文件路径

        Returns:
            (roiPoly, inROI_vec, lonGrid, latGrid, lonROI, latROI, column_names)
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"ROI 文件不存在: {file_path}")

        # 读取文件
        try:
            if file_path.suffix in ['.xlsx', '.xls']:
                df = pd.read_excel(file_path)
            else:
                df = pd.read_csv(file_path)
        except Exception as e:
            raise ValueError(f"无法读取 ROI 文件: {str(e)}")

        # 智能识别经纬度列
        candidates_lon = []
        candidates_lat = []
        column_names = []

        for col in df.columns:
            col_lower = str(col).lower()
            # 检查列名中是否包含关键词
            if any(keyword in col_lower for keyword in ['lon', 'lng', 'longitude', '经度']):
                candidates_lon.append(col)
                column_names.append(col)
            elif any(keyword in col_lower for keyword in ['lat', 'latitude', '纬度']):
                candidates_lat.append(col)
                column_names.append(col)
            else:
                # 尝试判断是否为数值数据
                try:
                    values = pd.to_numeric(df[col], errors='coerce')
                    if values.notna().sum() > len(df) * 0.8:  # 80%以上是数值
                        mean_val = values.mean()
                        min_val = values.min()
                        max_val = values.max()

                        # 判断是经度还是纬度
                        if 60 < mean_val < 160 and (max_val - min_val) < 20:
                            candidates_lon.append(col)
                            column_names.append(col)
                        elif 0 < mean_val < 60 and (max_val - min_val) < 20:
                            candidates_lat.append(col)
                            column_names.append(col)
                except:
                    pass

        if not candidates_lon or not candidates_lat:
            raise ValueError("无法识别经纬度列")

        # 使用第一个匹配的列
        lon_col = candidates_lon[0]
        lat_col = candidates_lat[0]

        # 提取坐标
        lonROI = df[lon_col].values
        latROI = df[lat_col].values

        # 创建 ROI 多边形
        roi_points = list(zip(lonROI, latROI))
        if len(roi_points) >= 3:
            from shapely.geometry import Polygon
            roiPoly = Polygon(roi_points)
        else:
            # 如果点数不足，创建缓冲区
            center = np.mean([lonROI, latROI], axis=1)
            roiPoly = Point(center[0], center[1]).buffer(0.01)

        # 创建网格（这里简化处理）
        lon_min, lon_max = np.min(lonROI), np.max(lonROI)
        lat_min, lat_max = np.min(latROI), np.max(latROI)

        lonGrid = np.linspace(lon_min, lon_max, 100)
        latGrid = np.linspace(lat_max, lat_min, 100)

        # 创建 ROI 向量
        inROI_vec = np.zeros((len(latGrid), len(lonGrid)), dtype=bool)

        return roiPoly, inROI_vec, lonGrid, latGrid, lonROI, latROI, column_names

    @staticmethod
    def _interpolate_nans(data: np.ndarray) -> np.ndarray:
        """
        使用插值填充 NaN 值

        Args:
            data: 输入数据

        Returns:
            插值后的数据
        """
        from scipy.interpolate import griddata

        # 创建坐标网格
        rows, cols = data.shape
        x, y = np.meshgrid(np.arange(cols), np.arange(rows))

        # 找到非 NaN 点
        valid_mask = ~np.isnan(data)
        points = np.column_stack((x[valid_mask], y[valid_mask]))
        values = data[valid_mask]

        # 插值填充 NaN
        data_interp = np.where(valid_mask, data,
                             griddata(points, values,
                                    (x, y),
                                    method='linear'))

        return data_interp

    @staticmethod
    def resample_raster(src_data: np.ndarray, src_transform: rasterio.transform.Affine,
                      dst_shape: Tuple[int, int], dst_bounds: Tuple[float, float, float, float]) -> np.ndarray:
        """
        重采样栅格数据

        Args:
            src_data: 源数据
            src_transform: 源变换
            dst_shape: 目标形状
            dst_bounds: 目标边界

        Returns:
            重采样后的数据
        """
        dst_transform = from_bounds(*dst_bounds, dst_shape[1], dst_shape[0])

        # 创建目标数据
        dst_data = np.zeros(dst_shape, dtype=src_data.dtype)

        # 执行重投影
        reproject(
            source=src_data,
            destination=dst_data,
            src_transform=src_transform,
            src_crs='EPSG:4326',
            dst_transform=dst_transform,
            dst_crs='EPSG:4326',
            resampling=Resampling.nearest
        )

        return dst_data

    @staticmethod
    def calculate_spatial_statistics(data: np.ndarray, mask: Optional[np.ndarray] = None) -> Dict[str, float]:
        """
        计算空间统计信息

        Args:
            data: 输入数据
            mask: 可选掩码

        Returns:
            统计信息字典
        """
        if mask is not None:
            data = data[mask]

        # 去除 NaN
        data = data[~np.isnan(data)]

        if len(data) == 0:
            return {}

        stats = {
            'count': len(data),
            'mean': float(np.mean(data)),
            'std': float(np.std(data)),
            'min': float(np.min(data)),
            'max': float(np.max(data)),
            'median': float(np.median(data)),
            'q25': float(np.percentile(data, 25)),
            'q75': float(np.percentile(data, 75))
        }

        return stats

    @staticmethod
    def create_roi_mask(lon_grid: np.ndarray, lat_grid: np.ndarray,
                      roi_polygon: Polygon) -> np.ndarray:
        """
        创建 ROI 掩码

        Args:
            lon_grid: 经度网格
            lat_grid: 纬度网格
            roi_polygon: ROI 多边形

        Returns:
            ROI 掩码
        """
        rows, cols = lat_grid.shape
        roi_mask = np.zeros((rows, cols), dtype=bool)

        # 创建坐标网格
        lon_grid_2d, lat_grid_2d = np.meshgrid(lon_grid, lat_grid)

        # 检查每个点是否在 ROI 内
        for i in range(rows):
            for j in range(cols):
                point = Point(lon_grid_2d[i, j], lat_grid_2d[i, j])
                roi_mask[i, j] = roi_polygon.contains(point)

        return roi_mask

    @staticmethod
    def get_mineral_color_scheme(mineral_type: str) -> str:
        """
        获取矿物类型对应的颜色方案

        Args:
            mineral_type: 矿物类型

        Returns:
            颜色方案名称
        """
        mineral_config = Config.get_mineral_config(mineral_type)
        return mineral_config.get('color_scheme', 'viridis')

    @staticmethod
    def validate_coordinates(lon: float, lat: float) -> bool:
        """
        验证坐标有效性

        Args:
            lon: 经度
            lat: 纬度

        Returns:
            是否有效
        """
        return -180 <= lon <= 180 and -90 <= lat <= 90

    @staticmethod
    def buffer_to_degrees(buffer_meters: float, lat: float) -> float:
        """
        将缓冲区距离（米）转换为度

        Args:
            buffer_meters: 缓冲区距离（米）
            lat: 纬度（用于计算经度方向的缩放）

        Returns:
            缓冲区角度（度）
        """
        # 纬度 1° ≈ 111km
        lat_deg = buffer_meters / 111000

        # 经度 1° ≈ 111km * cos(纬度)
        lon_deg = buffer_meters / (111000 * np.cos(np.radians(lat)))

        return max(lat_deg, lon_deg)  # 取最大值作为圆形缓冲区