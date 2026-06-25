"""
红边异常检测器

基于红边位置偏移（S2REP）和 Moran I 空间自相关的异常检测

严格按照 Matlab RedEdgeDetector.m 实现
"""

import numpy as np
from typing import Dict, Any
from .base_detector import GeoDetectorBase, DetectorResult
from utils.geo_utils import calculate_s2rep_from_dn, calc_local_sum_with_nan, mat2gray_roi, get_mineral_thresholds


class RedEdgeDetector(GeoDetectorBase):
    """
    红边异常检测器

    实现基于红边位置偏移（S2REP）和 Moran I 空间自相关的异常检测
    严格按照 Matlab RedEdgeDetector.m 逻辑
    """

    def __init__(self, params: Dict[str, Any] = None):
        """
        初始化红边检测器

        Args:
            params: 检测器参数
        """
        default_params = {
            's2rep_center': 705,  # S2REP 中心波长 (nm)
            'levashov_mode': True  # 是否应用 Levashov 阈值修正模式
        }

        if params:
            default_params.update(params)

        super().__init__('RedEdge', default_params)

    def calculate(self, context: Dict) -> DetectorResult:
        """
        计算红边异常

        Args:
            context: 数据上下文字典，包含:
                - s2: Sentinel-2 数据 (H, W, 10)
                - inROI: ROI 掩码 (H, W)
                - mineral_type: 矿物类型

        Returns:
            DetectorResult: 检测结果
        """
        self._debug_log("开始计算红边异常...")

        # 1. 提取 Sentinel-2 波段
        B4 = context['s2'][:, :, 2]  # Red (Band 3, 0-indexed)
        B5 = context['s2'][:, :, 6]  # Red Edge 1 (Band 7, 0-indexed)
        B6 = context['s2'][:, :, 7]  # Red Edge 2
        B7 = context['s2'][:, :, 8]  # NIR

        # 2. 计算 S2REP (红边位置)
        scale_factors = np.array([1.9997e-05, 1.9998e-05, 1.9998e-05, 1.9999e-05], dtype=np.float64)
        offsets = np.array([-0.1, -0.1, -0.1, -0.1], dtype=np.float64)
        S2REP, REP_QA = calculate_s2rep_from_dn(B4, B5, B6, B7, scale_factors, offsets)

        # 3. 异常强度 F_map 计算
        lambda_center = self.params['s2rep_center']
        delta_red_edge = S2REP - lambda_center
        F_map = np.abs(delta_red_edge) / lambda_center

        # 4. Moran I 计算 (严格按照 Matlab 逻辑)

        # (A) Z-score 统计
        # Matlab 使用 ROI 内统计
        F_vals = F_map[context['inROI']]
        F_mean = np.nanmean(F_vals)
        F_std = np.nanstd(F_vals, ddof=1)

        if F_std == 0:
            F_std = np.finfo(np.float32).eps

        Z = (F_map - F_mean) / F_std

        # (B) Local Sum
        ls = calc_local_sum_with_nan(Z)

        # (C) 原始 Moran 值
        moran_raw = Z * ls

        # (D) 归一化 (复刻 Matlab 的 mat2gray 逻辑)
        moran_local = np.full_like(moran_raw, np.nan, dtype=np.float32)
        valid_mask = ~np.isnan(moran_raw)

        if np.any(valid_mask):
            min_v = np.min(moran_raw[valid_mask])
            max_v = np.max(moran_raw[valid_mask])

            if max_v - min_v < np.finfo(np.float32).eps:
                moran_local[valid_mask] = 0
            else:
                moran_local[valid_mask] = (moran_raw[valid_mask] - min_v) / (max_v - min_v)

        # 清理无效区域 (ROI 外置 0)
        moran_local[~context['inROI']] = 0
        moran_local[np.isnan(moran_local)] = 0

        # 5. 阈值获取与 Levashov 修正
        F_thr, delta_thr, Moran_thr, _ = get_mineral_thresholds(context['mineral_type'])

        # Levashov 模式阈值打折
        if self.params['levashov_mode']:
            F_thr = F_thr * 0.8
            Moran_thr = Moran_thr * 0.8
            delta_thr = delta_thr * 1.2  # 负向阈值放宽

        # 6. 生成掩码 (严格按照 Matlab 筛选条件)
        mask = (
            (F_map > F_thr) &
            (delta_red_edge < delta_thr) &
            (moran_local > Moran_thr) &
            context['inROI'] &
            ~np.isnan(F_map)
        )

        mask = mask.astype(np.float32)
        mask[np.isnan(mask)] = 0

        self._debug_log(f"红边检测完成，有效像素: {np.sum(mask)}")

        return DetectorResult(
            mask=mask,
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

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        info = super().get_detector_info()
        info.update({
            'algorithm': 'S2REP-based red edge detection',
            'description': '基于红边位置偏移和 Moran I 空间自相关计算异常强度',
            'parameters': {
                's2rep_center': self.params['s2rep_center'],
                'levashov_mode': self.params['levashov_mode']
            }
        })
        return info