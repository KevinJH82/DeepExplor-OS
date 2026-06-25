"""geo-orchestrator 智能编排引擎 - 配置"""

import os

# 从服务根 .env 读取密钥(无 python-dotenv 依赖,导入即注入 os.environ,持久且重启不丢)
_envf = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
if os.path.exists(_envf):
    with open(_envf, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())


class Config:
    HOST = '0.0.0.0'
    PORT = 8090
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY', 'geo-orchestrator-secret-key-2026')

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))
    REPO_DIR = os.path.dirname(BASE_DIR)

    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    RESULTS_FOLDER = os.environ.get('RESULTS_ROOT', os.path.join(BASE_DIR, 'results'))
    TEMP_FOLDER = os.path.join(UPLOAD_FOLDER, 'temp')

    LOG_LEVEL = 'INFO'
    LOG_FILE = os.path.join(BASE_DIR, 'logs', 'app.log')

    # ── 上游服务地址 ──
    GEO_DOWNLOADER_URL = os.environ.get('GEO_DOWNLOADER_URL', 'http://127.0.0.1:8080')
    # 注意：geo-analyser 实测监听 5001（与 reporter 的 8081 区分）；如部署改端口用 env 覆盖
    GEO_ANALYSER_URL = os.environ.get('GEO_ANALYSER_URL', 'http://127.0.0.1:5001')
    GEO_STRU_URL = os.environ.get('GEO_STRU_URL', 'http://127.0.0.1:8082')
    GEO_EXPLORATION_URL = os.environ.get('GEO_EXPLORATION_URL', 'http://127.0.0.1:8083')
    GEO_INSAR_URL = os.environ.get('GEO_INSAR_URL', 'http://127.0.0.1:8084')
    DATA_COLLE_URL = os.environ.get('DATA_COLLE_URL', 'http://127.0.0.1:8085')
    GEO_MODEL3D_URL = os.environ.get('GEO_MODEL3D_URL', 'http://127.0.0.1:8086')
    GEO_GEOPHYS_URL = os.environ.get('GEO_GEOPHYS_URL', 'http://127.0.0.1:8087')
    GEO_GEOCHEM_URL = os.environ.get('GEO_GEOCHEM_URL', 'http://127.0.0.1:8088')
    GEO_DRILL_URL = os.environ.get('GEO_DRILL_URL', 'http://127.0.0.1:8089')
    GEO_REPORTER_URL = os.environ.get('GEO_REPORTER_URL', 'http://127.0.0.1:8081')
    GEO_7SLOW_URL = os.environ.get('GEO_7SLOW_URL', 'http://127.0.0.1:8001')

    # ── 上游服务 results 根路径（broker 扫描用）──
    GEO_DOWNLOADER_DOWNLOADS = os.environ.get(
        'GEO_DOWNLOADER_DOWNLOADS', os.path.join(REPO_DIR, 'geo-downloader', 'downloads'))
    GEO_ANALYSER_OUTPUTS = os.environ.get(
        'GEO_ANALYSER_OUTPUTS', os.path.join(REPO_DIR, 'geo-analyser', 'results'))
    GEO_STRU_OUTPUTS = os.environ.get(
        'GEO_STRU_OUTPUTS', os.path.join(REPO_DIR, 'geo-stru', 'results'))
    GEO_EXPLORATION_OUTPUTS = os.environ.get(
        'GEO_EXPLORATION_OUTPUTS',
        os.path.join(REPO_DIR, 'geo-exploration', 'Python_Project', 'web_app', 'uploads'))
    GEO_INSAR_DOWNLOADS = os.environ.get(
        'GEO_INSAR_DOWNLOADS', os.path.join(REPO_DIR, 'geo-insar', 'downloads'))
    DATACOLLE_OUTPUTS = os.environ.get(
        'DATACOLLE_OUTPUTS', os.path.join(REPO_DIR, 'data-colle', 'prospector', 'output'))
    GEO_MODEL3D_OUTPUTS = os.environ.get(
        'GEO_MODEL3D_OUTPUTS', os.path.join(REPO_DIR, 'geo-model3d', 'results'))
    GEO_GEOPHYS_OUTPUTS = os.environ.get(
        'GEO_GEOPHYS_OUTPUTS', os.path.join(REPO_DIR, 'geo-geophys', 'results'))
    GEO_GEOCHEM_OUTPUTS = os.environ.get(
        'GEO_GEOCHEM_OUTPUTS', os.path.join(REPO_DIR, 'geo-geochem', 'results'))
    GEO_DRILL_OUTPUTS = os.environ.get(
        'GEO_DRILL_OUTPUTS', os.path.join(REPO_DIR, 'geo-drill', 'results'))
    GEO_SLOWVARS_OUTPUTS = os.environ.get(
        'GEO_SLOWVARS_OUTPUTS', os.path.join(REPO_DIR, 'geo-7slow', 'backend', 'data', 'results'))

    # ── InSAR 有界等待 ──
    # geo-insar 在阶段一提交后异步处理（HyP3 云端，慢）；进入 geo-model3d 前最多等这么久，
    # 超时则按 P3 降级（model3d 不含形变层）。默认 30 分钟，按实际 HyP3 时延用 env 调整。
    INSAR_WAIT_TIMEOUT = int(os.environ.get('INSAR_WAIT_TIMEOUT', '1800'))

    # ── InSAR 配对/基线策略（阶段一提交 geo-insar 时下发，覆盖其服务端默认）──
    INSAR_PAIR_STRATEGY = os.environ.get('INSAR_PAIR_STRATEGY', 'cascade')
    INSAR_MAX_TEMPORAL_BASELINE_DAYS = int(os.environ.get('INSAR_MAX_TEMPORAL_BASELINE_DAYS', '24'))
    INSAR_MAX_PERP_BASELINE_M = float(os.environ.get('INSAR_MAX_PERP_BASELINE_M', '150'))

    # ── LLM 配置 ──
    LLM_MODEL = os.environ.get('ORCHESTRATOR_LLM_MODEL', 'deepseek-v4-flash')
    LLM_BASE_URL = os.environ.get('ORCHESTRATOR_LLM_BASE_URL', 'https://api.deepseek.com')
    LLM_TIMEOUT = 60
    LLM_MAX_TOKENS = 4000

    @staticmethod
    def create_directories():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        os.makedirs(Config.RESULTS_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)

    @staticmethod
    def upstream_roots():
        return {
            'downloader': Config.GEO_DOWNLOADER_DOWNLOADS,
            'analyser': Config.GEO_ANALYSER_OUTPUTS,
            'stru': Config.GEO_STRU_OUTPUTS,
            'exploration': Config.GEO_EXPLORATION_OUTPUTS,
            'insar': Config.GEO_INSAR_DOWNLOADS,
            'datacolle': Config.DATACOLLE_OUTPUTS,
            'model3d': Config.GEO_MODEL3D_OUTPUTS,
            'geophys': Config.GEO_GEOPHYS_OUTPUTS,
            'geochem': Config.GEO_GEOCHEM_OUTPUTS,
            'drill': Config.GEO_DRILL_OUTPUTS,
            'slowvars': Config.GEO_SLOWVARS_OUTPUTS,
        }
