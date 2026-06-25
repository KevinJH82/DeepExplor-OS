"""
Planet PlanetScope Downloader — Planet Labs API v2
使用 Planet Python SDK（pip install planet）或直接 REST API 搜索和下载。

传感器：PlanetScope（Dove/SuperDove 卫星星座）
        数百颗小卫星，每天覆盖全球一次以上
波段：
  · 4波段（BGRN）：蓝443nm/绿490nm/红531nm/近红外858nm
  · 8波段（AnalyticMS，SuperDove）：Coastal Blue/Blue/Green I/Green/Yellow/Red/Red Edge/NIR
分辨率：3-5m（nadir 约 3m，边缘约 5m）
幅宽：约 24.6km × 16.4km（单景）
重访：每天 1-2 次（星座密度高，赤道附近更频繁）

产品类型：
  PSScene — PlanetScope 4/8波段多光谱场景（主要产品）

资产类型（asset_type）：
  ortho_analytic_4b_sr  — 4波段地表反射率（L2，大气校正）★推荐
  ortho_analytic_8b_sr  — 8波段地表反射率（L2，SuperDove专属）★推荐
  ortho_analytic_4b     — 4波段辐亮度（未大气校正）
  ortho_analytic_8b     — 8波段辐亮度
  ortho_udm2            — 可用性/云掩膜

申请免费账号：
  · 教育/科研：https://www.planet.com/markets/education-and-research/
  · 注册后在 https://www.planet.com/account/#/ 获取 API Key

安装：pip install planet requests tqdm

使用方式：
  在 config/credentials.yaml 中添加：
    planet:
      api_key: YOUR_PLANET_API_KEY
"""

import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests
    from tqdm import tqdm
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


# Planet Data API v1 端点
_PLANET_BASE = "https://api.planet.com/data/v1"
_SEARCH_URL  = f"{_PLANET_BASE}/searches"
_ITEMS_URL   = f"{_PLANET_BASE}/item-types/{{item_type}}/items/{{item_id}}/assets"

# 默认产品类型和资产类型
_ITEM_TYPE = "PSScene"

# 按优先级尝试的资产类型（8波段SR优先，4波段SR次之）
_ASSET_PRIORITY = [
    "ortho_analytic_8b_sr",   # SuperDove 8波段地表反射率
    "ortho_analytic_4b_sr",   # Dove 4波段地表反射率
    "ortho_analytic_8b",      # 8波段辐亮度（无SR时回退）
    "ortho_analytic_4b",      # 4波段辐亮度
]

# 激活等待参数
_ACTIVATE_POLL_INTERVAL = 10   # 秒
_ACTIVATE_MAX_WAIT      = 300  # 秒


class PlanetDownloader(BaseDownloader):

    PLATFORM_NAME = "planet"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)
        api_key = credentials.get("api_key", "")
        if not api_key:
            raise ValueError(
                "缺少 Planet API Key\n"
                "请在 config/credentials.yaml 中添加:\n"
                "  planet:\n"
                "    api_key: YOUR_PLANET_API_KEY\n"
                "申请免费教育账号: https://www.planet.com/markets/education-and-research/"
            )
        # Planet 使用 HTTP Basic Auth：用户名=API Key，密码为空
        self._auth = requests.auth.HTTPBasicAuth(api_key, "")
        self._session: Optional[requests.Session] = None

    def _check_deps(self):
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests tqdm\n请运行: pip install requests tqdm")

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.auth = self._auth
        return self._session

    def _build_search_filter(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int,
    ) -> Dict:
        """构造 Planet API 搜索过滤器（AndFilter）"""
        min_lon, min_lat, max_lon, max_lat = bbox
        return {
            "type": "AndFilter",
            "config": [
                {
                    "type": "GeometryFilter",
                    "field_name": "geometry",
                    "config": {
                        "type": "Polygon",
                        "coordinates": [[
                            [min_lon, min_lat],
                            [max_lon, min_lat],
                            [max_lon, max_lat],
                            [min_lon, max_lat],
                            [min_lon, min_lat],
                        ]],
                    },
                },
                {
                    "type": "DateRangeFilter",
                    "field_name": "acquired",
                    "config": {
                        "gte": f"{start_date}T00:00:00Z",
                        "lte": f"{end_date}T23:59:59Z",
                    },
                },
                {
                    "type": "RangeFilter",
                    "field_name": "cloud_cover",
                    "config": {"lte": cloud_cover / 100.0},  # Planet 用 0-1 小数
                },
            ],
        }

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_results: int = 100,
        **kwargs,
    ) -> List[Dict]:
        """
        通过 Planet Data API v1 搜索 PlanetScope 影像。

        Returns
        -------
        List[Dict]：Planet GeoJSON Feature 列表，每项代表一景 PSScene
        """
        self._check_deps()
        session = self._get_session()

        filt = self._build_search_filter(bbox, start_date, end_date, cloud_cover)

        # 创建快速搜索（Quick Search，无需持久化）
        payload = {
            "item_types": [_ITEM_TYPE],
            "filter": filt,
        }
        resp = session.post(
            f"{_PLANET_BASE}/quick-search",
            json=payload,
            params={"_page_size": min(max_results, 250)},
            timeout=60,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            if resp.status_code == 401:
                raise RuntimeError(
                    "Planet API 认证失败（HTTP 401）\n"
                    "请检查 credentials.yaml 中的 planet.api_key"
                )
            raise RuntimeError(f"Planet 搜索失败 (HTTP {resp.status_code}): {resp.text[:300]}")

        data = resp.json()
        items = data.get("features", [])

        # 分页获取更多结果
        while len(items) < max_results:
            next_url = data.get("_links", {}).get("_next")
            if not next_url:
                break
            resp = session.get(next_url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            new_items = data.get("features", [])
            if not new_items:
                break
            items.extend(new_items)

        items = items[:max_results]

        print(f"    云量<{cloud_cover}%  共找到: {len(items)} 景 PlanetScope")
        for item in items[:5]:
            props = item.get("properties", {})
            print(
                f"      {props.get('acquired','?')[:10]}  "
                f"云量={props.get('cloud_cover',0)*100:.0f}%  "
                f"{item.get('id','?')}"
            )
        if len(items) > 5:
            print(f"      ... 共 {len(items)} 景")

        return items

    def _get_best_asset_type(self, item_id: str) -> Optional[str]:
        """
        查询该 item 可用的资产类型，按优先级选最佳。
        返回资产类型名称，如 'ortho_analytic_8b_sr'。
        """
        session = self._get_session()
        url = _ITEMS_URL.format(item_type=_ITEM_TYPE, item_id=item_id)
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
        assets = resp.json()

        for asset_type in _ASSET_PRIORITY:
            if asset_type in assets:
                return asset_type
        # 没有 SR 产品，取任意可用的
        available = list(assets.keys())
        if available:
            return available[0]
        return None

    def _activate_and_wait(self, item_id: str, asset_type: str) -> Optional[str]:
        """
        激活资产并等待就绪，返回下载 URL。

        Planet 资产需先激活（触发服务端处理），才能下载。
        激活通常需要 10-60 秒。
        """
        session = self._get_session()
        assets_url = _ITEMS_URL.format(item_type=_ITEM_TYPE, item_id=item_id)

        # 获取当前资产状态
        resp = session.get(assets_url, timeout=30)
        resp.raise_for_status()
        asset = resp.json().get(asset_type)
        if not asset:
            return None

        status = asset.get("status", "")

        # 若尚未激活，发送激活请求
        if status in ("inactive", ""):
            activate_url = asset["_links"]["activate"]
            session.post(activate_url, timeout=30)
            print(f"      [激活中] {item_id} / {asset_type}...")

        # 轮询等待激活完成
        elapsed = 0
        while elapsed < _ACTIVATE_MAX_WAIT:
            resp = session.get(assets_url, timeout=30)
            resp.raise_for_status()
            asset = resp.json().get(asset_type, {})
            status = asset.get("status", "")

            if status == "active":
                return asset["location"]  # 下载链接
            elif status == "activating":
                time.sleep(_ACTIVATE_POLL_INTERVAL)
                elapsed += _ACTIVATE_POLL_INTERVAL
            else:
                # 未知状态，稍等重试
                time.sleep(_ACTIVATE_POLL_INTERVAL)
                elapsed += _ACTIVATE_POLL_INTERVAL

        print(f"      [超时] {item_id} 激活等待超过 {_ACTIVATE_MAX_WAIT}s")
        return None

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """
        下载 PlanetScope 影像（GeoTIFF格式）。

        流程：搜索结果 → 选最佳资产类型 → 激活资产 → 等待就绪 → 下载
        每景约 50-200MB（4波段 GeoTIFF，约 24km×16km 区域）。
        """
        self._check_deps()
        session = self._get_session()
        downloaded = []

        to_download = search_results[:max_items]
        print(f"    正在处理 {len(to_download)} 景 PlanetScope...")

        for item in to_download:
            item_id = item.get("id", "")
            if not item_id:
                continue

            # 选最佳资产类型
            asset_type = self._get_best_asset_type(item_id)
            if not asset_type:
                print(f"      [跳过] {item_id}：无可用资产")
                continue

            # 构造目标文件名
            out_path = save_dir / f"{item_id}_{asset_type}.tif"
            if out_path.exists():
                print(f"      已存在，跳过: {out_path.name}")
                downloaded.append(out_path)
                continue

            # 激活并等待
            download_url = self._activate_and_wait(item_id, asset_type)
            if not download_url:
                print(f"      [跳过] {item_id}：激活失败或超时")
                continue

            # 下载（使用 session auth，支持断点续传）
            print(f"      [下载] {item_id}  资产: {asset_type}")
            try:
                download_with_resume(
                    session,
                    download_url,
                    out_path,
                    desc=out_path.name,
                    timeout=600,
                )
                downloaded.append(out_path)
                size_mb = out_path.stat().st_size / 1024 / 1024
                print(f"      [完成] {out_path.name}  ({size_mb:.0f} MB, {asset_type})")
            except Exception as e:
                print(f"      [错误] {item_id}: {e}")

        return downloaded
