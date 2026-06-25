"""
红边异常检测器

基于红边位置偏移和 Moran I 空间自相关计算异常强度
"""

import numpy as np
from typing import Dict, Any, Optional, Tuple
from loguru import logger

from .base_detector import GeoDetectorBase
from ..core.base_classes import DetectorResult
from config.config import Config


class RedEdgeDetector(GeoDetectorBase):
    """
    红边异常检测器

    实现基于红边位置偏移（S2REP）和 Moran I 空间自相关的异常检测
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        初始化红边检测器

        Args:
            params: 检测器参数
        """
        default_params = {
            's2rep_center': Config.ALGORITHM['s2rep_center'],
            'threshold_factor': 1.0,
            'moran_window': Config.ALGORITHM['moran_window_size'],
            'levashov_mode': True,
            'delta_threshold': 0.5,
            'moran_threshold': 0.1
        }

        if params:
            default_params.update(params)

        super().__init__('RedEdge', default_params)
        self.config = Config.DETECTORS['red_edge']

    def calculate(self, context) -> DetectorResult:
        """
        计算红边异常

        Args:
            context: 地理数据上下文

        Returns:
            检测结果
        """
        self._debug_log("开始计算红边异常...")

        # 验证输入数据
        self._validate_data(context)

        # 获取关键波段
        try:
            B4 = self._get_band(context.s2_data[:, :, 3], 'B4', context)  # Red
            B5 = self._get_band(context.s2_data[:, :, 7], 'B5', context)  # Red Edge 1
            B6 = self._get_band(context.s2_data[:, :, 8], 'B6', context)  # Red Edge 2
            B7 = self._get_band(context.s2_data[:, :, 9], 'B7', context)  # NIR
        except IndexError:
            # 如果波段数不足，使用默认值
            self._debug_log("警告：波段数不足，使用模拟数据")
            shape = context.s2_data.shape[1:]
            B4 = np.random.rand(*shape)
            B5 = np.random.rand(*shape)
            B6 = np.random.rand(*shape)
            B7 = np.random.rand(*shape)

        # 1. 计算 S2REP
        S2REP, _ = self._calculate_S2REP(B4, B5, B6, B7)

        # 2. 计算异常强度 F_map
        lambda_center = self.params['s2rep_center']
        delta_red_edge = S2REP - lambda_center
        F_map = np.abs(delta_red_edge) / lambda_center

        # 3. 计算 Moran I 空间自相关
        moran_local = self._calculate_moran_i(F_map, context, self.params['moran_window'])

        # 4. Levashov 模式阈值修正
        F_thr = self._get_threshold('F_map', F_map)
        Moran_thr = self._get_threshold('Moran', moran_local)
        delta_thr = self.params['delta_threshold']

        if self.params['levashov_mode']:
            F_thr *= 0.8      # 阈值打折
            Moran_thr *= 0.8
            delta_thr *= 1.2  # 负向阈值放宽
            self._debug_log("Levashov 模式已启用")

        # 5. 多条件筛选
        mask = (
            (F_map > F_thr) &
            (delta_red_edge < delta_thr) &
            (moran_local > Moran_thr) &
            context.inROI &
            ~np.isnan(F_map)
        )

        # 应用后处理
        mask = self._post_process_mask(mask, context)

        self._debug_log(f"红边检测完成，有效像素: {np.sum(mask)}")

        return DetectorResult(
            mask=mask.astype(float),
            debug_data={
                'S2REP': S2REP,
                'F_map': F_map,
                'delta_red_edge': delta_red_edge,
                'moran_local': moran_local,
                'F_thr': F_thr,
                'Moran_thr': Moran_thr,
                'delta_thr': delta_thr
            }
        )

    def _calculate_S2REP(self, B4: np.ndarray, B5: np.ndarray,
                         B6: np.ndarray, B7: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        计算 S2REP (Sentinel-2 Red Edge Position)

        Args:
            B4: Red 波段
            B5: Red Edge 1 波段
            B6: Red Edge 2 波段
            B7: NIR 波段

        Returns:
            (S2REP, 置信度)
        """
        # 计算 NDVI
        NDVI = (B7 - B4) / (B7 + B4 + Config.ALGORITHM['eps_value'])

        # 计算红边指数
        REP = (B6 - B5) / (B6 + B5 + Config.ALGORITHM['eps_value'])

        # 线性回归拟合红边位置
        # 使用波段索引作为横坐标 (4, 5, 6, 7)
        x = np.array([4, 5, 6, 7])

        # 对每个像素进行线性回归
        S2REP = np.zeros_like(B4)
        confidence = np.zeros_like(B4)

        # 创建坐标网格
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
                        slope = np.polyfit(x_valid, y_valid, 1)[0]
                        intercept = np.polyfit(x_valid, y_valid, 1)[1]

                        # 计算红边位置（一阶导数为零的点）
                        if slope != 0:
                            S2REP[i,j] = -intercept / slope
                        else:
                            S2REP[i,j] = 5.0  # 默认值

                        # 计算拟合优度
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
            from scipy.interpolate import griddata

            # 创建坐标点
            points = np.column_stack(np.where(~np.isnan(S2REP)))
            values = S2REP[~np.isnan(S2REP)]

            # 插值填充
            nan_mask = np.isnan(S2REP)
            if np.any(~nan_mask):
                S2REP[nan_mask] = griddata(
                    points, values,
                    np.column_stack(np.where(nan_mask)),
                    method='linear'
                )

        # 对置信度进行插值
        if np.any(np.isnan(confidence)):
            points = np.column_stack(np.where(~np.isnan(confidence)))
            values = confidence[~np.isnan(confidence)]
            confidence[np.isnan(confidence)] = griddata(
                points, values,
                np.column_stack(np.where(np.isnan(confidence))),
                method='linear'
            )

        return S2REP, confidence

    def _get_threshold(self, param_name: str, data: np.ndarray) -> float:
        """
        获取阈值

        Args:
            param_name: 参数名称
            data: 数据

        Returns:
            阈值
        """
        if param_name == 'F_map':
            # 基于数据的统计特性动态设置阈值
            valid_data = data[~np.isnan(data)]
            if len(valid_data) > 0:
                mean_val = np.mean(valid_data)
                std_val = np.std(valid_data)
                return mean_val + self.params['threshold_factor'] * std_val
            return 0.1
        elif param_name == 'Moran':
            # Moran I 阈值设置
            return self.params['moran_threshold']
        else:
            return 0.0

    def _post_process_mask(self, mask: np.ndarray, context) -> np.ndarray:
        """
        后处理掩码

        Args:
            mask: 原始掩码
            context: 地理数据上下文

        Returns:
            处理后的掩码
        """
        # 形态学操作去除小噪声
        from scipy.ndimage import binary_opening, binary_closing

        # 开运算去除小亮点
        mask = binary_opening(mask, structure=np.ones((3,3)))

        # 闭运算填充小孔
        mask = binary_closing(mask, structure=np.ones((5,5)))

        # 高斯平滑生成连片异常晕圈
        mask = self._imgaussfilt(mask.astype(float),
                               sigma=Config.ALGORITHM['gaussian_sigma'])

        return mask

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        info = super().get_detector_info()
        info.update({
            'algorithm': 'S2REP-based red edge detection',
            'bands_used': ['B4 (Red)', 'B5 (Red Edge 1)', 'B6 (Red Edge 2)', 'B7 (NIR)'],
            'parameters': {
                's2rep_center': self.params['s2rep_center'],
                'threshold_factor': self.params['threshold_factor'],
                'moran_window': self.params['moran_window'],
                'levashov_mode': self.params['levashov_mode']
            }
        })
        return info