"""
geo-preprocess/utils/pipeline.py — 数据预处理流水线(从 geo-analyser 迁移)

职责:目录扫描、影像读写、RGB 预览,以及"大气校正 → 几何校正 → 干扰剔除"三步流水线。
产物:<stem>_corrected.tif + 5 张干扰掩膜(vegetation/water/cloud/snow/buildup),供蚀变分析复用。
"""

import os
import re
import io
import json
import base64
import numpy as np
from pathlib import Path
from collections import defaultdict
from typing import Optional, Dict, Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.atmospheric_correction import atmospheric_correction, LANDSAT8_BANDS
from core.geometric_correction import geometric_correction, GroundControlPoint, LANDSAT8_PROJECTION
from core.interference_removal import remove_interference, apply_mask

SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".npy"}
# 单波段文件名模式：B1.tif, B2.tif, B3N.tif, band1.tif 等
BAND_FILE_PATTERN = re.compile(r'^[Bb](?:and)?(\d+)([A-Za-z]*)\.(tif|tiff)$', re.IGNORECASE)


def _band_sort_key(name: str):
    """返回 (数字, 字母后缀) 用于波段排序，如 B3N → (3, 'N')"""
    m = BAND_FILE_PATTERN.match(name)
    if not m:
        return (9999, name)
    return (int(m.group(1)), m.group(2).upper())


def get_band_files(directory: Path) -> list:
    """
    检测目录中的波段文件（B1.tif, B2.tif, B3N.tif ...），
    返回按波段号排序的文件路径列表。
    """
    band_files = [f for f in directory.iterdir() if BAND_FILE_PATTERN.match(f.name)]
    if not band_files:
        return []
    return sorted(band_files, key=lambda f: _band_sort_key(f.name))


def group_bands_by_resolution(band_files: list) -> dict:
    """
    按像元分辨率将波段文件分组，用于 ASTER 等多分辨率传感器。

    Returns
    -------
    dict
        {分辨率标签: [文件路径列表]}，如 {"15m": [...], "30m": [...], "90m": [...]}
        文件列表内部保持原波段顺序。
    """
    import rasterio
    from collections import defaultdict

    groups = defaultdict(list)
    for bf in band_files:
        with rasterio.open(bf) as src:
            pixel_size = round(abs(src.res[0]))   # 取 y 方向像元大小（米）
            label = f"{pixel_size}m"
        groups[label].append(bf)
    return dict(groups)


def scan_directory(directory: str) -> dict:
    """
    递归扫描目录，自动识别两种影像组织方式：
    1. 多波段单文件（.tif/.tiff/.npy）
    2. 每波段一个文件（B1.tif, B2.tif ...），以场景目录为处理单元
    """
    results = {"total": 0, "files": []}

    try:
        root = Path(directory)
        if not root.exists():
            return results

        visited_dirs = set()

        for f in sorted(root.rglob("*")):
            # 单波段目录模式：该目录含 B1.tif 等
            if f.is_file() and BAND_FILE_PATTERN.match(f.name):
                scene_dir = f.parent
                if scene_dir in visited_dirs:
                    continue
                visited_dirs.add(scene_dir)
                band_files = get_band_files(scene_dir)
                if not band_files:
                    continue
                rel_dir = str(scene_dir.relative_to(root)) if scene_dir != root else scene_dir.name
                total_size = sum(b.stat().st_size for b in band_files) / (1024 * 1024)

                # 检测是否多分辨率（ASTER 等）
                try:
                    import rasterio
                    res_groups = group_bands_by_resolution(band_files)
                except Exception:
                    res_groups = {}

                multi_res = len(res_groups) > 1
                results["files"].append({
                    "name": f"{scene_dir.name} ({len(band_files)} 波段)",
                    "path": str(scene_dir),
                    "rel_path": rel_dir,
                    "size": total_size,
                    "mode": "aster_multi_res" if multi_res else "bands",
                    "band_count": len(band_files),
                    "res_groups": {k: [str(f) for f in v] for k, v in res_groups.items()} if multi_res else {},
                })
                continue

            # 多波段单文件模式
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
                rel_path = str(f.relative_to(root))
                results["files"].append({
                    "name": f.name,
                    "path": str(f),
                    "rel_path": rel_path,
                    "size": f.stat().st_size / (1024 * 1024),
                    "mode": "single",
                })

        results["total"] = len(results["files"])
        results["files"].sort(key=lambda x: x["rel_path"])
    except Exception as e:
        results["error"] = str(e)

    return results


def read_image(image_path: str) -> tuple:
    """
    读取影像，返回 (image_array, profile)。
    - 若 image_path 是目录：按 B1.tif, B2.tif ... 顺序合并为多波段
    - 若是单 .tif/.tiff 文件：直接读取
    - 若是 .npy：np.load
    """
    import rasterio
    p = Path(image_path)

    if p.is_dir():
        band_files = get_band_files(p)
        if not band_files:
            raise ValueError(f"目录 {p} 中未找到波段文件（B1.tif, B2.tif ...）")

        # 按分辨率分组，取数量最多的分辨率组，组内裁剪到最小公共尺寸
        res_groups = {}
        for bf in band_files:
            with rasterio.open(bf) as src:
                res = round(abs(src.res[0]))
            res_groups.setdefault(res, []).append(bf)
        # 选数量最多的分辨率组（通常是 10m 或 30m 光学波段）
        dominant_res = max(res_groups, key=lambda r: len(res_groups[r]))
        band_files = res_groups[dominant_res]

        # 读出各波段尺寸，裁剪到最小公共 rows/cols
        band_shapes = {}
        for bf in band_files:
            with rasterio.open(bf) as src:
                band_shapes[bf] = (src.height, src.width)
        min_rows = min(s[0] for s in band_shapes.values())
        min_cols = min(s[1] for s in band_shapes.values())

        bands = []
        profile = None
        for bf in band_files:
            with rasterio.open(bf) as src:
                bands.append(src.read(1)[:min_rows, :min_cols].astype(np.float32))
                if profile is None:
                    profile = src.profile.copy()
        image = np.stack(bands, axis=0)  # (bands, rows, cols)
        profile.update(count=len(bands), height=min_rows, width=min_cols)
        return image, profile

    suffix = p.suffix.lower()
    if suffix in (".tif", ".tiff"):
        with rasterio.open(image_path) as src:
            image = src.read().astype(np.float32)
            profile = src.profile.copy()
        return image, profile

    return np.load(image_path, allow_pickle=True), None


def save_image(image: np.ndarray, output_path: str, profile=None) -> None:
    """
    保存影像：有 profile 时保存为 GeoTIFF，否则保存为 .npy
    """
    import rasterio
    suffix = Path(output_path).suffix.lower()
    if suffix in (".tif", ".tiff") and profile is not None:
        profile.update(
            dtype=rasterio.float32,
            count=image.shape[0],
            nodata=float('nan')
        )
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(image.astype(np.float32))
    else:
        np.save(output_path, image)


def generate_preview(image_path: str, bands_rgb: tuple = (4, 2, 1)) -> str:
    """
    生成影像 RGB 预览图（base64 PNG）

    Parameters
    ----------
    image_path : str
        影像 .npy 路径
    bands_rgb : tuple
        用于 RGB 的波段索引（0-based）

    Returns
    -------
    str
        base64 编码的 PNG
    """
    try:
        image, _ = read_image(image_path)

        # 确保在有效范围内
        if image.shape[0] < max(bands_rgb) + 1:
            # 波段不够，使用可用波段
            bands_rgb = tuple(min(i, image.shape[0] - 1) for i in bands_rgb)

        # 提取 RGB 波段并归一化
        r = image[bands_rgb[0]].astype(np.float32)
        g = image[bands_rgb[1]].astype(np.float32)
        b = image[bands_rgb[2]].astype(np.float32)

        # 归一化到 [0, 1]
        for band in [r, g, b]:
            valid = band[np.isfinite(band) & (band > 0)]
            if valid.size == 0:
                band[:] = 0
                continue
            vmin, vmax = np.percentile(valid, [2, 98])
            if vmax > vmin:
                band[:] = (band - vmin) / (vmax - vmin)
            band[~np.isfinite(band)] = 0
            band[band < 0] = 0
            band[band > 1] = 1

        # 合成 RGB
        rgb = np.stack([r, g, b], axis=2)

        # 生成缩略图
        fig, ax = plt.subplots(figsize=(4, 4), dpi=100)
        ax.imshow(rgb)
        ax.axis('off')

        # 转换为 base64
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
        buf.seek(0)
        plt.close(fig)

        img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        return img_base64

    except Exception as e:
        return None


def _warp_to_reference(
    image: np.ndarray,
    ref_b64: str,
    min_match_count: int = 10,
) -> tuple:
    """
    用 OpenCV ORB 特征匹配将卫星影像 warp 到参考图坐标系。

    Parameters
    ----------
    image : np.ndarray  shape=(bands, rows, cols)
    ref_b64 : str       参考图 base64（data:image/...;base64,...）
    min_match_count : int  最少有效匹配点数

    Returns
    -------
    (warped_image, match_count) : (np.ndarray, int)
    """
    import cv2
    from PIL import Image as PILImage

    # 解码参考图 → 灰度
    _, encoded = ref_b64.split(",", 1)
    ref_bytes = base64.b64decode(encoded)
    ref_pil = PILImage.open(io.BytesIO(ref_bytes)).convert("L")
    ref_gray = np.array(ref_pil, dtype=np.uint8)
    ref_h, ref_w = ref_gray.shape

    # 将卫星影像渲染为灰度（取所有波段均值，先归一化到 uint8）
    src_mean = np.mean(image, axis=0).astype(np.float32)
    valid = src_mean[np.isfinite(src_mean)]
    lo, hi = (float(np.percentile(valid, 2)), float(np.percentile(valid, 98))) if valid.size > 0 else (0, 1)
    src_norm = np.clip((src_mean - lo) / max(hi - lo, 1e-6), 0, 1)
    src_gray = (src_norm * 255).astype(np.uint8)

    # ORB 特征检测与匹配
    orb = cv2.ORB_create(nfeatures=2000)
    kp1, des1 = orb.detectAndCompute(src_gray, None)
    kp2, des2 = orb.detectAndCompute(ref_gray, None)

    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        raise ValueError("特征点不足，无法完成参考图配准")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw_matches = matcher.knnMatch(des1, des2, k=2)

    # Lowe's ratio test
    good = [m for m, n in raw_matches if m.distance < 0.75 * n.distance]

    if len(good) < min_match_count:
        raise ValueError(
            f"有效匹配点 {len(good)} 个，少于最低要求 {min_match_count} 个。"
            "请换一张与卫星影像地物更相似的参考图，或降低最少匹配点数。"
        )

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if H is None:
        raise ValueError("单应矩阵计算失败，匹配点分布可能不均匀")

    inlier_count = int(mask.sum())

    # 对每个波段做 warp
    bands = image.shape[0]
    warped = np.zeros((bands, ref_h, ref_w), dtype=np.float32)
    for b in range(bands):
        band = image[b].astype(np.float32)
        warped[b] = cv2.warpPerspective(band, H, (ref_w, ref_h),
                                         flags=cv2.INTER_LINEAR,
                                         borderMode=cv2.BORDER_CONSTANT,
                                         borderValue=np.nan)

    return warped, inlier_count


def ensure_output_directory(output_dir: str, rel_path: str) -> Path:
    """
    确保输出目录存在，保留子目录结构
    """
    out_path = Path(output_dir) / Path(rel_path).parent
    out_path.mkdir(parents=True, exist_ok=True)
    return out_path


def _save_masks(output_path: str, profile, interference_result, callback=None) -> dict:
    """
    把 RemovalResult 的 5 张二值 mask 各自保存到主输出同目录，命名为
      <stem-without-_corrected>_mask_<kind><suffix>
    主输出为 .tif/.tiff 时写成 uint8 单波段 GeoTIFF；为 .npy 时直接 np.save。
    失败不抛，仅 callback 一条 warning。返回 {kind: path} 成功落盘的字典。
    """
    if interference_result is None:
        return {}
    out = Path(output_path)
    base_stem = out.stem
    if base_stem.endswith('_corrected'):
        base_stem = base_stem[:-len('_corrected')]
    suffix = out.suffix.lower()

    mask_specs = [
        ('vegetation', getattr(interference_result, 'vegetation_mask', None)),
        ('water',      getattr(interference_result, 'water_mask',      None)),
        ('cloud',      getattr(interference_result, 'cloud_mask',      None)),
        ('snow',       getattr(interference_result, 'snow_mask',       None)),
        ('buildup',    getattr(interference_result, 'buildup_mask',    None)),
    ]
    paths = {}
    for kind, m in mask_specs:
        if m is None:
            continue
        mask_path = out.with_name(f'{base_stem}_mask_{kind}{out.suffix}')
        try:
            mask_u8 = m.astype(np.uint8)
            if suffix in ('.tif', '.tiff') and profile is not None:
                import rasterio
                mp = profile.copy()
                mp.update(count=1, dtype=rasterio.uint8, nodata=None)
                with rasterio.open(str(mask_path), 'w', **mp) as dst:
                    dst.write(mask_u8[np.newaxis, :, :])
            else:
                np.save(str(mask_path), mask_u8)
            paths[kind] = str(mask_path)
        except Exception as e:
            if callback:
                callback(f"  ⚠ 保存 mask_{kind} 失败：{e}")
    return paths


def _run_pipeline(image: np.ndarray, params: dict, label: str, res_label, callback):
    """
    对单组波段数据执行：大气校正 → 几何校正 → 干扰剔除。
    返回 (image, interference_result_or_None)。

    Parameters
    ----------
    image : np.ndarray  shape=(bands, rows, cols)
    params : dict       来自前端的参数
    label : str         文件/场景名（用于日志）
    res_label : str|None  分辨率标签（ASTER 模式）
    callback : callable|None

    Returns
    -------
    np.ndarray  处理后的影像
    """
    tag = f"[{res_label}] " if res_label else ""
    interference_result = None  # ASTER / 其它跳过分支保持 None

    # 步骤 1：大气校正
    sensor = params.get("sensor", "Landsat8/9")
    is_l2 = sensor.endswith("_L2")  # L2 产品已是地表反射率，跳过大气校正

    if callback:
        callback(f"  {tag}【步骤 1】大气校正")
    if is_l2:
        if callback:
            callback(f"  {tag}  L2 产品已含地表反射率，跳过大气校正")
    else:
        atmo_method = params.get("atmo_method", "dos")
        solar_zenith = float(params.get("solar_zenith", 30.0))
        aot550 = float(params.get("aot550", 0.1))
        altitude = float(params.get("altitude", 0.0))

        # 波段数预检：DOS 路径需要至少 NIR/RED 两个波段做 NDVI 识别暗目标
        min_required = max(LANDSAT8_BANDS.red, LANDSAT8_BANDS.nir) + 1  # = 5
        if image.shape[0] < min_required:
            raise ValueError(
                f"DOS 大气校正需要至少 {min_required} 个波段（按 Landsat8/9 顺序 含 RED/NIR），"
                f"当前文件仅 {image.shape[0]} 个波段。"
                f"请使用多波段影像（如完整的 Landsat 多波段 GeoTIFF），"
                f"或把分散的单波段文件命名成 B1.tif/B2.tif… 放在同一目录后再扫描。"
            )

        # 只对光学波段做大气校正（Landsat8/9 B1-B7，共 7 个）
        n_optical = min(image.shape[0], 7)
        tir_bands = image[n_optical:] if image.shape[0] > n_optical else None
        atmo_result = atmospheric_correction(
            image[:n_optical],
            band_cfg=LANDSAT8_BANDS,
            solar_zenith=solar_zenith,
            method=atmo_method,
            aot550=aot550,
            altitude=altitude
        )
        image = atmo_result.surface_reflectance
        if tir_bands is not None:
            image = np.concatenate([image, tir_bands], axis=0)

    # 步骤 2：几何校正
    if callback:
        callback(f"  {tag}【步骤 2】几何校正")
    if is_l2:
        if callback:
            callback(f"  {tag}  L2 产品已完成几何校正，跳过")
    else:
        geom_method = params.get("geom_method", "affine")

        if geom_method == "reference":
            ref_b64 = params.get("ref_image")
            if not ref_b64:
                if callback:
                    callback(f"  {tag}  ⚠ 未提供参考图，跳过参考图配准")
            else:
                min_match = int(params.get("min_match_count", 10))
                if callback:
                    callback(f"  {tag}  参考图配准中（ORB 特征匹配）...")
                image, match_count = _warp_to_reference(image, ref_b64, min_match)
                if callback:
                    callback(f"  {tag}  配准完成，使用匹配点: {match_count}")
        else:
            pixel_size = float(params.get("pixel_size", 30.0))
            interpolation = params.get("interpolation", "bilinear")

            rows, cols = image.shape[1], image.shape[2]
            gcps = [
                GroundControlPoint(pixel_x=0,    pixel_y=0,    geo_x=0.0,                      geo_y=100.0),
                GroundControlPoint(pixel_x=cols, pixel_y=0,    geo_x=cols*pixel_size/111000,    geo_y=100.0),
                GroundControlPoint(pixel_x=0,    pixel_y=rows, geo_x=0.0,                       geo_y=100-rows*pixel_size/111000),
            ]
            if geom_method == "polynomial":
                gcps.extend([
                    GroundControlPoint(pixel_x=cols//2, pixel_y=0,      geo_x=(cols//2)*pixel_size/111000, geo_y=100.0),
                    GroundControlPoint(pixel_x=cols,    pixel_y=rows,   geo_x=cols*pixel_size/111000,      geo_y=100-rows*pixel_size/111000),
                    GroundControlPoint(pixel_x=0,       pixel_y=rows//2,geo_x=0.0,                         geo_y=100-(rows//2)*pixel_size/111000),
                    GroundControlPoint(pixel_x=cols//2, pixel_y=rows//2,geo_x=(cols//2)*pixel_size/111000, geo_y=100-(rows//2)*pixel_size/111000),
                    GroundControlPoint(pixel_x=cols,    pixel_y=rows//2,geo_x=cols*pixel_size/111000,      geo_y=100-(rows//2)*pixel_size/111000),
                    GroundControlPoint(pixel_x=cols//2, pixel_y=rows,   geo_x=(cols//2)*pixel_size/111000, geo_y=100-rows*pixel_size/111000),
                ])

            geom_result = geometric_correction(image, gcps=gcps, method=geom_method, interpolation=interpolation)
            image = geom_result.corrected_image

    # 步骤 3：干扰剔除
    if callback:
        callback(f"  {tag}【步骤 3】干扰剔除")
    sensor_base = sensor.replace("_L2", "")  # 去掉 L2 后缀统一判断
    if sensor_base == "ASTER":
        # ASTER SWIR/TIR 波段不含标准 NIR，无法计算 NDVI/NDWI，跳过干扰剔除
        if callback:
            callback(f"  {tag}  ASTER 传感器：跳过干扰剔除（波段无标准 NIR）")
    else:
        if sensor_base == "Sentinel2":
            from core.interference_removal import SENTINEL2_BANDS
            band_cfg = SENTINEL2_BANDS
        else:
            band_cfg = LANDSAT8_BANDS

        # L2 整数格式（反射率×10000）需先归一化到 [0,1] 再做指数计算
        scale = 1.0
        if is_l2 and image.max() > 10.0:
            scale = 10000.0
            image = image / scale

        # 把前端两个滑块的值塞进 MaskThresholds：
        #   ndvi_threshold ∈ [0, 1] 直接对应 ndvi_veg
        #   cloud_threshold ∈ [0, 100]（保守→激进）反向线性映射到 cloud_blue：
        #     slider=0   → cloud_blue=0.50（最保守，几乎不标云）
        #     slider=100 → cloud_blue=0.05（最激进，大量标云）
        from core.interference_removal import MaskThresholds as _MT
        try:
            ndvi_v = float(params.get("ndvi_threshold", 0.20))
        except (TypeError, ValueError):
            ndvi_v = 0.20
        try:
            cloud_pct = float(params.get("cloud_threshold", 75))
        except (TypeError, ValueError):
            cloud_pct = 75.0
        cloud_pct = max(0.0, min(100.0, cloud_pct))
        cloud_blue_v = 0.50 - (cloud_pct / 100.0) * 0.45
        thresholds = _MT(ndvi_veg=ndvi_v, cloud_blue=cloud_blue_v)
        if callback:
            callback(f"  {tag}  阈值: NDVI>{ndvi_v:.2f} 判为植被，云敏感度 {cloud_pct:.0f}%（蓝反射>{cloud_blue_v:.2f} 判为云）")
        interference_result = remove_interference(image, band_cfg=band_cfg, thresholds=thresholds)

        # 按用户勾选项组合掩膜（默认只剔除云）
        mask = np.zeros(image.shape[1:], dtype=bool)
        if params.get("mask_vegetation", False):
            mask |= interference_result.vegetation_mask
        if params.get("mask_water", False):
            mask |= interference_result.water_mask
        if params.get("mask_cloud", False):
            mask |= interference_result.cloud_mask
        if params.get("mask_snow", False):
            mask |= interference_result.snow_mask
        if params.get("mask_buildup", False):
            mask |= interference_result.buildup_mask

        image = apply_mask(image, mask)

        # 还原原始量纲
        if scale != 1.0:
            image = image * scale

    return image, interference_result


def process_single_file(
    input_path: str,
    output_dir: str,
    rel_path: str,
    params: dict,
    file_info: dict,
    callback=None
) -> dict:
    """
    处理单个影像文件（三步流水线）

    Parameters
    ----------
    input_path : str
        输入 .npy 文件路径
    output_dir : str
        输出目录
    rel_path : str
        相对路径（用于保留目录结构）
    params : dict
        处理参数
    callback : callable
        进度回调函数

    Returns
    -------
    dict
        处理结果
    """
    result = {
        "file": rel_path,
        "status": "processing",
        "steps": {},
        "error": None
    }

    try:
        # 读取输入影像
        if callback:
            callback(f"读取文件: {rel_path}")

        mode = file_info.get("mode", "single")

        # ── ASTER 多分辨率模式 ──────────────────────────────────
        if mode == "aster_multi_res":
            res_groups = file_info.get("res_groups", {})
            group_results = {}
            for res_label, band_paths in sorted(res_groups.items()):
                if callback:
                    callback(f"  [{res_label}] 处理 {len(band_paths)} 个波段")

                import rasterio
                bands = []
                group_profile = None
                for bf in band_paths:
                    with rasterio.open(bf) as src:
                        bands.append(src.read(1).astype(np.float32))
                        if group_profile is None:
                            group_profile = src.profile.copy()
                group_image = np.stack(bands, axis=0)
                group_profile.update(count=len(bands))

                group_image, group_interference = _run_pipeline(group_image, params, rel_path, res_label, callback)

                # 保存每个分辨率组
                scene_name = Path(rel_path).name
                out_dir = Path(output_dir) / rel_path
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / f"{scene_name}_{res_label}_corrected.tif"
                save_image(group_image, str(out_file), group_profile)
                group_results[res_label] = str(out_file)
                if callback:
                    callback(f"  [{res_label}] 已保存 → {out_file.name}")

                # 同步落盘 5 张 mask 副产物（按分辨率组各一套）
                if group_interference is not None:
                    mp = _save_masks(str(out_file), group_profile, group_interference, callback)
                    if mp:
                        result.setdefault("mask_paths", {})[res_label] = mp

            result["output_groups"] = group_results
            result["status"] = "success"
            return result
        # ── 普通模式（单文件 / 同分辨率波段目录）──────────────────

        image, src_profile = read_image(input_path)
        result["input_shape"] = tuple(image.shape)
        image, interference_result = _run_pipeline(image, params, rel_path, None, callback)

        # 保存结果
        if callback:
            callback(f"保存结果: {rel_path}")

        ensure_output_directory(output_dir, rel_path)

        if mode == "bands":
            scene_name = Path(rel_path).name
            output_file = Path(output_dir) / rel_path / f"{scene_name}_corrected.tif"
            output_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_file = Path(output_dir) / rel_path
            output_file = output_file.with_name(output_file.stem + "_corrected" + output_file.suffix)

        save_image(image, str(output_file), src_profile)
        result["output_path"] = str(output_file)

        # 同步落盘 5 张 mask 副产物（供蚀变分析阶段复用）
        if interference_result is not None:
            mp = _save_masks(str(output_file), src_profile, interference_result, callback)
            if mp:
                result["mask_paths"] = mp

        result["status"] = "success"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        if callback:
            callback(f"❌ 错误 ({rel_path}): {str(e)}")

    return result
