"""
OPERA RTC Downloader — ASF DAAC
使用 asf_search 官方库搜索和下载。

产品：OPERA L2 RTC-S1（Sentinel-1 辐射地形校正SAR）
来源：JPL/NASA OPERA 项目，基于 Sentinel-1 GRD 制作
分辨率：30m（地理坐标，已正射校正）
极化：VV、VH 双极化 GeoTIFF
覆盖：全球陆地，2014年至今

相比原始 Sentinel-1 GRD 的优势：
  - 已完成辐射校正（去除地形效应）
  - 已正射校正（直接对齐DEM，可与光学叠合）
  - GeoTIFF格式（含CRS，可直接用rasterio裁剪）
  - 免去复杂的 SNAP/PyGEO 预处理流程

注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
需授权 ASF 应用：https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g
安装：pip install asf_search earthaccess
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any

try:
    import asf_search as asf
    HAS_ASF = True
except ImportError:
    HAS_ASF = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


class OPERADownloader(BaseDownloader):

    PLATFORM_NAME = "opera"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)

    def _check_deps(self):
        if not HAS_ASF:
            raise ImportError("缺少依赖: asf_search\n请运行: pip install asf_search")

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        **kwargs,
    ) -> List[Any]:
        """
        搜索 OPERA L2 RTC-S1 产品。
        每个场景是一个 Sentinel-1 burst（约 20×20km），
        已完成辐射地形校正，输出为 GeoTIFF。
        """
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        aoi_wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        results = asf.search(
            dataset="OPERA-S1",
            processingLevel=["RTC"],
            intersectsWith=aoi_wkt,
            start=f"{start_date}T00:00:00Z",
            end=f"{end_date}T23:59:59Z",
            maxResults=200,
        )

        print(f"    找到 {len(results)} 景 OPERA RTC-S1")
        for r in list(results)[:5]:
            p = r.properties
            print(
                f"      {p.get('startTime','?')[:10]}  "
                f"轨道={p.get('pathNumber','?')}  "
                f"{p.get('sceneName','?')[:55]}"
            )
        if len(results) > 5:
            print(f"      ... 共 {len(results)} 景")

        return list(results)

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """
        下载 OPERA RTC-S1 产品（GeoTIFF，含 VV/VH 极化）。
        输出为标准 GeoTIFF，含坐标系，可直接用 rasterio 裁剪。
        """
        self._check_deps()

        username = self.credentials["username"]
        password = self.credentials["password"]

        # 使用 earthaccess JWT token 认证
        try:
            import os
            import earthaccess
            os.environ["EARTHDATA_USERNAME"] = username
            os.environ["EARTHDATA_PASSWORD"] = password
            earthaccess.login(strategy="environment")
            token_dict = earthaccess.get_edl_token()
            token = token_dict.get("access_token", "")
            session = asf.ASFSession().auth_with_token(token)
        except Exception:
            session = asf.ASFSession().auth_with_creds(username, password)

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 OPERA RTC-S1...")

        import time, random
        max_retries = 3
        for attempt in range(max_retries):
            try:
                asf.ASFSearchResults(to_download).download(
                    path=str(save_dir),
                    session=session,
                    processes=1,
                )
                break
            except Exception as e:
                err_str = str(e).lower()
                is_ssl = "ssl" in err_str or "eof occurred" in err_str
                is_net = is_ssl or "connectionerror" in err_str or "max retries" in err_str or "timeout" in err_str
                if is_net and attempt < max_retries - 1:
                    wait = 2 ** attempt * 10 + random.uniform(0, 5)
                    print(f"    [重试 {attempt + 1}/{max_retries}] SSL/网络错误，{wait:.0f}s 后重连... ({e})")
                    time.sleep(wait)
                    # 重建 session 以刷新 SSL 连接
                    try:
                        import earthaccess
                        token_dict = earthaccess.get_edl_token()
                        token = token_dict.get("access_token", "")
                        session = asf.ASFSession().auth_with_token(token)
                    except Exception:
                        session = asf.ASFSession().auth_with_creds(username, password)
                else:
                    print(f"    [错误] 下载过程中出错: {e}")
                    break

        # OPERA RTC 的 GeoTIFF 在 additionalUrls 中（VV/VH/mask），
        # asf_search 默认下载的是 .h5 容器，改为直接下载 GeoTIFF
        downloaded = []
        for item in to_download:
            scene_id = item.properties.get("sceneName", "")
            add_urls = item.properties.get("additionalUrls", [])
            tif_urls = [u for u in add_urls if u.endswith(".tif")]

            if tif_urls:
                # 直接用 requests 下载各波段 GeoTIFF
                import requests
                for url in tif_urls:
                    fname = url.split("/")[-1]
                    fpath = save_dir / fname
                    if fpath.exists():
                        print(f"      已存在: {fname}")
                        downloaded.append(fpath)
                        continue
                    try:
                        download_with_resume(session, url, fpath, desc=fname, timeout=300)
                        downloaded.append(fpath)
                        print(f"    [完成] {fname}")
                    except Exception as e:
                        print(f"    [错误] {fname}: {e}")
            else:
                # 回退：使用 asf_search 默认下载
                fname = item.properties.get("fileName", "")
                fpath = save_dir / fname
                if fpath.exists():
                    downloaded.append(fpath)
                    print(f"    [完成] {fname}")
                else:
                    matches = list(save_dir.glob(f"{scene_id}*"))
                    if matches:
                        downloaded.extend(matches)
                        print(f"    [完成] {matches[0].name}")

        return downloaded
