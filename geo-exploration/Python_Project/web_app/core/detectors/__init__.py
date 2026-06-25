"""
探测器模块
"""

from .base_detector import GeoDetectorBase, DetectorResult
from .red_edge_detector import RedEdgeDetector
from .intrinsic_detector import IntrinsicDetector
from .slow_vars_detector import SlowVarsDetector

__all__ = [
    'GeoDetectorBase',
    'DetectorResult',
    'RedEdgeDetector',
    'IntrinsicDetector',
    'SlowVarsDetector'
]
