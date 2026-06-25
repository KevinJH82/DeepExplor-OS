"""
慢变量检测器

检测地应力、氧化还原、流体超压、断裂、盖层、温度梯度、化学势等地质构造因素

严格按照 Matlab SlowVarsDetector.m 实现
"""

import numpy as np
from typing import Dict, Any
from scipy.ndimage import binary_dilation, label, gaussian_filter
from skimage.feature import canny
from .base_detector import GeoDetectorBase, DetectorResult


class SlowVarsDetector(GeoDetectorBase):
    """
    慢变量检测器

    综合地应力、氧化还原、流体超压等多个地质构造因素
    严格按照 Matlab SlowVarsDetector.m 逻辑
    """

    def __init__(self, params: Dict[str, Any] = None):
        """
        初始化慢变量检测器

        Args:
            params: 检测器参数
        """
        default_params = {}

        if params:
            default_params.update(params)

        super().__init__('SlowVars', default_params)

    def calculate(self, context: Dict) -> DetectorResult:
        """
        计算慢变量异常

        Args:
            context: 数据上下文字典，包含:
                - dem: DEM 数据 (H, W)
                - lan: Landsat 数据 (H, W, 7)
                - s2: Sentinel-2 数据 (H, W, 10)
                - ast: ASTER 数据 (H, W, 14)
                - NIR: 近红外波段 (H, W)
                - Red: 红光波段 (H, W)
                - inROI: ROI 掩码 (H, W)

        Returns:
            检测结果
        """
        self._debug_log("开始计算慢变量异常...")
        inROI = context['inROI']
        eps = np.finfo(np.float32).eps

        # 1. 地应力
        gx, gy = np.gradient(context['dem'])
        stress_grad = np.sqrt(gx**2 + gy**2)

        # 2. 氧化还原
        iron_oxide = context['lan'][:, :, 2] / (context['lan'][:, :, 1] + eps)  # B3/B2
        swir1 = context['s2'][:, :, 7]  # B8 (0-indexed)
        swir2 = context['s2'][:, :, 8]  # B9 (0-indexed)
        oxy_fug = swir2 - np.mean(np.dstack([swir1, swir2]), axis=2)
        oxy_fug[np.isnan(oxy_fug) | np.isinf(oxy_fug)] = 0
        redox_grad = (np.abs(iron_oxide - np.nanmean(iron_oxide[inROI])) +
                     np.abs(oxy_fug - np.nanmean(oxy_fug[inROI])))

        # 3. 流体超压
        tir_mean = np.mean(context['ast'][:, :, 9:14], axis=2)  # B10:B14 (0-indexed: 9:14)
        ndvi = (context['NIR'] - context['Red']) / (context['NIR'] + context['Red'] + eps)
        fluid_over = tir_mean + 3 * (1 - ndvi)

        # 4. 断裂 — Canny 边缘检测 (匹配 Matlab edge(stress_grad, 'canny', [0.05 0.25]))
        stress_smooth = gaussian_filter(stress_grad, sigma=1.0, truncate=2.0)
        # 归一化到 [0, 1] 以匹配 skimage Canny 的阈值语义
        s_min, s_max = np.nanmin(stress_smooth), np.nanmax(stress_smooth)
        if s_max - s_min > eps:
            stress_norm = (stress_smooth - s_min) / (s_max - s_min)
        else:
            stress_norm = np.zeros_like(stress_smooth)
        edges = canny(stress_norm, low_threshold=0.05, high_threshold=0.25)

        # bwareaopen(edges, 50) — 去除小连通区域
        labeled, num_features = label(edges.astype(np.uint8))
        min_size = 50
        edges_filtered = np.zeros_like(edges, dtype=np.uint8)
        for i in range(1, num_features + 1):
            if np.sum(labeled == i) >= min_size:
                edges_filtered[labeled == i] = 1

        fault_activity = edges_filtered.astype(np.float64) * stress_grad

        # geo-stru 线性构造增强 fault_activity(D 部分,可选,需 context 提供 structural_density/control)。
        # 断裂是深部矿控构造,因果正确的家就是这里 —— 把糙 Canny 升级成 "Canny + 解译断裂"。
        # 镜像下方 InSAR 增强范式;缺失/异常则保持 Canny-only(零回归)。
        # 专用通道 fault_lineament:仅当 mineral_engine 判定 enabled+inject 时才注入,
        # 避免地表用的 structural_control 在未启用时被误读。
        lin = context.get('fault_lineament') if isinstance(context, dict) else None
        if lin is not None:
            try:
                ln = np.asarray(lin, dtype=np.float64)
                if ln.shape == fault_activity.shape:
                    lo, hi = np.nanmin(ln), np.nanmax(ln)
                    if np.isfinite(lo) and np.isfinite(hi) and (hi - lo) > eps:
                        lin_norm = np.nan_to_num((ln - lo) / (hi - lo), nan=0.0)
                        # 权重优先取 context(由 config 透传),回退 self.params,再回退默认
                        w_lin = float(context.get('lineament_weight',
                                      self.params.get('lineament_weight', 0.5)))
                        fault_activity = fault_activity + w_lin * lin_norm * stress_grad
                        self._debug_log(f"[fault_activity] 已叠加 geo-stru 线性构造(权重 {w_lin})")
                else:
                    self._debug_log(f"[fault_activity] 构造层形状不匹配 {ln.shape} vs {fault_activity.shape},跳过")
            except Exception as e:
                self._debug_log(f"[fault_activity] 构造增强失败: {e}")

        # Phase 1.5: 用 InSAR 速率差分场增强 fault_activity(可选,需 context 提供 insar_velocity)
        insar_v = context.get('insar_velocity') if isinstance(context, dict) else None
        insar_coh = context.get('insar_coherence') if isinstance(context, dict) else None
        if insar_v is not None:
            try:
                vgx, vgy = np.gradient(np.where(np.isnan(insar_v), 0.0, insar_v))
                v_grad = np.sqrt(vgx**2 + vgy**2)
                v_min, v_max = np.nanmin(v_grad), np.nanmax(v_grad)
                if v_max - v_min > eps:
                    v_norm = (v_grad - v_min) / (v_max - v_min)
                    if insar_coh is not None:
                        thr = float(self.params.get('insar_coherence_threshold', 0.3))
                        v_norm = np.where(insar_coh >= thr, v_norm, 0.0)
                    weight = float(self.params.get('insar_velocity_weight', 0.5))
                    fault_activity = fault_activity + weight * v_norm * stress_grad
                    self._debug_log("[fault_activity] 已叠加 InSAR 速率差分场")
            except Exception as e:
                self._debug_log(f"[fault_activity] InSAR 增强失败: {e}")

        # 5. 盖层
        carbonate = (context['ast'][:, :, 5] + context['ast'][:, :, 7]) / (context['ast'][:, :, 6] + eps)  # B6+B8/B7

        # 6. 温度梯度
        gtx, gty = np.gradient(tir_mean)
        temp_grad = np.sqrt(gtx**2 + gty**2)

        # 7. 化学势
        gcx, gcy = np.gradient(iron_oxide + oxy_fug)
        chem_grad = np.sqrt(gcx**2 + gcy**2)

        # Z-Score helper
        def z_score(x):
            return (x - np.nanmean(x[inROI])) / (np.nanstd(x[inROI], ddof=1) + eps)

        # Phase 1.5: 第 8 类构造因素 surface_deformation(LOS 形变速率绝对值)
        if insar_v is not None:
            v_abs = np.abs(np.where(np.isnan(insar_v), 0.0, insar_v))
            if insar_coh is not None:
                thr = float(self.params.get('insar_coherence_threshold', 0.3))
                v_abs = np.where(insar_coh >= thr, v_abs, 0.0)
            surface_deformation = v_abs
        else:
            surface_deformation = np.zeros_like(stress_grad)

        # 组合
        # 当有 InSAR 时,权重在 b 里再分一份给 surface_deformation;
        # 没有 InSAR 时,保持原版权重不变(零回归)。
        a = -(0.5 * z_score(carbonate) + 0.5 * z_score(temp_grad))
        if insar_v is not None:
            b = (0.22 * z_score(stress_grad) +
                 0.18 * z_score(redox_grad) +
                 0.22 * z_score(fluid_over) +
                 0.13 * z_score(fault_activity) +
                 0.13 * z_score(chem_grad) +
                 0.12 * z_score(surface_deformation))  # InSAR 形变项
        else:
            b = (0.25 * z_score(stress_grad) +
                 0.2 * z_score(redox_grad) +
                 0.25 * z_score(fluid_over) +
                 0.15 * z_score(fault_activity) +
                 0.15 * z_score(chem_grad))

        Delta = b**2 + (8.0 / 27.0) * a**3
        mask = (Delta < 0) & inROI

        # 形态学开运算 bwareaopen(mask, 100)
        labeled, num_features = label(mask.astype(np.uint8))
        min_size = 100

        mask_filtered = np.zeros_like(mask, dtype=np.uint8)
        for i in range(1, num_features + 1):
            if np.sum(labeled == i) >= min_size:
                mask_filtered[labeled == i] = 1

        # 膨胀 imdilate(mask, strel('disk', 8))
        # 创建圆形结构元素 (disk, 8)
        y, x = np.ogrid[-8:9, -8:9]
        disk = x**2 + y**2 <= 8**2

        mask_dilated = binary_dilation(mask_filtered, structure=disk)

        # 创建调试数据
        debug_data = {
            'Delta': Delta,
            'stress_grad': stress_grad,
            'redox_grad': redox_grad,
            'fluid_over': fluid_over,
            'fault_activity': fault_activity,
            'carbonate': carbonate,
            'temp_grad': temp_grad,
            'chem_grad': chem_grad,
            'surface_deformation': surface_deformation,  # Phase 1.5 InSAR
            'insar_enabled': insar_v is not None,
        }

        self._debug_log(f"慢变量计算完成，有效像素数: {np.sum(mask_dilated)}")

        return DetectorResult(mask_dilated.astype(np.float64), debug_data)