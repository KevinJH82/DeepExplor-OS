"""Prospector 配置集中管理"""

import os
from pathlib import Path

# 从同级 .env 读取密钥(无 python-dotenv 依赖,导入即注入 os.environ,持久且重启不丢)
_envf = Path(__file__).parent / ".env"
if _envf.exists():
    for _line in _envf.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── 目录 ──
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"
CACHE_DIR = BASE_DIR / "cache"

# ── Flask ──
HOST = "0.0.0.0"
PORT = 8085
DEBUG = True
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB

# ── 网络请求 ──
DEFAULT_TIMEOUT = 30          # 秒，普通 API 调用
LONG_TIMEOUT = 120            # 秒，大文件下载
GEOLOGY_MAP_TIMEOUT = int(os.getenv("GEOLOGY_MAP_TIMEOUT", "90"))  # 秒，地质瓦片拼图总耗时上限
MAX_RETRIES = 3               # 最大重试次数
RETRY_BACKOFF = 2             # 指数退避基数（秒）
RETRY_STATUS_CODES = {429, 502, 503, 504}
DOWNLOAD_CHUNK_SIZE = 8192    # 流式下载块大小

# ── 流水线默认参数 ──
DEFAULT_BUFFER_KM = 20
MIN_BUFFER_KM = 5
MAX_BUFFER_KM = 50

# ── 外部数据源 URL ──

# 地球物理
EMAG2_GEOTIFF_URL = (
    "https://www.ngdc.noaa.gov/geomag/data/EMAG2/EMAG2_V3_20170530/"
    "EMAG2_V3_20170530_UpCont.tif"
)
EMAG2_SEALEVEL_URL = (
    "https://www.ngdc.noaa.gov/geomag/data/EMAG2/EMAG2_V3_20170530/"
    "EMAG2_V3_20170530_Sealevel.tif"
)
WGM2012_BOUGUER_URL = (
    "https://bgi.obs-mip.fr/wp-content/uploads/data/WGM2012/bouguer_2x2.nc"
)
ICGEM_CALC_URL = "http://icgem.gfz-potsdam.de/calcgrid"
OPENTOPOGRAPHY_URL = "https://portal.opentopography.org/API/globaldem"
# OpenTopography API key（免费注册获取）；为空则 DEM 降级为下载链接，不自动下载出图
OPENTOPOGRAPHY_API_KEY = os.getenv("OPENTOPOGRAPHY_API_KEY", "")
EE_URL = "https://earthexplorer.usgs.gov/"

# 地球化学
GEOROC_API = "https://georoc.eu/api/v1/"

# 地质资料
NGAC_PORTAL_URL = "https://www.ngac.cn"
NGAC_SEARCH_PAGE = "https://www.ngac.cn/qgg_zt/#/search"
ONEGEOLOGY_URL = "https://portal.onegeology.org/OnegeologyGlobal/"

# 遥感
STAC_API_URL = "https://earth-search.aws.element84.com/v1"
GS_CLOUD_URL = "http://www.gscloud.cn/"

# ICGEM 本地重力场计算
ICGEM_GFC_URL = "https://datapub.gfz-potsdam.de/download/10.5880.ICGEM.2015.1/eigen-6c4.zip"
ICGEM_DEFAULT_MAX_DEGREE = 2190

# 学术论文
OPENALEX_URL = "https://api.openalex.org/works"
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# 翻译
MYMEMORY_URL = "https://api.mymemory.translated.net/get"

# ── 论文 LLM 提炼（DeepSeek，OpenAI 兼容接口）──
# 为空则跳过提炼，报告保留论文列表（优雅降级）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
# deepseek-chat 别名 2026-07-24 后弃用，对应 deepseek-v4-flash；可经环境变量切换
PAPER_SYNTHESIS_MODEL = os.getenv("PAPER_SYNTHESIS_MODEL", "deepseek-v4-flash")

# ── 清理 ──
TASK_MAX_AGE_DAYS = 30        # 超过此天数的任务输出目录打印警告
