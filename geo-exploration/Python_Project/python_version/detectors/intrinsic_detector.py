"""
本征吸收检测器

基于矿物特征光谱吸收，生成连续热力图梯度面
"""

import numpy as np
from typing import Dict, Any, Optional, Tuple
from loguru import logger

from .base_detector import GeoDetectorBase
from ..core.base_classes import DetectorResult
from config.config import Config


class IntrinsicDetector(GeoDetectorBase):
    """
    本征吸收检测器

    实现基于矿物特征光谱吸收的异常检测，生成连续热力图梯度面
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        初始化本征吸收检测器

        Args:
            params: 检测器参数
        """
        default_params = {
            'weight_ratio': [0.6, 0.4],  # 吸收强度 : 空间聚集度
            'gaussian_sigma': Config.ALGORITHM['gaussian_sigma'],
            'continuous_mode': True,
            'mineral_type': 'gold'  # 从 context 中获取
        }

        if params:
            default_params.update(params)

        super().__init__('Intrinsic', default_params)
        self.config = Config.DETECTORS['intrinsic']

    def calculate(self, context) -> DetectorResult:
        """
        计算本征吸收异常

        Args:
            context: 地理数据上下文

        Returns:
            检测结果
        """
        self._debug_log("开始计算本征吸收异常...")

        # 验证输入数据
        self._validate_data(context)

        # 获取矿物类型
        mineral_type = context.mineral_type
        mineral_config = Config.get_mineral_config(mineral_type)

        # 计算原始本征吸收强度
        F_abs_raw = self._compute_intrinsic_absorption(
            context.ast_data, mineral_type)

        # ROI 内归一化
        F_abs = self._mat2gray_roi(F_abs_raw, context)

        # 计算 Moran I 空间自相关
        moran_local = self._calculate_moran_i(F_abs, context)

        # 归一化
        F_norm = self._mat2gray_roi(F_abs, context)
        M_norm = self._mat2gray_roi(moran_local, context)

        # 综合异常强度
        weight_ratio = self.params['weight_ratio']
        if len(weight_ratio) == 2:
            continuous_mask = (weight_ratio[0] * F_norm +
                             weight_ratio[1] * M_norm)
        else:
            continuous_mask = F_norm

        # 高斯平滑生成连片异常晕圈
        if self.params['continuous_mode']:
            continuous_mask = self._imgaussfilt(continuous_mask,
                                             sigma=self.params['gaussian_sigma'])

        self._debug_log(f"本征吸收检测完成，有效像素: {np.sum(context.inROI)}")

        return DetectorResult(
            mask=continuous_mask,
            debug_data={
                'F_abs_raw': F_abs_raw,
                'F_abs': F_abs,
                'moran_local': moran_local,
                'F_norm': F_norm,
                'M_norm': M_norm,
                'weight_ratio': weight_ratio,
                'mineral_type': mineral_type
            }
        )

    def _compute_intrinsic_absorption(self, ast_data: np.ndarray,
                                    mineral_type: str) -> np.ndarray:
        """
        计算本征吸收强度

        Args:
            ast_data: ASTER 数据
            mineral_type: 矿物类型

        Returns:
            本征吸收强度
        """
        mineral_config = Config.get_mineral_config(mineral_type)
        intrinsic_bands = mineral_config.get('intrinsic_bands', [])

        # 获取 ASTER 波段
        aster_bands = Config.REMOTE_SENSING['aster_bands']

        # 计算吸收强度
        F_abs = np.zeros(ast_data.shape[1:])  # 假设 ast_data 是 (bands, height, width)

        for band_idx, role in intrinsic_bands:
            if band_idx < ast_data.shape[0]:
                band_data = ast_data[band_idx, :, :]

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
            cont = (ast_data[aster_bands['B3']] + ast_data[aster_bands['B5']]) / 2
            target = ast_data[aster_bands['B3']]
            F_abs = (cont - target) / (cont + Config.ALGORITHM['eps_value'])
            F_abs += 0.5 * (ast_data[aster_bands['B6']] /
                          (ast_data[aster_bands['B5']] + Config.ALGORITHM['eps_value']))

        elif mineral_type == 'copper':
            # Cu²⁺(0.8-0.9um) + OH(2.2um)
            cont = (ast_data[aster_bands['B3']] + ast_data[aster_bands['B5']]) / 2
            target = ast_data[aster_bands['B4']]
            F_abs = (cont - target) / (cont + Config.ALGORITHM['eps_value'])
            F_abs += 0.6 * (ast_data[aster_bands['B6']] /
                          (ast_data[aster_bands['B5']] + Config.ALGORITHM['eps_value']))

        elif mineral_type == 'iron':
            # 铁的氧化物在可见光和近红外有特征吸收
            # B4 (Red), B5 (NIR) 的比值
            F_abs = ast_data[aster_bands['B4']] / (ast_data[aster_bands['B5']] +
                                                  Config.ALGORITHM['eps_value'])

        elif mineral_type == 'coal':
            # 煤炭在 SWIR 波段有特征吸收
            # B6 (SWIR 1), B7 (SWIR 2) 的比值
            F_abs = ast_data[aster_bands['B6']] / (ast_data[aster_bands['B7']] +
                                                  Config.ALGORITHM['eps_value'])

        elif mineral_type == 'petroleum':
            # 石油的烃类吸收带
            # 使用 B7 (SWIR 1) 和 B8 (TIR 1) 的差异
            F_abs = ast_data[aster_bands['B7']] - ast_data[aster_bands['B8']]

        # 默认处理：使用前两个波段
        elif mineral_type not in mineral_config:
            # 默认算法：使用 ASTER 的 B3/B4 比值
            F_abs = ast_data[aster_bands['B3']] / (ast_data[aster_bands['B4']] +
                                                  Config.ALGORITHM['eps_value'])

        # 防止除零
        F_abs = np.nan_to_num(F_abs, nan=0.0)

        return F_abs

    def _calculate_depth_inversion(self, F_abs: np.ndarray,
                                 mineral_type: str) -> np.ndarray:
        """
        计算深度反演

        Args:
            F_abs: 吸收强度
            mineral_type: 矿物类型

        Returns:
            深度估计
        """
        mineral_config = Config.get_mineral_config(mineral_type)

        # Yakymchuk 参数模型
        params = mineral_config.get('yakymchuk_params', {})
        a = params.get('a', 10)
        b = params.get('b', 20)
        c = params.get('c', 0.1)

        # 计算共振频率
        f_res_MHz = a + b * np.exp(-c * np.abs(F_abs))

        # 计算深度
        c_light = 3e8  # 光速
        epsilon_r = 16  # 相对介电常数

        depth = c_light / (2 * f_res_MHz * 1e6 * np.sqrt(epsilon_r)) / 1000  # 转换为 km

        return depth

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        info = super().get_detector_info()
        info.update({
            'algorithm': 'Spectral absorption-based detection',
            'continuous_mode': self.params['continuous_mode'],
            'weight_ratio': self.params['weight_ratio'],
            'supported_minerals': list(Config.MINERAL_TYPES.keys()),
            'parameters': {
                'gaussian_sigma': self.params['gaussian_sigma'],
                'mineral_type': self.params['mineral_type']
            }
        })
        return info