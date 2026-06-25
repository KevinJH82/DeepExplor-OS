"""
舒曼波共振遥感矿产预测系统 - 主入口

基于 MATLAB 系统的 Python 完整实现
"""

import os
import sys
import argparse
from pathlib import Path
from typing import Optional
from datetime import datetime

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


def main(data_dir: str, roi_file: str, mineral_type: str,
         kmz_path: Optional[str] = None, output_dir: Optional[str] = None,
         kmz_threshold: float = 0.6, fusion_mode: bool = True,
         enable_all_detectors: bool = True, parallel: bool = True) -> dict:
    """
    主函数：执行完整的矿产预测流程

    Args:
        data_dir: 数据目录路径
        roi_file: ROI 文件路径
        mineral_type: 矿物类型
        kmz_path: KMZ/KML 文件路径（可选）
        output_dir: 输出目录（可选）
        kmz_threshold: KMZ 阈值
        fusion_mode: 是否使用融合模式
        enable_all_detectors: 是否启用所有探测器
        parallel: 是否使用并行计算

    Returns:
        处理结果字典
    """
    logger = get_logger(__name__)

    # 1. 验证输入参数
    if mineral_type not in Config.MINERAL_TYPES:
        available = ', '.join(Config.MINERAL_TYPES.keys())
        raise ValueError(f"不支持的矿物类型: {mineral_type}. 可用: {available}")

    if not Path(data_dir).exists():
        raise FileNotFoundError(f"数据目录不存在: {data_dir}")

    if not Path(roi_file).exists():
        raise FileNotFoundError(f"ROI 文件不存在: {roi_file}")

    logger.info("="*60)
    logger.info("舒曼波共振遥感矿产预测系统")
    logger.info("="*60)
    logger.info(f"数据目录: {data_dir}")
    logger.info(f"ROI 文件: {roi_file}")
    logger.info(f"目标矿种: {mineral_type}")
    logger.info(f"KMZ 文件: {kmz_path if kmz_path else '未提供'}")
    logger.info("="*60)

    # 2. 创建地理数据上下文
    logger.info("步骤 1/6: 创建地理数据上下文...")
    context = GeoDataContext(data_dir, roi_file)
    context.mineral_type = mineral_type

    # 3. 加载所有数据
    logger.info("步骤 2/6: 加载遥感数据...")
    try:
        context.load_all_data()
        logger.info(f"  - Sentinel-2: {'已加载' if context.s2_data is not None else '未找到'}")
        logger.info(f"  - Landsat-8: {'已加载' if context.l8_data is not None else '未找到'}")
        logger.info(f"  - ASTER: {'已加载' if context.ast_data is not None else '未找到'}")
        logger.info(f"  - DEM: {'已加载' if context.dem_data is not None else '未找到'}")
        logger.info(f"  - ROI: {np.sum(context.inROI)} 个有效像素")
    except Exception as e:
        logger.error(f"加载数据失败: {str(e)}")
        raise

    # 4. 创建融合引擎
    logger.info("步骤 3/6: 初始化融合引擎...")
    engine = FusionEngine()

    # 5. 注册探测器
    logger.info("步骤 4/6: 注册探测器...")

    # 红边检测器
    if enable_all_detectors or context.s2_data is not None:
        red_edge = RedEdgeDetector()
        engine.register_detector('red_edge', red_edge)
        logger.info("  - 红边检测器: 已注册")

    # 本征吸收检测器
    if enable_all_detectors or context.ast_data is not None:
        intrinsic = IntrinsicDetector({'mineral_type': mineral_type})
        engine.register_detector('intrinsic', intrinsic)
        logger.info("  - 本征吸收检测器: 已注册")

    # 慢变量检测器
    if enable_all_detectors:
        slow_vars = SlowVarsDetector()
        engine.register_detector('slow_vars', slow_vars)
        logger.info("  - 慢变量检测器: 已注册")

    # 已知异常检测器
    if kmz_path and Path(kmz_path).exists():
        known_anomaly = KnownAnomalyDetector(kmz_path)
        engine.register_detector('known_anomaly', known_anomaly)
        logger.info("  - 已知异常检测器: 已注册")

    # 6. 执行计算
    logger.info("步骤 5/6: 执行探测器计算...")
    try:
        engine.compute_all(context, parallel=parallel)

        # 输出各探测器统计
        for name, result in engine.results.items():
            if result.mask is not None:
                valid_pixels = np.sum(result.mask[context.inROI] > 0)
                logger.info(f"  - {name}: {valid_pixels} 个异常像素")
    except Exception as e:
        logger.error(f"探测器计算失败: {str(e)}")
        raise

    # 7. 融合结果
    detector_names = ['red_edge', 'intrinsic', 'slow_vars']
    available_detectors = [name for name in detector_names if name in engine.results]

    if not available_detectors:
        logger.warning("没有可用的探测器结果")
        available_detectors = list(engine.results.keys())

    if available_detectors:
        logger.info(f"融合探测器: {', '.join(available_detectors)}")
        final_mask = engine.get_fused_mask(available_detectors, method='max')
    else:
        logger.error("无法生成融合结果：没有可用的探测器")
        raise RuntimeError("没有可用的探测器结果")

    # 8. 后处理
    logger.info("步骤 6/6: 执行后处理...")

    # 设置输出目录
    if output_dir is None:
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
        logger.info(f"后处理完成！结果保存至: {output_dir}")
        logger.info(f"生成文件: {len(post_result['files_generated'])} 个")
        for file in post_result['files_generated']:
            logger.info(f"  - {file}")
    except Exception as e:
        logger.error(f"后处理失败: {str(e)}")
        raise

    logger.info("="*60)
    logger.info("分析完成！")
    logger.info("="*60)

    return {
        'output_dir': output_dir,
        'files': post_result['files_generated'],
        'statistics': post_result['statistics'],
        'mineral_type': mineral_type,
        'context': context,
        'engine': engine,
        'final_mask': final_mask
    }


def cli():
    """命令行接口"""
    parser = argparse.ArgumentParser(
        description='舒曼波共振遥感矿产预测系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python main.py --data-dir ./data --roi-file ./roi.xlsx --mineral-type gold
  python main.py --data-dir ./data --roi-file ./roi.xlsx --mineral-type copper --kmz-path ./known_anomalies.kmz
  python main.py --data-dir ./data --roi-file ./roi.xlsx --mineral-type iron --output-dir ./my_results --no-parallel
        '''
    )

    parser.add_argument('--data-dir', required=True, help='数据目录路径')
    parser.add_argument('--roi-file', required=True, help='ROI 文件路径 (CSV/XLSX)')
    parser.add_argument('--mineral-type', required=True, help=f'矿物类型: {", ".join(Config.MINERAL_TYPES.keys())}')
    parser.add_argument('--kmz-path', help='KMZ/KML 文件路径（可选）')
    parser.add_argument('--output-dir', help='输出目录（可选）')
    parser.add_argument('--kmz-threshold', type=float, default=0.6, help='KMZ 阈值 (默认: 0.6)')
    parser.add_argument('--no-fusion', action='store_true', help='禁用融合模式')
    parser.add_argument('--no-parallel', action='store_true', help='禁用并行计算')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')

    args = parser.parse_args()

    # 导入 numpy
    import numpy as np

    try:
        result = main(
            data_dir=args.data_dir,
            roi_file=args.roi_file,
            mineral_type=args.mineral_type,
            kmz_path=args.kmz_path,
            output_dir=args.output_dir,
            kmz_threshold=args.kmz_threshold,
            fusion_mode=not args.no_fusion,
            parallel=not args.no_parallel
        )

        print("\n" + "="*60)
        print("分析成功完成！")
        print("="*60)
        print(f"输出目录: {result['output_dir']}")
        print(f"矿物类型: {result['mineral_type']}")
        print(f"生成文件: {len(result['files'])} 个")
        print("="*60)

        return 0

    except Exception as e:
        print(f"\n错误: {str(e)}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    import numpy as np
    sys.exit(cli())
