"""
GEDI Downloader — NASA LP DAAC
使用 earthaccess 官方库搜索和下载。

产品：GEDI Level 2A（地形高程+植被高度，全球激光测高）
传感器：GEDI（Global Ecosystem Dynamics Investigation）
        搭载于国际空间站（ISS），激光雷达
覆盖范围：51.6°S ~ 51.6°N（ISS轨道倾角限制）
分辨率：25m footprint，约60m轨迹间距
特点：穿透植被获取真实地面高程，比DEM/SRTM精度高10倍

注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
安装：pip install earthaccess h5py

输出：HDF5格式（.h5），包含激光点坐标+高程+植被高度
"""

import os
from pathlib import Path
from typing import List, Tuple, Dict, Any

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

from .base import BaseDownloader


_GEDI_SHORTNAME = "GEDI02_A"
_GEDI_VERSION = "002"


class GEDIDownloader(BaseDownloader):

    PLATFORM_NAME = "gedi"
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
        **kwargs,
    ) -> List[Any]:
        """
        搜索覆盖 bbox 区域的 GEDI L2A 数据。

        注意：GEDI 轨道稀疏，每条轨迹约 25m footprint，
        实际覆盖取决于轨道经过时间，非连续面覆盖。
        """
        self._check_deps()
        self._authenticate()

        min_lon, min_lat, max_lon, max_lat = bbox

        results = earthaccess.search_data(
            short_name=_GEDI_SHORTNAME,
            version=_GEDI_VERSION,
            bounding_box=(min_lon, min_lat, max_lon, max_lat),
            temporal=(start_date, end_date),
            count=100,
        )

        print(f"    找到 {len(results)} 个 GEDI L2A granule")
        for r in results[:5]:
            try:
                gran_id = r["umm"].get("GranuleUR", "")
                dt = (r["umm"].get("TemporalExtent", {})
                              .get("RangeDateTime", {})
                              .get("BeginningDateTime", "")[:10])
                print(f"      {dt}  {gran_id[:60]}")
            except Exception:
                print(f"      {str(r)[:80]}")
        if len(results) > 5:
            print(f"      ... 共 {len(results)} 个")

        return results

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """
        下载 GEDI L2A granule（HDF5格式，每个约500MB-2GB）。
        每个 granule 是一条完整轨道，覆盖范围远超研究区，
        后处理时需按坐标筛选落入 bbox 的激光点。
        """
        self._check_deps()
        self._authenticate()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 个 GEDI granule...")
        print(f"    [提示] 每个 granule 是完整轨道（~500MB-2GB），下载后需按坐标筛选研究区激光点")

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
            print(f"    [完成] {f.name}  ({f.stat().st_size/1024/1024:.0f} MB)")

        return downloaded
