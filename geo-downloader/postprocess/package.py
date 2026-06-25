"""
Postprocess: 标准交付目录打包模块
将 downloads/{area}/ 原始下载数据整理为标准交付目录结构，
与纳兰金铜钼矿项目文件架构完全对齐。

标准交付目录结构：
  {delivery_root}/{area_name}/
  ├── {area_name}.kml / .ovkml          # KML边界文件（复制）
  ├── data-矿权-夏季（6-8月）/
  │   ├── Sentinel 2 L2/    B01-B12,B8A.tiff
  │   ├── Landsat 8 L2/     B1-B11.tif
  │   ├── Landsat 7 ETM+/   B1-B8.tif（含全色B8 15m，SLC-off标注）
  │   ├── ASTER L2/         B1-B14.tif（含TIR）
  │   ├── EMIT L2A/         SPECTRAL_IMAGE.nc（高光谱，替代EnMAP）
  │   ├── Hyperion L1/      SPECTRAL_IMAGE.hdf（242波段，30m，EO-1存档）
  │   ├── AVIRIS-NG/        SPECTRAL_IMAGE.*（432波段，~5m，机载）
  │   ├── PlanetScope/      B1-B8.tif（3-5m，4或8波段）
  │   ├── DEM.tif
  │   ├── 地表温度.tif
  │   ├── 温度梯度.tif
  │   ├── 温度异常梯度.tif
  │   └── OTCI.tiff
  └── data-矿权-冬季（11-3月）/
      └── （同上，无EMIT/AVIRIS-NG/Hyperion）

季节分割规则：
  夏季  = 拍摄月份 6、7、8
  冬季  = 拍摄月份 11、12、1、2、3
  其他月份（4、5、9、10）按云量/时间就近归入，无法判断时归入夏季

文件重命名规则（原始名 → 标准名）：
  Sentinel-2   _B02_*.jp2             → B02.tiff
  Landsat 8/9  _blue_clipped.TIF      → B2.tif  （按波段名映射）
  Landsat 7    _blue_clipped.TIF      → B1.tif  （L7波段编号偏移）
               _pan_clipped.TIF       → B8.tif  （全色）
  ASTER L2     AST_07_*_B01*          → B1.tif
  DEM/SRTM     *.tif                  → DEM.tif（取第一个）
  ECOSTRESS    *_LST_clipped.tif      → 地表温度.tif
  EMIT         EMIT_L2A_RFL_*         → SPECTRAL_IMAGE.nc
  Hyperion     EO1H*_HYP*.hdf         → SPECTRAL_IMAGE.hdf
  AVIRIS-NG    ang*_rfl*              → SPECTRAL_IMAGE.*（保留原始格式）
  PlanetScope  *_ortho_analytic*.tif  → B1-B8.tif（按波段顺序）
"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime


def resample_to_resolution(src_path: Path, dst_path: Path, target_res: float,
                           resampling=None) -> bool:
    """
    将 GeoTIFF 重采样到目标空间分辨率（单位：米）。
    仅当目标分辨率比原始分辨率更精细时才重采样，否则直接返回 False（由调用方执行普通复制）。

    Parameters
    ----------
    src_path   : 源 GeoTIFF 路径
    dst_path   : 输出路径
    target_res : 目标分辨率（米），如 10.0 / 15.0 / 30.0
    resampling : rasterio.warp.Resampling 枚举值；
                 None（默认）= bilinear（适合连续光学波段）；
                 传入 Resampling.nearest 适合热红外/分类/DEM

    Returns
    -------
    True  - 已完成重采样写出
    False - 原始分辨率已优于或等于目标，无需重采样（调用方应 _copy）
    """
    try:
        import rasterio
        from rasterio.warp import reproject, Resampling, calculate_default_transform
        from rasterio.crs import CRS
        import math
    except ImportError:
        return False

    if dst_path.exists():
        return True

    try:
        with rasterio.open(src_path) as src:
            # 用 sqrt(a²+b²) 计算真实像素尺寸，适配旋转 transform
            t = src.transform
            pixel_x = math.sqrt(t.a**2 + t.b**2)
            pixel_y = math.sqrt(t.d**2 + t.e**2)
            native_res_crs = (pixel_x + pixel_y) / 2.0

            # 若 CRS 是地理坐标（度），转换为近似米
            if src.crs and src.crs.is_geographic:
                # 1度纬度 ≈ 111320m
                native_res_m = native_res_crs * 111320.0
            else:
                native_res_m = native_res_crs

            # 原始分辨率已经优于或等于目标，不做上采样
            if native_res_m <= target_res * 1.05:  # 5% 容差
                return False

            # 若 CRS 是地理坐标，以度为单位计算 target_res
            if src.crs and src.crs.is_geographic:
                target_res_crs = target_res / 111320.0
            else:
                target_res_crs = target_res

            # 用 calculate_default_transform 正确处理旋转/非正方形像素
            new_transform, new_width, new_height = calculate_default_transform(
                src.crs, src.crs,
                src.width, src.height,
                *src.bounds,
                resolution=target_res_crs,
            )

            import numpy as _np
            _predictor = 3 if src.dtypes[0] in ("float32", "float64") else 2
            profile = src.profile.copy()
            profile.update({
                "driver": "GTiff",
                "width": new_width,
                "height": new_height,
                "transform": new_transform,
                "compress": "deflate",
                "predictor": _predictor,
            })

            _use_resampling = resampling if resampling is not None else Resampling.bilinear
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(dst_path, "w", **profile) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=new_transform,
                        dst_crs=src.crs,
                        resampling=_use_resampling,
                    )
        return True
    except Exception as e:
        print(f"    [警告] 重采样失败 {src_path.name}: {e}，回退普通复制")
        if dst_path.exists():
            dst_path.unlink()
        return False


_MIN_TIFF_SIZE = 50 * 1024  # 50 KB，小于此视为截断文件


def _copy_with_resample(src: Path, dst: Path, target_res: Optional[float] = None,
                        resampling=None, min_size: int = _MIN_TIFF_SIZE):
    """复制文件，若指定 target_res 则先尝试重采样；目标已存在且大小正常则跳过。
    min_size: 低于此字节数视为截断文件（默认50KB；对TIR/低分辨率波段可传入更小值）。
    """
    if dst.exists():
        if dst.stat().st_size >= min_size:
            return
        # 文件过小，疑似截断，删除重写
        print(f"    [警告] {dst.name} 体积极小（{dst.stat().st_size // 1024} KB），疑似截断，重新生成")
        dst.unlink()
    if target_res is not None:
        done = resample_to_resolution(src, dst, target_res, resampling=resampling)
        if done:
            if dst.suffix.lower() in {".tif", ".tiff"}:
                _write_statistics(dst)
            return
    # 普通复制（原始分辨率已够精细，或重采样失败）
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if dst.suffix.lower() in {".tif", ".tiff"}:
        _write_statistics(dst)


# ── 季节定义 ─────────────────────────────────────────────────
_SUMMER_MONTHS = {6, 7, 8}
_WINTER_MONTHS = {11, 12, 1, 2, 3}
_SEASON_SUMMER = "data-矿权-夏季（6-8月）"
_SEASON_WINTER = "data-矿权-冬季（11-3月）"

# Landsat 8/9 波段名称 → 标准编号
_LANDSAT_BAND_MAP = {
    "coastal":  "B1",
    "blue":     "B2",
    "green":    "B3",
    "red":      "B4",
    "nir08":    "B5",
    "swir16":   "B6",
    "swir22":   "B7",
    "pan":      "B8",
    "cirrus":   "B9",
    "lwir11":   "B10",
    "lwir12":   "B11",
    "qa_pixel": None,   # 跳过质量波段
}

# Landsat 7 ETM+ 波段名称 → 标准编号（与 L8/L9 不同，无 coastal/cirrus/lwir12）
_LANDSAT7_BAND_MAP = {
    "blue":     "B1",   # L7 B1 = 蓝
    "green":    "B2",   # L7 B2 = 绿
    "red":      "B3",   # L7 B3 = 红
    "nir08":    "B4",   # L7 B4 = 近红外
    "swir16":   "B5",   # L7 B5 = SWIR1
    "tir":      "B6",   # L7 B6 = 热红外（60m→30m）
    "swir22":   "B7",   # L7 B7 = SWIR2
    "pan":      "B8",   # L7 B8 = 全色 15m ★空间分辨率最高
    "qa_pixel": None,
}

# Sentinel-2 标准波段编号（从文件名 _B02_ 提取）
_S2_BAND_NAMES = {
    "B01": "B01", "B02": "B02", "B03": "B03", "B04": "B04",
    "B05": "B05", "B06": "B06", "B07": "B07", "B08": "B08",
    "B8A": "B8A", "B09": "B09", "B10": "B10", "B11": "B11",
    "B12": "B12",
}

# ASTER L2 波段映射
# AST_07: VNIR B01/B02/B03N → B1/B2/B3N；SWIR B04-B09 → B4-B9
# AST_08: SKT（动力温度） → 用于地表温度计算，不单独输出为 Bx
# AST_09T: TIR B10-B14 → B10-B14
# AST_L1T: VNIR_B01/B02/B03N, TIR_B10-B14（根目录散落文件）
_ASTER07_BAND_RE = re.compile(r'_SRF_(?:VNIR|SWIR)_(B\d+N?)', re.IGNORECASE)
_ASTER09T_BAND_RE = re.compile(r'_SIR_TIR_(B\d+)', re.IGNORECASE)
_ASTER_L1T_BAND_RE = re.compile(r'_(?:VNIR|TIR)_(B\d+N?)_clipped', re.IGNORECASE)


def _extract_date_from_filename(fname: str) -> Optional[datetime]:
    """从文件名提取拍摄日期"""
    # Landsat: LC08_L2SP_117044_20240414_...
    m = re.search(r'_(\d{8})_\d{2}_T', fname)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            pass
    # Sentinel-2: T51QTF_20240110T023051_...
    m = re.search(r'_(\d{8})T\d{6}_', fname)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            pass
    # ASTER: AST_07_004MMDDYYYYHHMMSS_ProcessTimestamp_...
    # granule ID 格式：004 + MM + DD + YYYY + HHMMSS
    # 例：AST_07_00410102007040922 → MM=10 DD=10 YYYY=2007
    m = re.search(r'AST_\w+_\d{3}(\d{2})(\d{2})(\d{4})\d{6}_', fname)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    # ECOSTRESS: _20220103T150104_
    m = re.search(r'_(\d{8})T\d{6}_', fname)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            pass
    # EMIT: _20240224T030526_
    m = re.search(r'_(\d{8})T\d{6}_', fname)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            pass
    return None


def _season_of(dt: Optional[datetime]) -> str:
    """根据日期判断季节目录名"""
    if dt is None:
        return _SEASON_SUMMER  # 无法判断默认夏季
    m = dt.month
    if m in _SUMMER_MONTHS:
        return _SEASON_SUMMER
    if m in _WINTER_MONTHS:
        return _SEASON_WINTER
    # 过渡季节：4/5 → 夏季，9/10 → 冬季
    return _SEASON_SUMMER if m in {4, 5} else _SEASON_WINTER


def _write_statistics(tiff_path: Path, p_low: float = 2.0, p_high: float = 98.0):
    """
    向 GeoTIFF 写入 p2~p98 百分位统计元数据（STATISTICS_MINIMUM/MAXIMUM/MEAN/STDDEV）。
    macOS Finder、QGIS、ArcGIS 等读取这些标签后会自动拉伸显示，避免因值域窄导致缩略图全黑。
    原地修改文件，失败时静默跳过（不影响主流程）。
    """
    try:
        import rasterio
        import numpy as np
    except ImportError:
        return
    try:
        with rasterio.open(tiff_path, "r+") as ds:
            for band_idx in range(1, ds.count + 1):
                data = ds.read(band_idx).astype(np.float64)
                nd = ds.nodata
                if nd is not None:
                    mask = ~np.isnan(data) & (data != nd) if not np.isnan(nd) else ~np.isnan(data)
                else:
                    mask = np.ones(data.shape, dtype=bool)
                valid = data[mask]
                if valid.size == 0:
                    continue
                vmin = float(np.percentile(valid, p_low))
                vmax = float(np.percentile(valid, p_high))
                vmean = float(valid.mean())
                vstd = float(valid.std())
                ds.update_tags(band_idx,
                               STATISTICS_MINIMUM=str(vmin),
                               STATISTICS_MAXIMUM=str(vmax),
                               STATISTICS_MEAN=str(vmean),
                               STATISTICS_STDDEV=str(vstd))
    except Exception:
        pass


def _copy(src: Path, dst: Path):
    """复制文件，目标已存在且大小正常则跳过"""
    if dst.exists():
        if dst.suffix.lower() not in {".tif", ".tiff"} or dst.stat().st_size >= _MIN_TIFF_SIZE:
            return
        # TIFF 文件过小，疑似截断，删除重写
        print(f"    [警告] {dst.name} 体积极小（{dst.stat().st_size // 1024} KB），疑似截断，重新复制")
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if dst.suffix.lower() in {".tif", ".tiff"}:
        _write_statistics(dst)


def _valid_pixel_ratio(fpath: Path) -> float:
    """快速估算 GeoTIFF 有效像素比例（0.0~1.0），打开失败返回 0.5（给个中间值）"""
    try:
        import rasterio
        import numpy as np
        with rasterio.open(fpath) as src:
            data = src.read(1)
            nd = src.nodata
            if nd is not None:
                # 有明确 nodata 元数据，以此为准
                if np.isnan(nd):
                    valid = int(np.count_nonzero(~np.isnan(data)))
                else:
                    valid = int(np.count_nonzero(data != nd))
            else:
                # 无 nodata 元数据：假定所有像素均有效（避免把合法的 0 值误判为无效）
                valid = data.size
            return valid / data.size if data.size > 0 else 0.0
    except Exception:
        return 0.5


def _best_file(files: List[Path], prefer_summer: bool = True) -> Optional[Path]:
    """从多个同类文件中选出最佳一个（覆盖率过滤 + 季节偏好 + 最新日期）"""
    if not files:
        return None

    # 过滤覆盖率极低的文件（<10%），避免选中空文件
    good = [f for f in files if _valid_pixel_ratio(f) > 0.1]
    pool_files = good if good else files

    dated = []
    for f in pool_files:
        dt = _extract_date_from_filename(f.name)
        dated.append((dt, f))

    # 优先按季节匹配
    target_season = _SEASON_SUMMER if prefer_summer else _SEASON_WINTER
    seasonal = [(dt, f) for dt, f in dated
                if _season_of(dt) == target_season]
    pool = seasonal if seasonal else dated

    # 取最新的
    pool.sort(key=lambda x: x[0] or datetime(2000, 1, 1), reverse=True)
    return pool[0][1]


# ═══════════════════════════════════════════════════════════════
# 各传感器的整理函数
# ═══════════════════════════════════════════════════════════════

def _package_sentinel2(raw_dir: Path, season_dir: Path, folder_label: str = "Sentinel 2 L2",
                       geometry=None) -> List[Path]:
    """Sentinel-2 SAFE → {folder_label}/B01.tiff … B12.tiff, B8A.tiff

    geometry: Optional WGS84 Shapely。若 raw_dir 里只见 .zip(下载流程异常退出留下的产物)
              且传了 geometry,则自动解压+按波段拼接裁剪(复用 mosaic_sentinel2_zips),
              避免"数据在盘上但 packager 看不见"的情况。
    """
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []

    # 顶层有 mosaic_*.tif(已 AOI 裁剪) 就**只**用它们,
    # 否则才退回 rglob 扫 SAFE/ 里的瓦片 JP2(可能未裁剪,体积大)
    def _scan_band_files(d: Path) -> Dict[str, List[Path]]:
        bf: Dict[str, List[Path]] = {}
        top_mosaics = list(d.glob("mosaic_*.tif")) + list(d.glob("mosaic_*.tiff"))
        if top_mosaics:
            candidates = [f for f in top_mosaics if f.is_file()]
            # 顺手把残留的 SAFE/ 子目录清掉,省盘+防下次扫描误选
            for safe_dir in d.glob("*.SAFE"):
                if safe_dir.is_dir():
                    shutil.rmtree(safe_dir, ignore_errors=True)
            for safe_dir in d.glob("S2*_MSIL2A_*"):
                if safe_dir.is_dir():
                    shutil.rmtree(safe_dir, ignore_errors=True)
        else:
            candidates = []
            for f in d.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in (".jp2", ".tif", ".tiff"):
                    continue
                if f.name.upper().startswith("MSK_"):
                    continue
                candidates.append(f)

        for f in candidates:
            name = f.name.upper()
            for bkey in _S2_BAND_NAMES:
                if f"_{bkey}_" in name or f"_{bkey}." in name:
                    bf.setdefault(bkey, []).append(f)
                    break
        return bf

    band_files: Dict[str, List[Path]] = _scan_band_files(raw_dir)

    # ── 兜底:只见 zip 不见栅格 → 调拼接,把 zip 解开 ──
    # 触发条件:base.py run() 里的 mosaic_sentinel2_zips 因下载异常被跳过,zip 还堆在 raw_dir
    if not band_files and geometry is not None:
        zip_files = [f for f in raw_dir.glob("*.zip") if f.stat().st_size > 1024 * 1024]
        if zip_files:
            print(f"  [Sentinel-2 兜底] 发现 {len(zip_files)} 个未处理 ZIP,自动解压拼接...")
            try:
                from postprocess.mosaic import mosaic_sentinel2_zips
                mosaic_sentinel2_zips(zip_files, geometry, raw_dir)
                band_files = _scan_band_files(raw_dir)
            except Exception as e:
                print(f"  [警告] Sentinel-2 兜底拼接失败: {e}")

    # 对每个波段：若有多个分辨率的 mosaic（_10m/_20m/_60m），只保留最高分辨率
    for bkey in list(band_files):
        files = band_files[bkey]
        if len(files) > 1:
            # 优先选 _10m > _20m > _60m（按文件名后缀排序）
            res_order = {"_10M": 0, "_20M": 1, "_60M": 2}
            def _res_rank(fp):
                n = fp.stem.upper()
                for suffix, rank in res_order.items():
                    if n.endswith(suffix):
                        return rank
                return 1  # 无后缀默认中间优先级
            files.sort(key=_res_rank)
            # 只保留最高分辨率的那组文件
            best_rank = _res_rank(files[0])
            band_files[bkey] = [f for f in files if _res_rank(f) == best_rank]

    # 所有波段统一重采样到 10m（原生 10m 的不会触发重采样）
    # 对每个波段选最佳文件
    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    # B10 在 L2A 产品中（卷云吸收带，用于云检测），通常全为0，不进行有效像素过滤
    _S2_SKIP_VALIDITY_CHECK = {"B09", "B10"}

    for bkey, files in sorted(band_files.items()):
        if bkey in _S2_SKIP_VALIDITY_CHECK:
            # B09/B10 不走 _best_file 的有效像素过滤，直接选最新的
            # 但只接受单波段文件，排除 GEE 多波段 tif 等误匹配
            pool = []
            for f in files:
                try:
                    import rasterio as _rio
                    with _rio.open(f) as _src:
                        if _src.count == 1:
                            pool.append(f)
                except Exception:
                    pool.append(f)  # 无法打开时保留，让后续流程处理
            if not pool:
                continue
            pool_dated = []
            for f in pool:
                dt = _extract_date_from_filename(f.name)
                pool_dated.append((dt, f))
            pool_dated.sort(key=lambda x: (x[0] is not None, x[0]), reverse=True)
            best = pool_dated[0][1] if pool_dated else None
            # 检查有效像素，仅打印提示，不跳过
            if best:
                ratio = _valid_pixel_ratio(best)
                if ratio < 0.05:
                    print(f"    [提示] {bkey} 有效像素 {ratio:.0%} — "
                          f"{'B10在L2A中为卷云波段，通常全为0，属正常现象' if bkey == 'B10' else 'B09水汽波段，部分区域可能受地形/传感器限制'}")
        else:
            best = _best_file(files, prefer_summer=season_is_summer)

        if best:
            dst = out_dir / f"{bkey}.tiff"
            _copy_with_resample(best, dst, target_res=10.0)
            done.append(dst)

    # 检查缺失波段并打印警告
    missing = [b for b in _S2_BAND_NAMES if b not in band_files]
    if missing:
        print(f"    [警告] Sentinel-2 缺少以下波段（原始ZIP中未找到对应文件）: {', '.join(missing)}")
        print(f"           预期 {len(_S2_BAND_NAMES)} 个，实际找到 {len(band_files)} 个")
        print(f"           建议：检查下载的ZIP是否完整，或重新下载该景")

    return done


def _autoclip_landsat_raw(raw_dir: Path, geometry) -> int:
    """兜底:当 base.py run() 因下载异常跳过裁剪时,扫 raw 裸 _band.TIF 调 clip_to_geometry。

    Returns: 实际裁剪成功的文件数。clip_raster 会写出 _clipped.TIF 并删除原文件。
    """
    raw_tifs = [
        f for f in raw_dir.rglob("*.TIF")
        if f.is_file() and "_clipped" not in f.name and f.stat().st_size > _MIN_TIFF_SIZE
    ]
    if not raw_tifs:
        return 0
    print(f"  [Landsat 兜底] 发现 {len(raw_tifs)} 个未裁剪 TIF,自动裁剪到 KML 范围...")
    from postprocess.clip import clip_to_geometry as _clip_to_geom
    ok = 0
    for f in raw_tifs:
        try:
            out = _clip_to_geom(f, geometry)
            if out and out.exists() and "_clipped" in out.name:
                ok += 1
        except Exception as e:
            print(f"    [警告] 裁剪 {f.name} 失败: {e}")
    return ok


def _package_landsat(raw_dir: Path, season_dir: Path, folder_label: str = "Landsat 8 L2",
                     geometry=None) -> List[Path]:
    """Landsat 8/9 _blue_clipped.TIF → {folder_label}/B2.tif …（重采样到15m）"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []

    def _scan() -> Dict[str, List[Path]]:
        bf: Dict[str, List[Path]] = {}
        for f in raw_dir.rglob("*_clipped.TIF"):
            name_lower = f.name.lower()
            for bname, bnum in _LANDSAT_BAND_MAP.items():
                if bnum is None:
                    continue
                if f"_{bname}_clipped" in name_lower:
                    bf.setdefault(bnum, []).append(f)
                    break
        return bf

    band_files: Dict[str, List[Path]] = _scan()
    if not band_files and geometry is not None:
        if _autoclip_landsat_raw(raw_dir, geometry) > 0:
            band_files = _scan()

    # 热红外波段用最近邻，避免插值引入虚假温度梯度
    _TIR_BANDS_L89 = {"B10", "B11"}

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    for bnum, files in sorted(band_files.items(), key=lambda x: int(x[0][1:])):
        best = _best_file(files, prefer_summer=season_is_summer)
        if best:
            dst = out_dir / f"{bnum}.tif"
            from rasterio.warp import Resampling
            rs = Resampling.nearest if bnum in _TIR_BANDS_L89 else None
            _copy_with_resample(best, dst, target_res=15.0, resampling=rs)
            done.append(dst)

    return done


def _package_landsat7(raw_dir: Path, season_dir: Path, folder_label: str = "Landsat 7 ETM+",
                      geometry=None) -> List[Path]:
    """Landsat 7 ETM+ _blue_clipped.TIF → {folder_label}/B1-B8.tif …（B8全色15m保持原样，其余重采样到15m）"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []

    def _scan() -> Dict[str, List[Path]]:
        bf: Dict[str, List[Path]] = {}
        for f in raw_dir.rglob("*_clipped.TIF"):
            name_lower = f.name.lower()
            for bname, bnum in _LANDSAT7_BAND_MAP.items():
                if bnum is None:
                    continue
                if f"_{bname}_clipped" in name_lower:
                    bf.setdefault(bnum, []).append(f)
                    break
        return bf

    band_files: Dict[str, List[Path]] = _scan()
    if not band_files and geometry is not None:
        if _autoclip_landsat_raw(raw_dir, geometry) > 0:
            band_files = _scan()

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    for bnum, files in sorted(band_files.items(), key=lambda x: int(x[0][1:])):
        best = _best_file(files, prefer_summer=season_is_summer)
        if not best:
            continue

        # 低覆盖率且有多景时，尝试 mosaic 提升覆盖率
        if len(files) > 1 and _valid_pixel_ratio(best) < 0.60:
            try:
                import numpy as np
                import rasterio
                from rasterio.merge import merge as _rmerge
                datasets = []
                for fp in files:
                    try:
                        datasets.append(rasterio.open(fp))
                    except Exception:
                        pass
                if len(datasets) > 1:
                    counts = [ds.count for ds in datasets]
                    maj = max(set(counts), key=counts.count)
                    datasets = [ds for ds in datasets if ds.count == maj]
                    with rasterio.open(files[0]) as ref:
                        _res = ref.res
                        _meta = ref.meta.copy()
                    merged_data, merged_transform = _rmerge(datasets, res=_res)
                    for ds in datasets:
                        ds.close()
                    _predictor = 3 if merged_data.dtype.kind == "f" else 2
                    _meta.update({
                        "count": merged_data.shape[0],
                        "height": merged_data.shape[1],
                        "width": merged_data.shape[2],
                        "transform": merged_transform,
                        "compress": "deflate",
                        "predictor": _predictor,
                    })
                    _tmp = out_dir / f"{bnum}_mosaic_tmp.tif"
                    with rasterio.open(_tmp, "w", **_meta) as dst_ds:
                        dst_ds.write(merged_data)
                    best = _tmp
            except Exception:
                pass  # mosaic 失败则回退到单景

        # SLC-off 标注：文件名含 SLC-off 日期范围则标注
        slcoff_note = ""
        dt = _extract_date_from_filename(best.name)
        if dt and dt.strftime("%Y-%m-%d") >= "2003-05-31":
            slcoff_note = "_SLCoff"
        dst = out_dir / f"{bnum}{slcoff_note}.tif"
        # B8 全色已是 15m 原始分辨率，无需重采样；B6 热红外用最近邻
        target_res = None if bnum == "B8" else 15.0
        from rasterio.warp import Resampling
        rs = Resampling.nearest if bnum == "B6" else None
        _copy_with_resample(best, dst, target_res, resampling=rs)
        # 清理 mosaic 临时文件
        if best.name.endswith("_mosaic_tmp.tif"):
            best.unlink(missing_ok=True)
        done.append(dst)

    # 兜底：确保所有输出文件都有统计元数据
    for dst in done:
        _write_statistics(dst)

    return done


def _normalize_aster_bkey(raw: str) -> str:
    """将正则提取的波段标识规范化为 B1/B2/B3N/B4…B14"""
    raw = raw.upper()
    if raw.endswith("N"):
        # B03N → B3N
        num = raw[1:-1].lstrip("0") or "0"
        return f"B{num}N"
    num = raw[1:].lstrip("0") or "0"
    return f"B{num}"


def _package_aster(raw_dir: Path, season_dir: Path, folder_label: str = "ASTER L2") -> List[Path]:
    """ASTER AST_07/AST_09T/AST_L1T → {folder_label}/B1-B14.tif"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    band_files: Dict[str, List[Path]] = {}

    # ── 新格式（ROI mosaic 合并后直接放 raw_dir 顶层）──
    # AST_07 VNIR/SWIR: *_mosaic_SRF_VNIR_B01.tif / *_mosaic_SRF_SWIR_B04.tif
    for f in raw_dir.glob("*_mosaic_SRF_*.tif"):
        m = _ASTER07_BAND_RE.search(f.name)
        if m:
            bkey = _normalize_aster_bkey(m.group(1))
            band_files.setdefault(bkey, []).append(f)
    # AST_09T TIR: *_mosaic_SIR_TIR_B10..B14.tif
    for f in raw_dir.glob("*_mosaic_SIR_TIR_*.tif"):
        m = _ASTER09T_BAND_RE.search(f.name)
        if m:
            bkey = _normalize_aster_bkey(m.group(1))
            band_files.setdefault(bkey, []).append(f)
    # AST_L1T mosaic: *_mosaic_B01.tif / *_mosaic_B10.tif（命名更简洁，B 后直接接号）
    for f in raw_dir.glob("*_mosaic_B*.tif"):
        # 跳过已被上两条匹配到的 SRF_/SIR_TIR_ 文件名
        if "_mosaic_SRF_" in f.name or "_mosaic_SIR_TIR_" in f.name:
            continue
        m = re.search(r"_mosaic_(B\d+N?)\.tif$", f.name, re.IGNORECASE)
        if m:
            bkey = _normalize_aster_bkey(m.group(1))
            band_files.setdefault(bkey, []).append(f)

    # ── 旧格式（分景 _clipped.tif，保留兼容老的 raw 目录）──
    # AST_07: VNIR B1/B2/B3N, SWIR B4-B9
    ast07_dir = raw_dir / "AST_07"
    if ast07_dir.exists():
        for f in ast07_dir.glob("*_clipped.tif"):
            m = _ASTER07_BAND_RE.search(f.name)
            if m:
                bkey = _normalize_aster_bkey(m.group(1))
                band_files.setdefault(bkey, []).append(f)

    # AST_09T: TIR B10-B14
    ast09t_dir = raw_dir / "AST_09T"
    if ast09t_dir.exists():
        for f in ast09t_dir.glob("*_clipped.tif"):
            m = _ASTER09T_BAND_RE.search(f.name)
            if m:
                bkey = _normalize_aster_bkey(m.group(1))
                band_files.setdefault(bkey, []).append(f)

    # AST_L1T: 根目录下散落的分波段文件（VNIR_B01/B02/B03N, TIR_B10-B14）
    for f in raw_dir.glob("AST_L1T_*_clipped.tif"):
        m = _ASTER_L1T_BAND_RE.search(f.name)
        if m:
            bkey = _normalize_aster_bkey(m.group(1))
            band_files.setdefault(bkey, []).append(f)

    # ASTER TIR 波段（B10-B14）为热红外，用最近邻避免插值产生虚假温度梯度
    _ASTER_TIR_BANDS = {"B10", "B11", "B12", "B13", "B14"}

    for bkey, files in sorted(band_files.items(),
                               key=lambda x: (int(x[0][1:].rstrip("N") or 0))):
        best = _best_file(files, prefer_summer=season_is_summer)
        if not best:
            continue

        # 低覆盖率且有多景时，尝试 mosaic 多景提升覆盖率
        if len(files) > 1 and _valid_pixel_ratio(best) < 0.60:
            try:
                import numpy as np
                import rasterio
                from rasterio.merge import merge as _rmerge
                datasets = []
                for fp in files:
                    try:
                        datasets.append(rasterio.open(fp))
                    except Exception:
                        pass
                if len(datasets) > 1:
                    counts = [ds.count for ds in datasets]
                    maj = max(set(counts), key=counts.count)
                    datasets = [ds for ds in datasets if ds.count == maj]
                    with rasterio.open(files[0]) as ref:
                        _res = ref.res
                        _meta = ref.meta.copy()
                    merged_data, merged_transform = _rmerge(datasets, res=_res)
                    for ds in datasets:
                        ds.close()
                    _predictor = 3 if merged_data.dtype.kind == "f" else 2
                    _meta.update({
                        "count": merged_data.shape[0],
                        "height": merged_data.shape[1],
                        "width": merged_data.shape[2],
                        "transform": merged_transform,
                        "compress": "deflate",
                        "predictor": _predictor,
                    })
                    _tmp = out_dir / f"{bkey}_mosaic_tmp.tif"
                    with rasterio.open(_tmp, "w", **_meta) as dst_ds:
                        dst_ds.write(merged_data)
                    best = _tmp
            except Exception as _e:
                pass  # mosaic 失败则回退到单景

        dst = out_dir / f"{bkey}.tif"
        from rasterio.warp import Resampling
        rs = Resampling.nearest if bkey in _ASTER_TIR_BANDS else None
        # TIR 波段（90m 原始）体积天然偏小，降低截断检测门槛到 5KB
        _min = 5 * 1024 if bkey in _ASTER_TIR_BANDS else _MIN_TIFF_SIZE
        _copy_with_resample(best, dst, target_res=15.0, resampling=rs, min_size=_min)
        # 清理 mosaic 临时文件
        if best.name.endswith("_mosaic_tmp.tif"):
            best.unlink(missing_ok=True)
        done.append(dst)

    # 兜底：确保所有输出文件都有统计元数据（_copy_with_resample 已写，此处幂等补写）
    for dst in done:
        _write_statistics(dst)

    return done


def _package_dem(raw_dir: Path, season_dir: Path, folder_label: str = "DEM.tif") -> Optional[Path]:
    """DEM/SRTM → {folder_label} (or DEM.tif directly in season_dir)，重采样到15m"""
    # folder_label may be a bare filename like "DEM.tif" or a subfolder
    # Convention: if it ends with a raster extension, place directly in season_dir
    if '.' in folder_label:
        dst = season_dir / folder_label
    else:
        dst = season_dir / folder_label / "DEM.tif"
    if dst.exists():
        return dst

    # 优先用 clipped 版本
    candidates = (
        list(raw_dir.glob("*_clipped.tif")) +
        list(raw_dir.glob("*.tif"))
    )
    if candidates:
        from rasterio.warp import Resampling
        _copy_with_resample(candidates[0], dst, target_res=15.0, resampling=Resampling.nearest)
        return dst
    return None


def _package_ecostress(raw_dir: Path, season_dir: Path, folder_label: str = "地表温度.tif") -> Optional[Path]:
    """ECOSTRESS LST → {folder_label}"""
    if '.' in folder_label:
        dst = season_dir / folder_label
    else:
        dst = season_dir / folder_label / "地表温度.tif"
    if dst.exists():
        return dst

    lst_files = sorted(raw_dir.glob("*_LST_clipped.tif"))
    if lst_files:
        season_is_summer = (_SEASON_SUMMER in str(season_dir))
        best = _best_file(lst_files, prefer_summer=season_is_summer)
        if best:
            _copy(best, dst)
            return dst
    return None


def _package_emit(
    raw_dir: Path,
    season_dir: Path,
    folder_label: str = "EMIT L2A",
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Optional[Path]:
    """EMIT RFL → {folder_label}/SPECTRAL_IMAGE.tif（裁剪到 bbox 后转 GeoTIFF，重采样到30m）"""
    emit_dir = season_dir / folder_label
    emit_dir.mkdir(parents=True, exist_ok=True)
    dst = emit_dir / "SPECTRAL_IMAGE.tif"
    if dst.exists():
        return dst

    rfl_files = sorted(raw_dir.glob("EMIT_L2A_RFL_*.nc"))
    if not rfl_files:
        return None

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    best = _best_file(rfl_files, prefer_summer=season_is_summer)
    if best is None:
        return None

    try:
        from postprocess.nc_to_tiff import emit_nc_to_tiff
        # 在临时副本上操作，避免修改原始下载文件
        import shutil as _shutil
        tmp_nc = emit_dir / best.name
        _shutil.copy2(best, tmp_nc)
        # 先输出到临时 tif，再重采样到 30m
        tmp_tif = emit_dir / "SPECTRAL_IMAGE_60m.tif"
        emit_nc_to_tiff(tmp_nc, tmp_tif, bbox=bbox)
        tmp_nc.unlink(missing_ok=True)
        if tmp_tif.exists():
            done = resample_to_resolution(tmp_tif, dst, target_res=30.0)
            if not done:
                # 原始已是 30m 或更精细，直接用
                tmp_tif.rename(dst)
            else:
                tmp_tif.unlink(missing_ok=True)
        return dst if dst.exists() else None
    except Exception as e:
        print(f"    [警告] EMIT转TIFF失败: {e}，回退复制原始.nc")
        dst_nc = emit_dir / "SPECTRAL_IMAGE.nc"
        _copy(best, dst_nc)
        return dst_nc


def _package_hyperion(raw_dir: Path, season_dir: Path, folder_label: str = "Hyperion L1") -> Optional[Path]:
    """Hyperion EO-1 HDF4 → {folder_label}/SPECTRAL_IMAGE.hdf"""
    hyp_dir = season_dir / folder_label
    hyp_dir.mkdir(parents=True, exist_ok=True)
    dst = hyp_dir / "SPECTRAL_IMAGE.hdf"
    if dst.exists():
        return dst

    # 匹配 Hyperion HDF4 文件（EO1H*_HYP*.hdf 或 *.hdf）
    candidates = (
        list(raw_dir.glob("EO1H*HYP*.hdf")) +
        list(raw_dir.glob("EO1H*HYP*.HDF")) +
        list(raw_dir.glob("*.hdf")) +
        list(raw_dir.glob("*.HDF"))
    )
    if not candidates:
        return None

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    best = _best_file(candidates, prefer_summer=season_is_summer)
    if best:
        _copy(best, dst)
        return dst
    return None


def _package_aviris(raw_dir: Path, season_dir: Path, folder_label: str = "AVIRIS-NG") -> Optional[Path]:
    """AVIRIS-NG L2 → {folder_label}/SPECTRAL_IMAGE.*（保留原始格式）"""
    avi_dir = season_dir / folder_label
    avi_dir.mkdir(parents=True, exist_ok=True)

    # AVIRIS-NG L2 文件命名：ang*_rfl* 或 *.nc
    candidates = (
        list(raw_dir.glob("ang*_rfl*.nc")) +
        list(raw_dir.glob("ang*_rfl*")) +
        list(raw_dir.glob("*.nc")) +
        list(raw_dir.glob("*.img"))   # ENVI BSQ 格式
    )
    if not candidates:
        return None

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    best = _best_file(candidates, prefer_summer=season_is_summer)
    if best:
        dst = avi_dir / f"SPECTRAL_IMAGE{best.suffix}"
        _copy(best, dst)
        return dst
    return None


# PlanetScope 资产类型到波段编号的映射
# 4波段（BGRN）：B1=Blue, B2=Green, B3=Red, B4=NIR
# 8波段（AnalyticMS SuperDove）：B1=CoastalBlue, B2=Blue, B3=GreenI,
#   B4=Green, B5=Yellow, B6=Red, B7=RedEdge, B8=NIR
_PLANET_ASSET_BAND_COUNT = {
    "ortho_analytic_8b_sr": 8,
    "ortho_analytic_8b":    8,
    "ortho_analytic_4b_sr": 4,
    "ortho_analytic_4b":    4,
}


def _package_planet(raw_dir: Path, season_dir: Path, folder_label: str = "PlanetScope") -> List[Path]:
    """
    PlanetScope GeoTIFF → {folder_label}/B1-B8.tif
    """
    planet_dir = season_dir / folder_label
    planet_dir.mkdir(parents=True, exist_ok=True)
    done = []

    # 按资产类型分组候选文件
    tif_files = list(raw_dir.glob("*_ortho_analytic*.tif"))
    if not tif_files:
        tif_files = list(raw_dir.glob("*.tif"))

    if not tif_files:
        return done

    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    # 按资产类型选最佳文件
    by_asset: Dict[str, List[Path]] = {}
    for f in tif_files:
        asset = "unknown"
        for at in _PLANET_ASSET_BAND_COUNT:
            if at in f.name:
                asset = at
                break
        by_asset.setdefault(asset, []).append(f)

    for asset, files in by_asset.items():
        best = _best_file(files, prefer_summer=season_is_summer)
        if best:
            band_n = _PLANET_ASSET_BAND_COUNT.get(asset, "")
            suffix = f"_{band_n}B" if band_n else ""
            dst = planet_dir / f"PlanetScope{suffix}.tif"
            if not dst.exists():
                _copy(best, dst)
            done.append(dst)

    return done


_BASEMAP_TARGET_LONG_PX = 800   # 长边像素底线;小于这个就 cubic 上采样到这个


def _package_basemap(raw_area_dir: Path, season_dir: Path, geometry) -> List[Path]:
    """生成蚀变分析等覆盖图层用的真彩色底图(带坐标 GeoTIFF)。

    源 cascade(per-polygon 各自试,首个有数据的就用):
      1. raw/sentinel2/mosaic_TCI_10m.tif       — S2 官方真彩色 8-bit RGB
      2. raw/sentinel2/mosaic_TCI_20m.tif       — 退到 20m
      3. raw/sentinel2/mosaic_B04/B03/B02_10m   — 合成 RGB(p2-p98 拉伸到 uint8)
      4. raw/landsat/<scene>/*_red/_green/_blue_clipped.TIF — 每景一个候选,Landsat 真彩色 30m

    每个 polygon 输出一张(bbox + 5% buffer 矩形裁剪 → 若长边 < 800 px,cubic 上采到 800)。
    多源补齐:polygon A 在 S2 mosaic 里有数据走 S2,polygon B 在 S2 里是 nodata 自动 fallback 到 Landsat。
    """
    if geometry is None:
        return []
    try:
        import rasterio
        from rasterio.warp import Resampling, reproject, transform_bounds
        from rasterio.windows import from_bounds as window_from_bounds, transform as window_transform
        from rasterio.transform import Affine
        import numpy as np
    except ImportError:
        print("    [底图] rasterio/numpy 不可用,跳过")
        return []

    s2_dir = raw_area_dir / "sentinel2"
    ls_dir = raw_area_dir / "landsat"

    # ── 1. 收集所有候选源的 spec(惰性 — 不立刻读)─────────────
    # 每条 spec: {kind: tci|synth, paths: [...], label: str, _cache: None}
    specs: List[Dict] = []
    if s2_dir.exists():
        tci10 = s2_dir / "mosaic_TCI_10m.tif"
        tci20 = s2_dir / "mosaic_TCI_20m.tif"
        if tci10.exists():
            specs.append({"kind": "tci", "paths": [tci10], "label": "S2 TCI 10m"})
        elif tci20.exists():
            specs.append({"kind": "tci", "paths": [tci20], "label": "S2 TCI 20m"})
        r = s2_dir / "mosaic_B04_10m.tif"
        g = s2_dir / "mosaic_B03_10m.tif"
        b = s2_dir / "mosaic_B02_10m.tif"
        if r.exists() and g.exists() and b.exists():
            specs.append({"kind": "synth", "paths": [r, g, b], "label": "S2 B04/B03/B02 合成 10m"})
    if ls_dir.exists():
        for scene_dir in sorted(ls_dir.iterdir()):
            if not scene_dir.is_dir():
                continue
            rl = list(scene_dir.glob("*_red_clipped.TIF"))
            gl = list(scene_dir.glob("*_green_clipped.TIF"))
            bl = list(scene_dir.glob("*_blue_clipped.TIF"))
            if rl and gl and bl:
                specs.append({"kind": "synth", "paths": [rl[0], gl[0], bl[0]],
                              "label": f"Landsat 真彩色 30m ({scene_dir.name[:25]})"})

    if not specs:
        print(f"    [底图] {season_dir.name}: 无可用真彩色源,跳过")
        return []

    # ── 内部工具 ───────────────────────────────────────────────
    # 缓存已加载的源(rgb_arr, transform, crs, h, w);per-polygon 试源时按需加载
    def _load_spec(spec):
        """加载 spec → 返回 (rgb_arr(3,H,W) uint8, transform, crs, h, w);失败返 None"""
        if spec.get("_cache") is not None:
            return spec["_cache"]
        try:
            if spec["kind"] == "tci":
                with rasterio.open(spec["paths"][0]) as ds:
                    rgb = ds.read(out_dtype="uint8")
                    cached = (rgb, ds.transform, ds.crs, ds.height, ds.width)
            else:
                paths = spec["paths"]
                with rasterio.open(paths[0]) as ref:
                    ref_shape = (ref.height, ref.width)
                    ref_transform = ref.transform
                    ref_crs = ref.crs
                bands_u8 = []
                for p in paths:
                    with rasterio.open(p) as ds:
                        if (ds.height, ds.width) == ref_shape and ds.crs == ref_crs:
                            arr = ds.read(1); nd = ds.nodata
                        else:
                            arr = np.zeros(ref_shape, dtype=ds.dtypes[0])
                            reproject(
                                source=rasterio.band(ds, 1), destination=arr,
                                src_transform=ds.transform, src_crs=ds.crs,
                                dst_transform=ref_transform, dst_crs=ref_crs,
                                resampling=Resampling.cubic,
                            )
                            nd = ds.nodata
                    valid = arr.ravel()
                    if nd is not None and not (isinstance(nd, float) and np.isnan(nd)):
                        valid = valid[valid != nd]
                    if np.issubdtype(valid.dtype, np.floating):
                        valid = valid[~np.isnan(valid)]
                    valid = valid[valid != 0]
                    if valid.size == 0:
                        bands_u8.append(np.zeros(ref_shape, dtype="uint8"))
                        continue
                    lo = float(np.percentile(valid, 2))
                    hi = float(np.percentile(valid, 98))
                    if hi <= lo:
                        hi = lo + 1.0
                    scaled = np.clip((arr.astype("float32") - lo) / (hi - lo) * 255.0, 0, 255).astype("uint8")
                    bands_u8.append(scaled)
                rgb = np.stack(bands_u8, axis=0)
                cached = (rgb, ref_transform, ref_crs, ref_shape[0], ref_shape[1])
        except Exception as e:
            print(f"    [底图] 源加载失败 {spec['label']}: {e}")
            cached = None
        spec["_cache"] = cached
        return cached

    def _crop_to_poly(rgb_arr, src_transform, src_crs, src_h, src_w, poly):
        """切 poly bbox + 5% buffer → 返回 (sub(3,h,w), sub_transform) 或 None。"""
        minx, miny, maxx, maxy = poly.bounds
        bx, by = (maxx - minx) * 0.05, (maxy - miny) * 0.05
        ext_wgs = (minx - bx, miny - by, maxx + bx, maxy + by)
        try:
            ext_src = transform_bounds("EPSG:4326", src_crs, *ext_wgs, densify_pts=21)
        except Exception:
            ext_src = ext_wgs
        src_bounds = (
            src_transform.c,
            src_transform.f + src_transform.e * src_h,
            src_transform.c + src_transform.a * src_w,
            src_transform.f,
        )
        ix0 = max(ext_src[0], src_bounds[0]); iy0 = max(ext_src[1], src_bounds[1])
        ix1 = min(ext_src[2], src_bounds[2]); iy1 = min(ext_src[3], src_bounds[3])
        if ix0 >= ix1 or iy0 >= iy1:
            return None
        try:
            win = window_from_bounds(ix0, iy0, ix1, iy1, transform=src_transform).round_offsets().round_lengths()
        except Exception:
            return None
        h, w = int(win.height), int(win.width)
        if h <= 0 or w <= 0:
            return None
        r0, c0 = int(win.row_off), int(win.col_off)
        sub = rgb_arr[:, r0:r0 + h, c0:c0 + w]
        if not (sub.sum(axis=0) > 0).any():
            return None  # 全 nodata, 试下一源
        return sub, window_transform(win, src_transform)

    def _upsample_if_small(sub, sub_transform, target_long):
        """长边 < target 就 cubic 上采到 target,transform 等比缩。"""
        _, h, w = sub.shape
        long_px = max(h, w)
        if long_px >= target_long:
            return sub, sub_transform
        scale = target_long / long_px
        new_h, new_w = int(round(h * scale)), int(round(w * scale))
        upsampled = np.zeros((3, new_h, new_w), dtype="uint8")
        new_transform = sub_transform * Affine.scale(w / new_w, h / new_h)
        for bi in range(3):
            reproject(
                source=sub[bi], destination=upsampled[bi],
                src_transform=sub_transform, src_crs="EPSG:4326",   # crs 占位,不参与计算
                dst_transform=new_transform, dst_crs="EPSG:4326",
                resampling=Resampling.cubic,
            )
        return upsampled, new_transform

    # ── 2. per-polygon 试 cascade ──────────────────────────────
    parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    single = (len(parts) == 1)
    season_dir.mkdir(parents=True, exist_ok=True)
    done: List[Path] = []

    for i, poly in enumerate(parts, 1):
        fname = "底图_RGB.tiff" if single else f"底图_RGB_矿权{i}.tiff"
        out_path = season_dir / fname
        if out_path.exists() and out_path.stat().st_size >= _MIN_TIFF_SIZE:
            done.append(out_path)
            continue

        picked = None  # (sub, sub_transform, src_crs, src_label)
        for spec in specs:
            loaded = _load_spec(spec)
            if loaded is None:
                continue
            rgb_arr, src_transform, src_crs, src_h, src_w = loaded
            cropped = _crop_to_poly(rgb_arr, src_transform, src_crs, src_h, src_w, poly)
            if cropped is not None:
                picked = (*cropped, src_crs, spec["label"])
                break

        if picked is None:
            print(f"    [底图] 第 {i} 块所有源都无数据(可能下载未覆盖此区),跳过")
            continue

        sub, sub_transform, src_crs, src_label = picked
        native_h, native_w = sub.shape[1], sub.shape[2]
        sub, sub_transform = _upsample_if_small(sub, sub_transform, _BASEMAP_TARGET_LONG_PX)
        h, w = sub.shape[1], sub.shape[2]

        # 兼容性优先:LZW + 显式 photometric=RGB,Apple Preview / QuickLook /
        # sips / Windows 资源管理器都能直接预览(deflate+predictor 是 GDAL 自有特性,
        # Apple libtiff 不识别会导致 sips/QL 解码失败 → 用户无法在 Finder 双击打开)
        use_tile = (h >= 256 and w >= 256)
        out_profile = {
            "driver":      "GTiff",
            "height":      h, "width": w, "count": 3, "dtype": "uint8",
            "crs":         src_crs, "transform": sub_transform,
            "nodata":      0,
            "compress":    "lzw",
            "photometric": "rgb",
            "interleave":  "pixel",
        }
        if use_tile:
            out_profile.update(tiled=True,
                               blockxsize=min(512, (w // 16) * 16),
                               blockysize=min(512, (h // 16) * 16))
        with rasterio.open(out_path, "w", **out_profile) as dst:
            dst.write(sub)
        _write_statistics(out_path)
        done.append(out_path)
        suffix = f"  ⤴ 上采到 {w}×{h}" if (native_w, native_h) != (w, h) else ""
        print(f"    [底图] 第 {i} 块 → {fname}  ({src_label}, 原始 {native_w}×{native_h} px{suffix})")

    return done


def _package_derive(raw_area_dir: Path, season_dir: Path):
    """将已计算的衍生产品（地表温度、温度梯度、温度异常梯度、OTCI）复制到交付目录"""
    # 衍生产品在 downloads/{area}/ 根目录（由 derive_all 生成）
    # 地表温度.tif 已存在则跳过 —— ECOSTRESS 路径在主循环里会先于本函数运行
    for fname in ("地表温度.tif", "温度梯度.tif", "温度异常梯度.tif", "OTCI.tiff"):
        src = raw_area_dir / fname
        dst = season_dir / fname
        if src.exists() and not dst.exists():
            _copy(src, dst)


def _package_prisma(raw_dir: Path, season_dir: Path, folder_label: str = "PRISMA L2D") -> Optional[Path]:
    """PRISMA HDF5 → {folder_label}/SPECTRAL_IMAGE.he5 (+ SPECTRAL_IMAGE.hdr)"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "SPECTRAL_IMAGE.he5"
    if dst.exists():
        _ensure_prisma_hdr(dst, out_dir, raw_dir)   # 已有 he5 也补 .hdr(幂等)
        return dst

    candidates = list(raw_dir.rglob("*.he5")) + list(raw_dir.rglob("*.HE5"))
    if not candidates:
        return None

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    best = _best_file(candidates, prefer_summer=season_is_summer)
    if best:
        _copy(best, dst)
        _ensure_prisma_hdr(dst, out_dir, raw_dir)
        return dst
    return None


def _ensure_prisma_hdr(he5_dst: Path, out_dir: Path, raw_dir: Path) -> None:
    """确保 out_dir 下有 SPECTRAL_IMAGE.hdr(交付规则要求 .hdr)。

    优先:raw 包里若自带 ENVI .hdr,直接复制。
    兜底:用 h5py 读 .he5 立方体形状 + 波长,写一个最小 ENVI 头。
    ⚠️ 当前无真实 PRISMA 数据可验证,HDF5 内部结构按 L2D 常见布局推断,属**best-effort**:
       读不到/h5py 缺失则跳过(不写错误的 .hdr,也不让打包失败),由自检标记缺 .hdr。
    幂等:已存在非空 .hdr 则跳过。
    """
    hdr = out_dir / "SPECTRAL_IMAGE.hdr"
    if hdr.exists() and hdr.stat().st_size > 0:
        return
    # 1) raw 自带 .hdr
    try:
        for h in list(raw_dir.rglob("*.hdr")) + list(raw_dir.rglob("*.HDR")):
            if h.is_file() and h.stat().st_size > 0:
                _copy(h, hdr)
                return
    except Exception:
        pass
    # 2) 从 he5 推断
    try:
        import h5py  # type: ignore
        import numpy as _np
    except Exception:
        print("    [PRISMA] 无 h5py,跳过 .hdr 生成(需人工补)")
        return
    try:
        with h5py.File(he5_dst, "r") as f:
            cubes = []
            f.visititems(lambda name, obj: cubes.append((name, obj))
                         if isinstance(obj, h5py.Dataset) and obj.ndim == 3 else None)
            if not cubes:
                print("    [PRISMA] he5 内未找到 3D 立方体,跳过 .hdr")
                return
            # 取体积最大的 3D 数据集当主立方体
            name, ds = max(cubes, key=lambda t: int(_np.prod(t[1].shape)))
            shape = ds.shape
            # 波长(L2D 根属性常见 List_Cw_Vnir / List_Cw_Swir)
            waves = []
            for attr in ("List_Cw_Vnir", "List_Cw_Swir"):
                v = f.attrs.get(attr)
                if v is not None:
                    waves.extend([float(x) for x in _np.array(v).ravel() if float(x) > 0])
            waves = sorted(waves)
            nbands = len(waves) if waves else None
            # 立方体三轴里,band 轴 = 与波段数吻合的那个;否则取中间轴
            if nbands and nbands in shape:
                bidx = shape.index(nbands)
            else:
                bidx = 1
                nbands = shape[bidx]
            spatial = [shape[i] for i in range(3) if i != bidx]
            lines, samples = spatial[0], spatial[1]
            interleave = {0: "bsq", 1: "bil", 2: "bip"}[bidx]
            _write_envi_hdr(hdr, samples=samples, lines=lines, bands=nbands,
                            interleave=interleave, wavelengths=waves)
            print(f"    [PRISMA] 生成 .hdr (best-effort): {samples}x{lines}x{nbands} {interleave}")
    except Exception as e:
        print(f"    [PRISMA] .hdr 生成失败(跳过,需人工): {e}")


def _write_envi_hdr(path: Path, samples: int, lines: int, bands: int,
                    interleave: str = "bil", wavelengths=None) -> None:
    """写一个最小可用的 ENVI 头(data type 默认 12=uint16,PRISMA L2D 反射率常见)。"""
    lines_out = [
        "ENVI",
        "description = {PRISMA L2D SPECTRAL_IMAGE (auto-generated header, best-effort)}",
        f"samples = {samples}",
        f"lines = {lines}",
        f"bands = {bands}",
        "header offset = 0",
        "file type = HDF5",
        "data type = 12",
        f"interleave = {interleave}",
        "byte order = 0",
    ]
    if wavelengths:
        lines_out.append("wavelength units = Nanometers")
        lines_out.append("wavelength = {" + ", ".join(f"{w:.3f}" for w in wavelengths) + "}")
    path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")


def _package_enmap(raw_dir: Path, season_dir: Path, folder_label: str = "EnMAP L2A") -> Optional[Path]:
    """EnMAP DLR FTPS 下载产物 → {folder_label}/SPECTRAL_IMAGE.{tif,he5}

    raw_dir 里典型布局:
      - *.tar.gz / *.tgz   FTPS 拉回的归档（包含 GeoTIFF 或 HDF5）
      - 已解压后的 *_SPECTRAL_IMAGE.TIF / *.tif / *.he5（如果之前手动解开过）
      - enmap_debug.log / *.png （Playwright 调试残留,忽略）
    """
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)

    # 已就位的最终产物,直接复用
    for fname in ("SPECTRAL_IMAGE.tif", "SPECTRAL_IMAGE.TIF", "SPECTRAL_IMAGE.he5"):
        if (out_dir / fname).exists():
            return out_dir / fname

    # 先扫已解压的栅格(包括子目录)
    rasters: List[Path] = []
    for ext in ("*.tif", "*.TIF", "*.tiff", "*.he5", "*.HE5", "*.h5"):
        rasters.extend(raw_dir.rglob(ext))
    # 过滤掉调试截图、报告等(虽然扩展名不会撞,但 dsda 包里可能藏元数据 tif)
    rasters = [r for r in rasters if r.is_file() and r.stat().st_size > _MIN_TIFF_SIZE]

    # 没有现成栅格 → 解压归档。DLR EnMAP 交付为 tar.gz,内部还嵌套一层 .ZIP,
    # 真正的高光谱立方体 *-SPECTRAL_IMAGE.TIF(约 1GB)在内层 ZIP 里,
    # 必须两层都解开 —— 旧逻辑只解 tar、不解内层 ZIP,故下载成功也产不出 EnMAP。
    if not rasters:
        import tarfile as _tarfile
        import zipfile as _zipfile

        # ── 第一层: tar.gz / tgz / tar ──
        tar_archives = (
            list(raw_dir.glob("*.tar.gz")) +
            list(raw_dir.glob("*.tgz")) +
            list(raw_dir.glob("*.tar"))
        )
        for arc in tar_archives:
            extract_dir = raw_dir / arc.stem.replace(".tar", "")
            extract_dir.mkdir(exist_ok=True)
            try:
                with _tarfile.open(arc, "r:*") as tf:
                    tf.extractall(extract_dir)
                print(f"    [EnMAP] 解压 {arc.name}")
            except Exception as e:
                print(f"    [警告] EnMAP 解压失败 {arc.name}: {e}")
                continue

        # ── 第二层: 递归解开内层 .ZIP(EnMAP L1B/L2A 立方体在此)──
        seen_zips = set()
        for zp in list(raw_dir.rglob("*.ZIP")) + list(raw_dir.rglob("*.zip")):
            if zp in seen_zips:            # macOS 大小写不敏感卷,两次 glob 会重复
                continue
            seen_zips.add(zp)
            zextract = zp.with_suffix("")  # 同名子目录
            marker = zextract / ".extracted"
            if marker.exists():            # 幂等:已解压过则跳过(避免重解 1GB)
                continue
            zextract.mkdir(parents=True, exist_ok=True)
            try:
                with _zipfile.ZipFile(zp) as zf:
                    zf.extractall(zextract)
                marker.touch()
                print(f"    [EnMAP] 解压内层 {zp.name}")
            except Exception as e:
                print(f"    [警告] EnMAP 内层 ZIP 解压失败 {zp.name}: {e}")
                continue

        for ext in ("*.tif", "*.TIF", "*.tiff", "*.he5", "*.HE5", "*.h5"):
            rasters.extend(raw_dir.rglob(ext))
        rasters = [r for r in rasters if r.is_file() and r.stat().st_size > _MIN_TIFF_SIZE]

    if not rasters:
        return None

    # 只保留 SPECTRAL_IMAGE 主数据,排除 QL_ 质量层/像素掩膜/快视
    spectral = [r for r in rasters if "SPECTRAL_IMAGE" in r.name.upper()
                and "QL_" not in r.name.upper()]
    if spectral:
        rasters = spectral

    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    # EnMAP L1B 把高光谱立方体拆成 VNIR(可见近红外)+ SWIR(短波红外)两个 TIF,
    # 二者缺一不可 —— 必须都交付,不能只挑一个。L2A 通常是单个 SPECTRAL_IMAGE.TIF。
    def _arm(name: str) -> Optional[str]:
        u = name.upper()
        if "_VNIR" in u:
            return "VNIR"
        if "_SWIR" in u:
            return "SWIR"
        return None

    armed = {}
    for r in rasters:
        a = _arm(r.name)
        if a:
            armed.setdefault(a, []).append(r)

    written: List[Path] = []
    if armed:
        # 分臂交付: SPECTRAL_IMAGE_VNIR.tif / SPECTRAL_IMAGE_SWIR.tif
        for arm_name, files in armed.items():
            best = _best_file(files, prefer_summer=season_is_summer)
            if not best:
                continue
            suf = best.suffix.lower()
            base = f"SPECTRAL_IMAGE_{arm_name}"
            if suf in (".he5", ".h5"):
                dst = out_dir / f"{base}.he5"
            elif suf in (".tif", ".tiff"):
                dst = out_dir / f"{base}.tif"
            else:
                dst = out_dir / f"{base}{best.suffix}"
            _copy(best, dst)
            written.append(dst)
            # 交付规则要求随附 METADATA.XML(在立方体 TIF 同目录)
            _copy_enmap_metadata(best.parent, out_dir)
        if written:
            return written[0]

    # 单文件场景(L2A 或非分臂): 落到 SPECTRAL_IMAGE.*
    best = _best_file(rasters, prefer_summer=season_is_summer)
    if not best:
        return None
    target_suffix = best.suffix.lower()
    if target_suffix in (".tif", ".tiff"):
        dst = out_dir / "SPECTRAL_IMAGE.tif"
    elif target_suffix in (".he5", ".h5"):
        dst = out_dir / "SPECTRAL_IMAGE.he5"
    else:
        dst = out_dir / f"SPECTRAL_IMAGE{best.suffix}"
    _copy(best, dst)
    _copy_enmap_metadata(best.parent, out_dir)
    return dst


def _copy_enmap_metadata(src_parent: Path, out_dir: Path) -> None:
    """把 EnMAP 产物随附的 *-METADATA.XML 复制为 out_dir/METADATA.XML。
    DLR 包内每景立方体 TIF 同目录有 ...-METADATA.XML(及 ...-HISTORY.XML,需排除)。
    交付规则要求 EnMAP 文件夹含 METADATA.XML。幂等:已存在则跳过。
    """
    dst = out_dir / "METADATA.XML"
    if dst.exists() and dst.stat().st_size > 0:
        return
    # 先在立方体同目录找,找不到再在更上层 raw 树里找
    candidates: List[Path] = []
    search_dirs = [src_parent]
    try:
        for xml in list(src_parent.glob("*.XML")) + list(src_parent.glob("*.xml")):
            candidates.append(xml)
    except Exception:
        pass
    meta = [p for p in candidates
            if "METADATA" in p.name.upper() and "HISTORY" not in p.name.upper()
            and p.is_file() and p.stat().st_size > 0]
    if meta:
        # 取最大的(主元数据通常比附属 xml 大)
        _copy(max(meta, key=lambda p: p.stat().st_size), dst)


def _package_desis(raw_dir: Path, season_dir: Path, folder_label: str = "DESIS L2A") -> Optional[Path]:
    """DESIS GeoTIFF → {folder_label}/SPECTRAL_IMAGE.tif"""
    candidates = list(raw_dir.rglob("*.tif")) + list(raw_dir.rglob("*.tiff"))
    if not candidates:
        return None

    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "SPECTRAL_IMAGE.tif"
    if dst.exists():
        return dst

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    best = _best_file(candidates, prefer_summer=season_is_summer)
    if best:
        _copy(best, dst)
        return dst
    return None


def _package_zy1(raw_dir: Path, season_dir: Path, folder_label: str = "ZY-1 02D AHSI") -> Optional[Path]:
    """ZY-1 02D GeoTIFF → {folder_label}/SPECTRAL_IMAGE.tif"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "SPECTRAL_IMAGE.tif"
    if dst.exists():
        return dst

    candidates = list(raw_dir.rglob("*.tif")) + list(raw_dir.rglob("*.tiff"))
    if not candidates:
        return None

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    best = _best_file(candidates, prefer_summer=season_is_summer)
    if best:
        _copy(best, dst)
        return dst
    return None


def _package_oneatlas(raw_dir: Path, season_dir: Path, folder_label: str = "SPOT 6/7") -> List[Path]:
    """SPOT 6/7 / Pleiades → {folder_label}/PAN.tif + MS.tif"""
    import zipfile as _zipfile
    import tempfile as _tempfile

    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []

    # 收集所有 tif（包括 ZIP 解压后的）
    tif_candidates: List[Path] = list(raw_dir.rglob("*.tif")) + list(raw_dir.rglob("*.tiff"))

    # 如有 ZIP 则解压找 tif
    for zp in raw_dir.glob("*.zip"):
        try:
            extract_dir = raw_dir / zp.stem
            extract_dir.mkdir(exist_ok=True)
            with _zipfile.ZipFile(zp, "r") as zf:
                zf.extractall(extract_dir)
            tif_candidates += list(extract_dir.rglob("*.tif")) + list(extract_dir.rglob("*.tiff"))
        except Exception:
            pass

    if not tif_candidates:
        return done

    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    # 按单波段/多波段区分 PAN vs MS
    try:
        import rasterio as _rio
        pan_files, ms_files = [], []
        for f in tif_candidates:
            try:
                with _rio.open(f) as src:
                    if src.count == 1:
                        pan_files.append(f)
                    else:
                        ms_files.append(f)
            except Exception:
                pass
    except ImportError:
        # rasterio 不可用时按文件名启发
        pan_files = [f for f in tif_candidates if "pan" in f.name.lower()]
        ms_files  = [f for f in tif_candidates if f not in pan_files]

    for tag, pool, name in (("PAN", pan_files, "PAN.tif"), ("MS", ms_files, "MS.tif")):
        if pool:
            best = _best_file(pool, prefer_summer=season_is_summer)
            if best:
                dst = out_dir / name
                _copy(best, dst)
                done.append(dst)

    return done


def _package_worldview(raw_dir: Path, season_dir: Path, folder_label: str = "WorldView-2") -> List[Path]:
    """WorldView-2/3 GeoTIFF → {folder_label}/PAN.tif + MS.tif"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []

    tif_candidates = list(raw_dir.rglob("*.tif")) + list(raw_dir.rglob("*.tiff"))
    if not tif_candidates:
        return done

    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    try:
        import rasterio as _rio
        pan_files, ms_files = [], []
        for f in tif_candidates:
            try:
                with _rio.open(f) as src:
                    if src.count == 1:
                        pan_files.append(f)
                    else:
                        ms_files.append(f)
            except Exception:
                pass
    except ImportError:
        pan_files = [f for f in tif_candidates if "pan" in f.name.lower() or "_p_" in f.name.lower()]
        ms_files  = [f for f in tif_candidates if f not in pan_files]

    for pool, name in ((pan_files, "PAN.tif"), (ms_files, "MS.tif")):
        if pool:
            best = _best_file(pool, prefer_summer=season_is_summer)
            if best:
                dst = out_dir / name
                _copy(best, dst)
                done.append(dst)

    return done


# Landsat TIRS 热红外波段映射（文件名关键字 → 输出文件名）
_TIRS_BAND_MAP = {
    "lwir11":   "B10.tif",
    "lwir12":   "B11.tif",
    "st_b10":   "ST_B10.tif",
    "st_atran": "ST_ATRAN.tif",
    "st_cdist": "ST_CDIST.tif",
    "st_drad":  "ST_DRAD.tif",
    "st_emis":  "ST_EMIS.tif",
    "st_emsd":  "ST_EMSD.tif",
    "st_trad":  "ST_TRAD.tif",
    "st_urad":  "ST_URAD.tif",
    "qa_pixel": "QA_PIXEL.tif",
}


def _package_landsat_tirs(raw_dir: Path, season_dir: Path, folder_label: str = "Landsat TIRS",
                          geometry=None) -> List[Path]:
    """Landsat TIRS → {folder_label}/B10.tif, B11.tif, ST_*.tif"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []

    def _scan() -> Dict[str, List[Path]]:
        bf: Dict[str, List[Path]] = {}
        for f in raw_dir.rglob("*_clipped.TIF"):
            name_lower = f.name.lower()
            for bname, out_name in _TIRS_BAND_MAP.items():
                if f"_{bname}_clipped" in name_lower:
                    bf.setdefault(out_name, []).append(f)
                    break
        return bf

    band_files: Dict[str, List[Path]] = _scan()
    if not band_files and geometry is not None:
        if _autoclip_landsat_raw(raw_dir, geometry) > 0:
            band_files = _scan()

    season_is_summer = (_SEASON_SUMMER in str(season_dir))
    for out_name, files in sorted(band_files.items()):
        best = _best_file(files, prefer_summer=season_is_summer)
        if best:
            dst = out_dir / out_name
            _copy(best, dst)
            done.append(dst)

    return done


def _package_sentinel1(raw_dir: Path, season_dir: Path, folder_label: str = "Sentinel-1 GRD") -> List[Path]:
    """Sentinel-1 GRD → {folder_label}/景号目录（含 .SAFE 结构）"""
    done = []
    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    # 优先 terrain_corrected/ 子目录（地理编码后的 GeoTIFF）
    tc_dir = raw_dir / "terrain_corrected"
    if tc_dir.exists():
        for pol, out_name in (("vv", "VV.tif"), ("vh", "VH.tif")):
            candidates = [f for f in tc_dir.rglob("*.tif") if pol in f.name.lower()]
            if candidates:
                best = _best_file(candidates, prefer_summer=season_is_summer)
                if best:
                    out_dir = season_dir / folder_label
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dst = out_dir / out_name
                    _copy(best, dst)
                    done.append(dst)
        if done:
            return done

    # 优先复制已解压的 .SAFE 目录（zip 损坏时的回退）
    safe_dirs = [d for d in raw_dir.iterdir()
                 if d.is_dir() and d.name.endswith(".SAFE")]
    # 也检查一层子目录（下载器把 SAFE 放在同名子目录里）
    for sub in raw_dir.iterdir():
        if sub.is_dir() and not sub.name.endswith(".SAFE"):
            for d in sub.iterdir():
                if d.is_dir() and d.name.endswith(".SAFE"):
                    safe_dirs.append(d)

    if safe_dirs:
        out_dir = season_dir / folder_label
        out_dir.mkdir(parents=True, exist_ok=True)
        # 按日期选最新的 5 景（避免复制过多）
        safe_dirs = sorted(safe_dirs, key=lambda d: d.name, reverse=True)[:5]
        for safe in safe_dirs:
            dst = out_dir / safe.name
            if not dst.exists():
                shutil.copytree(safe, dst)
            done.append(dst)
        return done

    # 最后回退：复制完好的 zip
    for zp in raw_dir.glob("*.zip"):
        try:
            import zipfile as _zf
            with _zf.ZipFile(zp) as z:
                z.testzip()
            out_dir = season_dir / folder_label
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / zp.name
            _copy(zp, dst)
            done.append(dst)
        except Exception:
            pass

    return done


def _package_alos(raw_dir: Path, season_dir: Path, folder_label: str = "ALOS PALSAR") -> List[Path]:
    """ALOS PALSAR / ALOS-2 → {folder_label}/ 复制已解压 CEOS 目录"""
    done = []

    # 优先复制已解压目录（含 VOL-/LED-/IMG- 等 CEOS 文件）
    ceos_dirs = [
        d for d in raw_dir.iterdir()
        if d.is_dir() and any(
            (d / f"VOL-{d.name}").exists() or
            any(f.name.startswith("VOL-") for f in d.iterdir())
            for _ in [None]  # 只执行一次
        )
    ]

    # 简化判断：有子文件（非空目录）的目录视为已解压产品
    ceos_dirs = [d for d in raw_dir.iterdir()
                 if d.is_dir() and any(d.iterdir())]

    if ceos_dirs:
        out_dir = season_dir / folder_label
        out_dir.mkdir(parents=True, exist_ok=True)
        ceos_dirs = sorted(ceos_dirs, key=lambda d: d.name, reverse=True)[:5]
        for ceos in ceos_dirs:
            dst = out_dir / ceos.name
            if not dst.exists():
                shutil.copytree(ceos, dst)
            done.append(dst)
        return done

    # 回退：尝试复制完好的 zip
    for zp in raw_dir.glob("*.zip"):
        try:
            import zipfile as _zf
            with _zf.ZipFile(zp) as z:
                z.testzip()
            out_dir = season_dir / folder_label
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / zp.name
            _copy(zp, dst)
            done.append(dst)
        except Exception:
            pass

    return done


def _package_opera(raw_dir: Path, season_dir: Path, folder_label: str = "OPERA RTC-S1") -> List[Path]:
    """OPERA RTC-S1 → {folder_label}/VV.tif + VH.tif"""
    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    for pol, out_name in (("_vv", "VV.tif"), ("_vh", "VH.tif")):
        candidates = [
            f for f in raw_dir.rglob("*.tif")
            if pol in f.name.lower()
        ]
        if candidates:
            best = _best_file(candidates, prefer_summer=season_is_summer)
            if best:
                dst = out_dir / out_name
                _copy(best, dst)
                done.append(dst)

    return done


# MODIS 波段关键字 → 输出文件名
_MODIS_BAND_RE = re.compile(
    r'_(B\d{2}|NDVI|EVI|sur_refl_b\d{2}|LST_Day|LST_Night)',
    re.IGNORECASE,
)


def _package_modis(raw_dir: Path, season_dir: Path, folder_label: str = "MODIS") -> List[Path]:
    """MODIS GeoTIFF → {folder_label}/B01.tif, NDVI.tif … (HDF 已被下载器转 tif)"""
    tif_files = list(raw_dir.rglob("*.tif")) + list(raw_dir.rglob("*.tiff"))
    if not tif_files:
        return []  # 无文件，不创建目录

    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    band_files: Dict[str, List[Path]] = {}
    for f in tif_files:
        m = _MODIS_BAND_RE.search(f.name)
        if m:
            key = m.group(1).upper()
            key = re.sub(r'SUR_REFL_(B\d{2})', r'\1', key)
            band_files.setdefault(key, []).append(f)
        else:
            band_files.setdefault(f.stem, []).append(f)

    for key, files in sorted(band_files.items()):
        best = _best_file(files, prefer_summer=season_is_summer)
        if best:
            out_name = f"{key}.tif"
            dst = out_dir / out_name
            _copy(best, dst)
            done.append(dst)

    return done


def _package_gedi(raw_dir: Path, season_dir: Path, folder_label: str = "GEDI L2A") -> Optional[Path]:
    """
    GEDI L2A HDF5 → {folder_label}/GEDI_L2A.csv
    提取所有 beam 的高质量激光点（quality_flag=1），输出含经纬度+高程+rh98 的 CSV。
    依赖 h5py；若不可用则回退为直接复制 .h5 文件。
    """
    candidates = list(raw_dir.rglob("*.h5")) + list(raw_dir.rglob("*.H5"))
    if not candidates:
        return None

    out_dir = season_dir / folder_label
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_csv = out_dir / "GEDI_L2A.csv"
    if dst_csv.exists():
        return dst_csv

    season_is_summer = (_SEASON_SUMMER in str(season_dir))

    try:
        import h5py
        import csv as _csv

        rows = []
        for h5_path in sorted(candidates):
            try:
                with h5py.File(h5_path, "r") as f:
                    beams = [k for k in f.keys() if k.startswith("BEAM")]
                    for bname in beams:
                        b = f[bname]
                        if "lat_lowestmode" not in b or "lon_lowestmode" not in b:
                            continue
                        lats  = b["lat_lowestmode"][:]
                        lons  = b["lon_lowestmode"][:]
                        elevs = b["elev_lowestmode"][:]
                        qual  = b["quality_flag"][:] if "quality_flag" in b else None
                        rh    = b["rh"][:] if "rh" in b else None

                        for i in range(len(lats)):
                            if qual is not None and qual[i] != 1:
                                continue
                            rh98 = float(rh[i, 98]) if rh is not None else ""
                            rows.append({
                                "lon": round(float(lons[i]), 6),
                                "lat": round(float(lats[i]), 6),
                                "elev_m": round(float(elevs[i]), 2),
                                "rh98_m": round(rh98, 2) if rh98 != "" else "",
                                "source": h5_path.name,
                            })
            except Exception as e:
                print(f"    [GEDI] 读取失败 {h5_path.name}: {e}")

        if not rows:
            raise RuntimeError("无有效激光点数据")

        with open(dst_csv, "w", newline="", encoding="utf-8") as fout:
            writer = _csv.DictWriter(fout, fieldnames=["lon", "lat", "elev_m", "rh98_m", "source"])
            writer.writeheader()
            writer.writerows(rows)

        print(f"    [GEDI→CSV] {len(rows)} 个质量点 → {dst_csv.name}")
        return dst_csv

    except ImportError:
        # h5py 不可用，回退复制原始 h5
        best = _best_file(candidates, prefer_summer=season_is_summer)
        if best:
            dst_h5 = out_dir / "GEDI_L2A.h5"
            _copy(best, dst_h5)
            return dst_h5
        return None
    except Exception as e:
        print(f"    [警告] GEDI转CSV失败: {e}，回退复制原始h5")
        best = _best_file(candidates, prefer_summer=season_is_summer)
        if best:
            dst_h5 = out_dir / "GEDI_L2A.h5"
            _copy(best, dst_h5)
            return dst_h5
        return None


# ═══════════════════════════════════════════════════════════════
# Schema 驱动的整理函数（支持 Web UI 架构设置）
# ═══════════════════════════════════════════════════════════════

# sensor.id → 对应的打包函数（接受 raw_dir, season_dir, folder_label）
_SENSOR_PACK_FUNCS = {
    "sentinel2":    _package_sentinel2,
    "landsat":      _package_landsat,
    "landsat7":     _package_landsat7,
    "landsat_tirs": _package_landsat_tirs,
    "aster":        _package_aster,
    "aster_l1t":    _package_aster,
    "dem":          _package_dem,
    "srtm":         _package_dem,
    "ecostress":    _package_ecostress,
    "emit":         _package_emit,
    "hyperion":     _package_hyperion,
    "aviris":       _package_aviris,
    "planet":       _package_planet,
    "prisma":       _package_prisma,
    "enmap":        _package_enmap,
    "desis":        _package_desis,
    "zy1":          _package_zy1,
    "spot67":       _package_oneatlas,
    "pleiades":     _package_oneatlas,
    "wv2":          _package_worldview,
    "wv3":          _package_worldview,
    "sentinel1":    _package_sentinel1,
    "alos":         _package_alos,
    "alos2":        _package_alos,
    "opera":        _package_opera,
    "modis":        _package_modis,
    "gedi":         _package_gedi,
}


def _try_satellite_overview(
    delivery_dir: Path,
    kml_path: Path,
    geometry=None,
):
    """尝试下载 Google Maps 卫星底图，若无 API Key 则跳过"""
    try:
        import yaml
        cred_path = Path(__file__).parent.parent / "config" / "credentials.yaml"
        if not cred_path.exists():
            return
        with open(cred_path, encoding="utf-8") as f:
            creds = yaml.safe_load(f) or {}
        api_key = (creds.get("google_maps") or {}).get("api_key")
        if not api_key:
            print("  [卫星底图] 未配置 google_maps.api_key，跳过（在 config/credentials.yaml 中添加）")
            return
        proxy = (creds.get("google_maps") or {}).get("proxy")  # 可选，如 "http://127.0.0.1:7890"

        # 从 KML 获取 geometry/bbox（若未传入）
        if geometry is None:
            try:
                from downloader.kml_parser import parse_kml
                geometry, bbox, _ = parse_kml(str(kml_path))
            except Exception:
                return
        else:
            bbox = geometry.bounds  # (min_x, min_y, max_x, max_y)

        from postprocess.satellite_overview import download_satellite_overview
        download_satellite_overview(
            bbox=bbox,
            api_key=api_key,
            delivery_dir=delivery_dir,
            geometry=geometry,
            maptype="satellite",
            proxy=proxy,
        )
    except Exception as e:
        print(f"  [卫星底图] 生成失败: {e}")


def package_delivery_from_schema(
    raw_area_dir: Path,
    kml_path: Path,
    delivery_root: Path,
    area_label: str,
    schema: dict,
) -> Path:
    """
    按 schema.yaml 定义整理交付目录（schema 驱动版本）。

    Parameters
    ----------
    raw_area_dir  : downloads/{area}/ 原始下载根目录
    kml_path      : 对应的 KML/ovKML 文件路径
    delivery_root : 交付目录根（如 ./delivery/）
    area_label    : 交付目录名
    schema        : 从 schema.yaml 加载的 dict，含 seasons/sensors 字段

    Returns
    -------
    Path : 交付目录路径
    """
    seasons_cfg = schema.get("seasons", {})
    summer_months = set(seasons_cfg.get("summer", [6, 7, 8]))
    winter_months = set(seasons_cfg.get("winter", [11, 12, 1, 2, 3]))
    season_summer_label = seasons_cfg.get("season_summer_label", _SEASON_SUMMER)
    season_winter_label = seasons_cfg.get("season_winter_label", _SEASON_WINTER)

    def season_of(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return season_summer_label
        m = dt.month
        if m in summer_months:
            return season_summer_label
        if m in winter_months:
            return season_winter_label
        # 过渡季节：4/5 → 夏季，9/10 → 冬季
        return season_summer_label if m in {4, 5} else season_winter_label

    delivery_dir = Path(delivery_root) / area_label
    delivery_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[打包] 交付目录: {delivery_dir}")

    # 复制 KML 边界文件 + 解析 geometry(供 S2/Landsat 兜底裁剪用)
    kml_path = Path(kml_path)
    geometry = None
    if kml_path.exists():
        kml_dst = delivery_dir / kml_path.name
        _copy(kml_path, kml_dst)
        print(f"  [KML] {kml_path.name}")
        try:
            from downloader.kml_parser import parse_kml
            geometry, _, _ = parse_kml(str(kml_path))
        except Exception as _e:
            print(f"  [警告] KML 解析失败,S2/Landsat 兜底裁剪不可用: {_e}")

    sensors = schema.get("sensors", [])

    import inspect as _inspect
    for season_name in (season_summer_label, season_winter_label):
        season_dir = delivery_dir / season_name
        season_is_summer = (season_name == season_summer_label)
        season_key = "summer" if season_is_summer else "winter"
        season_label = "夏季" if season_is_summer else "冬季"

        for sensor_def in sensors:
            sensor_id  = sensor_def.get("id", "")
            raw_dir_name = sensor_def.get("raw_dir", sensor_id)
            folder_label = sensor_def.get("label", sensor_id)
            allowed_seasons = sensor_def.get("seasons", ["summer", "winter"])

            # 检查该传感器是否参与当前季节
            if season_key not in allowed_seasons:
                continue

            raw_dir = Path(raw_area_dir) / raw_dir_name
            if not raw_dir.exists():
                continue

            pack_fn = _SENSOR_PACK_FUNCS.get(sensor_id)
            if pack_fn is None:
                continue

            sig = _inspect.signature(pack_fn)
            extra = {"geometry": geometry} if "geometry" in sig.parameters and geometry is not None else {}
            r = pack_fn(raw_dir, season_dir, folder_label, **extra)
            if r:
                count = len(r) if isinstance(r, list) else 1
                print(f"  [{season_label}] {folder_label}: {count} 个文件")

        # 真彩色底图(蚀变分析等覆盖图层用,带坐标 GeoTIFF)
        basemap_files = _package_basemap(Path(raw_area_dir), season_dir, geometry)
        if basemap_files:
            print(f"  [{season_label}] 底图_RGB: {len(basemap_files)} 块")

        # 衍生产品（固定，不受 schema 控制）
        _package_derive(Path(raw_area_dir), season_dir)
        derived = [f for f in ("温度梯度.tif", "温度异常梯度.tif", "OTCI.tiff")
                   if (season_dir / f).exists()]
        if derived:
            print(f"  [{season_label}] 衍生产品: {', '.join(derived)}")

        # 删除无数据的空季节目录
        if season_dir.exists():
            has_content = any(season_dir.iterdir())
            if not has_content:
                try:
                    season_dir.rmdir()
                except OSError:
                    pass

    print(f"  [完成] 交付目录整理完成: {delivery_dir}")

    # 卫星底图（Google Maps）
    _try_satellite_overview(delivery_dir, kml_path)

    # 下载报告（自动生成 docx）
    try:
        sensor_ids = [s.get("id", "") for s in schema.get("sensors", [])]
        from postprocess.report import generate_report
        generate_report(
            delivery_dir=delivery_dir,
            area_label=area_label,
            sensors_attempted=sensor_ids,
            summary={},
            raw_area_dir=Path(raw_area_dir),
        )
    except Exception as _e:
        print(f"  [警告] 报告生成失败: {_e}")

    # 清理所有空文件夹
    for dirpath, dirnames, filenames in os.walk(delivery_dir, topdown=False):
        p = Path(dirpath)
        if p == delivery_dir:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass

    return delivery_dir


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def package_delivery(
    raw_area_dir: Path,
    kml_path: Path,
    delivery_root: Path,
    area_label: str,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    sensors_attempted: Optional[List[str]] = None,
    download_summary: Optional[Dict[str, int]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    task_start_time=None,
    task_end_time=None,
    incremental: bool = False,
) -> Path:
    """
    将一个区域的原始下载数据整理为标准交付目录。

    Parameters
    ----------
    raw_area_dir      : downloads/{area}/ 原始下载根目录
    kml_path          : 对应的 KML/ovKML 文件路径
    delivery_root     : 交付目录根（如 ./delivery/）
    area_label        : 交付目录名（如 "twz"），即 KML stem
    bbox              : (min_lon, min_lat, max_lon, max_lat) 用于裁剪 .nc 文件
    sensors_attempted : 本次尝试下载的传感器列表（用于报告）
    download_summary  : {sensor_id: file_count} 下载结果（用于报告）
    start_date        : 搜索起始日期（用于报告）
    end_date          : 搜索结束日期（用于报告）
    task_start_time   : 任务开始时间 datetime（用于报告耗时统计）
    task_end_time     : 任务结束时间 datetime（用于报告耗时统计）

    Returns
    -------
    Path : 交付目录路径
    """
    delivery_dir = delivery_root / area_label
    delivery_dir.mkdir(parents=True, exist_ok=True)

    if incremental:
        # 增量补包模式:已存在的文件靠 _copy() 内部的"存在跳过"机制自然不动;
        # 这里只是显式 log,提示这是异步数据到货后的补漏,而非首次打包
        print(f"\n[补包] 增量模式 — 交付目录: {delivery_dir}")
        print(f"[补包] 已存在的传感器文件将跳过,仅补充本次新到的数据")
    else:
        print(f"\n[打包] 交付目录: {delivery_dir}")

    # 1. 复制 KML 边界文件
    kml_dst = delivery_dir / kml_path.name
    _copy(kml_path, kml_dst)
    print(f"  [KML] {kml_path.name}")

    # 解析一次 KML → geometry,用于 Sentinel-2/Landsat 兜底裁剪
    geometry = None
    try:
        from downloader.kml_parser import parse_kml
        geometry, _bbox_parsed, _ = parse_kml(str(kml_path))
        if bbox is None:
            bbox = _bbox_parsed
    except Exception as _e:
        print(f"  [警告] KML 解析失败,Sentinel-2/Landsat 兜底裁剪不可用: {_e}")

    # 已通过专用分支处理的 sensor id(避免后面通用循环重复跑)
    handled_ids = {
        "sentinel2", "landsat", "landsat7", "aster", "aster_l1t",
        "dem", "srtm", "ecostress", "emit", "hyperion", "aviris", "planet",
    }

    # 2. 整理夏季和冬季两套数据
    for season_name in (_SEASON_SUMMER, _SEASON_WINTER):
        season_dir = delivery_dir / season_name
        season_is_summer = (season_name == _SEASON_SUMMER)
        season_label = "夏季" if season_is_summer else "冬季"
        files_done = []

        # Sentinel-2
        s2_dir = raw_area_dir / "sentinel2"
        if s2_dir.exists():
            r = _package_sentinel2(s2_dir, season_dir, geometry=geometry)
            files_done += r
            if r:
                print(f"  [{season_label}] Sentinel 2 L2: {len(r)} 个波段文件")

        # Landsat
        ls_dir = raw_area_dir / "landsat"
        if ls_dir.exists():
            r = _package_landsat(ls_dir, season_dir, geometry=geometry)
            files_done += r
            if r:
                print(f"  [{season_label}] Landsat 8 L2: {len(r)} 个波段文件")

        # ASTER L2
        aster_dir = raw_area_dir / "aster"
        if aster_dir.exists():
            r = _package_aster(aster_dir, season_dir)
            files_done += r
            if r:
                print(f"  [{season_label}] ASTER L2: {len(r)} 个波段文件")

        # ASTER L1T（独立 raw 目录，独立交付子目录）
        aster_l1t_dir = raw_area_dir / "aster_l1t"
        if aster_l1t_dir.exists():
            r = _package_aster(aster_l1t_dir, season_dir, folder_label="ASTER L1T")
            files_done += r
            if r:
                print(f"  [{season_label}] ASTER L1T: {len(r)} 个波段文件")

        # DEM（只放夏季，冬季共用同一个）
        for dem_sensor in ("dem", "srtm"):
            dem_dir = raw_area_dir / dem_sensor
            if dem_dir.exists():
                p = _package_dem(dem_dir, season_dir)
                if p:
                    print(f"  [{season_label}] DEM.tif ← {dem_sensor}")
                    break

        # ECOSTRESS 地表温度
        eco_dir = raw_area_dir / "ecostress"
        if eco_dir.exists():
            p = _package_ecostress(eco_dir, season_dir)
            if p:
                print(f"  [{season_label}] 地表温度.tif ← ecostress")

        # EMIT 高光谱（仅夏季）
        if season_is_summer:
            emit_dir = raw_area_dir / "emit"
            if emit_dir.exists():
                p = _package_emit(emit_dir, season_dir, bbox=bbox)
                if p:
                    print(f"  [{season_label}] EMIT L2A/SPECTRAL_IMAGE.tif")

        # Hyperion EO-1 高光谱（仅夏季，历史存档数据）
        if season_is_summer:
            hyp_dir = raw_area_dir / "hyperion"
            if hyp_dir.exists():
                p = _package_hyperion(hyp_dir, season_dir)
                if p:
                    print(f"  [{season_label}] Hyperion L1/SPECTRAL_IMAGE.hdf")

        # AVIRIS-NG 机载高光谱（仅夏季）
        if season_is_summer:
            avi_dir = raw_area_dir / "aviris"
            if avi_dir.exists():
                p = _package_aviris(avi_dir, season_dir)
                if p:
                    print(f"  [{season_label}] AVIRIS-NG/SPECTRAL_IMAGE.*")

        # Landsat 7 ETM+
        ls7_dir = raw_area_dir / "landsat7"
        if ls7_dir.exists():
            r = _package_landsat7(ls7_dir, season_dir, geometry=geometry)
            files_done += r
            if r:
                print(f"  [{season_label}] Landsat 7 ETM+: {len(r)} 个波段文件")

        # PlanetScope 高分辨率
        planet_dir_raw = raw_area_dir / "planet"
        if planet_dir_raw.exists():
            r = _package_planet(planet_dir_raw, season_dir)
            files_done += r
            if r:
                print(f"  [{season_label}] PlanetScope: {len(r)} 个文件")

        # ── 通用兜底:遍历注册表里未被上面专用分支覆盖的 sensor ──
        # 覆盖范围:prisma / enmap / zy1 / desis / sentinel1 / alos / alos2 /
        #          opera / gedi / modis / landsat_tirs 等
        # daemon 在异步数据到货后调本函数补包,只有走这里才能把 PRISMA/EnMAP 真正写进交付目录
        for sensor_id, pack_fn in _SENSOR_PACK_FUNCS.items():
            if sensor_id in handled_ids:
                continue
            sensor_raw = raw_area_dir / sensor_id
            if not sensor_raw.exists():
                continue
            try:
                # 给 landsat_tirs 传 geometry,其它 fn 签名里没 geometry 就别强塞
                import inspect as _inspect
                sig = _inspect.signature(pack_fn)
                kwargs = {"geometry": geometry} if "geometry" in sig.parameters and geometry is not None else {}
                r = pack_fn(sensor_raw, season_dir, **kwargs)
            except Exception as _e:
                print(f"  [警告] {sensor_id} 打包异常: {_e}")
                continue
            if r:
                count = len(r) if isinstance(r, list) else 1
                print(f"  [{season_label}] {sensor_id}: {count} 个文件")

        # 真彩色底图(蚀变分析等覆盖图层用,带坐标 GeoTIFF)
        basemap_files = _package_basemap(raw_area_dir, season_dir, geometry)
        if basemap_files:
            print(f"  [{season_label}] 底图_RGB: {len(basemap_files)} 块")

        # 衍生产品
        _package_derive(raw_area_dir, season_dir)
        derived = [f for f in ("温度梯度.tif", "温度异常梯度.tif", "OTCI.tiff")
                   if (season_dir / f).exists()]
        if derived:
            print(f"  [{season_label}] 衍生产品: {', '.join(derived)}")

        # 删除完全没产物的空季节目录
        if season_dir.exists() and not any(season_dir.iterdir()):
            try:
                season_dir.rmdir()
            except OSError:
                pass

    # 清理所有空文件夹（传感器无数据时 mkdir 留下的空子目录）
    for dirpath, dirnames, filenames in os.walk(delivery_dir, topdown=False):
        p = Path(dirpath)
        if p == delivery_dir:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass

    print(f"  [完成] {delivery_dir}")

    # 卫星底图（Google Maps）
    _try_satellite_overview(delivery_dir, kml_path)

    # 下载报告（自动生成 docx）
    try:
        from postprocess.report import generate_report
        generate_report(
            delivery_dir=delivery_dir,
            area_label=area_label,
            sensors_attempted=sensors_attempted or [],
            summary=download_summary or {},
            start_date=start_date,
            end_date=end_date,
            start_time=task_start_time,
            end_time=task_end_time,
            raw_area_dir=raw_area_dir,
        )
    except Exception as _e:
        print(f"  [警告] 报告生成失败: {_e}")

    # ── 自描述清单 delivery.json:供下游按 delivery_id / 几何覆盖定位(KML 改名不再断) ──
    try:
        import sys as _sys
        if "/opt/deepexplor-services" not in _sys.path:
            _sys.path.insert(0, "/opt/deepexplor-services")
        from commons.delivery import write_manifest as _write_manifest
        _man = _write_manifest(delivery_dir, bbox=list(bbox) if bbox else None,
                               roi_name=area_label)
        print(f"  [交付] delivery.json 已写: delivery_id={_man.get('delivery_id') if _man else '?'}")
    except Exception as _e:
        print(f"  [警告] delivery.json 写入失败(忽略): {_e}")

    return delivery_dir


def package_all(
    downloads_root: Path,
    kml_root: Path,
    delivery_root: Path,
) -> Dict[str, Path]:
    """
    批量打包 downloads_root 下所有区域。

    Parameters
    ----------
    downloads_root : downloads/ 根目录
    kml_root       : KML 文件所在目录（或单个KML路径）
    delivery_root  : 交付目录根

    Returns
    -------
    dict: {area_name: delivery_path}
    """
    results = {}

    # 收集 KML 文件
    kml_root = Path(kml_root)
    if kml_root.is_file():
        kml_files = [kml_root]
    else:
        kml_files = (
            list(kml_root.glob("*.kml")) +
            list(kml_root.glob("*.ovkml")) +
            list(kml_root.glob("*.KML")) +
            list(kml_root.glob("*.OVKML"))
        )

    for kml in kml_files:
        area = kml.stem
        raw_dir = Path(downloads_root) / area
        if not raw_dir.exists():
            print(f"  [跳过] 无下载数据: {area}")
            continue
        p = package_delivery(raw_dir, kml, Path(delivery_root), area)
        results[area] = p

    return results
