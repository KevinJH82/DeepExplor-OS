"""
ECOSTRESS Downloader — NASA LP DAAC
使用 CMR API 搜索，earthaccess session 下载。

产品：ECO_L2T_LSTE v002（地表温度和发射率，MGRS分幅Tiled版）
传感器：ECOSTRESS（Earth Surface Mineral Dust Source Investigation）
        搭载于国际空间站（ISS）
分辨率：70m
重访频率：约 1-5 天（ISS轨道，非固定重访）
特点：比 ASTER 热红外重访频率更高，可检测地热异常、热液活动

注意：earthaccess 对 ECOSTRESS 的搜索存在已知 bug（IndexError），
      本下载器改用直接调 NASA CMR API 绕过该问题。

注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
安装：pip install earthaccess requests tqdm
"""

import time
import random
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests
    from tqdm import tqdm
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

try:
    import earthaccess
    HAS_EARTHACCESS = True
except ImportError:
    HAS_EARTHACCESS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


_CMR_GRANULE_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
_ECOSTRESS_SHORTNAME = "ECO_L2T_LSTE"
_ECOSTRESS_VERSION = "002"


class ECOSTRESSDownloader(BaseDownloader):

    PLATFORM_NAME = "ecostress"
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
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests tqdm\n请运行: pip install requests tqdm")
        if not HAS_EARTHACCESS:
            raise ImportError("缺少依赖: earthaccess\n请运行: pip install earthaccess")

    def _get_session(self) -> requests.Session:
        """获取带 NASA Earthdata 认证的 requests Session"""
        if self._session is None:
            import os
            os.environ["EARTHDATA_USERNAME"] = self.credentials["username"]
            os.environ["EARTHDATA_PASSWORD"] = self.credentials["password"]
            earthaccess.login(strategy="environment")
            # earthaccess 内部的 session 已处理 URS 认证重定向
            self._session = earthaccess.get_requests_https_session()
        return self._session

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        **kwargs,
    ) -> List[Dict]:
        """
        通过 CMR API 搜索 ECOSTRESS L2T 地表温度产品。
        绕过 earthaccess 的已知搜索 bug，直接查询 CMR。
        """
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        all_results = []
        page = 1

        while True:
            resp = requests.get(
                _CMR_GRANULE_URL,
                params={
                    "short_name": _ECOSTRESS_SHORTNAME,
                    "version": _ECOSTRESS_VERSION,
                    "bounding_box": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                    "temporal": f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
                    "page_size": 100,
                    "page_num": page,
                },
                timeout=30,
            )
            resp.raise_for_status()
            entries = resp.json().get("feed", {}).get("entry", [])
            if not entries:
                break
            all_results.extend(entries)
            if len(entries) < 100:
                break
            page += 1

        print(f"    找到 {len(all_results)} 景 ECOSTRESS L2T 地表温度（70m）")
        for r in all_results[:5]:
            title = r.get("title", "")
            # 从标题提取日期，格式如 ECOv002_L2T_LSTE_19821_005_51QTF_20220103T150104
            parts = title.split("_")
            dt = ""
            for p in parts:
                if len(p) == 15 and "T" in p:
                    dt = p[:8]
                    break
            print(f"      {dt}  {title[:65]}")
        if len(all_results) > 5:
            print(f"      ... 共 {len(all_results)} 景")

        return all_results

    def _get_download_url(self, granule: Dict) -> Optional[str]:
        """从 CMR granule 条目中提取下载 URL，优先取 LST 主体文件"""
        links = granule.get("links", [])
        data_links = [
            lk.get("href", "") for lk in links
            if "fedsearch/1.1/data#" in lk.get("rel", "")
            and lk.get("href", "").endswith(".tif")
        ]

        # 优先级：_LST.tif > _EmisWB.tif > 其他 .tif（排除辅助文件）
        _skip_suffixes = ("_water.tif", "_cloud.tif", "_QC.tif",
                          "_LST_err.tif", "_view_zenith.tif", "_height.tif")
        for url in data_links:
            if url.endswith("_LST.tif"):
                return url
        for url in data_links:
            if not any(url.endswith(s) for s in _skip_suffixes):
                return url
        return data_links[0] if data_links else None

    def _download_file(self, url: str, save_path: Path, session: requests.Session):
        """下载单个文件（带断点续传）"""
        download_with_resume(session, url, save_path, desc=save_path.name, timeout=300)

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """
        下载 ECOSTRESS 地表温度产品（GeoTIFF，含地表温度+发射率）。
        """
        self._check_deps()
        session = self._get_session()

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 ECOSTRESS...")

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
                max_file_retries = 3
                for file_attempt in range(max_file_retries):
                    try:
                        self._download_file(url, save_path, session)
                        downloaded.append(save_path)
                        print(f"    [完成] {filename}")
                        break
                    except Exception as e:
                        # 清理可能的残留 .part 文件
                        part = save_path.with_suffix(save_path.suffix + ".part")
                        part.unlink(missing_ok=True)

                        if file_attempt < max_file_retries - 1:
                            wait = 15 * (2 ** file_attempt) + random.uniform(0, 5)
                            print(f"      [文件重试 {file_attempt+1}/{max_file_retries}] "
                                  f"{filename}: {e}")
                            print(f"      重置会话，{wait:.0f}s 后重试...")
                            # 重置 session — earthaccess token 可能已过期
                            self._session = None
                            time.sleep(wait)
                            session = self._get_session()
                        else:
                            raise
            except Exception as e:
                print(f"    [错误] {title[:50]}: {e}")

        return downloaded
