#!/usr/bin/env python3
"""image_ingest — 地质图像数据集成脚本。

用于将解析后的图像数据集成到 geo-model3d 流程中。

用法:
    python integration/image_ingest.py parse --config uploads/image_parsing/dongan_gold_mine/config.json
    python integration/image_ingest.py ingest --aoi <bbox> --mineral gold
"""

import os
import sys
import argparse
import json
from pathlib import Path

# 添加项目根目录到路径
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from core.image_parser import (
    ImageParser, ParseConfig, GCP, parse_geochem_image,
    parse_csamt_image, parse_section_image
)
from utils.logger import get_logger

logger = get_logger(__name__)


def load_config(config_path: str) -> dict:
    """加载项目配置文件。"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_command(args):
    """执行解析命令。"""
    config_path = args.config
    config_data = load_config(config_path)

    project_dir = Path(config_path).parent
    raw_dir = project_dir / "raw"
    parsed_dir = project_dir / "parsed"

    # 获取要解析的图像
    images_to_parse = []
    for img_info in config_data.get("images", []):
        if img_info.get("enabled", True):
            images_to_parse.append(img_info)

    if not images_to_parse:
        logger.info("没有启用的图像需要解析")
        return

    # 优先解析高优先级的图像
    images_to_parse.sort(key=lambda x: x.get("priority", 99), reverse=True)

    for img_info in images_to_parse[:1]:  # 先处理第一个（优先级最高的）
        filename = img_info["filename"]
        img_type = img_info["type"]
        input_path = raw_dir / filename
        output_dir = parsed_dir / Path(filename).stem

        if not input_path.exists():
            logger.warning(f"图像不存在: {input_path}")
            continue

        logger.info(f"解析图像: {filename} (类型: {img_type})")

        # 创建解析配置
        parse_config = ParseConfig(
            input_image=str(input_path),
            output_dir=str(output_dir),
            mineral_type=config_data.get("mineral_type", "gold"),
            crs=config_data.get("crs", "EPSG:4326"),
            n_legend_colors=config_data.get("parse_options", {}).get("n_legend_colors", 16),
            color_tolerance=config_data.get("parse_options", {}).get("color_tolerance", 30),
            bounds=config_data.get("default_bounds")
        )

        # 根据图像类型选择解析方法
        try:
            if img_type == "geochem":
                output = parse_geochem_image(parse_config)
            elif img_type == "csamt":
                output = parse_csamt_image(parse_config)
            elif img_type == "section":
                output = parse_section_image(parse_config)
            else:
                logger.warning(f"不支持的图像类型: {img_type}，使用通用解析")
                parser = ImageParser(parse_config)
                output = parser.parse()

            logger.info(f"✓ 解析完成: {output}")
        except NotImplementedError as e:
            logger.warning(f"✗ {filename}: {e}")
        except Exception as e:
            logger.error(f"✗ 解析 {filename} 失败: {e}")


def batch_parse_command(args):
    """批量解析项目目录中的所有图像。"""
    project_dir = Path(args.project_dir)
    raw_dir = project_dir / "raw"
    parsed_dir = project_dir / "parsed"

    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    # 查找所有 JPG/PNG 图像
    image_files = list(raw_dir.glob("*.jpg")) + list(raw_dir.glob("*.png"))

    if not image_files:
        logger.warning(f"没有找到图像文件: {raw_dir}")
        return

    for img_path in image_files:
        output_dir = parsed_dir / img_path.stem

        parse_config = ParseConfig(
            input_image=str(img_path),
            output_dir=str(output_dir),
            mineral_type=args.mineral or "gold",
            crs=args.crs or "EPSG:4326"
        )

        try:
            parser = ImageParser(parse_config)
            output = parser.parse()
            logger.info(f"✓ {img_path.name} -> {output}")
        except Exception as e:
            logger.error(f"✗ {img_path.name}: {e}")


def ingest_command(args):
    """将解析结果集成到 geo-model3d。"""
    # TODO: 实现将解析的 GeoTIFF 作为证据层加载到 geo-model3d
    logger.info("集成功能待实现")


def list_command(args):
    """列出项目目录中的图像。"""
    project_dir = Path(args.project_dir)
    raw_dir = project_dir / "raw"

    if not raw_dir.exists():
        print(f"原始图像目录不存在: {raw_dir}")
        return

    images = list(raw_dir.glob("*.jpg")) + list(raw_dir.glob("*.png"))

    if not images:
        print(f"没有找到图像文件: {raw_dir}")
        return

    print(f"\n项目: {project_dir.name}")
    print(f"图像数量: {len(images)}\n")

    for img in sorted(images):
        size_mb = img.stat().st_size / (1024 * 1024)
        print(f"  - {img.name} ({size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(
        description="地质图像数据集成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 解析配置文件中指定的图像
  python integration/image_ingest.py parse --config uploads/image_parsing/dongan_gold_mine/config.json

  # 批量解析项目目录中的所有图像
  python integration/image_ingest.py batch-parse --project-dir uploads/image_parsing/dongan_gold_mine

  # 列出项目中的图像
  python integration/image_ingest.py list --project-dir uploads/image_parsing/dongan_gold_mine
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # parse 命令
    parse_parser = subparsers.add_parser("parse", help="解析配置文件中的图像")
    parse_parser.add_argument("--config", required=True, help="配置文件路径")
    parse_parser.set_defaults(func=parse_command)

    # batch-parse 命令
    batch_parser = subparsers.add_parser("batch-parse", help="批量解析图像")
    batch_parser.add_argument("--project-dir", required=True, help="项目目录")
    batch_parser.add_argument("--mineral", default="gold", help="矿种类型")
    batch_parser.add_argument("--crs", default="EPSG:4326", help="坐标系")
    batch_parser.set_defaults(func=batch_parse_command)

    # ingest 命令
    ingest_parser = subparsers.add_parser("ingest", help="集成到 geo-model3d")
    ingest_parser.add_argument("--aoi", help="研究区边界 (minx,miny,maxx,maxy)")
    ingest_parser.add_argument("--mineral", required=True, help="矿种类型")
    ingest_parser.set_defaults(func=ingest_command)

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出图像")
    list_parser.add_argument("--project-dir", required=True, help="项目目录")
    list_parser.set_defaults(func=list_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
