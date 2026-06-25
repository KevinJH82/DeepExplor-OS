"""
探测器基类
"""

import numpy as np
from typing import Dict, Any
from abc import ABC, abstractmethod


class DetectorResult:
    """探测器结果"""

    def __init__(self, mask: np.ndarray, debug_data: Dict[str, Any]):
        """
        初始化探测器结果

        Args:
            mask: 异常掩码或连续概率面
            debug_data: 调试数据
        """
        self.mask = mask
        self.debug_data = debug_data


class GeoDetectorBase(ABC):
    """地理探测器基类"""

    def __init__(self, name: str, params: Dict[str, Any]):
        """
        初始化探测器

        Args:
            name: 探测器名称
            params: 探测器参数
        """
        self.name = name
        self.params = params

    @abstractmethod
    def calculate(self, context) -> DetectorResult:
        """
        计算异常

        Args:
            context: 数据上下文

        Returns:
            检测结果
        """
        pass

    def _validate_data(self, context):
        """验证输入数据"""
        pass

    def _debug_log(self, message: str):
        """调试日志"""
        print(f"[{self.name}] {message}")

    def _imgaussfilt(self, img: np.ndarray, sigma: float = 4.0) -> np.ndarray:
        """
        高斯滤波

        Args:
            img: 输入图像
            sigma: 标准差

        Returns:
            滤波后的图像
        """
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(img, sigma=sigma, mode='constant', cval=0)

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        return {
            'name': self.name,
            'parameters': self.params
        }
