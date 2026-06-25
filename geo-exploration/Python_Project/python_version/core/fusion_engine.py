"""
融合引擎模块

负责管理多个探测器并执行融合计算
"""

import numpy as np
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import time
from loguru import logger
from .base_classes import BaseDetector, DetectorResult, TaskManager
from config.config import Config


class FusionEngine:
    """
    融合引擎类

    采用面向对象设计，负责管理多个探测器并执行融合计算
    """

    def __init__(self, n_jobs: int = -1, parallel_backend: str = 'threading'):
        """
        初始化融合引擎

        Args:
            n_jobs: 并行任务数，-1 表示使用所有 CPU 核心
            parallel_backend: 并行后端 ('threading', 'multiprocessing')
        """
        self.detectors = {}  # 存储探测器实例
        self.results = {}   # 存储计算结果
        self.n_jobs = n_jobs if n_jobs > 0 else Config.PERFORMANCE['n_jobs']
        self.parallel_backend = parallel_backend
        self.task_manager = TaskManager()
        self._setup_logging()

    def _setup_logging(self):
        """设置日志"""
        logger.add(
            Config.LOGS_DIR / "fusion_engine.log",
            level=Config.LOGGING['level'],
            format=Config.LOGGING['format'],
            rotation=Config.LOGGING['rotation'],
            retention=Config.LOGGING['retention']
        )

    def register_detector(self, name: str, detector: BaseDetector):
        """
        注册探测器

        Args:
            name: 探测器名称
            detector: 探测器实例
        """
        self.detectors[name] = detector
        logger.info(f"已注册探测器: {name}")

    def unregister_detector(self, name: str):
        """
        注销探测器

        Args:
            name: 探测器名称
        """
        if name in self.detectors:
            del self.detectors[name]
            logger.info(f"已注销探测器: {name}")

    def get_detectors(self) -> Dict[str, BaseDetector]:
        """获取所有探测器"""
        return self.detectors.copy()

    def get_detector_names(self) -> List[str]:
        """获取所有探测器名称"""
        return list(self.detectors.keys())

    def compute_all(self, context, task_id: Optional[str] = None) -> Dict[str, DetectorResult]:
        """
        计算所有探测器的结果

        Args:
            context: 地理数据上下文
            task_id: 任务 ID（可选）

        Returns:
            所有探测器的计算结果
        """
        if task_id:
            self.task_manager.update_task_status(task_id, 'running', 0)

        start_time = time.time()
        self.results = {}

        # 获取探测器列表
        detector_names = list(self.detectors.keys())
        n_detectors = len(detector_names)

        logger.info(f"开始计算 {n_detectors} 个探测器...")

        # 并行计算
        if Config.PERFORMANCE['use_multiprocessing'] and n_detectors > 1:
            self._compute_parallel(context, detector_names, task_id)
        else:
            self._compute_sequential(context, detector_names, task_id)

        # 更新任务进度
        if task_id:
            self.task_manager.update_task_status(task_id, 'completed', 100)

        end_time = time.time()
        logger.info(f"所有探测器计算完成，耗时: {end_time - start_time:.2f} 秒")

        return self.results

    def _compute_parallel(self, context, detector_names: List[str], task_id: Optional[str]):
        """并行计算多个探测器"""
        if self.parallel_backend == 'threading':
            executor = ThreadPoolExecutor(max_workers=self.n_jobs)
        else:
            executor = ProcessPoolExecutor(max_workers=self.n_jobs)

        # 提交任务
        futures = {
            executor.submit(self._compute_single, name, self.detectors[name], context)
            for name in detector_names
        }

        # 收集结果
        completed = 0
        total = len(detector_names)

        for future in futures.as_completed():
            try:
                name, result = future.result()
                self.results[name] = result
                completed += 1

                # 更新进度
                if task_id:
                    progress = int((completed / total) * 90)  # 留 10% 给融合
                    self.task_manager.update_task_status(task_id, 'running', progress)
                    logger.info(f"{name} 完成 ({completed}/{total})")

            except Exception as e:
                logger.error(f"计算失败: {name} - {str(e)}")

        executor.shutdown(wait=True)

    def _compute_sequential(self, context, detector_names: List[str], task_id: Optional[str]):
        """顺序计算多个探测器"""
        total = len(detector_names)

        for i, name in enumerate(detector_names):
            try:
                detector = self.detectors[name]
                result = self._compute_single(name, detector, context)
                self.results[name] = result

                # 更新进度
                if task_id:
                    progress = int(((i + 1) / total) * 90)  # 留 10% 给融合
                    self.task_manager.update_task_status(task_id, 'running', progress)
                    logger.info(f"{name} 完成 ({i + 1}/{total})")

            except Exception as e:
                logger.error(f"计算失败: {name} - {str(e)}")
                # 继续计算其他探测器
                continue

    def _compute_single(self, name: str, detector: BaseDetector, context) -> tuple:
        """
        计算单个探测器的结果

        Args:
            name: 探测器名称
            detector: 探测器实例
            context: 地理数据上下文

        Returns:
            (name, result): 探测器名称和结果
        """
        logger.debug(f"开始计算探测器: {name}")
        result = detector.calculate(context)
        logger.debug(f"探测器 {name} 计算完成")
        return name, result

    def get_fused_mask(self, names_list: List[str], method: str = 'max') -> np.ndarray:
        """
        获取融合后的掩码

        Args:
            names_list: 要融合的探测器名称列表
            method: 融合方法 ('max', 'mean', 'weighted')

        Returns:
            融合后的掩码
        """
        if not names_list:
            raise ValueError("未指定要融合的探测器")

        # 检查所有探测器是否都已计算
        for name in names_list:
            if name not in self.results:
                raise ValueError(f"探测器 {name} 尚未计算")

        # 获取第一个探测器的尺寸作为基准
        first_name = names_list[0]
        reference_shape = self.results[first_name].mask.shape

        fused_mask = np.zeros(reference_shape)

        for name in names_list:
            current_mask = self.results[name].mask

            # 统一尺寸
            if current_mask.shape != reference_shape:
                from scipy.ndimage import zoom
                scale = np.array(reference_shape) / np.array(current_mask.shape)
                current_mask = zoom(current_mask, scale, order=1)

            # 融合
            if method == 'max':
                fused_mask = np.maximum(fused_mask, current_mask)
            elif method == 'mean':
                fused_mask = (fused_mask + current_mask) / 2
            elif method == 'weighted':
                # 加权融合（示例：前两个探测器权重高）
                weights = np.linspace(1.0, 0.5, len(names_list))
                if not hasattr(self, '_weighted_fused'):
                    self._weighted_fused = np.zeros_like(fused_mask)
                    self._weight_sum = 0.0

                weight = weights[names_list.index(name)]
                self._weighted_fused += current_mask * weight
                self._weight_sum += weight
                fused_mask = self._weighted_fused / self._weight_sum
            else:
                raise ValueError(f"不支持的融合方法: {method}")

        logger.info(f"融合完成，使用方法: {method}")
        return fused_mask

    def get_detector_results(self, name: str) -> Optional[DetectorResult]:
        """获取指定探测器的结果"""
        return self.results.get(name)

    def get_all_results(self) -> Dict[str, DetectorResult]:
        """获取所有结果"""
        return self.results.copy()

    def clear_results(self):
        """清空计算结果"""
        self.results = {}
        logger.info("已清空计算结果")

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            'detector_count': len(self.detectors),
            'computed_count': len(self.results),
            'detector_names': list(self.detectors.keys()),
            'computed_names': list(self.results.keys())
        }

        # 计算结果的统计信息
        if self.results:
            stats['result_stats'] = {}
            for name, result in self.results.items():
                mask = result.mask
                stats['result_stats'][name] = {
                    'shape': mask.shape,
                    'min': float(np.nanmin(mask)),
                    'max': float(np.nanmax(mask)),
                    'mean': float(np.nanmean(mask)),
                    'std': float(np.nanstd(mask))
                }

        return stats

    def save_results(self, save_path: str):
        """保存结果到文件"""
        import pickle
        import os

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        results_data = {
            'detectors': self.get_statistics(),
            'results': {name: {
                'mask': result.mask,
                'debug_data': result.debug_data,
                'metadata': result.metadata
            } for name, result in self.results.items()},
            'timestamp': time.time()
        }

        with open(save_path, 'wb') as f:
            pickle.dump(results_data, f)

        logger.info(f"结果已保存到: {save_path}")

    def load_results(self, load_path: str):
        """从文件加载结果"""
        import pickle

        with open(load_path, 'rb') as f:
            results_data = pickle.load(f)

        # 重建结果对象
        for name, result_data in results_data['results'].items():
            result = DetectorResult(
                mask=result_data['mask'],
                debug_data=result_data.get('debug_data'),
                metadata=result_data.get('metadata')
            )
            self.results[name] = result

        logger.info(f"结果已从 {load_path} 加载")

    def create_task(self, mineral_type: str, detectors: List[str],
                  config: Dict[str, Any]) -> str:
        """创建分析任务"""
        return self.task_manager.create_task(mineral_type, detectors, config)

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        task = self.task_manager.get_task(task_id)
        if task:
            return {
                'id': task.id,
                'mineral_type': task.mineral_type,
                'detectors': task.detectors,
                'status': task.status,
                'progress': task.progress,
                'start_time': task.start_time,
                'end_time': task.end_time,
                'error': task.error
            }
        return None