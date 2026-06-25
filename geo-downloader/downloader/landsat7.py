"""
Landsat 7 ETM+ Downloader — Microsoft Planetary Computer STAC
LandsatDownloader 的子类，专门搜索 Landsat 7 数据。

传感器：Landsat 7 ETM+（Enhanced Thematic Mapper Plus）
        NASA / USGS，发射于 1999年4月15日
服务期：1999年至今（2022年4月正式退役）
覆盖范围：全球，16天重访周期
分辨率：
  VNIR/SWIR（B1-B5, B7）：30m
  全色（B8）：15m（用于 PAN 融合提升分辨率）
  热红外（B6）：60m（L2 产品中已重采样至 30m）

波段列表（L2C2）：
  B1 blue    450-520nm  30m  （蓝，区分水体/土壤）
  B2 green   520-600nm  30m  （绿，植被活力）
  B3 red     630-690nm  30m  （红，叶绿素吸收）
  B4 nir08   770-900nm  30m  （近红外，植被/水体边界）
  B5 swir16  1550-1750nm 30m （SWIR，铁染/氧化蚀变）★地质关键
  B6 tir     10400-12500nm 60m→30m（热红外，地表温度）
  B7 swir22  2080-2350nm 30m （SWIR，黏土/碳酸盐矿物）★地质关键
  B8 pan     520-900nm  15m  （全色，最高空间分辨率）★PAN融合

⚠️  SLC-off 问题（2003年5月31日后）：
    ETM+ 的扫描线校正器（SLC）于 2003年5月31日发生故障，
    此后所有影像约22%区域呈条带状空洞（wedge-shaped gaps）。
    处理方案：
    1. 直接使用（大部分区域完好，空洞规律可预测）
    2. ENVI/GDAL 填补（用多期数据插值填洞）
    3. 与 Landsat 8 数据融合（时相配准后覆盖空洞）
    本下载器会在 SLC-off 影像旁打印提示，不阻止下载。

地质应用价值：
  · B8（15m 全色）是 Landsat 系列中最高分辨率免费数据之一
  · B5/B7 SWIR 波段对铁氧化物、黏土矿化蚀变有良好响应
  · 1999-2003年 SLC-on 数据质量完整，可用于历史时相对比

数据来源：Microsoft Planetary Computer（landsat-c2-l2 collection）
无需账号，完全免费。
"""

from typing import List, Tuple, Dict, Optional
from pathlib import Path

from .landsat import LandsatDownloader


class Landsat7Downloader(LandsatDownloader):
    """
    Landsat 7 ETM+ 专用下载器。

    继承 LandsatDownloader，在搜索时自动限定 platform=landsat-7，
    其余下载逻辑完全复用（波段自动切换为 _BANDS_L7）。
    """

    PLATFORM_NAME = "landsat7"
    REQUIRES_AUTH = False

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_results: int = 50,
        slc_off: bool = True,   # True=包含SLC-off数据，False=仅1999-2003年完整数据
        **kwargs,
    ) -> List[Dict]:
        """
        搜索 Landsat 7 ETM+ 影像。

        Parameters
        ----------
        slc_off : 是否包含 SLC-off 数据（2003年5月31日后）
                  True  = 包含（默认，数据量更多但有条带空洞）
                  False = 仅搜索 1999-04-15 ~ 2003-05-30 完整数据

        Notes
        -----
        · Landsat 7 在 Planetary Computer 中与 L8/L9 共用 landsat-c2-l2 collection
        · 通过 platform="landsat-7" 过滤器隔离 L7 数据
        """
        # SLC-off 限制
        _start = start_date
        _end = end_date
        if not slc_off:
            _end = min(end_date, "2003-05-30")
            if _end < _start:
                print(f"    [提示] SLC-off=False 时时间范围限制为 1999-04-15~2003-05-30")
                print(f"    指定的起始日期 {_start} 晚于 SLC-on 结束日期，无有效数据")
                return []
            print(f"    [SLC-on模式] 只搜索 {_start} ~ {_end} 的完整影像")

        print(f"    搜索 Landsat 7 ETM+  {'（含SLC-off）' if slc_off else '（仅SLC-on）'}")

        # 调用父类 search，传入 platforms 限制
        results = super().search(
            bbox=bbox,
            start_date=_start,
            end_date=_end,
            cloud_cover=cloud_cover,
            max_results=max_results,
            platforms=["landsat-7"],
            **kwargs,
        )

        # 统计 SLC-off 比例
        slcoff_count = sum(
            1 for item in results
            if item.get("properties", {}).get("datetime", "")[:10] >= "2003-05-31"
        )
        if slcoff_count > 0:
            print(f"    [注意] 其中 {slcoff_count} 景为 SLC-off（含条带空洞，属正常情况）")

        return results
