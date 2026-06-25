"""
Landsat Downloader — Microsoft Planetary Computer STAC + USGS LandsatLook STAC
使用 Planetary Computer STAC API 搜索 L2 产品，USGS LandsatLook STAC 补充 L1 波段。

数据来源：
  L2 主体：Microsoft Planetary Computer（Landsat Collection 2 Level-2），无需账号
  L1 补充：USGS LandsatLook（Landsat Collection 2 Level-1），需要 USGS ERS 账号

支持卫星：
  Landsat 8/9  Collection 2 Level-2（2013年至今）
               L2 波段：coastal/B1, blue/B2, green/B3, red/B4, nir08/B5,
                        swir16/B6, swir22/B7, lwir11/B10（共8个，30m）
               L1 补充：pan/B8（15m全色）, cirrus/B9（卷云）, lwir12/B11（热红外2）
               注意：pan/cirrus/lwir12 在 L2 产品中不存在（USGS 设计决策）；
                     lwir12 存在 TIRS 杂散光问题，精度低于 lwir11

  Landsat 7    Collection 2 Level-2（1999-2022年）
               L2 波段：blue/B1, green/B2, red/B3, nir08/B4, swir16/B5,
                        tir/B6, swir22/B7, pan/B8（8个）
               注意：2003年5月 SLC 扫描线校正器故障，之后影像有条带状空洞

Planetary Computer STAC collection：
  两者均在 "landsat-c2-l2" collection 中，通过 platform 字段区分
  Landsat 7:  platform = "landsat-7"
  Landsat 8:  platform = "landsat-8"
  Landsat 9:  platform = "landsat-9"

USGS LandsatLook STAC：
  L1 collection：landsat-c2l1（注意无连字符）
  平台字段：LANDSAT_8（大写）
  认证：credentials.yaml 中 usgs.username / usgs.password（USGS ERS 账号）
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

from .base import BaseDownloader, download_with_chunks as download_with_resume, download_with_resume as _download_single, download_with_chunks

_PC_STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
_PC_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
_USGS_STAC_SEARCH = "https://landsatlook.usgs.gov/stac-server/search"
_USGS_ERS_LOGIN = "https://ers.cr.usgs.gov/login"

# Landsat 8/9 Collection 2 Level-2 实际包含的波段（asset key）
# 注意：pan(B8)、cirrus(B9)、lwir12(B11) 在 L2 中不存在，由 L1 补充下载
_BANDS_L89_L2 = [
    "coastal",   # B1  443nm
    "blue",      # B2  482nm
    "green",     # B3  562nm
    "red",       # B4  655nm
    "nir08",     # B5  865nm
    "swir16",    # B6  1610nm
    "swir22",    # B7  2200nm
    "lwir11",    # B10 热红外 10895nm（100m→30m）
    "qa_pixel",  # QA像素云掩膜
]

# Landsat 8/9 L1 补充波段（L2 中不存在，从 USGS LandsatLook L1 下载）
_BANDS_L89_L1_SUPPLEMENT = [
    "pan",       # B8  全色 590nm，15m 分辨率
    "cirrus",    # B9  卷云检测 1370nm
    "lwir12",    # B11 热红外 12005nm（注意：存在杂散光问题，精度低于 lwir11）
]

# 向后兼容别名
_BANDS_L89 = _BANDS_L89_L2 + _BANDS_L89_L1_SUPPLEMENT
_BANDS_L2 = _BANDS_L89

# Landsat 7 ETM+ L2 波段（asset key）
# L2C2 中 Landsat 7 无 coastal/cirrus/lwir12 波段
_BANDS_L7 = [
    "blue",      # B1  450-520nm   30m
    "green",     # B2  520-600nm   30m
    "red",       # B3  630-690nm   30m
    "nir08",     # B4  770-900nm   30m
    "swir16",    # B5  1550-1750nm 30m
    "tir",       # B6  热红外 60m→30m（L2产品中字段名为 tir）
    "swir22",    # B7  2080-2350nm 30m
    "pan",       # B8  520-900nm   15m（全色，分辨率最高）
    "qa_pixel",  # QA像素云掩膜
]


def _band_file_exists(scene_dir: Path, scene_id: str, band: str, ext: str = ".TIF"):
    """检查波段文件是否存在且完整（兼容原始和裁剪后的文件名，< 50 KB 视为截断）"""
    _MIN_SIZE = 50 * 1024
    raw = scene_dir / f"{scene_id}_{band}{ext}"
    if raw.exists():
        if raw.stat().st_size >= _MIN_SIZE:
            return raw
        raw.unlink()   # 截断文件，删除后重下
    clipped = scene_dir / f"{scene_id}_{band}_clipped{ext}"
    if clipped.exists():
        if clipped.stat().st_size >= _MIN_SIZE:
            return clipped
        clipped.unlink()
    return None


class LandsatDownloader(BaseDownloader):

    PLATFORM_NAME = "landsat"
    REQUIRES_AUTH = False   # Planetary Computer无需账号

    def __init__(
        self,
        credentials: Optional[Dict[str, str]] = None,
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials or {}, output_dir=output_dir)

    def _check_deps(self):
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests, tqdm\n请运行: pip3 install requests tqdm")

    def _sign_url(self, href: str, max_retries: int = 5) -> str:
        """通过 Planetary Computer SAS 签名端点获取可下载的URL（带重试）"""
        _sign_session = requests.Session()
        for attempt in range(max_retries):
            try:
                resp = _sign_session.get(_PC_SIGN_URL, params={"href": href}, timeout=30)
                if resp.status_code == 429:
                    # Retry-After 头优先，否则指数退避（最短 30s）
                    retry_after = int(resp.headers.get("Retry-After", 0))
                    wait = max(retry_after, 30 * (2 ** attempt)) + random.uniform(0, 5)
                    print(f"      [签名重试 {attempt+1}/{max_retries}] 429限速，{wait:.0f}s后重试")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()["href"]
            except requests.HTTPError:
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 5 * (2 ** attempt) + random.uniform(0, 2)
                    print(f"      [签名重试 {attempt+1}/{max_retries}] {wait:.0f}s后重试: {e}")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"PC签名失败，{max_retries}次重试后仍为429限速: {href}")

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_results: int = 50,
        platforms: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Dict]:
        """
        通过 Planetary Computer STAC 搜索 Landsat C2 L2 产品。

        Parameters
        ----------
        platforms : 限制卫星平台，可选 ["landsat-8", "landsat-9", "landsat-7"]
                    默认 None = ["landsat-8", "landsat-9"]（与 sensor id "landsat" 的契约一致；
                    L7 走独立的 Landsat7Downloader/sensor id "landsat7"，子类已显式传 platforms）
        """
        self._check_deps()
        min_lon, min_lat, max_lon, max_lat = bbox

        # Why: sensor id "landsat" 在主程序里默认指 L8/L9。早期 platforms=None 表示"全部"
        # 会让 L7 数据混入 landsat/ 目录，被 _package_landsat 按 L8 band-map 处理（L7 没
        # coastal/B1 → 输出残缺）。已观察到刚果(金)项目这个问题，统一默认到 L8/L9。
        if platforms is None:
            platforms = ["landsat-8", "landsat-9"]

        query: Dict[str, Any] = {"eo:cloud_cover": {"lt": cloud_cover}}
        if platforms:
            query["platform"] = {"in": platforms}

        payload = {
            "collections": ["landsat-c2-l2"],
            "bbox": [min_lon, min_lat, max_lon, max_lat],
            "datetime": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
            "query": query,
            "limit": max_results,
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        }
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = requests.post(_PC_STAC_SEARCH, json=payload, timeout=90)
                resp.raise_for_status()
                break
            except (requests.HTTPError, requests.ConnectionError,
                    requests.Timeout) as e:
                status = getattr(resp, "status_code", None) if isinstance(e, requests.HTTPError) else None
                # 5xx / 超时 / 连接错误 → 可重试
                retryable = status is None or (status and status >= 500)
                if retryable and attempt < max_retries - 1:
                    wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                    msg = f"HTTP {status}" if status else str(e)
                    print(f"    [STAC搜索重试 {attempt+1}/{max_retries}] {wait:.0f}s后重试: {msg}")
                    time.sleep(wait)
                else:
                    detail = resp.text[:300] if status else str(e)
                    code = f" (HTTP {status})" if status else ""
                    raise RuntimeError(f"Planetary Computer STAC搜索失败{code}: {detail}")

        items = resp.json().get("features", [])

        # 附加 _footprint（Shapely Polygon, EPSG:4326）供覆盖选景使用
        try:
            from shapely.geometry import shape as _shape
            for it in items:
                geom = it.get("geometry")
                if geom:
                    try:
                        it["_footprint"] = _shape(geom)
                        it["_cloud_cover"] = it.get("properties", {}).get("eo:cloud_cover", 100)
                    except Exception:
                        pass
                # 采集日期（供时序选景使用，取不到则不挂）
                _d = it.get("properties", {}).get("datetime", "")[:10]
                if _d:
                    it["_acq_date"] = _d
        except ImportError:
            pass

        print(f"    云量<{cloud_cover}%  共找到: {len(items)} 景")
        for item in items[:5]:
            props = item["properties"]
            platform = props.get("platform", "?")
            slc_note = " [SLC-off]" if (
                platform == "landsat-7" and
                props.get("datetime", "")[:10] >= "2003-05-31"
            ) else ""
            print(
                f"      {props.get('datetime','?')[:10]}  "
                f"云量={props.get('eo:cloud_cover','?')}%  "
                f"{platform}{slc_note}  {item['id']}"
            )
        if len(items) > 5:
            print(f"      ... 共 {len(items)} 景")

        return items

    def _download_band(self, href: str, save_path: Path, max_retries: int = 3):
        """下载单个波段文件（带 SAS 签名 + 多线程分块 + 波段级重试）"""
        for attempt in range(max_retries):
            try:
                signed_url = self._sign_url(href)
                download_with_chunks(requests, signed_url, save_path, desc=save_path.name, timeout=300)
                return
            except Exception as e:
                # 清理可能的残留文件
                for f in (save_path, save_path.with_suffix(save_path.suffix + ".part")):
                    f.unlink(missing_ok=True)
                if attempt < max_retries - 1:
                    # 429（签名限速耗尽重试）用更长退避
                    is_429 = "429" in str(e)
                    wait = (120 * (2 ** attempt) if is_429
                            else 10 * (2 ** attempt)) + random.uniform(0, 5)
                    print(f"      [波段重试 {attempt+1}/{max_retries}] {save_path.name} {wait:.0f}s后重试: {e}")
                    time.sleep(wait)
                else:
                    raise

    def _bands_for_item(self, item: Dict) -> List[str]:
        """根据平台选择对应的 L2 波段列表，只返回该 item 实际存在的资产"""
        platform = item.get("properties", {}).get("platform", "")
        if platform == "landsat-7":
            band_list = _BANDS_L7
        else:
            band_list = _BANDS_L89_L2
        assets = item.get("assets", {})
        return [b for b in band_list if b in assets]

    def _usgs_login(self) -> "requests.Session":
        """USGS ERS cookie 认证，返回可直接下载 LandsatLook 数据的 session"""
        import re as _re
        username = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        if not username or not password:
            raise RuntimeError(
                "缺少 USGS ERS 账号，请在 credentials.yaml 的 usgs 条目中填写 username/password\n"
                "注册地址：https://ers.cr.usgs.gov/register"
            )
        session = requests.Session()
        r = session.get(_USGS_ERS_LOGIN, timeout=15)
        r.raise_for_status()
        csrf_m = _re.search(r'name="csrf"[^>]*value="([^"]+)"', r.text)
        if not csrf_m:
            raise RuntimeError("无法从 USGS ERS 登录页获取 CSRF token")
        r2 = session.post(_USGS_ERS_LOGIN, data={
            "username": username,
            "password": password,
            "csrf": csrf_m.group(1),
        }, timeout=15, allow_redirects=True)
        if r2.url.rstrip("/").endswith("/login"):
            raise RuntimeError(
                "USGS ERS 登录失败（账号或密码错误），请检查 credentials.yaml 中的 usgs 条目"
            )
        return session

    def _download_l1_supplement(
        self, scene_id: str, scene_dir: Path, platform: str
    ) -> List[Path]:
        """
        对 Landsat 8/9 景，额外从 USGS LandsatLook L1 下载 pan/cirrus/lwir12。
        使用 L2 scene_id 中的 path/row/date 信息匹配对应的 L1 景。
        """
        import re as _re

        # 仅对 L8/L9 补充；Landsat 7 不走此逻辑
        if platform not in ("landsat-8", "landsat-9"):
            return []

        # 检查是否已全部存在
        wanted = _BANDS_L89_L1_SUPPLEMENT
        existing = [p for b in wanted
                    if (p := _band_file_exists(scene_dir, scene_id, b)) is not None]
        if len(existing) == len(wanted):
            return existing

        # 从 L2 scene_id 提取 path/row/date（用于搜索对应的 L1 景）
        # L2 格式：LC08_L2SP_170073_20251013_02_T1
        # L1 格式：LC08_L1TP_170073_20251013_20251118_02_T1（处理日期不同）
        m = _re.match(r"LC\d+_[A-Z0-9]+_(\d{3})(\d{3})_(\d{4})(\d{2})(\d{2})_", scene_id)
        if not m:
            print(f"      [L1补充] 无法解析 scene_id: {scene_id}")
            return []
        path_str, row_str, yr, mo, dy = m.groups()
        date_str = f"{yr}-{mo}-{dy}"
        # 用 WRS-2 path/row + 日期查询 L1
        try:
            resp = requests.post(
                _USGS_STAC_SEARCH,
                json={
                    "collections": ["landsat-c2l1"],
                    "datetime": f"{date_str}T00:00:00Z/{date_str}T23:59:59Z",
                    "query": {
                        "landsat:wrs_path": {"eq": path_str},
                        "landsat:wrs_row":  {"eq": row_str},
                    },
                    "limit": 1,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"      [L1补充] USGS STAC 查询失败: {e}")
            return []

        features = resp.json().get("features", [])
        if not features:
            print(f"      [L1补充] 未找到 L1 景 (path={path_str} row={row_str} date={date_str})")
            return []

        l1_item = features[0]
        l1_assets = l1_item.get("assets", {})
        print(f"      [L1补充] 找到 L1 景: {l1_item['id']}")

        # 登录 USGS ERS
        try:
            usgs_session = self._usgs_login()
        except RuntimeError as e:
            print(f"      [L1补充] {e}")
            return []

        downloaded = list(existing)
        for band_name in wanted:
            out_path = scene_dir / f"{scene_id}_{band_name}.TIF"
            found = _band_file_exists(scene_dir, scene_id, band_name)
            if found is not None:
                downloaded.append(found)
                continue
            asset = l1_assets.get(band_name, {})
            href = asset.get("href", "")
            if not href:
                print(f"      [L1补充] 无下载链接: {band_name}")
                continue
            try:
                _download_single(usgs_session, href, out_path,
                                 desc=f"L1/{band_name}", timeout=300)
                downloaded.append(out_path)
                print(f"      [L1补充] 下载完成: {band_name}")
            except Exception as e:
                print(f"      [L1补充] 下载失败 {band_name}: {e}")

        return downloaded

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """下载 Landsat 场景的核心波段（GeoTIFF 格式）"""
        self._check_deps()
        downloaded = []

        for item in search_results[:max_items]:
            scene_id = item["id"]
            scene_dir = save_dir / scene_id
            scene_dir.mkdir(parents=True, exist_ok=True)

            assets = item.get("assets", {})
            bands_to_dl = self._bands_for_item(item)
            platform = item.get("properties", {}).get("platform", "?")

            # SLC-off 提示
            acquired = item.get("properties", {}).get("datetime", "")[:10]
            if platform == "landsat-7" and acquired >= "2003-05-31":
                print(f"    [SLC-off] {scene_id} 拍摄于 {acquired}，"
                      f"影像含条带空洞（正常现象，2003年5月后所有Landsat 7均如此）")

            # 检查是否已全部下载
            existing = [p for b in bands_to_dl
                        if (p := _band_file_exists(scene_dir, scene_id, b)) is not None]
            if len(existing) == len(bands_to_dl):
                print(f"    已存在，跳过: {scene_id}")
                downloaded.extend(existing)
                continue

            print(f"    下载: {scene_id}  平台={platform}  ({len(bands_to_dl)} 个波段)")
            scene_files = []
            failed_bands = []  # (band_name, href, out_path)
            for band_name in bands_to_dl:
                href = assets[band_name].get("href", "")
                if not href:
                    continue
                ext = Path(href.split("?")[0]).suffix or ".TIF"
                out_path = scene_dir / f"{scene_id}_{band_name}{ext}"
                found = _band_file_exists(scene_dir, scene_id, band_name, ext)
                if found is not None:
                    scene_files.append(found)
                    continue
                try:
                    self._download_band(href, out_path)
                    scene_files.append(out_path)
                    time.sleep(random.uniform(1, 3))   # 波段间签名请求冷却，避免429
                except Exception as e:
                    print(f"      [错误] 波段 {band_name}: {e}")
                    failed_bands.append((band_name, href, out_path))

            # 失败波段二次补救：等待后统一重试一轮
            if failed_bands:
                print(f"    [补救] {len(failed_bands)} 个波段失败，30s后重试...")
                time.sleep(30)
                for band_name, href, out_path in failed_bands:
                    try:
                        self._download_band(href, out_path)
                        scene_files.append(out_path)
                        print(f"      [补救成功] {band_name}")
                    except Exception as e:
                        print(f"      [最终失败] 波段 {band_name}: {e}")

            if scene_files:
                downloaded.extend(scene_files)
                print(f"    [完成] {scene_id}  下载了 {len(scene_files)} 个波段")
            else:
                print(f"    [警告] {scene_id}  无文件下载成功")

            # 对 Landsat 8/9 额外从 L1 补充 pan/cirrus/lwir12
            if platform in ("landsat-8", "landsat-9"):
                l1_files = self._download_l1_supplement(scene_id, scene_dir, platform)
                downloaded.extend(f for f in l1_files if f not in downloaded)

        return downloaded
