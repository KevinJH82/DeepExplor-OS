"""
舒曼波共振遥感矿产预测系统 - 配置文件
"""

import os

class Config:
    """应用配置类"""

    # 服务器配置
    HOST = '0.0.0.0'
    PORT = 8083
    DEBUG = True
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mineral-analysis-secret-key-2024')

    # 文件上传配置
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
    TEMP_FOLDER = os.path.join(UPLOAD_FOLDER, 'temp')

    # 结果存储配置
    RESULTS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'results')

    # 矿物类型配置
    MINERAL_TYPES = {
        'gold': {'name': '黄金', 'color': '#FFD700', 'icon': '🥇'},
        'copper': {'name': '铜矿', 'color': '#B87333', 'icon': '🟫'},
        'cave': {'name': '洞穴', 'color': '#8B4513', 'icon': '🕳️'},
        'iron': {'name': '铁矿', 'color': '#696969', 'icon': '⚫'},
        'lead': {'name': '铅矿', 'color': '#708090', 'icon': '🔘'},
        'zinc': {'name': '锌矿', 'color': '#4682B4', 'icon': '🔵'},
        'coal': {'name': '煤炭', 'color': '#2F4F4F', 'icon': '⚫'},
        'petroleum': {'name': '石油', 'color': '#000000', 'icon': '⚫'},
        'gas': {'name': '天然气', 'color': '#87CEEB', 'icon': '💨'},
        'rare_earth': {'name': '稀土', 'color': '#9370DB', 'icon': '💜'},
        'lithium': {'name': '锂矿', 'color': '#FF1493', 'icon': '💗'}
    }

    # 探测器配置
    DETECTORS = {
        'red_edge': {
            'name': 'RedEdge (红边)',
            'description': '基于红边位置偏移和 Moran I 空间自相关',
            'icon': '🔴',
            'default': True
        },
        'intrinsic': {
            'name': 'Intrinsic (本征吸收)',
            'description': '基于矿物特征光谱吸收',
            'icon': '🟡',
            'default': True
        },
        'slow_vars': {
            'name': 'SlowVars (慢变量)',
            'description': '地应力、氧化还原、流体超压等多因素综合',
            'icon': '🟢',
            'default': False
        },
        'known_anomaly': {
            'name': 'KnownAnomaly (KML)',
            'description': '集成 KML/KMZ 已知异常数据',
            'icon': '🔵',
            'default': False
        }
    }

    # 可视化配置
    VISUALIZATION = {
        'width': 1200,
        'height': 800,
        'color_schemes': {
            'gold': 'YlOrRd',
            'copper': 'PuBu',
            'iron': 'Greys',
            'coal': 'binary',
            'petroleum': 'hot',
            'gas': 'cool'
        }
    }

    # 日志配置
    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'app.log')

    # 算法参数配置
    ALGORITHM = {
        's2rep_center': 705,
        'moran_window_size': 3,
        'gaussian_sigma': 4,
        'fusion_threshold': 0.6,
        'depth_unit': 'km',
        'pressure_unit': 'MPa'
    }

    # 蚀变接入(geo-analyser):A 旁证重排 + B 地表项升级。
    # 默认全关 —— 不装 geo-analyser / 无 results 时系统行为零变更。
    ALTERATION = {
        'enabled': False,
        'mode_A_rerank': True,        # A: 一致性叠层 + Top-20 重排
        'mode_B_surface': True,       # B: 用蚀变图升级地表潜力糙代理
        'results_root': os.environ.get('GEO_ANALYSER_RESULTS',
                                       '/opt/deepexplor-services/geo-analyser/results'),
        'explicit_run_id': None,      # 显式指定 run 则跳过自动匹配
        'min_roi_overlap': 0.15,      # ROI 空间重叠下限(IoU-like)
        'consistency_weight': 0.25,   # A: 重排时蚀变佐证权重
        'min_mineral_coverage': 0.30, # B: 某代理 ROI 内覆盖率下限,否则回退糙代理
        'min_run_pixels': 2000,       # 稀疏度门控:ROI 像素下限
        'min_high_conf_frac': 0.001,  # 稀疏度门控:高置信异常占比下限
    }

    # 交付库自动取数:未上传数据目录时,按 ROI 空间重叠从交付库匹配项目并定位季节数据目录。
    # 上传了 data_dir(zip)则优先用上传(后备/显式)。
    DELIVERY = {
        'enabled': True,
        'root': os.environ.get('DELIVERY_ROOT',
                               '/Volumes/大硬盘可劲用/DeepExplor/数据留存/交付数据'),
        'season': 'winter',          # winter | summer | auto
        'min_roi_overlap': 0.10,     # ROI 空间重叠下限
    }

    # 构造接入(geo-stru):D 部分,重定位到深部慢变量 fault_activity。
    # 默认关 —— 无 structural 产物时维持原 Canny-only fault_activity。
    STRUCTURAL = {
        'enabled': False,
        'inject_into_faultactivity': True,  # 进深部 slow_vars(因果正确的家)
        'structural_in_surface': False,     # 撤地表乘子防双计(原行为=True)
        'results_root': os.environ.get('GEO_STRU_RESULTS',
                                       '/opt/deepexplor-services/geo-stru/results'),
        'auto_discover': True,              # enabled 时自动从 results_root 按 ROI 匹配 geo-stru run
        'min_roi_overlap': 0.15,            # 跨系统匹配的 ROI 空间重叠下限
        'lineament_weight': 0.5,            # fault_activity 注入权重(对标 InSAR)
        'structural_weight': 0.12,          # 仅 structural_in_surface=True 时用
        'min_lineaments': 1,                # 稀疏门控:n_lineaments < 此值则跳过
    }

    @staticmethod
    def create_directories():
        """创建必要的目录"""
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        os.makedirs(Config.RESULTS_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)