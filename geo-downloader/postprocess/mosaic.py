"""
Mosaic：多景拼接后裁剪
当研究区域（KML）跨越多景卫星图像边界时，先将多景合并成完整覆盖，再裁剪到 KML 范围。

使用条件：
  - 下载景数 > 1
  - 没有任何单景能独立完整覆盖 KML（≥99% 面积）

支持格式：GeoTIFF（逐景拼接）、Sentinel-2 ZIP（按波段分组拼接）
不支持：EMIT .nc、HDF/H5（跳过，原有逐景裁剪处理）
"""

import tarfile
import zipfile
from pathlib import Path
from typing import List, Optional

try:
    import numpy as np
    import rasterio
    from rasterio.merge import merge as rasterio_merge
    from rasterio.enums import Resampling
    from rasterio.crs import CRS
    from shapely.geometry import box
    from shapely.ops import transform
    import pyproj
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

_RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".jp2"}


def covers_geometry(raster_path: Path, geometry) -> bool:
    """
    检测单景栅格是否完整覆盖 KML 几何体（交集面积 ≥ 99%）。

    Parameters
    ----------
    raster_path : GeoTIFF 文件路径
    geometry    : Shapely 几何体（WGS84）

    Returns
    -------
    True — 该单景已完整覆盖，无需拼接
    """
    if not HAS_DEPS:
        return False
    try:
        with rasterio.open(raster_path) as src:
            if src.crs is None:
                return False
            try:
                epsg = src.crs.to_epsg()
                dst_crs = pyproj.CRS.from_epsg(epsg) if epsg else pyproj.CRS.from_wkt(src.crs.to_wkt())
            except Exception:
                dst_crs = pyproj.CRS.from_wkt(src.crs.to_wkt())
            proj = pyproj.Transformer.from_crs(
                pyproj.CRS("EPSG:4326"), dst_crs, always_xy=True
            ).transform
            geom_proj = transform(proj, geometry)
            raster_box = box(*src.bounds)
            if geom_proj.area == 0:
                return raster_box.contains(geom_proj)
            overlap = raster_box.intersection(geom_proj).area / geom_proj.area
            return overlap >= 0.99
    except Exception:
        return False


def select_covering_scenes(candidates, geometry, max_scenes: int = 30,
                           same_period_days: int = 30):
    """
    贪心覆盖选景：从候选景中选出能完整覆盖 geometry 的最小子集。

    在覆盖贪心之上叠加两条约束（保物理量、零像素改动，纯选片阶段）：
      1) 云量作为主排序键之一：同一轮内覆盖增益接近(相差<5%)时优先选低云景，
         避免高云大景把低云景挤掉；
      2) 时序聚类：用 ``_acq_date`` 把候选按 ±``same_period_days`` 天聚成"时相簇"，
         优先在单个时相簇内完成覆盖，避免一份镶嵌拼进相隔数月的景而制造辐射边界。
         没有任何单簇能完整覆盖时，才跨时相补洞并 print 警示。

    每景需携带 ``_footprint`` 属性(Shapely Polygon, EPSG:4326)，没有则被跳过。
    候选可携带 ``_acq_date``(YYYY-MM-DD) 与 ``_cloud_cover``；取不到时对应约束自动降级。

    Parameters
    ----------
    candidates       : list  — search() 返回的候选景列表
    geometry         : Shapely 几何体(WGS84)
    max_scenes       : int   — 安全上限，防止选景过多
    same_period_days : int   — 时相簇日期窗口(天)；<=0 关闭时序聚类=旧行为

    Returns
    -------
    list — 覆盖所需的景子集
    """
    if not HAS_DEPS:
        return candidates

    # 过滤出有 footprint 的候选(STAC items 通常是 dict,各 sensor 把 _footprint
    # 既可能写成属性也可能写成 dict key,两种都得能读到)
    def _get_fp(c):
        v = getattr(c, "_footprint", None)
        if v is not None:
            return v
        if isinstance(c, dict):
            return c.get("_footprint")
        return None

    def _get_cloud(c):
        v = getattr(c, "_cloud_cover", None)
        if v is None and isinstance(c, dict):
            v = c.get("_cloud_cover")
        try:
            return float(v) if v is not None else 100.0
        except (TypeError, ValueError):
            return 100.0

    def _get_date(c):
        """返回 datetime.date 或 None。优先 _acq_date，再退回常见日期字段。"""
        from datetime import date as _date
        raw = getattr(c, "_acq_date", None)
        if raw is None and isinstance(c, dict):
            raw = c.get("_acq_date")
        if raw is None and isinstance(c, dict):
            raw = ((c.get("properties") or {}).get("datetime")
                   or (c.get("ContentDate") or {}).get("Start")
                   or c.get("time_start") or c.get("date"))
        if not raw:
            return None
        try:
            return _date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            return None

    fp_candidates = [(c, _get_fp(c)) for c in candidates]
    fp_candidates = [(c, fp) for c, fp in fp_candidates if fp is not None]

    if not fp_candidates:
        # 没有 footprint 信息，无法评估覆盖，原样返回
        return candidates

    geom_area = geometry.area
    if geom_area == 0:
        # 点/线几何体，取第一景即可
        return [fp_candidates[0][0]] if fp_candidates else candidates[:1]

    # ── 贪心覆盖核心：返回 (selected, coverage_fraction) ──────────────
    def _greedy_cover(pool):
        # 单景已≥99%覆盖：直接返回(多景满足时取最低云)
        singles = []
        for c, fp in pool:
            try:
                ov = fp.intersection(geometry).area / geom_area
            except Exception:
                continue
            if ov >= 0.99:
                singles.append(c)
        if singles:
            singles.sort(key=_get_cloud)
            return [singles[0]], 1.0

        selected, uncovered = [], geometry
        while not uncovered.is_empty and len(selected) < max_scenes:
            if uncovered.area / geom_area < 0.01:
                break
            gains = []
            for c, fp in pool:
                if c in selected:
                    continue
                try:
                    g = fp.intersection(uncovered).area
                except Exception:
                    continue
                if g > 0:
                    gains.append((c, fp, g))
            if not gains:
                break
            max_gain = max(g for _, _, g in gains)
            # 增益在最优 95% 以内者视为"接近",其中优先最低云(再按增益降序)
            near = [(c, fp, g) for c, fp, g in gains if g >= 0.95 * max_gain]
            best_c, best_fp, _g = min(near, key=lambda t: (_get_cloud(t[0]), -t[2]))
            selected.append(best_c)
            try:
                uncovered = uncovered.difference(best_fp)
            except Exception:
                pass
        cov = 1.0 - (uncovered.area / geom_area if not uncovered.is_empty else 0.0)
        return selected, cov

    # ── 时序聚类：优先在单个时相簇内完整覆盖 ──────────────────────────
    if same_period_days and same_period_days > 0:
        dated = [(c, fp, _get_date(c)) for c, fp in fp_candidates]
        if any(d is not None for _, _, d in dated):
            with_date = sorted([t for t in dated if t[2] is not None], key=lambda t: t[2])
            # 单链聚类：按日期排序,相邻间隔 > same_period_days 断簇
            clusters, cur, prev = [], [], None
            for c, fp, d in with_date:
                if prev is not None and (d - prev).days > same_period_days:
                    clusters.append(cur)
                    cur = []
                cur.append((c, fp))
                prev = d
            if cur:
                clusters.append(cur)

            # 评估各簇:能覆盖≥99%者择优(景少优先,其次云量和小)
            best = None  # (selected, n_scenes, cloud_sum)
            for cl in clusters:
                sel, cov = _greedy_cover(cl)
                if cov >= 0.99 and sel:
                    cand = (sel, len(sel), sum(_get_cloud(c) for c in sel))
                    if best is None or (cand[1], cand[2]) < (best[1], best[2]):
                        best = cand
            if best is not None:
                print(f"  [覆盖选景] 时序聚类({same_period_days}天)：单时相簇 "
                      f"{best[1]} 景完整覆盖(云量和={best[2]:.0f})")
                return best[0]
            if len(clusters) > 1:
                print(f"  [覆盖选景] 警告：无单一时相簇可完整覆盖，跨 {len(clusters)} 个时相"
                      f"镶嵌，可能存在辐射边界(可调 --same-period-days)")

    # ── 全局贪心(关闭聚类 / 无日期 / 跨时相补洞) ────────────────────
    selected, cov = _greedy_cover(fp_candidates)
    if cov < 0.99:
        print(f"  [覆盖选景] 警告：{len(selected)} 景仅覆盖 {cov * 100:.1f}%，无法完全覆盖研究区")
    else:
        print(f"  [覆盖选景] 已选 {len(selected)} 景，可完整覆盖研究区")

    return selected if selected else candidates[:1]


def mosaic_and_clip(
    file_paths: List[Path],
    geometry,
    output_path: Path,
    rrn: bool = False,
) -> Path:
    """
    将多景 GeoTIFF 拼接后裁剪到 geometry，输出单一文件。

    拼接完成后删除所有输入文件。

    Parameters
    ----------
    file_paths  : 多景 GeoTIFF 路径列表（同一传感器、同一波段或多波段）
    geometry    : 裁剪几何体（WGS84 Shapely）
    output_path : 输出路径
    rrn         : 拼接前是否做逐波段相对辐射归一化（消除缝处辐射台阶，默认关）

    Returns
    -------
    裁剪后的拼接文件路径
    """
    if not HAS_DEPS:
        raise ImportError("缺少依赖: rasterio numpy，请运行: pip install rasterio numpy")

    if output_path.exists():
        # 已存在则直接返回，清理输入文件
        for f in file_paths:
            f.unlink(missing_ok=True)
        return output_path

    # 相对辐射归一化（就地写回，返回相同路径；失败不影响后续拼接）
    if rrn and len(file_paths) > 1:
        try:
            from postprocess.radiometric import normalize_to_reference
            file_paths = normalize_to_reference(file_paths)
        except Exception as e:
            print(f"    [RRN] 跳过（{e}）")

    # 打开所有数据集
    datasets = []
    for fp in file_paths:
        try:
            datasets.append(rasterio.open(fp))
        except Exception as e:
            print(f"    [拼接] 无法打开 {fp.name}: {e}，跳过该景")

    if not datasets:
        raise RuntimeError("拼接失败：所有输入文件均无法打开")

    # 过滤波段数不一致的景（rasterio merge 要求所有输入 count 相同）
    counts = [ds.count for ds in datasets]
    majority_count = max(set(counts), key=counts.count)
    filtered = [ds for ds in datasets if ds.count == majority_count]
    skipped = len(datasets) - len(filtered)
    if skipped:
        print(f"    [拼接] 跳过 {skipped} 个波段数不一致的景（期望 count={majority_count}）")
        # 关闭被过滤掉的数据集
        for ds in datasets:
            if ds not in filtered:
                ds.close()
    datasets = filtered

    if not datasets:
        raise RuntimeError("拼接失败：过滤后无有效景")

    # 取第一景分辨率作为基准，避免各景分辨率不一致导致 merge buffer 错位
    with rasterio.open(file_paths[0]) as ref:
        target_res = ref.res
        base_meta = ref.meta.copy()
        ref_nodata = ref.nodata
        ref_dtype = ref.dtypes[0]

    # nodata：优先源文件值，缺失时按 dtype 推断（浮点→nan，整型→0）。
    # 显式传给 merge 可避免边缘 0 值被当作有效数据、在缝/边界制造伪异常。
    _is_float = np.dtype(ref_dtype).kind == "f"
    if ref_nodata is not None:
        fill_nodata = ref_nodata
    elif _is_float:
        fill_nodata = float("nan")
    else:
        fill_nodata = 0
    # 重采样显式化：浮点(反射率/热红外)用 bilinear 抑制缝效应；
    # 整型(scaled-int 反射率 / DN / QA / SCL 分类)用 nearest 保精确值、不破坏类别。
    _res_method = Resampling.bilinear if _is_float else Resampling.nearest

    try:
        merged_data, merged_transform = rasterio_merge(
            datasets, res=target_res, nodata=fill_nodata, resampling=_res_method
        )
    finally:
        for ds in datasets:
            ds.close()

    _predictor = 3 if merged_data.dtype.kind == "f" else 2
    # 用第一景的元数据作为基础
    meta = base_meta
    meta.update({
        "driver": "GTiff",
        "count":  merged_data.shape[0],
        "height": merged_data.shape[1],
        "width":  merged_data.shape[2],
        "transform": merged_transform,
        "nodata": fill_nodata,
        "compress": "deflate",
        "predictor": _predictor,
    })

    # 写临时拼接文件
    tmp_path = output_path.with_suffix(".mosaic_tmp.tif")
    try:
        with rasterio.open(tmp_path, "w", **meta) as dst:
            dst.write(merged_data)

        # 裁剪复用现有 clip_raster
        from postprocess.clip import clip_raster
        result = clip_raster(tmp_path, geometry, output_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    # 删除原始多景文件
    for f in file_paths:
        f.unlink(missing_ok=True)

    return result


def mosaic_sentinel2_zips(
    zip_paths: List[Path],
    geometry,
    output_dir: Path,
    rrn: bool = False,
) -> List[Path]:
    """
    处理多景 Sentinel-2 ZIP：解压 → 按波段分组 → 各波段拼接裁剪。

    Parameters
    ----------
    zip_paths  : 多景 Sentinel-2 ZIP 文件列表
    geometry   : 裁剪几何体（WGS84 Shapely）
    output_dir : 输出目录

    Returns
    -------
    拼接裁剪后的波段文件列表
    """
    # 解压所有 ZIP
    band_files: dict = {}   # band_key -> List[Path]
    extract_dirs: List[Path] = []

    for zp in zip_paths:
        extract_dir = output_dir / zp.stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        extract_dirs.append(extract_dir)
        print(f"    [拼接] 解压 {zp.name}")
        try:
            if tarfile.is_tarfile(zp):
                with tarfile.open(zp, "r:*") as tf:
                    tf.extractall(extract_dir)
            elif zipfile.is_zipfile(zp):
                with zipfile.ZipFile(zp, "r") as zf:
                    zf.extractall(extract_dir)
            else:
                # 检查是否是截断的 ZIP
                is_truncated = False
                try:
                    with open(zp, 'rb') as f:
                        is_truncated = f.read(2) == b'PK'
                except Exception:
                    pass
                if is_truncated:
                    size_kb = zp.stat().st_size // 1024
                    print(f"    [警告] {zp.name} 是截断的 ZIP 文件（{size_kb} KB），删除后需重新下载")
                    zp.unlink(missing_ok=True)
                else:
                    print(f"    [警告] {zp.name} 既非 ZIP 也非 tar 格式，跳过")
                continue
        except Exception as e:
            print(f"    [警告] 解压失败 {zp.name}: {e}")
            continue
        zp.unlink(missing_ok=True)

        # 收集栅格文件，按波段名分组
        for ext in _RASTER_EXTENSIONS:
            for rf in extract_dir.rglob(f"*{ext}"):
                band_key = _extract_band_key(rf)
                band_files.setdefault(band_key, []).append(rf)

    if not band_files:
        return []

    # 每个波段独立拼接裁剪
    results = []
    for band_key, files in sorted(band_files.items()):
        if len(files) == 1:
            # 单景直接裁剪
            from postprocess.clip import clip_raster
            try:
                out = clip_raster(files[0], geometry)
                results.append(out)
            except RuntimeError as e:
                if "__no_overlap__" in str(e):
                    files[0].unlink(missing_ok=True)
                else:
                    print(f"    [警告] 裁剪失败 {files[0].name}: {e}")
                    results.append(files[0])
        else:
            out_path = output_dir / f"mosaic_{band_key}.tif"
            try:
                merged = mosaic_and_clip(files, geometry, out_path, rrn=rrn)
                results.append(merged)
                print(f"    [拼接完成] {out_path.name}")
            except Exception as e:
                print(f"    [警告] 波段 {band_key} 拼接失败，逐景裁剪: {e}")
                from postprocess.clip import clip_raster
                for f in files:
                    try:
                        results.append(clip_raster(f, geometry))
                    except Exception:
                        if f.exists():
                            results.append(f)

    return [r for r in results if r and r.exists()]


def _extract_band_key(file_path: Path) -> str:
    """
    从文件名提取波段标识键，用于跨景分组。

    Sentinel-2 文件名示例：
      T50RKV_20240110T023051_B04_10m.jp2  → "B04_10m"
      T50RKV_20240110T023051_B8A.jp2      → "B8A"
    ASTER 文件名示例：
      AST_07_..._SRF_VNIR_B01.tif        → "SRF_VNIR_B01"
      AST_09T_..._SIR_TIR_B10.tif        → "SIR_TIR_B10"
      AST_08_..._SKT.tif                 → "SKT"
    其他传感器：直接用文件名（去除日期/景号等前缀后的部分）
    """
    import re as _re
    name = file_path.stem
    # ASTER 格式：匹配 _SRF_VNIR_Bxx、_SRF_SWIR_Bxx、_SIR_TIR_Bxx、_SKT
    m = _re.search(r'_((?:SRF_(?:VNIR|SWIR)|SIR_TIR)_B\d+N?|SKT)$', name, _re.IGNORECASE)
    if m:
        return m.group(1)
    # Sentinel-2 格式：TXXXXX_日期_波段[_分辨率]
    parts = name.split("_")
    # 找到形如 B01, B02, B8A, TCI 等波段标识
    for i, p in enumerate(parts):
        if (p.startswith("B") and len(p) <= 4) or p in ("TCI", "SCL", "AOT", "WVP", "SNW"):
            return "_".join(parts[i:])
    # 回退：用完整文件名
    return name
