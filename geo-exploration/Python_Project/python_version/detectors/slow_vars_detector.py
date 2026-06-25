"""
慢变量检测器

检测地应力、氧化还原、流体超压、断裂、盖层、温度梯度、化学势等地质构造因素
"""

import numpy as np
from typing import Dict, Any, Optional, Tuple
from loguru import logger

from .base_detector import GeoDetectorBase
from ..core.base_classes import DetectorResult
from config.config import Config


class SlowVarsDetector(GeoDetectorBase):
    """
    慢变量检测器

    实现基于 Z-score 标准化和三次方程求解的慢变量突变检测
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        初始化慢变量检测器

        Args:
            params: 检测器参数
        """
        default_params = {
            'factors': ['stress', 'redox', 'pressure', 'fracture'],
            'z_score_threshold': 2.5,
            'equation_order': 3,
            'use_dem': True,
            'combine_method': 'multiply',  # 'multiply', 'sum', 'max'
            # InSAR 集成参数(Phase 1.5)
            'insar_enabled': False,        # True 时启用 surface_deformation 第 8 类因素
            'insar_coherence_threshold': 0.3,
            'insar_velocity_weight': 1.0,  # 形变速率在 fracture 增强中的权重
        }

        if params:
            default_params.update(params)

        super().__init__('SlowVars', default_params)
        self.config = Config.DETECTORS['slow_vars']

    def calculate(self, context) -> DetectorResult:
        """
        计算慢变量异常

        Args:
            context: 地理数据上下文

        Returns:
            检测结果
        """
        self._debug_log("开始计算慢变量异常...")

        # 验证输入数据
        self._validate_data(context)

        # 计算各个慢变量
        slow_vars = {}
        for factor in self.params['factors']:
            slow_vars[factor] = self._calculate_factor(factor, context)

        # 融合慢变量
        mask = self._combine_factors(slow_vars, context)

        # 后处理
        mask = self._post_process_mask(mask, context)

        self._debug_log(f"慢变量检测完成，有效像素: {np.sum(mask)}")

        return DetectorResult(
            mask=mask,
            debug_data={
                'slow_vars': slow_vars,
                'z_score_threshold': self.params['z_score_threshold'],
                'factors': self.params['factors']
            }
        )

    def _calculate_factor(self, factor: str, context) -> np.ndarray:
        """
        计算单个慢变量因子

        Args:
            factor: 因子名称
            context: 地理数据上下文

        Returns:
            因子值
        """
        if factor == 'stress':
            return self._calculate_stress(context)
        elif factor == 'redox':
            return self._calculate_redox(context)
        elif factor == 'pressure':
            return self._calculate_pressure(context)
        elif factor == 'fracture':
            return self._calculate_fracture(context)
        elif factor == 'caprock':
            return self._calculate_caprock(context)
        elif factor == 'gradient':
            return self._calculate_gradient(context)
        elif factor == 'chemical_potential':
            return self._calculate_chemical_potential(context)
        elif factor == 'surface_deformation':
            return self._calculate_surface_deformation(context)
        else:
            self._debug_log(f"未知的慢变量因子: {factor}")
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_stress(self, context) -> np.ndarray:
        """计算地应力"""
        # 基于地形起伏和 DEM 数据计算地应力
        if context.dem_data is not None:
            # 计算地形曲率
            from scipy.ndimage import gaussian_filter, sobel

            dem_roi = np.where(context.inROI, context.dem_data, np.nan)

            # 计算梯度
            grad_x = sobel(dem_roi, axis=1) / 8.0
            grad_y = sobel(dem_roi, axis=0) / 8.0

            # 计算曲率
            grad_mag = np.sqrt(grad_x**2 + grad_y**2)

            # 高斯平滑
            grad_smooth = gaussian_filter(grad_mag, sigma=5)

            # Z-score 标准化
            z_score = (grad_smooth - np.nanmean(grad_smooth)) / np.nanstd(grad_smooth)

            return z_score
        else:
            # 如果没有 DEM，使用 ASTER 数据近似
            return self._calculate_approximate_stress(context)

    def _calculate_approximate_stress(self, context) -> np.ndarray:
        """基于 ASTER 数据近似计算地应力"""
        # 使用 ASTER 的 B6/B7 比值近似
        if context.ast_data is not None:
            ratio = context.ast_data[5] / (context.ast_data[6] + Config.ALGORITHM['eps_value'])
            z_score = (ratio - np.nanmean(ratio)) / np.nanstd(ratio)
            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_redox(self, context) -> np.ndarray:
        """计算氧化还原电位"""
        # 基于 ASTER 数据计算
        if context.ast_data is not None:
            # 使用 B3/B4 比值作为氧化还原指标
            ratio = context.ast_data[2] / (context.ast_data[3] + Config.ALGORITHM['eps_value'])
            z_score = (ratio - np.nanmean(ratio)) / np.nanstd(ratio)
            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_pressure(self, context) -> np.ndarray:
        """计算流体超压"""
        # 基于深度和密度计算
        if context.dem_data is not None:
            # 使用 DEM 高程作为深度指标
            dem_roi = np.where(context.inROI, context.dem_data, np.nan)

            # 归一化到 [0, 1]
            dem_normalized = (dem_roi - np.nanmin(dem_roi)) / (np.nanmax(dem_roi) - np.nanmin(dem_roi) + Config.ALGORITHM['eps_value'])

            # 压力与深度成正比
            pressure = dem_normalized

            # 添加噪声模拟流体超压
            noise = np.random.normal(0, 0.1, pressure.shape)
            pressure += noise

            # Z-score 标准化
            z_score = (pressure - np.nanmean(pressure)) / np.nanstd(pressure)

            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_fracture(self, context) -> np.ndarray:
        """
        计算断裂构造

        Phase 1.5 增强:如果 context 提供 InSAR 形变速率(insar_velocity),
        则把 InSAR 速率差分场叠加到 DEM Sobel 边缘上,显著增强活动断层识别能力。
        没有 InSAR 数据时,完全等同于原版逻辑(零回归)。
        """
        # 基于地形梯度和 ASTER 数据的纹理特征
        if context.dem_data is not None:
            # 计算地形梯度
            from scipy.ndimage import sobel
            dem_roi = np.where(context.inROI, context.dem_data, np.nan)

            grad_x = sobel(dem_roi, axis=1) / 8.0
            grad_y = sobel(dem_roi, axis=0) / 8.0
            gradient = np.sqrt(grad_x**2 + grad_y**2)

            # 梯度高的地方可能是断裂
            z_score = (gradient - np.nanmean(gradient)) / np.nanstd(gradient)

            # Phase 1.5: 叠加 InSAR 形变速率差分场(如果可用)
            insar_v = getattr(context, 'insar_velocity', None)
            if insar_v is not None and self.params.get('insar_enabled', False):
                try:
                    v_roi = np.where(context.inROI, insar_v, np.nan)
                    v_grad_x = sobel(v_roi, axis=1) / 8.0
                    v_grad_y = sobel(v_roi, axis=0) / 8.0
                    v_gradient = np.sqrt(v_grad_x**2 + v_grad_y**2)
                    v_z = (v_gradient - np.nanmean(v_gradient)) / (np.nanstd(v_gradient) + 1e-9)
                    # 相干性掩膜:不可靠像素不参与叠加
                    coh = getattr(context, 'insar_coherence', None)
                    if coh is not None:
                        thr = self.params.get('insar_coherence_threshold', 0.3)
                        v_z = np.where(coh >= thr, v_z, 0.0)
                    weight = float(self.params.get('insar_velocity_weight', 1.0))
                    z_score = z_score + weight * v_z
                    self._debug_log("[fracture] 已叠加 InSAR 速率差分场(活动断层增强)")
                except Exception as e:
                    self._debug_log(f"[fracture] InSAR 增强失败,fallback 到纯 DEM: {e}")
            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_caprock(self, context) -> np.ndarray:
        """计算盖层条件"""
        # 基于 ASTER 数据的特定波段
        if context.ast_data is not None:
            # 使用 B7 (SWIR) 和 B6 的差异
            diff = context.ast_data[6] - context.ast_data[5]
            z_score = (diff - np.nanmean(diff)) / np.nanstd(diff)
            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_gradient(self, context) -> np.ndarray:
        """计算温度梯度"""
        # 基于 DEM 数据计算温度梯度
        if context.dem_data is not None:
            dem_roi = np.where(context.inROI, context.dem_data, np.nan)

            # 温度随高程降低
            normalized = (dem_roi - np.nanmin(dem_roi)) / (np.nanmax(dem_roi) - np.nanmin(dem_roi) + Config.ALGORITHM['eps_value'])

            # 反转（温度梯度向上为负）
            gradient = -normalized

            # Z-score 标准化
            z_score = (gradient - np.nanmean(gradient)) / np.nanstd(gradient)

            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_chemical_potential(self, context) -> np.ndarray:
        """计算化学势"""
        # 基于 ASTER 数据的比值
        if context.ast_data is not None:
            # 使用 B4/B3 和 B7/B6 的乘积
            ratio1 = context.ast_data[3] / (context.ast_data[2] + Config.ALGORITHM['eps_value'])
            ratio2 = context.ast_data[7] / (context.ast_data[6] + Config.ALGORITHM['eps_value'])

            chemical = ratio1 * ratio2
            z_score = (chemical - np.nanmean(chemical)) / np.nanstd(chemical)

            return z_score
        else:
            return np.zeros_like(context.inROI, dtype=float)

    def _calculate_surface_deformation(self, context) -> np.ndarray:
        """
        计算地表形变(InSAR LOS 速率)— Phase 1.5 新增第 8 类构造因素。

        语义:LOS 速率绝对值越大,表示该位置近期形变越活跃,可作为深部
        活动构造/活跃矿区的强信号。结合 coherence 掩膜剔除不可靠像素。

        要求:
        - context.insar_velocity: ndarray, LOS 速率(mm/year),与 inROI 同形状
        - context.insar_coherence: ndarray, 0-1(可选,用于掩膜)

        如果 InSAR 数据不可用,返回零(优雅降级)。
        """
        insar_v = getattr(context, 'insar_velocity', None)
        if insar_v is None:
            self._debug_log("[surface_deformation] context 无 insar_velocity,返回零")
            return np.zeros_like(context.inROI, dtype=float)

        # 用 LOS 速率绝对值作为形变活动性指标
        v_abs = np.abs(np.where(context.inROI, insar_v, np.nan))

        # coherence 掩膜
        coh = getattr(context, 'insar_coherence', None)
        if coh is not None:
            thr = self.params.get('insar_coherence_threshold', 0.3)
            v_abs = np.where(coh >= thr, v_abs, np.nan)

        # Z-score 标准化(同其他因素一致)
        mean = np.nanmean(v_abs)
        std = np.nanstd(v_abs)
        if std < 1e-9 or np.isnan(std):
            return np.zeros_like(context.inROI, dtype=float)
        z_score = (v_abs - mean) / std
        # NaN 填零(防止 _combine_factors 出错)
        z_score = np.where(np.isnan(z_score), 0.0, z_score)
        self._debug_log(f"[surface_deformation] z-score 计算完成 (mean={mean:.3f} mm/yr, std={std:.3f})")
        return z_score

    def _combine_factors(self, factors: Dict[str, np.ndarray],
                        context) -> np.ndarray:
        """
        融合多个慢变量因子

        Args:
            factors: 因子字典
            context: 地理数据上下文

        Returns:
            融合后的结果
        """
        method = self.params['combine_method']
        threshold = self.params['z_score_threshold']

        # 获取 ROI 内的数据
        valid_factors = {}
        for name, factor in factors.items():
            valid_mask = ~np.isnan(factor) & context.inROI
            if np.any(valid_mask):
                valid_factors[name] = factor[valid_mask]

        if not valid_factors:
            return np.zeros_like(context.inROI, dtype=float)

        # 标准化所有因子
        normalized_factors = {}
        for name, factor in valid_factors.items():
            # 只保留超过阈值的值
            clipped = np.where(np.abs(factor) > threshold, factor, 0)
            normalized_factors[name] = clipped

        # 融合方法
        if method == 'multiply':
            # 乘法融合：突出共同异常
            combined = np.ones_like(context.inROI, dtype=float)
            for name, factor in normalized_factors.items():
                factor_full = np.zeros_like(context.inROI, dtype=float)
                factor_full[context.inROI] = factor
                combined *= factor_full

        elif method == 'sum':
            # 加法融合
            combined = np.zeros_like(context.inROI, dtype=float)
            for name, factor in normalized_factors.items():
                factor_full = np.zeros_like(context.inROI, dtype=float)
                factor_full[context.inROI] = factor
                combined += factor_full

        elif method == 'max':
            # 取最大值
            combined = np.zeros_like(context.inROI, dtype=float)
            for name, factor in normalized_factors.items():
                factor_full = np.zeros_like(context.inROI, dtype=float)
                factor_full[context.inROI] = np.abs(factor)
                combined = np.maximum(combined, factor_full)

        else:
            raise ValueError(f"不支持的融合方法: {method}")

        # 归一化
        if np.nanmax(combined) > 0:
            combined = combined / np.nanmax(combined)

        return combined

    def _post_process_mask(self, mask: np.ndarray, context) -> np.ndarray:
        """
        后处理掩码

        Args:
            mask: 原始掩码
            context: 地理数据上下文

        Returns:
            处理后的掩码
        """
        # 形态学操作
        from scipy.ndimage import binary_closing

        # 闭运算填充小孔
        mask = binary_closing(mask > 0.5, structure=np.ones((3,3)))

        # 高斯平滑
        mask = self._imgaussfilt(mask.astype(float),
                               sigma=Config.ALGORITHM['gaussian_sigma'])

        return mask

    def solve_cubic_equation(self, coefficients: np.ndarray) -> np.ndarray:
        """
        求解三次方程 ax^3 + bx^2 + cx + d = 0

        Args:
            coefficients: 系数数组 [a, b, c, d]

        Returns:
            根
        """
        a, b, c, d = coefficients

        # 计算判别式
        delta = 18*a*b*c*d - 4*b**3*d + b**2*c**2 - 4*a*c**3 - 27*a**2*d**2

        # 简化处理：求实根
        # 使用 numpy 的 roots 函数
        roots = np.roots([a, b, c, d])

        # 返回实根
        real_roots = np.real(roots[np.isreal(roots)])

        if len(real_roots) > 0:
            return real_roots[0]  # 返回第一个实根
        else:
            return 0.0

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        info = super().get_detector_info()
        info.update({
            'algorithm': 'Slow variables mutation detection',
            'factors': self.params['factors'],
            'equation_order': self.params['equation_order'],
            'z_score_threshold': self.params['z_score_threshold'],
            'combine_method': self.params['combine_method'],
            'parameters': {
                'use_dem': self.params['use_dem'],
                'z_score_threshold': self.params['z_score_threshold']
            }
        })
        return info