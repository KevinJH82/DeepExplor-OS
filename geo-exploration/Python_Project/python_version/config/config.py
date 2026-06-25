"""
系统配置文件
"""

import os
from pathlib import Path
from typing import Dict, Any
import yaml

class Config:
    """系统配置类"""

    # 根目录
    ROOT_DIR = Path(__file__).parent.parent

    # 数据路径配置
    DATA_DIR = ROOT_DIR / "data"
    RESULTS_DIR = ROOT_DIR / "results"
    OUTPUT_DIR = ROOT_DIR / "output"
    LOGS_DIR = ROOT_DIR / "logs"
    TEMP_DIR = ROOT_DIR / "temp"

    # 遥感数据配置
    REMOTE_SENSING = {
        'sentinel2_bands': {
            'B4': 0,  # Red
            'B5': 1,  # Red Edge 1
            'B6': 2,  # Red Edge 2
            'B7': 3,  # Red Edge 3
            'B8': 4,  # NIR
            'B11': 5, # SWIR 1
            'B12': 6  # SWIR 2
        },
        'landsat8_bands': {
            'B4': 0,  # Red
            'B5': 1,  # NIR
            'B6': 2,  # SWIR 1
            'B7': 3   # SWIR 2
        },
        'aster_bands': {
            'B1': 0,  # VNIR 1
            'B2': 1,  # VNIR 2
            'B3': 2,  # VNIR 3
            'B4': 3,  # SWIR 1
            'B5': 4,  # SWIR 2
            'B6': 5,  # SWIR 3
            'B7': 6,  # TIR 1
            'B8': 7,  # TIR 2
            'B9': 8,  # TIR 3
        }
    }

    # 矿物类型配置
    MINERAL_TYPES = {
        'gold': {
            'name': '黄金',
            'color': '#FFD700',
            'icon': '🥇',
            'intrinsic_bands': [(3, 3), (5, 5)],  # ASTER band index, role
            's2rep_center': 705,
            'yakymchuk_params': {'a': 10, 'b': 20, 'c': 0.1},
            'color_scheme': 'YlOrRd'
        },
        'copper': {
            'name': '铜矿',
            'color': '#B87333',
            'icon': '🟫',
            'intrinsic_bands': [(4, 4), (6, 6)],
            's2rep_center': 720,
            'yakymchuk_params': {'a': 15, 'b': 25, 'c': 0.12},
            'color_scheme': 'PuBu'
        },
        'iron': {
            'name': '铁矿',
            'color': '#696969',
            'icon': '⚫',
            'intrinsic_bands': [(4, 4)],
            's2rep_center': 710,
            'yakymchuk_params': {'a': 12, 'b': 22, 'c': 0.11},
            'color_scheme': 'Greys'
        },
        'lead': {
            'name': '铅矿',
            'color': '#708090',
            'icon': '🔘',
            'intrinsic_bands': [(5, 5)],
            's2rep_center': 715,
            'yakymchuk_params': {'a': 14, 'b': 24, 'c': 0.115},
            'color_scheme': 'Blues'
        },
        'zinc': {
            'name': '锌矿',
            'color': '#4682B4',
            'icon': '🔵',
            'intrinsic_bands': [(4, 4), (6, 6)],
            's2rep_center': 718,
            'yakymchuk_params': {'a': 13, 'b': 23, 'c': 0.112},
            'color_scheme': 'viridis'
        },
        'coal': {
            'name': '煤炭',
            'color': '#2F4F4F',
            'icon': '⚫',
            'intrinsic_bands': [(6, 6)],
            's2rep_center': 725,
            'yakymchuk_params': {'a': 16, 'b': 26, 'c': 0.13},
            'color_scheme': 'binary'
        },
        'petroleum': {
            'name': '石油',
            'color': '#000000',
            'icon': '⚫',
            'intrinsic_bands': [(7, 7)],
            's2rep_center': 730,
            'yakymchuk_params': {'a': 18, 'b': 28, 'c': 0.14},
            'color_scheme': 'hot'
        },
        'gas': {
            'name': '天然气',
            'color': '#87CEEB',
            'icon': '💨',
            'intrinsic_bands': [(7, 7)],
            's2rep_center': 728,
            'yakymchuk_params': {'a': 17, 'b': 27, 'c': 0.135},
            'color_scheme': 'cool'
        },
        'rare_earth': {
            'name': '稀土',
            'color': '#9370DB',
            'icon': '💜',
            'intrinsic_bands': [(4, 4), (6, 6), (7, 7)],
            's2rep_center': 722,
            'yakymchuk_params': {'a': 19, 'b': 29, 'c': 0.145},
            'color_scheme': 'magma'
        },
        'lithium': {
            'name': '锂矿',
            'color': '#FF1493',
            'icon': '💗',
            'intrinsic_bands': [(3, 3), (7, 7)],
            's2rep_center': 712,
            'yakymchuk_params': {'a': 11, 'b': 21, 'c': 0.105},
            'color_scheme': 'plasma'
        }
    }

    # 探测器配置
    DETECTORS = {
        'red_edge': {
            'name': 'RedEdge',
            'description': '红边异常检测器',
            'class_name': 'RedEdgeDetector',
            'default': True,
            'parameters': {
                'window_size': 3,
                'threshold_factor': 1.0,
                'moran_window': 3,
                'levashov_mode': True
            }
        },
        'intrinsic': {
            'name': 'Intrinsic',
            'description': '本征吸收检测器',
            'class_name': 'IntrinsicDetector',
            'default': True,
            'parameters': {
                'weight_ratio': [0.6, 0.4],  # 吸收强度 : 空间聚集度
                'gaussian_sigma': 4,
                'continuous_mode': True
            }
        },
        'slow_vars': {
            'name': 'SlowVars',
            'description': '慢变量检测器',
            'class_name': 'SlowVarsDetector',
            'default': False,
            'parameters': {
                'factors': ['stress', 'redox', 'pressure', 'fracture'],
                'z_score_threshold': 2.5,
                'equation_order': 3
            }
        },
        'known_anomaly': {
            'name': 'KnownAnomaly',
            'description': '已知异常检测器',
            'class_name': 'KnownAnomalyDetector',
            'default': False,
            'parameters': {
                'keywords': ['矿体投影', 'Object ID', 'ZK', '异常', '已知矿点'],
                'correction_method': 'geometric',
                'buffer_radius': 100
            }
        }
    }

    # 算法参数配置
    ALGORITHM = {
        's2rep_center': 705,
        'moran_window_size': 3,
        'gaussian_sigma': 4,
        'fusion_threshold': 0.6,
        'depth_unit': 'km',
        'pressure_unit': 'MPa',
        'nan_value': -9999,
        'eps_value': 1e-6
    }

    # 可视化配置
    VISUALIZATION = {
        'dpi': 300,
        'figure_size': (12, 8),
        'colorbar_shrink': 0.8,
        'contour_levels': 20,
        'alpha': 0.7,
        'line_width': 0.5,
        'font_size': 12
    }

    # 性能配置
    PERFORMANCE = {
        'n_jobs': -1,  # 使用所有CPU核心
        'chunk_size': 1024,
        'max_memory_gb': 8,
        'use_multiprocessing': True,
        'parallel_backend': 'threading'
    }

    # 日志配置
    LOGGING = {
        'level': 'INFO',
        'format': '{time:YYYY-MM-DD HH:mm:ss} | {level} | {name} | {message}',
        'rotation': '10 MB',
        'retention': '7 days',
        'log_file': LOGS_DIR / 'mineral_analysis.log'
    }

    @classmethod
    def create_directories(cls):
        """创建必要的目录"""
        directories = [
            cls.DATA_DIR,
            cls.RESULTS_DIR,
            cls.OUTPUT_DIR,
            cls.LOGS_DIR,
            cls.TEMP_DIR
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_detector_config(cls, detector_name: str) -> Dict[str, Any]:
        """获取探测器配置"""
        return cls.DETECTORS.get(detector_name, {})

    @classmethod
    def get_mineral_config(cls, mineral_name: str) -> Dict[str, Any]:
        """获取矿物配置"""
        return cls.MINERAL_TYPES.get(mineral_name, {})

    @classmethod
    def load_yaml_config(cls, config_path: str) -> Dict[str, Any]:
        """加载 YAML 配置文件"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    @classmethod
    def save_yaml_config(cls, config: Dict[str, Any], config_path: str):
        """保存配置到 YAML 文件"""
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)