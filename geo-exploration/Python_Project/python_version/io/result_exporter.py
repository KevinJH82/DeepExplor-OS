"""
结果导出器

支持多种格式的结果导出
"""

import os
import json
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import zipfile
from loguru import logger

from scipy.io import savemat
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


class ResultExporter:
    """
    结果导出器类

    支持导出为 numpy、MAT 文件、JSON、PNG、KMZ 等格式
    """

    def __init__(self, output_dir: str):
        """
        初始化结果导出器

        Args:
            output_dir: 输出目录
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.exported_files = []

    def export_numpy(self, data: Dict[str, np.ndarray],
                    prefix: str = '') -> List[str]:
        """
        导出为 numpy 格式

        Args:
            data: 数据字典
            prefix: 文件名前缀

        Returns:
            导出的文件列表
        """
        files = []
        for name, array in data.items():
            file_path = self.output_dir / f'{prefix}{name}.npy'
            np.save(file_path, array)
            files.append(str(file_path))

        self.exported_files.extend(files)
        logger.info(f"已导出 {len(files)} 个 numpy 文件")
        return files

    def export_mat(self, data: Dict[str, np.ndarray],
                   filename: str = 'results.mat') -> str:
        """
        导出为 MATLAB .mat 文件

        Args:
            data: 数据字典
            filename: 文件名

        Returns:
            导出的文件路径
        """
        # 准备要保存的数据（移除 NaN）
        mat_data = {}
        for name, array in data.items():
            mat_data[name] = np.nan_to_num(array, nan=0)

        file_path = self.output_dir / filename
        savemat(str(file_path), mat_data)

        self.exported_files.append(str(file_path))
        logger.info(f"已导出 MAT 文件: {file_path}")
        return str(file_path)

    def export_json(self, data: Dict[str, Any],
                   filename: str = 'metadata.json') -> str:
        """
        导出为 JSON 格式

        Args:
            data: 数据字典
            filename: 文件名

        Returns:
            导出的文件路径
        """
        file_path = self.output_dir / filename

        # 转换 numpy 类型为 Python 原生类型
        json_data = self._convert_to_json_serializable(data)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        self.exported_files.append(str(file_path))
        logger.info(f"已导出 JSON 文件: {file_path}")
        return str(file_path)

    def export_image(self, data: np.ndarray, title: str,
                    filename: str = None, cmap: str = 'viridis',
                    dpi: int = 300, figsize: Tuple[int, int] = (12, 10)) -> str:
        """
        导出为图像文件

        Args:
            data: 数据数组
            title: 图像标题
            filename: 文件名（可选）
            cmap: 颜色方案
            dpi: 分辨率
            figsize: 图像大小

        Returns:
            导出的文件路径
        """
        if filename is None:
            filename = f'{title}.png'.replace(' ', '_')

        file_path = self.output_dir / filename

        fig, ax = plt.subplots(figsize=figsize)

        # 绘制图像
        im = ax.imshow(data, cmap=cmap, origin='upper')

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
        plt.savefig(str(file_path), dpi=dpi, bbox_inches='tight')
        plt.close()

        self.exported_files.append(str(file_path))
        logger.info(f"已导出图像: {file_path}")
        return str(file_path)

    def export_images_batch(self, data_dict: Dict[str, np.ndarray],
                           cmap_map: Optional[Dict[str, str]] = None) -> List[str]:
        """
        批量导出图像

        Args:
            data_dict: 数据字典 {名称: 数组}
            cmap_map: 颜色方案映射（可选）

        Returns:
            导出的文件列表
        """
        if cmap_map is None:
            cmap_map = {}

        files = []
        for name, data in data_dict.items():
            cmap = cmap_map.get(name, 'viridis')
            file_path = self.export_image(data, name, cmap=cmap)
            files.append(file_path)

        return files

    def export_kmz(self, data: np.ndarray, lon_grid: np.ndarray,
                   lat_grid: np.ndarray, title: str = 'Prediction',
                   filename: str = 'result.kmz', cmap: str = 'viridis',
                   threshold: float = None) -> str:
        """
        导出为 Google Earth KMZ 文件

        Args:
            data: 数据数组
            lon_grid: 经度网格
            lat_grid: 纬度网格
            title: 图层名称
            filename: 文件名
            cmap: 颜色方案
            threshold: 显示阈值（可选）

        Returns:
            导出的文件路径
        """
        # 创建临时目录
        kmz_temp = self.output_dir / 'kmz_temp'
        kmz_temp.mkdir(exist_ok=True)

        # 生成 PNG 图像
        png_file = kmz_temp / 'overlay.png'
        self._save_kmz_image(data, png_file, cmap, threshold)

        # 生成 KML 内容
        kml_content = self._create_kml_content(
            data, lon_grid, lat_grid, title, threshold
        )

        # 保存 KML 文件
        kml_file = kmz_temp / 'doc.kml'
        with open(kml_file, 'w', encoding='utf-8') as f:
            f.write(kml_content)

        # 打包为 KMZ
        kmz_file = self.output_dir / filename
        with zipfile.ZipFile(str(kmz_file), 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in kmz_temp.iterdir():
                if file.is_file():
                    arcname = 'doc.kml' if file.name == 'doc.kml' else file.name
                    zipf.write(str(file), arcname)

        # 清理临时目录
        import shutil
        shutil.rmtree(kmz_temp)

        self.exported_files.append(str(kmz_file))
        logger.info(f"已导出 KMZ 文件: {kmz_file}")
        return str(kmz_file)

    def _save_kmz_image(self, data: np.ndarray, file_path: Path,
                       cmap: str = 'viridis', threshold: float = None):
        """保存 KMZ 叠加图像"""
        from matplotlib.colors import LinearSegmentedColormap

        # 七点颜色方案（与 MATLAB 版本一致）
        colors = [
            (0.0, '#00FFFF'),  # 青色
            (0.1, '#00FF00'),  # 绿色
            (0.3, '#FFFF00'),  # 黄色
            (0.5, '#FF8000'),  # 橙色
            (0.7, '#FF0000'),  # 红色
            (0.9, '#8000FF'),  # 紫色
            (1.0, '#FF00FF')   # 紫红色
        ]

        custom_cmap = LinearSegmentedColormap.from_list('seven_point', colors)

        fig, ax = plt.subplots(figsize=(10, 10))
        im = ax.imshow(data, cmap=custom_cmap, origin='upper',
                      vmin=0 if threshold is None else threshold,
                      vmax=1)
        ax.set_xticks([])
        ax.set_yticks([])
        plt.axis('off')
        plt.tight_layout(pad=0)
        plt.savefig(str(file_path), dpi=100, bbox_inches='tight',
                   pad_inches=0, transparent=True)
        plt.close()

    def _create_kml_content(self, data: np.ndarray, lon_grid: np.ndarray,
                           lat_grid: np.ndarray, title: str,
                           threshold: float) -> str:
        """创建 KML 内容"""
        # 计算边界
        if len(lon_grid.shape) == 1:
            lon_min, lon_max = lon_grid.min(), lon_grid.max()
            lat_min, lat_max = lat_grid.min(), lat_grid.max()
        else:
            lon_min, lon_max = lon_grid.min(), lon_grid.max()
            lat_min, lat_max = lat_grid.min(), lat_grid.max()

        # 如果提供了阈值，计算百分比
        if threshold is not None:
            valid_data = data[~np.isnan(data)]
            if len(valid_data) > 0:
                percent = (valid_data > threshold).sum() / len(valid_data) * 100
            else:
                percent = 0
        else:
            percent = 100

        kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{title}</name>
    <description>舒曼波共振遥感矿产预测结果</description>

    <GroundOverlay>
      <name>{title}</name>
      <description>预测结果（显示前 {percent:.1f}%）</description>
      <Icon>
        <href>overlay.png</href>
      </Icon>
      <LatLonBox>
        <north>{lat_max:.6f}</north>
        <south>{lat_min:.6f}</south>
        <east>{lon_max:.6f}</east>
        <west>{lon_min:.6f}</west>
        <rotation>0</rotation>
      </LatLonBox>
    </GroundOverlay>

    <ScreenOverlay>
      <name>Legend</name>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/pal4/icon57.png</href>
      </Icon>
      <overlayXY x="0" y="1" xunits="fraction" yunits="fraction"/>
      <screenXY x="0" y="1" xunits="fraction" yunits="fraction"/>
      <size x="0" y="0" xunits="fraction" yunits="fraction"/>
    </ScreenOverlay>
  </Document>
</kml>'''

        return kml

    def export_report(self, results: Dict[str, Any],
                     template: str = None) -> str:
        """
        导出分析报告

        Args:
            results: 结果字典
            template: 报告模板路径（可选）

        Returns:
            导出的文件路径
        """
        file_path = self.output_dir / 'analysis_report.txt'

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("="*70 + "\n")
            f.write("舒曼波共振遥感矿产预测分析报告\n")
            f.write("="*70 + "\n\n")

            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            # 基本信息
            if 'mineral_type' in results:
                f.write(f"目标矿种: {results['mineral_type']}\n")
            if 'data_dir' in results:
                f.write(f"数据目录: {results['data_dir']}\n")
            f.write("\n")

            # 统计信息
            if 'statistics' in results:
                f.write("统计信息:\n")
                f.write("-"*70 + "\n")
                for name, stats in results['statistics'].items():
                    f.write(f"\n{name}:\n")
                    if isinstance(stats, dict):
                        for key, value in stats.items():
                            f.write(f"  {key}: {value:.4f}\n")
                f.write("\n")

            # 生成的文件
            f.write("生成的文件:\n")
            f.write("-"*70 + "\n")
            for file in self.exported_files:
                f.write(f"  - {Path(file).name}\n")

            f.write("\n" + "="*70 + "\n")

        self.exported_files.append(str(file_path))
        logger.info(f"已导出报告: {file_path}")
        return str(file_path)

    def _convert_to_json_serializable(self, obj: Any) -> Any:
        """转换为 JSON 可序列化格式"""
        if isinstance(obj, dict):
            return {k: self._convert_to_json_serializable(v)
                   for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_to_json_serializable(item)
                   for item in obj]
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, (np.integer, np.floating)):
            return float(obj)
        elif isinstance(obj, Path):
            return str(obj)
        else:
            return obj

    def get_exported_files(self) -> List[str]:
        """获取已导出的文件列表"""
        return self.exported_files.copy()

    def clear_exported_files(self):
        """清除已导出文件列表"""
        self.exported_files.clear()
