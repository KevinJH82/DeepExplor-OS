"""
AVIRIS-NG Hyperspectral Downloader — NASA JPL / ORNL DAAC
使用 earthaccess 官方库搜索和下载。

传感器：AVIRIS-NG（Airborne Visible/Infrared Imaging Spectrometer - Next Generation）
        NASA JPL 研制的机载高光谱传感器，搭载于飞机（ER-2、Twin Otter等）
波段：432个波段，380-2510nm，连续覆盖
分辨率：4-8m（典型飞行高度 4km AGL，地面采样距离约 5m）
幅宽：约 600m（典型 4km 高度）
特点：全球飞行任务，覆盖印度（HyspIRI预研）、非洲、南美、北美等地区

数据产品：
  AVNG_L1B  — 辐亮度（radiance），未大气校正
  AVNG_L2   — 地表反射率（surface reflectance），大气校正后（推荐）

数据托管：
  ORNL DAAC（https://daac.ornl.gov/）
  部分任务数据也在 LP DAAC

地质应用价值：
  · 5m 超高空间分辨率 + 432波段，可精细识别：
    - 矿化蚀变带（铁染、泥化、矽卡岩化）
    - 单矿物端元提取（高岭石、蒙脱石、钙铁辉石）
  · 与 EMIT（60m）配合：AVIRIS-NG 精细勘探 → EMIT 区域填图

注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
安装：pip install earthaccess
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

from .base import BaseDownloader


# AVIRIS-NG 产品（ORNL DAAC 托管）
# short_name 以 CMR 中实际注册为准，以下为主要产品
_AVIRIS_PRODUCTS = {
    "AVNG_L2":  {"version": "001", "desc": "AVIRIS-NG L2 地表反射率（大气校正，推荐）"},
    "AVNG_L1B": {"version": "001", "desc": "AVIRIS-NG L1B 辐亮度"},
}

_DEFAULT_PRODUCT = "AVNG_L2"


class AVIRISDownloader(BaseDownloader):

    PLATFORM_NAME = "aviris"
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
        product: str = _DEFAULT_PRODUCT,
        **kwargs,
    ) -> List[Any]:
        """
        搜索 AVIRIS-NG 高光谱数据。

        Parameters
        ----------
        product : 产品代码，可选 AVNG_L2 / AVNG_L1B

        重要说明
        --------
        · AVIRIS-NG 是机载传感器（飞机），覆盖取决于飞行任务，非卫星连续覆盖
        · 主要飞行任务区域：美国本土、印度（HyspIRI预研2014-2016）、
                             非洲东部（2016-2018）、南美（2013-）
        · 若研究区无飞行任务历史，搜索结果可能为0
        · 可先用较大时间范围（2013-2024）探查是否有覆盖
        """
        self._check_deps()
        self._authenticate()

        if product not in _AVIRIS_PRODUCTS:
            raise ValueError(
                f"不支持的产品: {product}\n可选: {list(_AVIRIS_PRODUCTS.keys())}"
            )

        info = _AVIRIS_PRODUCTS[product]
        min_lon, min_lat, max_lon, max_lat = bbox

        # 先尝试精确产品名搜索
        results = self._search_cmr(product, info["version"], bbox, start_date, end_date)

        # 若无结果，尝试模糊搜索（部分任务的 short_name 略有不同）
        if not results:
            results = self._search_cmr_fallback(bbox, start_date, end_date)

        print(f"    找到 {len(results)} 景 AVIRIS-NG（{info['desc']}）")
        if not results:
            print(f"    [提示] 当前区域可能无 AVIRIS-NG 飞行任务覆盖")
            print(f"    [提示] AVIRIS-NG 为机载传感器，覆盖范围取决于飞行计划")
            print(f"    [参考] 可查询飞行记录: https://avirisng.jpl.nasa.gov/dataportal/")

        for r in results[:5]:
            try:
                umm = r["umm"]
                gran_id = umm.get("GranuleUR", "")
                dt = (umm.get("TemporalExtent", {})
                         .get("RangeDateTime", {})
                         .get("BeginningDateTime", "")[:10])
                print(f"      {dt}  {gran_id[:70]}")
            except Exception:
                print(f"      {str(r)[:80]}")
        if len(results) > 5:
            print(f"      ... 共 {len(results)} 景")

        return results

    def _search_cmr(
        self,
        short_name: str,
        version: str,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
    ) -> List[Any]:
        """通过 earthaccess CMR 精确搜索"""
        min_lon, min_lat, max_lon, max_lat = bbox
        try:
            return earthaccess.search_data(
                short_name=short_name,
                version=version,
                bounding_box=(min_lon, min_lat, max_lon, max_lat),
                temporal=(start_date, end_date),
                count=100,
            )
        except Exception:
            return []

    def _search_cmr_fallback(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
    ) -> List[Any]:
        """
        使用关键词回退搜索（覆盖不同命名规则的 AVIRIS-NG 任务数据集）
        各任务的 short_name 可能是 AVNG_L2、AVNG_L2A、ang等
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        fallback_names = ["AVNG_L2A", "ANG_L2", "AVIRISNG_L2"]
        for name in fallback_names:
            try:
                results = earthaccess.search_data(
                    short_name=name,
                    bounding_box=(min_lon, min_lat, max_lon, max_lat),
                    temporal=(start_date, end_date),
                    count=100,
                )
                if results:
                    print(f"    [回退搜索] 使用产品名 {name} 找到 {len(results)} 景")
                    return results
            except Exception:
                continue
        return []

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 3,   # AVIRIS-NG 单景较大，默认少下
        **kwargs,
    ) -> List[Path]:
        """
        下载 AVIRIS-NG 产品。

        注意：AVIRIS-NG 单个飞行线文件约 1-5GB，含432个波段。
        本下载器保留原始格式（ENVI BSQ 或 NetCDF），不做波段拆分。
        """
        self._check_deps()
        self._authenticate()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 AVIRIS-NG...")
        print(f"    [提示] 每景约 1-5GB，包含432个波段（380-2510nm，~5m）")

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
        for f in downloaded:
            size_mb = f.stat().st_size / 1024 / 1024
            print(f"    [完成] {f.name}  ({size_mb:.0f} MB)")

        return downloaded
