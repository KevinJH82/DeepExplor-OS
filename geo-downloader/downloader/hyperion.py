"""
Hyperion EO-1 Hyperspectral Downloader — NASA LP DAAC
使用 CMR API 搜索（绕过 earthaccess 已知 IndexError bug），earthaccess session 下载。

传感器：Hyperion（搭载于 NASA EO-1 卫星）
        242个波段，400-2500nm，空间分辨率 30m，幅宽 7.5km
        EO-1 卫星服务期：2000-2017年（历史存档数据）
覆盖范围：全球（按需成像模式，非系统性全球覆盖）
数据产品：
  EO1_HYP_L1R   — 辐射校正产品（原始辐亮度，未正射校正）
  EO1_HYP_L1GST — 系统几何校正产品（推荐用于地理分析）
  EO1_HYP_L1T   — 精确几何校正产品（最高精度，较少）
格式：HDF4（.hdf）
注意：EO-1已于2017年退役，所有数据均为历史档案。

地质应用价值：
  Hyperion 的 SWIR 波段（1000-2500nm）是识别蚀变矿物的核心数据：
  · 1300-1500nm → OH基矿物（绿泥石、蛇纹石）
  · 2000-2250nm → 碳酸盐矿物（方解石、白云石）
  · 2100-2400nm → 黏土矿物（高岭石、伊利石、明矾石）

注意：earthaccess 对部分历史产品存在已知 IndexError bug，
      本下载器改用直接调 NASA CMR API 搜索，earthaccess session 下载。

注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
安装：pip install earthaccess requests
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


_CMR_GRANULE_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"

# Hyperion 产品短名称（LP DAAC 托管）
_HYPERION_PRODUCTS = {
    "EO1_HYP_L1GST": {"version": "001", "desc": "Hyperion L1 系统几何校正（推荐）"},
    "EO1_HYP_L1R":   {"version": "001", "desc": "Hyperion L1 辐射校正"},
    "EO1_HYP_L1T":   {"version": "001", "desc": "Hyperion L1 精确地形校正"},
}

_DEFAULT_PRODUCT = "EO1_HYP_L1GST"


class HyperionDownloader(BaseDownloader):

    PLATFORM_NAME = "hyperion"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._session: Optional[requests.Session] = None

    def _check_deps(self):
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests\n请运行: pip install requests")
        if not HAS_EARTHACCESS:
            raise ImportError("缺少依赖: earthaccess\n请运行: pip install earthaccess")

    def _get_session(self) -> requests.Session:
        """获取带 NASA Earthdata 认证的 requests Session"""
        if self._session is None:
            import os
            os.environ["EARTHDATA_USERNAME"] = self.credentials["username"]
            os.environ["EARTHDATA_PASSWORD"] = self.credentials["password"]
            earthaccess.login(strategy="environment")
            self._session = earthaccess.get_requests_https_session()
        return self._session

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        product: str = _DEFAULT_PRODUCT,
        **kwargs,
    ) -> List[Dict]:
        """
        通过 CMR API 搜索 Hyperion EO-1 高光谱存档数据。

        Parameters
        ----------
        product : 产品代码，可选 EO1_HYP_L1GST / EO1_HYP_L1R / EO1_HYP_L1T
                  注意：EO-1 数据覆盖 2000-2017年，超出此范围无结果

        Tips
        ----
        · Hyperion 是按需成像，非系统扫描，数据密度远低于 Sentinel/Landsat
        · 若搜索结果为0，可尝试放宽时间范围至 2001-01-01 ~ 2017-03-30
        · 覆盖取决于历史任务规划，部分区域可能无数据
        """
        self._check_deps()

        if product not in _HYPERION_PRODUCTS:
            raise ValueError(
                f"不支持的产品: {product}\n可选: {list(_HYPERION_PRODUCTS.keys())}"
            )

        info = _HYPERION_PRODUCTS[product]
        min_lon, min_lat, max_lon, max_lat = bbox

        # EO-1 退役于2017年，自动将结束日期限制在此之前
        _end = min(end_date, "2017-03-30")
        if _end < start_date:
            print(f"    [提示] Hyperion EO-1 已于2017年3月退役，"
                  f"指定时间范围 {start_date}~{end_date} 无有效数据")
            return []

        # 直接调 CMR API（绕过 earthaccess IndexError bug）
        # 注意：Hyperion 数据原托管于 USGS_LTA，该节点已于 2023 年关闭。
        # 数据已迁移至 USGS EarthExplorer（https://earthexplorer.usgs.gov/）。
        # CMR 中的 granule 记录已清空，无法通过 API 自动搜索和下载。
        # 以下尝试搜索，预期返回 0 景，并给出手动下载指引。
        all_results = []
        page = 1
        while True:
            resp = requests.get(
                _CMR_GRANULE_URL,
                params={
                    "short_name": product,
                    "version":    info["version"],
                    "bounding_box": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                    "temporal":   f"{start_date}T00:00:00Z,{_end}T23:59:59Z",
                    "page_size":  200,
                    "page_num":   page,
                },
                timeout=30,
            )
            resp.raise_for_status()
            entries = resp.json().get("feed", {}).get("entry", [])
            if not entries:
                break
            all_results.extend(entries)
            if len(entries) < 200:
                break
            page += 1

        print(f"    找到 {len(all_results)} 景 Hyperion {product}（{info['desc']}）")
        if not all_results:
            print(f"    [说明] Hyperion EO-1 数据已于 2023 年迁移至 USGS EarthExplorer，")
            print(f"           CMR/earthaccess 无法自动搜索，需手动下载：")
            print(f"           1. 访问 https://earthexplorer.usgs.gov/")
            print(f"           2. 登录 USGS 账号（username: kevin_jh）")
            print(f"           3. 搜索数据集: EO-1 > EO-1 Hyperion")
            print(f"           4. 下载后放入 downloads/{{area}}/hyperion/ 目录")
            print(f"           5. 打包时会自动归入 Hyperion L1/SPECTRAL_IMAGE.hdf")

        for r in all_results[:5]:
            title = r.get("title", "")
            dt = r.get("time_start", "")[:10]
            print(f"      {dt}  {title[:70]}")
        if len(all_results) > 5:
            print(f"      ... 共 {len(all_results)} 景")

        return all_results

    def _get_download_url(self, granule: Dict) -> Optional[str]:
        """从 CMR granule 条目提取 HDF 下载链接"""
        links = granule.get("links", [])
        # 优先取 data# rel 的 .hdf 文件
        for lk in links:
            rel = lk.get("rel", "")
            href = lk.get("href", "")
            if "fedsearch/1.1/data#" in rel and href.lower().endswith(".hdf"):
                return href
        # 回退：任意 .hdf 链接
        for lk in links:
            href = lk.get("href", "")
            if href.lower().endswith(".hdf"):
                return href
        return None

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """
        下载 Hyperion EO-1 产品（HDF4 格式）。

        注意：Hyperion HDF4 文件内含所有242个波段，单文件约200-400MB。
        后处理时需用 pyhdf 或 GDAL 提取各波段。
        """
        self._check_deps()
        session = self._get_session()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 Hyperion EO-1（HDF4格式）...")
        print(f"    [提示] 每景约 200-400MB，包含242个波段（400-2500nm，30m）")

        downloaded = []
        for granule in to_download:
            title = granule.get("title", "unknown")
            url = self._get_download_url(granule)
            if not url:
                print(f"      [跳过] 无法获取下载链接: {title[:50]}")
                continue

            filename = url.split("/")[-1].split("?")[0]
            save_path = save_dir / filename
            if save_path.exists():
                print(f"      已存在，跳过: {filename}")
                downloaded.append(save_path)
                continue

            try:
                download_with_resume(session, url, save_path,
                                     desc=filename, timeout=600)
                downloaded.append(save_path)
                size_mb = save_path.stat().st_size / 1024 / 1024
                print(f"    [完成] {filename}  ({size_mb:.0f} MB)")
            except Exception as e:
                print(f"    [错误] {title[:50]}: {e}")

        return downloaded
