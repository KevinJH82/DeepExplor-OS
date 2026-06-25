"""
后处理器模块

执行深度与压力反演及结果可视化
"""

import os
import numpy as np
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
from datetime import datetime
import json

from fusion_engine import FusionEngine
from geo_data_context import GeoDataContext
from .base_classes import DetectorResult
from config.config import Config
from utils.geo_utils import GeoUtils
from utils.logger import get_logger


class PostProcessor:
    """
    后处理器类

    执行深度反演、压力计算、地表潜力计算及可视化
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        初始化后处理器

        Args:
            params: 后处理参数
        """
        self.params = params or {}
        self.logger = get_logger(__name__)
        self.result_data = {}

    def run(self, context: GeoDataContext, engine: FusionEngine,
           final_mask: np.ndarray, output_dir: str) -> Dict[str, Any]:
        """
        执行完整的后处理流程

        Args:
            context: 地理数据上下文
            engine: 融合引擎
            final_mask: 融合后的掩码
            output_dir: 输出目录

        Returns:
            后处理结果
        """
        self.logger.info("开始执行后处理...")

        # 创建输出目录
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. 深度反演
        self.logger.info("执行深度反演...")
        depth_map = self._calculate_depth_inversion(final_mask, context)

        # 2. 压力反演
        self.logger.info("执行压力反演...")
        grad_P = self._calculate_pressure_inversion(depth_map, context)

        # 3. 地表潜力变量计算
        self.logger.info("计算地表潜力变量...")
        surface_variables = self._calculate_surface_variables(context)

        # 4. PCA 分析
        self.logger.info("执行 PCA 分析...")
        pca_results = self._perform_pca(context)

        # 5. 地表潜力增强函数
        self.logger.info("计算地表潜力增强...")
        Au_surface = self._calculate_surface_potential(
            surface_variables, pca_results, final_mask, context)

        # 6. 融合地表背景
        if self.params.get('fusion_mode', True):
            self.logger.info("融合地表背景...")
            Au_deep = self._apply_surface_background(Au_surface, final_mask, context)
        else:
            Au_deep = self._normalize_roi_mask(Au_surface, context)

        # 7. 高斯滤波归一化
        self.logger.info("应用高斯滤波...")
        Au_deep = self._normalize_gaussian_filter(Au_deep, context)

        # 8. 保存结果
        self.logger.info("保存结果...")
        self._save_results(
            {
                'depth_map': depth_map,
                'pressure_map': grad_P,
                'surface_variables': surface_variables,
                'pca_results': pca_results,
                'Au_surface': Au_surface,
                'Au_deep': Au_deep,
                'final_mask': final_mask
            },
            output_dir,
            context
        )

        # 9. 生成可视化
        self.logger.info("生成可视化...")
        self._generate_visualizations(
            {
                'depth_map': depth_map,
                'pressure_map': grad_P,
                'Au_deep': Au_deep,
                'final_mask': final_mask
            },
            output_dir,
            context
        )

        self.logger.info("后处理完成！")

        return {
            'output_dir': str(output_dir),
            'files_generated': self._get_generated_files(output_dir),
            'statistics': self._calculate_statistics({
                'depth_map': depth_map,
                'pressure_map': grad_P,
                'Au_deep': Au_deep,
                'final_mask': final_mask
            })
        }

    def _calculate_depth_inversion(self, mask: np.ndarray,
                                 context: GeoDataContext) -> np.ndarray:
        """
        计算深度反演

        Args:
            mask: 融合掩码
            context: 地理数据上下文

        Returns:
            深度图
        """
        mineral_config = Config.get_mineral_config(context.mineral_type)

        # Yakymchuk 参数模型
        params = mineral_config.get('yakymchuk_params', {})
        a = params.get('a', 10)
        b = params.get('b', 20)
        c = params.get('c', 0.1)

        # 计算共振频率
        f_res_MHz = a + b * np.exp(-c * np.abs(mask))

        # 避免除零
        f_res_MHz = np.maximum(f_res_MHz, 0.1)

        # 计算深度
        c_light = 3e8  # 光速
        epsilon_r = 16  # 相对介电常数

        # 转换为 km
        depth_map = c_light / (2 * f_res_MHz * 1e6 * np.sqrt(epsilon_r)) / 1000

        # 应用 ROI
        depth_map = np.where(context.inROI, depth_map, np.nan)

        return depth_map

    def _calculate_pressure_inversion(self, depth_map: np.ndarray,
                                   context: GeoDataContext) -> np.ndarray:
        """
        计算压力反演

        Args:
            depth_map: 深度图
            context: 地理数据上下文

        Returns:
            压力梯度
        """
        # 压力公式：P = 25 + 5 * depth (MPa)
        grad_P = 25 + 5 * depth_map

        # 应用 ROI
        grad_P = np.where(context.inROI, grad_P, np.nan)

        return grad_P

    def _calculate_surface_variables(self, context: GeoDataContext) -> Dict[str, np.ndarray]:
        """
        计算地表潜力变量

        Args:
            context: 地理数据上下文

        Returns:
            地表变量字典
        """
        surface_vars = {}

        # Ferric 比值
        if context.ast_data is not None:
            Ferric = context.ast_data[1] / (context.ast_data[0] + Config.ALGORITHM['eps_value'])
            surface_vars['Ferric'] = self._normalize_roi_mask(Ferric, context)

        # Clay 比值
        if context.ast_data is not None:
            Clay = context.ast_data[5] / (context.ast_data[6] + Config.ALGORITHM['eps_value'])
            surface_vars['Clay'] = self._normalize_roi_mask(Clay, context)

        # NDVI 反演
        if context.s2_data is not None:
            NIR = context.s2_data[:, :, 4]
            Red = context.s2_data[:, :, 3]
            NDVI = (NIR - Red) / (NIR + Red + Config.ALGORITHM['eps_value'])
            NDVI_inv = 1 - NDVI
            surface_vars['NDVI_inv'] = self._normalize_roi_mask(NDVI_inv, context)

        return surface_vars

    def _perform_pca(self, context: GeoDataContext) -> Dict[str, np.ndarray]:
        """
        执行 PCA 分析

        Args:
            context: 地理数据上下文

        Returns:
            PCA 结果
        """
        if context.ast_data is None:
            return {}

        # 准备 PCA 输入 (4-7 波段)
        pca_input = np.stack([
            context.ast_data[3],  # B4
            context.ast_data[4],  # B5
            context.ast_data[5],  # B6
            context.ast_data[6]   # B7
        ], axis=-1)

        # 应用 ROI
        valid_mask = context.inROI
        pca_input_valid = pca_input[valid_mask]

        from sklearn.decomposition import PCA

        # 执行 PCA
        pca = PCA(n_components=3)
        score = pca.fit_transform(pca_input_valid)

        # 恢复原始形状
        score_3d = np.zeros((pca_input.shape[0], pca_input.shape[1], 3))
        score_3d[valid_mask] = score

        # 归一化各个主成分
        pca_results = {}
        for i in range(3):
            component_name = f'PC{i+1}'
            pca_results[component_name] = self._normalize_roi_mask(score_3d[:, :, i], context)

        # 计算异常指数
        pca_results['Hydroxy_anomaly'] = pca_results['PC2']  # 第二主成分作为羟基异常
        pca_results['Fe_anomaly'] = pca_results['PC3']      # 第三主成分作为铁异常

        return pca_results

    def _calculate_surface_potential(self, surface_vars: Dict[str, np.ndarray],
                                   pca_results: Dict[str, np.ndarray],
                                   mask: np.ndarray,
                                   context: GeoDataContext) -> np.ndarray:
        """
        计算地表潜力增强函数

        Args:
            surface_vars: 地表变量
            pca_results: PCA 结果
            mask: 融合掩码
            context: 地理数据上下文

        Returns:
            地表潜力
        """
        # 初始化 Au_surface
        Au_surface = np.zeros_like(mask)

        # 获取各种变量
        Ferric = surface_vars.get('Ferric', np.zeros_like(mask))
        Clay = surface_vars.get('Clay', np.zeros_like(mask))
        NDVI_inv = surface_vars.get('NDVI_inv', np.zeros_like(mask))
        Hydroxy_anomaly = pca_results.get('Hydroxy_anomaly', np.zeros_like(mask))
        Fe_anomaly = pca_results.get('Fe_anomaly', np.zeros_like(mask))

        # 地表潜力增强函数
        # Au = f(Ferric, Fe_anomaly, Hydroxy_anomaly, Clay, NDVI_inv)
        Au_surface = (
            0.3 * Ferric +
            0.2 * Fe_anomaly +
            0.2 * Hydroxy_anomaly +
            0.15 * Clay +
            0.15 * NDVI_inv
        )

        # 应用掩码增强
        Au_surface = Au_surface * (1 + mask * 0.4)

        return Au_surface

    def _apply_surface_background(self, Au_surface: np.ndarray,
                                mask: np.ndarray,
                                context: GeoDataContext) -> np.ndarray:
        """
        应用地表背景融合

        Args:
            Au_surface: 地表潜力
            mask: 融合掩码
            context: 地理数据上下文

        Returns:
            融合后的结果
        """
        # 叠加地表背景
        Au_surface[context.inROI] = Au_surface[context.inROI] * (1 + mask[context.inROI] * 0.4)

        # ROI 归一化
        Au_deep = self._normalize_roi_mask(Au_surface, context)

        return Au_deep

    def _normalize_roi_mask(self, data: np.ndarray,
                          context: GeoDataContext) -> np.ndarray:
        """
        ROI 内归一化

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

    def _normalize_gaussian_filter(self, Au_deep: np.ndarray,
                                context: GeoDataContext) -> np.ndarray:
        """
        高斯滤波归一化

        Args:
            Au_deep: 输入数据
            context: 地理数据上下文

        Returns:
            滤波后的数据
        """
        # 创建有效掩码
        valid_mask = context.inROI & ~np.isnan(Au_deep)

        # 应用高斯滤波
        from scipy.ndimage import gaussian_filter
        Au_filt = gaussian_filter(Au_deep, sigma=8, mode='constant', cval=0)

        # 滤波权重
        W_filt = gaussian_filter(valid_mask.astype(float), sigma=8, mode='constant', cval=0)

        # 避免除零
        W_filt = np.maximum(W_filt, Config.ALGORITHM['eps_value'])

        # 归一化
        Au_deep_normalized = Au_filt / W_filt

        # 恢复原始 NaN 值
        Au_deep_normalized[~valid_mask] = np.nan

        return Au_deep_normalized

    def _save_results(self, results: Dict[str, np.ndarray],
                      output_dir: Path, context: GeoDataContext):
        """保存结果数据"""
        # 保存为 .mat 格式
        from scipy.io import savemat

        # 准备要保存的数据
        mat_data = {}
        for name, data in results.items():
            # 移除 NaN 值，替换为 0
            mat_data[name] = np.nan_to_num(data, nan=0)

        # 添加元数据
        mat_data['lonGrid'], mat_data['latGrid'] = context.get_coordinates()
        mat_data['mineral_type'] = context.mineral_type
        mat_data['inROI'] = context.inROI.astype(int)

        # 保存 .mat 文件
        mat_file = output_dir / 'mineral_prediction_results.mat'
        savemat(str(mat_file), mat_data)

        # 保存为 numpy 格式
        for name, data in results.items():
            np.save(output_dir / f'{name}.npy', data)

        # 保存配置信息
        config_data = {
            'mineral_type': context.mineral_type,
            'fusion_mode': self.params.get('fusion_mode', True),
            'kmz_threshold': self.params.get('kmz_threshold', 0.6),
            'output_time': datetime.now().isoformat(),
            'statistics': self._calculate_statistics(results)
        }

        with open(output_dir / 'analysis_config.json', 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

    def _generate_visualizations(self, results: Dict[str, np.ndarray],
                               output_dir: Path, context: GeoDataContext):
        """生成可视化结果"""
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors

        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        # 颜色方案
        mineral_config = Config.get_mineral_config(context.mineral_type)
        color_scheme = mineral_config.get('color_scheme', 'viridis')

        # 1. 共振参数图（如果有）
        if 'Au_surface' in results:
            self._plot_image(results['Au_surface'],
                           output_dir / '01_共振参数综合图.png',
                           '共振参数综合图',
                           color_scheme,
                           context)

        # 2. 掩码集成图
        self._plot_image(results['final_mask'],
                       output_dir / '02_掩码集成.png',
                       '掩码集成图',
                       'hot',
                       context)

        # 3. 深部预测图
        self._plot_image(results['Au_deep'],
                       output_dir / '03_深部成矿预测图.png',
                       '深部成矿预测图',
                       color_scheme,
                       context)

        # 4. 深度图
        if 'depth_map' in results:
            self._plot_image(results['depth_map'],
                           output_dir / '04_深度反演图.png',
                           '深度反演图',
                           'terrain',
                           context)

        # 5. 压力图
        if 'pressure_map' in results:
            self._plot_image(results['pressure_map'],
                           output_dir / '05_压力反演图.png',
                           '压力反演图',
                           'plasma',
                           context)

        # 6. 生成 KMZ 文件
        self._generate_kmz(results, output_dir, context)

    def _plot_image(self, data: np.ndarray, output_path: Path,
                   title: str, color_scheme: str, context: GeoDataContext):
        """绘制图像"""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(12, 10))

        # 应用 ROI 掩码
        display_data = np.where(context.inROI, data, np.nan)

        # 绘制图像
        im = ax.imshow(display_data, cmap=color_scheme, origin='upper')

        # 添加颜色条
        cbar = plt.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label(title, fontsize=12)

        # 设置标题
        ax.set_title(title, fontsize=14, fontweight='bold')

        # 隐藏坐标轴
        ax.set_xticks([])
        ax.set_yticks([])

        # 保存图像
        plt.tight_layout()
        plt.savefig(str(output_path), dpi=300, bbox_inches='tight')
        plt.close()

    def _generate_kmz(self, results: Dict[str, np.ndarray],
                     output_dir: Path, context: GeoDataContext):
        """生成 KMZ 文件"""
        try:
            # 创建临时目录
            kmz_dir = output_dir / 'kmz_temp'
            kmz_dir.mkdir(exist_ok=True)

            # 生成 KML 文件
            kml_content = self._create_kml_file(results, kmz_dir, context)

            # 保存 KML 文件
            kml_file = kmz_dir / 'prediction_overlay.kml'
            with open(kml_file, 'w', encoding='utf-8') as f:
                f.write(kml_content)

            # 打包为 KMZ
            import zipfile

            kmz_file = output_dir / 'mineral_prediction.kmz'
            with zipfile.ZipFile(str(kmz_file), 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(str(kml_file), 'doc.kml')

            # 清理临时目录
            import shutil
            shutil.rmtree(kmz_dir)

            self.logger.info(f"KMZ 文件已生成: {kmz_file}")

        except Exception as e:
            self.logger.error(f"生成 KMZ 文件失败: {str(e)}")

    def _create_kml_file(self, results: Dict[str, np.ndarray],
                        kmz_dir: Path, context: GeoDataContext) -> str:
        """创建 KML 文件内容"""
        kml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Mineral Prediction Results</name>
    <description>舒曼波共振遥感矿产预测结果</description>

    <GroundOverlay>
      <name>深部成矿预测</name>
      <description>深部成矿预测结果图</description>
      <Icon>
        <href>prediction_overlay.png</href>
      </Icon>
      <LatLonBox>
        <north>{context.latGrid.max()}</north>
        <south>{context.latGrid.min()}</south>
        <east>{context.lonGrid.max()}</east>
        <west>{context.lonGrid.min()}</west>
      </LatLonBox>
    </GroundOverlay>
  </Document>
</kml>'''

        # 保存预测图像为 PNG
        from PIL import Image
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(12, 10))
        display_data = np.where(context.inROI, results['Au_deep'], np.nan)
        im = ax.imshow(display_data, cmap='viridis', origin='upper')
        ax.set_xticks([])
        ax.set_yticks([])
        plt.tight_layout()

        # 保存为 PNG
        png_path = kmz_dir / 'prediction_overlay.png'
        plt.savefig(str(png_path), dpi=100, bbox_inches='tight')
        plt.close()

        return kml_content

    def _calculate_statistics(self, results: Dict[str, np.ndarray]) -> Dict[str, Any]:
        """计算统计信息"""
        stats = {}

        for name, data in results.items():
            valid_data = data[~np.isnan(data)]
            if len(valid_data) > 0:
                stats[name] = {
                    'min': float(np.min(valid_data)),
                    'max': float(np.max(valid_data)),
                    'mean': float(np.mean(valid_data)),
                    'std': float(np.std(valid_data)),
                    'median': float(np.median(valid_data))
                }

        return stats

    def _get_generated_files(self, output_dir: Path) -> List[str]:
        """获取生成的文件列表"""
        files = []
        for file in output_dir.glob('*'):
            if file.is_file():
                files.append(file.name)
        return sorted(files)

    def export_summary_report(self, output_dir: Path, context: GeoDataContext):
        """导出总结报告"""
        report_path = output_dir / 'analysis_summary.txt'

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("舒曼波共振遥感矿产预测分析报告\n")
            f.write("=" * 50 + "\n\n")

            f.write(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"目标矿种: {context.mineral_type}\n")
            f.write(f"数据目录: {context.data_dir}\n")
            f.write(f"ROI 点数: {len(context.roi_points)}\n\n")

            f.write("输出文件:\n")
            for file in self._get_generated_files(output_dir):
                f.write(f"- {file}\n")

            f.write("\n统计信息:\n")
            for stat_name, stat_data in self.result_data.get('statistics', {}).items():
                f.write(f"\n{stat_name}:\n")
                for key, value in stat_data.items():
                    f.write(f"  {key}: {value:.4f}\n")