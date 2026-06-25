"""
Sentinel-1 SAR Downloader — ASF DAAC
使用 asf_search 官方库搜索和下载。

注册（免费，与NASA Earthdata共用账号）：
  https://urs.earthdata.nasa.gov/

安装：pip install asf_search pyroSAR

产品：Sentinel-1 GRD（地距产品，适合地质形变监测）
波段：C波段（5.6cm），VV+VH双极化
分辨率：IW模式 5×20m（处理后约10m）

地理编码：下载后自动调用 pyroSAR + SNAP 做 Range-Doppler Terrain Correction，
          输出带 WGS84 CRS 的 GeoTIFF，方可裁剪。
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import asf_search as asf
    HAS_ASF = True
except ImportError:
    HAS_ASF = False

try:
    from pyroSAR.snap.util import geocode as pyrosar_geocode
    HAS_PYROSAR = True
except Exception:
    pyrosar_geocode = None
    HAS_PYROSAR = False

from .base import BaseDownloader, download_with_resume


def _geocode_s1(zip_path: Path, out_dir: Path, spacing: float = 10.0) -> List[Path]:
    """
    用 pyroSAR 对单个 Sentinel-1 GRD ZIP 做地理编码（Range-Doppler TC）。

    Parameters
    ----------
    zip_path : 下载的 .zip 文件路径
    out_dir  : 地理编码结果输出目录
    spacing  : 输出像素间距（米），默认 10m

    Returns
    -------
    生成的 GeoTIFF 文件列表
    """
    if not HAS_PYROSAR:
        raise ImportError(
            "缺少依赖: pyroSAR\n"
            "请运行: pip install pyroSAR\n"
            "同时需要安装 ESA SNAP（https://step.esa.int/main/download/snap-download/）"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"      [地理编码] {zip_path.name} → {out_dir.name}/")

    pyrosar_geocode(
        infile=str(zip_path),
        outdir=str(out_dir),
        tr=spacing,            # pyroSAR ≥0.10 改名 spacing→tr
        t_srs=4326,           # 输出 WGS84
        terrainFlattening=False,
        cleanup=True,         # 删除中间临时文件
    )

    # 收集输出的 GeoTIFF
    results = list(out_dir.glob("*.tif")) + list(out_dir.glob("*.tiff"))
    if results:
        print(f"      [地理编码完成] 生成 {len(results)} 个文件")
    else:
        print(f"      [警告] 地理编码完成但未找到输出文件，请检查 SNAP 配置")
    return results


class Sentinel1Downloader(BaseDownloader):

    PLATFORM_NAME = "sentinel1"
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
            raise ImportError(
                "缺少依赖: asf_search\n请运行: pip install asf_search"
            )

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,   # SAR不受云量影响
        beam_mode: str = "IW",    # 干涉宽幅模式
        polarization: str = "VV+VH",
        **kwargs,
    ) -> List[Any]:
        """
        通过asf_search搜索Sentinel-1 GRD产品。
        """
        self._check_deps()

        min_lon, min_lat, max_lon, max_lat = bbox
        # asf_search需要WKT格式的AOI
        aoi_wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        _asf_session = asf.ASFSession()

        results = asf.search(
            platform=[asf.PLATFORM.SENTINEL1],
            processingLevel=[asf.PRODUCT_TYPE.GRD_HD],
            beamMode=[beam_mode],
            intersectsWith=aoi_wkt,
            start=f"{start_date}T00:00:00Z",
            end=f"{end_date}T23:59:59Z",
            maxResults=100,
            opts=asf.ASFSearchOptions(session=_asf_session),
        )

        # 附加 _footprint（从 asf_search 结果的 geometry）供覆盖选景使用
        try:
            from shapely.geometry import shape as _shape
            for r in results:
                try:
                    geom = r.geojson().get("geometry")
                    if geom:
                        r._footprint = _shape(geom)
                except Exception:
                    pass
        except ImportError:
            pass

        print(f"    找到 {len(results)} 景 Sentinel-1 GRD")
        for r in list(results)[:5]:
            p = r.properties
            print(
                f"      {p.get('startTime','?')[:10]}  "
                f"轨道={p.get('pathNumber','?')}  "
                f"极化={p.get('polarization','?')}"
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
        使用 asf_search 认证 + download_with_resume 下载 Sentinel-1 产品（ZIP格式）。
        支持断点续传和自动重试，ZIP损坏时自动重下（最多2次）。
        """
        self._check_deps()

        username = self.credentials["username"]
        password = self.credentials["password"]
        static_token = self.credentials.get("token")

        # 认证优先级：
        #   1. credentials.yaml 中手动填写的 nasa_earthdata.token（长期有效）
        #   2. earthaccess 动态获取 EDL Bearer Token（推荐，绕过密码认证限制）
        #   不再 fallback 到 auth_with_creds：NASA 已限制旧式密码认证
        import asf_search
        session = asf_search.ASFSession()
        if static_token:
            session.auth_with_token(static_token)
        else:
            import os
            import earthaccess
            os.environ["EARTHDATA_USERNAME"] = username
            os.environ["EARTHDATA_PASSWORD"] = password
            try:
                earthaccess.login(strategy="environment")
            except Exception as e:
                raise RuntimeError(
                    f"[sentinel1] earthaccess 登录失败: {e}\n"
                    "  请检查 credentials.yaml 中的 nasa_earthdata username/password，\n"
                    "  或在 nasa_earthdata.token 填入长期 token：\n"
                    "  https://urs.earthdata.nasa.gov/user_tokens"
                ) from e
            token_dict = earthaccess.get_edl_token()
            # get_edl_token 可能返回 list（多个 token）或 dict
            if isinstance(token_dict, list):
                token_dict = token_dict[0] if token_dict else {}
            token = token_dict.get("access_token", "") if isinstance(token_dict, dict) else ""
            if not token:
                raise RuntimeError(
                    "[sentinel1] earthaccess 未返回有效 token，无法认证。\n"
                    "  请到 https://urs.earthdata.nasa.gov/user_tokens 手动生成 token，\n"
                    "  填入 credentials.yaml 的 nasa_earthdata.token 字段。"
                )
            session.auth_with_token(token)

        to_download = search_results[:max_items]
        print(f"    正在下载 {len(to_download)} 景...")

        import zipfile
        downloaded = []
        for item in to_download:
            fname = item.properties.get("fileName", "")
            url   = item.properties.get("url", "")
            if not fname or not url:
                continue
            fpath = save_dir / fname

            # 最多尝试 2 次（首次 + 1 次重下）
            for attempt in range(2):
                try:
                    download_with_resume(session, url, fpath, desc=fname, timeout=600, proxies=None)
                except Exception as e:
                    print(f"    [错误] 下载失败 {fname}: {e}")
                    break

                # 验证 ZIP 完整性
                if not fpath.exists():
                    print(f"    [错误] 下载后文件不存在: {fname}")
                    break
                try:
                    with zipfile.ZipFile(fpath, 'r') as zf:
                        bad = zf.testzip()
                    if bad:
                        raise zipfile.BadZipFile(f"损坏文件: {bad}")
                    # ZIP 完整
                    downloaded.append(fpath)
                    print(f"    [完成] {fname}")
                    break
                except zipfile.BadZipFile as e:
                    if attempt == 0:
                        print(f"    [警告] ZIP 校验失败，删除并重新下载: {fname} ({e})")
                        fpath.unlink(missing_ok=True)
                        # 同时清除 .part 残片
                        part = fpath.with_suffix(fpath.suffix + ".part")
                        part.unlink(missing_ok=True)
                        # 继续 attempt=1 重下
                    else:
                        print(f"    [错误] ZIP 重下后仍损坏，跳过: {fname}")
                        fpath.unlink(missing_ok=True)
                except Exception as e:
                    print(f"    [警告] ZIP 验证失败，跳过: {fname}: {e}")
                    break

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
        geocode_spacing: float = 10.0,
        **kwargs,
    ) -> List[Path]:
        """
        完整流程：搜索 → 下载 → 地理编码（pyroSAR+SNAP TC）→ 裁剪
        """
        save_dir = self.get_save_dir(area_name)

        print(f"\n[sentinel1] 搜索影像...")
        print(f"  区域: {area_name} | 范围: {bbox}")
        print(f"  时间: {start_date} ~ {end_date}")

        results = self.search(bbox, start_date, end_date, cloud_cover, **kwargs)
        if not results:
            print(f"  [!] 未找到符合条件的影像")
            return []

        # ── 覆盖选景 ──────────────────────────────────────────────
        if geometry is not None and len(results) > 1:
            try:
                from postprocess.mosaic import select_covering_scenes
                selected = select_covering_scenes(results, geometry, max_scenes=max_items * 3)
                if selected:
                    max_items = max(max_items, len(selected))
                    results = selected
            except Exception as e:
                print(f"  [覆盖选景] 跳过（{e}），使用原始搜索结果")

        print(f"  找到 {len(results)} 景，开始下载（最多 {max_items} 景）...")
        downloaded = self.download(results, save_dir, max_items, **kwargs)

        if not downloaded:
            return []

        # 地理编码：对每个 ZIP 调用 pyroSAR geocode
        if not HAS_PYROSAR:
            print(
                "  [警告] 未安装 pyroSAR，跳过地理编码。\n"
                "  原始 GRD 产品无 CRS，无法裁剪。\n"
                "  请运行: pip install pyroSAR  并安装 ESA SNAP"
            )
            return downloaded

        geocoded_files: List[Path] = []
        tc_dir = save_dir / "terrain_corrected"
        for zip_path in downloaded:
            if zip_path.suffix.lower() != ".zip":
                geocoded_files.append(zip_path)
                continue
            try:
                tifs = _geocode_s1(zip_path, tc_dir, spacing=geocode_spacing)
                geocoded_files.extend(tifs)
            except Exception as e:
                print(f"  [警告] 地理编码失败 {zip_path.name}: {e}")
                print(f"  [跳过] 原始 GRD 无 CRS，无法裁剪，保留 ZIP: {zip_path.name}")
                # 不加入 geocoded_files，避免无 CRS 的原始文件进入裁剪流程

        if not geocoded_files:
            print("  [信息] 所有场景地理编码失败，原始 ZIP 已保留在下载目录")
            return []

        # 裁剪
        if clip and geometry is not None:
            from postprocess.clip import clip_to_geometry
            print(f"  裁剪影像到KML范围...")
            clipped = []
            for f in geocoded_files:
                try:
                    out = clip_to_geometry(f, geometry)
                    clipped.append(out)
                except Exception as e:
                    print(f"  [警告] 裁剪失败 {f.name}: {e}")
                    clipped.append(f)
            return clipped

        return geocoded_files
