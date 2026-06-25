"""image_utils — 地质图像预处理工具集。

提供图像加载、预处理、颜色分析、图例检测等功能。
目标是帮助从地质勘查图（JPG/PNG）中提取可量化的信息。

主要功能：
- 图像加载与基础预处理
- 颜色量化和主色调提取
- 图例区域检测
- 颜色映射推断
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image
import cv2


@dataclass
class ColorLegend:
    """颜色图例：颜色值 → 数值/类别的映射。"""
    color_rgb: Tuple[int, int, int]      # RGB 像素值
    value: Union[float, str]             # 对应的数值或类别标签
    label: Optional[str] = None           # 图例文字说明


@dataclass
class ImageMetadata:
    """图像元数据：包含尺寸、模式、主色调等信息。"""
    width: int
    height: int
    mode: str
    dominant_colors: List[Tuple[int, int, int]] = field(default_factory=list)
    color_counts: List[int] = field(default_factory=list)
    legend_regions: List[Dict] = field(default_factory=list)


def load_image(path: str, mode: str = "RGB") -> np.ndarray:
    """加载图像为 NumPy 数组。

    Args:
        path: 图像路径
        mode: "RGB", "RGBA" 或 "L" (灰度)

    Returns:
        图像数组 (H, W, C) 对于 RGB/RGBA，(H, W) 对于灰度
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"图像不存在: {path}")

    # 增加最大像素限制以处理大图像（地质图通常分辨率很高）
    Image.MAX_IMAGE_PIXELS = None

    img = Image.open(path)
    if mode == "RGB":
        if img.mode != "RGB":
            img = img.convert("RGB")
        return np.array(img, dtype=np.uint8)
    elif mode == "RGBA":
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        return np.array(img, dtype=np.uint8)
    elif mode == "L":
        if img.mode != "L":
            img = img.convert("L")
        return np.array(img, dtype=np.uint8)
    else:
        raise ValueError(f"不支持的图像模式: {mode}")


def denoise_image(img: np.ndarray, method: str = "bilateral") -> np.ndarray:
    """图像去噪。

    Args:
        img: 输入图像 (H, W, C)
        method: "bilateral" (保边) 或 "gaussian"

    Returns:
        去噪后的图像
    """
    if method == "bilateral":
        # 双边滤波保边，适合地质图的等值线
        if len(img.shape) == 3:
            return cv2.bilateralFilter(img, d=9, sigmaColor=75, sigmaSpace=75)
        else:
            return cv2.bilateralFilter(img, d=9, sigmaColor=75, sigmaSpace=75)
    elif method == "gaussian":
        if len(img.shape) == 3:
            return cv2.GaussianBlur(img, (5, 5), 0)
        else:
            return cv2.GaussianBlur(img, (5, 5), 0)
    else:
        return img


def enhance_contrast(img: np.ndarray, method: str = "clahe") -> np.ndarray:
    """增强对比度。

    Args:
        img: 输入图像
        method: "clahe" (自适应直方图均衡) 或 "normalize"

    Returns:
        增强后的图像
    """
    if method == "clahe":
        if len(img.shape) == 3:
            # 转换到 Lab 空间，只增强 L 通道
            lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            l = clahe.apply(l)
            lab = cv2.merge([l, a, b])
            return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        else:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            return clahe.apply(img)
    elif method == "normalize":
        return cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    else:
        return img


def extract_dominant_colors(img: np.ndarray, n_colors: int = 16) -> List[Tuple[int, int, int]]:
    """使用 K-means 提取图像主色调。

    Args:
        img: RGB 图像 (H, W, 3)
        n_colors: 提取的颜色数量

    Returns:
        主色调列表，按像素数量降序排列
    """
    if len(img.shape) != 3 or img.shape[2] != 3:
        raise ValueError("输入必须是 RGB 图像")

    # 重塑为 (H*W, 3)
    pixels = img.reshape(-1, 3).astype(np.float32)

    # K-means 聚类
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
    _, labels, centers = cv2.kmeans(pixels, n_colors, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)

    # 统计每个簇的像素数量
    unique, counts = np.unique(labels, return_counts=True)
    sorted_indices = np.argsort(counts)[::-1]

    # 返回按数量排序的颜色
    dominant_colors = []
    for idx in sorted_indices:
        rgb = tuple(int(c) for c in centers[idx])
        dominant_colors.append(rgb)

    return dominant_colors


def detect_legend_regions(img: np.ndarray, method: str = "corner") -> List[Dict]:
    """检测图例区域。

    地质图的图例通常位于图的角落，有规则的矩形布局。

    Args:
        img: 输入图像
        method: "corner" (检测角落矩形), "contour" (轮廓检测)

    Returns:
        图例区域列表，每个包含 bbox (x, y, w, h) 和置信度
    """
    h, w = img.shape[:2]
    regions = []

    if method == "corner":
        # 检测四个角落的矩形区域
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if len(img.shape) == 3 else img

        # 使用形态学操作检测矩形
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)

        # 边缘检测
        edges = cv2.Canny(closed, 50, 150)

        # 轮廓检测
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            # 过滤小轮廓
            area = cv2.contourArea(cnt)
            if area < 1000:
                continue

            # 多边形近似
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)

            # 检查是否为四边形
            if len(approx) == 4:
                x, y, bw, bh = cv2.boundingRect(cnt)

                # 检查是否在角落
                margin = w * 0.1
                is_corner = (
                    (x < margin and y < margin) or  # 左上
                    (x + bw > w - margin and y < margin) or  # 右上
                    (x < margin and y + bh > h - margin) or  # 左下
                    (x + bw > w - margin and y + bh > h - margin)  # 右下
                )

                if is_corner:
                    regions.append({
                        "bbox": (int(x), int(y), int(bw), int(bh)),
                        "confidence": float(area) / (w * h),
                        "polygon": approx.tolist()
                    })

        # 去重：合并重叠区域
        regions = _merge_overlapping_regions(regions)

    return regions


def _merge_overlapping_regions(regions: List[Dict], iou_threshold: float = 0.5) -> List[Dict]:
    """合并重叠的图例区域。"""
    if not regions:
        return []

    # 按置信度排序
    regions = sorted(regions, key=lambda r: r["confidence"], reverse=True)

    merged = [regions[0]]
    for r in regions[1:]:
        # 检查与已合并区域的重叠
        overlap = False
        for m in merged:
            if _calculate_iou(r["bbox"], m["bbox"]) > iou_threshold:
                overlap = True
                break
        if not overlap:
            merged.append(r)

    return merged


def _calculate_iou(bbox1: Tuple, bbox2: Tuple) -> float:
    """计算两个边界框的 IoU。"""
    x1, y1, w1, h1 = bbox1
    x2, y2, w2, h2 = bbox2

    # 交集
    xi1, yi1 = max(x1, x2), max(y1, y2)
    xi2, yi2 = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
    inter_w, inter_h = max(0, xi2 - xi1), max(0, yi2 - yi1)
    intersection = inter_w * inter_h

    # 并集
    union = w1 * h1 + w2 * h2 - intersection

    return intersection / union if union > 0 else 0.0


def infer_color_legend(img: np.ndarray, legend_region: Optional[Dict] = None,
                        n_colors: int = 16) -> List[ColorLegend]:
    """从图像推断颜色图例。

    策略：
    1. 如果指定了图例区域，从该区域提取颜色
    2. 否则，从全图提取主色调
    3. 尝试识别图例中的颜色条（颜色 + 文字对）

    Args:
        img: 输入图像
        legend_region: 可选的图例区域 bbox
        n_colors: 提取的颜色数量

    Returns:
        ColorLegend 列表
    """
    if legend_region:
        x, y, w, h = legend_region["bbox"]
        legend_img = img[y:y+h, x:x+w]
    else:
        legend_img = img

    # 提取主色调
    colors = extract_dominant_colors(legend_img, n_colors)

    # 这里先返回简单的颜色列表，实际应用中需要：
    # 1. 识别图例布局（通常是垂直或水平的颜色条）
    # 2. 对每个颜色条提取代表色
    # 3. OCR 识别对应的文字（可选）
    # 4. 建立颜色 → 数值映射

    legends = []
    for i, rgb in enumerate(colors):
        # 假设图例按颜色出现频率排序，高频率 = 高值
        # 这是简化假设，实际需要更智能的推断
        legends.append(ColorLegend(
            color_rgb=rgb,
            value=float(i) / (len(colors) - 1) if len(colors) > 1 else 0.5,
            label=f"level_{i}"
        ))

    return legends


def create_color_mask(img: np.ndarray, target_color: Tuple[int, int, int],
                      tolerance: int = 30) -> np.ndarray:
    """创建特定颜色的掩码。

    Args:
        img: RGB 图像
        target_color: 目标颜色 (R, G, B)
        tolerance: 颜色容差 (0-255)

    Returns:
        二值掩码，同颜色区域为 1
    """
    if len(img.shape) != 3:
        raise ValueError("输入必须是 RGB 图像")

    lower = np.array([max(0, c - tolerance) for c in target_color], dtype=np.uint8)
    upper = np.array([min(255, c + tolerance) for c in target_color], dtype=np.uint8)

    mask = cv2.inRange(img, lower, upper)
    return mask.astype(np.uint8)


def segment_by_color(img: np.ndarray, legends: List[ColorLegend],
                     tolerance: int = 30) -> Dict[str, np.ndarray]:
    """按图例颜色分割图像。

    Args:
        img: RGB 图像
        legends: 颜色图例列表
        tolerance: 颜色容差

    Returns:
        字典，key 为标签，value 为掩码数组
    """
    segments = {}
    for legend in legends:
        mask = create_color_mask(img, legend.color_rgb, tolerance)
        label = legend.label or f"color_{legend.color_rgb}"
        segments[label] = mask

    return segments


def detect_grid_lines(img: np.ndarray, orientation: str = "both") -> Dict:
    """检测图像中的网格线（经纬度网格、坐标网格）。

    Args:
        img: 输入图像
        orientation: "horizontal", "vertical" 或 "both"

    Returns:
        包含水平线和垂直线位置的字典
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if len(img.shape) == 3 else img

    # 自适应阈值
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 11, 2)

    lines = {"horizontal": [], "vertical": []}

    if orientation in ("horizontal", "both"):
        # 检测水平线
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
        h_contours, _ = cv2.findContours(horizontal, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in h_contours:
            if cv2.contourArea(cnt) > 100:
                x, y, w, h = cv2.boundingRect(cnt)
                lines["horizontal"].append(int(y + h/2))

    if orientation in ("vertical", "both"):
        # 检测垂直线
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
        v_contours, _ = cv2.findContours(vertical, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in v_contours:
            if cv2.contourArea(cnt) > 100:
                x, y, w, h = cv2.boundingRect(cnt)
                lines["vertical"].append(int(x + w/2))

    # 去重并排序
    lines["horizontal"] = sorted(set(lines["horizontal"]))
    lines["vertical"] = sorted(set(lines["vertical"]))

    return lines


def save_preprocessed(img: np.ndarray, output_path: str):
    """保存预处理后的图像。"""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    Image.fromarray(img).save(output_path)
    return output_path


def analyze_image(path: str, output_dir: Optional[str] = None) -> ImageMetadata:
    """分析图像，提取元数据和主色调。

    Args:
        path: 图像路径
        output_dir: 可选输出目录

    Returns:
        ImageMetadata 对象
    """
    img = load_image(path)
    h, w = img.shape[:2]

    # 提取主色调
    colors = extract_dominant_colors(img, n_colors=16)

    # 检测图例区域
    legend_regions = detect_legend_regions(img, method="corner")

    metadata = ImageMetadata(
        width=w,
        height=h,
        mode="RGB" if len(img.shape) == 3 else "L",
        dominant_colors=colors,
        legend_regions=legend_regions
    )

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        # 保存元数据
        meta_path = os.path.join(output_dir, "image_metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "width": w,
                "height": h,
                "dominant_colors": [list(c) for c in colors],
                "legend_regions": legend_regions
            }, f, indent=2)

    return metadata
