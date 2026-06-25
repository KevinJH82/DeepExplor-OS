"""
ASTER Downloader — NASA LP DAAC
使用 CMR API 搜索（绕过 earthaccess IndexError bug），earthaccess session 下载。

支持产品（通过 products 参数选择，默认同时下载）：

  L2 产品（ASTERDownloader，sensor=aster）：
    AST_07   ASTER L2 Surface Reflectance VNIR/SWIR（v004）
               VNIR: B01-B03N, 15m；SWIR: B04-B09, 30m（注：SWIR于2008年故障）
    AST_08   ASTER L2 Surface Kinetic Temperature（v004）
               TIR: 动力温度，90m，可用于地表温度反演
    AST_09T  ASTER L2 Surface Radiance TIR（v004）
               TIR: B10-B14 发射率/辐亮度，90m，结合 AST_08 可计算地表温度

  L1T 产品（ASTERL1TDownloader，sensor=aster_l1t）：
    AST_L1T  ASTER L1T Precision Terrain Corrected Registered At-Sensor Radiance（v004）
               VNIR: B01/B02/B03N, 15m（辐亮度，精准地形校正）
               TIR:  B10-B14, 90m（辐亮度）
               注：SWIR 探测器 2008 年故障，2008 年后无 SWIR 波段

v004 格式说明：
  每景数据在 CMR 中直接以独立 .tif 文件发布，每个波段一个文件，无需 HDF 解析。
  AST_07  → *_SRF_VNIR_B01.tif, *_SRF_VNIR_B02.tif, *_SRF_VNIR_B03N.tif
              *_SRF_SWIR_B04.tif … B09.tif（2008年前数据才有）
  AST_08  → *_SKT.tif
  AST_09T → *_SIR_TIR_B10.tif … B14.tif
  AST_L1T → *_VNIR_B01.tif, *_VNIR_B02.tif, *_VNIR_B03N.tif（15m）
              *_TIR_B10.tif … B14.tif（90m）

地质应用：
  - AST_07 SWIR 波段用于矿物/岩性识别（蚀变矿物、铁氧化物、碳酸盐）
  - AST_08 + AST_09T TIR 波段用于热液异常、岩石类型区分
  - AST_L1T 原始辐亮度，适合自定义大气校正流程

注意：SWIR 探测器（波段4-9）已于2008年4月停止工作，2008年后数据仅含 VNIR 和 TIR 波段。
      AST_09T 于2026年1月永久停止采集，但历史数据仍可下载。

注册（免费，NASA Earthdata账号）：https://urs.earthdata.nasa.gov/
安装：pip install earthaccess requests tqdm
"""

import re
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests
    from tqdm import tqdm
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

# 支持的产品配置：(short_name, version, 描述)
_ASTER_PRODUCTS = {
    "AST_07":  ("AST_07",  "004", "ASTER L2 地表反射率 VNIR/SWIR（15/30m）"),
    "AST_08":  ("AST_08",  "004", "ASTER L2 地表动力温度 TIR（90m）"),
    "AST_09T": ("AST_09T", "004", "ASTER L2 地表发射率 TIR（90m）"),
}

_DEFAULT_PRODUCTS = ["AST_07", "AST_08", "AST_09T"]

# v004 各产品需要下载的文件名关键词（用于过滤 CMR links，排除 QA 辅助文件）
# 匹配规则：文件名包含以下任意关键词即为目标波段文件
_PRODUCT_BAND_PATTERNS = {
    "AST_07":  re.compile(r'_SRF_(?:VNIR|SWIR)_B\d+N?\.tif$', re.IGNORECASE),
    "AST_08":  re.compile(r'_SKT\.tif$', re.IGNORECASE),
    "AST_09T": re.compile(r'_SIR_TIR_B\d+\.tif$', re.IGNORECASE),
}

# ── ASTER L1T 配置 ────────────────────────────────────────────────────────────

_ASTER_L1T_PRODUCTS = {
    "AST_L1T": ("AST_L1T", "004", "ASTER L1T 精准地形校正辐亮度 VNIR+TIR（15/90m）"),
}

_DEFAULT_L1T_PRODUCTS = ["AST_L1T"]

# L1T v004 波段文件命名：
#   VNIR: *_VNIR_B01.tif / *_VNIR_B02.tif / *_VNIR_B03N.tif（15m）
#   TIR:  *_TIR_B10.tif … *_TIR_B14.tif（90m）
#   合并文件 *_VNIR.tif / *_TIR.tif 不下载（逐波段更灵活）
_L1T_BAND_PATTERNS = {
    "AST_L1T": re.compile(r'_(?:VNIR_B\d+N?|TIR_B\d+)\.tif$', re.IGNORECASE),
}


def _aster_cloud_cover(granule: Dict) -> Optional[float]:
    """Return ASTER granule-level cloud cover percentage from CMR metadata."""
    for key in ("cloud_cover", "CloudCover", "_cloud_cover"):
        value = granule.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _filter_and_sort_by_cloud(granules: List[Dict], cloud_cover: int) -> List[Dict]:
    filtered = []
    for granule in granules:
        cloud = _aster_cloud_cover(granule)
        if cloud is None:
            if cloud_cover < 100:
                continue
            cloud = 100.0
        if cloud <= cloud_cover:
            granule["_cloud_cover"] = cloud
            filtered.append(granule)
    filtered.sort(key=lambda g: (_aster_cloud_cover(g) or 100.0, g.get("time_start", "")))
    return filtered


class ASTERDownloader(BaseDownloader):

    PLATFORM_NAME = "aster"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        products: List[str] = None,
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._auth_done = False
        self._session: Optional[requests.Session] = None
        self.products = products or _DEFAULT_PRODUCTS

    def _check_deps(self):
        if not HAS_REQUESTS:
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
            self._session = earthaccess.get_requests_https_session()
        return self._session

    def _search_product(self, short_name: str, version: str,
                        bbox: Tuple, start_date: str, end_date: str,
                        cloud_cover: int = 100) -> List[Dict]:
        """通过 CMR API 搜索单个 ASTER L2 产品"""
        min_lon, min_lat, max_lon, max_lat = bbox
        all_results = []
        page = 1

        while True:
            params = {
                "short_name": short_name,
                "version": version,
                "bounding_box": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "temporal": f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
                "page_size": 100,
                "page_num": page,
            }
            if cloud_cover < 100:
                params["cloud_cover"] = f"0,{cloud_cover}"
            resp = requests.get(
                _CMR_GRANULE_URL,
                params=params,
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

        return all_results

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        **kwargs,
    ) -> List[Any]:
        """
        通过 CMR API 搜索 ASTER L2 产品（AST_07 / AST_08 / AST_09T）。
        返回 [(prod_key, granule_list), ...] 结构。
        """
        self._check_deps()

        all_results = []
        for prod_key in self.products:
            if prod_key not in _ASTER_PRODUCTS:
                print(f"    [警告] 未知产品: {prod_key}，跳过")
                continue

            short_name, version, desc = _ASTER_PRODUCTS[prod_key]
            raw_results = self._search_product(
                short_name, version, bbox, start_date, end_date, cloud_cover
            )
            results = _filter_and_sort_by_cloud(raw_results, cloud_cover)

            print(f"    云量≤{cloud_cover}%  找到 {len(results)} 景 {prod_key}（{desc}）")
            if len(raw_results) != len(results):
                print(f"      [云量过滤] CMR返回 {len(raw_results)} 景，本地复核保留 {len(results)} 景")
            for r in results[:3]:
                title = r.get("title", "")
                dt = r.get("time_start", "")[:10]
                cloud = _aster_cloud_cover(r)
                cloud_text = f"{cloud:.1f}".rstrip("0").rstrip(".") if cloud is not None else "?"
                print(f"      {dt}  云量={cloud_text}%  {title[:60]}")
            if len(results) > 3:
                print(f"      ... 共 {len(results)} 景")

            # 附加 _footprint（从 CMR boxes/polygons 字段）供覆盖选景使用
            try:
                from shapely.geometry import box as _box, Polygon as _Polygon
                for granule in results:
                    # 采集日期（供时序选景使用，取不到则不挂）
                    _d = granule.get("time_start", "")[:10]
                    if _d and "_acq_date" not in granule:
                        granule["_acq_date"] = _d
                    if "_footprint" in granule:
                        continue
                    polys = granule.get("polygons", [])
                    if polys:
                        try:
                            coords_str = polys[0] if isinstance(polys[0], str) else polys[0][0]
                            pairs = coords_str.strip().split()
                            pts = [(float(pairs[i+1]), float(pairs[i])) for i in range(0, len(pairs)-1, 2)]
                            granule["_footprint"] = _Polygon(pts)
                        except Exception:
                            pass
                    if "_footprint" not in granule:
                        boxes = granule.get("boxes", [])
                        if boxes:
                            try:
                                parts = boxes[0].split()
                                s, w, n, e = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
                                granule["_footprint"] = _box(w, s, e, n)
                            except Exception:
                                pass
            except ImportError:
                pass

            all_results.append((prod_key, results))

        return all_results

    def _get_band_urls(self, granule: Dict, prod_key: str) -> List[str]:
        """
        从 CMR granule links 中提取该产品所有目标波段的下载 URL。
        v004 格式：每个波段是独立 .tif 文件，直接通过 CMR links 获取。
        """
        pattern = _PRODUCT_BAND_PATTERNS.get(prod_key)
        if pattern is None:
            return []

        urls = []
        for link in granule.get("links", []):
            if "/data#" not in link.get("rel", ""):
                continue
            href = link.get("href", "")
            filename = href.split("/")[-1].split("?")[0]
            if pattern.search(filename):
                urls.append(href)
        return urls

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 3,
        **kwargs,
    ) -> List[Path]:
        """
        下载 ASTER L2 v004 产品（各波段独立 .tif），无需 HDF 解析。
        每个产品存入子目录：save_dir/AST_07/、save_dir/AST_08/ 等。
        """
        self._check_deps()
        session = self._get_session()

        downloaded = []

        for item in search_results:
            if isinstance(item, tuple) and len(item) == 2:
                prod_key, results = item
            else:
                prod_key = "AST_07"
                results = search_results
                search_results = [(prod_key, results)]

            prod_dir = save_dir / prod_key
            prod_dir.mkdir(parents=True, exist_ok=True)

            to_download = results[:max_items]
            print(f"    正在下载 {len(to_download)} 景 {prod_key} → {prod_dir.name}/")

            for granule in to_download:
                title = granule.get("title", "unknown")
                band_urls = self._get_band_urls(granule, prod_key)

                if not band_urls:
                    print(f"      [跳过] 未找到波段文件链接: {title[:50]}")
                    continue

                # 检查该景所有波段文件是否已全部存在且完整（> 50 KB）
                _MIN_BAND_SIZE = 50 * 1024
                filenames = [u.split("/")[-1].split("?")[0] for u in band_urls]
                existing = [prod_dir / fn for fn in filenames
                            if (prod_dir / fn).exists() and (prod_dir / fn).stat().st_size >= _MIN_BAND_SIZE]
                if len(existing) == len(band_urls):
                    print(f"      已存在全部 {len(band_urls)} 个波段，跳过: {title[:50]}")
                    downloaded.extend(existing)
                    continue

                # 逐波段下载
                scene_bands = []
                for url in band_urls:
                    filename = url.split("/")[-1].split("?")[0]
                    save_path = prod_dir / filename
                    if save_path.exists():
                        if save_path.stat().st_size < _MIN_BAND_SIZE:
                            print(f"      [截断] {filename}（{save_path.stat().st_size} B），删除重下")
                            save_path.unlink()
                        else:
                            scene_bands.append(save_path)
                            continue
                    try:
                        download_with_resume(
                            session, url, save_path,
                            desc=filename, timeout=300,
                        )
                        scene_bands.append(save_path)
                    except Exception as e:
                        print(f"      [错误] {filename}: {e}")

                if scene_bands:
                    band_names = [p.name.split("_")[-1].replace(".tif", "") for p in scene_bands]
                    print(f"      [完成] {prod_key}/{title[:40]} → {len(scene_bands)} 个波段: {', '.join(band_names)}")
                    downloaded.extend(scene_bands)

        return downloaded


class ASTERL1TDownloader(BaseDownloader):
    """
    ASTER L1T 精准地形校正辐亮度下载器。

    产品：AST_L1T v004
    波段：VNIR B01/B02/B03N（15m） + TIR B10-B14（90m）
    数据源：CMR + NASA Earthdata（与 L2 完全相同的认证方式）
    格式：逐波段 .tif，无需 HDF 解析
    """

    PLATFORM_NAME = "aster_l1t"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        products: List[str] = None,
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._session: Optional[requests.Session] = None
        self.products = products or _DEFAULT_L1T_PRODUCTS

    def _check_deps(self):
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests tqdm\n请运行: pip install requests tqdm")
        if not HAS_EARTHACCESS:
            raise ImportError("缺少依赖: earthaccess\n请运行: pip install earthaccess")

    def _get_session(self) -> requests.Session:
        if self._session is None:
            import os
            os.environ["EARTHDATA_USERNAME"] = self.credentials["username"]
            os.environ["EARTHDATA_PASSWORD"] = self.credentials["password"]
            earthaccess.login(strategy="environment")
            self._session = earthaccess.get_requests_https_session()
        return self._session

    def _search_product(self, short_name: str, version: str,
                        bbox: Tuple, start_date: str, end_date: str,
                        cloud_cover: int = 100) -> List[Dict]:
        min_lon, min_lat, max_lon, max_lat = bbox
        all_results = []
        page = 1

        while True:
            params = {
                "short_name": short_name,
                "version": version,
                "bounding_box": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "temporal": f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
                "page_size": 100,
                "page_num": page,
            }
            if cloud_cover < 100:
                params["cloud_cover"] = f"0,{cloud_cover}"
            resp = requests.get(
                _CMR_GRANULE_URL,
                params=params,
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

        return all_results

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        **kwargs,
    ) -> List[Any]:
        """
        通过 CMR API 搜索 AST_L1T v004 产品。
        返回 [(prod_key, granule_list), ...] 结构。
        """
        self._check_deps()

        all_results = []
        for prod_key in self.products:
            if prod_key not in _ASTER_L1T_PRODUCTS:
                print(f"    [警告] 未知产品: {prod_key}，跳过")
                continue

            short_name, version, desc = _ASTER_L1T_PRODUCTS[prod_key]
            raw_results = self._search_product(
                short_name, version, bbox, start_date, end_date, cloud_cover
            )
            results = _filter_and_sort_by_cloud(raw_results, cloud_cover)

            print(f"    云量≤{cloud_cover}%  找到 {len(results)} 景 {prod_key}（{desc}）")
            if len(raw_results) != len(results):
                print(f"      [云量过滤] CMR返回 {len(raw_results)} 景，本地复核保留 {len(results)} 景")
            for r in results[:3]:
                title = r.get("title", "")
                dt = r.get("time_start", "")[:10]
                cloud = _aster_cloud_cover(r)
                cloud_text = f"{cloud:.1f}".rstrip("0").rstrip(".") if cloud is not None else "?"
                print(f"      {dt}  云量={cloud_text}%  {title[:60]}")
            if len(results) > 3:
                print(f"      ... 共 {len(results)} 景")

            # 附加 _footprint（从 CMR boxes/polygons 字段）供覆盖选景使用
            try:
                from shapely.geometry import box as _box, Polygon as _Polygon
                for granule in results:
                    # 采集日期（供时序选景使用，取不到则不挂）
                    _d = granule.get("time_start", "")[:10]
                    if _d and "_acq_date" not in granule:
                        granule["_acq_date"] = _d
                    if "_footprint" in granule:
                        continue
                    polys = granule.get("polygons", [])
                    if polys:
                        try:
                            coords_str = polys[0] if isinstance(polys[0], str) else polys[0][0]
                            pairs = coords_str.strip().split()
                            pts = [(float(pairs[i+1]), float(pairs[i])) for i in range(0, len(pairs)-1, 2)]
                            granule["_footprint"] = _Polygon(pts)
                        except Exception:
                            pass
                    if "_footprint" not in granule:
                        boxes = granule.get("boxes", [])
                        if boxes:
                            try:
                                parts = boxes[0].split()
                                s, w, n, e = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
                                granule["_footprint"] = _box(w, s, e, n)
                            except Exception:
                                pass
            except ImportError:
                pass

            all_results.append((prod_key, results))

        return all_results

    def _get_band_urls(self, granule: Dict, prod_key: str) -> List[str]:
        pattern = _L1T_BAND_PATTERNS.get(prod_key)
        if pattern is None:
            return []

        urls = []
        seen = set()
        for link in granule.get("links", []):
            if "/data#" not in link.get("rel", ""):
                continue
            href = link.get("href", "")
            filename = href.split("/")[-1].split("?")[0]
            if pattern.search(filename) and filename not in seen:
                urls.append(href)
                seen.add(filename)
        return urls

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 3,
        **kwargs,
    ) -> List[Path]:
        """
        下载 AST_L1T v004 逐波段 .tif 文件。
        存入子目录：save_dir/AST_L1T/
        """
        self._check_deps()
        session = self._get_session()

        downloaded = []

        for item in search_results:
            if isinstance(item, tuple) and len(item) == 2:
                prod_key, results = item
            else:
                prod_key = "AST_L1T"
                results = search_results
                search_results = [(prod_key, results)]

            prod_dir = save_dir / prod_key
            prod_dir.mkdir(parents=True, exist_ok=True)

            to_download = results[:max_items]
            print(f"    正在下载 {len(to_download)} 景 {prod_key} → {prod_dir.name}/")

            for granule in to_download:
                title = granule.get("title", "unknown")
                band_urls = self._get_band_urls(granule, prod_key)

                if not band_urls:
                    print(f"      [跳过] 未找到波段文件链接: {title[:50]}")
                    continue

                filenames = [u.split("/")[-1].split("?")[0] for u in band_urls]
                existing = [prod_dir / fn for fn in filenames if (prod_dir / fn).exists()]
                if len(existing) == len(band_urls):
                    print(f"      已存在全部 {len(band_urls)} 个波段，跳过: {title[:50]}")
                    downloaded.extend(existing)
                    continue

                scene_bands = []
                for url in band_urls:
                    filename = url.split("/")[-1].split("?")[0]
                    save_path = prod_dir / filename
                    if save_path.exists():
                        scene_bands.append(save_path)
                        continue
                    try:
                        download_with_resume(
                            session, url, save_path,
                            desc=filename, timeout=300,
                        )
                        scene_bands.append(save_path)
                    except Exception as e:
                        print(f"      [错误] {filename}: {e}")

                if scene_bands:
                    band_names = [p.name.split("_")[-1].replace(".tif", "") for p in scene_bands]
                    print(f"      [完成] {prod_key}/{title[:40]} → {len(scene_bands)} 个波段: {', '.join(band_names)}")
                    downloaded.extend(scene_bands)

        return downloaded
