"""
可视化器

提供静态图像可视化功能
"""

import os
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Polygon as MPLPolygon
from matplotlib.figure import Figure
from loguru import logger

from config.config import Config


class Visualizer:
    """
    可视化器类

    提供多种遥感数据的静态可视化功能
    """

    def __init__(self, figsize: Tuple[int, int] = (12, 10),
                 dpi: int = 300, style: str = 'dark_background'):
        """
        初始化可视化器

        Args:
            figsize: 图像大小
            dpi: 分辨率
            style: 样式
        """
        self.figsize = figsize
        self.dpi = dpi
        self.style = style

        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        # 设置样式
        plt.style.use(style)

        # 预定义颜色方案
        self.color_schemes = {
            'viridis': 'viridis',
            'plasma': 'plasma',
            'inferno': 'inferno',
            'magma': 'magma',
            'cividis': 'cividis',
            'coolwarm': 'coolwarm',
            'RdYlBu_r': 'RdYlBu_r',
            'terrain': 'terrain',
            'hot': 'hot',
            'jet': 'jet',
            'rainbow': 'rainbow',
            'seismic': 'seismic'
        }

        # 七点颜色方案（与 MATLAB 版本一致）
        self.seven_point_colors = [
            (0.0, '#00FFFF'),  # 青色
            (0.1, '#00FF00'),  # 绿色
            (0.3, '#FFFF00'),  # 黄色
            (0.5, '#FF8000'),  # 橙色
            (0.7, '#FF0000'),  # 红色
            (0.9, '#8000FF'),  # 紫色
            (1.0, '#FF00FF')   # 紫红色
        ]
        self.seven_point_cmap = mcolors.LinearSegmentedColormap.from_list(
            'seven_point', self.seven_point_colors
        )

    def plot_image(self, data: np.ndarray, title: str = 'Image',
                  cmap: str = 'viridis', vmin: Optional[float] = None,
                  vmax: Optional[float] = None, colorbar: bool = True,
                  save_path: Optional[Path] = None) -> Figure:
        """
        绘制单张图像

        Args:
            data: 数据数组
            title: 图像标题
            cmap: 颜色方案
            vmin: 最小值
            vmax: 最大值
            colorbar: 是否显示颜色条
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        fig, ax = plt.subplots(figsize=self.figsize)

        # 处理 NaN
        display_data = data.copy()
        if np.any(np.isnan(display_data)):
            display_data = np.ma.masked_invalid(display_data)

        # 绘制图像
        if cmap == 'seven_point':
            im = ax.imshow(display_data, cmap=self.seven_point_cmap,
                          origin='upper', vmin=vmin, vmax=vmax)
        else:
            im = ax.imshow(display_data, cmap=cmap,
                          origin='upper', vmin=vmin, vmax=vmax)

        # 添加颜色条
        if colorbar:
            cbar = plt.colorbar(im, ax=ax, shrink=0.8)
            cbar.set_label(title, fontsize=12)

        # 设置标题
        ax.set_title(title, fontsize=14, fontweight='bold', pad=10)

        # 隐藏坐标轴
        ax.set_xticks([])
        ax.set_yticks([])

        plt.tight_layout()

        # 保存图像
        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"图像已保存: {save_path}")

        return fig

    def plot_multi_image(self, data_dict: Dict[str, np.ndarray],
                        cmap: Optional[str] = None,
                        cols: int = 2,
                        save_path: Optional[Path] = None) -> Figure:
        """
        绘制多张图像

        Args:
            data_dict: 数据字典 {名称: 数组}
            cmap: 统一颜色方案（可选）
            cols: 列数
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        num_images = len(data_dict)
        rows = (num_images + cols - 1) // cols

        fig, axes = plt.subplots(rows, cols, figsize=(self.figsize[0] * cols, self.figsize[1] * rows))

        # 处理单个子图的情况
        if num_images == 1:
            axes = np.array([axes])
        elif rows == 1:
            axes = axes.reshape(1, -1)

        # 绘制每张图像
        for idx, (name, data) in enumerate(data_dict.items()):
            row, col = idx // cols, idx % cols
            ax = axes[row, col]

            # 处理 NaN
            display_data = data.copy()
            if np.any(np.isnan(display_data)):
                display_data = np.ma.masked_invalid(display_data)

            # 选择颜色方案
            if cmap is None:
                plot_cmap = 'viridis'
            else:
                plot_cmap = cmap

            # 绘制
            im = ax.imshow(display_data, cmap=plot_cmap, origin='upper')

            # 标题
            ax.set_title(name, fontsize=12, fontweight='bold')
            ax.set_xticks([])
            ax.set_yticks([])

            # 颜色条
            plt.colorbar(im, ax=ax, shrink=0.8)

        # 隐藏多余的子图
        for idx in range(num_images, rows * cols):
            row, col = idx // cols, idx % cols
            axes[row, col].axis('off')

        plt.tight_layout()

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"多图已保存: {save_path}")

        return fig

    def plot_composite_rgb(self, red: np.ndarray, green: np.ndarray,
                          blue: np.ndarray, title: str = 'RGB Composite',
                          stretch: str = 'linear', percent: float = 2,
                          save_path: Optional[Path] = None) -> Figure:
        """
        绘制 RGB 合成图像

        Args:
            red: 红色波段
            green: 绿色波段
            blue: 蓝色波段
            title: 标题
            stretch: 拉伸方法 ('linear' 或 'percentile')
            percent: 百分位拉伸参数
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        # 处理 NaN
        r = np.nan_to_num(red, nan=0)
        g = np.nan_to_num(green, nan=0)
        b = np.nan_to_num(blue, nan=0)

        # 拉伸
        if stretch == 'percentile':
            for band in [r, g, b]:
                p_low = np.percentile(band, percent)
                p_high = np.percentile(band, 100 - percent)
                band = np.clip((band - p_low) / (p_high - p_low), 0, 1)
        else:
            # 线性拉伸
            r = (r - r.min()) / (r.max() - r.min() + 1e-10)
            g = (g - g.min()) / (g.max() - g.min() + 1e-10)
            b = (b - b.min()) / (b.max() - b.min() + 1e-10)

        # 组合
        rgb = np.dstack((r, g, b))
        rgb = np.clip(rgb, 0, 1)

        fig, ax = plt.subplots(figsize=self.figsize)
        ax.imshow(rgb)
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xticks([])
        ax.set_yticks([])

        plt.tight_layout()

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"RGB 合成图已保存: {save_path}")

        return fig

    def plot_histogram(self, data: np.ndarray, title: str = 'Histogram',
                      bins: int = 100, color: str = 'blue',
                      alpha: float = 0.7, save_path: Optional[Path] = None) -> Figure:
        """
        绘制直方图

        Args:
            data: 数据数组
            title: 标题
            bins: 箱数
            color: 颜色
            alpha: 透明度
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        valid_data = data[~np.isnan(data)].flatten()

        fig, ax = plt.subplots(figsize=self.figsize)

        ax.hist(valid_data, bins=bins, color=color, alpha=alpha, edgecolor='black')
        ax.set_xlabel('Value', fontsize=12)
        ax.set_ylabel('Frequency', fontsize=12)
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"直方图已保存: {save_path}")

        return fig

    def plot_scatter(self, x: np.ndarray, y: np.ndarray,
                    title: str = 'Scatter Plot',
                    xlabel: str = 'X', ylabel: str = 'Y',
                    color: str = 'blue', alpha: float = 0.6,
                    save_path: Optional[Path] = None) -> Figure:
        """
        绘制散点图

        Args:
            x: x 数据
            y: y 数据
            title: 标题
            xlabel: x 轴标签
            ylabel: y 轴标签
            color: 颜色
            alpha: 透明度
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        # 移除 NaN
        valid_mask = ~np.isnan(x) & ~np.isnan(y)
        x_valid = x[valid_mask]
        y_valid = y[valid_mask]

        fig, ax = plt.subplots(figsize=self.figsize)

        ax.scatter(x_valid, y_valid, c=color, alpha=alpha, s=1)
        ax.set_xlabel(xlabel, fontsize=12)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"散点图已保存: {save_path}")

        return fig

    def plot_with_roi(self, data: np.ndarray, roi_polygon,
                     title: str = 'Image with ROI',
                     cmap: str = 'viridis',
                     save_path: Optional[Path] = None) -> Figure:
        """
        绘制带 ROI 的图像

        Args:
            data: 数据数组
            roi_polygon: ROI 多边形
            title: 标题
            cmap: 颜色方案
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        fig, ax = plt.subplots(figsize=self.figsize)

        # 绘制数据
        display_data = data.copy()
        if np.any(np.isnan(display_data)):
            display_data = np.ma.masked_invalid(display_data)

        im = ax.imshow(display_data, cmap=cmap, origin='upper')

        # 绘制 ROI
        if roi_polygon is not None:
            mpl_poly = MPLPolygon(list(roi_polygon.exterior.coords),
                                 edgecolor='red', facecolor='none',
                                 linewidth=2, label='ROI')
            ax.add_patch(mpl_poly)
            ax.legend()

        # 颜色条
        plt.colorbar(im, ax=ax, shrink=0.8)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xticks([])
        ax.set_yticks([])

        plt.tight_layout()

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"ROI 图像已保存: {save_path}")

        return fig

    def plot_comparison(self, data1: np.ndarray, data2: np.ndarray,
                       title1: str = 'Data 1', title2: str = 'Data 2',
                       cmap: str = 'viridis',
                       save_path: Optional[Path] = None) -> Figure:
        """
        绘制对比图

        Args:
            data1: 第一个数据
            data2: 第二个数据
            title1: 第一个标题
            title2: 第二个标题
            cmap: 颜色方案
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(self.figsize[0] * 2, self.figsize[1]))

        # 第一个图
        display1 = data1.copy()
        if np.any(np.isnan(display1)):
            display1 = np.ma.masked_invalid(display1)

        im1 = ax1.imshow(display1, cmap=cmap, origin='upper')
        ax1.set_title(title1, fontsize=14, fontweight='bold')
        ax1.set_xticks([])
        ax1.set_yticks([])
        plt.colorbar(im1, ax=ax1, shrink=0.8)

        # 第二个图
        display2 = data2.copy()
        if np.any(np.isnan(display2)):
            display2 = np.ma.masked_invalid(display2)

        im2 = ax2.imshow(display2, cmap=cmap, origin='upper')
        ax2.set_title(title2, fontsize=14, fontweight='bold')
        ax2.set_xticks([])
        ax2.set_yticks([])
        plt.colorbar(im2, ax=ax2, shrink=0.8)

        plt.tight_layout()

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"对比图已保存: {save_path}")

        return fig

    def create_dashboard(self, results: Dict[str, np.ndarray],
                        title: str = 'Analysis Dashboard',
                        mineral_type: str = 'gold',
                        save_path: Optional[Path] = None) -> Figure:
        """
        创建分析仪表板

        Args:
            results: 结果字典
            title: 仪表板标题
            mineral_type: 矿物类型
            save_path: 保存路径

        Returns:
            matplotlib Figure 对象
        """
        # 确定矿物颜色方案
        mineral_config = Config.get_mineral_config(mineral_type)
        cmap = mineral_config.get('color_scheme', 'viridis')

        # 创建子图布局
        fig = plt.figure(figsize=(20, 16))
        gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

        # 主预测图（占据顶部）
        if 'Au_deep' in results:
            ax_main = fig.add_subplot(gs[0, :])
            display = results['Au_deep'].copy()
            if np.any(np.isnan(display)):
                display = np.ma.masked_invalid(display)

            im = ax_main.imshow(display, cmap=self.seven_point_cmap, origin='upper')
            ax_main.set_title('深部成矿预测图', fontsize=16, fontweight='bold')
            ax_main.set_xticks([])
            ax_main.set_yticks([])
            plt.colorbar(im, ax=ax_main, shrink=0.6, label='预测概率')

        # 其他结果图
        idx = 1
        for name, data in results.items():
            if name == 'Au_deep':
                continue

            if idx >= 9:
                break

            row, col = idx // 3, idx % 3
            ax = fig.add_subplot(gs[row, col])

            display = data.copy()
            if np.any(np.isnan(display)):
                display = np.ma.masked_invalid(display)

            im = ax.imshow(display, cmap=cmap, origin='upper')
            ax.set_title(name, fontsize=12, fontweight='bold')
            ax.set_xticks([])
            ax.set_yticks([])
            plt.colorbar(im, ax=ax, shrink=0.7)

            idx += 1

        fig.suptitle(f'{title} - {mineral_type}', fontsize=18, fontweight='bold')

        if save_path is not None:
            plt.savefig(save_path, dpi=self.dpi, bbox_inches='tight')
            logger.info(f"仪表板已保存: {save_path}")

        return fig

    def close_all(self):
        """关闭所有图像"""
        plt.close('all')
