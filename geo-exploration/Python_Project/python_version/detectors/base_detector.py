"""
探测器基类

定义所有探测器共用的方法和属性
"""

import numpy as np
from typing import Dict, Any, Optional, Tuple
from abc import ABC, abstractmethod
import logging
from loguru import logger

from ..core.base_classes import BaseDetector, DetectorResult


class GeoDetectorBase(BaseDetector):
    """
    地理探测器基类

    继承自 BaseDetector，添加了地理数据处理相关的方法
    """

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        super().__init__(name, params)
        self.logger = logger

    def _validate_data(self, context) -> bool:
        """
        验证输入数据

        Args:
            context: 地理数据上下文

        Returns:
            bool: 验证是否通过
        """
        # 检查必要的数据
        required_attrs = ['s2_data', 'ast_data', 'inROI', 'lonGrid', 'latGrid']

        for attr in required_attrs:
            if not hasattr(context, attr):
                raise ValueError(f"缺少必要的数据属性: {attr}")

        # 检查 ROI
        if not np.any(context.inROI):
            raise ValueError("ROI 区域为空")

        return True

    def _get_band(self, data: np.ndarray, band_name: str, context) -> np.ndarray:
        """
        获取指定波段的数据

        Args:
            data: 输入数据
            band_name: 波段名称
            context: 地理数据上下文

        Returns:
            波段数据
        """
        # 应用 ROI 掩码
        roi_data = np.where(context.inROI, data, np.nan)

        # 填充 NaN 值
        if self.params.get('fill_nan', True):
            roi_data = np.nan_to_num(roi_data, nan=Config.ALGORITHM['nan_value'])

        return roi_data

    def _calculate_moran_i(self, data: np.ndarray, context, window_size: int = 3) -> np.ndarray:
        """
        计算 Moran I 空间自相关

        Args:
            data: 输入数据
            context: 地理数据上下文
            window_size: 窗口大小

        Returns:
            Moran I 值
        """
        # 计算均值和标准差
        valid_mask = ~np.isnan(data)
        if np.sum(valid_mask) < window_size**2:
            return np.zeros_like(data)

        mean_val = np.nanmean(data)
        std_val = np.nanstd(data)

        if std_val < Config.ALGORITHM['eps_value']:
            return np.zeros_like(data)

        # 标准化
        Z = (data - mean_val) / std_val

        # 计算局部和
        moran_local = np.zeros_like(data)

        for i in range(window_size//2, data.shape[0] - window_size//2):
            for j in range(window_size//2, data.shape[1] - window_size//2):
                if not valid_mask[i, j]:
                    continue

                # 计算邻域内的和
                window = Z[i-window_size//2:i+window_size//2+1,
                           j-window_size//2:j+window_size//2+1]
                window_sum = np.nansum(window)

                # 忽略中心点
                center_value = Z[i, j]
                moran_local[i, j] = center_value * window_sum

        return moran_local

    def _mat2gray_roi(self, data: np.ndarray, context) -> np.ndarray:
        """
        ROI 内归一化到 [0, 1]

        Args:
            data: 输入数据
            context: 地理数据上下文

        Returns:
            归一化后的数据
        """
        roi_data = np.where(context.inROI, data, np.nan)

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
        result[context.inROI] = normalized[context.inROI]

        return result

    def _imgaussfilt(self, image: np.ndarray, sigma: float = 1.0,
                    padding: str = 'replicate') -> np.ndarray:
        """
        高斯滤波（替代 MATLAB 的 imgaussfilt）

        Args:
            image: 输入图像
            sigma: 标准差
            padding: 填充方式 ('replicate', 'constant', 'reflect')

        Returns:
            滤波后的图像
        """
        from scipy.ndimage import gaussian_filter

        # 处理填充
        if padding == 'replicate':
            padded = np.pad(image, int(3*sigma), mode='edge')
        elif padding == 'constant':
            padded = np.pad(image, int(3*sigma), mode='constant', constant_values=0)
        elif padding == 'reflect':
            padded = np.pad(image, int(3*sigma), mode='reflect')
        else:
            padded = image

        # 高斯滤波
        filtered = gaussian_filter(padded, sigma=sigma)

        # 去除填充
        filtered = filtered[int(3*sigma):-int(3*sigma), int(3*sigma):-int(3*sigma)]

        return filtered

    def _apply_threshold(self, data: np.ndarray, threshold: float,
                        below_threshold: bool = False) -> np.ndarray:
        """
        应用阈值

        Args:
            data: 输入数据
            threshold: 阈值
            below_threshold: 是否取阈值以下的数据

        Returns:
            二值掩码
        """
        if below_threshold:
            return data < threshold
        else:
            return data > threshold

    def _debug_log(self, message: str, level: str = 'debug'):
        """
        记录调试信息

        Args:
            message: 消息
            level: 日志级别
        """
        if self.logger:
            getattr(self.logger, level)(f"[{self.name}] {message}")

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器详细信息"""
        info = super().get_detector_info()
        info['algorithm_type'] = 'geographic'
        return info