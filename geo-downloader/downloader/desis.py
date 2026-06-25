"""
DESIS Hyperspectral Downloader — NASA EarthData (LP DAAC)
使用 earthaccess 官方库搜索和下载。

DESIS (DLR Earth Sensing Imaging Spectrometer) 搭载于国际空间站，
2018年6月安装，2021年9月移除并退役。
现有存档数据通过 NASA EarthData 公开。

注册（免费）：
  https://urs.earthdata.nasa.gov/

产品：DESIS L2A Surface Reflectance
波段：235波段，400-1000nm（VNIR），分辨率30m
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

from .base import BaseDownloader


# DESIS Level-2A 地表反射率产品
_DESIS_SHORTNAME = "DESIS-HSI-L2A"
_DESIS_VERSION   = "0112"


class DESISDownloader(BaseDownloader):

    PLATFORM_NAME = "desis"
    REQUIRES_AUTH = True

    def __init__(self, credentials: Dict[str, str], output_dir: str = "./downloads", **kwargs):
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
        self._check_deps()
        self._authenticate()

        min_lon, min_lat, max_lon, max_lat = bbox

        # 先尝试标准短名，失败则宽泛搜索
        try:
            results = earthaccess.search_data(
                short_name=_DESIS_SHORTNAME,
                version=_DESIS_VERSION,
                bounding_box=(min_lon, min_lat, max_lon, max_lat),
                temporal=(start_date, end_date),
                count=kwargs.get("count", 50),
            )
        except Exception:
            try:
                # version 号可能不同，不指定 version 再试
                results = earthaccess.search_data(
                    short_name=_DESIS_SHORTNAME,
                    bounding_box=(min_lon, min_lat, max_lon, max_lat),
                    temporal=(start_date, end_date),
                    count=kwargs.get("count", 50),
                )
            except Exception as e:
                print(f"    [警告] DESIS CMR 搜索失败（earthaccess 内部异常: {e}），跳过此传感器")
                return []

        print(f"    找到 {len(results)} 景 DESIS L2A 高光谱")
        for r in results[:3]:
            print(f"      {str(r)[:80]}")
        if len(results) > 3:
            print(f"      ... 共 {len(results)} 景")

        return results

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        self._check_deps()
        self._authenticate()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 DESIS...")

        for p in save_dir.glob("*.part"):
            p.unlink()

        try:
            files = earthaccess.download(to_download, local_path=str(save_dir))
        except Exception as e:
            print(f"    [错误] DESIS 下载失败: {e}")
            return []

        downloaded = [Path(f) for f in files if Path(f).exists()]
        for f in downloaded:
            print(f"    [完成] {f.name}")

        return downloaded
