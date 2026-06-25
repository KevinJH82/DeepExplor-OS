"""
geology_map.py — ROI 标准地质图（Macrostrat 综合地质图瓦片叠加）

Macrostrat carto 瓦片（https://tiles.macrostrat.org/carto/{z}/{x}/{y}.png，CC BY 4.0）
含地层单元色块（units）与断层/构造线（lines），采用与 basemap 相同的 XYZ Web-Mercator
切片方案，可直接半透明叠加到现有底图上。

注意：Macrostrat 详细覆盖以北美为主，全球多数地区无覆盖。本模块先做覆盖率探测，
无覆盖时返回 None（跳过该图，避免出"仅卫星底图"的误导图件）。
"""

import io
import math
import ssl
import tempfile
import urllib.request
from types import SimpleNamespace
from typing import Optional

from .basemap import render_basemap, _zoom_for_span

MACROSTRAT_CARTO = "https://tiles.macrostrat.org/carto/{z}/{x}/{y}.png"

# 全球兜底：CGMW 世界地质图（1:50M，含地层/构造单元，OneGeology/BRGM 托管，全球覆盖）
CGMW_WMS_BASE = "http://mapsref.brgm.fr/wxs/1GG/CGMW_Bedrock_and_Structural_Geology"
CGMW_WMS_LAYER = "World_CGMW_50M_Geology"

# 覆盖率阈值：质心邻域瓦片非透明像素占比低于此值视为"无地质图覆盖"
_COVERAGE_MIN_RATIO = 0.005


def _lon_to_tile_x(lon: float, zoom: int) -> float:
    return (lon + 180.0) / 360.0 * (2 ** zoom)


def _lat_to_tile_y(lat: float, zoom: int) -> float:
    lat_r = math.radians(lat)
    return (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * (2 ** zoom)


def _has_coverage(center_lon: float, center_lat: float, zoom: int) -> bool:
    """探测 Macrostrat 在质心邻域是否有地质图覆盖（非透明像素占比）。"""
    try:
        from PIL import Image
    except ImportError:
        return False

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    cx = int(_lon_to_tile_x(center_lon, zoom))
    cy = int(_lat_to_tile_y(center_lat, zoom))
    max_tile = 2 ** zoom

    total_px = 0
    nonempty_px = 0
    # 中心 + 上下左右共 5 个瓦片（足够判断覆盖，控制联网耗时）
    for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
        tx = (cx + dx) % max_tile
        ty = cy + dy
        if ty < 0 or ty >= max_tile:
            continue
        url = MACROSTRAT_CARTO.format(z=zoom, x=tx, y=ty)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "geo-reporter/0.1"})
            with urllib.request.urlopen(req, timeout=6, context=ssl_ctx) as resp:
                im = Image.open(io.BytesIO(resp.read())).convert("RGBA")
        except Exception:
            continue
        px = im.getdata()
        total_px += len(px)
        nonempty_px += sum(1 for _, _, _, a in px if a > 10)
    if total_px == 0:
        return False
    return (nonempty_px / total_px) >= _COVERAGE_MIN_RATIO


def _render_cgmw_fallback(min_lon: float, min_lat: float,
                          max_lon: float, max_lat: float) -> Optional[str]:
    """
    全球兜底：请求 CGMW 世界地质图 WMS（EPSG:4326 等经纬），独立成图，叠加研究区红框。
    返回临时 PNG 路径或 None。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    dlon = (max_lon - min_lon) or 0.1
    dlat = (max_lat - min_lat) or 0.1
    # 四周各扩 15%，使色块更完整、红框居中有参照
    pad_x, pad_y = dlon * 0.15, dlat * 0.15
    pminlon, pmaxlon = min_lon - pad_x, max_lon + pad_x
    pminlat, pmaxlat = min_lat - pad_y, max_lat + pad_y
    pdlon, pdlat = (pmaxlon - pminlon), (pmaxlat - pminlat)

    # 等经纬 plate carrée：宽高比按经纬跨度，避免拉伸失真
    height = 720
    width = int(max(300, min(1200, round(height * pdlon / pdlat))))
    bbox = f"{pminlon},{pminlat},{pmaxlon},{pmaxlat}"
    url = (f"{CGMW_WMS_BASE}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
           f"&LAYERS={CGMW_WMS_LAYER}&STYLES=&SRS=EPSG:4326&BBOX={bbox}"
           f"&WIDTH={width}&HEIGHT={height}&FORMAT=image/png&TRANSPARENT=TRUE")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "geo-reporter/0.1"})
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
            wms = Image.open(io.BytesIO(resp.read())).convert("RGBA")
    except Exception as e:
        print(f"[GeologyMap] CGMW WMS 兜底失败：{e}")
        return None

    base = Image.new("RGB", (width, height), (255, 255, 255))
    base.paste(wms, (0, 0), wms)
    draw = ImageDraw.Draw(base)

    def gx(lon):
        return (lon - pminlon) / pdlon * width

    def gy(lat):
        return (pmaxlat - lat) / pdlat * height

    x0, x1 = gx(min_lon), gx(max_lon)
    y0, y1 = gy(max_lat), gy(min_lat)
    if abs(x1 - x0) < 14:
        m = (x0 + x1) / 2; x0, x1 = m - 7, m + 7
    if abs(y1 - y0) < 14:
        m = (y0 + y1) / 2; y0, y1 = m - 7, m + 7
    draw.rectangle([x0, y0, x1, y1], outline=(220, 30, 30), width=3)
    for o in range(3):
        draw.rectangle([o, o, width - 1 - o, height - 1 - o], outline=(212, 168, 67))

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    base.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name


def render_geology_map(min_lon: float, min_lat: float,
                       max_lon: float, max_lat: float) -> Optional[dict]:
    """
    渲染 ROI 标准地质图，返回图件 dict 或 None：
    - 优先 Macrostrat 综合地质图叠加底图（覆盖区精细）；
    - 无 Macrostrat 覆盖时，兜底用全球 CGMW 世界地质图 WMS（全球覆盖，较粗）。
    """
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    span = max(max_lon - min_lon, max_lat - min_lat)
    zoom = _zoom_for_span(span)

    if _has_coverage(center_lon, center_lat, zoom):
        shim = SimpleNamespace(
            min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat,
            centroid_lon=center_lon, centroid_lat=center_lat,
        )
        path = render_basemap(
            shim, width_px=900, height_px=720, draw_aoi_box=True,
            overlay_url_template=MACROSTRAT_CARTO, overlay_opacity=0.75,
        )
        if path:
            return {
                "path": path,
                "caption": ("ROI 区域地质图（Macrostrat 综合地质图：色块为地层单元、线为断层/构造；"
                            "红框为研究区。数据来源 Macrostrat, CC BY 4.0）"),
                "source": "Macrostrat",
            }

    # 注：CGMW 1:50M 世界地质图兜底（_render_cgmw_fallback）已停用——对小 ROI 过粗（常为单一色块），
    # 经评估无实用价值；地质图以 geo-stru 本地解译图为优先来源，Macrostrat 为公开覆盖区次选。
    print("[GeologyMap] Macrostrat 无覆盖，跳过公开地质图（geo-stru 为优先来源）")
    return None
