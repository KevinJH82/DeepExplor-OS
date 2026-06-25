"""image_parser — 从地质图像解析结构化数据的核心模块。

实现从 JPG/PNG 地质图到 GeoTIFF 的完整解析流程：
1. 图像预处理和图例识别
2. 颜色分割和矢量化
3. 地理参考重建
4. GeoTIFF 输出

主要类:
- ImageParser: 主解析器，编排完整流程
- GeoReferencer: 地理参考重建
- GeoTIFFWriter: GeoTIFF 输出
"""

from __future__ import annotations

import os
import sys
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union
from pathlib import Path

import numpy as np
from PIL import Image

# 添加 utils 到路径
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from utils.logger import get_logger
from utils.image_utils import (
    load_image, denoise_image, enhance_contrast,
    extract_dominant_colors, detect_legend_regions, infer_color_legend,
    create_color_mask, segment_by_color, detect_grid_lines,
    save_preprocessed, analyze_image, ColorLegend
)

logger = get_logger(__name__)


@dataclass
class GCP:
    """地面控制点：连接像素坐标和地理坐标。"""
    pixel_x: float      # 图像像素坐标 X
    pixel_y: float      # 图像像素坐标 Y
    lon: float          # 经度
    lat: float          # 纬度
    label: Optional[str] = None  # 控制点标签 (如 "NE_corner")


@dataclass
class ParseConfig:
    """图像解析配置。"""
    input_image: str                    # 输入图像路径
    output_dir: str                     # 输出目录
    mineral_type: str = "gold"          # 矿种类型
    crs: str = "EPSG:4326"              # 坐标系 (WGS84)
    grid_tolerance: float = 0.05       # 网格检测容差
    color_tolerance: int = 30           # 颜色分割容差
    n_legend_colors: int = 16           # 图例颜色数量
    # 可选的控制点（如果图像上有经纬度网格）
    gcps: List[GCP] = field(default_factory=list)
    # 可选的边界坐标
    bounds: Optional[Tuple[float, float, float, float]] = None  # (min_lon, min_lat, max_lon, max_lat)


class GeoReferencer:
    """地理参考重建器。

    支持多种方法建立像素坐标与地理坐标的映射：
    1. 输入控制点 (GCPs) + 仿射变换
    2. 输入边界坐标 + 线性映射
    3. 自动检测网格线 + 手动标注坐标值
    """

    def __init__(self, image_width: int, image_height: int):
        self.width = image_width
        self.height = image_height
        self.transform = None  # 仿射变换矩阵 (3x3)
        self.gcps: List[GCP] = []
        self.bounds = None

    def set_gcps(self, gcps: List[GCP]) -> None:
        """设置地面控制点。"""
        self.gcps = gcps
        if len(gcps) >= 3:
            self._compute_affine_transform()

    def set_bounds(self, bounds: Tuple[float, float, float, float]) -> None:
        """设置边界坐标 (min_lon, min_lat, max_lon, max_lat)。

        假设图像均匀覆盖边界框。
        """
        self.bounds = bounds
        min_lon, min_lat, max_lon, max_lat = bounds
        # 简单的线性映射
        self.transform = self._linear_transform(min_lon, min_lat, max_lon, max_lat)

    def _linear_transform(self, min_lon: float, min_lat: float,
                         max_lon: float, max_lat: float) -> np.ndarray:
        """计算线性变换矩阵 (假设图像均匀覆盖)。"""
        # 像素到地理的线性映射
        # lon = min_lon + (x / width) * (max_lon - min_lon)
        # lat = max_lat - (y / height) * (max_lat - min_lat)  # y 向下为正

        d_lon = (max_lon - min_lon) / self.width
        d_lat = (max_lat - min_lat) / self.height

        # 仿射矩阵 (2x3): [a, b, c; d, e, f]
        # geo_x = a * pixel_x + b * pixel_y + c
        # geo_y = d * pixel_x + e * pixel_y + f
        transform = np.array([
            [d_lon, 0, min_lon],
            [0, -d_lat, max_lat]
        ])
        return transform

    def _compute_affine_transform(self) -> None:
        """从 GCPs 计算仿射变换矩阵。"""
        if len(self.gcps) < 3:
            logger.warning("GCPs 数量不足，无法计算仿射变换")
            return

        # 构建线性方程组
        # 使用最小二乘法拟合仿射变换
        pixel_coords = np.array([[g.pixel_x, g.pixel_y, 1] for g in self.gcps])
        geo_x = np.array([g.lon for g in self.gcps])
        geo_y = np.array([g.lat for g in self.gcps])

        # 解 ax + by + c = geo_x, dx + ey + f = geo_y
        try:
            params_x, _, _, _ = np.linalg.lstsq(pixel_coords, geo_x, rcond=None)
            params_y, _, _, _ = np.linalg.lstsq(pixel_coords, geo_y, rcond=None)

            self.transform = np.array([params_x, params_y])
            logger.info(f"仿射变换矩阵: {self.transform}")
        except Exception as e:
            logger.error(f"计算仿射变换失败: {e}")

    def pixel_to_geo(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        """将像素坐标转换为地理坐标。"""
        if self.transform is None:
            return None

        geo = self.transform @ np.array([x, y, 1])
        return float(geo[0]), float(geo[1])

    def geo_to_pixel(self, lon: float, lat: float) -> Optional[Tuple[float, float]]:
        """将地理坐标转换为像素坐标。"""
        if self.transform is None:
            return None

        # 解线性方程
        a, b, c = self.transform[0]
        d, e, f = self.transform[1]

        # geo_x = a*x + b*y + c
        # geo_y = d*x + e*y + f
        # => 求解 x, y

        det = a * e - b * d
        if abs(det) < 1e-10:
            return None

        x = (e * (lon - c) - b * (lat - f)) / det
        y = (a * (lat - f) - d * (lon - c)) / det

        return float(x), float(y)

    def estimate_bounds_from_grid(self, grid_info: Dict, known_coords: Dict) -> Optional[Tuple[float, float, float, float]]:
        """从网格线推断边界。

        Args:
            grid_info: detect_grid_lines 的输出
            known_coords: 已知坐标，如 {"top_left": (lon, lat), "bottom_right": (lon, lat)}

        Returns:
            (min_lon, min_lat, max_lon, max_lat) 或 None
        """
        # 实现略：需要根据具体网格标注推断
        # 简化版：如果提供了两个角点的坐标
        if "top_left" in known_coords and "bottom_right" in known_coords:
            tl = known_coords["top_left"]
            br = known_coords["bottom_right"]
            return (tl[0], br[1], br[0], tl[1])
        return None


class GeoTIFFWriter:
    """GeoTIFF 写入器。

    将数值数组写入带地理参考的 GeoTIFF 文件。
    """

    def __init__(self, crs: str = "EPSG:4326"):
        self.crs = crs

    def write(self, data: np.ndarray, output_path: str,
              transform: np.ndarray, nodata: float = np.nan) -> str:
        """写入 GeoTIFF 文件。

        Args:
            data: 数值数组 (H, W) 或 (H, W, C)
            output_path: 输出文件路径
            transform: 仿射变换矩阵 (2x3)
            nodata: 无数据值

        Returns:
            输出文件路径
        """
        try:
            import rasterio
            from rasterio.transform import Affine
        except ImportError:
            logger.error("rasterio 未安装，无法写入 GeoTIFF")
            raise

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # 处理数据维度
        if len(data.shape) == 2:
            height, width = data.shape
            count = 1
        elif len(data.shape) == 3:
            count, height, width = data.shape
        else:
            raise ValueError(f"不支持的数据维度: {data.shape}")

        # 转换变换矩阵
        affine = Affine(
            transform[0, 0], transform[0, 1], transform[0, 2],
            transform[1, 0], transform[1, 1], transform[1, 2]
        )

        # 处理 NaN
        if np.isnan(nodata):
            dtype = rasterio.float32
            data = np.where(np.isfinite(data), data, -9999)
            nodata = -9999
        else:
            dtype = rasterio.float32

        # 写入
        with rasterio.open(
            output_path, 'w',
            driver='GTiff',
            height=height,
            width=width,
            count=count,
            dtype=dtype,
            crs=self.crs,
            transform=affine,
            nodata=nodata,
            compress='lzw'
        ) as dst:
            if count == 1:
                dst.write(data, 1)
            else:
                for i in range(count):
                    dst.write(data[i], i + 1)

        logger.info(f"GeoTIFF 已写入: {output_path}")
        return output_path


class ImageParser:
    """地质图像解析器。

    完整流程：
    1. 加载图像
    2. 预处理和图例识别
    3. 颜色分割
    4. 地理参考
    5. GeoTIFF 输出
    """

    def __init__(self, config: ParseConfig):
        self.config = config
        self.img = None
        self.preprocessed = None
        self.legends: List[ColorLegend] = []
        self.georeferencer: Optional[GeoReferencer] = None
        self.metadata = {}

        os.makedirs(config.output_dir, exist_ok=True)

    def load(self) -> None:
        """加载图像。"""
        logger.info(f"加载图像: {self.config.input_image}")
        self.img = load_image(self.config.input_image)
        self.metadata["image_shape"] = self.img.shape

    def preprocess(self) -> None:
        """预处理图像。"""
        logger.info("预处理图像...")
        # 去噪
        denoised = denoise_image(self.img, method="bilateral")
        # 增强对比度
        self.preprocessed = enhance_contrast(denoised, method="clahe")

        # 保存预处理结果
        prep_path = os.path.join(self.config.output_dir, "preprocessed.png")
        save_preprocessed(self.preprocessed, prep_path)
        logger.info(f"预处理图像已保存: {prep_path}")

    def analyze_legend(self) -> None:
        """分析图例。"""
        logger.info("分析图例...")
        # 检测图例区域
        legend_regions = detect_legend_regions(self.preprocessed, method="corner")
        self.metadata["legend_regions"] = legend_regions

        # 选择最佳图例区域（如果有多个）
        best_region = None
        if legend_regions:
            best_region = max(legend_regions, key=lambda r: r["confidence"])
            logger.info(f"检测到图例区域: {best_region['bbox']}, 置信度: {best_region['confidence']:.2f}")

        # 推断颜色图例
        self.legends = infer_color_legend(
            self.preprocessed,
            legend_region=best_region,
            n_colors=self.config.n_legend_colors
        )

        # 保存图例信息
        legend_path = os.path.join(self.config.output_dir, "color_legend.json")
        with open(legend_path, "w", encoding="utf-8") as f:
            legend_data = []
            for leg in self.legends:
                legend_data.append({
                    "color_rgb": list(leg.color_rgb),
                    "value": leg.value,
                    "label": leg.label
                })
            json.dump(legend_data, f, indent=2)
        logger.info(f"颜色图例已保存: {legend_path}")

    def setup_georeference(self) -> None:
        """设置地理参考。"""
        logger.info("设置地理参考...")
        h, w = self.img.shape[:2]
        self.georeferencer = GeoReferencer(w, h)

        # 优先使用 GCPs
        if self.config.gcps:
            self.georeferencer.set_gcps(self.config.gcps)
            logger.info(f"使用 {len(self.config.gcps)} 个控制点")
        # 其次使用边界
        elif self.config.bounds:
            self.georeferencer.set_bounds(self.config.bounds)
            logger.info(f"使用边界: {self.config.bounds}")
        else:
            logger.warning("没有提供地理参考信息，将使用像素坐标")
            # 使用图像边界作为默认
            self.georeferencer.set_bounds((0, 0, w, h))

    def parse_colors_to_values(self) -> np.ndarray:
        """将图像颜色转换为数值数组。"""
        logger.info("将颜色转换为数值...")
        h, w = self.preprocessed.shape[:2]
        values = np.zeros((h, w), dtype=np.float32)

        # 对每个像素，找到最接近的图例颜色
        img_flat = self.preprocessed.reshape(-1, 3)
        values_flat = values.reshape(-1)

        # 构建颜色查找表
        legend_colors = np.array([leg.color_rgb for leg in self.legends])
        legend_values = np.array([leg.value for leg in self.legends])

        # 对每个像素，找到最近的颜色
        from scipy.spatial import cKDTree
        tree = cKDTree(legend_colors)
        # 批量查询（分块避免内存问题）
        chunk_size = 10000
        for i in range(0, len(img_flat), chunk_size):
            chunk = img_flat[i:i+chunk_size]
            distances, indices = tree.query(chunk)
            values_flat[i:i+chunk_size] = legend_values[indices]

        return values

    def write_geotiff(self, data: np.ndarray, name: str = "parsed") -> str:
        """写入 GeoTIFF。"""
        if self.georeferencer is None or self.georeferencer.transform is None:
            logger.warning("没有地理参考，使用默认变换")
            h, w = data.shape
            transform = np.array([
                [1.0, 0.0, 0.0],
                [0.0, -1.0, h]
            ])
        else:
            transform = self.georeferencer.transform

        writer = GeoTIFFWriter(crs=self.config.crs)
        output_path = os.path.join(self.config.output_dir, f"{name}.tif")
        return writer.write(data, output_path, transform)

    def save_metadata(self) -> None:
        """保存解析元数据。"""
        metadata_path = os.path.join(self.config.output_dir, "metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)
        logger.info(f"元数据已保存: {metadata_path}")

    def parse(self) -> str:
        """执行完整解析流程。"""
        self.load()
        self.preprocess()
        self.analyze_legend()
        self.setup_georeference()

        # 将颜色转换为数值
        values = self.parse_colors_to_values()

        # 写入 GeoTIFF
        output_tif = self.write_geotiff(values, name="geochem_anomaly")

        # 保存元数据
        self.save_metadata()

        logger.info(f"解析完成！输出: {output_tif}")
        return output_tif


def parse_geochem_image(config: ParseConfig) -> str:
    """解析地球化学异常图的便捷函数。

    这是针对"地球浅钻组合元素数据异常图"的专用解析函数。

    Args:
        config: 解析配置

    Returns:
        输出 GeoTIFF 路径
    """
    parser = ImageParser(config)
    return parser.parse()


def parse_csamt_image(config: ParseConfig) -> str:
    """解析 CSAMT 反演成果图。

    CSAMT 图通常包含等值线，需要不同的处理策略。

    Args:
        config: 解析配置

    Returns:
        输出 GeoTIFF 路径
    """
    # TODO: 实现等值线提取
    raise NotImplementedError("CSAMT 解析待实现")


def parse_section_image(config: ParseConfig) -> str:
    """解析剖面图。

    剖面图包含深度信息，需要特殊处理。

    Args:
        config: 解析配置

    Returns:
        输出 GeoTIFF 路径
    """
    # TODO: 实现剖面解析
    raise NotImplementedError("剖面图解析待实现")
