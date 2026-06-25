"""
ALOS-2 PALSAR-2 Downloader — ASF DAAC
使用 asf_search 官方库搜索和下载。

数据来源：Alaska Satellite Facility (ASF DAAC)
传感器：PALSAR-2（相控阵L波段合成孔径雷达，第二代）
        搭载于 ALOS-2 卫星（日本 JAXA，2014年至今在轨）
波段：L波段（23.6cm），穿透植被和浅层土壤能力强，适合地质填图
分辨率：
  ScanSAR：100m（宽覆盖，350km幅宽）
  Wide    ：10m（全极化，50km幅宽）
  Fine    ：3m（单/双极化，50km幅宽）
  Ultra-fine：1m（单极化，50km幅宽）
极化：HH、HV、VV、VH（视模式而定）

相比 ALOS-1 PALSAR 的改进：
- 分辨率提升约3倍（3m vs 10m）
- 更宽覆盖模式（ScanSAR 350km）
- 支持右侧视和左侧视
- 2014年至今持续积累数据

产品类型（processingLevel）：
  GRD    — Ground Range Detected（地距强度图，推荐，易于使用）
  SLC    — Single Look Complex（单视复数，含相位信息，用于InSAR）
  RTC-GAMMA  — 辐射地形校正（地表形变、植被穿透应用）

地质应用价值：
  · L波段穿透干植被和浅层松散覆盖，揭示地表岩性/构造
  · HH/HV双极化比值对岩性分类敏感
  · SLC数据支持 InSAR 地表形变监测（矿区地面沉降）
  · 与 Sentinel-1（C波段）互补，增强穿透深度

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

from .base import BaseDownloader


class ALOS2Downloader(BaseDownloader):

    PLATFORM_NAME = "alos2"
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
        cloud_cover: int = 100,   # SAR 不受云量影响
        processing_level: str = "GRD",
        **kwargs,
    ) -> List[Any]:
        """
        通过 asf_search 搜索 ALOS-2 PALSAR-2 产品。

        Parameters
        ----------
        processing_level : 'GRD'（地距强度图，推荐）、'SLC'（单视复数）
        """
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        aoi_wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        results = asf.search(
            platform=[asf.PLATFORM.ALOS2],
            processingLevel=[processing_level],
            intersectsWith=aoi_wkt,
            start=f"{start_date}T00:00:00Z",
            end=f"{end_date}T23:59:59Z",
            maxResults=100,
        )

        print(f"    找到 {len(results)} 景 ALOS-2 PALSAR-2 {processing_level}")
        for r in list(results)[:5]:
            p = r.properties
            print(
                f"      {p.get('startTime','?')[:10]}  "
                f"轨道={p.get('pathNumber','?')}  "
                f"极化={p.get('polarization','?')}  "
                f"模式={p.get('beamModeType','?')}  "
                f"{p.get('sceneName','?')}"
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
        """使用 asf_search 下载 ALOS-2 PALSAR-2 产品（ZIP格式）。"""
        self._check_deps()

        username = self.credentials["username"]
        password = self.credentials["password"]

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
        print(f"    正在下载 {len(to_download)} 景 ALOS-2 PALSAR-2...")

        # 清除上次中断留下的残片（asf_search 托管下载，无法续传）
        for item in to_download:
            fname = item.properties.get("fileName", "")
            part = save_dir / (fname + ".part")
            if part.exists():
                part.unlink()
                print(f"    [清除残片] {part.name}")

        try:
            asf.ASFSearchResults(to_download).download(
                path=str(save_dir),
                session=session,
                processes=1,
            )
        except Exception as e:
            print(f"    [错误] 下载过程中出错: {e}")

        downloaded = []
        for item in to_download:
            fname = item.properties.get("fileName", "")
            fpath = save_dir / fname
            if fpath.exists():
                downloaded.append(fpath)
                print(f"    [完成] {fname}")
            else:
                scene_id = item.properties.get("sceneName", "")
                matches = list(save_dir.glob(f"{scene_id}*"))
                if matches:
                    downloaded.extend(matches)
                    print(f"    [完成] {matches[0].name}")

        return downloaded
