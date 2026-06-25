"""
本征吸收检测器

基于矿物特征光谱吸收 + Moran I 空间自相关，动态阈值二值化
严格按照 Matlab IntrinsicDetector.m 旧版逻辑（二值化阈值 + 形态学开运算）
"""

import numpy as np
from typing import Dict, Any
from .base_detector import GeoDetectorBase, DetectorResult
from utils.geo_utils import compute_intrinsic_absorption, mat2gray_roi, calc_local_sum_with_nan, get_mineral_thresholds
from scipy.ndimage import binary_opening


class IntrinsicDetector(GeoDetectorBase):
    """
    本征吸收检测器

    基于矿物特征光谱吸收 + Moran I 空间自相关，动态阈值二值化
    """

    def __init__(self, params: Dict[str, Any] = None):
        default_params = {}
        if params:
            default_params.update(params)
        super().__init__('Intrinsic', default_params)

    def calculate(self, context: Dict) -> DetectorResult:
        """
        计算本征吸收异常（二值化阈值模式）

        Args:
            context: 数据上下文字典，包含:
                - ast: ASTER 数据 (H, W, 14)
                - inROI: ROI 掩码 (H, W)
                - mineral_type: 矿物类型

        Returns:
            DetectorResult: 检测结果（二值掩码）
        """
        self._debug_log("计算本征吸收异常 (二值化阈值模式)...")
        inROI = context['inROI']
        mineral_type = context['mineral_type']

        # 1. 计算本征吸收强度 F_abs
        ast = context['ast']
        F_abs_raw = compute_intrinsic_absorption(ast, mineral_type)
        F_abs = mat2gray_roi(F_abs_raw, inROI)

        # 2. 计算 Moran I (空间自相关)
        F_vals = F_abs[inROI]
        F_mean = np.nanmean(F_vals)
        F_std = np.nanstd(F_vals, ddof=1)
        if F_std == 0:
            F_std = np.finfo(np.float64).eps

        Z = (F_abs - F_mean) / F_std
        Z[~inROI] = np.nan

        local_sum = calc_local_sum_with_nan(Z)

        ls_roi = local_sum[inROI]
        max_ls = np.nanmax(ls_roi)
        if max_ls == 0 or np.isnan(max_ls):
            max_ls = np.finfo(np.float64).eps

        moran_local = Z * local_sum / max_ls
        moran_local[~inROI] = np.nan
        moran_local[np.isnan(moran_local) | np.isinf(moran_local)] = 0

        # 3. 动态阈值逻辑 (复刻旧版 Matlab)
        F_thr, _, Moran_thr, _ = get_mineral_thresholds(mineral_type)

        # ROI 内有效值
        valid_F = F_abs[inROI & ~np.isnan(F_abs)]
        valid_M = moran_local[inROI & ~np.isnan(moran_local)]

        F_dyn = np.nanpercentile(valid_F, 95)
        M_dyn = np.nanpercentile(valid_M, 95)

        F_final = max(F_thr, F_dyn * 0.9)
        M_final = max(Moran_thr, M_dyn * 0.9)

        self._debug_log(f"  阈值: F_thr={F_thr:.4f}, F_dyn95={F_dyn:.4f}, F_final={F_final:.4f}")
        self._debug_log(f"  阈值: M_thr={Moran_thr:.4f}, M_dyn95={M_dyn:.4f}, M_final={M_final:.4f}")

        # 生成二值掩码
        mask = (F_abs > F_final) & (moran_local > M_final) & inROI

        # 形态学开运算 (strel('square', 3) + imopen)
        struct = np.ones((3, 3), dtype=bool)
        mask = binary_opening(mask, structure=struct)

        detected = np.sum(mask)
        total = np.sum(inROI)
        self._debug_log(f"  检测到 {detected}/{total} 异常像素 ({100*detected/total:.2f}%)")

        return DetectorResult(
            mask=mask.astype(np.float64),
            debug_data={
                'F_abs': F_abs,
                'moran_local': moran_local,
                'F_final': F_final,
                'M_final': M_final,
                'mineral_type': mineral_type
            }
        )

    def get_detector_info(self) -> Dict[str, Any]:
        info = super().get_detector_info()
        info.update({
            'algorithm': 'Spectral absorption + Moran I (binary threshold)',
            'description': '基于矿物特征光谱吸收 + Moran I 空间自相关，动态阈值二值化',
        })
        return info
