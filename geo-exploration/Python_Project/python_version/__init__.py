"""
舒曼波共振遥感矿产预测系统

基于 MATLAB 代码转换的 Python 实现，保持原有算法逻辑的同时，
利用 Python 生态系统提升性能和可扩展性。
"""

__version__ = "1.0.0"
__author__ = "Mineral Analysis Team"
__email__ = "your-email@example.com"

from .core.fusion_engine import FusionEngine
from .core.geo_data_context import GeoDataContext
from .core.post_processor import PostProcessor
from .detectors.base_detector import BaseDetector
from .utils.geo_utils import GeoUtils

__all__ = [
    'FusionEngine',
    'GeoDataContext',
    'PostProcessor',
    'BaseDetector',
    'GeoUtils'
]