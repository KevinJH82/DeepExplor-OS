"""
Postprocess: Clip raster to KML geometry
使用rasterio将下载的影像裁剪到KML多边形范围，减少存储占用。

支持格式：GeoTIFF（.tif）、ZIP包内的GeoTIFF（Sentinel-2产品）、EMIT L2A .nc
不支持裁剪的格式（非EMIT的NetCDF/HDF）会直接返回原路径。
"""

import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Optional, List

try:
    import rasterio
    from rasterio.mask import mask
    from rasterio.crs import CRS
    from rasterio.warp import reproject, Resampling
    from shapely.geometry import mapping
    from shapely.ops import transform
    import pyproj
    HAS_RASTERIO = True
    _MISSING_DEP_MSG = ""
except ImportError as _e:
    HAS_RASTERIO = False
    # Why: 之前这里笼统说"未安装 rasterio"，实际可能是 shapely / pyproj 缺失，
    # 误导用户去重装 rasterio。保留具体的 ImportError 文本，让 skip 日志和函数
    # 异常都能直接指明缺哪个包。
    _MISSING_DEP_MSG = str(_e)


# 不做裁剪处理的文件扩展名（通常是复杂格式）
# 注意：.nc 由 EMIT 专用逻辑处理，不在此列表中
_SKIP_EXTENSIONS = {".h5", ".hdf", ".hdf5", ".he4", ".he5"}
# ZIP包中会被提取并裁剪的文件扩展名
_RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".jp2"}


def _reproject_geometry(geometry, src_crs):
    """将WGS84几何体重投影到栅格的坐标系。
    src_crs 接受 rasterio CRS 对象或 WKT 字符串。
    always_xy=True 确保始终以 (lon, lat) 顺序处理，
    避免 pyproj >= 2.2 对某些 CRS 自动交换轴序导致坐标错位。
    """
    wgs84 = pyproj.CRS("EPSG:4326")
    if isinstance(src_crs, str):
        dst_proj = pyproj.CRS.from_wkt(src_crs)
    else:
        # 直接从 rasterio CRS 的 EPSG/authority code 构建，避免 WKT 轴序歧义
        try:
            epsg = src_crs.to_epsg()
            dst_proj = pyproj.CRS.from_epsg(epsg) if epsg else pyproj.CRS.from_wkt(src_crs.to_wkt())
        except Exception:
            dst_proj = pyproj.CRS.from_wkt(src_crs.to_wkt())

    if wgs84 == dst_proj:
        return geometry

    project = pyproj.Transformer.from_crs(
        wgs84, dst_proj, always_xy=True
    ).transform
    return transform(project, geometry)


def clip_raster(input_path: Path, geometry, output_path: Optional[Path] = None,
                target_res: Optional[float] = None) -> Path:
    """
    裁剪单个GeoTIFF文件到指定几何范围，可选重采样到目标分辨率。

    Parameters
    ----------
    input_path : 输入栅格路径
    geometry   : Shapely几何体（WGS84坐标）
    output_path: 输出路径，默认在原文件旁添加 _clipped 后缀
    target_res : 目标分辨率（米）。若指定且大于原始分辨率，则裁剪后重采样到该分辨率。
                 None 表示保持原始分辨率。

    Returns
    -------
    裁剪后的文件路径
    """
    if not HAS_RASTERIO:
        raise ImportError(
            f"缺少裁剪依赖（{_MISSING_DEP_MSG}）\n"
            "请运行: pip install rasterio shapely pyproj"
        )

    if output_path is None:
        stem = input_path.stem
        suffix = input_path.suffix
        output_path = input_path.parent / f"{stem}_clipped{suffix}"

    if output_path.exists():
        return output_path

    with rasterio.open(input_path) as src:
        # Sentinel-1 GRD 原始产品无 CRS（斜距坐标），需先在 SNAP 中做地理编码
        if src.crs is None:
            raise RuntimeError(
                f"__no_crs__: {input_path.name} 缺少坐标系信息（Sentinel-1 原始产品需先在 SNAP 中做地理编码再裁剪）"
            )
        # 将几何体投影到栅格坐标系（直接传 rasterio CRS 对象，避免 WKT 轴序歧义）
        geom_proj = _reproject_geometry(geometry, src.crs)

        # 拆解为独立多边形列表，确保多地块各自精确 mask（地块间变为 nodata）
        if geom_proj.geom_type in ("MultiPolygon", "GeometryCollection"):
            geom_json = [mapping(g) for g in geom_proj.geoms if g.geom_type == "Polygon"]
        else:
            geom_json = [mapping(geom_proj)]

        # nodata：优先用源文件的 nodata；若未设置则按数据类型选合理默认值，
        # 避免 nodata=None 时 rasterio.mask 对 KML 外区域行为未定义
        import numpy as np
        if src.nodata is not None:
            fill_nodata = src.nodata
        elif np.issubdtype(src.dtypes[0], np.floating):
            fill_nodata = float("nan")
        else:
            fill_nodata = 0

        try:
            out_image, out_transform = mask(src, geom_json, crop=True, nodata=fill_nodata)
        except Exception as e:
            err_msg = str(e)
            if "do not overlap" in err_msg or "outside bounds" in err_msg.lower():
                raise RuntimeError(f"__no_overlap__: {input_path.name} 与KML无交集，跳过")
            raise RuntimeError(f"裁剪失败 {input_path.name}: {e}")

        out_meta = src.meta.copy()
        _predictor = 3 if out_image.dtype.kind == "f" else 2
        out_meta.update({
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
            "nodata": fill_nodata,
            "compress": "deflate",
            "predictor": _predictor,
        })

        # 若指定目标分辨率，且原始分辨率比目标粗（需上采样），则重采样
        if target_res is not None:
            t = out_transform
            native_res_crs = (abs(t.a) + abs(t.e)) / 2.0
            if src.crs.is_geographic:
                native_res_m = native_res_crs * 111320.0
                target_res_crs = target_res / 111320.0
            else:
                native_res_m = native_res_crs
                target_res_crs = target_res

            # 原始分辨率比目标粗（native > target），才做上采样
            if native_res_m > target_res * 1.05:
                new_width = max(1, round(out_image.shape[2] * (native_res_crs / target_res_crs)))
                new_height = max(1, round(out_image.shape[1] * (native_res_crs / target_res_crs)))
                new_transform = rasterio.transform.from_bounds(
                    *(rasterio.transform.array_bounds(
                        out_image.shape[1], out_image.shape[2], out_transform
                    )),
                    new_width, new_height
                )
                resampled = np.zeros(
                    (out_image.shape[0], new_height, new_width),
                    dtype=out_image.dtype
                )
                for i in range(out_image.shape[0]):
                    reproject(
                        source=out_image[i],
                        destination=resampled[i],
                        src_transform=out_transform,
                        src_crs=src.crs,
                        dst_transform=new_transform,
                        dst_crs=src.crs,
                        resampling=Resampling.bilinear,
                    )
                out_image = resampled
                out_transform = new_transform
                out_meta.update({
                    "height": new_height,
                    "width": new_width,
                    "transform": new_transform,
                })

        with rasterio.open(output_path, "w", **out_meta) as dst:
            dst.write(out_image)

    # 确认输出文件写入成功后再删除原文件
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"裁剪输出文件异常: {output_path.name}")
    input_path.unlink()

    return output_path


def _extract_and_clip_zip(zip_path: Path, geometry, output_dir: Path) -> List[Path]:
    """
    解压归档文件（ZIP 或 tar/tar.gz/tar.bz2）并裁剪其中的栅格文件。
    自动检测文件真实格式，不依赖扩展名。
    """
    extract_dir = output_dir / zip_path.stem
    extract_dir.mkdir(parents=True, exist_ok=True)

    print(f"      解压: {zip_path.name}")
    if tarfile.is_tarfile(zip_path):
        with tarfile.open(zip_path, 'r:*') as tf:
            tf.extractall(extract_dir)
    elif zipfile.is_zipfile(zip_path):
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
    else:
        # 检查是否是截断的 ZIP（文件头是 PK 但不完整）
        is_truncated = False
        try:
            with open(zip_path, 'rb') as f:
                is_truncated = f.read(2) == b'PK'
        except Exception:
            pass
        if is_truncated:
            size_kb = zip_path.stat().st_size // 1024
            print(f"      [警告] {zip_path.name} 是截断的 ZIP 文件（{size_kb} KB），删除后需重新下载")
            zip_path.unlink(missing_ok=True)
        else:
            print(f"      [警告] {zip_path.name} 既非 ZIP 也非 tar 格式，跳过")
        return []

    # 找出所有栅格文件
    raster_files = []
    for ext in _RASTER_EXTENSIONS:
        raster_files.extend(extract_dir.rglob(f"*{ext}"))

    clipped = []
    for rf in raster_files:
        try:
            c = clip_raster(rf, geometry)
            clipped.append(c)
            print(f"      裁剪完成: {c.name}")
        except RuntimeError as e:
            if "__no_overlap__" in str(e):
                print(f"      [跳过] {rf.name} 不覆盖KML区域")
                rf.unlink(missing_ok=True)
            elif "__no_crs__" in str(e):
                print(f"      [跳过裁剪] {rf.name} 无坐标系，保留原文件")
                clipped.append(rf)
            else:
                print(f"      [警告] 裁剪失败 {rf.name}: {e}")
                clipped.append(rf)

    # 删除原始ZIP
    zip_path.unlink()
    return clipped


def clip_to_geometry(file_path: Path, geometry) -> Path:
    """
    主入口：对下载的文件进行裁剪处理。
    自动识别文件类型并选择处理方式。

    Returns
    -------
    裁剪后文件路径（或原路径，若跳过处理）
    """
    if not HAS_RASTERIO:
        print(f"  [跳过裁剪] 缺少裁剪依赖（{_MISSING_DEP_MSG}），保留原始文件")
        return file_path

    suffix = file_path.suffix.lower()

    # EMIT L2A NetCDF — 使用 xarray 做空间子集裁剪
    if suffix == ".nc":
        try:
            from postprocess.emit_clip import clip_emit_nc
            bbox = geometry.bounds  # (min_lon, min_lat, max_lon, max_lat)
            return clip_emit_nc(file_path, bbox)
        except ImportError as e:
            print(f"    [跳过裁剪] {file_path.name}（{e}，请运行: pip install xarray netCDF4）")
            return file_path
        except ValueError as e:
            # bbox 无交集，保留原文件
            print(f"    [跳过裁剪] {e}")
            return file_path
        except RuntimeError as e:
            print(f"    [警告] EMIT裁剪失败 {file_path.name}: {e}")
            return file_path

    # 跳过不支持裁剪的格式（HDF 系列）
    if suffix in _SKIP_EXTENSIONS:
        print(f"    [跳过裁剪] {file_path.name}（格式{suffix}不支持自动裁剪，请在GIS软件中手动处理）")
        return file_path

    # ZIP包：解压后裁剪
    if suffix == ".zip":
        results = _extract_and_clip_zip(file_path, geometry, file_path.parent)
        return results[0] if results else file_path

    # tar.gz / tgz / tar 包：解压后裁剪（EnMAP 等）
    if suffix in (".gz", ".tgz", ".bz2") or file_path.name.endswith(".tar.gz"):
        results = _extract_and_clip_zip(file_path, geometry, file_path.parent)
        return results[0] if results else file_path

    # 直接裁剪GeoTIFF
    if suffix in _RASTER_EXTENSIONS:
        try:
            return clip_raster(file_path, geometry)
        except RuntimeError as e:
            if "__no_overlap__" in str(e):
                print(f"    [跳过] {file_path.name} 不覆盖KML区域，已删除")
                file_path.unlink(missing_ok=True)
                return file_path
            elif "__no_crs__" in str(e):
                print(f"    [跳过裁剪] {file_path.name} 无坐标系，保留原文件")
                return file_path
            raise

    print(f"    [跳过裁剪] 未知格式: {file_path.name}")
    return file_path
