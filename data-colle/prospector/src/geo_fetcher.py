"""地质资料获取器 — 地质图检索链接 / 在线地质图出图 / 学术文献"""

import io
import math
import queue
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote

from .logger import get_logger
from .http_client import get as http_get
from .roi_parser import get_bbox_tuple
from config import NGAC_SEARCH_PAGE, ONEGEOLOGY_URL, GEOLOGY_MAP_TIMEOUT

logger = get_logger("geo")


# ============================================================
# 在线地质图出图（Macrostrat 全球地质底图，可公网访问 / CC-BY 4.0）
# OneGeology 中国 1:100万 图层 WMS 在 cgs.gov.cn:8080，跨境多不可达，
# 故改用 Macrostrat 栅格瓦片拼接 ROI 区域地质图。
# ============================================================

MACROSTRAT_TILE_URL = "https://tiles.macrostrat.org/carto/{z}/{x}/{y}.png"


def _deg2num(lon: float, lat: float, z: int):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _num2deg(x: float, y: float, z: int):
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


def _fetch_geology_map_with_timeout(roi: Dict[str, Any], output_dir: Path) -> Optional[Dict[str, Any]]:
    """Run geology tile rendering with a hard wall-clock timeout.

    External tile services can occasionally hang below requests' per-call timeout.
    The geology map is optional, so timeout here should degrade to links and let
    the rest of the collection pipeline continue.
    """
    result_q = queue.Queue(maxsize=1)

    def worker():
        try:
            result_q.put((True, fetch_geology_map(roi, output_dir)), block=False)
        except Exception as exc:
            result_q.put((False, exc), block=False)

    timeout = max(1, int(GEOLOGY_MAP_TIMEOUT or 90))
    thread = threading.Thread(target=worker, name="geology-map-fetch", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        logger.warning("地质图出图超过 %ss，降级为在线查看链接", timeout)
        return None
    try:
        ok, payload = result_q.get_nowait()
    except queue.Empty:
        logger.warning("地质图出图无返回，降级为在线查看链接")
        return None
    if ok:
        return payload
    logger.warning("地质图出图失败: %s", payload)
    return None


def fetch_geology_map(roi: Dict[str, Any], output_dir: Path) -> Optional[Dict[str, Any]]:
    """
    用 Macrostrat 全球地质瓦片拼出 ROI 区域地质图并叠加 ROI 边界，出 PNG。

    返回 {"map","source","note"}；任一步失败返回 None（降级为在线查看链接）。
    """
    try:
        from PIL import Image
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        matplotlib.rcParams['font.sans-serif'] = [
            'Hiragino Sans GB', 'Heiti TC', 'STHeiti', 'SimHei',
            'Noto Sans CJK SC', 'DejaVu Sans',
        ]
        matplotlib.rcParams['axes.unicode_minus'] = False
    except ImportError:
        logger.warning("缺少 PIL/matplotlib，跳过地质图出图")
        return None

    try:
        w, s, e, n_ = get_bbox_tuple(roi, use_expanded=True)

        # 选 zoom：使覆盖瓦片数 ≤ 4×4，取尽量大的 zoom（更清晰）
        z = 6
        for ztry in range(11, 4, -1):
            x0 = int(_deg2num(w, s, ztry)[0]); x1 = int(_deg2num(e, n_, ztry)[0])
            y0 = int(_deg2num(w, n_, ztry)[1]); y1 = int(_deg2num(e, s, ztry)[1])
            if (x1 - x0 + 1) <= 4 and (y1 - y0 + 1) <= 4:
                z = ztry
                break

        xt0 = int(_deg2num(w, s, z)[0]); xt1 = int(_deg2num(e, n_, z)[0])
        yt0 = int(_deg2num(w, n_, z)[1]); yt1 = int(_deg2num(e, s, z)[1])

        ts = 512
        mosaic = Image.new("RGBA", ((xt1 - xt0 + 1) * ts, (yt1 - yt0 + 1) * ts))
        got = 0
        for xt in range(xt0, xt1 + 1):
            for yt in range(yt0, yt1 + 1):
                url = MACROSTRAT_TILE_URL.format(z=z, x=xt, y=yt)
                resp = http_get(url, timeout=20)
                if resp.status_code == 200 and resp.content:
                    tile = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    mosaic.paste(tile, ((xt - xt0) * ts, (yt - yt0) * ts))
                    got += 1
        if got == 0:
            logger.warning("Macrostrat 瓦片未取到，跳过地质图出图")
            return None

        # 瓦片块经纬度范围（小范围近似，Mercator 失真可忽略）
        lon_min, lat_max = _num2deg(xt0, yt0, z)
        lon_max, lat_min = _num2deg(xt1 + 1, yt1 + 1, z)

        fig, ax = plt.subplots(figsize=(9, 8), dpi=120)
        ax.imshow(mosaic, extent=[lon_min, lon_max, lat_min, lat_max], origin="upper")
        # 叠加 ROI 外接框 + 中心
        b = roi.get("bbox", {})
        if b:
            ax.plot([b['west'], b['east'], b['east'], b['west'], b['west']],
                    [b['south'], b['south'], b['north'], b['north'], b['south']],
                    'r-', linewidth=1.6, label='ROI')
        c = roi.get("center", {})
        if c.get('lon') is not None:
            ax.plot(c['lon'], c['lat'], marker='*', color='yellow', markersize=14,
                    markeredgecolor='black', markeredgewidth=0.8, zorder=5)
        ax.set_xlim(max(lon_min, w - (e - w)), min(lon_max, e + (e - w)))
        ax.set_ylim(max(lat_min, s - (n_ - s)), min(lat_max, n_ + (n_ - s)))
        ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
        ax.set_title("ROI 区域地质图（Macrostrat 全球地质底图）")
        if b:
            ax.legend(loc='upper right', fontsize=9)

        out_dir = Path(output_dir) / "01_地质资料"
        out_dir.mkdir(parents=True, exist_ok=True)
        png = out_dir / "geology_map.png"
        fig.tight_layout()
        fig.savefig(png, bbox_inches="tight", dpi=120)
        plt.close(fig)

        logger.info("地质图已出图: %s (zoom=%d, %d tiles)", png, z, got)
        return {
            "map": str(png),
            "source": "Macrostrat — Global Geologic Map (CC-BY 4.0)",
            "note": "基于 Macrostrat 全球地质底图按 ROI 范围拼接；点查询可溯源岩性/年代",
        }
    except Exception as ex:
        logger.warning("地质图出图失败: %s", ex)
        return None


def _get_1m_map_sheet(roi: Dict[str, Any]) -> str:
    """
    根据经纬度推算 1:100万 图幅号（用于 NGAC 检索）
    中国 1:100万 图幅编号: 行(A-U) + 列(1-60)
    """
    center = roi['center']
    lat = center['lat']
    lon = center['lon']

    # 纬度行号 (从赤道开始，每4度一个字母，A=0-4°N)
    if lat < 0:
        letter_idx = int((-lat) / 4)
        row = chr(ord('A') + letter_idx)
        row = 'S' + row  # 南半球前缀
    else:
        letter_idx = int(lat / 4)
        if letter_idx > 21:
            letter_idx = 21
        row = chr(ord('A') + letter_idx)

    # 经度列号 (从180°W开始，每6度)
    col = int((lon + 180) / 6) + 1
    if col > 60:
        col = 60
    if col < 1:
        col = 1

    return f"{row}{col:02d}"


def generate_ngac_geology_links(roi: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    生成 NGAC 地质图检索链接
    """
    b = roi['bbox']
    map_sheet = _get_1m_map_sheet(roi)
    center = roi['center']

    links = [
        {
            "label": "全国地质资料馆 — 地质图检索入口",
            "url": NGAC_SEARCH_PAGE,
            "note": "NGAC 检索平台，进入后手动输入关键词搜索"
        },
        {
            "label": f"▶ 建议检索词: {map_sheet}",
            "url": NGAC_SEARCH_PAGE,
            "note": "1:100万 图幅号，含地质图+说明书"
        },
        {
            "label": "▶ 建议检索词: 区域地质调查报告 / 区域地质图 / 地质矿产图",
            "url": NGAC_SEARCH_PAGE,
            "note": f"ROI 中心: {center['lon']:.1f}°E, {center['lat']:.1f}°N"
        },
    ]

    return links


def generate_ngac_mineral_links(roi: Dict[str, Any], mineral: str) -> List[Dict[str, str]]:
    """生成矿产地 + 钻孔数据检索链接"""
    return [
        {
            "label": f"NGAC 矿产地: '{mineral}矿床' / '{mineral}矿产地'",
            "url": NGAC_SEARCH_PAGE,
            "note": "进入后搜索矿产地关键词"
        },
        {
            "label": "NGAC 钻孔数据库",
            "url": "https://www.ngac.cn/125cms/c/qggnew/zljs.htm",
            "note": "全国地质资料馆检索，含钻孔数据"
        },
    ]


def generate_ngac_geochem_links(roi: Dict[str, Any], mineral: str) -> List[Dict[str, str]]:
    """生成化探数据检索链接"""
    map_sheet = _get_1m_map_sheet(roi)

    return [
        {
            "label": f"NGAC 化探: {map_sheet} 图幅地球化学图",
            "url": NGAC_SEARCH_PAGE,
            "note": f"检索词: '{map_sheet} 地球化学' — 39种元素，1039张图件"
        },
        {
            "label": f"NGAC 化探: '{mineral}' 化探异常图",
            "url": NGAC_SEARCH_PAGE,
            "note": f"检索词: '{mineral} 化探异常'"
        },
        {
            "label": "国家级地质资料数据中心",
            "url": "https://www.ngac.cn",
            "note": "NGAC 门户，含化探/物探/遥感等公开数据，DOI: 10.23650/data.G.2018.NGA122099.K1.1.1.V1"
        },
    ]


# ============================================================
# 学术文献检索链接生成
# ============================================================

def generate_cnki_links(roi: Dict[str, Any], mineral: str,
                        mineral_info: Optional[Dict] = None,
                        location: Optional[Dict] = None) -> List[Dict[str, str]]:
    """
    生成 CNKI 学术文献检索链接（带构造单元/区域定位）
    """
    center = roi['center']

    # 构造区域关键词
    region_terms = []
    if location:
        tu = location.get('center_tectonic')
        if tu:
            region_terms.append(tu['name'])
        pb = location.get('petroleum_basin')
        if pb:
            region_terms.append(pb['name'])

    # 经纬度兜底
    region_terms.append(f"({center['lon']:.1f}E,{center['lat']:.1f}N)")

    links = []
    for term in region_terms[:2]:  # 取前2个最相关的区域词
        # CNKI
        su = f"{term} {mineral}矿 成矿 地质特征"
        links.append({
            "label": f"CNKI: [{term}] {mineral}矿床地质",
            "url": f"https://kns.cnki.net/kns8s/search?keyword={quote(su)}",
            "note": "按构造单元/盆地+矿种精准检索"
        })
        # 化探
        su2 = f"{term} {mineral} 化探 地球化学"
        links.append({
            "label": f"CNKI: [{term}] {mineral}化探异常",
            "url": f"https://kns.cnki.net/kns8s/search?keyword={quote(su2)}",
            "note": "检索该区域的化探研究成果"
        })

    # Google Scholar
    if location:
        tu = location.get('center_tectonic')
        if tu:
            en = tu.get('name_en', '')
            scholar_term = f"{mineral} deposit {en}"
            links.append({
                "label": f"Google Scholar: {scholar_term}",
                "url": f"https://scholar.google.com/scholar?q={quote(scholar_term)}",
                "note": "英文文献"
            })

    return links


def generate_onegeology_link(roi: Dict[str, Any]) -> str:
    """生成 OneGeology 全球地质图查看链接"""
    b = roi['bbox']
    return (
        f"{ONEGEOLOGY_URL}"
        f"?bbox={b['west']},{b['south']},{b['east']},{b['north']}"
    )


def fetch_all_geological(
    roi: Dict[str, Any],
    output_dir: Path,
    mineral: str,
    mineral_info: Optional[Dict] = None,
    location: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    获取所有地质资料（生成检索链接 + 元数据）

    Returns:
        {
            "ngac_geology": [...],
            "ngac_mineral": [...],
            "ngac_geochem": [...],
            "cnki": [...],
            "onegeology": "...",
            "map_sheet": str,
        }
    """
    logger.info("收集地质资料...")

    results = {
        "ngac_geology": generate_ngac_geology_links(roi),
        "ngac_mineral": generate_ngac_mineral_links(roi, mineral),
        "ngac_geochem": generate_ngac_geochem_links(roi, mineral),
        "cnki": generate_cnki_links(roi, mineral, mineral_info, location),
        "onegeology": generate_onegeology_link(roi),
        "geology_map": _fetch_geology_map_with_timeout(roi, output_dir),
        "map_sheet": _get_1m_map_sheet(roi),
    }

    n_links = (
        len(results["ngac_geology"]) +
        len(results["ngac_mineral"]) +
        len(results["ngac_geochem"]) +
        len(results["cnki"])
    )

    logger.info("生成 %d 个地质资料检索链接", n_links)
    logger.info("1:100万 图幅号: %s", results['map_sheet'])

    return results
