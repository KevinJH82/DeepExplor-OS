"""
Airbus OneAtlas Downloader — SPOT 6/7 & Pleiades 1A/1B
通过 Airbus OneAtlas Living Library API 搜索和订购。

注册（需商业账号）：
  https://oneatlas.airbus.com/

产品：
  SPOT 6/7   — 全色1.5m / 多光谱6m，4波段（BGRN）
  Pleiades 1A/1B — 全色0.5m / 多光谱2m，4波段（BGRN）

格式：GeoTIFF

API 文档：
  https://api.oneatlas.airbus.com/api-catalog/oneatlas-data/index.html

注意：
  OneAtlas 是商业平台，数据按景收费（~数百美元/景）。
  API Key 在账户控制台申请：https://account.foundation.oneatlas.airbus.com/
  此下载器仅执行搜索（免费），下载需在账户余额充足时执行。
"""

import json
import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .base import BaseDownloader

# Airbus OneAtlas API 端点
_BASE_URL   = "https://authenticate.foundation.api.oneatlas.airbus.com"
_TOKEN_URL  = f"{_BASE_URL}/auth/realms/IDP/protocol/openid-connect/token"
_SEARCH_URL = "https://search.foundation.api.oneatlas.airbus.com/api/v2/opensearch"
_ORDER_URL  = "https://data.api.oneatlas.airbus.com/api/v1/orders"

_PLATFORM_IDS = {
    "spot67":     ["SPOT 6", "SPOT 7"],
    "pleiades":   ["PHR 1A", "PHR 1B"],
}


class OneAtlasDownloader(BaseDownloader):
    """Airbus OneAtlas SPOT 6/7 & Pleiades 1A/1B 下载器"""

    PLATFORM_NAME = "oneatlas"
    REQUIRES_AUTH = True

    def __init__(self, credentials: Dict[str, str],
                 output_dir: str = "./downloads",
                 platform: str = "spot67",   # "spot67" | "pleiades"
                 **kwargs):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._platform = platform
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    def _check_deps(self):
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests\n请运行: pip install requests")

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """获取 / 刷新 OAuth2 access token"""
        if self._token and time.time() < self._token_expiry - 30:
            return self._token

        api_key = self.credentials.get("api_key", "")
        if not api_key:
            raise RuntimeError(
                "缺少 Airbus OneAtlas API Key。\n"
                "请在 https://account.foundation.oneatlas.airbus.com/ 申请，"
                "并在 credentials.yaml 中配置 oneatlas.api_key"
            )

        resp = requests.post(
            _TOKEN_URL,
            data={
                "apikey":       api_key,
                "grant_type":   "api_key",
                "client_id":    "IDP",
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + body.get("expires_in", 3600)
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type":  "application/json",
        }

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 80,
        **kwargs,
    ) -> List[Dict]:
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        platform_names = _PLATFORM_IDS.get(self._platform, _PLATFORM_IDS["spot67"])

        all_results = []
        for platform_name in platform_names:
            params = {
                "bbox":             f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "acquisitionDate":  f"[{start_date},{end_date}]",
                "cloudCover":       f"[0,{cloud_cover}]",
                "constellation":    platform_name,
                "processingLevel":  "ORTHO",      # 正射校正产品
                "count":            kwargs.get("count", 50),
            }
            try:
                r = requests.get(
                    _SEARCH_URL, params=params,
                    headers=self._headers(), timeout=60,
                )
                r.raise_for_status()
                data = r.json()
                features = data.get("features", [])
                for feat in features:
                    props = feat.get("properties", {})
                    all_results.append({
                        "id":           feat.get("id", ""),
                        "name":         props.get("title", feat.get("id", "")),
                        "date":         props.get("acquisitionDate", ""),
                        "cloud_cover":  props.get("cloudCover", 0),
                        "platform":     props.get("constellation", platform_name),
                        "resolution":   props.get("resolution", ""),
                        "_raw":         feat,
                    })
            except Exception as e:
                print(f"    [警告] OneAtlas 搜索失败 ({platform_name}): {e}")

        label = "SPOT 6/7" if self._platform == "spot67" else "Pleiades 1A/1B"
        print(f"    找到 {len(all_results)} 景 {label}")
        for item in all_results[:3]:
            print(f"      {item['name']}  日期:{item['date']}  云量:{item['cloud_cover']}%  分辨率:{item['resolution']}m")
        if len(all_results) > 3:
            print(f"      ... 共 {len(all_results)} 景")

        if all_results:
            print(
                "\n  [提示] OneAtlas 为商业平台，下载前请确认账户余额充足。\n"
                "  订单管理：https://oneatlas.airbus.com/orders\n"
            )

        return all_results

    # ------------------------------------------------------------------
    # 下载（需账户余额）
    # ------------------------------------------------------------------

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 3,
        **kwargs,
    ) -> List[Path]:
        self._check_deps()

        to_download = search_results[:max_items]
        downloaded = []

        for item in to_download:
            product_id = item.get("id", "")
            name       = item.get("name", product_id)
            if not product_id:
                print(f"    [跳过] 无产品 ID: {name}")
                continue

            dest = save_dir / f"{name}.zip"
            if dest.exists():
                print(f"    [已存在] {dest.name}")
                downloaded.append(dest)
                continue

            print(f"    下载 OneAtlas: {name}")

            try:
                # 第一步：创建订单获取下载链接
                order_payload = {
                    "kind":     "order.data.product",
                    "products": [{"id": product_id, "crsCode": "urn:ogc:def:crs:EPSG::4326"}],
                }
                r_order = requests.post(
                    _ORDER_URL, json=order_payload,
                    headers=self._headers(), timeout=60,
                )
                r_order.raise_for_status()
                order = r_order.json()
                download_url = (
                    order.get("_links", {}).get("download", {}).get("href") or
                    order.get("downloadUrl", "")
                )

                if not download_url:
                    print(f"    [警告] 未获取到下载链接，请在 OneAtlas 控制台手动下载: {name}")
                    continue

                # 第二步：流式下载
                r_dl = requests.get(
                    download_url, headers=self._headers(),
                    stream=True, timeout=600,
                )
                r_dl.raise_for_status()

                part = dest.with_suffix(".zip.part")
                with open(part, "wb") as f:
                    for chunk in r_dl.iter_content(65536):
                        if chunk:
                            f.write(chunk)
                part.rename(dest)

                print(f"    [完成] {dest.name}")
                downloaded.append(dest)

            except Exception as e:
                print(f"    [错误] OneAtlas 下载失败 {name}: {e}")

        return downloaded
