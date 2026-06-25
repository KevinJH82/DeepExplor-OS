"""中国大地构造单元空间数据库 — 根据经纬度自动判断构造归属"""

from typing import Dict, List, Optional, Tuple
from shapely.geometry import Point, Polygon, box


# ============================================================
# 一级构造单元简化多边形 (WGS84)
# 基于中国大地构造图简化边界
# ============================================================

TECTONIC_UNITS = [
    {
        "id": "tianshan_xingmeng",
        "name": "天山-兴蒙造山系",
        "name_en": "Tianshan-Xingmeng Orogenic System",
        "level": 1,
        "polygon": Polygon([
            (75.0, 40.0), (96.0, 42.0), (110.0, 42.0), (120.0, 42.0),
            (135.0, 48.0), (135.0, 53.5), (120.0, 53.5), (110.0, 50.0),
            (96.0, 46.0), (80.0, 45.0), (75.0, 40.0),
        ]),
        "features": "古生代造山带，多期拼合，发育岛弧、蛇绿混杂岩",
        "major_minerals": ["Cu", "Au", "Mo", "Fe", "Pb", "Zn", "W", "Sn"],
        "major_basins": ["准噶尔盆地", "吐哈盆地", "二连盆地", "海拉尔盆地"],
        "element_bg_key": "天山-兴蒙造山系",
    },
    {
        "id": "north_china_craton",
        "name": "华北克拉通",
        "name_en": "North China Craton",
        "level": 1,
        "polygon": Polygon([
            (106.0, 34.0), (106.0, 40.0), (110.0, 42.0), (120.0, 42.0),
            (125.0, 40.0), (125.0, 37.0), (120.0, 34.0), (118.0, 32.0),
            (115.0, 34.0), (106.0, 34.0),
        ]),
        "features": "太古宙-古元古代基底，中生代活化克拉通",
        "major_minerals": ["Fe", "Au", "Cu", "Mo", "Pb", "Zn", "Al", "Coal"],
        "major_basins": ["渤海湾盆地", "鄂尔多斯盆地", "南华北盆地"],
        "element_bg_key": "华北克拉通",
    },
    {
        "id": "qinling_dabie",
        "name": "秦岭-大别造山带",
        "name_en": "Qinling-Dabie Orogenic Belt",
        "level": 1,
        "polygon": Polygon([
            (102.0, 32.0), (106.0, 34.0), (115.0, 34.0), (118.0, 32.0),
            (120.0, 32.0), (120.0, 30.5), (116.0, 30.0), (110.0, 31.0),
            (102.0, 32.0),
        ]),
        "features": "中生代碰撞造山带，多金属成矿带",
        "major_minerals": ["Au", "Mo", "Pb", "Zn", "Ag", "Cu", "Sb", "Hg"],
        "major_basins": ["南襄盆地"],
        "element_bg_key": "秦岭-大别造山带",
    },
    {
        "id": "yangtze_craton",
        "name": "扬子克拉通",
        "name_en": "Yangtze Craton",
        "level": 1,
        "polygon": Polygon([
            (102.0, 26.0), (102.0, 32.0), (110.0, 31.0), (116.0, 30.0),
            (120.0, 30.5), (122.0, 31.0), (122.0, 28.0), (118.0, 26.0),
            (112.0, 26.0), (106.0, 26.0), (102.0, 26.0),
        ]),
        "features": "新元古代基底，中生代活化，长江中下游成矿带",
        "major_minerals": ["Cu", "Fe", "Au", "Pb", "Zn", "Ag", "W", "Sn", "P"],
        "major_basins": ["四川盆地", "江汉盆地"],
        "element_bg_key": "扬子克拉通",
    },
    {
        "id": "south_china_orogen",
        "name": "华南造山系",
        "name_en": "South China Orogenic System",
        "level": 1,
        "polygon": Polygon([
            (106.0, 22.0), (106.0, 26.0), (112.0, 26.0), (118.0, 26.0),
            (122.0, 28.0), (122.0, 24.0), (120.0, 22.0), (118.0, 22.0),
            (112.0, 22.0), (108.0, 21.0), (106.0, 22.0),
        ]),
        "features": "加里东-印支-燕山期多期造山，大规模花岗岩",
        "major_minerals": ["W", "Sn", "Mo", "Bi", "Cu", "Au", "Pb", "Zn", "REE", "U"],
        "major_basins": ["珠江口盆地", "北部湾盆地", "莺歌海盆地"],
        "element_bg_key": "华南造山系",
    },
    {
        "id": "tibet_sanjiang",
        "name": "西藏-三江造山系",
        "name_en": "Tibet-Sanjiang Orogenic System",
        "level": 1,
        "polygon": Polygon([
            (78.0, 27.0), (78.0, 36.0), (96.0, 36.0), (102.0, 32.0),
            (102.0, 26.0), (98.0, 22.0), (96.0, 22.0), (96.0, 27.0),
            (78.0, 27.0),
        ]),
        "features": "特提斯构造域，多岛弧-盆地体系，新生代碰撞造山",
        "major_minerals": ["Cu", "Au", "Pb", "Zn", "Ag", "Mo", "Cr", "Fe", "Li"],
        "major_basins": ["柴达木盆地", "羌塘盆地", "伦坡拉盆地"],
        "element_bg_key": "西藏-三江造山系",
    },
    {
        "id": "tarim_craton",
        "name": "塔里木克拉通",
        "name_en": "Tarim Craton",
        "level": 1,
        "polygon": Polygon([
            (75.0, 36.0), (75.0, 42.0), (80.0, 42.0), (96.0, 42.0),
            (96.0, 36.0), (90.0, 36.0), (87.0, 36.5), (78.0, 36.0),
            (75.0, 36.0),
        ]),
        "features": "太古宙-元古宙基底，长期稳定克拉通",
        "major_minerals": ["Oil", "Gas", "Fe", "Cu", "Pb", "Zn"],
        "major_basins": ["塔里木盆地", "库车坳陷"],
        "element_bg_key": "天山-兴蒙造山系",  # 背景值借用
    },
    {
        "id": "songliao",
        "name": "松辽盆地",
        "name_en": "Songliao Basin",
        "level": 2,
        "polygon": Polygon([
            (120.0, 42.0), (128.0, 42.0), (128.0, 48.0), (135.0, 48.0),
            (135.0, 53.5), (120.0, 53.5), (120.0, 42.0),
        ]),
        "features": "中生代裂谷-坳陷盆地，中国最大陆相油田",
        "major_minerals": ["Oil", "Gas", "Coal"],
        "major_basins": ["松辽盆地"],
        "element_bg_key": "天山-兴蒙造山系",
    },
]

# 石油盆地（更细粒度的含油气盆地）
PETROLEUM_BASINS = [
    {"name": "松辽盆地", "area_km2": 260000, "polygon": Polygon([
        (120.0, 42.0), (128.0, 42.0), (128.0, 49.0), (120.0, 49.0), (120.0, 42.0)
    ]), "main_plays": ["白垩系坳陷型", "深层火山岩气藏"], "max_well_depth": 7000},
    {"name": "渤海湾盆地", "area_km2": 200000, "polygon": Polygon([
        (114.0, 35.0), (122.0, 35.0), (122.0, 41.0), (114.0, 41.0), (114.0, 35.0)
    ]), "main_plays": ["古近系裂谷型", "潜山型"], "max_well_depth": 8000},
    {"name": "鄂尔多斯盆地", "area_km2": 370000, "polygon": Polygon([
        (106.0, 34.0), (112.0, 34.0), (112.0, 40.5), (106.0, 40.5), (106.0, 34.0)
    ]), "main_plays": ["中生界致密油", "上古生界致密气", "下古生界碳酸盐岩气"], "max_well_depth": 5000},
    {"name": "四川盆地", "area_km2": 260000, "polygon": Polygon([
        (102.0, 27.0), (110.0, 27.0), (110.0, 33.0), (102.0, 33.0), (102.0, 27.0)
    ]), "main_plays": ["志留系页岩气", "震旦-寒武系碳酸盐岩气", "须家河组致密气"], "max_well_depth": 9000},
    {"name": "塔里木盆地", "area_km2": 530000, "polygon": Polygon([
        (76.0, 36.0), (88.0, 36.0), (88.0, 42.0), (76.0, 42.0), (76.0, 36.0)
    ]), "main_plays": ["奥陶系碳酸盐岩", "库车白垩系砂岩", "塔中寒武系"], "max_well_depth": 9000},
    {"name": "准噶尔盆地", "area_km2": 130000, "polygon": Polygon([
        (80.0, 43.0), (92.0, 43.0), (92.0, 47.0), (80.0, 47.0), (80.0, 43.0)
    ]), "main_plays": ["二叠系-侏罗系常规油", "石炭系火山岩"], "max_well_depth": 7500},
    {"name": "柴达木盆地", "area_km2": 120000, "polygon": Polygon([
        (90.0, 35.0), (98.0, 35.0), (98.0, 39.0), (90.0, 39.0), (90.0, 35.0)
    ]), "main_plays": ["新生界生物气", "侏罗系致密油"], "max_well_depth": 7000},
    {"name": "珠江口盆地", "area_km2": 175000, "polygon": Polygon([
        (112.0, 17.0), (118.0, 17.0), (118.0, 23.0), (112.0, 23.0), (112.0, 17.0)
    ]), "main_plays": ["新近系-古近系海相砂岩"], "max_well_depth": 6000},
    {"name": "羌塘盆地", "area_km2": 180000, "polygon": Polygon([
        (85.0, 31.0), (96.0, 31.0), (96.0, 36.0), (85.0, 36.0), (85.0, 31.0)
    ]), "main_plays": ["中生界海相碳酸盐岩+碎屑岩"], "max_well_depth": 6000},
]


def find_tectonic_unit(lon: float, lat: float) -> Optional[Dict]:
    """根据经纬度查找所属一级构造单元"""
    point = Point(lon, lat)
    for unit in TECTONIC_UNITS:
        if unit["polygon"].contains(point):
            return {
                "id": unit["id"],
                "name": unit["name"],
                "name_en": unit["name_en"],
                "level": unit["level"],
                "features": unit["features"],
                "major_minerals": unit["major_minerals"],
                "element_bg_key": unit["element_bg_key"],
            }
    return None


def find_petroleum_basin(lon: float, lat: float) -> Optional[Dict]:
    """查找所属含油气盆地"""
    point = Point(lon, lat)
    for basin in PETROLEUM_BASINS:
        if basin["polygon"].contains(point):
            return {
                "name": basin["name"],
                "area_km2": basin["area_km2"],
                "main_plays": basin["main_plays"],
                "max_well_depth": basin["max_well_depth"],
            }
    return None


def analyze_roi_location(roi: Dict) -> Dict:
    """
    分析 ROI 空间位置，返回构造归属信息

    Args:
        roi: parse_roi 的输出（含 center 和 bbox）

    Returns:
        {
            "center_tectonic": {...},
            "intersecting_tectonics": [...],
            "petroleum_basin": {...},
            "is_marine": bool,
        }
    """
    center = roi['center']
    lon, lat = center['lon'], center['lat']

    result = {
        "center_tectonic": find_tectonic_unit(lon, lat),
        "intersecting_tectonics": [],
        "petroleum_basin": find_petroleum_basin(lon, lat),
    }

    # 检查 ROI bbox 与各构造单元的交集
    b = roi['bbox']
    roi_box = box(b['west'], b['south'], b['east'], b['north'])

    for unit in TECTONIC_UNITS:
        if roi_box.intersects(unit["polygon"]) and not roi_box.contains(unit["polygon"]):
            # ROI 跨越多个构造单元
            intersection = roi_box.intersection(unit["polygon"])
            frac = intersection.area / roi_box.area if roi_box.area > 0 else 0
            if frac > 0.05:  # 交集占比 > 5%
                result["intersecting_tectonics"].append({
                    "name": unit["name"],
                    "overlap_fraction": round(frac, 2),
                    "major_minerals": unit["major_minerals"],
                })

    # 如果中心不在已知单元内，仅在邻近中国边境时才找最近的单元
    # 中国大致范围：73-135°E, 18-54°N，超出此范围 10° 以上不做匹配
    if result["center_tectonic"] is None:
        _CHINA_BBOX = (63, 8, 145, 64)  # (west, south, east, north) 留余量
        if (_CHINA_BBOX[0] <= lon <= _CHINA_BBOX[2] and
                _CHINA_BBOX[1] <= lat <= _CHINA_BBOX[3]):
            min_dist = float('inf')
            nearest = None
            for unit in TECTONIC_UNITS:
                dist = unit["polygon"].distance(Point(lon, lat))
                if dist < min_dist:
                    min_dist = dist
                    nearest = unit
            if nearest:
                result["center_tectonic"] = {
                    "name": nearest["name"] + " (邻近)",
                    "name_en": nearest["name_en"],
                    "features": nearest["features"],
                    "major_minerals": nearest["major_minerals"],
                    "element_bg_key": nearest["element_bg_key"],
                    "distance_deg": round(min_dist, 2),
                }
                result["outside_known_units"] = True
        else:
            result["outside_known_units"] = True
            result["overseas"] = True

    return result


if __name__ == "__main__":
    # 测试
    test_points = [
        (117.975, 30.95, "铜陵"),
        (88.0, 42.0, "库尔勒"),
        (123.0, 45.0, "松原"),
        (104.0, 30.5, "成都"),
        (114.0, 23.0, "广州"),
    ]
    for lon, lat, name in test_points:
        tu = find_tectonic_unit(lon, lat)
        pb = find_petroleum_basin(lon, lat)
        print(f"\n{name} ({lon}°E, {lat}°N)")
        print(f"  构造单元: {tu['name'] if tu else '未知'}")
        print(f"  含油气盆地: {pb['name'] if pb else '无'}")
