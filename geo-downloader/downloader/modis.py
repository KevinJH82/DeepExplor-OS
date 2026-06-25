"""
MODIS Downloader — NASA LP DAAC
使用 earthaccess 官方库搜索和下载。

产品（二选一，通过 product 参数控制）：
  MOD09GA  — Terra 地表反射率（500m，每日）
  MOD13Q1  — Terra NDVI/EVI 植被指数（250m，16天合成）

传感器：MODIS（Terra/Aqua卫星搭载）
注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
安装：pip install earthaccess
"""

import os
from pathlib import Path
from typing import List, Tuple, Dict, Any

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

try:
    from osgeo import gdal
    HAS_GDAL = True
    # 检查是否有 HDF4 驱动
    _drv_names = [gdal.GetDriver(i).ShortName for i in range(gdal.GetDriverCount())]
    HAS_GDAL_HDF4 = "HDF4" in _drv_names or "HDF4Image" in _drv_names
except ImportError:
    HAS_GDAL = False
    HAS_GDAL_HDF4 = False

try:
    from pyhdf.SD import SD, SDC
    HAS_PYHDF = True
except ImportError:
    HAS_PYHDF = False

from .base import BaseDownloader


# MOD09GA 子数据集索引 → 波段名称映射
# HDF4 子数据集格式: HDF4_EOS:EOS_GRID:"file.hdf":MOD_Grid_500m_Surface_Reflectance:sur_refl_b01
_MOD09GA_SUBDATASETS = [
    ("sur_refl_b01", "B01_red_620_670nm"),
    ("sur_refl_b02", "B02_nir_841_876nm"),
    ("sur_refl_b03", "B03_blue_459_479nm"),
    ("sur_refl_b04", "B04_green_545_565nm"),
    ("sur_refl_b05", "B05_swir1_1230_1250nm"),
    ("sur_refl_b06", "B06_swir2_1628_1652nm"),
    ("sur_refl_b07", "B07_swir3_2105_2155nm"),
]

_MOD13Q1_SUBDATASETS = [
    ("250m 16 days NDVI",        "NDVI"),
    ("250m 16 days EVI",         "EVI"),
    ("250m 16 days red reflectance",  "B01_red"),
    ("250m 16 days NIR reflectance",  "B02_nir"),
    ("250m 16 days blue reflectance", "B03_blue"),
    ("250m 16 days MIR reflectance",  "B07_swir"),
]


def _hdf_to_tif(hdf_path: Path, product: str = "MOD09GA") -> List[Path]:
    """
    将 MODIS HDF4 文件转换为多个单波段 GeoTIFF。
    优先使用 GDAL HDF4 驱动；若不可用则用 pyhdf + rasterio 写出。
    """
    if HAS_GDAL and HAS_GDAL_HDF4:
        return _hdf_to_tif_gdal(hdf_path, product)
    elif HAS_PYHDF:
        return _hdf_to_tif_pyhdf(hdf_path, product)
    else:
        print(f"    [错误] 无法转换HDF4: 需要 GDAL(HDF4驱动) 或 pyhdf")
        print(f"           请运行: pip install pyhdf")
        return []


def _hdf_to_tif_gdal(hdf_path: Path, product: str = "MOD09GA") -> List[Path]:
    """使用 GDAL HDF4 子数据集驱动转换。"""
    ds = gdal.Open(str(hdf_path))
    if ds is None:
        return []

    subdatasets = ds.GetSubDatasets()
    ds = None

    if product.startswith("MOD09GA") or product.startswith("MYD09GA"):
        band_map = _MOD09GA_SUBDATASETS
    else:
        band_map = _MOD13Q1_SUBDATASETS

    out_dir = hdf_path.parent
    stem = hdf_path.stem
    results = []

    for sds_path, sds_name in subdatasets:
        matched_label = None
        for key, label in band_map:
            if key in sds_name:
                matched_label = label
                break
        if matched_label is None:
            continue

        out_path = out_dir / f"{stem}_{matched_label}.tif"
        if out_path.exists():
            results.append(out_path)
            continue

        src_ds = gdal.Open(sds_path)
        if src_ds is None:
            continue

        driver = gdal.GetDriverByName("GTiff")
        dst_ds = driver.CreateCopy(
            str(out_path), src_ds,
            options=["COMPRESS=DEFLATE", "PREDICTOR=2", "TILED=YES"],
        )
        dst_ds = None
        src_ds = None

        if out_path.exists():
            results.append(out_path)
            print(f"      [转换] {out_path.name}")

    return results


def _hdf_to_tif_pyhdf(hdf_path: Path, product: str = "MOD09GA") -> List[Path]:
    """使用 pyhdf 读取 HDF4 并用 rasterio 写出 GeoTIFF（GDAL无HDF4驱动时的后备）。"""
    import numpy as np
    try:
        import rasterio
        from rasterio.transform import from_bounds
        from rasterio.crs import CRS
    except ImportError:
        print(f"    [错误] pyhdf后备方案还需要rasterio: pip install rasterio")
        return []

    if product.startswith("MOD09GA") or product.startswith("MYD09GA"):
        band_map = _MOD09GA_SUBDATASETS
    else:
        band_map = _MOD13Q1_SUBDATASETS

    out_dir = hdf_path.parent
    stem = hdf_path.stem
    results = []

    try:
        hdf = SD(str(hdf_path), SDC.READ)
    except Exception as e:
        print(f"    [错误] pyhdf无法打开: {hdf_path.name}: {e}")
        return []

    datasets = hdf.datasets()

    for key, label in band_map:
        # 在 HDF 数据集中查找匹配的变量
        matched_ds_name = None
        for ds_name in datasets:
            if key in ds_name:
                matched_ds_name = ds_name
                break
        if matched_ds_name is None:
            continue

        out_path = out_dir / f"{stem}_{label}.tif"
        if out_path.exists():
            results.append(out_path)
            continue

        try:
            sds = hdf.select(matched_ds_name)
            data = sds.get()

            # 读取地理信息属性
            attrs = sds.attributes()

            # MODIS 正弦投影参数
            sinusoidal_wkt = (
                'PROJCS["MODIS Sinusoidal",'
                'GEOGCS["GCS_WGS_1984",'
                'DATUM["D_WGS_1984",'
                'SPHEROID["WGS_1984",6371007.181,0]],'
                'PRIMEM["Greenwich",0],'
                'UNIT["Degree",0.0174532925199433]],'
                'PROJECTION["Sinusoidal"],'
                'PARAMETER["central_meridian",0],'
                'PARAMETER["false_easting",0],'
                'PARAMETER["false_northing",0],'
                'UNIT["Meter",1]]'
            )

            # 从 StructMetadata.0 提取边界坐标
            try:
                struct_meta = hdf.attributes().get("StructMetadata.0", "")
                ul_x = _extract_meta_value(struct_meta, "UpperLeftPointMtrs")
                lr_x = _extract_meta_value(struct_meta, "LowerRightMtrs")
                if ul_x and lr_x:
                    west, north = ul_x
                    east, south = lr_x
                else:
                    sds.endaccess()
                    continue
            except Exception:
                sds.endaccess()
                continue

            height, width = data.shape[:2] if data.ndim >= 2 else (data.shape[0], 1)
            transform = from_bounds(west, south, east, north, width, height)

            # 确定数据类型
            if data.dtype == np.int16:
                dtype = rasterio.int16
                nodata = -28672
            elif data.dtype == np.uint16:
                dtype = rasterio.uint16
                nodata = 65535
            elif data.dtype == np.float32:
                dtype = rasterio.float32
                nodata = -9999.0
            else:
                dtype = rasterio.int16
                nodata = -28672

            with rasterio.open(
                str(out_path), "w", driver="GTiff",
                height=height, width=width, count=1,
                dtype=dtype, crs=CRS.from_wkt(sinusoidal_wkt),
                transform=transform,
                compress="deflate", predictor=2, tiled=True,
                nodata=nodata,
            ) as dst:
                dst.write(data if data.ndim == 2 else data[:, :, 0], 1)

            sds.endaccess()

            if out_path.exists():
                results.append(out_path)
                print(f"      [转换/pyhdf] {out_path.name}")

        except Exception as e:
            print(f"      [警告] 转换失败 {matched_ds_name}: {e}")
            continue

    hdf.end()
    return results


def _extract_meta_value(struct_meta: str, key: str):
    """从 StructMetadata.0 中提取坐标对，如 UpperLeftPointMtrs=(x,y)"""
    import re
    pattern = rf'{key}=\(([^)]+)\)'
    m = re.search(pattern, struct_meta)
    if m:
        parts = m.group(1).split(",")
        return float(parts[0]), float(parts[1])
    return None


_MODIS_PRODUCTS = {
    "MOD09GA": {"version": "061", "desc": "Terra 地表反射率 500m 每日"},
    "MOD13Q1": {"version": "061", "desc": "Terra NDVI/EVI 250m 16天合成"},
    "MYD09GA": {"version": "061", "desc": "Aqua 地表反射率 500m 每日"},
    "MYD13Q1": {"version": "061", "desc": "Aqua NDVI/EVI 250m 16天合成"},
}


class MODISDownloader(BaseDownloader):

    PLATFORM_NAME = "modis"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._auth_done = False

    def _check_deps(self):
        if not HAS_EARTHACCESS:
            raise ImportError("缺少依赖: earthaccess\n请运行: pip install earthaccess")

    def _authenticate(self):
        if not self._auth_done:
            import os
            os.environ["EARTHDATA_USERNAME"] = self.credentials["username"]
            os.environ["EARTHDATA_PASSWORD"] = self.credentials["password"]
            earthaccess.login(strategy="environment")
            self._auth_done = True

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        product: str = "MOD09GA",
        **kwargs,
    ) -> List[Any]:
        """
        搜索 MODIS 产品。

        Parameters
        ----------
        product : 产品代码，可选 MOD09GA / MOD13Q1 / MYD09GA / MYD13Q1
        """
        self._check_deps()
        self._authenticate()

        if product not in _MODIS_PRODUCTS:
            raise ValueError(
                f"不支持的MODIS产品: {product}\n"
                f"可选: {list(_MODIS_PRODUCTS.keys())}"
            )

        info = _MODIS_PRODUCTS[product]
        min_lon, min_lat, max_lon, max_lat = bbox

        results = earthaccess.search_data(
            short_name=product,
            version=info["version"],
            bounding_box=(min_lon, min_lat, max_lon, max_lat),
            temporal=(start_date, end_date),
            count=100,
        )

        print(f"    找到 {len(results)} 景 {product}（{info['desc']}）")
        for r in results[:5]:
            try:
                umm = r["umm"]
                gran_id = umm.get("GranuleUR", "")
                dt = (umm.get("TemporalExtent", {})
                         .get("RangeDateTime", {})
                         .get("BeginningDateTime", "")[:10])
                print(f"      {dt}  {gran_id[:60]}")
            except Exception:
                print(f"      {str(r)[:80]}")
        if len(results) > 5:
            print(f"      ... 共 {len(results)} 景")

        return results

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """使用 earthaccess 下载 MODIS 产品（HDF4格式）。"""
        self._check_deps()
        self._authenticate()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 MODIS...")

        # earthaccess 托管下载，无法注入 Range 头；清除残片避免误用
        for p in save_dir.glob("*.part"):
            p.unlink()
            print(f"    [清除残片] {p.name}")

        try:
            files = earthaccess.download(
                to_download,
                local_path=str(save_dir),
            )
        except Exception as e:
            print(f"    [错误] 下载失败: {e}")
            return []

        downloaded = []
        for f in files:
            if isinstance(f, (str, os.PathLike)):
                p = Path(f)
                if p.exists():
                    downloaded.append(p)
            else:
                print(f"    [单条失败] {type(f).__name__}: {f}")
        for f in downloaded:
            print(f"    [完成] {f.name}")

        return downloaded

    def run(
        self,
        bbox,
        geometry,
        area_name: str,
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        max_items: int = 5,
        clip: bool = True,
        **kwargs,
    ):
        """下载 → HDF转GeoTIFF → 裁剪"""
        save_dir = self.get_save_dir(area_name)

        print(f"\n[{self.PLATFORM_NAME}] 搜索影像...")
        print(f"  区域: {area_name} | 范围: {bbox}")
        print(f"  时间: {start_date} ~ {end_date}")

        results = self.search(bbox, start_date, end_date, cloud_cover, **kwargs)
        if not results:
            print(f"  [!] 未找到符合条件的影像")
            return []

        print(f"  找到 {len(results)} 景，开始下载（最多 {max_items} 景）...")
        hdf_files = self.download(results, save_dir, max_items, **kwargs)

        # 识别本次下载的 product 类型（从搜索结果 short_name 推断）
        try:
            product = results[0]["umm"]["CollectionReference"]["ShortName"]
        except Exception:
            product = "MOD09GA"

        # HDF → GeoTIFF 转换
        tif_files = []
        if HAS_GDAL_HDF4 or HAS_PYHDF:
            print(f"  [MODIS] 转换 HDF → GeoTIFF{'（pyhdf后备）' if not HAS_GDAL_HDF4 else ''}...")
            for hdf in hdf_files:
                if hdf.suffix.lower() in {".hdf", ".hdf5", ".he4"}:
                    converted = _hdf_to_tif(hdf, product=product)
                    if converted:
                        tif_files.extend(converted)
                    else:
                        print(f"    [警告] HDF转换失败，跳过: {hdf.name}")
                else:
                    tif_files.append(hdf)
        else:
            print("  [警告] 无法转换HDF4: 需安装 pyhdf (pip install pyhdf) 或含HDF4驱动的GDAL")
            tif_files = hdf_files

        # 裁剪
        if clip and tif_files and geometry is not None:
            from postprocess.clip import clip_to_geometry
            print(f"  裁剪影像到KML范围...")
            clipped = []
            for f in tif_files:
                try:
                    out = clip_to_geometry(f, geometry)
                    clipped.append(out)
                except Exception as e:
                    print(f"  [警告] 裁剪失败 {f.name}: {e}")
                    clipped.append(f)
            return clipped

        return tif_files
