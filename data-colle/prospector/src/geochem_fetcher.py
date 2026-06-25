"""地球化学资料获取器 — GEOROC / 元素背景值 / 化探链接"""

import csv
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote, urlencode

import requests

from .logger import get_logger
from .http_client import get as http_get
from config import GEOROC_API

logger = get_logger("geochem")


# ============================================================
# 中国水系沉积物 39 种元素背景值 (史长义等, 2016)
# 根据构造单元和景观区提供参考
# ============================================================

# 全国及各构造单元 39 种元素水系沉积物背景值 (参考值, ppm)
# 数据来源: 史长义, 梁萌, 冯斌. (2016). 地球科学, 41(2): 234-251.
ELEMENT_BACKGROUNDS = {
    "全国": {
        "Ag": 0.078, "As": 9.2, "Au": 0.0013, "B": 47, "Ba": 490, "Be": 1.9,
        "Bi": 0.32, "Cd": 0.15, "Co": 12.5, "Cr": 60, "Cu": 22, "F": 500,
        "Hg": 0.032, "La": 37, "Li": 30, "Mn": 650, "Mo": 0.8, "Nb": 14,
        "Ni": 26, "P": 680, "Pb": 24, "Sb": 0.7, "Sn": 3.1, "Sr": 200,
        "Th": 11, "Ti": 4000, "U": 2.5, "V": 80, "W": 1.8, "Y": 24,
        "Zn": 70, "Zr": 260,
        "SiO2": 64.5, "Al2O3": 12.6, "Fe2O3": 4.5, "K2O": 2.5,
        "Na2O": 1.8, "CaO": 2.3, "MgO": 1.6,
    },
    "天山-兴蒙造山系": {
        "Ag": 0.071, "As": 8.8, "Au": 0.0011, "Cu": 20, "Mo": 0.7,
        "Pb": 22, "Zn": 65, "W": 1.5, "Sn": 2.5,
    },
    "华北克拉通": {
        "Ag": 0.065, "As": 8.2, "Au": 0.0012, "Cu": 22, "Mo": 0.8,
        "Pb": 24, "Zn": 68, "W": 1.7, "Sn": 2.8,
    },
    "秦岭-大别造山带": {
        "Ag": 0.085, "As": 13.5, "Au": 0.0020, "Cu": 30, "Mo": 1.2,
        "Pb": 28, "Zn": 85, "W": 2.2, "Sn": 3.5,
    },
    "扬子克拉通": {
        "Ag": 0.090, "As": 12.8, "Au": 0.0015, "Cu": 28, "Mo": 1.0,
        "Pb": 28, "Zn": 82, "W": 2.0, "Sn": 3.8,
    },
    "华南造山系": {
        "Ag": 0.095, "As": 14.2, "Au": 0.0018, "Cu": 25, "Mo": 1.1,
        "Pb": 32, "Zn": 88, "W": 3.5, "Sn": 6.0,
    },
    "西藏-三江造山系": {
        "Ag": 0.082, "As": 14.0, "Au": 0.0015, "Cu": 25, "Mo": 0.9,
        "Pb": 25, "Zn": 78, "W": 1.6, "Sn": 3.0,
    },
}

# 全球上地壳平均丰度 (Rudnick & Gao, 2013, ppm)
# 用于境外地区替代中国水系沉积物背景值
CRUSTAL_ABUNDANCE = {
    "Ag": 0.053, "As": 4.8, "Au": 0.0013, "B": 47, "Ba": 628, "Be": 2.1,
    "Bi": 0.16, "Cd": 0.09, "Co": 17.3, "Cr": 92, "Cu": 28, "F": 611,
    "Hg": 0.05, "La": 31, "Li": 24, "Mn": 775, "Mo": 1.1, "Nb": 12,
    "Ni": 47, "P": 757, "Pb": 17, "Sb": 0.4, "Sn": 2.1, "Sr": 320,
    "Th": 10.5, "Ti": 4100, "U": 2.7, "V": 97, "W": 1.4, "Y": 21,
    "Zn": 67, "Zr": 193,
}


def get_element_backgrounds(
    roi: Dict[str, Any],
    elements: Optional[List[str]] = None,
    location: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    根据 ROI 位置返回元素背景值（优先构造单元，兜底全国）

    Args:
        roi: parse_roi 的输出
        elements: 关心的元素列表（如矿种的指示元素），None 则返回全部
        location: analyze_roi_location() 的输出

    Returns:
        {
            "source_unit": str,  # 使用的构造单元名
            "backgrounds": {...},  # 该单元的背景值
            "national_reference": {...},  # 全国对比值
            "anomaly_thresholds": {...},
        }
    """
    # 判断是否境外
    is_overseas = location and location.get('overseas')

    if is_overseas:
        # 境外：使用全球上地壳丰度 (Rudnick & Gao, 2013)
        unit_name = "全球上地壳"
        bgs_unit = dict(CRUSTAL_ABUNDANCE)
        source_ref = "Rudnick & Gao (2013) Continental Crust Composition"
    else:
        # 中国境内：使用构造单元背景值
        unit_name = "全国"
        if location and location.get('center_tectonic'):
            unit_name = location['center_tectonic'].get('element_bg_key', '全国')
            if unit_name not in ELEMENT_BACKGROUNDS:
                unit_name = "全国"
        bgs_unit = dict(ELEMENT_BACKGROUNDS.get(unit_name, ELEMENT_BACKGROUNDS["全国"]))
        source_ref = "史长义等 (2016) 中国水系沉积物39种元素系列背景值, 地球科学, 41(2): 234-251"

    bgs_national = ELEMENT_BACKGROUNDS["全国"]

    if elements:
        bgs_unit = {k: v for k, v in bgs_unit.items() if k in elements}
        bgs_national = {k: v for k, v in bgs_national.items() if k in elements}

    # 用构造单元背景值计算异常下限
    anomaly_threshold = {}
    for elem, val in bgs_unit.items():
        if val == 0:
            continue
        anomaly_threshold[elem] = {
            "background": round(val, 4),
            "weak_anomaly": round(val * 1.5, 4),
            "moderate_anomaly": round(val * 2.0, 4),
            "strong_anomaly": round(val * 3.0, 4),
        }

    return {
        "source_unit": unit_name,
        "source_reference": source_ref,
        "unit_backgrounds": bgs_unit,
        "national_reference": bgs_national,
        "anomaly_thresholds": anomaly_threshold,
    }


# ============================================================
# GEOROC 全球火成岩地球化学数据
# ============================================================



def query_georoc(
    roi: Dict[str, Any],
    rock_types: Optional[List[str]] = None,
    elements: Optional[List[str]] = None,
    output_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    查询 GEOROC 数据库中 ROI 范围内的火成岩地球化学数据

    GEOROC 新版 API (DIGIS/Göttingen) 支持按坐标范围查询

    Args:
        roi: parse_roi 的输出
        rock_types: 岩石类型列表 (如 ["Granite", "Diorite"])
        elements: 元素列表
        output_dir: 输出目录（用于保存结果）

    Returns:
        查询结果或 None
    """
    b = roi['bbox']

    try:
        # GEOROC API 查询参数
        params = {
            "min_latitude": b['south'],
            "max_latitude": b['north'],
            "min_longitude": b['west'],
            "max_longitude": b['east'],
            "format": "json",
        }

        if rock_types:
            params["rock_type"] = ",".join(rock_types[:5])

        logger.info("查询 GEOROC 数据库...")
        response = http_get(
            f"{GEOROC_API}queries/samples",
            params=params,
            timeout=60,
        )

        if response.status_code == 200:
            data = response.json()
            n_samples = len(data) if isinstance(data, list) else data.get('count', 0)
            logger.info("GEOROC 查询结果: %d 个样品", n_samples)

            if output_dir and n_samples > 0:
                output_dir = Path(output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                outfile = output_dir / "georoc_results.json"
                import json
                with open(outfile, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                return {
                    "file": str(outfile),
                    "n_samples": n_samples,
                    "url": f"{GEOROC_API}queries/samples?{urlencode(params)}",
                    "source": "GEOROC (Göttingen DIGIS)",
                }

            return {"n_samples": n_samples, "source": "GEOROC"}

        elif response.status_code == 404:
            logger.info("GEOROC: ROI 范围内暂无数据")
            return None
        else:
            logger.warning("GEOROC API 返回 %d", response.status_code)
            return None

    except requests.exceptions.Timeout:
        logger.warning("GEOROC 查询超时")
        return None
    except Exception as e:
        logger.warning("GEOROC 查询失败: %s", e)
        return None


def generate_georoc_url(
    roi: Dict[str, Any],
    rock_types: Optional[List[str]] = None,
) -> str:
    """生成 GEOROC 网页查询链接（备用）"""
    b = roi['bbox']
    params = {
        "min_latitude": b['south'],
        "max_latitude": b['north'],
        "min_longitude": b['west'],
        "max_longitude": b['east'],
    }
    if rock_types:
        params["rock_type"] = ",".join(rock_types[:5])

    return f"https://georoc.eu/?" + urlencode(params)


def fetch_all_geochemical(
    roi: Dict[str, Any],
    output_dir: Path,
    mineral: str,
    mineral_info: Optional[Dict] = None,
    location: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    获取所有地球化学资料（位置感知：使用构造单元特定背景值）
    """
    logger.info("收集地球化学资料...")

    geo_dir = output_dir / "03_地球化学资料"
    geo_dir.mkdir(parents=True, exist_ok=True)

    # 1. 元素背景值（构造单元特定）
    elements = mineral_info.get("all_key_elements", []) if mineral_info else []
    backgrounds = get_element_backgrounds(roi, elements, location)
    logger.info("获取 %d 种元素背景值 (构造单元: %s)", len(backgrounds['unit_backgrounds']), backgrounds['source_unit'])

    # 2. GEOROC
    rock_types = mineral_info.get("georoc_rock_types", []) if mineral_info else []
    georoc = query_georoc(roi, rock_types, elements, geo_dir / "georoc")

    # 3. 化探链接（使用 geo_fetcher 的链接生成）
    from .geo_fetcher import generate_ngac_geochem_links
    ngac_links = generate_ngac_geochem_links(roi, mineral)

    return {
        "backgrounds": backgrounds,
        "georoc": georoc,
        "ngac_links": ngac_links,
    }
