"""
Google Maps Static API — 卫星底图下载器
为指定 KML 区域下载 Google Earth 卫星影像（PNG），放入交付目录根目录。

功能：
  - 自动计算 KML bbox 的最佳缩放级别和拼接瓦片数
  - 按 bbox 中心下载单张（小区域）或多张拼接（大区域）
  - 叠加 KML 边界轮廓（可选）
  - 输出 satellite_overview.png 到交付目录根

需要：
  Google Maps Static API Key
  申请地址: https://console.cloud.google.com/
  启用: Maps Static API
  免费额度: 每月 $200（约 28,000 次请求）

在 config/credentials.yaml 中配置:
  google_maps:
    api_key: YOUR_KEY_HERE
"""

# Why: 必须延迟 annotation 求值。本模块把 PIL.Image 作为可选依赖
# (try/except 包 import,HAS_PIL 旗标),但函数签名里用了 Image.Image
# 作为返回/参数类型注解。Python 3.9 默认在 def 求值 annotation,
# PIL 缺失时 NameError 'Image' 会让整个模块 import 直接失败,后续
# `if not HAS_PIL: return` 那种优雅降级根本来不及发生。加 PEP 563
# 后 annotation 变成字符串,延后到真正反射时才解析,模块就能在
# 没装 PIL 的环境里正常 import。
from __future__ import annotations

import math
import io
import time
from pathlib import Path
from typing import Optional, Tuple, List

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# Google Maps Static API 端点
_STATIC_API = "https://maps.googleapis.com/maps/api/staticmap"

# 每张图最大尺寸（scale=2 时实际像素 × 2）
_TILE_SIZE = 640
_SCALE = 2   # 使用 retina 倍图，实际 1280×1280px

# 瓦片下载重试（Google Static Maps 偶发限流/瞬时失败）
_TILE_RETRIES = 3
_TILE_RETRY_BACKOFF = 1.5   # 秒；第 n 次重试前等待 n × backoff


def _bbox_to_zoom(min_lon: float, min_lat: float,
                  max_lon: float, max_lat: float,
                  target_px: int = 1280) -> int:
    """
    根据 bbox 和目标像素尺寸估算最佳 zoom 级别。
    Google Maps 每瓦片 256px，zoom=n 时全球分辨率 = 256 * 2^n px。
    """
    # 经度跨度 → 对应 zoom
    lon_span = max_lon - min_lon
    lat_span = max_lat - min_lat

    # Mercator 纬度范围
    def lat_to_merc(lat):
        return math.log(math.tan(math.radians(45 + lat / 2)))

    merc_span = abs(lat_to_merc(max_lat) - lat_to_merc(min_lat))
    # 归一化到 0-1
    lon_frac = lon_span / 360.0
    lat_frac = merc_span / (2 * math.pi)

    fraction = max(lon_frac, lat_frac)
    if fraction <= 0:
        return 15

    zoom = math.log2(target_px / 256 / fraction)
    return max(1, min(20, int(zoom) - 1))


def _tile_grid(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    zoom: int, tile_px: int = 640
) -> List[Tuple[float, float, int, int]]:
    """
    将 bbox 拆分为若干瓦片，返回每个瓦片的 (center_lat, center_lon, col, row)。
    对于小区域通常只有 1 个瓦片。
    """
    # 每像素度数（经度）
    meters_per_px = 156543.03392 * math.cos(math.radians((min_lat + max_lat) / 2)) / (2 ** zoom)
    deg_per_px_lon = meters_per_px / 111320
    deg_per_px_lat = meters_per_px / 111320

    tile_deg_lon = tile_px * deg_per_px_lon * _SCALE
    tile_deg_lat = tile_px * deg_per_px_lat * _SCALE

    cols = max(1, math.ceil((max_lon - min_lon) / tile_deg_lon))
    rows = max(1, math.ceil((max_lat - min_lat) / tile_deg_lat))

    tiles = []
    for row in range(rows):
        for col in range(cols):
            c_lon = min_lon + (col + 0.5) * (max_lon - min_lon) / cols
            c_lat = min_lat + (row + 0.5) * (max_lat - min_lat) / rows
            tiles.append((c_lat, c_lon, col, row, cols, rows))

    return tiles


def _download_tile(
    center_lat: float, center_lon: float, zoom: int, api_key: str,
    size: int = 640, maptype: str = "satellite",
    proxies: Optional[dict] = None,
) -> Optional[Image.Image]:
    """下载单张 Google Maps Static 瓦片"""
    if not HAS_REQUESTS or not HAS_PIL:
        return None

    params = {
        "center":  f"{center_lat},{center_lon}",
        "zoom":    zoom,
        "size":    f"{size}x{size}",
        "scale":   _SCALE,
        "maptype": maptype,
        "key":     api_key,
        "format":  "png",
    }

    resp = requests.get(_STATIC_API, params=params, timeout=30, proxies=proxies)
    resp.raise_for_status()

    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    return img


def _stitch_tiles(
    tiles_data: List[Tuple[int, int, Image.Image]],
    cols: int, rows: int, tile_px: int
) -> Image.Image:
    """拼接多张瓦片为一整张图"""
    total_w = cols * tile_px * _SCALE
    total_h = rows * tile_px * _SCALE
    canvas = Image.new("RGB", (total_w, total_h))

    for col, row, img in tiles_data:
        x = col * tile_px * _SCALE
        # 注意：row=0 是南边，图像坐标 y=0 是北边，需要翻转
        y = (rows - 1 - row) * tile_px * _SCALE
        canvas.paste(img, (x, y))

    return canvas


def _draw_kml_boundary(
    img: Image.Image,
    geometry,
    img_bounds: Tuple[float, float, float, float],
    color: Tuple[int, int, int] = (255, 50, 50),
    line_width: int = 3,
) -> Image.Image:
    """
    在图像上绘制 KML 边界。
    img_bounds: (min_lon, min_lat, max_lon, max_lat) 对应整张图的地理范围。
    """
    if geometry is None:
        return img

    try:
        min_lon, min_lat, max_lon, max_lat = img_bounds
        w, h = img.size

        def geo_to_px(lon, lat):
            x = (lon - min_lon) / (max_lon - min_lon) * w
            y = h - (lat - min_lat) / (max_lat - min_lat) * h  # 翻转Y轴
            return (x, y)

        draw = ImageDraw.Draw(img)

        def draw_ring(coords):
            if len(coords) < 2:
                return
            px_coords = [geo_to_px(lon, lat) for lon, lat in coords]
            for i in range(len(px_coords) - 1):
                for lw in range(-line_width // 2, line_width // 2 + 1):
                    draw.line([
                        (px_coords[i][0] + lw, px_coords[i][1]),
                        (px_coords[i+1][0] + lw, px_coords[i+1][1]),
                    ], fill=color, width=1)

        geom_type = geometry.geom_type
        if geom_type == "Polygon":
            coords = list(geometry.exterior.coords)
            draw_ring(coords)
        elif geom_type in ("MultiPolygon", "GeometryCollection"):
            for g in geometry.geoms:
                if hasattr(g, "exterior"):
                    draw_ring(list(g.exterior.coords))

    except Exception:
        pass

    return img


def download_satellite_overview(
    bbox: Tuple[float, float, float, float],
    api_key: str,
    delivery_dir: Path,
    geometry=None,
    output_name: str = "satellite_overview.png",
    maptype: str = "satellite",
    proxy: Optional[str] = None,
) -> Optional[Path]:
    """
    下载 Google Maps 卫星底图并保存到交付目录。

    Parameters
    ----------
    bbox         : (min_lon, min_lat, max_lon, max_lat)
    api_key      : Google Maps Static API Key
    delivery_dir : 交付目录路径
    geometry     : Shapely 几何体，用于绘制边界轮廓
    output_name  : 输出文件名
    maptype      : "satellite"（卫星图）或 "hybrid"（卫星+道路标注）
    proxy        : HTTP/HTTPS 代理地址，如 "http://127.0.0.1:7890"

    Returns
    -------
    生成的 PNG 路径，或 None
    """
    if not HAS_REQUESTS:
        print("  [卫星底图] 缺少 requests 库，跳过")
        return None
    if not HAS_PIL:
        print("  [卫星底图] 缺少 Pillow 库，跳过")
        return None

    out_path = Path(delivery_dir) / output_name
    if out_path.exists():
        return out_path

    proxies = {"http": proxy, "https": proxy} if proxy else None

    min_lon, min_lat, max_lon, max_lat = bbox

    # 计算最佳 zoom
    zoom = _bbox_to_zoom(min_lon, min_lat, max_lon, max_lat, target_px=1280)
    print(f"  [卫星底图] bbox={bbox}  zoom={zoom}  maptype={maptype}")
    if proxy:
        print(f"  [卫星底图] 使用代理: {proxy}")

    # 计算瓦片网格
    tiles = _tile_grid(min_lon, min_lat, max_lon, max_lat, zoom)
    cols = tiles[0][4] if tiles else 1
    rows = tiles[0][5] if tiles else 1
    print(f"  [卫星底图] 下载 {len(tiles)} 张瓦片 ({cols}列 × {rows}行)...")

    tiles_data = []
    for c_lat, c_lon, col, row, _, _ in tiles:
        # Google Static Maps 偶发限流/瞬时网络失败 → 重试，避免整张底图静默丢失
        img = None
        last_err = None
        for attempt in range(_TILE_RETRIES):
            try:
                img = _download_tile(c_lat, c_lon, zoom, api_key, size=_TILE_SIZE,
                                     maptype=maptype, proxies=proxies)
                if img:
                    break
            except Exception as e:
                last_err = e
            if attempt < _TILE_RETRIES - 1:
                time.sleep(_TILE_RETRY_BACKOFF * (attempt + 1))
        if img:
            tiles_data.append((col, row, img))
            print(f"    [{col},{row}] 下载成功 ({img.size[0]}×{img.size[1]}px)")
        else:
            print(f"    [{col},{row}] 下载失败(重试 {_TILE_RETRIES} 次): {last_err}")

    if not tiles_data:
        print("  [卫星底图] 所有瓦片下载失败")
        return None

    # 拼接
    if len(tiles_data) == 1:
        result = tiles_data[0][2]
    else:
        result = _stitch_tiles(tiles_data, cols, rows, _TILE_SIZE)

    # 计算整图的地理范围（用于边界绘制）
    # 瓦片网格覆盖范围略大于原始 bbox
    img_bounds = (min_lon, min_lat, max_lon, max_lat)

    # 叠加 KML 边界
    if geometry is not None:
        result = _draw_kml_boundary(result, geometry, img_bounds)

    # 添加图例
    try:
        draw = ImageDraw.Draw(result)
        draw.text((10, 10), f"卫星底图  © Google Maps  zoom={zoom}", fill=(255, 255, 100))
    except Exception:
        pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(out_path, "PNG", optimize=True)
    print(f"  [卫星底图] 已生成: {out_path.name}  ({result.size[0]}×{result.size[1]}px)")
    return out_path
