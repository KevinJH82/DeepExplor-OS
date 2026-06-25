"""
可视化生成工具
"""

import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import matplotlib


# 设置中文字体
matplotlib.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False


class Visualizer:
    """可视化生成器"""

    @staticmethod
    def run_resonance(F_map, delta_red, moran, mask, depth, gradP, freq, RGB, outDir, lonGrid, latGrid):
        """生成共振参数综合图 - 8个子图"""
        lonV = np.linspace(np.min(lonGrid), np.max(lonGrid), F_map.shape[1])
        latV = np.linspace(np.min(latGrid), np.max(latGrid), F_map.shape[0])

        fig, axes = plt.subplots(2, 4, figsize=(28, 10))
        fig.patch.set_facecolor('white')

        data_list = [RGB, F_map, delta_red, moran, mask, depth * 1000, gradP, freq]
        titles = ['RGB', 'F判别', '红边位移', 'Moran I', '综合异常', '深度(m)', '压力', '频率']
        clims = [None, [0, 0.15], [-15, 15], [0, 1], [0, 1], [0, 2000], [0, 40], [10, 100]]
        extent = [lonV[0], lonV[-1], latV[0], latV[-1]]

        for i, (ax, data, title, clim) in enumerate(zip(axes.flat, data_list, titles, clims)):
            img_data = np.flipud(data)

            if i == 0:  # RGB 图像
                rgb_norm = (img_data - np.nanmin(img_data)) / (np.nanmax(img_data) - np.nanmin(img_data) + 1e-10)
                rgb_norm = np.clip(rgb_norm, 0, 1)
                if rgb_norm.ndim == 2:
                    rgb_norm = np.stack([rgb_norm] * 3, axis=-1)
                ax.imshow(rgb_norm, extent=extent, aspect='equal', origin='lower')
            else:
                im = ax.imshow(img_data, extent=extent, aspect='equal', origin='lower')
                ax.set_title(title, fontsize=12, fontweight='bold')
                plt.colorbar(im, ax=ax)

                if clim is not None:
                    im.set_clim(clim)

            ax.grid(True, alpha=0.3)
            ax.set_xlabel('经度', fontsize=9)
            ax.set_ylabel('纬度', fontsize=9)

        plt.tight_layout()
        output_path = os.path.join(outDir, '01_共振参数综合图.png')
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        return output_path

    @staticmethod
    def run_mask_fusion(mask_list, title_list, lonGrid, latGrid, outDir):
        """生成掩码集成图"""
        if len(mask_list) == 0:
            return None

        lonV = np.linspace(np.min(lonGrid), np.max(lonGrid), mask_list[0].shape[1])
        latV = np.linspace(np.min(latGrid), np.max(latGrid), mask_list[0].shape[0])

        # 智能排版 — 每个子图保持地理横向比例
        num_masks = len(mask_list)
        if num_masks <= 3:
            cols = num_masks
            rows = 1
        else:
            cols = 3
            rows = int(np.ceil(num_masks / cols))

        fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 5 * rows + 0.5))
        fig.patch.set_facecolor('white')

        # 自定义 colormap: 白 -> 绿 (匹配 MATLAB Visualizer)
        n_colors = 256
        r = np.linspace(1, 0, n_colors)
        g = np.linspace(1, 0.8, n_colors)
        b = np.linspace(1, 0, n_colors)
        custom_map = LinearSegmentedColormap.from_list('custom', list(zip(r, g, b)), N=n_colors)

        # 如果 axes 是二维数组，展平它
        if num_masks > 1 and rows > 1:
            axes = axes.flatten()
        elif num_masks == 1:
            axes = [axes]

        for i in range(num_masks):
            if i >= len(axes):
                break

            ax = axes[i]
            img_data = np.flipud(mask_list[i])
            img_data = np.nan_to_num(img_data, nan=0)

            im = ax.imshow(img_data, extent=[lonV[0], lonV[-1], latV[0], latV[-1]],
                          aspect='equal', origin='lower', cmap=custom_map, vmin=0, vmax=1)
            ax.set_title(title_list[i], fontsize=12, fontweight='bold')
            ax.set_xlabel('经度', fontsize=9)
            ax.set_ylabel('纬度', fontsize=9)
            ax.grid(True, alpha=0.3)

            if num_masks <= 4 or i % cols == cols - 1 or i == num_masks - 1:
                plt.colorbar(im, ax=ax)

        # 隐藏多余的子图
        for i in range(num_masks, len(axes)):
            axes[i].axis('off')

        plt.tight_layout()
        outName = f'02_掩码集成_{num_masks}图.png'
        output_path = os.path.join(outDir, outName)
        plt.savefig(output_path, dpi=400, bbox_inches='tight')
        plt.close(fig)
        return output_path

    @staticmethod
    def run_deep_prediction(Au, lonG, latG, lonR, latR, lonT, latT, rIdx, mineral, outDir):
        """生成深部成矿预测图 — 严格匹配 MATLAB Visualizer.run_deep_prediction"""
        lonV = np.linspace(np.min(lonG), np.max(lonG), Au.shape[1])
        latV = np.linspace(np.min(latG), np.max(latG), Au.shape[0])

        fig, ax = plt.subplots(figsize=(12, 10))
        fig.patch.set_facecolor('white')

        au_data = np.flipud(Au)
        au_data = np.nan_to_num(au_data, nan=0)

        # MATLAB: contourf(lonV, latV, flipud(Au), 80, 'LineColor', 'none')
        # 减少等值层数 → [0.4, 1]区间内色阶更稀疏 → 峰值颜色更淡
        data_min = float(np.min(au_data))
        data_max = float(np.max(au_data))
        if data_max - data_min < 1e-10:
            data_max = data_min + 1.0
        levels = np.linspace(data_min, data_max, 30)

        cf = ax.contourf(lonV, latV, au_data, levels=levels, cmap='jet',
                         edgecolors='none', linewidths=0)

        # MATLAB: caxis([0.4 1]) — 峰值区域上边界微调，避免最深红
        cf.set_clim(0.4, 0.92)
        plt.colorbar(cf, ax=ax, label='预测概率')

        # MATLAB: contour(..., 0.4:0.05:1, 'LineColor', [0.8 0.8 0.8])
        cs = ax.contour(lonV, latV, au_data, levels=np.arange(0.4, 1.05, 0.05),
                        colors='#CCCCCC', linewidths=0.5)
        ax.clabel(cs, cs.levels[::2], inline=True, fontsize=6, fmt='%.2f')

        # MATLAB: plot(lonR, latR, 'k-', 'LineWidth', 2.5)
        if len(lonR) > 0 and len(lonR) == len(latR):
            ax.plot(lonR, latR, 'k-', linewidth=2.5, label='ROI边界')

        # MATLAB: plot(lonT, latT, 'wo', 'MarkerSize', 10, 'MarkerFaceColor', [0.2 0.2 0.2])
        if len(lonT) > 0:
            ax.plot(lonT, latT, 'wo', markersize=10,
                    markerfacecolor='#333333', markeredgecolor='white',
                    markeredgewidth=1.0, label='预测点')
            # MATLAB: plot(lonT(rIdx), latT(rIdx), 'yo', 'MarkerSize', 18, 'LineWidth', 3)
            if len(rIdx) > 0 and max(rIdx) < len(lonT):
                ax.plot(lonT[rIdx], latT[rIdx], 'yo', markersize=18,
                        markeredgecolor='#CC9900', markeredgewidth=3,
                        markerfacecolor='#FFFF00', label='Top 20')

        ax.set_title(f'Deep Prediction: {mineral.upper()}', fontsize=16, fontweight='bold')
        ax.set_xlabel('经度', fontsize=12)
        ax.set_ylabel('纬度', fontsize=12)
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.set_aspect('equal', adjustable='box')

        plt.tight_layout()
        output_path = os.path.join(outDir, '03_深部成矿预测图.png')
        plt.savefig(output_path, dpi=500, bbox_inches='tight')
        plt.close(fig)
        return output_path

    # Phase 1.5: InSAR 形变图(双向 diverging colormap)
    @staticmethod
    def plot_insar_deformation(velocity, coherence, lonV, latV, outDir,
                                title='InSAR LOS 形变速率',
                                clip_pct=2.0):
        """
        生成 InSAR 形变速率图,使用 RdBu_r 双向 colormap(红=沉降,蓝=抬升)。

        Parameters
        ----------
        velocity : 2D ndarray, mm/year
        coherence : 2D ndarray 或 None, 0-1 用于掩膜(< 0.3 显示为透明)
        lonV, latV : 1D ndarray, 经纬度网格
        outDir : 输出目录
        clip_pct : 颜色刻度截断百分比(默认 2%,即 vmin=p2, vmax=p98)
        """
        import os
        v = np.asarray(velocity, dtype=np.float32)
        if coherence is not None:
            v = np.where(np.asarray(coherence) >= 0.3, v, np.nan)

        # 对称的双向截断,让 0 永远在 colormap 中心
        finite = v[np.isfinite(v)]
        if finite.size == 0:
            return None
        lo = np.percentile(finite, clip_pct)
        hi = np.percentile(finite, 100 - clip_pct)
        vmax = max(abs(lo), abs(hi))
        vmin = -vmax

        fig, ax = plt.subplots(figsize=(10, 8))
        fig.patch.set_facecolor('white')
        im = ax.imshow(
            v,
            extent=[lonV[0], lonV[-1], latV[0], latV[-1]],
            aspect='equal', origin='lower',
            cmap='RdBu_r', vmin=vmin, vmax=vmax,
        )
        cb = plt.colorbar(im, ax=ax, label='LOS 形变速率 (mm/year)')
        cb.ax.set_facecolor('white')
        ax.set_title(title)
        ax.set_xlabel('经度 (°)')
        ax.set_ylabel('纬度 (°)')
        ax.text(0.02, 0.02,
                f'红=沉降  蓝=抬升  vmin/vmax=±{vmax:.2f} mm/yr  相干性 ≥ 0.3 掩膜',
                transform=ax.transAxes, fontsize=9, color='#555',
                bbox=dict(facecolor='white', alpha=0.7, edgecolor='none'))
        plt.tight_layout()
        output_path = os.path.join(outDir, '04_InSAR形变速率.png')
        plt.savefig(output_path, dpi=400, bbox_inches='tight')
        plt.close(fig)
        return output_path
