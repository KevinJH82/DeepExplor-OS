#!/Users/mac/geo-env/bin/python
"""
geo-downloader — 卫星图像自动下载工具
面向地质勘探的多平台遥感数据批量下载CLI

用法示例：
  # 使用配置文件中的默认参数（推荐）
  python3 main.py

  # 命令行参数优先级高于配置文件
  python3 main.py --kml area.kml --sensor sentinel2 --start 2024-01-01 --end 2024-06-30
"""

import sys
import argparse
import importlib
import logging
import traceback
from pathlib import Path
from datetime import datetime

# 确保项目根目录在sys.path中，支持从任意位置运行
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from downloader.kml_parser import parse_kml, parse_kml_folder, KMLParseError
from downloader.credentials import load_credentials, get_platform_creds, CredentialsError
from downloader.base import _emit_progress_event


# 传感器名称 → (下载器类, 所需平台凭据key)
SENSOR_MAP = {
    "sentinel2":  ("downloader.sentinel2",  "Sentinel2Downloader",  "copernicus"),
    "sentinel1":  ("downloader.sentinel1",  "Sentinel1Downloader",  "nasa_earthdata"),
    "landsat":    ("downloader.landsat",    "LandsatDownloader",    "usgs"),
    "landsat7":   ("downloader.landsat7",   "Landsat7Downloader",   None),
    "emit":       ("downloader.emit",       "EMITDownloader",       "nasa_earthdata"),
    "dem":        ("downloader.dem",        "DEMDownloader",        None),
    "srtm":       ("downloader.srtm",       "SRTMDownloader",       None),
    "aster":      ("downloader.aster",      "ASTERDownloader",      "nasa_earthdata"),
    "aster_l1t":  ("downloader.aster",      "ASTERL1TDownloader",   "nasa_earthdata"),
    "modis":      ("downloader.modis",      "MODISDownloader",      "nasa_earthdata"),
    "alos":       ("downloader.alos",       "ALOSDownloader",       "nasa_earthdata"),
    "alos2":      ("downloader.alos2",      "ALOS2Downloader",      "nasa_earthdata"),
    "gedi":       ("downloader.gedi",       "GEDIDownloader",       "nasa_earthdata"),
    "opera":      ("downloader.opera",      "OPERADownloader",      "nasa_earthdata"),
    "ecostress":  ("downloader.ecostress",  "ECOSTRESSDownloader",  "nasa_earthdata"),
    "enmap":      ("downloader.enmap",      "EnMAPDownloader",      "dlr_eoweb"),
    # ── 第一批新增传感器 ──────────────────────────────────────────
    "hyperion":   ("downloader.hyperion",   "HyperionDownloader",   "nasa_earthdata"),
    "aviris":     ("downloader.aviris",     "AVIRISDownloader",     "nasa_earthdata"),
    "planet":     ("downloader.planet",     "PlanetDownloader",     "planet"),
    # ── 第二批：高光谱 ────────────────────────────────────────────
    "prisma":     ("downloader.prisma",     "PRISMADownloader",     "prisma"),
    "desis":      ("downloader.desis",      "DESISDownloader",      "nasa_earthdata"),
    "zy1":        ("downloader.zy1",        "ZY1Downloader",        None),
    # ── 第二批：高分辨率 ──────────────────────────────────────────
    "spot67":     ("downloader.oneatlas",   "OneAtlasDownloader",   "oneatlas"),
    "pleiades":   ("downloader.oneatlas",   "OneAtlasDownloader",   "oneatlas"),
    "wv2":        ("downloader.worldview",  "WorldViewDownloader",  "worldview"),
    "wv3":        ("downloader.worldview",  "WorldViewDownloader",  "worldview"),
    # ── 第三批：高精度SAR + 热红外 ────────────────────────────────
    "landsat_tirs": ("downloader.landsat_tirs", "LandsatTIRSDownloader", None),
    # ── 第四批：新一代SAR ─────────────────────────────────────────
    "nisar":        ("downloader.nisar",        "NISARDownloader",       "nasa_earthdata"),
    # ── GEE 数据源（Google Earth Engine）────────────────────────
    "gee_sentinel2": ("downloader.gee", "GEESentinel2Downloader", "google_earth_engine"),
    "gee_landsat":   ("downloader.gee", "GEELandsatDownloader",   "google_earth_engine"),
    "gee_modis":     ("downloader.gee", "GEEMODISDownloader",     "google_earth_engine"),
    "gee_custom":    ("downloader.gee", "GEECustomDownloader",    "google_earth_engine"),
}

SENSOR_DESCRIPTIONS = {
    "sentinel2":  "Sentinel-2 光学多光谱（ESA，10-60m，需Copernicus账号）",
    "sentinel1":  "Sentinel-1 SAR雷达（ESA/ASF，5-20m，需NASA Earthdata账号）",
    "landsat":    "Landsat 8/9 光学多光谱（Planetary Computer，30m，无需账号）",
    "landsat7":   "Landsat 7 ETM+ 光学多光谱（Planetary Computer，15-30m，无需账号，含全色B8）",
    "emit":       "EMIT 高光谱（NASA，60m，285波段，需NASA Earthdata账号）",
    "dem":        "Copernicus DEM GLO-30 高程（ESA，30m，无需账号）",
    "srtm":       "SRTM DEM 高程（NASA，30m，AWS直接下载，无需账号）",
    "aster":      "ASTER L2 多光谱+热红外 AST_07/08/09T（NASA，15-90m，需NASA Earthdata账号）",
    "aster_l1t":  "ASTER L1T 精准地形校正辐亮度 VNIR+TIR（NASA，15/90m，需NASA Earthdata账号）",
    "modis":      "MODIS 地表反射率/植被指数（NASA，250-500m，需NASA Earthdata账号）",
    "alos":       "ALOS PALSAR L波段SAR（JAXA/ASF，2006-2011年档案，需NASA Earthdata账号）",
    "alos2":      "ALOS-2 PALSAR-2 L波段SAR（JAXA/ASF，2014年至今，1-100m，需NASA Earthdata账号）",
    "gedi":       "GEDI 激光测高（NASA/ISS，25m footprint，需NASA Earthdata账号）",
    "opera":      "OPERA RTC-S1 辐射地形校正SAR（NASA/JPL，30m，需NASA Earthdata账号）",
    "ecostress":  "ECOSTRESS 地表温度（NASA/ISS，70m，需NASA Earthdata账号）",
    "enmap":      "EnMAP L2A 高光谱（DLR，244波段 420-2450nm，30m，需EOWEB账号）",
    # ── 第一批新增传感器 ──────────────────────────────────────────
    "hyperion":   "Hyperion EO-1 高光谱存档（NASA，30m，242波段，2001-2017年，需NASA Earthdata账号）",
    "aviris":     "AVIRIS-NG 机载高光谱（NASA JPL，~5m，432波段，需NASA Earthdata账号）",
    "planet":     "PlanetScope 高分辨率光学（Planet Labs，3-5m，4/8波段，需Planet API Key）",
    # ── 第二批：高光谱 ────────────────────────────────────────────
    "prisma":     "PRISMA L2D 高光谱（ASI，30m，239波段 400-2500nm，需ASI PRISMA账号）",
    "desis":      "DESIS L2A 高光谱（DLR/NASA，30m，235波段 400-1000nm，需NASA Earthdata账号）",
    "zy1":        "资源一号02D AHSI 高光谱（CRESDA，30m，166波段，需手动申请数据）",
    # ── 第二批：高分辨率 ──────────────────────────────────────────
    "spot67":     "SPOT 6/7 高分辨率光学（Airbus，1.5m全色/6m多光谱，需OneAtlas商业账号）",
    "pleiades":   "Pleiades 1A/1B 高分辨率光学（Airbus，0.5m全色/2m多光谱，需OneAtlas商业账号）",
    "wv2":        "WorldView-2 高分辨率光学（Maxar，0.46m全色/1.85m多光谱，需Maxar商业账号）",
    "wv3":        "WorldView-3 高分辨率光学（Maxar，0.31m全色/1.24m多光谱，需Maxar商业账号）",
    # ── 第三批：高精度SAR + 热红外 ────────────────────────────────
    "landsat_tirs": "Landsat 8/9 TIRS 热红外+地表温度（Planetary Computer，30m，含ST辅助波段，无需账号）",
    # ── 第四批：新一代SAR ─────────────────────────────────────────
    "nisar":        "NISAR L波段SAR（NASA/ISRO，3-25m，全极化，240km幅宽，2024年至今，需NASA Earthdata账号）",
    # ── GEE 数据源（Google Earth Engine）────────────────────────
    "gee_sentinel2": "GEE Sentinel-2 L2A 地表反射率（Google Earth Engine，10m，COPERNICUS/S2_SR_HARMONIZED，需GEE服务账号）",
    "gee_landsat":   "GEE Landsat 8/9 L2 地表反射率（Google Earth Engine，30m，L8+L9合并，需GEE服务账号）",
    "gee_modis":     "GEE MODIS 日地表反射率（Google Earth Engine，500m，MOD09GA 7波段，需GEE服务账号）",
    "gee_custom":    "GEE 自定义数据集（Google Earth Engine，用户指定 Collection ID + 波段，需GEE服务账号）",
}

# 各传感器下载路径的网络可达性。requires_vpn=False 表示数据源国内可直连，
# 无需 VPN；True 表示需走 VPN（NASA Earthdata / DLR / Google / 商业卫星）。
# source 为简短数据源标签，与 SENSOR_DESCRIPTIONS 的来源描述保持一致。
# 注意：键集合必须与 SENSOR_MAP 一致，新增传感器时需同步维护。
SENSOR_NETWORK = {
    # ── 免 VPN（国内直连）──────────────────────────────────────────
    "sentinel2":     {"requires_vpn": False, "source": "ESA Copernicus 直连"},
    "dem":           {"requires_vpn": False, "source": "ESA Copernicus 直连"},
    "srtm":          {"requires_vpn": False, "source": "AWS S3 直连"},
    "zy1":           {"requires_vpn": False, "source": "CRESDA 国内"},
    "landsat":       {"requires_vpn": False, "source": "Planetary Computer"},
    "landsat7":      {"requires_vpn": False, "source": "Planetary Computer"},
    "landsat_tirs":  {"requires_vpn": False, "source": "Planetary Computer"},
    "prisma":        {"requires_vpn": False, "source": "ASI 意大利"},
    # ── 需 VPN：NASA Earthdata ────────────────────────────────────
    "sentinel1":     {"requires_vpn": True,  "source": "NASA Earthdata"},
    "emit":          {"requires_vpn": True,  "source": "NASA Earthdata"},
    "aster":         {"requires_vpn": True,  "source": "NASA Earthdata"},
    "aster_l1t":     {"requires_vpn": True,  "source": "NASA Earthdata"},
    "modis":         {"requires_vpn": True,  "source": "NASA Earthdata"},
    "alos":          {"requires_vpn": True,  "source": "NASA Earthdata"},
    "alos2":         {"requires_vpn": True,  "source": "NASA Earthdata"},
    "gedi":          {"requires_vpn": True,  "source": "NASA Earthdata"},
    "opera":         {"requires_vpn": True,  "source": "NASA Earthdata"},
    "ecostress":     {"requires_vpn": True,  "source": "NASA Earthdata"},
    "hyperion":      {"requires_vpn": True,  "source": "NASA Earthdata"},
    "aviris":        {"requires_vpn": True,  "source": "NASA Earthdata"},
    "desis":         {"requires_vpn": True,  "source": "NASA Earthdata"},
    "nisar":         {"requires_vpn": True,  "source": "NASA Earthdata"},
    # ── 需 VPN：DLR EOWEB ─────────────────────────────────────────
    "enmap":         {"requires_vpn": True,  "source": "DLR EOWEB"},
    # ── 需 VPN：商业卫星 ──────────────────────────────────────────
    "planet":        {"requires_vpn": True,  "source": "Planet Labs 商业"},
    "spot67":        {"requires_vpn": True,  "source": "Airbus 商业"},
    "pleiades":      {"requires_vpn": True,  "source": "Airbus 商业"},
    "wv2":           {"requires_vpn": True,  "source": "Maxar 商业"},
    "wv3":           {"requires_vpn": True,  "source": "Maxar 商业"},
    # ── 需 VPN：Google Earth Engine ───────────────────────────────
    "gee_sentinel2": {"requires_vpn": True,  "source": "Google Earth Engine"},
    "gee_landsat":   {"requires_vpn": True,  "source": "Google Earth Engine"},
    "gee_modis":     {"requires_vpn": True,  "source": "Google Earth Engine"},
    "gee_custom":    {"requires_vpn": True,  "source": "Google Earth Engine"},
}

assert set(SENSOR_NETWORK) == set(SENSOR_MAP), (
    "SENSOR_NETWORK 与 SENSOR_MAP 键不一致："
    f"缺失 {set(SENSOR_MAP) - set(SENSOR_NETWORK)}，"
    f"多余 {set(SENSOR_NETWORK) - set(SENSOR_MAP)}"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geo-downloader",
        description="卫星图像自动下载工具 — 面向地质勘探",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
所有参数均可在 config/credentials.yaml 的 task 节中设置默认值，
命令行参数优先级高于配置文件。

传感器列表 (--sensor):
  ── 无需账号 ──────────────────────────────────────
  dem        Copernicus DEM GLO-30    30m  无需账号
  srtm       SRTM DEM                30m  无需账号（AWS）★推荐首次测试
  landsat    Landsat 8/9 L2 光学      30m  无需账号（Planetary Computer）
  landsat7   Landsat 7 ETM+ L2 光学  15-30m  无需账号（含全色B8，含SLC-off）
  ── 需 Copernicus 账号 ───────────────────────────
  sentinel2  Sentinel-2 L2A 光学      10m  需Copernicus账号
  ── 需 NASA Earthdata 账号 ───────────────────────
  sentinel1  Sentinel-1 GRD SAR      10m  C波段，全天时
  opera      OPERA RTC-S1            30m  已校正SAR，可直接叠合光学
  alos       ALOS PALSAR L波段SAR    ---  L波段，穿透植被（2006-2011档案）
  emit       EMIT L2A 高光谱          60m  285波段，矿物填图
  aster      ASTER L2 热红外+光学    15m  矿物/岩性识别
  modis      MODIS 反射率/植被指数   250m  高频次，时间序列分析
  gedi       GEDI 激光测高            25m  穿透植被的真实地形+植被高度
  ecostress  ECOSTRESS 地表温度       70m  地热/热液异常探测
  hyperion   Hyperion EO-1 高光谱    30m  242波段，2001-2017年存档
  aviris     AVIRIS-NG 机载高光谱     ~5m  432波段，机载任务覆盖区域
  ── 需 DLR EOWEB 账号 ───────────────────────────
  enmap      EnMAP L2A 高光谱        30m  244波段，自动下载（Playwright+FTPS）
  ── 需 Planet API Key ────────────────────────────
  planet     PlanetScope 高分辨率     3-5m  4/8波段，每日覆盖，需Planet账号

示例:
  python3 main.py                              # 使用配置文件默认参数
  python3 main.py --sensor dem srtm            # 双DEM对比
  python3 main.py --sensor sentinel2 aster opera --start 2024-01-01 --end 2024-06-30
  python3 main.py --sensor alos --start 2006-01-01 --end 2011-12-31
        """,
    )

    # 所有参数均为可选，未指定时从配置文件读取
    parser.add_argument("--kml", metavar="PATH",
        help="坐标文件路径（.kml / .ovkml / .kmz / .ovkmz / .xlsx / .xls），或包含多个坐标文件的文件夹路径")
    parser.add_argument("--sensor", nargs="+", choices=list(SENSOR_MAP.keys()), metavar="SENSOR",
        help=f"要下载的数据类型，可多选。可选: {', '.join(SENSOR_MAP.keys())}")
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="搜索起始日期（DEM可省略）")
    parser.add_argument("--end",   metavar="YYYY-MM-DD", help="搜索结束日期（DEM可省略）")
    parser.add_argument("--cloud", type=int, metavar="PCT", help="光学影像最大云量百分比（0-100）")
    parser.add_argument("--output", metavar="DIR", help="下载输出目录")
    parser.add_argument("--max-items", type=int, metavar="N", help="每区域每传感器最多下载景数")
    parser.add_argument("--no-clip", action="store_true", help="下载后不裁剪到KML范围")
    parser.add_argument("--no-derive", action="store_true",
        help="跳过衍生产品计算（地表温度/温度梯度/OTCI），默认自动计算")
    parser.add_argument("--delivery-dir", metavar="DIR",
        help="标准交付目录根路径（默认 ./delivery），打包后按纳兰矿标准目录结构存放")
    parser.add_argument("--no-package", action="store_true",
        help="跳过打包到标准交付目录，仅保留原始下载数据")
    parser.add_argument("--config", metavar="FILE",
        help="credentials.yaml路径，默认 config/credentials.yaml")
    parser.add_argument("--point-buffer", type=float, metavar="DEG",
        help="KML中点要素的缓冲半径（度），默认0.1°≈11km")
    parser.add_argument("--area-workers", type=int, metavar="N", default=None,
        help="多KML区域并发数，默认2（设为1退化为串行）")
    parser.add_argument("--order-timeout", type=int, metavar="SEC", default=None,
        help="EnMAP等需下单的传感器的订单轮询超时（秒），默认28800（8小时）")
    parser.add_argument("--same-period-days", type=int, metavar="DAYS", default=None,
        help="拼接选片的时相窗口（天），优先用同期景拼接以减少辐射边界；默认30，设0关闭")
    parser.add_argument("--radiometric-normalize", action="store_true", default=None,
        help="拼接前对多景做逐波段相对辐射归一化（RRN），消除缝处辐射台阶；默认关")

    return parser


def merge_config(args: argparse.Namespace, task_cfg: dict) -> argparse.Namespace:
    """
    将配置文件 task 节的值合并到 args。
    命令行显式指定的参数优先级更高，未指定时使用配置文件值，
    配置文件也未指定时使用硬编码默认值。
    """
    defaults = {
        "kml":          None,
        "sensor":       ["dem"],
        "start":        None,
        "end":          None,
        "cloud":        20,
        "output":       "./downloads",
        "max_items":    5,
        "no_clip":      False,
        "point_buffer": 0.1,
        "no_derive":    False,
        "delivery_dir": "./delivery",
        "no_package":   False,
        "area_workers": 2,
        "order_timeout": 28800,
        "same_period_days": 30,
        "radiometric_normalize": False,
    }

    for attr, default in defaults.items():
        # 配置文件键名：max_items → max_items, no_clip 通过 clip 控制
        cfg_key = attr
        if getattr(args, attr, None) is None or getattr(args, attr, None) == argparse.SUPPRESS:
            # 命令行未指定，尝试从配置文件读取
            if cfg_key == "no_clip":
                # clip: true → no_clip: False
                cfg_val = task_cfg.get("clip", True)
                setattr(args, attr, not cfg_val)
            else:
                cfg_val = task_cfg.get(cfg_key, default)
                if cfg_val is not None:
                    setattr(args, attr, cfg_val)
                else:
                    setattr(args, attr, default)

    # sensor 在配置文件中是列表，需验证合法性
    if isinstance(args.sensor, list):
        invalid = [s for s in args.sensor if s not in SENSOR_MAP]
        if invalid:
            raise ValueError(
                f"配置文件中包含不支持的传感器: {invalid}\n"
                f"可选值: {list(SENSOR_MAP.keys())}"
            )

    return args


def load_downloader(sensor: str, creds: dict, output_dir: str):
    """动态加载并实例化下载器"""
    module_path, class_name, cred_key = SENSOR_MAP[sensor]

    # 动态导入
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    # 部分传感器需要额外的 platform 参数
    _platform_kwargs = {
        "spot67":   {"platform": "spot67"},
        "pleiades": {"platform": "pleiades"},
        "wv2":      {"platform": "wv2"},
        "wv3":      {"platform": "wv3"},
        "dem":      {"dem_resolution": creds.get("task", {}).get("dem_resolution", 30),
                      "credentials": creds.get("aws", {})},
        "srtm":     {"dem_resolution": creds.get("task", {}).get("dem_resolution", 30),
                      "credentials": creds.get("aws", {})},
        # gee_custom 需要 collection_id / bands / scale_meters（从配置文件 task 节读取）
        "gee_custom": {
            "collection_id": creds.get("task", {}).get("gee_collection_id", ""),
            "bands": creds.get("task", {}).get("gee_bands", []),
            "scale_meters": creds.get("task", {}).get("gee_scale", 30),
        },
    }
    extra_kwargs = _platform_kwargs.get(sensor, {})

    # 获取平台凭据
    if cred_key is not None:
        try:
            platform_creds = get_platform_creds(creds, cred_key)
        except CredentialsError as e:
            raise CredentialsError(
                f"传感器 '{sensor}' 需要 '{cred_key}' 的账号凭据:\n{e}"
            )
        return cls(credentials=platform_creds, output_dir=output_dir, **extra_kwargs)
    else:
        return cls(output_dir=output_dir, **extra_kwargs)


def _create_downloaders_for_area(sensors: list, creds: dict, output_dir: str) -> dict:
    """在调用线程内为单个区域独立实例化下载器，避免跨区域共享状态。"""
    downloaders = {}
    for sensor in sensors:
        try:
            downloaders[sensor] = load_downloader(sensor, creds, output_dir)
        except (CredentialsError, ImportError) as e:
            print(f"  [{sensor}] 跳过: {e}")
        except Exception as e:
            print(f"  [{sensor}] 初始化失败: {e}")
    return downloaders


def _validate_and_prewarm(sensors: list, creds: dict, output_dir: str) -> list:
    """
    在主线程中预验证凭据并完成首次 earthaccess 登录，
    让全局 auth 状态就绪，消除区域并发时的认证竞态。
    返回可用的传感器列表。
    """
    active = []
    for sensor in sensors:
        try:
            load_downloader(sensor, creds, output_dir)
            print(f"  [初始化] {sensor}: {SENSOR_DESCRIPTIONS[sensor]}")
            active.append(sensor)
        except CredentialsError as e:
            print(f"  [跳过] {sensor}: 凭据缺失\n    {e}")
        except ImportError as e:
            print(f"  [跳过] {sensor}: 依赖缺失\n    {e}")
    return active


def _make_error_logger(area_dir: Path, area_name: str) -> logging.Logger:
    """为指定区域目录创建专属的异常日志 logger，写入 download_errors.log。"""
    area_dir.mkdir(parents=True, exist_ok=True)
    log_path = area_dir / "download_errors.log"
    logger = logging.getLogger(f"geo.errors.{area_name}")
    logger.setLevel(logging.ERROR)
    # 避免重复添加 handler（任务重启场景）
    if not logger.handlers:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  [%(sensor)s]  %(levelname)s\n%(message)s\n" + "-" * 60,
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
    logger.propagate = False
    return logger


def process_area(
    geometry, bbox, area_name: str,
    sensors: list, creds: dict, output_dir: str,
    start_date, end_date,
    cloud_cover: int, max_items: int, clip: bool,
    derive: bool = True,
    kml_path: Path = None,
    delivery_dir: str = "./delivery",
    package: bool = True,
    order_timeout: int = 7200,
    same_period_days: int = 30,
    radiometric_normalize: bool = False,
):
    """处理单个KML区域：并发下载所有传感器，完成后计算衍生产品"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _task_start = datetime.now()

    downloaders = _create_downloaders_for_area(sensors, creds, output_dir)
    if not downloaders:
        print(f"\n[{area_name}] 没有可用的下载器，跳过。")
        return {}

    # 区域目录（下载器已创建，此处直接引用）
    area_dir = Path(output_dir) / area_name
    area_dir.mkdir(parents=True, exist_ok=True)
    err_logger = _make_error_logger(area_dir, area_name)

    print(f"\n{'='*60}")
    print(f"区域: {area_name}")
    print(f"BBox: 经度 {bbox[0]:.4f}~{bbox[2]:.4f}  纬度 {bbox[1]:.4f}~{bbox[3]:.4f}")
    print(f"{'='*60}")

    def _log_error(sensor: str, exc: BaseException, *, include_tb: bool = True):
        """将异常写入区域的 download_errors.log。"""
        tb_text = traceback.format_exc() if include_tb else ""
        msg = f"{type(exc).__name__}: {exc}"
        if tb_text and tb_text.strip() != "NoneType: None":
            msg += f"\n\nTraceback:\n{tb_text.rstrip()}"
        err_logger.error(msg, extra={"sensor": sensor})

    def _run_sensor(sensor):
        _emit_progress_event(sensor, phase="start")
        dl = downloaders[sensor]
        # PRISMA / EnMAP 异步:下单后立即返回,主流程不阻塞 8h 轮询,
        # 由 web/app.py 的 _async_pending_loop daemon 接管后续下载和补包
        extra = {}
        if sensor in ("prisma", "enmap"):
            extra["defer_poll"] = True
        try:
            files = dl.run(
                bbox=bbox,
                geometry=geometry,
                area_name=area_name,
                start_date=start_date or "2000-01-01",
                end_date=end_date or "2099-12-31",
                cloud_cover=cloud_cover,
                max_items=max_items,
                clip=clip,
                order_timeout=order_timeout,
                sensor_key=sensor,
                same_period_days=same_period_days,
                radiometric_normalize=radiometric_normalize,
                **extra,
            )
            if extra.get("defer_poll") and not files:
                _emit_progress_event(sensor, phase="pending")
            else:
                _emit_progress_event(sensor, phase="done", done=len(files))
            return sensor, len(files), None
        except CredentialsError as e:
            print(f"\n[{sensor}] 账号错误，跳过:\n  {e}")
            _log_error(sensor, e, include_tb=False)
            _emit_progress_event(sensor, phase="error")
            return sensor, 0, None
        except ImportError as e:
            print(f"\n[{sensor}] 依赖缺失，跳过:\n  {e}")
            _log_error(sensor, e, include_tb=False)
            _emit_progress_event(sensor, phase="error")
            return sensor, 0, None
        except Exception as e:
            print(f"\n[{sensor}] 下载出错:\n  {e}")
            _log_error(sensor, e)
            if "--debug" in sys.argv:
                traceback.print_exc()
            try:
                from downloader.stats import record as _stats_record
                _stats_record(sensor, success=False)
            except Exception:
                pass
            _emit_progress_event(sensor, phase="error")
            return sensor, 0, None

    summary = {}
    # 最多4个传感器并行，平衡带宽利用与服务端限速
    MAX_WORKERS = 4

    if "enmap" in sensors and len(sensors) > 1:
        # ── EnMAP 优先策略 ──────────────────────────────────────────
        # 1. 先搜索 EnMAP（不下载），判断是否有数据
        # 2a. 无数据：立即启动其他传感器并发下载
        # 2b. 有数据：提交 EnMAP 下单并等待进入轮询状态，再启动其他传感器
        import threading

        other_sensors = [s for s in sensors if s != "enmap"]

        # Event：EnMAP 已进入轮询（或搜索无数据），通知其他传感器可以启动
        enmap_polling_event = threading.Event()

        enmap_dl = downloaders["enmap"]

        def _run_enmap_first():
            """运行 EnMAP 全流程，并在进入轮询时触发 event。
            defer_poll=True 后,'进入轮询' 实际是 '下单完成' 的同义事件 ——
            on_polling_started 仍会被调,event 仍会触发,其他 sensor 仍能解阻塞。"""
            _emit_progress_event("enmap", phase="start")
            try:
                files = enmap_dl.run(
                    bbox=bbox,
                    geometry=geometry,
                    area_name=area_name,
                    start_date=start_date or "2000-01-01",
                    end_date=end_date or "2099-12-31",
                    cloud_cover=cloud_cover,
                    max_items=max_items,
                    clip=clip,
                    order_timeout=order_timeout,
                    on_polling_started=enmap_polling_event.set,
                    defer_poll=True,
                    sensor_key="enmap",
                )
                # defer_poll=True 下立即返回,真正的下载交给 daemon
                if not files:
                    _emit_progress_event("enmap", phase="pending")
                else:
                    _emit_progress_event("enmap", phase="done", done=len(files))
                return "enmap", len(files), None
            except CredentialsError as e:
                print(f"\n[enmap] 账号错误，跳过:\n  {e}")
                _log_error("enmap", e, include_tb=False)
                _emit_progress_event("enmap", phase="error")
                return "enmap", 0, None
            except ImportError as e:
                print(f"\n[enmap] 依赖缺失，跳过:\n  {e}")
                _log_error("enmap", e, include_tb=False)
                _emit_progress_event("enmap", phase="error")
                return "enmap", 0, None
            except Exception as e:
                print(f"\n[enmap] 下载出错:\n  {e}")
                _log_error("enmap", e)
                if "--debug" in sys.argv:
                    traceback.print_exc()
                _emit_progress_event("enmap", phase="error")
                return "enmap", 0, None
            finally:
                # 无论成功/失败/无数据，确保其他传感器不会永久阻塞
                enmap_polling_event.set()

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(sensors))) as pool:
            # 先提交 EnMAP
            enmap_future = pool.submit(_run_enmap_first)

            # 等待 EnMAP 进入轮询（或完成/失败），再提交其他传感器
            enmap_polling_event.wait()

            other_futures = {pool.submit(_run_sensor, s): s for s in other_sensors}

            # 收集所有结果
            enmap_sensor, enmap_count, _ = enmap_future.result()
            summary[enmap_sensor] = enmap_count

            for fut in as_completed(other_futures):
                sensor, count, _ = fut.result()
                summary[sensor] = count
    else:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(sensors))) as pool:
            futures = {pool.submit(_run_sensor, s): s for s in sensors}
            for fut in as_completed(futures):
                sensor, count, _ = fut.result()
                summary[sensor] = count

    # ── 衍生产品计算（地表温度 / 温度梯度 / 温度异常梯度 / OTCI）──
    _derive_sensors = {"landsat", "aster", "sentinel2"}
    if derive and any(s in sensors for s in _derive_sensors):
        print(f"\n[衍生产品] 正在计算地表温度、温度梯度、OTCI...")
        try:
            from postprocess.derive import derive_all
            derived = derive_all(area_dir)
            n_derived = len(derived)
            if n_derived:
                print(f"  [衍生产品] 已生成 {n_derived} 个产品: {', '.join(derived.keys())}")
            summary["derive"] = n_derived
        except ImportError as e:
            print(f"  [跳过衍生产品] 依赖缺失: {e}")
        except Exception as e:
            print(f"  [警告] 衍生产品计算出错: {e}")
            if "--debug" in sys.argv:
                traceback.print_exc()

    # ── 检测异步 sensor 的待办订单 ──
    # PRISMA / EnMAP 采用 defer_poll 模式,主流程下单后立即返回,真正下载和补包
    # 由 web/app.py 的 _async_pending_loop daemon 接管。这里只探测 .xxx_pending_order.json
    # 是否存在,通过 stdout 协议消息告诉 web 后端"task 进入 polling 状态"
    async_pending_sensors = []
    if downloaders:
        _base_output = Path(next(iter(downloaders.values())).output_dir)
        _area_root = _base_output / area_name
        for _async_sensor, _async_cls_path in (
            ("enmap",  "downloader.enmap.EnMAPDownloader"),
            ("prisma", "downloader.prisma.PRISMADownloader"),
        ):
            if _async_sensor not in sensors:
                continue
            try:
                _mod_path, _cls_name = _async_cls_path.rsplit(".", 1)
                _mod = importlib.import_module(_mod_path)
                _cls = getattr(_mod, _cls_name)
                # 对 PRISMA 订单文件在 area_root / "prisma";EnMAP 同理
                if _cls._order_file(_area_root / _async_sensor).exists():
                    async_pending_sensors.append(_async_sensor)
            except Exception:
                pass
    if async_pending_sensors:
        # 协议消息给 web/app.py 子进程 stdout 解析,标记 task.status = "polling"
        print(f"__ASYNC_PENDING__:{','.join(async_pending_sensors)}")

    # ── 标准交付目录打包 ──
    # 同步 sensor 部分先打包,异步部分等 daemon 完成后再调 package_delivery(incremental=True) 补漏
    if package and kml_path is not None:
        try:
            from postprocess.package import package_delivery
            pkg_path = package_delivery(
                raw_area_dir=area_dir,
                kml_path=Path(kml_path),
                delivery_root=Path(delivery_dir),
                area_label=area_name,
                bbox=bbox,
                sensors_attempted=sensors,
                download_summary=summary,
                start_date=start_date,
                end_date=end_date,
                task_start_time=_task_start,
                task_end_time=datetime.now(),
            )
            summary["delivery"] = str(pkg_path)
        except Exception as e:
            print(f"\n[警告] 打包到标准交付目录失败: {e}")
            if "--debug" in sys.argv:
                traceback.print_exc()

        # ── 交付自检 + SAFE 修复(同步部分;异步源此时仍 pending,会判 PENDING_ASYNC)──
        try:
            _delivery_self_check(area_dir, Path(delivery_dir), area_name,
                                 Path(kml_path), sensors, summary)
        except Exception as e:
            print(f"[自检] 交付自检异常(忽略): {e}")
            if "--debug" in sys.argv:
                traceback.print_exc()

    return summary


def _delivery_self_check(area_dir, delivery_root, area_name, kml_path, sensors, summary):
    """同步打包后跑交付自检 + SAFE 修复,并用 __DELIVERY_CHECK__ 上报 web。
    计数器落 sidecar(area_dir/.delivery_repair_state.json),跨子进程/daemon 不清零。"""
    import json as _json
    import os as _os
    from postprocess.delivery_check import check_delivery, execute_repairs, load_rules
    from postprocess.package import package_delivery
    from postprocess.dem_fetch import fetch_dem_for_area

    sidecar = area_dir / ".delivery_repair_state.json"
    try:
        state = _json.loads(sidecar.read_text()) if sidecar.exists() else {}
    except Exception:
        state = {}
    attempts = dict(state.get("attempts", {}))

    def _safe_pkg():
        try:
            package_delivery(raw_area_dir=area_dir, kml_path=kml_path,
                             delivery_root=delivery_root, area_label=area_name,
                             incremental=True)
            return True
        except Exception as _e:
            print(f"[自检] 增量补包失败: {_e}")
            return False

    # DEM 自动补全:下载 Copernicus DEM 并补包到季节根。
    # 子进程递归保护:本函数若由 fetch_dem_for_area 派生的 dem 子进程触发,则不再自补。
    _dem_cb = None
    if not _os.environ.get("GEO_DEM_FETCH_CHILD"):
        def _dem_cb():
            return fetch_dem_for_area(kml_path, area_dir.parent, delivery_root)

    # 投影底图自动补全:重新下载 Google 卫星底图到交付区域目录顶层。
    def _overview_cb():
        try:
            import yaml as _yaml
            from postprocess.satellite_overview import download_satellite_overview
            from downloader.kml_parser import parse_kml
            cred_path = Path(__file__).parent / "config" / "credentials.yaml"
            if not cred_path.exists():
                return False
            with open(cred_path, encoding="utf-8") as _f:
                g = (_yaml.safe_load(_f) or {}).get("google_maps") or {}
            if not g.get("api_key"):
                return False
            geom, bbox, _ = parse_kml(str(kml_path))
            out = download_satellite_overview(
                bbox=bbox, api_key=g["api_key"], delivery_dir=delivery_root / area_name,
                geometry=geom, maptype="satellite", proxy=g.get("proxy"))
            return bool(out and Path(out).exists())
        except Exception as _e:
            print(f"[自检] 投影底图补全失败: {_e}")
            return False

    report = check_delivery(
        delivery_dir=delivery_root / area_name, area_label=area_name,
        raw_area_dir=area_dir, requested_sensors=sensors,
        summary={k: v for k, v in summary.items() if isinstance(v, int)},
        rules=load_rules())
    report = execute_repairs(
        report, safe_package_cb=_safe_pkg, dem_fetch_cb=_dem_cb,
        overview_fetch_cb=_overview_cb,
        attempts_get=lambda k: attempts.get(k, 0),
        attempts_inc=lambda k: attempts.__setitem__(k, attempts.get(k, 0) + 1))

    try:
        sidecar.write_text(_json.dumps(
            {"attempts": attempts, "last_checked_at": report.checked_at},
            ensure_ascii=False))
    except Exception:
        pass
    print("__DELIVERY_CHECK__" + _json.dumps(report.to_dict(), ensure_ascii=False))
    print(f"[自检] 交付总体: {report.overall};SAFE修复 {len(report.safe_repairs_run)} 项,"
          f"需关注 {len(report.risky_repairs_offered)} 项")


def print_summary(all_summaries: dict):
    """打印全局下载汇总"""
    print(f"\n{'='*60}")
    print("下载汇总")
    print(f"{'='*60}")
    total_files = 0
    for area, sensors in all_summaries.items():
        print(f"  {area}:")
        for sensor, count in sensors.items():
            if sensor == "delivery":
                print(f"    {'交付目录':<12} {count}")
                continue
            status = f"{count} 个文件" if count > 0 else "无文件"
            print(f"    {sensor:<12} {status}")
            total_files += count
    print(f"\n  总计: {total_files} 个文件")


def main():
    parser = build_parser()
    args = parser.parse_args()

    # 加载配置文件（账号 + 任务默认参数）
    try:
        all_creds = load_credentials(args.config)
    except Exception:
        all_creds = {}

    task_cfg = all_creds.get("task", {}) or {}

    # 将配置文件的 task 节合并到 args（命令行优先）
    try:
        args = merge_config(args, task_cfg)
    except ValueError as e:
        print(f"[错误] 配置文件参数有误: {e}")
        sys.exit(1)

    # 验证必要参数
    if not args.kml:
        parser.error("未指定 --kml，也未在 config/credentials.yaml 的 task.kml 中配置")
    if not args.sensor:
        parser.error("未指定 --sensor，也未在 config/credentials.yaml 的 task.sensor 中配置")

    # 验证时间参数（DEM/SRTM不需要，其他都需要）
    _no_date_sensors = {"dem", "srtm"}
    non_dem_sensors = [s for s in args.sensor if s not in _no_date_sensors]
    if non_dem_sensors and (not args.start or not args.end):
        parser.error(
            f"传感器 {non_dem_sensors} 需要 start 和 end 时间范围\n"
            f"请在命令行或 config/credentials.yaml 的 task 节中配置"
        )

    print("=" * 60)
    print("  geo-downloader — 卫星图像自动下载工具")
    print("=" * 60)
    print(f"  KML输入  : {args.kml}")
    print(f"  传感器   : {', '.join(args.sensor)}")
    if args.start:
        print(f"  时间范围 : {args.start} ~ {args.end}")
    if "sentinel2" in args.sensor or "landsat" in args.sensor:
        print(f"  最大云量 : {args.cloud}%")
    print(f"  输出目录 : {args.output}")
    print(f"  最大下载 : 每区域每传感器 {args.max_items} 景")
    print(f"  裁剪     : {'否（保留完整景）' if args.no_clip else '是（裁剪到KML范围）'}")
    if not args.no_package:
        print(f"  交付目录 : {args.delivery_dir}")
    print()

    # 解析KML — 同时记录每个区域对应的kml文件路径（打包时需要）
    kml_path = Path(args.kml)
    area_kml_map: dict = {}  # area_name → Path
    try:
        if kml_path.is_dir():
            print(f"批量模式：扫描文件夹 {kml_path}")
            kml_files = sorted(
                list(kml_path.glob("*.kml")) + list(kml_path.glob("*.KML")) +
                list(kml_path.glob("*.ovkml")) + list(kml_path.glob("*.OVKML")) +
                list(kml_path.glob("*.kmz")) + list(kml_path.glob("*.KMZ")) +
                list(kml_path.glob("*.ovkmz")) + list(kml_path.glob("*.OVKMZ")) +
                list(kml_path.glob("*.xlsx")) + list(kml_path.glob("*.XLSX")) +
                list(kml_path.glob("*.xls")) + list(kml_path.glob("*.XLS"))
            )
            areas = []
            for kf in kml_files:
                try:
                    result = parse_kml(str(kf), args.point_buffer)
                    areas.append(result)
                    area_kml_map[result[2]] = kf
                except KMLParseError as e:
                    print(f"  [警告] 跳过 {kf.name}: {e}")
        else:
            print(f"单文件模式：{kml_path.name}")
            area = parse_kml(str(kml_path), args.point_buffer)
            areas = [area]
            area_kml_map[area[2]] = kml_path
    except KMLParseError as e:
        print(f"[错误] KML解析失败: {e}")
        sys.exit(1)

    print(f"  共解析 {len(areas)} 个区域\n")

    # 凭据已在上方随配置文件一起加载，直接使用
    creds = all_creds
    if not creds and any(SENSOR_MAP[s][2] is not None for s in args.sensor):
        print("[提示] 未加载到账号配置，仅DEM等无需认证的传感器将正常工作。\n")

    # 预验证凭据 + 预热 earthaccess（主线程一次完成，消除并发认证竞态）
    active_sensors = _validate_and_prewarm(args.sensor, creds, args.output)

    if not active_sensors:
        print("\n[错误] 没有可用的下载器，请检查账号配置和依赖安装。")
        sys.exit(1)

    # 按历史下载成功率降序排序，让成功率高的传感器优先跑
    from downloader.stats import sort_by_rate
    active_sensors = sort_by_rate(active_sensors)
    print(f"\n  活跃传感器（按成功率排序）: {', '.join(active_sensors)}")
    area_workers = args.area_workers
    if len(areas) > 1:
        print(f"  区域并发数 : {area_workers}")

    # 处理每个区域（area_workers=1 退化为串行）
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    all_summaries = {}

    def _process_one(area_tuple):
        geometry, bbox, area_name = area_tuple
        return area_name, process_area(
            geometry=geometry,
            bbox=bbox,
            area_name=area_name,
            sensors=active_sensors,
            creds=creds,
            output_dir=args.output,
            start_date=args.start,
            end_date=args.end,
            cloud_cover=args.cloud,
            max_items=args.max_items,
            clip=not args.no_clip,
            derive=not args.no_derive,
            kml_path=area_kml_map.get(area_name),
            delivery_dir=args.delivery_dir,
            package=not args.no_package,
            order_timeout=args.order_timeout,
            same_period_days=args.same_period_days,
            radiometric_normalize=args.radiometric_normalize,
        )

    with ThreadPoolExecutor(max_workers=area_workers) as pool:
        futures = {pool.submit(_process_one, a): a[2] for a in areas}
        for fut in _as_completed(futures):
            try:
                area_name, summary = fut.result()
                all_summaries[area_name] = summary
            except Exception as e:
                area_name = futures[fut]
                print(f"\n[错误] 区域 {area_name} 处理失败: {e}")
                if "--debug" in sys.argv:
                    traceback.print_exc()
                all_summaries[area_name] = {}

    print_summary(all_summaries)
    print(f"\n  输出目录: {Path(args.output).resolve()}")
    if not args.no_package:
        print(f"  交付目录: {Path(args.delivery_dir).resolve()}")


if __name__ == "__main__":
    main()
