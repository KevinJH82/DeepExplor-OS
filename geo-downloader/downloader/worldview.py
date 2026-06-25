"""
WorldView Downloader — WorldView-2/3 (Maxar)
通过 Maxar Discovery API (STAC) 搜索，通过 Maxar Download API 下载。

注册（需商业账号）：
  https://account.maxar.com/

产品：
  WorldView-2 — 全色0.46m / 多光谱1.85m，8波段
  WorldView-3 — 全色0.31m / 多光谱1.24m，8波段 + SWIR 16波段

格式：GeoTIFF

API 文档：
  https://developers.maxar.com/docs/discovery/
  https://developers.maxar.com/docs/streaming/

注意：
  Maxar 是商业平台，数据按图幅收费（~数百至数千美元/景）。
  API Key 在账户控制台申请后，配置在 credentials.yaml 中。
"""

import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .base import BaseDownloader

# Maxar API 端点
_MAXAR_STAC    = "https://api.maxar.com/discovery/v1/search"
_MAXAR_STREAM  = "https://api.maxar.com/streaming/v1/ogc/wms"

# 产品集合
_COLLECTIONS = {
    "wv2": "wv02",
    "wv3": "wv03-multispectral",
}


class WorldViewDownloader(BaseDownloader):
    """Maxar WorldView-2/3 下载器"""

    PLATFORM_NAME = "worldview"
    REQUIRES_AUTH = True

    def __init__(self, credentials: Dict[str, str],
                 output_dir: str = "./downloads",
                 platform: str = "wv3",   # "wv2" | "wv3"
                 **kwargs):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._platform = platform

    def _check_deps(self):
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests\n请运行: pip install requests")

    def _api_key(self) -> str:
        key = self.credentials.get("api_key", "")
        if not key:
            raise RuntimeError(
                "缺少 Maxar API Key。\n"
                "请在 https://account.maxar.com/ 申请，"
                "并在 credentials.yaml 中配置 worldview.api_key"
            )
        return key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type":  "application/json",
        }

    # ------------------------------------------------------------------
    # 搜索（Maxar STAC Discovery API）
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
        collection = _COLLECTIONS.get(self._platform, "wv03-multispectral")

        payload = {
            "collections": [collection],
            "bbox":        [min_lon, min_lat, max_lon, max_lat],
            "datetime":    f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
            "query": {
                "eo:cloud_cover": {"lte": cloud_cover},
            },
            "limit": kwargs.get("count", 50),
        }

        try:
            r = requests.post(
                _MAXAR_STAC, json=payload,
                headers=self._headers(), timeout=60,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    [警告] WorldView 搜索失败: {e}")
            return []

        features = data.get("features", [])
        results = []
        for feat in features:
            props = feat.get("properties", {})
            # 下载链接可能在 assets 中
            assets = feat.get("assets", {})
            download_url = ""
            for asset_key in ("visual", "pan", "ms", "data"):
                if asset_key in assets:
                    download_url = assets[asset_key].get("href", "")
                    break

            results.append({
                "id":           feat.get("id", ""),
                "name":         props.get("platform", "") + "_" + feat.get("id", "")[:16],
                "date":         props.get("datetime", props.get("date", ""))[:10],
                "cloud_cover":  props.get("eo:cloud_cover", 0),
                "platform":     props.get("platform", collection),
                "gsd":          props.get("gsd", ""),
                "download_url": download_url,
                "_raw":         feat,
            })

        label = "WorldView-2" if self._platform == "wv2" else "WorldView-3"
        print(f"    找到 {len(results)} 景 {label}")
        for item in results[:3]:
            print(f"      {item['name']}  日期:{item['date']}  云量:{item['cloud_cover']}%  GSD:{item['gsd']}m")
        if len(results) > 3:
            print(f"      ... 共 {len(results)} 景")

        if results:
            print(
                "\n  [提示] WorldView 为商业平台，下载前请确认账户余额充足。\n"
                "  订单管理：https://account.maxar.com/\n"
            )

        return results

    # ------------------------------------------------------------------
    # 下载
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
            name        = item.get("name", item.get("id", "WV_unknown"))
            download_url = item.get("download_url", "")

            if not download_url:
                print(
                    f"    [跳过] {name}：无直接下载链接。\n"
                    "    请在 Maxar SecureWatch 控制台手动订购并下载。\n"
                    "    https://securewatch.digitalglobe.com/"
                )
                continue

            dest = save_dir / f"{name}.tif"
            if dest.exists():
                print(f"    [已存在] {dest.name}")
                downloaded.append(dest)
                continue

            print(f"    下载 WorldView: {name}")
            try:
                r = requests.get(
                    download_url, headers=self._headers(),
                    stream=True, timeout=600,
                )
                r.raise_for_status()

                part = dest.with_suffix(".tif.part")
                with open(part, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if chunk:
                            f.write(chunk)
                part.rename(dest)

                print(f"    [完成] {dest.name}")
                downloaded.append(dest)

            except Exception as e:
                print(f"    [错误] WorldView 下载失败 {name}: {e}")

        return downloaded
