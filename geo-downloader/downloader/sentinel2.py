"""
Sentinel-2 Downloader — ESA Copernicus Data Space Ecosystem
使用 OData Catalogue API 搜索和下载。

注册（免费）：https://dataspace.copernicus.eu/
API文档：https://documentation.dataspace.copernicus.eu/

产品：Sentinel-2 Level-2A（大气校正地表反射率，开箱即用）
波段：13波段，10/20/60m分辨率
"""

from pathlib import Path
from typing import List, Tuple, Dict, Optional
import zipfile

try:
    import requests
    from tqdm import tqdm
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


_ODATA_SEARCH_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_ODATA_DOWNLOAD_URL = "https://zipper.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"

# Copernicus 域名在国内可直连，强制绕过代理
# 注意：proxies 值用空字符串 "" 而非 None，才能覆盖环境变量里的 http_proxy/https_proxy
_NO_PROXY = {"http": "", "https": ""}


class Sentinel2Downloader(BaseDownloader):

    PLATFORM_NAME = "sentinel2"
    REQUIRES_AUTH = True

    def __init__(self, credentials: Dict[str, str], output_dir: str = "./downloads", **kwargs):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._access_token: Optional[str] = None

    def _get_token(self) -> str:
        """获取 OAuth2 访问令牌"""
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests\n请运行: pip3 install requests")
        token_session = requests.Session()
        token_session.trust_env = False
        resp = token_session.post(
            _TOKEN_URL,
            data={
                "grant_type": "password",
                "username": self.credentials["username"],
                "password": self.credentials["password"],
                "client_id": "cdse-public",
            },
            timeout=30,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError(
                f"Copernicus认证失败（HTTP {resp.status_code}）\n"
                "请检查 credentials.yaml 中的 copernicus 账号信息。\n"
                f"详情: {resp.text[:200]}"
            )
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_results: int = 50,
        level: str = "S2MSI2A",   # L2A大气校正；L1C用 S2MSI1C
        **kwargs,
    ) -> List[Dict]:
        """通过 OData Catalogue API 搜索 Sentinel-2 影像"""
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests")

        min_lon, min_lat, max_lon, max_lat = bbox
        wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        filter_expr = " and ".join([
            "Collection/Name eq 'SENTINEL-2'",
            f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}')",
            f"ContentDate/Start gt {start_date}T00:00:00.000Z",
            f"ContentDate/Start lt {end_date}T23:59:59.000Z",
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
            f"and att/OData.CSC.StringAttribute/Value eq '{level}')",
            f"Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' "
            f"and att/OData.CSC.DoubleAttribute/Value lt {cloud_cover})",
        ])

        # 搜索请求带重试（网络超时/代理抖动）
        # trust_env=False 确保完全忽略 http_proxy/https_proxy 环境变量，Copernicus 直连
        search_session = requests.Session()
        search_session.trust_env = False
        last_err = None
        for _attempt in range(3):
            try:
                resp = search_session.get(
                    _ODATA_SEARCH_URL,
                    params={"$filter": filter_expr, "$top": max_results,
                            "$orderby": "ContentDate/Start desc"},
                    timeout=90,
                )
                resp.raise_for_status()
                last_err = None
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                wait = 5 * (2 ** _attempt)
                print(f"    [重试] Copernicus 搜索失败 ({type(e).__name__})，{wait}s 后重试 ({_attempt+1}/3)")
                import time; time.sleep(wait)
            except requests.HTTPError:
                raise RuntimeError(f"OData搜索失败 (HTTP {resp.status_code}): {resp.text[:300]}")
        if last_err:
            raise RuntimeError(f"Copernicus 搜索超时，3次重试均失败: {last_err}")

        items = resp.json().get("value", [])
        def _attrs(it):
            attrs = {}
            for a in it.get("Attributes", []):
                name = a.get("Name") or a.get("name")
                if not name:
                    continue
                attrs[name] = a.get("Value", a.get("value"))
            return attrs

        # OData $orderby 不支持 Attributes any() lambda，在 Python 侧按云量升序排序
        def _cloud(it):
            v = _attrs(it).get("cloudCover", 100)
            try:
                return float(v)
            except (TypeError, ValueError):
                return 100.0
        items.sort(key=_cloud)

        # 附加 _footprint（Shapely Polygon, EPSG:4326）供覆盖选景使用
        try:
            from shapely import wkt as _wkt
            for it in items:
                fp_wkt = it.get("GeoFootprint") or it.get("Footprint") or ""
                if fp_wkt:
                    try:
                        it["_footprint"] = _wkt.loads(fp_wkt)
                        it["_cloud_cover"] = _cloud(it)
                    except Exception:
                        pass
                if "_footprint" not in it:
                    # 回退：用 ContentDate 区域构建 bbox
                    it["_cloud_cover"] = _cloud(it)
                # 采集日期（供时序选景使用，取不到则不挂）
                _d = (it.get("ContentDate") or {}).get("Start", "")[:10]
                if _d:
                    it["_acq_date"] = _d
        except ImportError:
            pass

        print(f"    云量<{cloud_cover}%  共找到: {len(items)} 景")
        for it in items[:5]:
            attrs = _attrs(it)
            print(
                f"      {it.get('ContentDate',{}).get('Start','?')[:10]}  "
                f"云量={attrs.get('cloudCover','?')}%  "
                f"{it.get('Name','?')[:65]}"
            )
        if len(items) > 5:
            print(f"      ... 共 {len(items)} 景")

        return items

    @staticmethod
    def _verify_zip(path: Path) -> bool:
        """检查 ZIP 文件是否完整可解压。"""
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                # testzip() 返回第一个损坏文件名，None 表示全部完好
                bad = zf.testzip()
                return bad is None
        except (zipfile.BadZipFile, Exception):
            return False

    def _download_one(self, product_id: str, filename: str, save_path: Path, token: str):
        url = _ODATA_DOWNLOAD_URL.format(product_id=product_id)
        session = requests.Session()
        session.trust_env = False
        # Copernicus 国内可直连，trust_env=False 让 _resolve_proxies 返回空串字典，
        # 显式屏蔽 OS/env 里可能存在的代理设置（OpenVPN 之外的兜底）
        download_with_resume(
            session, url, save_path,
            desc=filename,
            headers={"Authorization": f"Bearer {token}"},
            timeout=600,
            proxies=_NO_PROXY,
        )
        # 下载完成后校验 ZIP 完整性
        if save_path.exists() and not self._verify_zip(save_path):
            size_kb = save_path.stat().st_size // 1024
            save_path.unlink()
            raise RuntimeError(
                f"ZIP 文件不完整（{size_kb} KB），已删除，将重试"
            )

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """通过 OData API 下载 Sentinel-2 产品（ZIP格式）"""
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests, tqdm")

        token = self._get_token()
        downloaded = []

        for item in search_results[:max_items]:
            product_id = item.get("Id", "")
            name = item.get("Name", product_id)
            filename = f"{name}.zip" if not name.endswith(".zip") else name
            save_path = save_dir / filename

            # 已存在的文件：校验 ZIP 完整性，截断则删除重下
            if save_path.exists():
                if self._verify_zip(save_path):
                    print(f"    已存在，跳过: {filename}")
                    downloaded.append(save_path)
                    continue
                else:
                    size_kb = save_path.stat().st_size // 1024
                    print(f"    [警告] {filename} 不完整（{size_kb} KB），删除并重新下载")
                    save_path.unlink()

            print(f"    下载: {filename}")
            try:
                self._download_one(product_id, filename, save_path, token)
                downloaded.append(save_path)
                print(f"    [完成] {filename}")
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 401:
                    print("    [提示] Token已过期，正在刷新...")
                    token = self._get_token()
                    try:
                        self._download_one(product_id, filename, save_path, token)
                        downloaded.append(save_path)
                        print(f"    [完成] {filename}")
                    except Exception as e2:
                        print(f"    [错误] 重试失败 {filename}: {e2}")
                else:
                    print(f"    [错误] 下载失败 {filename}: {e}")
            except Exception as e:
                print(f"    [错误] {filename}: {e}")

        return downloaded
