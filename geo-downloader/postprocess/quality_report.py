"""
数据质量分析模块
扫描 delivery/{area}/ 目录下所有 GeoTIFF 文件，从分辨率、波段数、
文件大小、nodata 比例、统计元数据、CRS 等维度生成质量分析报告，
并给出问题发现与修复方向。

被 report.py 调用，结果作为第六章写入 docx 报告。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── 各传感器期望波段数 ────────────────────────────────────────────────
_EXPECTED_BANDS: Dict[str, int] = {
    "Sentinel 2 L2":  13,   # B01-B12 + B8A（B09/B10 在 L2A 中通常不输出，见下方检查逻辑）
    "Landsat 8 L2":   11,
    "Landsat 9 L2":   11,
    "Landsat 7 ETM+":  6,   # SLC-off 通常 B1-B5,B7（B8 全色单独计）
    "ASTER L2":       14,   # B1-B14
    "ASTER L1T":      14,
    "DEM":             1,
    "SRTM":            1,
}

# Sentinel-2 全部 13 个波段名（B09/B10 在 L2A 中为可选）
_S2_ALL_BANDS = [
    "B01", "B02", "B03", "B04", "B05", "B06", "B07",
    "B08", "B8A", "B09", "B10", "B11", "B12",
]

# 文件大小异常阈值（字节）
_MIN_FILE_BYTES = 50 * 1024        # < 50 KB → 极可能截断
_WARN_FILE_BYTES = 200 * 1024      # < 200 KB → 疑似过小

# nodata 比例警告阈值
_NODATA_WARN_RATIO = 0.30          # > 30% 无效像素 → 警告
_NODATA_ERROR_RATIO = 0.80         # > 80% 无效像素 → 严重问题

# 分辨率容差（像素尺寸与期望值相差超过此比例则报告）
_RES_TOLERANCE = 0.15


@dataclass
class FileQuality:
    """单个文件的质量指标"""
    path: Path
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    bands: int = 0
    res_x_m: float = 0.0          # 像素宽（米）
    res_y_m: float = 0.0          # 像素高（米）
    crs_name: str = ""
    has_statistics: bool = False   # GeoTIFF STATISTICS_ 元数据是否存在
    nodata_ratio: float = 0.0      # 无效像素比例 0-1
    issues: List[str] = field(default_factory=list)
    fixes: List[str] = field(default_factory=list)
    issue_keys: List[str] = field(default_factory=list)   # 不含数值的问题分类key，用于跨文件合并


@dataclass
class SensorQuality:
    """一个传感器子目录的质量汇总"""
    sensor_label: str
    season: str
    files: List[FileQuality] = field(default_factory=list)
    missing_bands: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    fixes: List[str] = field(default_factory=list)


def _pixel_res_m(transform, crs) -> Tuple[float, float]:
    """从 rasterio transform + CRS 计算像素尺寸（米）"""
    try:
        from rasterio.crs import CRS as RioCRS
        px = math.sqrt(transform.a ** 2 + transform.b ** 2)
        py = math.sqrt(transform.d ** 2 + transform.e ** 2)
        if crs and crs.is_geographic:
            px *= 111320.0
            py *= 111320.0
        return px, py
    except Exception:
        return 0.0, 0.0


def _nodata_ratio(dataset) -> float:
    """估算第一个波段的 nodata 像素比例（采样 ≤ 1024×1024）"""
    try:
        import numpy as np
        band = dataset.read(1, out_shape=(
            min(dataset.height, 512),
            min(dataset.width, 512)
        ), resampling=__import__("rasterio.enums", fromlist=["Resampling"]).Resampling.nearest)
        nd = dataset.nodata
        if nd is not None:
            invalid = np.sum(band == nd)
        else:
            # 没有 nodata 元数据：检查全零（常见截断特征）
            if band.dtype in (
                __import__("numpy").float32, __import__("numpy").float64
            ):
                invalid = np.sum(~np.isfinite(band))
            else:
                invalid = 0
        total = band.size
        return float(invalid) / total if total > 0 else 0.0
    except Exception:
        return 0.0


def analyze_file(tif_path: Path) -> FileQuality:
    """分析单个 GeoTIFF 文件，返回质量指标"""
    fq = FileQuality(path=tif_path)
    try:
        fq.size_bytes = tif_path.stat().st_size
    except OSError:
        fq.issues.append("无法读取文件大小")
        return fq

    # 文件大小检查
    if fq.size_bytes < _MIN_FILE_BYTES:
        fq.issues.append(f"文件极小（{fq.size_bytes // 1024} KB），疑似下载截断")
        fq.fixes.append("重新下载该文件；检查下载时网络是否中断")
        fq.issue_keys.append("truncated")
    elif fq.size_bytes < _WARN_FILE_BYTES:
        fq.issues.append(f"文件偏小（{fq.size_bytes // 1024} KB），需核查内容完整性")
        fq.fixes.append("确认研究区面积是否极小，或重新下载验证")
        fq.issue_keys.append("small_file")

    try:
        import rasterio
    except ImportError:
        return fq

    try:
        with rasterio.open(tif_path) as src:
            fq.width = src.width
            fq.height = src.height
            fq.bands = src.count
            fq.crs_name = src.crs.to_string() if src.crs else "无 CRS"
            fq.res_x_m, fq.res_y_m = _pixel_res_m(src.transform, src.crs)
            fq.nodata_ratio = _nodata_ratio(src)

            # 检查 STATISTICS 元数据
            # _write_statistics() 将统计写在波段级 tag（src.tags(band_idx)），
            # 而非文件级 tag（src.tags()），需逐波段检查
            fq.has_statistics = any(
                k.startswith("STATISTICS_")
                for i in range(1, src.count + 1)
                for k in src.tags(i)
            )

            # ── 问题检测 ──────────────────────────────────────────
            # 像素尺寸过小（可能 CRS 未投影，单位仍是度）
            if 0 < fq.res_x_m < 0.5:
                fq.issues.append(
                    f"像素尺寸异常小（{fq.res_x_m:.4f} m），CRS 可能仍为地理坐标度单位"
                )
                fq.fixes.append("检查 clip.py/mosaic.py 输出 CRS，确保重投影到投影坐标系")
                fq.issue_keys.append("bad_crs_geo")

            # 像素尺寸超大（可能为未裁剪全景）
            if fq.res_x_m > 1000:
                fq.issues.append(
                    f"像素尺寸异常大（{fq.res_x_m:.0f} m），可能未正确重采样"
                )
                fq.fixes.append("检查 resample_to_resolution() 参数是否正确")
                fq.issue_keys.append("bad_res_large")

            # 像素数极少（裁剪后太小）
            if 0 < fq.width < 10 or 0 < fq.height < 10:
                fq.issues.append(
                    f"影像尺寸过小（{fq.width}×{fq.height} px），ROI 可能未与影像重叠"
                )
                fq.fixes.append("确认 KML 边界与影像覆盖范围是否正确重叠；检查 CRS 轴序")
                fq.issue_keys.append("tiny_pixels")

            # nodata 比例
            if fq.nodata_ratio > _NODATA_ERROR_RATIO:
                fq.issues.append(
                    f"无效像素占比 {fq.nodata_ratio:.0%}，数据几乎全为空值"
                )
                fq.fixes.append(
                    "重新搜索该传感器数据；检查云量阈值设置；或扩大搜索时间范围"
                )
                fq.issue_keys.append("nodata_severe")
            elif fq.nodata_ratio > _NODATA_WARN_RATIO:
                fq.issues.append(
                    f"无效像素占比 {fq.nodata_ratio:.0%}，覆盖质量偏低"
                )
                fq.fixes.append("考虑拼接多景数据或降低云量阈值重新搜索")
                fq.issue_keys.append("nodata_warn")

            # 缺少统计元数据（导致 QGIS/ArcGIS 预览全黑）
            if not fq.has_statistics:
                fq.issues.append("缺少 STATISTICS 元数据（QGIS 预览可能显示全黑）")
                fq.fixes.append(
                    "运行 fix_tiff_statistics.py 补写统计元数据，"
                    "或在打包流程中确保 _write_statistics() 正常执行"
                )
                fq.issue_keys.append("no_statistics")

            # CRS 缺失
            if not src.crs:
                fq.issues.append("文件缺少坐标参考系（CRS）")
                fq.fixes.append("检查 clip.py 是否正确写入 CRS；对 SAR 产品检查 pyroSAR 输出")
                fq.issue_keys.append("no_crs")

    except Exception as e:
        fq.issues.append(f"无法用 rasterio 打开文件：{e}")
        fq.fixes.append("确认文件完整性；尝试重新下载或重新打包")
        fq.issue_keys.append("open_error")

    return fq


def analyze_sensor_dir(sensor_dir: Path, season: str) -> SensorQuality:
    """分析一个传感器子目录（如 'Sentinel 2 L2'）"""
    sq = SensorQuality(sensor_label=sensor_dir.name, season=season)

    tif_files = sorted(
        f for f in sensor_dir.iterdir()
        if f.is_file() and f.suffix.lower() in (".tif", ".tiff")
    )

    for f in tif_files:
        fq = analyze_file(f)

        # Sentinel-2 B09/B10 在 L2A 中全为0属正常，过滤误报
        if sensor_dir.name == "Sentinel 2 L2" and f.stem.upper() in ("B09", "B10"):
            _s2_known_empty = {"nodata_severe", "truncated"}
            fq.issues = [iss for iss, key in zip(fq.issues, fq.issue_keys)
                         if key not in _s2_known_empty]
            fq.fixes  = [fix for fix, key in zip(fq.fixes,  fq.issue_keys)
                         if key not in _s2_known_empty]
            fq.issue_keys = [key for key in fq.issue_keys
                             if key not in _s2_known_empty]

        # ASTER TIR 波段（B10-B14）：90m 原始分辨率，小区域下体积天然偏小，不视为截断
        # ASTER 全部波段（B1-B14）：倾斜轨道裁剪导致边缘 nodata 天然偏高（约 40%），不报警
        if sensor_dir.name in ("ASTER L2", "ASTER L1T"):
            _aster_tir_suppress = {"truncated", "small_file"} if f.stem.upper() in (
                "B10", "B11", "B12", "B13", "B14"
            ) else set()
            _aster_suppress = _aster_tir_suppress | {"nodata_warn"}
            fq.issues = [iss for iss, key in zip(fq.issues, fq.issue_keys)
                         if key not in _aster_suppress]
            fq.fixes  = [fix for fix, key in zip(fq.fixes,  fq.issue_keys)
                         if key not in _aster_suppress]
            fq.issue_keys = [key for key in fq.issue_keys
                             if key not in _aster_suppress]

        # EMIT / EnMAP：多波段高光谱单文件，小区域裁剪后压缩体积天然偏小
        if sensor_dir.name in ("EMIT L2A", "EnMAP L2A"):
            _hyperspectral_suppress = {"truncated", "small_file"}
            fq.issues = [iss for iss, key in zip(fq.issues, fq.issue_keys)
                         if key not in _hyperspectral_suppress]
            fq.fixes  = [fix for fix, key in zip(fq.fixes,  fq.issue_keys)
                         if key not in _hyperspectral_suppress]
            fq.issue_keys = [key for key in fq.issue_keys
                             if key not in _hyperspectral_suppress]

        sq.files.append(fq)

    # 检查期望波段数（文件数 ≈ 波段数，每个文件单波段）
    expected = _EXPECTED_BANDS.get(sensor_dir.name)
    if expected and len(tif_files) < expected:
        # Sentinel-2 L2A 中 B09/B10 天然缺失属正常：
        #   B10（卷云）仅用于大气校正，不在 L2A 交付产品中输出；
        #   B09（水汽）部分区域/处理器也不输出。
        #   实际最多 11 个波段，预期 13 需对应调整。
        if sensor_dir.name == "Sentinel 2 L2":
            present_stems = {f.stem.upper() for f in tif_files}
            missing = [b for b in _S2_ALL_BANDS if b not in present_stems]
            _s2_optional = {"B09", "B10"}
            real_missing = [b for b in missing if b not in _s2_optional]
            if real_missing:
                sq.issues.append(
                    f"波段文件数量不足：缺少 {', '.join(real_missing)}"
                )
                sq.fixes.append(
                    "检查下载的 SAFE 包是否完整；对应波段可重新下载补全"
                )
        else:
            sq.issues.append(
                f"波段文件数量不足：预期 {expected} 个，实际 {len(tif_files)} 个"
            )
            sq.fixes.append(
                "检查下载日志中是否有波段下载失败；"
                "对 Landsat 可尝试手动补全缺失波段"
            )

    # 分辨率一致性检查（同一传感器各波段分辨率应相同）
    # 已知多分辨率传感器：ASTER（15/30/90m）、Landsat 8/9（30m + 100m TIR）
    # 、Landsat 7（30m + 60m TIR）、Sentinel-2（10/20/60m）
    _MULTI_RES_SENSORS = {
        "ASTER L2", "ASTER L1T",
        "Landsat 8 L2", "Landsat 9 L2", "Landsat 7 ETM+",
        "Sentinel 2 L2",
    }
    if sensor_dir.name not in _MULTI_RES_SENSORS:
        res_set = set()
        for fq in sq.files:
            if fq.res_x_m > 0:
                res_set.add(round(fq.res_x_m, 1))
        if len(res_set) > 1:
            sq.issues.append(
                f"波段间分辨率不一致：{sorted(res_set)} m，"
                "叠加计算时可能出现形状不匹配错误"
            )
            sq.fixes.append(
                "在 package.py 中统一 resample_to_resolution() 目标分辨率"
            )

    return sq


def analyze_delivery(delivery_dir: Path) -> List[SensorQuality]:
    """
    扫描整个 delivery/{area}/ 目录，返回所有传感器的质量分析结果。
    同时分析散落在 season_dir 根目录的单文件（DEM.tif、地表温度.tif 等）。
    """
    results: List[SensorQuality] = []
    delivery_dir = Path(delivery_dir)

    season_dirs = [
        d for d in delivery_dir.iterdir()
        if d.is_dir() and d.name.startswith("data-")
    ]
    season_dirs.sort()

    for season_dir in season_dirs:
        season_label = season_dir.name

        # 传感器子目录
        for entry in sorted(season_dir.iterdir()):
            if entry.is_dir():
                sq = analyze_sensor_dir(entry, season_label)
                if sq.files or sq.issues:
                    results.append(sq)

        # 根目录散落文件（DEM.tif、地表温度.tif、OTCI.tiff 等）
        loose_files = [
            f for f in season_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".tif", ".tiff")
        ]
        if loose_files:
            sq = SensorQuality(sensor_label="衍生产品 / DEM", season=season_label)
            for f in sorted(loose_files):
                fq = analyze_file(f)
                # 衍生产品（OTCI、温度梯度等）体积天然偏小，不套用遥感波段的大小阈值
                fq.issues    = [iss for iss, key in zip(fq.issues, fq.issue_keys) if key != "small_file"]
                fq.fixes     = [fix for fix, key in zip(fq.fixes,  fq.issue_keys) if key != "small_file"]
                fq.issue_keys = [key for key in fq.issue_keys if key != "small_file"]
                sq.files.append(fq)
            results.append(sq)

    return results


# ── 汇总统计 ──────────────────────────────────────────────────────────

def summarize(results: List[SensorQuality]) -> Dict:
    """返回全局质量汇总统计字典"""
    total_files = sum(len(sq.files) for sq in results)
    total_size = sum(
        fq.size_bytes for sq in results for fq in sq.files
    )
    files_with_issues = sum(
        1 for sq in results for fq in sq.files if fq.issues
    )
    sensors_with_issues = sum(1 for sq in results if sq.issues or
                               any(fq.issues for fq in sq.files))
    all_issues: List[Tuple[str, str, str]] = []  # (sensor, file, issue)
    for sq in results:
        for issue in sq.issues:
            all_issues.append((sq.sensor_label, "—", issue))
        for fq in sq.files:
            for issue in fq.issues:
                all_issues.append((sq.sensor_label, fq.path.name, issue))

    return {
        "total_files": total_files,
        "total_size_mb": total_size / (1024 * 1024),
        "files_with_issues": files_with_issues,
        "sensors_with_issues": sensors_with_issues,
        "all_issues": all_issues,
        "results": results,
    }
