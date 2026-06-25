"""
舒曼波共振遥感矿产预测系统 - 核心算法接口

提供与 MATLAB run_core_algorithm.m 兼容的 Python 接口
"""

import sys
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.fusion_engine import FusionEngine
from core.geo_data_context import GeoDataContext
from core.post_processor import PostProcessor
from detectors.red_edge_detector import RedEdgeDetector
from detectors.intrinsic_detector import IntrinsicDetector
from detectors.slow_vars_detector import SlowVarsDetector
from detectors.known_anomaly_detector import KnownAnomalyDetector
from config.config import Config
from utils.logger import get_logger


def run_core_algorithm(
    data_dir: str,
    roi_file: str,
    mineral_type: str,
    kmz_path: str = '',
    kmz_threshold: float = 0.6,
    fusion_mode: bool = True,
    output_dir: Optional[str] = None,
    enable_detectors: Optional[Dict[str, bool]] = None,
    verbose: bool = True
) -> Tuple[str, Dict[str, Any]]:
    """
    核心算法接口（与 MATLAB run_core_algorithm.m 兼容）

    执行完整的矿产预测分析流程：
    1. 加载遥感数据
    2. 运行多个探测器
    3. 融合检测结果
    4. 深度与压力反演
    5. 生成可视化结果

    Args:
        data_dir: 数据目录路径（包含 Sentinel-2, Landsat-8, ASTER, DEM 数据）
        roi_file: ROI 文件路径（CSV 或 Excel 格式，包含经纬度列）
        mineral_type: 矿物类型（gold, copper, iron, coal, petroleum 等）
        kmz_path: 已知异常 KMZ/KML 文件路径（可选）
        kmz_threshold: KMZ 导出阈值（默认 0.6）
        fusion_mode: 是否使用融合模式（默认 True）
        output_dir: 输出目录（可选，默认自动生成）
        enable_detectors: 启用/禁用特定探测器（可选）
        verbose: 是否输出详细日志（默认 True）

    Returns:
        (output_dir, result_dict): 输出目录路径和结果字典

    Example:
        >>> output_dir, results = run_core_algorithm(
        ...     data_dir='./data',
        ...     roi_file='./roi.xlsx',
        ...     mineral_type='gold',
        ...     kmz_path='./known_anomalies.kmz'
        ... )
        >>> print(f"结果保存至: {output_dir}")
    """
    logger = get_logger(__name__)

    # 设置默认探测器配置
    if enable_detectors is None:
        enable_detectors = {
            'red_edge': True,
            'intrinsic': True,
            'slow_vars': True,
            'known_anomaly': bool(kmz_path)
        }

    if verbose:
        logger.info("="*70)
        logger.info("舒曼波共振遥感矿产预测系统 - 核心算法")
        logger.info("="*70)
        logger.info(f"数据目录: {data_dir}")
        logger.info(f"ROI 文件: {roi_file}")
        logger.info(f"目标矿种: {mineral_type}")
        logger.info(f"融合模式: {fusion_mode}")
        logger.info(f"KMZ 阈值: {kmz_threshold}")
        logger.info("="*70)

    # 1. 验证矿物类型
    if mineral_type not in Config.MINERAL_TYPES:
        available = ', '.join(Config.MINERAL_TYPES.keys())
        raise ValueError(f"不支持的矿物类型: {mineral_type}. 可用: {available}")

    # 2. 创建地理数据上下文并加载数据
    if verbose:
        logger.info("步骤 1/5: 加载遥感数据...")

    context = GeoDataContext(data_dir, roi_file)
    context.mineral_type = mineral_type

    try:
        context.load_all_data()

        if verbose:
            data_status = []
            if context.s2_data is not None:
                data_status.append(f"Sentinel-2 ({context.s2_data.shape})")
            if context.l8_data is not None:
                data_status.append(f"Landsat-8 ({context.l8_data.shape})")
            if context.ast_data is not None:
                data_status.append(f"ASTER ({context.ast_data.shape})")
            if context.dem_data is not None:
                data_status.append(f"DEM ({context.dem_data.shape})")

            logger.info(f"  已加载数据: {', '.join(data_status)}")
            logger.info(f"  ROI 范围: {np.sum(context.inROI)} 个有效像素")

    except Exception as e:
        error_msg = f"数据加载失败: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e

    # 3. 创建融合引擎并注册探测器
    if verbose:
        logger.info("步骤 2/5: 初始化探测器...")

    engine = FusionEngine()

    # 红边检测器
    if enable_detectors.get('red_edge', True) and context.s2_data is not None:
        red_edge = RedEdgeDetector()
        engine.register_detector('red_edge', red_edge)
        if verbose:
            logger.info("  [√] 红边检测器 (RedEdgeDetector)")

    # 本征吸收检测器
    if enable_detectors.get('intrinsic', True):
        intrinsic = IntrinsicDetector({'mineral_type': mineral_type})
        engine.register_detector('intrinsic', intrinsic)
        if verbose:
            logger.info("  [√] 本征吸收检测器 (IntrinsicDetector)")

    # 慢变量检测器
    if enable_detectors.get('slow_vars', True):
        slow_vars = SlowVarsDetector()
        engine.register_detector('slow_vars', slow_vars)
        if verbose:
            logger.info("  [√] 慢变量检测器 (SlowVarsDetector)")

    # 已知异常检测器
    if enable_detectors.get('known_anomaly', False) and kmz_path:
        if Path(kmz_path).exists():
            known_anomaly = KnownAnomalyDetector(kmz_path)
            engine.register_detector('known_anomaly', known_anomaly)
            if verbose:
                logger.info("  [√] 已知异常检测器 (KnownAnomalyDetector)")
        else:
            logger.warning(f"  [!] KMZ 文件不存在: {kmz_path}")

    # 4. 执行探测器计算
    if verbose:
        logger.info("步骤 3/5: 执行探测器分析...")

    try:
        engine.compute_all(context, parallel=True)

        if verbose:
            for name, result in engine.results.items():
                if result.mask is not None:
                    valid_pixels = np.sum(result.mask[context.inROI] > 0)
                    logger.info(f"  {name}: {valid_pixels} 个异常像素")

    except Exception as e:
        error_msg = f"探测器计算失败: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e

    # 5. 融合探测器结果
    if verbose:
        logger.info("步骤 4/5: 融合检测结果...")

    # 确定要融合的探测器
    detector_names = ['red_edge', 'intrinsic', 'slow_vars', 'known_anomaly']
    available_detectors = [name for name in detector_names if name in engine.results]

    if not available_detectors:
        error_msg = "没有可用的探测器结果"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # 获取融合掩码
    final_mask = engine.get_fused_mask(available_detectors, method='max')

    if verbose:
        logger.info(f"  融合探测器: {', '.join(available_detectors)}")
        logger.info(f"  融合后异常像素: {np.sum(final_mask[context.inROI] > 0)}")

    # 6. 后处理与结果输出
    if verbose:
        logger.info("步骤 5/5: 深度反演与可视化...")

    # 设置输出目录
    if output_dir is None:
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = str(project_root / 'results' / f'{mineral_type}_{timestamp}')

    # 创建后处理器
    post_processor = PostProcessor({
        'fusion_mode': fusion_mode,
        'kmz_threshold': kmz_threshold
    })

    # 执行后处理
    try:
        post_result = post_processor.run(context, engine, final_mask, output_dir)

        if verbose:
            logger.info("  深度反演: 完成")
            logger.info("  压力反演: 完成")
            logger.info("  地表潜力计算: 完成")
            logger.info("  可视化生成: 完成")

    except Exception as e:
        error_msg = f"后处理失败: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e

    # 准备返回结果
    result_dict = {
        'mineral_type': mineral_type,
        'output_dir': output_dir,
        'files': post_result['files_generated'],
        'statistics': post_result['statistics'],
        'detector_results': {
            name: {
                'valid_pixels': int(np.sum(result.mask[context.inROI] > 0))
                if result.mask is not None else 0
            }
            for name, result in engine.results.items()
        },
        'fusion_detectors': available_detectors,
        'parameters': {
            'kmz_threshold': kmz_threshold,
            'fusion_mode': fusion_mode
        }
    }

    if verbose:
        logger.info("="*70)
        logger.info("分析完成！")
        logger.info("="*70)
        logger.info(f"输出目录: {output_dir}")
        logger.info(f"生成文件: {len(result_dict['files'])} 个")
        for file in result_dict['files'][:5]:  # 显示前5个文件
            logger.info(f"  - {file}")
        if len(result_dict['files']) > 5:
            logger.info(f"  ... 及其他 {len(result_dict['files']) - 5} 个文件")
        logger.info("="*70)

    return output_dir, result_dict


def quick_analysis(
    data_dir: str,
    roi_file: str,
    mineral_type: str
) -> np.ndarray:
    """
    快速分析接口（仅返回深部预测结果）

    Args:
        data_dir: 数据目录路径
        roi_file: ROI 文件路径
        mineral_type: 矿物类型

    Returns:
        深部成矿预测图 (numpy array)
    """
    from scipy.io import loadmat

    output_dir, results = run_core_algorithm(
        data_dir=data_dir,
        roi_file=roi_file,
        mineral_type=mineral_type,
        verbose=False
    )

    # 加载结果
    mat_file = Path(output_dir) / 'mineral_prediction_results.mat'
    if mat_file.exists():
        mat_data = loadmat(str(mat_file))
        return mat_data.get('Au_deep', np.array([]))
    else:
        raise FileNotFoundError(f"结果文件未生成: {mat_file}")


def batch_analysis(
    config_file: str,
    verbose: bool = True
) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """
    批量分析接口

    Args:
        config_file: 配置文件路径（JSON 格式）
        verbose: 是否输出详细日志

    Returns:
        批量分析结果字典 {task_name: (output_dir, result_dict)}
    """
    import json

    # 加载配置文件
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    results = {}

    # 执行批量任务
    for task_name, task_config in config.get('tasks', {}).items():
        if verbose:
            logger.info(f"执行任务: {task_name}")

        try:
            output_dir, result = run_core_algorithm(
                data_dir=task_config.get('data_dir'),
                roi_file=task_config.get('roi_file'),
                mineral_type=task_config.get('mineral_type'),
                kmz_path=task_config.get('kmz_path', ''),
                kmz_threshold=task_config.get('kmz_threshold', 0.6),
                output_dir=task_config.get('output_dir'),
                enable_detectors=task_config.get('enable_detectors'),
                verbose=verbose
            )
            results[task_name] = (output_dir, result)

            if verbose:
                logger.info(f"任务 {task_name} 完成")

        except Exception as e:
            logger.error(f"任务 {task_name} 失败: {str(e)}")
            results[task_name] = (None, {'error': str(e)})

    return results


# MATLAB 兼容别名（方便从 MATLAB 调用）
runCoreAlgorithm = run_core_algorithm


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='舒曼波共振遥感矿产预测系统',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--data-dir', required=True, help='数据目录')
    parser.add_argument('--roi-file', required=True, help='ROI 文件')
    parser.add_argument('--mineral-type', required=True,
                       help=f'矿物类型: {", ".join(Config.MINERAL_TYPES.keys())}')
    parser.add_argument('--kmz-path', help='KMZ 文件路径')
    parser.add_argument('--kmz-threshold', type=float, default=0.6, help='KMZ 阈值')
    parser.add_argument('--output-dir', help='输出目录')
    parser.add_argument('--quiet', action='store_true', help='静默模式')

    args = parser.parse_args()

    try:
        output_dir, results = run_core_algorithm(
            data_dir=args.data_dir,
            roi_file=args.roi_file,
            mineral_type=args.mineral_type,
            kmz_path=args.kmz_path or '',
            kmz_threshold=args.kmz_threshold,
            output_dir=args.output_dir,
            verbose=not args.quiet
        )

        print(f"\n✓ 分析完成！")
        print(f"  输出目录: {output_dir}")
        print(f"  生成文件: {len(results['files'])} 个")

        sys.exit(0)

    except Exception as e:
        print(f"\n✗ 错误: {str(e)}", file=sys.stderr)
        sys.exit(1)
