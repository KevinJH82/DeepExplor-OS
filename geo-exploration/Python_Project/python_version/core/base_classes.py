"""
基础类定义

包含探测器基类和结果类等通用定义
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np


@dataclass
class DetectorResult:
    """探测器结果类"""
    mask: np.ndarray  # 异常强度掩码
    debug_data: Optional[Dict[str, Any]] = None  # 调试数据
    metadata: Optional[Dict[str, Any]] = None  # 元数据


class BaseDetector(ABC):
    """
    探测器基类

    所有具体的探测器都应该继承此类并实现 calculate 方法
    """

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        """
        初始化探测器

        Args:
            name: 探测器名称
            params: 探测器参数
        """
        self.name = name
        self.params = params or {}
        self.logger = None

    @abstractmethod
    def calculate(self, context: 'GeoDataContext') -> DetectorResult:
        """
        计算异常检测结果

        Args:
            context: 地理数据上下文

        Returns:
            DetectorResult: 检测结果
        """
        pass

    def validate_input(self, context: 'GeoDataContext') -> bool:
        """
        验证输入数据

        Args:
            context: 地理数据上下文

        Returns:
            bool: 验证是否通过
        """
        # 检查必要的数据
        required_attrs = ['s2_data', 'ast_data', 'inROI', 'lonGrid', 'latGrid']

        for attr in required_attrs:
            if not hasattr(context, attr):
                raise ValueError(f"缺少必要的数据属性: {attr}")

        # 检查 ROI
        if not np.any(context.inROI):
            raise ValueError("ROI 区域为空")

        return True

    def get_params(self) -> Dict[str, Any]:
        """获取探测器参数"""
        return self.params

    def update_params(self, new_params: Dict[str, Any]):
        """更新探测器参数"""
        self.params.update(new_params)

    def get_detector_info(self) -> Dict[str, Any]:
        """获取探测器信息"""
        return {
            'name': self.name,
            'class': self.__class__.__name__,
            'params': self.params,
            'description': self.__doc__ or ""
        }


class BaseProcessor(ABC):
    """基础处理器类"""

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        self.name = name
        self.params = params or {}
        self.logger = None

    @abstractmethod
    def process(self, data: Any) -> Any:
        """
        处理数据

        Args:
            data: 输入数据

        Returns:
            处理后的数据
        """
        pass


@dataclass
class AnalysisTask:
    """分析任务数据类"""
    id: str
    mineral_type: str
    detectors: list
    config: Dict[str, Any]
    start_time: str
    end_time: Optional[str] = None
    status: str = "pending"  # pending, running, completed, failed
    results: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: float = 0.0


class TaskManager:
    """任务管理器"""

    def __init__(self):
        self.tasks = {}
        self.task_counter = 0

    def create_task(self, mineral_type: str, detectors: list,
                   config: Dict[str, Any]) -> str:
        """创建新任务"""
        self.task_counter += 1
        task_id = f"task_{self.task_counter:04d}"

        task = AnalysisTask(
            id=task_id,
            mineral_type=mineral_type,
            detectors=detectors,
            config=config,
            start_time=self._get_current_time()
        )

        self.tasks[task_id] = task
        return task_id

    def update_task_status(self, task_id: str, status: str,
                          progress: Optional[float] = None):
        """更新任务状态"""
        if task_id in self.tasks:
            self.tasks[task_id].status = status
            if progress is not None:
                self.tasks[task_id].progress = progress
            if status in ['completed', 'failed']:
                self.tasks[task_id].end_time = self._get_current_time()

    def get_task(self, task_id: str) -> Optional[AnalysisTask]:
        """获取任务信息"""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> Dict[str, AnalysisTask]:
        """获取所有任务"""
        return self.tasks.copy()

    def _get_current_time(self) -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def numpy_compatible(cls):
    """
    装饰器，为类添加 NumPy 兼容方法
    """
    cls.__array_ufunc__ = lambda self, ufunc, method, *inputs, **kwargs: None
    return cls