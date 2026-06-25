"""
Landsat TIRS Thermal Downloader — Microsoft Planetary Computer STAC
专门下载 Landsat 8/9 热红外（TIRS）相关波段及地表温度辅助产品。

数据来源：Microsoft Planetary Computer（landsat-c2-l2 collection）
传感器：Landsat 8 TIRS（2013年至今）/ Landsat 9 TIRS-2（2021年至今）
分辨率：100m（TIRS原始）→ 重采样至 30m（L2产品中）
波段：
  lwir11  (B10)  10.60-11.19μm   主热红外通道，用于地表温度反演
  lwir12  (B11)  11.50-12.51μm   次热红外通道（L9 TIRS-2噪声更低）
辅助产品（ST系列）：
  st_b10  地表温度 Band 10（DN，已大气校正，单位 K×10000）
  st_atran 大气透射率
  st_cdist 云距离（像素到最近云的距离，km）
  st_drad  下行长波辐射（W/m²·sr·μm）
  st_emis  地表比辐射率（ASTER GED 辅助）
  st_emsd  比辐射率标准差
  st_trad  上行辐射（W/m²·sr·μm）
  st_urad  上行辐射（W/m²·sr·μm）
  qa_pixel 云/水/雪掩膜（QA位标志）

地质应用价值：
  · 识别地热异常、热液蚀变带、火山活动区
  · 矿坑废石堆氧化反应产生的温度异常
  · 城市热岛效应排查（矿区选址）
  · 与 ECOSTRESS（70m，日内多次）互补，提供长时序热特征

无需账号，完全免费。
"""

from pathlib import Path
from typing import List, Tuple, Dict, Optional

try:
    import requests
    from tqdm import tqdm
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .base import BaseDownloader, download_with_resume as _download_single
from .landsat import LandsatDownloader

# 热红外+地表温度辅助波段（ST系列）
_BANDS_TIRS = [
    "lwir11",    # B10 热红外主通道 10.6-11.2μm（100m→30m）
    "lwir12",    # B11 热红外次通道 11.5-12.5μm（L9 TIRS-2）
    "st_b10",    # 地表温度产品（大气校正，DN→K 需乘0.00341802+149）
    "st_atran",  # 大气透射率（0-1）
    "st_cdist",  # 云距离（km）
    "st_drad",   # 下行辐射
    "st_emis",   # 地表比辐射率（ASTER GED）
    "st_emsd",   # 比辐射率标准差
    "st_trad",   # 上行热辐射
    "st_urad",   # 大气上行辐射
    "qa_pixel",  # 云/水/雪 QA 掩膜
]


class LandsatTIRSDownloader(LandsatDownloader):
    """
    Landsat 8/9 热红外（TIRS）专用下载器。

    继承 LandsatDownloader，只下载热红外和地表温度辅助波段，
    忽略光学波段，节省存储空间。
    """

    PLATFORM_NAME = "landsat_tirs"
    REQUIRES_AUTH = False

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 30,
        max_results: int = 50,
        **kwargs,
    ) -> List[Dict]:
        """搜索 Landsat 8/9 影像（只返回含 TIRS 波段的 L8/L9，不含 L7）"""
        print(f"    搜索 Landsat 8/9 TIRS 热红外数据...")
        results = super().search(
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
            cloud_cover=cloud_cover,
            max_results=max_results,
            platforms=["landsat-8", "landsat-9"],
            **kwargs,
        )
        return results

    def _bands_for_item(self, item: Dict) -> List[str]:
        """只返回该 item 实际存在的 TIRS/ST 波段"""
        assets = item.get("assets", {})
        return [b for b in _BANDS_TIRS if b in assets]

    def _download_band(self, href: str, save_path: Path):
        """下载单个波段（SAS 签名 + 单线程续传）"""
        signed_url = self._sign_url(href)
        _download_single(requests, signed_url, save_path, desc=save_path.name, timeout=300)
