"""geo-drill 钻探验证与布孔闭环系统 - 配置

硬约束同各服务：上游根用真实路径并支持 env 覆盖，broker 调用显式传入。
"""

import os


class Config:
    HOST = '0.0.0.0'
    PORT = 8089  # 8088 已被 geo-geochem 占用
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY', 'geo-drill-secret-key-2024')

    MAX_CONTENT_LENGTH = 512 * 1024 * 1024  # 512MB（编录 CSV 一般不大）

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))     # .../geo-drill
    REPO_DIR = os.path.dirname(BASE_DIR)                       # .../deepexplor-services
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    TEMP_FOLDER = os.path.join(UPLOAD_FOLDER, 'temp')
    RESULTS_FOLDER = os.environ.get('RESULTS_ROOT', os.path.join(BASE_DIR, 'results'))

    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_FILE = os.path.join(BASE_DIR, 'logs', 'app.log')

    # ── 上游服务 results/output 根（真实路径，env 可覆盖）──
    GEO_MODEL3D_OUTPUTS = os.environ.get(
        'GEO_MODEL3D_OUTPUTS', os.path.join(REPO_DIR, 'geo-model3d', 'results'))
    DATACOLLE_OUTPUTS = os.environ.get(
        'DATACOLLE_OUTPUTS', os.path.join(REPO_DIR, 'data-colle', 'prospector', 'output'))
    GEO_SLOWVARS_OUTPUTS = os.environ.get(
        'GEO_SLOWVARS_OUTPUTS', os.path.join(REPO_DIR, 'geo-7slow', 'backend', 'data', 'results'))

    # ── AI 布孔默认参数 ──
    TOP_N = int(os.environ.get('DRILL_TOP_N', 20))                # 布孔数
    MIN_SEP_M = float(os.environ.get('DRILL_MIN_SEP_M', 200.0))   # 最小孔距(米)
    EXPLORE_WEIGHT = float(os.environ.get('DRILL_EXPLORE_WEIGHT', 0.3))  # 不确定性(信息增益)权重
    SLOWVARS_WEIGHT = float(os.environ.get('DRILL_SLOWVARS_WEIGHT', 0.25))  # geo-7slow 慢变量靶区软先验权重(0=关)
    # 矿种 → 主指示元素（见矿判定取该元素品位 vs cutoff）
    MINERAL_MAIN_ELEMENT = {
        "铜": "Cu", "铜钼": "Cu", "钼": "Mo", "金": "Au", "银": "Ag",
        "铅锌": "Pb", "铁": "Fe", "镍": "Ni", "铬": "Cr", "钨": "W",
        "锡": "Sn", "钨锡": "W", "锂": "Li", "稀土": "REE", "铀": "U", "锰": "Mn",
    }

    # 一键串联：geo-model3d 服务地址（带 drill_feedback 回灌重算）
    MODEL3D_URL = os.environ.get('MODEL3D_URL', 'http://127.0.0.1:8086')

    @staticmethod
    def main_element_for(mineral: str) -> str:
        return Config.MINERAL_MAIN_ELEMENT.get((mineral or "").strip(), "")

    @staticmethod
    def upstream_roots():
        return {'model3d': Config.GEO_MODEL3D_OUTPUTS, 'datacolle': Config.DATACOLLE_OUTPUTS,
                'slowvars': Config.GEO_SLOWVARS_OUTPUTS}

    @staticmethod
    def create_directories():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        os.makedirs(Config.RESULTS_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)
