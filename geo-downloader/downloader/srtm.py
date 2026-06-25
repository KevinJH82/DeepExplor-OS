"""
SRTM DEM Downloader — AWS S3 公开数据集
直接从 AWS elevation-tiles-prod bucket 下载，无需账号。

数据来源：NASA SRTM（航天飞机雷达地形测绘任务，2000年）
格式：HGT（SRTM原始格式），自动转换为GeoTIFF并合并
分辨率：1弧秒（约30m）
覆盖范围：60°S ~ 60°N

S3 URL格式：
  s3://elevation-tiles-prod/skadi/{LAT}/{LAT}{LON}.hgt.gz
  例如：N22E121 → s3://elevation-tiles-prod/skadi/N22/N22E121.hgt.gz

无需安装额外库（仅用 requests）
"""

import gzip
import math
import struct
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Any

try:
    import requests
    from tqdm import tqdm
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume


_AWS_SRTM_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
_TILE_SIZE = 3601       # SRTM1: 3601×3601 samples per 1°×1° tile
_NODATA = -32768


class SRTMDownloader(BaseDownloader):

    PLATFORM_NAME = "srtm"
    REQUIRES_AUTH = False

    def __init__(
        self,
        credentials: Optional[Dict[str, str]] = None,
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials or {}, output_dir=output_dir)

    def _check_deps(self):
        if not HAS_DEPS:
            raise ImportError("缺少依赖: requests, tqdm\n请运行: pip install requests tqdm")

    def _bbox_to_tiles(self, bbox: Tuple[float, float, float, float]) -> List[str]:
        """
        将 bbox 转换为覆盖所有需要的 SRTM 瓦片名称列表。
        瓦片以左下角坐标命名，如 N22E121。
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        tiles = []
        for lat in range(math.floor(min_lat), math.ceil(max_lat)):
            for lon in range(math.floor(min_lon), math.ceil(max_lon)):
                lat_tag = f"N{lat:02d}" if lat >= 0 else f"S{abs(lat):02d}"
                lon_tag = f"E{lon:03d}" if lon >= 0 else f"W{abs(lon):03d}"
                tiles.append(f"{lat_tag}{lon_tag}")
        return tiles

    def _tile_url(self, tile_name: str) -> str:
        """构造瓦片下载URL，例如 N22E121 → .../skadi/N22/N22E121.hgt.gz"""
        lat_dir = tile_name[:3]   # e.g. N22
        return f"{_AWS_SRTM_BASE}/{lat_dir}/{tile_name}.hgt.gz"

    def _download_tile(self, tile_name: str, save_dir: Path) -> Optional[Path]:
        """下载并解压单个 SRTM 瓦片，返回 .hgt 文件路径。"""
        hgt_path = save_dir / f"{tile_name}.hgt"
        if hgt_path.exists():
            return hgt_path

        url = self._tile_url(tile_name)

        # AWS 公开 S3 无需代理，创建不带代理的 session 避免 SOCKS 错误
        session = requests.Session()
        session.trust_env = False   # 不读取系统环境变量中的代理设置

        # 先探测是否存在（404 = 海洋区域）
        head = session.head(url, timeout=30)
        if head.status_code == 404:
            print(f"      [跳过] {tile_name}（无数据，可能是海洋区域）")
            return None
        head.raise_for_status()

        gz_path = save_dir / f"{tile_name}.hgt.gz"
        download_with_resume(session, url, gz_path, desc=tile_name, timeout=60)

        # 解压 .gz
        with gzip.open(gz_path, "rb") as gz_f:
            with open(hgt_path, "wb") as hgt_f:
                hgt_f.write(gz_f.read())
        gz_path.unlink()

        return hgt_path

    def _hgt_to_geotiff(self, hgt_path: Path) -> Path:
        """
        将 .hgt 文件转换为 GeoTIFF。
        使用 rasterio（若已安装），否则保留 .hgt 格式。
        """
        tif_path = hgt_path.with_suffix(".tif")
        if tif_path.exists():
            hgt_path.unlink(missing_ok=True)
            return tif_path

        try:
            import rasterio
            from rasterio.transform import from_bounds
            from rasterio.crs import CRS

            # 从文件名解析左下角坐标
            name = hgt_path.stem   # e.g. N22E121
            lat_sign = 1 if name[0] == 'N' else -1
            lon_sign = 1 if name[3] == 'E' else -1
            lat = lat_sign * int(name[1:3])
            lon = lon_sign * int(name[4:7])

            # 读取 HGT 二进制（big-endian int16）
            data = []
            with open(hgt_path, "rb") as f:
                raw = f.read()
            n = _TILE_SIZE
            for i in range(n * n):
                val = struct.unpack(">h", raw[i*2:i*2+2])[0]
                data.append(val)

            import numpy as np
            arr = np.array(data, dtype=np.int16).reshape(n, n)

            transform = from_bounds(lon, lat, lon + 1, lat + 1, n, n)
            with rasterio.open(
                tif_path, "w",
                driver="GTiff",
                height=n, width=n,
                count=1, dtype="int16",
                crs=CRS.from_epsg(4326),
                transform=transform,
                nodata=_NODATA,
                compress="deflate",
            ) as dst:
                dst.write(arr, 1)

            hgt_path.unlink()
            return tif_path

        except ImportError:
            # rasterio 未安装，保留 .hgt
            return hgt_path

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str = "",
        end_date: str = "",
        cloud_cover: int = 100,
        **kwargs,
    ) -> List[Any]:
        """
        SRTM 不需要搜索，直接根据 bbox 计算瓦片列表。
        返回瓦片名称列表作为"搜索结果"。
        """
        self._check_deps()
        tiles = self._bbox_to_tiles(bbox)
        print(f"    SRTM 覆盖瓦片: {tiles}")
        return tiles

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 100,
        **kwargs,
    ) -> List[Path]:
        """下载 SRTM 瓦片并转换为 GeoTIFF。"""
        self._check_deps()

        downloaded = []
        for tile_name in search_results:
            try:
                hgt_path = self._download_tile(tile_name, save_dir)
                if hgt_path is None:
                    continue
                tif_path = self._hgt_to_geotiff(hgt_path)
                downloaded.append(tif_path)
                print(f"    [完成] {tif_path.name}")
            except Exception as e:
                print(f"    [错误] {tile_name}: {e}")

        return downloaded
