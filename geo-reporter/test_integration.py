#!/usr/bin/env python3
"""
测试脚本：验证 KML 解析、地理编码、搜索引擎、报告生成
"""

import sys
from pathlib import Path

# 添加项目路径
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from reporter.kml_parser import parse_kml, KMLParseError
from reporter.geocoder import create_location_context, GeocoderError
from reporter.categories import get_all_categories
from reporter.search_engine import SearchEngine
from reporter.report_builder import ReportBuilder


def test_kml_parsing():
    """测试 KML 解析"""
    print("=" * 60)
    print("测试 1: KML 文件解析")
    print("=" * 60)

    # 查找参考 test_baiyin.kml 文件
    test_kml_path = Path("/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-downloader/test_baiyin.kml")

    if not test_kml_path.exists():
        print(f"❌ 测试 KML 文件不存在：{test_kml_path}")
        return False

    try:
        geometry, bbox, name, area_name, description = parse_kml(str(test_kml_path))

        print(f"✅ KML 解析成功")
        print(f"   文件名：{name}")
        print(f"   区块名称：{area_name}")
        print(f"   描述：{description[:50]}...")
        print(f"   BBox：{bbox}")
        print(f"   几何类型：{geometry.geom_type}")
        return True

    except KMLParseError as e:
        print(f"❌ KML 解析失败：{e}")
        return False


def test_geocoding(bbox, area_name, description):
    """测试地理编码"""
    print("\n" + "=" * 60)
    print("测试 2: Nominatim 反向地理编码")
    print("=" * 60)

    try:
        location = create_location_context(bbox, area_name, description)

        print(f"✅ 地理编码成功")
        print(f"   国家：{location.country}")
        print(f"   省：{location.province}")
        print(f"   市：{location.city}")
        print(f"   区：{location.district}")
        print(f"   坐标范围：{location.coords_str}")
        print(f"   中心点：({location.centroid_lat:.4f}, {location.centroid_lon:.4f})")
        return True, location

    except GeocoderError as e:
        print(f"❌ 地理编码失败：{e}")
        return False, None


def test_categories():
    """测试类别定义"""
    print("\n" + "=" * 60)
    print("测试 3: 搜索类别定义")
    print("=" * 60)

    categories = get_all_categories()
    print(f"✅ 已定义 {len(categories)} 个搜索类别：")
    for cat in categories:
        print(f"   - {cat.id}: {cat.name} ({len(cat.sub_topics)} 个子主题)")

    return len(categories) == 8


def test_prompt_rendering(location):
    """测试 Prompt 渲染"""
    print("\n" + "=" * 60)
    print("测试 4: Prompt 模板渲染")
    print("=" * 60)

    try:
        templates_dir = PROJECT_DIR / "templates"
        search_engine = SearchEngine(str(templates_dir))

        categories = get_all_categories()
        sample_cat = categories[0]  # 气候
        prompt = search_engine.render_prompt(sample_cat, location)

        print(f"✅ Prompt 渲染成功（气候类别）")
        print(f"   Prompt 长度：{len(prompt)} 字符")
        print(f"   Prompt 摘录（前 150 字）：")
        print(f"   {prompt[:150]}...")
        return True

    except Exception as e:
        print(f"❌ Prompt 渲染失败：{e}")
        return False


def test_report_builder(location):
    """测试报告生成器初始化"""
    print("\n" + "=" * 60)
    print("测试 5: 报告生成器初始化")
    print("=" * 60)

    try:
        output_dir = PROJECT_DIR / "test_reports"
        builder = ReportBuilder(str(output_dir))

        print(f"✅ 报告生成器初始化成功")
        print(f"   输出目录：{output_dir}")
        return True

    except Exception as e:
        print(f"❌ 报告生成器初始化失败：{e}")
        return False


def main():
    print("\n🧪 Geo-Reporter 集成测试\n")

    # 测试 1: KML 解析
    result1 = test_kml_parsing()
    if not result1:
        print("\n❌ KML 解析失败，停止测试")
        return False

    # 获取 KML 数据
    test_kml_path = Path("/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-downloader/test_baiyin.kml")
    geometry, bbox, name, area_name, description = parse_kml(str(test_kml_path))

    # 测试 2: 地理编码
    result2, location = test_geocoding(bbox, area_name, description)
    if not result2:
        print("\n❌ 地理编码失败，停止测试")
        return False

    # 测试 3: 类别定义
    result3 = test_categories()
    if not result3:
        print("\n❌ 类别定义验证失败")
        return False

    # 测试 4: Prompt 渲染
    result4 = test_prompt_rendering(location)
    if not result4:
        print("\n❌ Prompt 渲染失败")
        return False

    # 测试 5: 报告生成器
    result5 = test_report_builder(location)
    if not result5:
        print("\n❌ 报告生成器初始化失败")
        return False

    # 总结
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)
    print("\n下一步：启动 Web 服务器")
    print("  python3 web/app.py")
    print("  → http://localhost:8080")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
