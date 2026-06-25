"""系统配置"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
PROCESSED_DIR = DATA_DIR / "processed"
RESULTS_DIR = DATA_DIR / "results"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

NODATA = -9999.0

DEFAULT_WEIGHTS = {
    "stress": 0.25,
    "redox": 0.15,
    "fluid": 0.20,
    "fault": 0.20,
    "chem": 0.15,
    "temp_drive": 0.05,
    "cap_rock": 0.50,
    "temp_resist": 0.50,
}

DEFAULT_DELTA_THRESHOLD = None  # None=自适应阈值(取有效Δ的低分位);传入数值则按该数值阈值
DEFAULT_DELTA_PERCENTILE = 10.0  # 自适应阈值分位(Δ越负越有利,默认取最有利的约10%)
DEFAULT_TARGET_RESOLUTION = 30.0
DEFAULT_GAUSSIAN_SIGMA = 3.0
# 植被掩膜阈值(NDVI>此值视为植被,剔除蚀变指数)。只掩浓密冠层:本类冬季场景NDVI中位~0.30,
# 取0.5仅掩约13%(geo-analyser默认0.20会掩掉~75%,对本场景过激)。
DEFAULT_NDVI_VEG_THRESHOLD = 0.5

# 上传文件槽位定义
UPLOAD_SLOTS = {
    "dem": {"label": "DEM (SRTM/ASTER GDEM)", "required": True},
    "s2_b03": {"label": "Sentinel-2 B03 (绿)", "required": True},
    "s2_b04": {"label": "Sentinel-2 B04 (红)", "required": True},
    "s2_b08": {"label": "Sentinel-2 B08 (近红外)", "required": True},
    "aster_b05": {"label": "ASTER B05 (SWIR)", "required": True},
    "aster_b06": {"label": "ASTER B06 (SWIR)", "required": True},
    "aster_b07": {"label": "ASTER B07 (SWIR)", "required": True},
    "aster_b08": {"label": "ASTER B08 (SWIR)", "required": True},
    "aster_b10": {"label": "ASTER B10 (TIR)", "required": True},
    "aster_b11": {"label": "ASTER B11 (TIR)", "required": True},
    "aster_b12": {"label": "ASTER B12 (TIR)", "required": True},
    "aster_b13": {"label": "ASTER B13 (TIR)", "required": True},
    "aster_b14": {"label": "ASTER B14 (TIR)", "required": True},
    # P2 蚀变图谱扩展波段(可选,缺失则相关端元自动跳过)
    "s2_b02": {"label": "Sentinel-2 B02 (蓝/铁氧化参考)", "required": False},
    "s2_b11": {"label": "Sentinel-2 B11 (SWIR1/Al-OH)", "required": False},
    "s2_b12": {"label": "Sentinel-2 B12 (SWIR2/Al-OH)", "required": False},
    "aster_b01": {"label": "ASTER B1 (VNIR/铁参考)", "required": False},
    "aster_b03n": {"label": "ASTER B3N (VNIR/Fe³⁺)", "required": False},
    "aster_b09": {"label": "ASTER B9 (SWIR/碳酸盐)", "required": False},
    "insar": {"label": "InSAR 速度场 (可选)", "required": False},
    "insar_coherence": {"label": "InSAR 相干性 (可选，与速度场配套)", "required": False},
    "kml": {"label": "KML / OVKML 研究区边界", "required": True},
}

# ZIP 自动匹配规则：文件名/路径模式 → slot
SLOT_MATCH_RULES = {
    "kml": {
        "extensions": [".kml", ".ovkml"],
        "patterns": [],  # 所有 .kml/.ovkml 自动匹配
    },
    "dem": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["dem", "srtm", "gdem", "elevation", "height"],
    },
    "s2_b03": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b03"],
        "context": ["sentinel", "s2", "msi"],
        "aliases": ["green"],
    },
    "s2_b04": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b04"],
        "context": ["sentinel", "s2", "msi"],
        "aliases": ["red"],
    },
    "s2_b08": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b08"],
        "context": ["sentinel", "s2", "msi"],
        "aliases": ["nir", "near_infrared", "nearir"],
        "default_for_ambiguous": True,
    },
    "aster_b05": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b05"],
        "context": ["aster", "swir"],
    },
    "aster_b06": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b06"],
        "context": ["aster", "swir"],
    },
    "aster_b07": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b07"],
        "context": ["aster", "swir"],
    },
    "aster_b08": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b08"],
        "context": ["aster", "swir"],
    },
    "aster_b10": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b10"],
        "context": ["aster", "tir"],
    },
    "aster_b11": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b11"],
        "context": ["aster", "tir"],
    },
    "aster_b12": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b12"],
        "context": ["aster", "tir"],
    },
    "aster_b13": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b13"],
        "context": ["aster", "tir"],
    },
    "aster_b14": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["b14"],
        "context": ["aster", "tir"],
    },
    "insar": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["insar", "ifg", "velocity", "deformation"],
    },
    "insar_coherence": {
        "extensions": [".tif", ".tiff"],
        "patterns": ["coherence", "coh"],
        "context": ["insar", "sar"],
    },
}

# 波段号 → 可能冲突的 slot 列表
BAND_CONFLICTS = {
    "b08": ["s2_b08", "aster_b08"],
}
