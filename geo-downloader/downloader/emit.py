"""
EMIT Hyperspectral Downloader — NASA LP DAAC
使用 earthaccess 官方库搜索和下载。

注册（免费，NASA Earthdata账号）：
  https://urs.earthdata.nasa.gov/

安装：pip install earthaccess

产品：EMIT L2A Reflectance（地表反射率）
传感器：Earth Surface Mineral Dust Source Investigation（ISS搭载）
波段：285波段，380-2500nm，分辨率60m
发射时间：2022年
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

from .base import BaseDownloader


# EMIT Level-2A反射率产品的短名称
_EMIT_SHORTNAME = "EMITL2ARFL"
_EMIT_VERSION = "001"


class EMITDownloader(BaseDownloader):

    PLATFORM_NAME = "emit"
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
            raise ImportError(
                "缺少依赖: earthaccess\n请运行: pip install earthaccess"
            )

    def _authenticate(self):
        """使用earthaccess进行NASA Earthdata认证（通过credentials.yaml）"""
        if not self._auth_done:
            import os
            os.environ["EARTHDATA_USERNAME"] = self.credentials["username"]
            os.environ["EARTHDATA_PASSWORD"] = self.credentials["password"]
            auth = earthaccess.login(strategy="environment")
            self._auth_done = True

    @staticmethod
    def _is_valid_nc(path: Path) -> bool:
        """用 h5py 检查 NetCDF/HDF5 文件是否完整（未截断）"""
        try:
            import h5py
            with h5py.File(str(path), "r") as f:
                _ = list(f.keys())
            return True
        except Exception:
            return False

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,  # 高光谱不按云量过滤（EMIT本身会筛选质量）
        **kwargs,
    ) -> List[Any]:
        """
        通过earthaccess搜索EMIT L2A反射率产品。
        """
        self._check_deps()
        self._authenticate()

        min_lon, min_lat, max_lon, max_lat = bbox

        results = earthaccess.search_data(
            short_name=_EMIT_SHORTNAME,
            version=_EMIT_VERSION,
            bounding_box=(min_lon, min_lat, max_lon, max_lat),
            temporal=(start_date, end_date),
            count=100,
        )

        # 附加 _footprint（从 earthaccess DataGranule UMM 空间信息）供覆盖选景使用
        try:
            from shapely.geometry import Polygon as _Polygon, box as _box
            for r in results:
                try:
                    umm = r.get("umm", {}) if hasattr(r, "get") else {}
                    spatial = umm.get("SpatialExtent", {}).get("HorizontalSpatialDomain", {})
                    geom = spatial.get("Geometry", {})
                    polys = geom.get("GPolygons", [])
                    if polys:
                        boundary = polys[0].get("Boundary", {}).get("Points", [])
                        if boundary:
                            pts = [(p["Longitude"], p["Latitude"]) for p in boundary]
                            r._footprint = _Polygon(pts)
                    if not hasattr(r, "_footprint"):
                        bbox = geom.get("BoundingRectangles", [])
                        if bbox:
                            b = bbox[0]
                            r._footprint = _box(
                                b["WestBoundingCoordinate"], b["SouthBoundingCoordinate"],
                                b["EastBoundingCoordinate"], b["NorthBoundingCoordinate"]
                            )
                    # 采集日期（供时序选景使用，取不到则不挂）
                    _d = (umm.get("TemporalExtent", {})
                             .get("RangeDateTime", {})
                             .get("BeginningDateTime", "") or "")[:10]
                    if _d:
                        r._acq_date = _d
                except Exception:
                    pass
        except ImportError:
            pass

        print(f"    找到 {len(results)} 景 EMIT L2A 高光谱")
        for r in results[:5]:
            # earthaccess返回的DataGranule对象
            info = r.get("umm", {}) if hasattr(r, "get") else {}
            gran_id = str(r)[:60] if not info else info.get("GranuleUR", str(r)[:60])
            print(f"      {gran_id}")
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
        """
        使用earthaccess下载EMIT产品（NetCDF格式）。
        """
        self._check_deps()
        self._authenticate()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 EMIT...")

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

        downloaded = [Path(f) for f in files if Path(f).exists()]

        # 验证 NetCDF/HDF5 完整性，删除截断文件并重试
        valid = []
        truncated = []
        for f in downloaded:
            if f.suffix.lower() == ".nc" and not self._is_valid_nc(f):
                print(f"    [截断] {f.name} 文件不完整，删除后重试")
                f.unlink(missing_ok=True)
                truncated.append(f)
            else:
                valid.append(f)

        if truncated:
            # 找到截断文件对应的搜索结果，重试一次
            truncated_names = {f.name for f in truncated}
            retry_items = [
                item for item in to_download
                if any(tn in str(item) for tn in
                       {n.replace(".nc", "") for n in truncated_names})
            ]
            if retry_items:
                print(f"    [重试] 重新下载 {len(retry_items)} 个截断文件...")
                try:
                    retry_files = earthaccess.download(
                        retry_items, local_path=str(save_dir),
                    )
                    for rf in retry_files:
                        rp = Path(rf)
                        if rp.exists() and self._is_valid_nc(rp):
                            valid.append(rp)
                            print(f"    [重试成功] {rp.name}")
                        elif rp.exists():
                            print(f"    [重试仍截断] {rp.name}，跳过")
                            rp.unlink(missing_ok=True)
                except Exception as e:
                    print(f"    [重试失败] {e}")

        for f in valid:
            print(f"    [完成] {f.name}")

        return valid
