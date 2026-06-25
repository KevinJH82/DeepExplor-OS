"""
探测器模块

包含各种异常检测器的实现：
- RedEdgeDetector: 红边异常检测
- IntrinsicDetector: 本征吸收检测
- SlowVarsDetector: 慢变量检测
- KnownAnomalyDetector: 已知异常检测
"""

from .red_edge_detector import RedEdgeDetector
from .intrinsic_detector import IntrinsicDetector
from .slow_vars_detector import SlowVarsDetector
from .known_anomaly_detector import KnownAnomalyDetector

__all__ = [
    'RedEdgeDetector',
    'IntrinsicDetector',
    'SlowVarsDetector',
    'KnownAnomalyDetector'
]