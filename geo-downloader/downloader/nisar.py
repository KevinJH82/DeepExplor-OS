"""
NISAR L-band SAR Downloader — ASF DAAC / NASA Earthdata
使用 asf_search 官方库搜索和下载。

传感器：NISAR（NASA-ISRO Synthetic Aperture Radar）
卫星：NISAR（NASA + ISRO 联合研制，2024年3月发射）
波段：L波段（1.25 GHz，波长 ~24cm）+ S波段（2.86GHz，印方提供）
分辨率：
  精细模式（Fine）：3-10m
  标准扫描模式（ScanSAR）：~25m
极化：全极化 HH/HV/VH/VV 或双极化
幅宽：240km（远超同类SAR，单次过境覆盖极宽）
重访周期：12天（全球覆盖）

数据产品（Level）：
  L1 RSLC  — 单视复数影像（斜距，含相位信息，适合InSAR）
  L2 GSLC  — 地理编码单视复数（大地坐标，推荐，可直接裁剪）
  L2 GCOV  — 地理编码协方差矩阵（极化分析，岩性分类）
  L2 GUNW  — InSAR 展开相位图（地表形变）
  L3       — 位移时间序列（累积形变速率）

文件格式：HDF5（.h5）

地质应用价值：
  · L波段穿透茂密植被和浅层干燥土壤，揭示地下地质构造
  · 全极化数据支持岩性填图和矿化带识别
  · InSAR 差分干涉精度达毫米级，适用于矿区沉降/断层监测
  · 与 Sentinel-1（C波段）和 ALOS-2（L波段）构成 SAR 互补体系

收费情况：完全免费（NASA 开放数据政策）
注册：NASA Earthdata 账号（与 Sentinel-1/ALOS 共用）
  https://urs.earthdata.nasa.gov/

安装：pip install asf_search earthaccess h5py
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any

try:
    import asf_search as asf
    HAS_ASF = True
except ImportError:
    HAS_ASF = False

from .base import BaseDownloader


class NISARDownloader(BaseDownloader):

    PLATFORM_NAME = "nisar"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)

    def _check_deps(self):
        if not HAS_ASF:
            raise ImportError("缺少依赖: asf_search\n请运行: pip install asf_search")

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,        # SAR 不受云量影响
        processing_level: str = "GSLC",  # 推荐 GSLC（地理编码，可直接裁剪）
        **kwargs,
    ) -> List[Any]:
        """
        通过 asf_search 搜索 NISAR L-band SAR 产品。

        Parameters
        ----------
        bbox             : (min_lon, min_lat, max_lon, max_lat)
        start_date       : 'YYYY-MM-DD'
        end_date         : 'YYYY-MM-DD'
        processing_level : 'GSLC'（地理编码单视复数，推荐）
                           'RSLC'（单视复数，斜距，适合 InSAR）
                           'GCOV'（地理编码协方差，极化分析）
                           'GUNW'（InSAR 展开相位，形变图）
        """
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        aoi_wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        results = asf.search(
            platform=[asf.PLATFORM.NISAR],
            processingLevel=[processing_level],
            intersectsWith=aoi_wkt,
            start=f"{start_date}T00:00:00Z",
            end=f"{end_date}T23:59:59Z",
            maxResults=100,
        )

        print(f"    找到 {len(results)} 景 NISAR L-band {processing_level}")
        for r in list(results)[:5]:
            p = r.properties
            print(
                f"      {p.get('startTime','?')[:10]}  "
                f"轨道={p.get('pathNumber','?')}  "
                f"极化={p.get('polarization','?')}  "
                f"{p.get('sceneName','?')}"
            )
        if len(results) > 5:
            print(f"      ... 共 {len(results)} 景")

        return list(results)

    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 5,
        **kwargs,
    ) -> List[Path]:
        """
        使用 asf_search 下载 NISAR 产品（HDF5 .h5 格式）。

        注意：NISAR 产品为 HDF5 格式，与 GeoTIFF 不同。
        如需裁剪，可用 h5py 读取后转换，或使用 ISCE3 工具链处理。
        """
        self._check_deps()

        username = self.credentials["username"]
        password = self.credentials["password"]

        # 优先 earthaccess JWT token，回退到 cookie 认证
        try:
            import os
            import earthaccess
            os.environ["EARTHDATA_USERNAME"] = username
            os.environ["EARTHDATA_PASSWORD"] = password
            earthaccess.login(strategy="environment")
            token_dict = earthaccess.get_edl_token()
            token = token_dict.get("access_token", "")
            session = asf.ASFSession().auth_with_token(token)
        except Exception:
            session = asf.ASFSession().auth_with_creds(username, password)

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景 NISAR（HDF5 格式）...")

        # 清除未完成的残片
        for item in to_download:
            fname = item.properties.get("fileName", "")
            part = save_dir / (fname + ".part")
            if part.exists():
                part.unlink()
                print(f"    [清除残片] {part.name}")

        try:
            asf.ASFSearchResults(to_download).download(
                path=str(save_dir),
                session=session,
                processes=1,
            )
        except Exception as e:
            print(f"    [错误] 下载过程中出错: {e}")

        # 收集已下载文件（.h5 格式）
        downloaded = []
        for item in to_download:
            fname = item.properties.get("fileName", "")
            fpath = save_dir / fname
            if fpath.exists():
                downloaded.append(fpath)
                print(f"    [完成] {fname}")
            else:
                scene_id = item.properties.get("sceneName", "")
                # NISAR 文件可能带有 .h5 后缀
                matches = (
                    list(save_dir.glob(f"{scene_id}*.h5"))
                    + list(save_dir.glob(f"{scene_id}*"))
                )
                if matches:
                    downloaded.extend(matches[:1])
                    print(f"    [完成] {matches[0].name}")

        return downloaded

    def run(
        self,
        bbox,
        geometry,
        area_name: str,
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        max_items: int = 5,
        clip: bool = True,
        processing_level: str = "GSLC",
        **kwargs,
    ) -> List[Path]:
        """
        完整流程：搜索 → 下载（HDF5）

        注意：NISAR HDF5 产品不能直接用 rasterio 裁剪。
        GSLC/GCOV 产品内嵌地理坐标，可用 h5py + ISCE3 提取为 GeoTIFF 后再裁剪。
        当前版本直接返回原始 HDF5 文件，裁剪步骤跳过（输出提示）。
        """
        save_dir = self.get_save_dir(area_name)

        print(f"\n[nisar] 搜索影像...")
        print(f"  区域: {area_name} | 范围: {bbox}")
        print(f"  时间: {start_date} ~ {end_date}")
        print(f"  产品级别: {processing_level}")

        results = self.search(
            bbox, start_date, end_date, cloud_cover,
            processing_level=processing_level, **kwargs
        )
        if not results:
            print(f"  [!] 未找到符合条件的影像")
            return []

        print(f"  找到 {len(results)} 景，开始下载（最多 {max_items} 景）...")
        downloaded = self.download(results, save_dir, max_items, **kwargs)

        if not downloaded:
            return []

        if clip and geometry is not None:
            print(
                "  [提示] NISAR 产品为 HDF5 格式，暂不支持自动裁剪。\n"
                "  如需裁剪，请使用 ISCE3（https://github.com/isce-framework/isce3）\n"
                "  或 h5py 读取后手动转换为 GeoTIFF，再调用 clip_to_geometry()。"
            )

        return downloaded
