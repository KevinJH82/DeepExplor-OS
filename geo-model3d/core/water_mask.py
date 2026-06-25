"""water_mask — 水体识别与排除模块。

从多种来源构建水体排除掩码，策略按可靠性优先：

策略 A（最可靠）: 用户手动上传水体掩码 (GeoTIFF/Shapefile)
策略 B: 从 geo-analyser interference_removal 产物中获取（如已持久化）
策略 C: 从蚀变评分栅格间接推断 — 评分=0 的区域且 InSAR 无数据区域交集
策略 D（降级）: 无法构建掩码，不排除

注意：
- 对砂金矿等河床矿种应跳过水体排除（见 mineral_type 参数）
- 策略 C 的精度有限（蚀变=0 可能是正常背景，非水体），仅作为兜底

使用方式：
  1. 用户上传水体掩码 → params["water_mask_path"]
  2. 自动从上游产物推断
"""

from __future__ import annotations

import os
import json
import glob
from typing import Dict, List, Optional, Tuple

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


def build_water_mask(grid, analyser_run_dir: Optional[str],
                     mineral_type: Optional[str] = None,
                     water_mask_path: Optional[str] = None,
                     insar_dir: Optional[str] = None) -> Optional[np.ndarray]:
    """构建水体排除掩码。

    按优先级尝试多种策略，返回第一个成功的掩码。

    Args:
        grid: VoxelGrid 实例，用于空间对齐
        analyser_run_dir: geo-analyser 本次 run 的输出目录
        mineral_type: 矿种类型，砂金等河床矿种自动跳过
        water_mask_path: 用户手动上传的水体掩码路径（GeoTIFF）
        insar_dir: geo-insar 下载目录，用于辅助推断

    Returns:
        (ny, nx) 布尔数组，True=水体。无数据返回 None。
    """
    # 砂金/砂矿跳过水体排除
    if mineral_type and _is_placer(mineral_type):
        logger.info(f"矿种 '{mineral_type}' 为砂矿类型，跳过水体排除")
        return None

    # 策略 A: 用户手动上传的水体掩码
    if water_mask_path and os.path.isfile(water_mask_path):
        mask = _load_uploaded_mask(water_mask_path, grid)
        if mask is not None:
            _log_mask_stats(mask, "策略A(用户上传)")
            return mask

    # 策略 B: 从 geo-analyser interference_removal 产物获取
    if analyser_run_dir:
        mask = _load_analyser_water_mask(analyser_run_dir, grid)
        if mask is not None:
            _log_mask_stats(mask, "策略B(analyser产物)")
            return mask

    # 策略 C: 从蚀变评分 + InSAR 无数据区间接推断
    if analyser_run_dir and insar_dir:
        mask = _infer_water_from_data(analyser_run_dir, insar_dir, grid)
        if mask is not None:
            _log_mask_stats(mask, "策略C(间接推断)")
            return mask

    # 策略 D: 从 HydroSHEDS/HydroRIVERS 公开水系数据获取
    mask = _load_hydrosheds_water_mask(grid)
    if mask is not None:
        _log_mask_stats(mask, "策略D(HydroSHEDS)")
        return mask

    logger.info("水体掩码: 所有策略均未产出有效掩码，跳过水体排除")
    return None


def _is_placer(mineral_type: str) -> bool:
    """判断矿种是否为砂矿/河床矿类型。"""
    placer_keywords = ["砂金", "砂矿", "砂锡", "砂铁", "砂铂", "placer", "alluvial"]
    mt = mineral_type.lower()
    return any(kw in mt for kw in placer_keywords)


def _log_mask_stats(mask: np.ndarray, strategy: str):
    """记录掩码统计信息。"""
    n_water = int(mask.sum())
    n_total = mask.size
    ratio = n_water / n_total if n_total > 0 else 0
    logger.info(f"水体掩码({strategy}): {n_water}/{n_total} 像元 ({ratio*100:.1f}%)")


def _load_uploaded_mask(path: str, grid) -> Optional[np.ndarray]:
    """策略 A: 加载用户上传的水体掩码 GeoTIFF。"""
    try:
        arr = grid.reproject_to_grid(path)  # (ny, nx)
        if arr is None:
            return None
        # 掩码中非零且有限值 = 水体
        mask = np.isfinite(arr) & (arr != 0)
        if mask.sum() == 0:
            logger.info("用户上传的水体掩码全为空")
            return None
        return mask
    except Exception as e:
        logger.warning(f"加载用户水体掩码失败: {e}")
        return None


def _load_analyser_water_mask(analyser_run_dir: str, grid) -> Optional[np.ndarray]:
    """策略 B: 从 geo-analyser 的 interference_removal 产物加载水体掩码。

    检查是否有持久化的 water_mask.tif 或 ndwi.tif 产物。
    当前 geo-analyser 不持久化这些，但保留接口供未来使用。
    """
    rasters_dir = os.path.join(analyser_run_dir, "rasters")
    if not os.path.isdir(rasters_dir):
        return None

    # 查找可能的水体掩膜文件
    water_patterns = ["*water_mask*", "*ndwi*", "*NDWI*", "*mndwi*"]
    for pat in water_patterns:
        matches = glob.glob(os.path.join(rasters_dir, pat))
        if matches:
            try:
                arr = grid.reproject_to_grid(matches[0])
                if arr is not None:
                    return np.isfinite(arr) & (arr > 0)
            except Exception:
                continue

    return None


def _infer_water_from_data(analyser_run_dir: str, insar_dir: str,
                            grid) -> Optional[np.ndarray]:
    """策略 C: 从已有数据间接推断水体。

    利用多源数据的交叉验证：
    1. 读取 InSAR 原始栅格 — 水面 InSAR 常无形变信号（但窄河不一定）
    2. 读取蚀变评分 — 背景区 score=0
    3. 交集：InSAR 无数据 + 评分=0 → 可能是水体

    注意：此策略精度有限，仅作为降级方案。
    """
    # 读取 InSAR 原始 deformation evidence
    insar_tif = os.path.join(insar_dir, "deformation_evidence.tif")
    if not os.path.isfile(insar_tif):
        return None

    try:
        insar_arr = grid.reproject_to_grid(insar_tif)  # (ny, nx)
    except Exception:
        return None

    if insar_arr is None:
        return None

    # InSAR 无数据区域
    insar_nan = ~np.isfinite(insar_arr)
    if insar_nan.sum() == 0:
        # InSAR 在 AOI 内全覆盖 → 无法用 InSAR 区分水体
        logger.info("策略C: InSAR 全覆盖，无法用 NaN 区分水体")
        return None

    # 读取蚀变评分
    score_path = os.path.join(analyser_run_dir, "rasters")
    # 查找 composite score
    score_files = glob.glob(os.path.join(score_path, "*composite*score*.tif"))
    if not score_files:
        return None

    try:
        score_arr = grid.reproject_to_grid(score_files[0])
    except Exception:
        return None

    if score_arr is None:
        return None

    # 评分接近零的区域（背景/无蚀变信号）
    score_zero = np.isfinite(score_arr) & (score_arr <= 0.01)

    # 交集：InSAR 无数据 + 评分零 → 水体候选
    water = insar_nan & score_zero

    if water.sum() == 0:
        logger.info("策略C: InSAR NaN 与 评分零值无交集，无法推断水体")
        return None

    # 安全检查：水体比例不应超过 30%
    ratio = water.sum() / water.size
    if ratio > 0.3:
        logger.warning(f"策略C推断水体比例 {ratio*100:.0f}% 过高，可能不准确，不排除")
        return None

    return water


def _load_hydrosheds_water_mask(grid,
                                 min_order: int = 3) -> Optional[np.ndarray]:
    """策略 D: 从 HydroSHEDS/HydroRIVERS 公开水系数据构建水体掩码。

    数据已预下载到 data/hydrosheds/（91MB 亚洲 Shapefile）。
    自动按 grid 的 bbox 裁剪、按河流等级过滤、生成缓冲区后栅格化。
    """
    try:
        from config.config import Config
        data_dir = getattr(Config, 'HYDROSHEDS_DIR', '')
    except Exception:
        data_dir = ''

    if not data_dir or not os.path.isdir(data_dir):
        logger.debug("HydroSHEDS 数据目录未配置或不存在")
        return None

    try:
        from utils.hydrosheds import build_water_mask_from_hydrosheds
        bbox = list(grid.bbox_wgs84)  # [min_lon, min_lat, max_lon, max_lat]
        mask = build_water_mask_from_hydrosheds(
            grid, bbox, data_dir, min_order=min_order)
        return mask
    except Exception as e:
        logger.info(f"HydroSHEDS 水体掩码构建失败: {e}")
        return None
