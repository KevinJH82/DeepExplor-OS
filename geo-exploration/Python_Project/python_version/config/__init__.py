"""
配置管理模块

从 config.py 导入所有配置项，保持向后兼容性
"""

from .config import Config

# 导出常用的配置
__all__ = [
    'Config',
    'MINERAL_TYPES',
    'DETECTORS',
    'ALGORITHM',
    'VISUALIZATION',
    'PERFORMANCE',
    'LOGGING'
]

# 为了保持向后兼容，也可以直接导入
MINERAL_TYPES = Config.MINERAL_TYPES
DETECTORS = Config.DETECTORS
ALGORITHM = Config.ALGORITHM
VISUALIZATION = Config.VISUALIZATION
PERFORMANCE = Config.PERFORMANCE
LOGGING = Config.LOGGING