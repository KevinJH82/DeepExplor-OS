"""P3 UI 一致性测试 — conftest.py"""
import pytest
from pathlib import Path

# All HTML templates across 4 subsystems
TEMPLATE_ROOTS = [
    Path("/opt/deepexplor-services/geo-downloader/web/templates"),
    Path("/opt/deepexplor-services/geo-analyser/templates"),
    Path("/opt/deepexplor-services/geo-analyser/NewPage/遥感分析平台浅色风格_v5"),
    Path("/opt/deepexplor-services/geo-reporter/web/templates"),
    Path("/opt/deepexplor-services/geo-exploration/Python_Project/web_app/templates"),
]

# Additional portal pages
PORTAL_PAGES = [
    Path("/opt/deepexplor-system/index.html"),
    Path("/opt/deepexplor-system/1st Page/login.html"),
    Path("/opt/deepexplor-system/2nd Page/index.html"),
]

# 2nd Page sub-pages (mineral detection platform)
SECOND_PAGE_SUB = Path("/opt/deepexplor-system/2nd Page/地球物理卫星监测管理平台_v12")


def collect_all_html():
    """收集所有 HTML 模板文件"""
    files = list(PORTAL_PAGES)
    
    for root in TEMPLATE_ROOTS:
        if root.exists():
            files.extend(root.glob("*.html"))
    
    if SECOND_PAGE_SUB.exists():
        files.extend(SECOND_PAGE_SUB.glob("*.html"))
    
    return [f for f in files if f.exists()]


@pytest.fixture(scope="module")
def all_html_files():
    """所有 HTML 模板文件列表"""
    return collect_all_html()


@pytest.fixture(scope="module")
def all_html_content(all_html_files):
    """所有 HTML 文件内容字典 {path: content}"""
    result = {}
    for f in all_html_files:
        try:
            result[str(f)] = f.read_text(errors='ignore')
        except Exception:
            result[str(f)] = ""
    return result


@pytest.fixture(scope="module")
def template_inventory(all_html_files):
    """按子系统分组的模板清单"""
    inventory = {}
    for f in all_html_files:
        path_str = str(f)
        if "geo-downloader" in path_str:
            subsystem = "geo-downloader"
        elif "geo-analyser" in path_str:
            subsystem = "geo-analyser"
        elif "geo-reporter" in path_str:
            subsystem = "geo-reporter"
        elif "geo-exploration" in path_str:
            subsystem = "geo-exploration"
        elif "deepexplor-system" in path_str:
            subsystem = "portal"
        else:
            subsystem = "unknown"
        
        if subsystem not in inventory:
            inventory[subsystem] = []
        inventory[subsystem].append(str(f))
    
    return inventory
