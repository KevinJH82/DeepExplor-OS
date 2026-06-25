#!/usr/bin/env python3
"""
🏔️ Prospector — 找矿前期资料自动收集系统

用法:
    python prospector.py --roi <ROI文件> --mineral <矿种> [--output <输出目录>] [--download]

示例:
    python prospector.py --roi ./target_area.kml --mineral 铜
    python prospector.py --roi ./coords.xlsx --mineral 金 --download
    python prospector.py --interactive
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.roi_parser import parse_roi, expand_bbox
from src.mineral_kb import get_mineral_info, list_all_minerals
from src.tectonic_units import analyze_roi_location
from src.geo_fetcher import fetch_all_geological
from src.geophy_fetcher import fetch_all_geophysical, clip_external_emag2
from src.geochem_fetcher import fetch_all_geochemical
from src.report_generator import generate_report, save_json_summary


def main():
    parser = argparse.ArgumentParser(
        description="🏔️ Prospector — 找矿前期资料自动收集系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python prospector.py --roi area.kml --mineral 铜
  python prospector.py --roi coords.xlsx --mineral 金 --download
  python prospector.py --interactive
        """,
    )

    parser.add_argument(
        "--roi", "-r",
        type=str,
        help="ROI 文件路径 (.kml / .ovkml / .xlsx)",
    )
    parser.add_argument(
        "--mineral", "-m",
        type=str,
        help="目标矿种 (如: 铜, 金, 锂, 铅锌, 钨锡, 稀土, 铁)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="./output",
        help="输出目录 (默认: ./output)",
    )
    parser.add_argument(
        "--download", "-d",
        action="store_true",
        help="自动下载可获取的数据 (EMAG2磁法, WGM2012重力等)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="仅生成检索链接，不下载任何数据",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="交互模式",
    )
    parser.add_argument(
        "--list-minerals",
        action="store_true",
        help="列出所有支持的矿种",
    )
    parser.add_argument(
        "--buffer", "-b",
        type=float,
        default=20.0,
        help="ROI 外扩距离 (km, 默认 20)",
    )
    parser.add_argument(
        "--emag2-file", "-e",
        type=str,
        default=None,
        help="本地 EMAG2 v3 全球 GeoTIFF 文件路径（用于离线裁剪+出图）",
    )

    args = parser.parse_args()

    # 列出矿种
    if args.list_minerals:
        minerals = list_all_minerals()
        print(f"\n支持的矿种 ({len(minerals)} 种):")
        for m in minerals:
            info = get_mineral_info(m)
            types = [t['name'] for t in info['metallogenic_types']]
            print(f"  ● {m} — {', '.join(types)}")
        return

    # 交互模式
    if args.interactive:
        run_interactive(args)
        return

    # 参数校验
    if not args.roi:
        parser.error("需要 --roi 参数指定 ROI 文件，或使用 --interactive 进入交互模式")
    if not args.mineral:
        parser.error("需要 --mineral 参数指定目标矿种")

    # 检查文件
    roi_path = Path(args.roi)
    if not roi_path.exists():
        print(f"❌ ROI 文件不存在: {args.roi}")
        sys.exit(1)

    # 运行
    run_pipeline(
        roi_path=str(roi_path),
        mineral=args.mineral,
        output_dir=args.output,
        auto_download=args.download and not args.no_download,
        buffer_km=args.buffer,
        emag2_file=args.emag2_file,
    )


def run_pipeline(
    roi_path: str,
    mineral: str,
    output_dir: str = "./output",
    auto_download: bool = False,
    buffer_km: float = 20.0,
    emag2_file: str = None,
):
    """执行完整资料收集流水线"""
    print("\n" + "=" * 60)
    print("  🏔️  Prospector — 找矿前期资料自动收集系统 v0.1.0")
    print("=" * 60)

    # 创建输出目录
    project_name = f"{Path(roi_path).stem}_{mineral}_{datetime.now().strftime('%Y%m%d_%H%M')}"
    out_dir = Path(output_dir) / project_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n📂 输出目录: {out_dir}")
    print(f"📄 ROI 文件: {roi_path}")
    print(f"🎯 目标矿种: {mineral}")
    print(f"📥 自动下载: {'是' if auto_download else '否 (仅生成链接)'}")

    # Step 1: 解析 ROI
    print("\n" + "=" * 60)
    print("  Step 1/6: 解析 ROI")
    print("=" * 60)
    roi = parse_roi(roi_path)
    roi = expand_bbox(roi, buffer_km)
    print(f"  ✅ ROI 面积: {roi['area_km2']:.2f} km²")
    print(f"  ✅ 中心坐标: {roi['center']['lon']:.4f}°E, {roi['center']['lat']:.4f}°N")
    b = roi['expanded_bbox']
    print(f"  ✅ 扩展范围: {b['west']:.4f}°E, {b['south']:.4f}°N → {b['east']:.4f}°E, {b['north']:.4f}°N")

    # Step 2: 定位构造单元
    print("\n" + "=" * 60)
    print("  Step 2/6: 定位构造单元")
    print("=" * 60)
    location = analyze_roi_location(roi)
    tu = location.get('center_tectonic')
    pb = location.get('petroleum_basin')
    if tu:
        print(f"  ✅ 构造单元: {tu['name']}")
        print(f"     区内主要矿产: {', '.join(tu.get('major_minerals', []))}")
    if pb:
        print(f"  ✅ 含油气盆地: {pb['name']} ({pb['area_km2']:,} km²)")
        print(f"     主要成藏组合: {', '.join(pb['main_plays'])}")

    # Step 3: 知识库
    print("\n" + "=" * 60)
    print("  Step 3/6: 查询矿种知识库")
    print("=" * 60)
    mineral_info = get_mineral_info(mineral)
    mts = mineral_info.get('metallogenic_types', [])
    print(f"  ✅ 识别到 {len(mts)} 种可能成矿类型")
    print(f"  ✅ 指示元素: {', '.join(mineral_info.get('all_key_elements', []))}")

    # Step 4: 地质资料
    print("\n" + "=" * 60)
    print("  Step 4/6: 收集地质资料")
    print("=" * 60)
    geological = fetch_all_geological(roi, out_dir, mineral, mineral_info, location)

    # Step 5: 地球物理
    print("\n" + "=" * 60)
    print("  Step 5/6: 收集地球物理资料")
    print("=" * 60)
    geophysical = fetch_all_geophysical(roi, out_dir, mineral_info, auto_download)

    # --- 如果提供了本地 EMAG2 文件，用它覆盖磁法数据 ---
    if emag2_file:
        emag2_path = Path(emag2_file)
        if emag2_path.exists():
            print("\n  🗂️  使用本地 EMAG2 文件进行裁剪和出图...")
            mag = clip_external_emag2(
                emag2_path, roi,
                out_dir / "02_地球物理资料" / "magnetic",
                variant="upcont",
            )
            if mag:
                geophysical["magnetic"] = mag
                print(f"  ✅ 已用本地 EMAG2 文件完成裁剪和出图")
            else:
                print("  ⚠️  本地 EMAG2 处理失败")
        else:
            print(f"  ⚠️  EMAG2 文件不存在: {emag2_file}")

    # Step 6: 地球化学
    print("\n" + "=" * 60)
    print("  Step 6/6: 收集地球化学资料")
    print("=" * 60)
    geochemical = fetch_all_geochemical(roi, out_dir, mineral, mineral_info, location)

    # 生成报告
    print("\n" + "=" * 60)
    print("  📝 生成报告")
    print("=" * 60)
    report_path = generate_report(
        roi, mineral, mineral_info, location,
        geological, geophysical, geochemical,
        live_data=None,
        output_dir=out_dir,
    )
    json_path = save_json_summary(
        roi, mineral, mineral_info,
        geological, geophysical, geochemical,
        out_dir,
    )

    print(f"\n✅ 报告已生成: {report_path}")
    print(f"✅ JSON 摘要: {json_path}")

    # 最终总结
    print("\n" + "=" * 60)
    print("  ✅ 资料收集完成!")
    print("=" * 60)
    print(f"""
📂 成果包位置: {out_dir}/

  00_项目摘要.md          — 完整报告（本文件）
  summary.json            — JSON 结构化摘要
  02_地球物理资料/        — 磁法/重力数据
  03_地球化学资料/        — 元素背景值 + GEOROC

🔗 地质资料链接: {len(geological.get('ngac_geology', [])) + len(geological.get('ngac_mineral', []))} 条 NGAC 检索
🔗 化探资料链接: {len(geochemical.get('ngac_links', []))} 条
🔗 学术文献链接: {len(geological.get('cnki', []))} 条
""")

    return str(out_dir)


def run_interactive(args):
    """交互模式"""
    print("\n🏔️  Prospector 交互模式\n")

    # 输入 ROI
    roi_path = input("📄 ROI 文件路径 (.kml/.ovkml/.xlsx): ").strip()
    if not roi_path or not Path(roi_path).exists():
        print("❌ 文件不存在")
        return

    # 输入矿种
    minerals = list_all_minerals()
    print(f"\n支持矿种: {', '.join(minerals)}")
    mineral = input("🎯 目标矿种: ").strip()
    if not mineral:
        print("❌ 未输入矿种")
        return

    # 是否下载
    dl = input("📥 自动下载数据? (y/n, 默认 n): ").strip().lower()
    auto_download = dl == 'y'

    # 输出目录
    output_dir = input("📂 输出目录 (默认 ./output): ").strip() or "./output"

    # Buffer
    buffer_str = input("📏 ROI 外扩距离 km (默认 20): ").strip()
    buffer_km = float(buffer_str) if buffer_str else 20.0

    run_pipeline(roi_path, mineral, output_dir, auto_download, buffer_km)


if __name__ == "__main__":
    main()
