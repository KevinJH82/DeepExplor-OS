"""
已知异常检测器

集成 KML/KMZ 已知异常数据，与遥感数据对齐
"""

import os
import numpy as np
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import zipfile
import tempfile
import shutil
from loguru import logger

from .base_detector import GeoDetectorBase
from ..core.base_classes import DetectorResult
from config.config import Config


class KnownAnomalyDetector(GeoDetectorBase):
    """
    已知异常检测器

    集成 KML/KMZ 已知矿点数据，与遥感数据对齐
    """

    def __init__(self, kmz_path: str, params: Optional[Dict[str, Any]] = None):
        """
        初始化已知异常检测器

        Args:
            kmz_path: KMZ 文件路径
            params: 检测器参数
        """
        default_params = {
            'keywords': ['矿体投影', 'Object ID', 'ZK', '异常', '已知矿点'],
            'correction_method': 'geometric',
            'buffer_radius': 100,  # 缓冲区半径（米）
            'use_all_points': True,
            'min_points': 3
        }

        if params:
            default_params.update(params)

        super().__init__('KnownAnomaly', default_params)
        self.kmz_path = Path(kmz_path)
        self.kml_data = None
        self.anomaly_points = None
        self.anomaly_mask = None

        self.config = Config.DETECTORS['known_anomaly']

    def calculate(self, context) -> DetectorResult:
        """
        计算已知异常

        Args:
            context: 地理数据上下文

        Returns:
            检测结果
        """
        self._debug_log("开始计算已知异常...")

        # 验证输入数据
        self._validate_data(context)

        # 加载 KML 数据
        self._load_kml_data()

        # 提取异常点
        self._extract_anomaly_points()

        # 生成异常掩码
        self._generate_anomaly_mask(context)

        self._debug_log(f"已知异常检测完成，有效点数: {len(self.anomaly_points)}")

        return DetectorResult(
            mask=self.anomaly_mask,
            debug_data={
                'anomaly_points': self.anomaly_points,
                'buffer_radius': self.params['buffer_radius'],
                'total_points': len(self.anomaly_points),
                'masked_points': np.sum(self.anomaly_mask) if self.anomaly_mask is not None else 0
            }
        )

    def _load_kml_data(self):
        """加载 KML 数据"""
        if not self.kmz_path.exists():
            raise FileNotFoundError(f"KMZ 文件不存在: {self.kmz_path}")

        self._debug_log("加载 KML 数据...")

        # 解压 KMZ 文件
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # 解压 KMZ
            with zipfile.ZipFile(self.kmz_path, 'r') as zip_ref:
                zip_ref.extractall(temp_path)

            # 查找 KML 文件
            kml_files = list(temp_path.glob('**/*.kml'))
            if not kml_files:
                raise ValueError("KMZ 文件中未找到 .kml 文件")

            # 解析 KML
            kml_file = kml_files[0]
            self.kml_data = self._parse_kml_file(kml_file)

        self._debug_log(f"KML 数据加载完成，找到 {len(self.kml_data.get('points', []))} 个点")

    def _parse_kml_file(self, kml_file: Path) -> Dict[str, Any]:
        """
        解析 KML 文件

        Args:
            kml_file: KML 文件路径

        Returns:
            解析后的数据
        """
        import xml.etree.ElementTree as ET

        # 读取 KML 文件
        with open(kml_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 去除命名空间
        content = content.replace('kml:', '').replace('gx:', '')

        # 解析 XML
        root = ET.fromstring(content)

        # 提取坐标
        coordinates = []
        names = []
        descriptions = []

        # 查找所有 Placemark 元素
        for placemark in root.findall('.//Placemark'):
            # 提取名称
            name_elem = placemark.find('name')
            name = name_elem.text if name_elem is not None else ''

            # 提取描述
            desc_elem = placemark.find('description')
            description = desc_elem.text if desc_elem is not None else ''

            # 提取坐标
            coords_elem = placemark.find('Point/coordinates')
            if coords_elem is not None:
                coords_text = coords_elem.text.strip()
                # 解析坐标格式：经度,纬度,高度
                try:
                    lon, lat, _ = map(float, coords_text.split(','))
                    coordinates.append((lon, lat))
                    names.append(name)
                    descriptions.append(description)
                except ValueError:
                    continue

        # 过滤关键点
        filtered_points = []
        filtered_names = []
        filtered_descriptions = []

        for coord, name, desc in zip(coordinates, names, descriptions):
            # 检查名称或描述是否包含关键词
            text = f"{name} {desc}".lower()
            if any(keyword.lower() in text for keyword in self.params['keywords']):
                filtered_points.append(coord)
                filtered_names.append(name)
                filtered_descriptions.append(desc)

        return {
            'points': filtered_points,
            'names': filtered_names,
            'descriptions': filtered_descriptions,
            'raw_coordinates': coordinates,
            'raw_names': names,
            'raw_descriptions': descriptions
        }

    def _extract_anomaly_points(self):
        """提取异常点"""
        if not self.kml_data or not self.kml_data.get('points'):
            self._debug_log("警告：未找到有效的异常点")
            return

        self.anomaly_points = np.array(self.kml_data['points'])
        self.anomaly_names = self.kml_data['names']
        self.anomaly_descriptions = self.kml_data['descriptions']

        # 如果不使用所有点，进行筛选
        if not self.params['use_all_points']:
            self._filter_anomaly_points()

        # 检查最小点数
        if len(self.anomaly_points) < self.params['min_points']:
            self._debug_log(f"警告：异常点数量不足 ({len(self.anomaly_points)} < {self.params['min_points']})")
            # 可以补充一些点或返回空掩码
            self.anomaly_points = None

    def _filter_anomaly_points(self):
        """
        过滤异常点

        根据特定条件过滤异常点，如：
        - 去除重复点
        - 去除异常值
        - 聚类分析
        """
        if len(self.anomaly_points) < 2:
            return

        # 去重（距离小于 10m 的点视为重复）
        filtered_indices = [0]
        threshold = 10 / 111000  # 10m 转换为度

        for i in range(1, len(self.anomaly_points)):
            is_new = True
            for j in filtered_indices:
                distance = np.linalg.norm(self.anomaly_points[i] - self.anomaly_points[j])
                if distance < threshold:
                    is_new = False
                    break
            if is_new:
                filtered_indices.append(i)

        self.anomaly_points = self.anomaly_points[filtered_indices]
        self.anomaly_names = [self.anomaly_names[i] for i in filtered_indices]
        self.anomaly_descriptions = [self.anomaly_descriptions[i] for i in filtered_indices]

    def _generate_anomaly_mask(self, context):
        """生成异常掩码"""
        if self.anomaly_points is None or len(self.anomaly_points) == 0:
            self.anomaly_mask = np.zeros_like(context.inROI, dtype=float)
            return

        # 创建掩码
        rows, cols = context.inROI.shape
        self.anomaly_mask = np.zeros((rows, cols), dtype=float)

        # 获取坐标网格
        lon_grid, lat_grid = np.meshgrid(context.lonGrid, context.latGrid)

        # 将异常点转换为掩码
        for i, (point_lon, point_lat) in enumerate(self.anomaly_points):
            # 计算每个网格点到异常点的距离
            distances = np.sqrt((lon_grid - point_lon)**2 + (lat_grid - point_lat)**2)

            # 转换缓冲区半径（米）为度
            # 简化处理：假设 1° ≈ 111km
            buffer_deg = self.params['buffer_radius'] / 111000

            # 创建缓冲区掩码
            buffer_mask = distances < buffer_deg

            # 使用高斯衰减
            gaussian_mask = np.exp(-(distances / (buffer_deg/3))**2)

            # 叠加到总掩码
            if i == 0:
                self.anomaly_mask = gaussian_mask * buffer_mask
            else:
                self.anomaly_mask = np.maximum(self.anomaly_mask, gaussian_mask * buffer_mask)

        # 应用 ROI
        self.anomaly_mask = np.where(context.inROI, self.anomaly_mask, 0)

        # 归一化
        if np.max(self.anomaly_mask) > 0:
            self.anomaly_mask = self.anomaly_mask / np.max(self.anomaly_mask)

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            'total_points': len(self.anomaly_points) if self.anomaly_points is not None else 0,
            'filtered_points': len(self.anomaly_names),
            'buffer_radius': self.params['buffer_radius'],
            'keywords': self.params['keywords']
        }

        if self.anomaly_points is not None and len(self.anomaly_points) > 0:
            # 计算边界
            lons, lats = zip(*self.anomaly_points)
            stats['bounds'] = {
                'min_lon': min(lons),
                'max_lon': max(lons),
                'min_lat': min(lats),
                'max_lat': max(lats)
            }

        return stats

    def get_anomaly_points_info(self) -> List[Dict[str, Any]]:
        """获取异常点信息"""
        if self.anomaly_points is None:
            return []

        points_info = []
        for i, (point, name, desc) in enumerate(zip(self.anomaly_points,
                                                  self.anomaly_names,
                                                  self.anomaly_descriptions)):
            points_info.append({
                'id': i,
                'name': name,
                'description': desc,
                'coordinates': point,
                'lon': point[0],
                'lat': point[1]
            })

        return points_info

    def _validate_kml_data(self) -> bool:
        """验证 KML 数据"""
        if self.kml_data is None:
            return False

        if not self.kml_data.get('points'):
            return False

        # 检查坐标格式
        for point in self.kml_data['points']:
            if len(point) != 2 or not (-180 <= point[0] <= 180) or not (-90 <= point[1] <= 90):
                return False

        return True

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        info = super().get_detector_info()
        stats = self.get_statistics()

        info.update({
            'algorithm': 'Known anomaly integration',
            'kmz_path': str(self.kmz_path),
            'correction_method': self.params['correction_method'],
            'statistics': stats,
            'parameters': {
                'buffer_radius': self.params['buffer_radius'],
                'use_all_points': self.params['use_all_points'],
                'min_points': self.params['min_points']
            }
        })
        return info