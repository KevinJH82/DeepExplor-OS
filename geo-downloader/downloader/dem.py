"""
DEM Downloader — Copernicus DEM GLO-30 / GLO-10
全球数字高程模型。

数据来源：
  GLO-30（30m）：AWS S3 公开桶，HTTP 直接下载，无需任何配置
  GLO-10（10m）：AWS S3 私有桶（copernicus-dem-10m），需要 AWS 账号授权
                 申请地址：https://registry.opendata.aws/copernicus-dem/
                 配置方式：在 credentials.yaml 的 aws 节填写 access_key_id / secret_access_key

瓦片命名规则（两者相同）：
  Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif  （GLO-30）
  Copernicus_DSM_COG_03_N{lat:02d}_00_E{lon:03d}_00_DEM.tif  （GLO-10，COG_03 表示 1/3 弧秒≈10m）

分辨率选择（通过 credentials.yaml 中 task.dem_resolution 设置）：
  dem_resolution: 30   →  GLO-30，30m，无需账号（默认）
  dem_resolution: 10   →  GLO-10，10m，需配置 aws.access_key_id / aws.secret_access_key
"""

import math
from pathlib import Path
from typing import List, Tuple, Any, Optional, Dict

try:
    import requests
    from tqdm import tqdm
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


# GLO-30：HTTP 公开端点
_GLO30_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{lat_tag}_00_{lon_tag}_00_DEM/"
    "Copernicus_DSM_COG_10_{lat_tag}_00_{lon_tag}_00_DEM.tif"
)

# GLO-10：S3 需认证访问
_GLO10_BUCKET = "copernicus-dem-10m"
_GLO10_KEY_TPL = (
    "Copernicus_DSM_COG_03_{lat_tag}_00_{lon_tag}_00_DEM/"
    "Copernicus_DSM_COG_03_{lat_tag}_00_{lon_tag}_00_DEM.tif"
)


def _tile_tags(lat: int, lon: int) -> Tuple[str, str]:
    """
    将整数纬度/经度转换为瓦片名称中的标签。
    例：lat=35, lon=116 → ('N35', 'E116')
        lat=-5, lon=-70  → ('S05', 'W070')
    """
    lat_tag = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
    lon_tag = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
    return lat_tag, lon_tag


def _tiles_for_bbox(bbox: Tuple[float, float, float, float]) -> List[Tuple[int, int]]:
    """
    根据BBox计算需要下载的1°×1°瓦片列表。
    返回 [(lat_floor, lon_floor), ...]
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    tiles = []
    for lat in range(math.floor(min_lat), math.ceil(max_lat)):
        for lon in range(math.floor(min_lon), math.ceil(max_lon)):
            tiles.append((lat, lon))
    return tiles


def _download_glo10_tile(lat_tag: str, lon_tag: str, save_path: Path,
                         access_key: str, secret_key: str) -> bool:
    """
    用 boto3 认证访问下载 GLO-10 单瓦片。
    返回 True 表示成功，False 表示该瓦片无数据（海洋/空白区）。
    """
    try:
        import boto3
    except ImportError:
        raise ImportError("GLO-10 需要 boto3\n请运行: pip install boto3")

    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    key = _GLO10_KEY_TPL.format(lat_tag=lat_tag, lon_tag=lon_tag)

    try:
        s3.head_object(Bucket=_GLO10_BUCKET, Key=key)
    except Exception as e:
        err = str(e)
        if "404" in err or "NoSuchKey" in err or "Not Found" in err:
            return False
        if "403" in err or "AccessDenied" in err:
            raise RuntimeError(
                "AWS 账号无权访问 GLO-10（copernicus-dem-10m 桶）\n"
                "请在 https://registry.opendata.aws/copernicus-dem/ 申请访问权限，\n"
                "并在 credentials.yaml 的 aws 节填写 access_key_id / secret_access_key"
            )
        raise

    save_path.parent.mkdir(parents=True, exist_ok=True)
    part = save_path.with_suffix(save_path.suffix + ".part")
    try:
        s3.download_file(_GLO10_BUCKET, key, str(part))
        part.rename(save_path)
        return True
    except Exception as e:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"GLO-10 下载失败 ({lat_tag} {lon_tag}): {e}")


class DEMDownloader(BaseDownloader):

    PLATFORM_NAME = "dem"
    REQUIRES_AUTH = False

    def __init__(
        self,
        output_dir: str = "./downloads",
        dem_resolution: int = 30,
        credentials: Optional[Dict] = None,
        **kwargs,
    ):
        super().__init__(credentials=credentials or {}, output_dir=output_dir)
        self.dem_resolution = dem_resolution if dem_resolution in (10, 30) else 30

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str = None,
        end_date: str = None,
        cloud_cover: int = 100,
        **kwargs,
    ) -> List[dict]:
        """
        DEM不依赖时间/云量，直接按BBox计算瓦片列表。
        返回列表中每项为 {'lat': int, 'lon': int, ...}
        """
        tiles = _tiles_for_bbox(bbox)
        results = []
        for lat, lon in tiles:
            lat_tag, lon_tag = _tile_tags(lat, lon)
            url = _GLO30_URL.format(lat_tag=lat_tag, lon_tag=lon_tag)
            results.append({
                "lat": lat, "lon": lon,
                "url": url,
                "lat_tag": lat_tag, "lon_tag": lon_tag,
            })
        return results

    def download(
        self,
        search_results: List[dict],
        save_dir: Path,
        max_items: int = 100,
        **kwargs,
    ) -> List[Path]:
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests, tqdm\n请运行: pip install requests tqdm")

        res = self.dem_resolution
        print(f"    DEM 分辨率: GLO-{res} ({res}m)")

        downloaded = []
        for item in search_results[:max_items]:
            lat_tag = item["lat_tag"]
            lon_tag = item["lon_tag"]
            prefix = f"CopDEM_{res}m"
            filename = f"{prefix}_{lat_tag}_{lon_tag}.tif"
            save_path = save_dir / filename

            if save_path.exists():
                print(f"    已存在，跳过: {filename}")
                downloaded.append(save_path)
                continue

            print(f"    下载瓦片: {filename}")

            if res == 10:
                # GLO-10：需要 AWS 认证
                access_key = self.credentials.get("access_key_id", "")
                secret_key = self.credentials.get("secret_access_key", "")
                if not access_key or not secret_key:
                    print(f"    [错误] GLO-10 需要 AWS 账号，请在 credentials.yaml 的 aws 节填写：")
                    print(f"           access_key_id / secret_access_key")
                    print(f"    [回退] 改用 GLO-30 下载...")
                    self._download_glo30(item, save_dir, downloaded)
                    continue
                try:
                    ok = _download_glo10_tile(lat_tag, lon_tag, save_path, access_key, secret_key)
                    if ok:
                        downloaded.append(save_path)
                        print(f"    [完成] {filename}")
                    else:
                        print(f"    [跳过] 无数据区域: {lat_tag} {lon_tag}")
                except RuntimeError as e:
                    print(f"    [错误] {e}")
                    print(f"    [回退] 改用 GLO-30 下载...")
                    self._download_glo30(item, save_dir, downloaded)
            else:
                # GLO-30：HTTP 直接下载
                self._download_glo30(item, save_dir, downloaded)

        return downloaded

    def _download_glo30(self, item: dict, save_dir: Path, downloaded: list):
        """下载单个 GLO-30 瓦片（HTTP），绕过系统代理直连 AWS S3"""
        lat_tag = item["lat_tag"]
        lon_tag = item["lon_tag"]
        filename = f"CopDEM_30m_{lat_tag}_{lon_tag}.tif"
        save_path = save_dir / filename

        if save_path.exists():
            downloaded.append(save_path)
            return

        # AWS 公开 S3 无需代理
        session = requests.Session()
        session.trust_env = False

        try:
            head = session.head(item["url"], timeout=30, allow_redirects=True)
            if head.status_code == 404:
                print(f"    [跳过] 无数据区域: {lat_tag} {lon_tag}")
                return
            head.raise_for_status()
            download_with_resume(
                session, item["url"], save_path,
                desc=filename, timeout=60,
            )
            downloaded.append(save_path)
            print(f"    [完成] {filename}")
        except requests.RequestException as e:
            print(f"    [错误] 下载失败 {filename}: {e}")
