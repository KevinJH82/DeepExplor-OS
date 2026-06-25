"""
GEE Downloader — Google Earth Engine Python API
通过服务账号认证，搜索并下载 GEE 数据目录中的影像。

支持数据集：
  GEE Sentinel-2  COPERNICUS/S2_SR_HARMONIZED (10m L2A 地表反射率)
  GEE Landsat 8/9 LANDSAT/LC08/C02/T1_L2 + LANDSAT/LC09/C02/T1_L2 (30m L2)
  GEE MODIS       MODIS/061/MOD09GA (500m 日地表反射率)
  GEE 自定义       用户指定 Collection ID + 波段列表

认证方式：服务账号 JSON Key
  在 credentials.yaml 中配置：
    google_earth_engine:
      service_account_email: xxx@your-project.iam.gserviceaccount.com
      service_account_key_path: /path/to/service-account-key.json

安装：pip install earthengine-api

下载策略：
  小区域 (<= 20MB 估算) → getDownloadUrl() 即时下载，无需等待
  大区域 (> 20MB 估算)  → 自动分块（N×N 瓦片）+ rasterio 合并
"""

import math
import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import ee
    HAS_EE = True
except ImportError:
    HAS_EE = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .base import BaseDownloader


class GEEDownloader(BaseDownloader):
    """
    GEE 下载器基类。
    子类通过覆盖 _COLLECTION_ID、_DEFAULT_BANDS、_CLOUD_PROP、_SCALE_METERS 来定制。
    """

    PLATFORM_NAME: str = "gee"
    REQUIRES_AUTH: bool = True

    # 子类覆盖这四个属性
    _COLLECTION_ID: str = ""
    _DEFAULT_BANDS: List[str] = []
    _CLOUD_PROP: str = "CLOUDY_PIXEL_PERCENTAGE"   # 空字符串表示不过滤云量
    _SCALE_METERS: int = 30

    # getDownloadUrl 单次下载估算上限（MB），超过此值自动分块
    _MAX_DIRECT_MB: float = 20.0

    def __init__(
        self,
        credentials: Dict[str, str] = None,
        output_dir: str = "./downloads",
        collection_id: str = "",
        bands: Optional[List[str]] = None,
        scale_meters: int = 0,
        **kwargs,
    ):
        super().__init__(credentials=credentials or {}, output_dir=output_dir)
        self._ee_initialized = False
        # 允许实例级覆盖（供 GEECustomDownloader 使用）
        self._use_collection = collection_id or self._COLLECTION_ID
        self._use_bands = bands if bands is not None else list(self._DEFAULT_BANDS)
        self._use_scale = scale_meters if scale_meters > 0 else self._SCALE_METERS

    # ──────────────────────────────────────────────────────────────
    # 认证
    # ──────────────────────────────────────────────────────────────

    def _authenticate(self):
        """延迟初始化 ee，整个进程只做一次。"""
        if self._ee_initialized:
            return
        if not HAS_EE:
            raise ImportError(
                "缺少依赖: earthengine-api\n请运行: pip install earthengine-api"
            )
        email = self.credentials.get("service_account_email", "").strip()
        key_path = self.credentials.get("service_account_key_path", "").strip()
        if not email or not key_path:
            raise RuntimeError(
                "GEE 需要服务账号凭据，请在 credentials.yaml 中配置：\n"
                "  google_earth_engine:\n"
                "    service_account_email: xxx@your-project.iam.gserviceaccount.com\n"
                "    service_account_key_path: /path/to/service-account-key.json\n"
                "申请方式：Google Cloud Console 创建服务账号 → 下载 JSON 密钥 "
                "→ 在 GEE 控制台注册该服务账号"
            )
        if not Path(key_path).exists():
            raise RuntimeError(
                f"GEE 服务账号 JSON 密钥文件不存在: {key_path}\n"
                "请确认文件路径正确，且使用服务器上的绝对路径。"
            )
        cred = ee.ServiceAccountCredentials(email, key_path)
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Unable to initialize deprecated assets')
            ee.Initialize(credentials=cred)
        self._ee_initialized = True

    # ──────────────────────────────────────────────────────────────
    # 搜索
    # ──────────────────────────────────────────────────────────────

    def _search_collection(
        self,
        collection_id: str,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int,
        max_results: int,
    ) -> List[Dict]:
        """对单个 Collection 执行搜索，返回字典列表。"""
        min_lon, min_lat, max_lon, max_lat = bbox
        roi = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])

        col = (
            ee.ImageCollection(collection_id)
            .filterBounds(roi)
            .filterDate(start_date, end_date)
        )
        if self._CLOUD_PROP:
            col = col.filter(ee.Filter.lt(self._CLOUD_PROP, cloud_cover))
            col = col.sort(self._CLOUD_PROP)
        else:
            col = col.sort("system:time_start")

        col_info = col.limit(max_results).getInfo()
        features = col_info.get("features", []) if col_info else []

        results = []
        for feat in features:
            props = feat.get("properties", {})
            image_id = feat.get("id", "")
            date_ms = props.get("system:time_start", 0)
            date_str = (
                time.strftime("%Y-%m-%d", time.gmtime(date_ms / 1000))
                if date_ms else "unknown"
            )
            cloud = float(props.get(self._CLOUD_PROP, 0)) if self._CLOUD_PROP else 0.0
            results.append({
                "image_id": image_id,
                "date": date_str,
                "cloud_cover": cloud,
                "collection_id": collection_id,
                "bands": list(self._use_bands),
                "bbox": bbox,
                "scale_meters": self._use_scale,
            })
        return results

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_results: int = 50,
        **kwargs,
    ) -> List[Dict]:
        self._authenticate()
        results = self._search_collection(
            self._use_collection, bbox, start_date, end_date, cloud_cover, max_results
        )
        print(f"    GEE [{self._use_collection}] 找到 {len(results)} 景")
        for r in results[:5]:
            cloud_str = f"  云量={r['cloud_cover']:.1f}%" if self._CLOUD_PROP else ""
            print(f"      {r['date']}{cloud_str}  {r['image_id'].split('/')[-1]}")
        if len(results) > 5:
            print(f"      ... 共 {len(results)} 景")
        return results

    # ──────────────────────────────────────────────────────────────
    # 下载
    # ──────────────────────────────────────────────────────────────

    def _estimate_size_mb(
        self,
        bbox: Tuple[float, float, float, float],
        scale_m: int,
        n_bands: int,
    ) -> float:
        """粗略估算下载大小（MB），用于选择下载策略。"""
        min_lon, min_lat, max_lon, max_lat = bbox
        lat_mid = (min_lat + max_lat) / 2
        width_m  = (max_lon - min_lon) * 111000 * math.cos(math.radians(lat_mid))
        height_m = (max_lat - min_lat) * 111000
        pixels = (width_m / max(scale_m, 1)) * (height_m / max(scale_m, 1))
        return pixels * n_bands * 2 / 1024 / 1024  # uint16 = 2 bytes/pixel

    def _download_direct(
        self,
        image_id: str,
        bbox: Tuple[float, float, float, float],
        bands: List[str],
        scale_m: int,
        out_path: Path,
    ) -> Optional[Path]:
        """调用 getDownloadUrl() 下载单景影像到 out_path。"""
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests\n请运行: pip install requests")

        min_lon, min_lat, max_lon, max_lat = bbox
        image = ee.Image(image_id)

        params = {
            "bands": bands,
            "region": ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat]),
            "scale": scale_m,
            "format": "GEO_TIFF",
            "crs": "EPSG:4326",
        }

        # GEE 限流时指数退避重试
        url = None
        for attempt in range(3):
            try:
                url = image.getDownloadUrl(params)
                break
            except Exception as e:
                err_str = str(e).lower()
                if "quota" in err_str or "429" in err_str or "too many" in err_str:
                    wait = 30 * (2 ** attempt)
                    print(f"      [限流] GEE 配额限制，{wait}s 后重试...")
                    time.sleep(wait)
                elif "too large" in err_str or "limit" in err_str:
                    print(f"      [错误] 区域过大，无法直接下载: {e}")
                    return None
                else:
                    print(f"      [错误] 获取下载 URL 失败: {e}")
                    return None

        if not url:
            return None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = out_path.with_suffix(out_path.suffix + ".part")
        print(f"      下载: {out_path.name}")

        import random
        max_dl_retries = 3
        for dl_attempt in range(max_dl_retries):
            try:
                resp = _requests.get(url, stream=True, timeout=(30, 300))
                resp.raise_for_status()
                with open(part_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                part_path.rename(out_path)
                size_mb = out_path.stat().st_size / 1024 / 1024
                print(f"      完成: {out_path.name}  ({size_mb:.1f} MB)")
                return out_path
            except Exception as e:
                err_str = str(e).lower()
                is_net = any(k in err_str for k in ("ssl", "proxy", "timeout", "connection", "eof"))
                if is_net and dl_attempt < max_dl_retries - 1:
                    wait = 2 ** dl_attempt * 5 + random.uniform(0, 3)
                    print(f"      [重试 {dl_attempt + 1}/{max_dl_retries}] 网络错误，{wait:.0f}s 后重连... ({e})")
                    part_path.unlink(missing_ok=True)
                    time.sleep(wait)
                else:
                    part_path.unlink(missing_ok=True)
                    print(f"      [错误] 下载失败 {out_path.name}: {e}")
                    return None

    def _download_tiled(
        self,
        image_id: str,
        bbox: Tuple[float, float, float, float],
        bands: List[str],
        scale_m: int,
        out_path: Path,
        est_mb: float,
    ) -> Optional[Path]:
        """大区域分块下载，每块调用 _download_direct()，最后用 rasterio 合并。"""
        n_tiles = max(2, math.ceil(math.sqrt(est_mb / 15)))
        min_lon, min_lat, max_lon, max_lat = bbox
        lon_step = (max_lon - min_lon) / n_tiles
        lat_step = (max_lat - min_lat) / n_tiles
        # 5% 重叠，避免合并时出现缝隙
        overlap_lon = lon_step * 0.05
        overlap_lat = lat_step * 0.05

        print(f"      [分块] 估算 {est_mb:.0f}MB，切分为 {n_tiles}×{n_tiles} 块下载...")

        tile_paths = []
        for i in range(n_tiles):
            for j in range(n_tiles):
                tile_bbox = (
                    max(min_lon, min_lon + i * lon_step - overlap_lon),
                    max(min_lat, min_lat + j * lat_step - overlap_lat),
                    min(max_lon, min_lon + (i + 1) * lon_step + overlap_lon),
                    min(max_lat, min_lat + (j + 1) * lat_step + overlap_lat),
                )
                tile_out = out_path.with_suffix(f".tile_{i}_{j}.tif")
                result = self._download_direct(image_id, tile_bbox, bands, scale_m, tile_out)
                if result:
                    tile_paths.append(result)

        if not tile_paths:
            print("      [错误] 所有分块下载失败")
            return None

        # 合并瓦片
        try:
            import rasterio
            from rasterio.merge import merge as rio_merge

            src_files = [rasterio.open(p) for p in tile_paths]
            try:
                # 指定 res 以第一块分辨率为基准，避免各块因 GEE 内部取整
                # 略有差异导致合并后尺寸偏小
                ref_res = src_files[0].res
                mosaic, transform = rio_merge(src_files, method="first", res=ref_res)
                meta = src_files[0].meta.copy()
                _predictor = 3 if mosaic.dtype.kind == "f" else 2
                meta.update({
                    "driver": "GTiff",
                    "height": mosaic.shape[1],
                    "width":  mosaic.shape[2],
                    "transform": transform,
                    "compress": "deflate",
                    "predictor": _predictor,
                })
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with rasterio.open(out_path, "w", **meta) as dst:
                    dst.write(mosaic)
            finally:
                for src in src_files:
                    src.close()
            for p in tile_paths:
                p.unlink(missing_ok=True)
            print(f"      [完成] 合并 {len(tile_paths)} 块 → {out_path.name}")
            return out_path
        except ImportError:
            print("      [警告] 未安装 rasterio，无法合并分块，返回第一块")
            return tile_paths[0]
        except Exception as e:
            print(f"      [错误] 合并分块失败: {e}")
            return tile_paths[0] if tile_paths else None

    def _download_one_image(
        self,
        image_dict: Dict,
        save_dir: Path,
    ) -> Optional[Path]:
        """下载单景影像，自动选择直接下载或分块下载。"""
        image_id   = image_dict["image_id"]
        date_str   = image_dict.get("date", "unknown")
        bbox       = image_dict["bbox"]
        bands      = image_dict.get("bands") or self._use_bands
        scale_m    = image_dict.get("scale_meters") or self._use_scale

        # 文件命名：collection简称_日期_imageid末段.tif
        collection_short = self._use_collection.replace("/", "_")
        id_tail = image_id.split("/")[-1]
        fname = f"{collection_short}_{date_str}_{id_tail}.tif"
        out_path = save_dir / fname

        if out_path.exists():
            print(f"      已存在，跳过: {fname}")
            return out_path

        est_mb = self._estimate_size_mb(bbox, scale_m, len(bands) or 1)
        if est_mb <= self._MAX_DIRECT_MB:
            return self._download_direct(image_id, bbox, bands, scale_m, out_path)
        else:
            return self._download_tiled(image_id, bbox, bands, scale_m, out_path, est_mb)

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        if not HAS_EE:
            raise ImportError(
                "缺少依赖: earthengine-api\n请运行: pip install earthengine-api"
            )
        self._authenticate()
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        downloaded = []
        for item in search_results[:max_items]:
            result = self._download_one_image(item, save_dir)
            if result:
                downloaded.append(result)

        return downloaded


# ══════════════════════════════════════════════════════════════════
# 具体子类
# ══════════════════════════════════════════════════════════════════

class GEESentinel2Downloader(GEEDownloader):
    """
    GEE Sentinel-2 L2A 地表反射率
    Collection: COPERNICUS/S2_SR_HARMONIZED
    分辨率: 10m
    波段: B2(蓝) B3(绿) B4(红) B8(NIR) B11(SWIR1) B12(SWIR2)
    """
    PLATFORM_NAME   = "gee_sentinel2"
    _COLLECTION_ID  = "COPERNICUS/S2_SR_HARMONIZED"
    _DEFAULT_BANDS  = ["B2", "B3", "B4", "B8", "B11", "B12"]
    _CLOUD_PROP     = "CLOUDY_PIXEL_PERCENTAGE"
    _SCALE_METERS   = 10


class GEELandsatDownloader(GEEDownloader):
    """
    GEE Landsat 8 + 9 L2 地表反射率（合并搜索）
    Collections: LANDSAT/LC08/C02/T1_L2, LANDSAT/LC09/C02/T1_L2
    分辨率: 30m
    波段: SR_B2(蓝) SR_B3(绿) SR_B4(红) SR_B5(NIR) SR_B6(SWIR1) SR_B7(SWIR2)
    """
    PLATFORM_NAME   = "gee_landsat"
    _COLLECTION_ID  = "LANDSAT/LC08/C02/T1_L2"   # 仅用作默认，实际同时搜索 L8+L9
    _DEFAULT_BANDS  = ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]
    _CLOUD_PROP     = "CLOUD_COVER"
    _SCALE_METERS   = 30

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_results: int = 50,
        **kwargs,
    ) -> List[Dict]:
        self._authenticate()
        # 同时搜索 Landsat 8 和 Landsat 9，按日期合并排序
        results_l8 = self._search_collection(
            "LANDSAT/LC08/C02/T1_L2", bbox, start_date, end_date, cloud_cover, max_results
        )
        results_l9 = self._search_collection(
            "LANDSAT/LC09/C02/T1_L2", bbox, start_date, end_date, cloud_cover, max_results
        )
        combined = sorted(results_l8 + results_l9, key=lambda x: x["date"])
        print(f"    GEE [Landsat 8+9] 找到 {len(combined)} 景"
              f"（L8: {len(results_l8)}，L9: {len(results_l9)}）")
        for r in combined[:5]:
            sat = "L8" if "LC08" in r["image_id"] else "L9"
            print(f"      {r['date']}  云量={r['cloud_cover']:.1f}%  [{sat}] {r['image_id'].split('/')[-1]}")
        if len(combined) > 5:
            print(f"      ... 共 {len(combined)} 景")
        return combined


class GEEMODISDownloader(GEEDownloader):
    """
    GEE MODIS 日地表反射率 MOD09GA
    Collection: MODIS/061/MOD09GA
    分辨率: 500m
    波段: sur_refl_b01 ~ sur_refl_b07
    注：MOD09GA 无标准云量属性，不过滤云量，建议下载后自行用 state_1km 波段做云掩膜
    """
    PLATFORM_NAME   = "gee_modis"
    _COLLECTION_ID  = "MODIS/061/MOD09GA"
    _DEFAULT_BANDS  = [
        "sur_refl_b01", "sur_refl_b02", "sur_refl_b03",
        "sur_refl_b04", "sur_refl_b05", "sur_refl_b06", "sur_refl_b07",
    ]
    _CLOUD_PROP     = ""    # 不过滤云量
    _SCALE_METERS   = 500


class GEECustomDownloader(GEEDownloader):
    """
    GEE 自定义数据集下载器
    用户指定 Collection ID、波段列表和下载分辨率。

    示例：
      dl = GEECustomDownloader(
          credentials=creds,
          collection_id="LANDSAT/LE07/C02/T1_L2",
          bands=["SR_B3", "SR_B4"],
          scale_meters=30,
      )
    """
    PLATFORM_NAME   = "gee_custom"
    _COLLECTION_ID  = ""
    _DEFAULT_BANDS  = []
    _CLOUD_PROP     = ""
    _SCALE_METERS   = 30

    def __init__(
        self,
        credentials: Dict[str, str] = None,
        output_dir: str = "./downloads",
        collection_id: str = "",
        bands: Optional[List[str]] = None,
        scale_meters: int = 30,
        **kwargs,
    ):
        if not collection_id:
            raise ValueError(
                "GEECustomDownloader 必须指定 collection_id，例如：\n"
                "  GEECustomDownloader(credentials=creds, "
                "collection_id='LANDSAT/LE07/C02/T1_L2', "
                "bands=['SR_B3','SR_B4'])"
            )
        super().__init__(
            credentials=credentials,
            output_dir=output_dir,
            collection_id=collection_id,
            bands=bands or [],
            scale_meters=scale_meters or 30,
        )
