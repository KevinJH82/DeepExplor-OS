"""
ALOS PALSAR Downloader — ASF DAAC
使用 asf_search 官方库搜索和下载。

数据来源：Alaska Satellite Facility (ASF DAAC)
产品：ALOS-1 PALSAR L1.1（单视复数，SLC）或 L1.5（地距）
传感器：PALSAR（相控阵L波段合成孔径雷达）
        搭载于 ALOS-1 卫星（日本，2006-2011年）
波段：L波段（23.6cm），穿透植被能力强，适合地质填图
极化：HH、HV、VV、VH（视模式而定）

注意：ALOS-2 PALSAR-2 数据需通过 JAXA G-Portal 单独申请，
      此下载器仅支持 ASF 存档的 ALOS-1 数据（2006-2011年）。

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


class ALOSDownloader(BaseDownloader):

    PLATFORM_NAME = "alos"
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
        cloud_cover: int = 100,   # SAR不受云量影响
        processing_level: str = "L1.1",
        **kwargs,
    ) -> List[Any]:
        """
        通过 asf_search 搜索 ALOS PALSAR 产品。

        Parameters
        ----------
        processing_level : 'L1.1'（SLC复数）或 'L1.5'（地距强度图）
        """
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        aoi_wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        results = asf.search(
            platform=[asf.PLATFORM.ALOS],
            processingLevel=[processing_level],
            intersectsWith=aoi_wkt,
            start=f"{start_date}T00:00:00Z",
            end=f"{end_date}T23:59:59Z",
            maxResults=100,
        )

        print(f"    找到 {len(results)} 景 ALOS PALSAR {processing_level}")
        if len(results) == 0:
            print(f"    [提示] ALOS-1 卫星运行期为 2006-2011 年，请将时间范围设在此区间内")
        for r in list(results)[:5]:
            p = r.properties
            print(
                f"      {p.get('startTime','?')[:10]}  "
                f"轨道={p.get('pathNumber','?')}  "
                f"极化={p.get('polarization','?')}  "
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
        """使用 asf_search 下载 ALOS PALSAR 产品（ZIP格式）。"""
        self._check_deps()

        username = self.credentials["username"]
        password = self.credentials["password"]

        # 使用 earthaccess JWT token 认证（与 Sentinel-1 相同方式）
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
        print(f"    正在下载 {len(to_download)} 景 ALOS PALSAR...")

        # 清除上次中断留下的 .part 残片（asf_search 托管下载，无法续传）
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

        # 收集已下载文件
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
