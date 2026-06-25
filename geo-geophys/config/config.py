"""geo-geophys 物探（位场处理 + ANT 速度体接入）- 配置

硬约束同 geo-model3d：上游根用真实路径并支持 env 覆盖，broker 调用显式传入。
"""

import os


class Config:
    HOST = '0.0.0.0'
    PORT = 8087  # 8086 已被 geo-model3d 占用
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY', 'geo-geophys-secret-key-2024')

    MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1GB（速度体可能较大）

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))     # .../geo-geophys
    REPO_DIR = os.path.dirname(BASE_DIR)                       # .../deepexplor-services
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    TEMP_FOLDER = os.path.join(UPLOAD_FOLDER, 'temp')
    RESULTS_FOLDER = os.environ.get('RESULTS_ROOT', os.path.join(BASE_DIR, 'results'))

    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_FILE = os.path.join(BASE_DIR, 'logs', 'app.log')

    # ── 上游服务 results/output 根（真实路径，env 可覆盖）──
    DATACOLLE_OUTPUTS = os.environ.get(
        'DATACOLLE_OUTPUTS', os.path.join(REPO_DIR, 'data-colle', 'prospector', 'output'))
    GEO_STRU_OUTPUTS = os.environ.get(
        'GEO_STRU_OUTPUTS', os.path.join(REPO_DIR, 'geo-stru', 'results'))

    # ── 处理默认参数 ──
    IGRF_DATE = os.environ.get('GEOPHYS_IGRF_DATE', '2020-01-01')   # RTP 用的地磁年代
    EULER_SI = float(os.environ.get('GEOPHYS_EULER_SI', 1.0))       # 欧拉构造指数(0接触/1岩墙/2管/3球)
    EULER_WINDOW = int(os.environ.get('GEOPHYS_EULER_WINDOW', 10))  # 欧拉滑动窗口(像元)
    # 速度体网格（与 geo-model3d 对齐）
    GRID_RES_M = float(os.environ.get('GEOPHYS_GRID_RES_M', 100.0))
    GRID_ZMAX_M = float(os.environ.get('GEOPHYS_ZMAX_M', 3000.0))
    GRID_DZ_M = float(os.environ.get('GEOPHYS_DZ_M', 100.0))
    GRID_MAX_CELLS = int(os.environ.get('GEOPHYS_MAX_CELLS', 8_000_000))

    # 一键串联：geo-model3d 服务地址
    MODEL3D_URL = os.environ.get('MODEL3D_URL', 'http://127.0.0.1:8086')

    # 欧拉构造指数按矿种自动定（源体几何：0接触/脉, 1岩床/层状, 2岩管/紧凑磁性体）
    # 非专家不用懂 SI，选矿种即自动；高级选项可覆盖。
    EULER_SI_BY_MINERAL = {
        "铁": 1.0, "铜": 2.0, "钼": 2.0, "铜钼": 2.0,
        "金": 1.0, "银": 1.0, "铅锌": 1.0,
        "镍": 2.0, "铬": 2.0, "钛": 2.0, "铂族": 2.0,
        "稀土": 2.0, "金刚石": 2.0, "铀": 1.0,
        "钨": 2.0, "锡": 2.0, "钨锡": 2.0, "锂": 1.0, "锰": 1.0,
    }

    @staticmethod
    def auto_euler_si(mineral: str) -> float:
        return float(Config.EULER_SI_BY_MINERAL.get((mineral or "").strip(), 1.0))

    @staticmethod
    def upstream_roots():
        return {'datacolle': Config.DATACOLLE_OUTPUTS, 'stru': Config.GEO_STRU_OUTPUTS}

    @staticmethod
    def create_directories():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        os.makedirs(Config.RESULTS_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)
