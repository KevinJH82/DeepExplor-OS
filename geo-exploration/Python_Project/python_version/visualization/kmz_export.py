"""
KMZ 导出器

生成 Google Earth 可视化的 KMZ 文件
"""

import os
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union
import zipfile
import tempfile
from loguru import logger

import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap


class KMZExporter:
    """
    KMZ 导出器类

    生成 Google Earth 可视化的 KMZ 文件
    """

    def __init__(self, dpi: int = 100):
        """
        初始化 KMZ 导出器

        Args:
            dpi: 图像分辨率
        """
        self.dpi = dpi

        # 七点颜色方案（与 MATLAB 版本一致）
        self.colors = [
            (0.0, '#00FFFF'),  # 青色
            (0.1, '#00FF00'),  # 绿色
            (0.3, '#FFFF00'),  # 黄色
            (0.5, '#FF8000'),  # 橙色
            (0.7, '#FF0000'),  # 红色
            (0.9, '#8000FF'),  # 紫色
            (1.0, '#FF00FF')   # 紫红色
        ]
        self.cmap = LinearSegmentedColormap.from_list('seven_point', self.colors)

    def export(self, data: np.ndarray, lon_grid: np.ndarray,
              lat_grid: np.ndarray, output_path: str,
              title: str = 'Mineral Prediction',
              description: str = 'Schumann Resonance Remote Sensing Mineral Prediction',
              threshold: Optional[float] = None,
              show_legend: bool = True) -> str:
        """
        导出为 KMZ 文件

        Args:
            data: 数据数组
            lon_grid: 经度网格
            lat_grid: 纬度网格
            output_path: 输出文件路径
            title: 图层标题
            description: 图层描述
            threshold: 显示阈值（可选）
            show_legend: 是否显示图例

        Returns:
            导出的文件路径
        """
        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # 生成叠加图像
            overlay_file = temp_path / 'overlay.png'
            self._create_overlay_image(data, overlay_file, threshold)

            # 生成图例图像
            legend_file = None
            if show_legend:
                legend_file = temp_path / 'legend.png'
                self._create_legend_image(legend_file)

            # 生成 KML 文件
            kml_content = self._create_kml(
                data, lon_grid, lat_grid, title, description,
                threshold, show_legend
            )

            kml_file = temp_path / 'doc.kml'
            with open(kml_file, 'w', encoding='utf-8') as f:
                f.write(kml_content)

            # 打包为 KMZ
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(kml_file, 'doc.kml')
                zipf.write(overlay_file, 'overlay.png')

                if legend_file is not None:
                    zipf.write(legend_file, 'legend.png')

        logger.info(f"KMZ 文件已导出: {output_path}")
        return str(output_path)

    def _create_overlay_image(self, data: np.ndarray, output_path: Path,
                             threshold: Optional[float] = None):
        """创建叠加图像"""
        fig, ax = plt.subplots(figsize=(10, 10))

        # 设置显示范围
        if threshold is not None:
            vmin = threshold
            vmax = 1.0
        else:
            vmin = 0.0
            vmax = 1.0

        # 绘制图像
        im = ax.imshow(data, cmap=self.cmap, origin='upper',
                      vmin=vmin, vmax=vmax)

        # 移除所有装饰
        ax.set_xticks([])
        ax.set_yticks([])
        ax.axis('off')

        # 保存
        plt.tight_layout(pad=0)
        plt.savefig(output_path, dpi=self.dpi, bbox_inches='tight',
                   pad_inches=0, transparent=True)
        plt.close()

    def _create_legend_image(self, output_path: Path):
        """创建图例图像"""
        fig, ax = plt.subplots(figsize=(2, 6))

        # 创建渐变条
        gradient = np.linspace(0, 1, 256).reshape(1, -1)
        ax.imshow(gradient, aspect='auto', cmap=self.cmap, extent=[0, 1, 0, 1])

        # 设置标签
        ax.set_yticks([0, 0.1, 0.3, 0.5, 0.7, 0.9, 1])
        ax.set_yticklabels(['低', '', '', '中', '', '', '高'])
        ax.set_xticks([])

        # 标题
        ax.text(0.5, 1.05, '成矿概率',
               ha='center', va='bottom', fontsize=10, fontweight='bold',
               transform=ax.transAxes)

        plt.tight_layout()
        plt.savefig(output_path, dpi=self.dpi, bbox_inches='tight',
                   transparent=True)
        plt.close()

    def _create_kml(self, data: np.ndarray, lon_grid: np.ndarray,
                   lat_grid: np.ndarray, title: str, description: str,
                   threshold: Optional[float] = None,
                   show_legend: bool = True) -> str:
        """创建 KML 内容"""
        # 计算边界
        if len(lon_grid.shape) == 1:
            lon_min, lon_max = lon_grid.min(), lon_grid.max()
            lat_min, lat_max = lat_grid.min(), lat_grid.max()
        else:
            lon_min, lon_max = lon_grid.min(), lon_grid.max()
            lat_min, lat_max = lat_grid.min(), lat_grid.max()

        # 计算中心点
        lon_center = (lon_min + lon_max) / 2
        lat_center = (lat_min + lat_max) / 2

        # 计算百分比
        if threshold is not None:
            valid_data = data[~np.isnan(data)]
            if len(valid_data) > 0:
                percent = (valid_data > threshold).sum() / len(valid_data) * 100
            else:
                percent = 0
        else:
            percent = 100

        # 图例 KML（如果启用）
        legend_kml = ''
        if show_legend:
            legend_kml = f'''
    <ScreenOverlay>
      <name>Legend</name>
      <visibility>1</visibility>
      <Icon>
        <href>legend.png</href>
      </Icon>
      <overlayXY x="0" y="1" xunits="fraction" yunits="fraction"/>
      <screenXY x="0.02" y="0.98" xunits="fraction" yunits="fraction"/>
      <rotationXY x="0" y="0" xunits="fraction" yunits="fraction"/>
      <size x="0" y="0" xunits="fraction" yunits="fraction"/>
    </ScreenOverlay>'''

        # 构建 KML
        kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <name>{title}</name>
    <description><![CDATA[{description}]]></description>

    <Style id="overlayStyle">
      <BalloonStyle>
        <text>$[description]</text>
      </BalloonStyle>
    </Style>

    <Region>
      <LatLonAltBox>
        <north>{lat_max:.6f}</north>
        <south>{lat_min:.6f}</south>
        <east>{lon_max:.6f}</east>
        <west>{lon_min:.6f}</west>
      </LatLonAltBox>
      <Lod>
        <minLodPixels>128</minLodPixels>
        <maxLodPixels>-1</maxLodPixels>
      </Lod>
    </Region>

    <GroundOverlay>
      <name>{title}</name>
      <description><![CDATA[
        <h3>{title}</h3>
        <p><b>描述:</b> {description}</p>
        <p><b>显示阈值:</b> {threshold if threshold is not None else '无'}</p>
        <p><b>显示范围:</b> 前 {percent:.1f}%</p>
        <p><b>中心坐标:</b> {lat_center:.4f}, {lon_center:.4f}</p>
        <p><b>边界范围:</b></p>
        <ul>
          <li>北: {lat_max:.4f}</li>
          <li>南: {lat_min:.4f}</li>
          <li>东: {lon_max:.4f}</li>
          <li>西: {lon_min:.4f}</li>
        </ul>
      ]]></description>
      <styleUrl>#overlayStyle</styleUrl>
      <Icon>
        <href>overlay.png</href>
        <viewBoundScale>0.75</viewBoundScale>
      </Icon>
      <LatLonBox>
        <north>{lat_max:.6f}</north>
        <south>{lat_min:.6f}</south>
        <east>{lon_max:.6f}</east>
        <west>{lon_min:.6f}</west>
        <rotation>0</rotation>
      </LatLonBox>
    </GroundOverlay>
{legend_kml}

    <LookAt>
      <longitude>{lon_center:.6f}</longitude>
      <latitude>{lat_center:.6f}</latitude>
      <altitude>0</altitude>
      <heading>0</heading>
      <tilt>0</tilt>
      <range>{(lat_max - lat_min) * 111000 * 2}</range>
      <altitudeMode>clampToGround</altitudeMode>
    </LookAt>
  </Document>
</kml>'''

        return kml

    def export_multiple(self, data_dict: Dict[str, np.ndarray],
                       lon_grid: np.ndarray, lat_grid: np.ndarray,
                       output_dir: str, title_prefix: str = 'Mineral_',
                       description_template: str = '{name} Prediction',
                       threshold: Optional[float] = None) -> List[str]:
        """
        批量导出多个数据为 KMZ 文件

        Args:
            data_dict: 数据字典
            lon_grid: 经度网格
            lat_grid: 纬度网格
            output_dir: 输出目录
            title_prefix: 标题前缀
            description_template: 描述模板
            threshold: 显示阈值

        Returns:
            导出的文件列表
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        files = []
        for name, data in data_dict.items():
            output_path = output_dir / f'{title_prefix}{name}.kmz'
            title = f'{title_prefix}{name}'
            description = description_template.format(name=name)

            file_path = self.export(
                data, lon_grid, lat_grid, str(output_path),
                title=title, description=description,
                threshold=threshold
            )
            files.append(file_path)

        logger.info(f"已导出 {len(files)} 个 KMZ 文件")
        return files

    def export_with_contours(self, data: np.ndarray,
                            lon_grid: np.ndarray, lat_grid: np.ndarray,
                            output_path: str,
                            contour_levels: Optional[List[float]] = None,
                            title: str = 'Mineral Prediction with Contours',
                            description: str = 'Prediction with Contour Lines',
                            threshold: Optional[float] = None) -> str:
        """
        导出带等高线的 KMZ 文件

        Args:
            data: 数据数组
            lon_grid: 经度网格
            lat_grid: 纬度网格
            output_path: 输出文件路径
            contour_levels: 等高线级别
            title: 标题
            description: 描述
            threshold: 显示阈值

        Returns:
            导出的文件路径
        """
        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # 生成叠加图像（带等高线）
            overlay_file = temp_path / 'overlay.png'
            self._create_overlay_with_contours(
                data, lon_grid, lat_grid, overlay_file,
                contour_levels, threshold
            )

            # 生成 KML 文件
            kml_content = self._create_kml(
                data, lon_grid, lat_grid, title, description, threshold
            )

            kml_file = temp_path / 'doc.kml'
            with open(kml_file, 'w', encoding='utf-8') as f:
                f.write(kml_content)

            # 打包为 KMZ
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(kml_file, 'doc.kml')
                zipf.write(overlay_file, 'overlay.png')

        logger.info(f"带等高线的 KMZ 文件已导出: {output_path}")
        return str(output_path)

    def _create_overlay_with_contours(self, data: np.ndarray,
                                     lon_grid: np.ndarray, lat_grid: np.ndarray,
                                     output_path: Path,
                                     contour_levels: Optional[List[float]],
                                     threshold: Optional[float]):
        """创建带等高线的叠加图像"""
        fig, ax = plt.subplots(figsize=(10, 10))

        # 设置显示范围
        if threshold is not None:
            vmin = threshold
            vmax = 1.0
        else:
            vmin = 0.0
            vmax = 1.0

        # 绘制图像
        im = ax.imshow(data, cmap=self.cmap, origin='upper',
                      vmin=vmin, vmax=vmax)

        # 绘制等高线
        if contour_levels is None:
            contour_levels = [0.3, 0.5, 0.7, 0.9]

        # 创建经纬度网格用于等高线
        lon_2d, lat_2d = np.meshgrid(lon_grid, lat_grid)

        ax.contour(lon_2d, lat_2d, data, levels=contour_levels,
                  colors='black', linewidths=0.5, alpha=0.5)

        # 移除所有装饰
        ax.set_xticks([])
        ax.set_yticks([])
        ax.axis('off')

        # 保存
        plt.tight_layout(pad=0)
        plt.savefig(output_path, dpi=self.dpi, bbox_inches='tight',
                   pad_inches=0, transparent=True)
        plt.close()

    @staticmethod
    def get_kmz_color(value: float) -> str:
        """
        获取指定值的颜色（七点颜色方案）

        Args:
            value: 值（0-1）

        Returns:
            颜色（十六进制）
        """
        colors = [
            (0.0, '#00FFFF'),
            (0.1, '#00FF00'),
            (0.3, '#FFFF00'),
            (0.5, '#FF8000'),
            (0.7, '#FF0000'),
            (0.9, '#8000FF'),
            (1.0, '#FF00FF')
        ]

        # 找到对应的颜色区间
        for i in range(len(colors) - 1):
            if colors[i][0] <= value <= colors[i + 1][0]:
                return colors[i][1]

        return colors[-1][1]
