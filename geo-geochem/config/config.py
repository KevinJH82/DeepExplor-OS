"""geo-geochem 地球化学异常处理系统 - 配置

硬约束同 geo-geophys / geo-model3d：上游根用真实路径并支持 env 覆盖，broker 调用显式传入。
"""

import os


class Config:
    HOST = '0.0.0.0'
    PORT = 8088  # 8087 已被 geo-geophys 占用
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY', 'geo-geochem-secret-key-2024')

    MAX_CONTENT_LENGTH = 512 * 1024 * 1024  # 512MB（化探点位 CSV 一般不大）

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))     # .../geo-geochem
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
    GEO_ANALYSER_OUTPUTS = os.environ.get(
        'GEO_ANALYSER_OUTPUTS', os.path.join(REPO_DIR, 'geo-analyser', 'results'))
    # 用于导入 prospector 的 mineral_kb（取矿种 key_elements）
    PROSPECTOR_SRC = os.environ.get(
        'PROSPECTOR_SRC', os.path.join(REPO_DIR, 'data-colle', 'prospector'))

    # ── 处理默认参数 ──
    GRID_RES_M = float(os.environ.get('GEOCHEM_GRID_RES_M', 100.0))   # 水平网格分辨率(米)
    GRID_MAX_CELLS = int(os.environ.get('GEOCHEM_MAX_CELLS', 4_000_000))
    IDW_POWER = float(os.environ.get('GEOCHEM_IDW_POWER', 2.0))       # 反距离加权指数
    IDW_K = int(os.environ.get('GEOCHEM_IDW_K', 12))                  # 最近邻点数
    # C-A 分形异常下限：搜索分段拐点；点太少时回退百分位
    CA_FALLBACK_PCT = float(os.environ.get('GEOCHEM_CA_FALLBACK_PCT', 85.0))

    # ── 公开化探数据 broker（注册表/预置式，数据源无关）──
    # broker 不联网取数，只发现已落地到本目录的公开化探点位数据集（注册表 index.json）。
    # 海外开放集（USGS NGDB / 澳洲 NGSA / 欧洲 FOREGS·GEMAS 等）可脚本化入库；
    # 中国 RGNR/CGB 点位级数据不开放、需用户合法获取后按格式放入即自动生效。
    PUBLIC_GEOCHEM_ENABLED = os.environ.get(
        'PUBLIC_GEOCHEM_ENABLED', '1').strip().lower() not in ('0', 'false', 'no', '')
    PUBLIC_GEOCHEM_ROOT = os.environ.get(
        'PUBLIC_GEOCHEM_ROOT', os.path.join(BASE_DIR, 'data', 'public_geochem'))

    # 一键串联：geo-model3d 服务地址
    MODEL3D_URL = os.environ.get('MODEL3D_URL', 'http://127.0.0.1:8086')

    # 矿种 → 关键指示元素（导入 mineral_kb 失败时的兜底）
    FALLBACK_KEY_ELEMENTS = {
        "铜": ["Cu", "Mo", "Au", "Ag", "Pb", "Zn", "As", "Sb"],
        "铜钼": ["Cu", "Mo", "Au", "Ag", "Pb", "Zn", "W", "Bi"],
        "钼": ["Mo", "Cu", "W", "Bi", "Pb", "Zn", "Ag"],
        "金": ["Au", "As", "Sb", "Hg", "Ag", "Cu", "Pb", "Zn", "W", "Bi"],
        "银": ["Ag", "Pb", "Zn", "Cu", "As", "Sb", "Au"],
        "铅锌": ["Pb", "Zn", "Ag", "Cu", "Cd", "As", "Sb", "Ba"],
        "铁": ["Fe", "Mn", "Ti", "V", "Co", "Ni", "Cu"],
        "镍": ["Ni", "Cu", "Co", "Cr", "Pt", "Pd"],
        "铬": ["Cr", "Ni", "Co", "Mg", "Pt"],
        "钨": ["W", "Sn", "Mo", "Bi", "Cu", "As", "F"],
        "锡": ["Sn", "W", "Cu", "As", "Pb", "Zn", "F"],
        "钨锡": ["W", "Sn", "Mo", "Bi", "Cu", "As", "F"],
        "锂": ["Li", "Be", "Nb", "Ta", "Cs", "Rb", "Sn"],
        "稀土": ["La", "Ce", "Y", "Nb", "Th", "P"],
        "铀": ["U", "Th", "Mo", "V", "Pb", "Ra"],
        "锰": ["Mn", "Fe", "Co", "Ba", "Pb", "Zn"],
    }

    # 原生晕轴向分带序列（Grigorian）——前缘晕/近矿晕/尾缘晕（P2 用，P1 先登记）
    HALO_ZONATION = {
        "front": ["As", "Sb", "Hg", "B", "Ba", "I", "F"],          # 前缘晕（头晕）
        "near_ore": ["Cu", "Pb", "Zn", "Ag", "Au", "Cd"],         # 近矿晕
        "tail": ["Bi", "Mo", "Mn", "W", "Sn", "Co", "Ni", "V"],   # 尾缘晕
    }

    @staticmethod
    def key_elements_for(mineral: str) -> list:
        """取矿种关键指示元素：优先 prospector mineral_kb，失败回退内置表。"""
        m = (mineral or "").strip()
        try:
            import sys
            src = os.path.join(Config.PROSPECTOR_SRC, 'src')
            if src not in sys.path:
                sys.path.insert(0, src)
            from mineral_kb import get_mineral_info  # type: ignore
            info = get_mineral_info(m) or {}
            els = info.get("all_key_elements") or []
            if els:
                return list(els)
        except Exception:
            pass
        return list(Config.FALLBACK_KEY_ELEMENTS.get(m, ["Cu", "Pb", "Zn", "Au", "Ag", "As", "Sb", "Mo"]))

    @staticmethod
    def upstream_roots():
        return {'datacolle': Config.DATACOLLE_OUTPUTS, 'analyser': Config.GEO_ANALYSER_OUTPUTS}

    @staticmethod
    def create_directories():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        os.makedirs(Config.RESULTS_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)
