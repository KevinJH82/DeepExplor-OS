"""geo-model3d 三维地质建模与立体成矿预测 - 配置

注意(硬约束)：commons 各 broker 的 DEFAULT_* 指向 /opt/deepexplor-services（本仓库不存在），
故此处显式给真实根路径并支持 env 覆盖，调用 find_*_for_bbox 时必须显式传入。
"""

import os


class Config:
    HOST = '0.0.0.0'
    PORT = 8086  # 8085 已被 data-colle/prospector 占用
    DEBUG = True
    SECRET_KEY = os.environ.get('SECRET_KEY', 'geo-model3d-secret-key-2024')

    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))                    # .../geo-model3d
    REPO_DIR = os.path.dirname(BASE_DIR)                                     # .../deepexplor-services
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    TEMP_FOLDER = os.path.join(UPLOAD_FOLDER, 'temp')
    RESULTS_FOLDER = os.environ.get('RESULTS_ROOT', os.path.join(BASE_DIR, 'results'))

    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_FILE = os.path.join(BASE_DIR, 'logs', 'app.log')

    # ── 上游服务 results 根（真实路径，env 可覆盖；显式传入各 broker）──
    GEO_ANALYSER_OUTPUTS = os.environ.get(
        'GEO_ANALYSER_OUTPUTS', os.path.join(REPO_DIR, 'geo-analyser', 'results'))
    GEO_STRU_OUTPUTS = os.environ.get(
        'GEO_STRU_OUTPUTS', os.path.join(REPO_DIR, 'geo-stru', 'results'))
    GEO_EXPLORATION_OUTPUTS = os.environ.get(
        'GEO_EXPLORATION_OUTPUTS',
        os.path.join(REPO_DIR, 'geo-exploration', 'Python_Project', 'web_app', 'uploads'))
    GEO_GEOPHYS_OUTPUTS = os.environ.get(
        'GEO_GEOPHYS_OUTPUTS', os.path.join(REPO_DIR, 'geo-geophys', 'results'))
    GEO_GEOCHEM_OUTPUTS = os.environ.get(
        'GEO_GEOCHEM_OUTPUTS', os.path.join(REPO_DIR, 'geo-geochem', 'results'))
    GEO_INSAR_OUTPUTS = os.environ.get(
        'GEO_INSAR_OUTPUTS', os.path.join(REPO_DIR, 'geo-insar', 'downloads'))
    GEO_SLOWVARS_OUTPUTS = os.environ.get(
        'GEO_SLOWVARS_OUTPUTS', os.path.join(REPO_DIR, 'geo-7slow', 'backend', 'data', 'results'))

    # ── 体元网格默认参数 ──
    GRID_RES_M = float(os.environ.get('MODEL3D_GRID_RES_M', 30.0))   # 水平分辨率(米)
    GRID_ZMAX_M = float(os.environ.get('MODEL3D_ZMAX_M', 2000.0))    # 地表下最大深度(米)
    GRID_DZ_M = float(os.environ.get('MODEL3D_DZ_M', 100.0))         # 深度步长(米)
    GRID_MAX_CELLS = int(os.environ.get('MODEL3D_MAX_CELLS', 8_000_000))  # 体元总数上限(防爆内存)

    # ── P2 特性A：三维构造几何（断裂倾向投影骨架）──
    # 断裂默认倾角(度)；深部按 offset=depth/tan(dip) 沿倾向横移 2D 构造有利度。
    STRUCT_DIP_DEG = float(os.environ.get('MODEL3D_STRUCT_DIP_DEG', 80.0))

    # ── P2 特性B：2D 证据融合方法选择 ──
    # knowledge=加权融合(默认,与历史一致) | fuzzy=模糊γ算子 | bayesian=贝叶斯后验
    FUSION_METHOD = os.environ.get('MODEL3D_FUSION_METHOD', 'knowledge')
    FUZZY_GAMMA = float(os.environ.get('MODEL3D_FUZZY_GAMMA', 0.9))   # 模糊γ算子参数

    # ── 方向二：物探速度反演体作真三维证据的融合占比（仅在有覆盖处生效）──
    VELOCITY_BETA = float(os.environ.get('MODEL3D_VELOCITY_BETA', 0.35))

    # ── P2 特性C：三维 Web 查看器 ──
    VIEWER_MAX_POINTS = int(os.environ.get('MODEL3D_VIEWER_MAX_POINTS', 60000))  # 点云上限(控文件大小)
    VIEWER_VEXAG = float(os.environ.get('MODEL3D_VIEWER_VEXAG', 3.0))            # 垂向夸大倍数

    # ── 方向四：数据驱动成矿预测（已知矿点标签）──
    # 已知矿点(MRDS+上传)≥ LABEL_MIN 才启用数据驱动 WofE，否则诚实回退知识融合。
    LABEL_MIN = int(os.environ.get('MODEL3D_LABEL_MIN', 8))
    DEPOSITS_CACHE_DIR = os.environ.get(
        'MODEL3D_DEPOSITS_CACHE', os.path.join(BASE_DIR, 'data', 'deposits_cache'))
    # 本地已知矿点库：放置任意区域(含中国)的真实矿点 GeoJSON/CSV，按 bbox+矿种自动取正样本。
    # MRDS 仅覆盖美国；中国等区域用此库补充，避免每次手动上传。
    LOCAL_DEPOSITS_DIR = os.environ.get(
        'MODEL3D_LOCAL_DEPOSITS', os.path.join(BASE_DIR, 'data', 'deposits_library'))
    # P4 跨区迁移：源模型 registry（数据富矿区训练的模型，供数据稀缺新区域自适应调用）
    MODEL_REGISTRY_DIR = os.environ.get(
        'MODEL3D_MODEL_REGISTRY', os.path.join(BASE_DIR, 'data', 'model_registry'))

    # ── HydroSHEDS 公开水系数据（HydroRIVERS 河网矢量）──
    HYDROSHEDS_DIR = os.environ.get(
        'MODEL3D_HYDROSHEDS_DIR', os.path.join(BASE_DIR, 'data', 'hydrosheds'))
    # 已知矿点层 broker 根（geo-model3d 自身 results，deposits 子目录）
    GEO_DEPOSITS_OUTPUTS = os.environ.get('GEO_DEPOSITS_OUTPUTS', RESULTS_FOLDER)

    @staticmethod
    def upstream_roots():
        """返回供 ingest 使用的上游根路径包。"""
        return {
            'analyser': Config.GEO_ANALYSER_OUTPUTS,
            'stru': Config.GEO_STRU_OUTPUTS,
            'exploration': Config.GEO_EXPLORATION_OUTPUTS,
            'geophys': Config.GEO_GEOPHYS_OUTPUTS,
            'geochem': Config.GEO_GEOCHEM_OUTPUTS,
            'insar': Config.GEO_INSAR_OUTPUTS,
            'slowvars': Config.GEO_SLOWVARS_OUTPUTS,
            'deposits': Config.GEO_DEPOSITS_OUTPUTS,
            'deposits_library': Config.LOCAL_DEPOSITS_DIR,
        }

    @staticmethod
    def create_directories():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        os.makedirs(Config.RESULTS_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)
