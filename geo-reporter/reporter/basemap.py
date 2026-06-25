"""
basemap.py — 可复用的瓦片底图渲染（抽自 pptx_builder._generate_location_map）

拼接高德卫星+中文标注瓦片，绘制研究区红框；可选叠加靶区点（编号+矩形框）。
供 PPTX、Word（靶区推荐图）共用。失败时返回 None（调用方需容错）。
"""

import io
import math
import ssl
import tempfile
import urllib.request
from typing import List, Optional, Tuple


def _hot_color(t: float):
    """热力色标 hot colormap：t∈[0,1]，0=暗红、0.5=橙、1=近白。"""
    t = max(0.0, min(1.0, t))
    if t < 0.4:
        return (int(110 + (255 - 110) * (t / 0.4)), 0, 0)
    if t < 0.75:
        u = (t - 0.4) / 0.35
        return (255, int(200 * u), 0)
    u = (t - 0.75) / 0.25
    return (255, int(200 + 55 * u), int(235 * u))


# 置信等级 → (热斑半径 px, 弧圈颜色)
_GRADE_RADIUS = {"A": 50, "B": 43, "C": 36, "D": 30}
_GRADE_RING = {"A": (255, 60, 30), "B": (255, 140, 0),
               "C": (0, 210, 210), "D": (130, 200, 255)}


def _draw_targets(img, targets, geo_to_px):
    """在底图上叠加每个靶区的高热力径向晕染 + 弧形圈点（弧形=留缺口的同心弧）。"""
    from PIL import Image, ImageDraw

    W, H = img.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    pts = []
    for t in targets:
        lon = t.get("longitude"); lat = t.get("latitude")
        if lon is None or lat is None:
            continue
        px, py = geo_to_px(lon, lat)
        grade = t.get("grade", "C")
        R = _GRADE_RADIUS.get(grade, 36)
        pts.append((px, py, R, grade))
        # 径向热力：由外(暗红/低透明)向中心(近白/高不透明)堆叠同心实心圆
        steps = 28
        for i in range(steps, 0, -1):
            frac = i / steps          # 1=最外, →0 中心
            rr = R * frac
            col = _hot_color(1.0 - frac)
            alpha = int(16 + 165 * (1.0 - frac))
            od.ellipse([px - rr, py - rr, px + rr, py + rr], fill=col + (alpha,))
    # 把热力图按 alpha 混合到底图
    img.paste(overlay, (0, 0), overlay)
    # 弧形圈点：两层留缺口的同心弧
    d = ImageDraw.Draw(img)
    for px, py, R, grade in pts:
        ring = _GRADE_RING.get(grade, (0, 210, 210))
        for rr in (R + 6, R + 13):
            d.arc([px - rr, py - rr, px + rr, py + rr], start=20, end=160, fill=ring, width=3)
            d.arc([px - rr, py - rr, px + rr, py + rr], start=200, end=340, fill=ring, width=3)
        d.line([px - 4, py, px + 4, py], fill=(255, 255, 255), width=2)
        d.line([px, py - 4, px, py + 4], fill=(255, 255, 255), width=2)


def _zoom_for_span(span: float) -> int:
    if span < 0.03:
        return 14
    elif span < 0.1:
        return 12
    elif span < 0.5:
        return 10
    elif span < 2.0:
        return 8
    elif span < 8.0:
        return 7
    return 6


def _lon_to_tile_x(lon: float, zoom: int) -> float:
    return (lon + 180.0) / 360.0 * (2 ** zoom)


def _lat_to_tile_y(lat: float, zoom: int) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_r = math.radians(lat)
    return (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * (2 ** zoom)


def _normalize_bounds(bounds: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bounds)
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat

    # 极小 ROI 在高 zoom 下会被画成接近点，给拟合计算保留最小跨度。
    min_span = 0.0005
    if max_lon - min_lon < min_span:
        mid = (min_lon + max_lon) / 2.0
        min_lon, max_lon = mid - min_span / 2.0, mid + min_span / 2.0
    if max_lat - min_lat < min_span:
        mid = (min_lat + max_lat) / 2.0
        min_lat, max_lat = mid - min_span / 2.0, mid + min_span / 2.0
    return min_lon, min_lat, max_lon, max_lat


def _fit_zoom_for_bounds(
    bounds: Tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    padding_ratio: float,
    min_zoom: int = 3,
    max_zoom: int = 16,
) -> int:
    """选择能把 bounds 放入画布的最高 Web-Mercator zoom。"""
    min_lon, min_lat, max_lon, max_lat = _normalize_bounds(bounds)
    padding_ratio = max(0.0, min(0.6, padding_ratio))
    available_w = max(1.0, width_px * (1.0 - padding_ratio))
    available_h = max(1.0, height_px * (1.0 - padding_ratio))

    for zoom in range(max_zoom, min_zoom - 1, -1):
        px_w = abs(_lon_to_tile_x(max_lon, zoom) - _lon_to_tile_x(min_lon, zoom)) * 256
        px_h = abs(_lat_to_tile_y(min_lat, zoom) - _lat_to_tile_y(max_lat, zoom)) * 256
        if px_w <= available_w and px_h <= available_h:
            return zoom
    return min_zoom


def render_basemap(
    location,
    width_px: int = 660,
    height_px: int = 600,
    targets: Optional[List[dict]] = None,
    draw_aoi_box: bool = True,
    overlay_url_template: Optional[str] = None,
    overlay_opacity: float = 1.0,
    fit_bounds: Optional[Tuple[float, float, float, float]] = None,
    fit_padding_ratio: float = 0.18,
    max_zoom: int = 16,
) -> Optional[str]:
    """
    渲染底图 PNG，返回临时文件路径；失败返回 None。

    Parameters
    ----------
    location : LocationContext  （需含 min_lon/min_lat/max_lon/max_lat、centroid_lat/lon）
    targets : 可选靶区列表 [{longitude, latitude, rank, value}, ...]，叠加编号点与矩形框
    draw_aoi_box : 是否绘制研究区红框
    overlay_url_template : 可选叠加图层 XYZ 瓦片模板（含 {z}{x}{y}，如 Macrostrat carto）；
        与卫星+标注采用同一 Web-Mercator 切片方案，半透明叠加到底图上
    overlay_opacity : 叠加图层不透明度（0~1），仅在 overlay_url_template 非空时生效
    fit_bounds : 可选取景范围 (min_lon, min_lat, max_lon, max_lat)。提供时按画布拟合该范围，
        研究区红框仍使用 location 原始 ROI。
    fit_padding_ratio : fit_bounds 在画面中的总留白比例。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    try:
        min_lon = location.min_lon
        min_lat = location.min_lat
        max_lon = location.max_lon
        max_lat = location.max_lat

        view_bounds = fit_bounds or (min_lon, min_lat, max_lon, max_lat)
        view_min_lon, view_min_lat, view_max_lon, view_max_lat = _normalize_bounds(view_bounds)
        center_lat = (view_min_lat + view_max_lat) / 2.0
        center_lon = (view_min_lon + view_max_lon) / 2.0

        if fit_bounds:
            zoom = _fit_zoom_for_bounds(
                (view_min_lon, view_min_lat, view_max_lon, view_max_lat),
                width_px,
                height_px,
                fit_padding_ratio,
                max_zoom=max_zoom,
            )
        else:
            span = max(max_lon - min_lon, max_lat - min_lat)
            zoom = _zoom_for_span(span)
        TILE_SIZE = 256

        cx_f = _lon_to_tile_x(center_lon, zoom)
        cy_f = _lat_to_tile_y(center_lat, zoom)
        cols = math.ceil(width_px / TILE_SIZE) + 2
        rows = math.ceil(height_px / TILE_SIZE) + 2
        start_x = int(cx_f - cols / 2)
        start_y = int(cy_f - rows / 2)
        canvas = Image.new("RGB", (cols * TILE_SIZE, rows * TILE_SIZE), (200, 200, 200))

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        max_tile = 2 ** zoom
        for tx in range(cols):
            for ty in range(rows):
                tile_x = (start_x + tx) % max_tile
                tile_y = (start_y + ty) % max_tile
                if tile_y < 0 or tile_y >= max_tile:
                    continue
                url_sat = (f"https://webst01.is.autonavi.com/appmaptile"
                           f"?style=6&x={tile_x}&y={tile_y}&z={zoom}")
                url_label = (f"https://wprd01.is.autonavi.com/appmaptile"
                             f"?lang=zh_cn&size=1&scl=2&style=8"
                             f"&x={tile_x}&y={tile_y}&z={zoom}")
                try:
                    req = urllib.request.Request(url_sat, headers={"User-Agent": "geo-reporter/0.1"})
                    with urllib.request.urlopen(req, timeout=8, context=ssl_ctx) as resp:
                        tile_img = Image.open(io.BytesIO(resp.read())).convert("RGBA")
                    try:
                        req2 = urllib.request.Request(url_label, headers={"User-Agent": "geo-reporter/0.1"})
                        with urllib.request.urlopen(req2, timeout=8, context=ssl_ctx) as resp2:
                            label_img = Image.open(io.BytesIO(resp2.read())).convert("RGBA")
                        tile_img = Image.alpha_composite(tile_img, label_img)
                    except Exception:
                        pass
                    # 可选叠加图层（如 Macrostrat 地质图瓦片，可能为 512²，统一缩放到 TILE_SIZE）
                    if overlay_url_template:
                        try:
                            ov_url = overlay_url_template.format(z=zoom, x=tile_x, y=tile_y)
                            req3 = urllib.request.Request(ov_url, headers={"User-Agent": "geo-reporter/0.1"})
                            with urllib.request.urlopen(req3, timeout=8, context=ssl_ctx) as resp3:
                                ov_img = Image.open(io.BytesIO(resp3.read())).convert("RGBA")
                            if ov_img.size != (TILE_SIZE, TILE_SIZE):
                                ov_img = ov_img.resize((TILE_SIZE, TILE_SIZE))
                            if overlay_opacity < 1.0:
                                alpha = ov_img.split()[3].point(lambda p: int(p * overlay_opacity))
                                ov_img.putalpha(alpha)
                            tile_img = Image.alpha_composite(tile_img, ov_img)
                        except Exception:
                            pass
                    canvas.paste(tile_img.convert("RGB"), (tx * TILE_SIZE, ty * TILE_SIZE))
                except Exception:
                    pass

        center_canvas_x = (cx_f - start_x) * TILE_SIZE
        center_canvas_y = (cy_f - start_y) * TILE_SIZE
        crop_x0 = int(center_canvas_x - width_px / 2)
        crop_y0 = int(center_canvas_y - height_px / 2)
        img = canvas.crop((crop_x0, crop_y0, crop_x0 + width_px, crop_y0 + height_px))

        def geo_to_px(lon, lat):
            fx = (_lon_to_tile_x(lon, zoom) - start_x) * TILE_SIZE - crop_x0
            fy = (_lat_to_tile_y(lat, zoom) - start_y) * TILE_SIZE - crop_y0
            return fx, fy

        draw = ImageDraw.Draw(img)

        if draw_aoi_box:
            bx0, by0 = geo_to_px(min_lon, max_lat)
            bx1, by1 = geo_to_px(max_lon, min_lat)
            if abs(bx1 - bx0) < 20:
                mx = (bx0 + bx1) / 2; bx0, bx1 = mx - 10, mx + 10
            if abs(by1 - by0) < 20:
                my = (by0 + by1) / 2; by0, by1 = my - 10, my + 10
            draw.rectangle([bx0, by0, bx1, by1], outline=(220, 30, 30), width=2)

        # 叠加靶区：高热力弧形圈点 + 编号(置信等级)
        if targets:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 15)
            except Exception:
                font = ImageFont.load_default()
            _draw_targets(img, targets, geo_to_px)
            draw = ImageDraw.Draw(img)  # 热力混合后重建 draw

            # 计算各靶区像素坐标
            placed = []  # [{px,py,label}]
            for t in targets:
                lon = t.get("longitude"); lat = t.get("latitude")
                if lon is None or lat is None:
                    continue
                px, py = geo_to_px(lon, lat)
                rank = t.get("rank", "")
                grade = t.get("grade", "")
                placed.append({"px": px, "py": py,
                               "label": f"#{rank}" + (f"·{grade}" if grade else "")})

            # 按像素邻近聚类：相距 < CLUSTER_PX 视为同一热斑（深部靶区常密集相邻、亚像素重叠）
            CLUSTER_PX = 16.0
            clusters = []  # [[item,...]]
            for it in placed:
                for c in clusters:
                    cx = sum(m["px"] for m in c) / len(c)
                    cy = sum(m["py"] for m in c) / len(c)
                    if (it["px"] - cx) ** 2 + (it["py"] - cy) ** 2 <= CLUSTER_PX ** 2:
                        c.append(it); break
                else:
                    clusters.append([it])

            def _label(ax, ay, text):
                ax = min(max(ax, 2), width_px - 8 * len(text) - 2)
                ay = min(max(ay, 2), height_px - 20)
                for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    draw.text((ax + dx, ay + dy), text, fill=(0, 0, 0), font=font)
                draw.text((ax, ay), text, fill=(255, 255, 255), font=font)

            for c in clusters:
                if len(c) == 1:
                    it = c[0]
                    _label(it["px"] + 14, it["py"] - 22, it["label"])
                    continue
                # 多个靶区重叠：以簇心为锚，按角度均匀扇形散开标签，并画引线指向真实点
                cx = sum(m["px"] for m in c) / len(c)
                cy = sum(m["py"] for m in c) / len(c)
                fan_r = 30 + 7 * len(c)
                n = len(c)
                for i, it in enumerate(c):
                    ang = 2 * math.pi * i / n - math.pi / 2  # 从正上方起均匀分布
                    lx = cx + fan_r * math.cos(ang)
                    ly = cy + fan_r * math.sin(ang)
                    draw.line([cx, cy, lx, ly], fill=(255, 255, 255), width=1)
                    tx = lx + (4 if math.cos(ang) >= 0 else -8 * len(it["label"]) - 4)
                    ty = ly - 8
                    _label(tx, ty, it["label"])

        # 金色外框
        for offset in range(3):
            draw.rectangle([offset, offset, width_px - 1 - offset, height_px - 1 - offset],
                           outline=(212, 168, 67))

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, "PNG")
        tmp.close()
        return tmp.name
    except Exception:
        return None
