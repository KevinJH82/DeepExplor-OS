"""
拉取 ESRI World Imagery 卫星底图瓦片，叠加KML边界+坐标+比例尺，生成高清2D地图
"""
import math, urllib.request, io, time
import numpy as np
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image

import matplotlib
matplotlib.rcParams['font.family'] = ['PingFang HK', 'Heiti TC', 'DejaVu Sans']
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker

# ── 配置 ──────────────────────────────────────────────────────────────────────
BASE    = Path("/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-downloader")
KML     = BASE / "uploads/kml/津巴布韦金矿.kml"
OUT     = BASE / "delivery/津巴布韦金矿/map_2d.png"
ZOOM    = 17   # 17级 ≈ 1.2m/pixel，可调18但瓦片数多

ESRI_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

# ── KML ───────────────────────────────────────────────────────────────────────
def parse_kml(path):
    tree = ET.parse(path)
    ns   = {'k': 'http://www.opengis.net/kml/2.2'}
    txt  = tree.getroot().find('.//k:coordinates', ns).text.strip()
    return [(float(p.split(',')[0]), float(p.split(',')[1])) for p in txt.split()]

kml_coords = parse_kml(KML)
kml_lons   = [c[0] for c in kml_coords]
kml_lats   = [c[1] for c in kml_coords]

# 研究区范围 + 少量buffer
buf = 0.003
lon_min = min(kml_lons) - buf
lon_max = max(kml_lons) + buf
lat_min = min(kml_lats) - buf
lat_max = max(kml_lats) + buf
cx = (lon_min + lon_max) / 2
cy = (lat_min + lat_max) / 2

# ── 瓦片工具函数 ──────────────────────────────────────────────────────────────
def deg2tile(lon, lat, zoom):
    lat_r = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(lat_r) + 1/math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y

def tile2deg(x, y, zoom):
    """瓦片左上角经纬度"""
    n = 2 ** zoom
    lon = x / n * 360 - 180
    lat_r = math.atan(math.sinh(math.pi * (1 - 2*y/n)))
    lat = math.degrees(lat_r)
    return lon, lat

def fetch_tile(x, y, z, retries=3):
    url = ESRI_URL.format(x=x, y=y, z=z)
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; geo-research-tool)'}
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                return Image.open(io.BytesIO(r.read())).convert('RGB')
        except Exception as e:
            if i < retries-1:
                time.sleep(1)
            else:
                print(f"  瓦片 {z}/{y}/{x} 失败: {e}")
                return Image.new('RGB', (256,256), (80,80,80))

# ── 计算需要的瓦片范围 ────────────────────────────────────────────────────────
x0, y0 = deg2tile(lon_min, lat_max, ZOOM)   # 左上
x1, y1 = deg2tile(lon_max, lat_min, ZOOM)   # 右下

print(f"Zoom={ZOOM}, 瓦片范围: x={x0}~{x1}, y={y0}~{y1}")
print(f"共 {(x1-x0+1)*(y1-y0+1)} 张瓦片")

# ── 拼接瓦片 ─────────────────────────────────────────────────────────────────
tile_w, tile_h = 256, 256
mosaic_w = (x1 - x0 + 1) * tile_w
mosaic_h = (y1 - y0 + 1) * tile_h
mosaic = Image.new('RGB', (mosaic_w, mosaic_h))

total = (x1-x0+1)*(y1-y0+1)
done  = 0
for ty in range(y0, y1+1):
    for tx in range(x0, x1+1):
        tile = fetch_tile(tx, ty, ZOOM)
        px = (tx - x0) * tile_w
        py = (ty - y0) * tile_h
        mosaic.paste(tile, (px, py))
        done += 1
        print(f"  下载进度 {done}/{total}", end='\r')
print()

# ── 精确地理范围（瓦片边界） ──────────────────────────────────────────────────
img_lon_min, img_lat_max = tile2deg(x0,   y0,   ZOOM)
img_lon_max, img_lat_min = tile2deg(x1+1, y1+1, ZOOM)
extent = [img_lon_min, img_lon_max, img_lat_min, img_lat_max]

mosaic_np = np.array(mosaic)
print(f"底图尺寸: {mosaic_w}x{mosaic_h} px  范围: lon [{img_lon_min:.4f}, {img_lon_max:.4f}]  lat [{img_lat_min:.4f}, {img_lat_max:.4f}]")

# ── 绘图 ──────────────────────────────────────────────────────────────────────
print("绘制地图...")
fig, ax = plt.subplots(figsize=(14, 12), dpi=200)
fig.patch.set_facecolor('#1a1a2e')

ax.imshow(mosaic_np, extent=extent, origin='upper', aspect='auto', zorder=1)

# KML边界
ax.plot(kml_lons, kml_lats, color='#FF4444', linewidth=2.5, zorder=3)
ax.fill(kml_lons, kml_lats, color='#FF4444', alpha=0.08, zorder=2)
ax.annotate('津巴布韦金矿\n研究区', xy=(cx, cy), fontsize=9, color='white',
            fontweight='bold', ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#FF4444',
                      alpha=0.75, edgecolor='white', linewidth=0.8), zorder=4)

# 坐标轴
ax.set_xlim(img_lon_min, img_lon_max)
ax.set_ylim(img_lat_min, img_lat_max)
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.4f}°E"))
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y,_: f"{abs(y):.4f}°{'S' if y<0 else 'N'}"))
ax.xaxis.set_major_locator(mticker.MaxNLocator(5))
ax.yaxis.set_major_locator(mticker.MaxNLocator(5))
ax.tick_params(colors='#cccccc', labelsize=8)
ax.set_xlabel('经度 (WGS84)', fontsize=9, color='#aaaaaa')
ax.set_ylabel('纬度 (WGS84)', fontsize=9, color='#aaaaaa')
ax.grid(True, color='white', alpha=0.12, linewidth=0.5, linestyle='--', zorder=5)
for sp in ax.spines.values():
    sp.set_edgecolor('#555555')

# 比例尺 1km
lat_rad = math.radians(abs(cy))
deg_1km = 1.0 / (111.32 * math.cos(lat_rad))
w = img_lon_max - img_lon_min
h = img_lat_max - img_lat_min
bx = img_lon_min + w*0.05
by = img_lat_min + h*0.04
ax.plot([bx, bx+deg_1km], [by, by], color='white', lw=3, zorder=6, solid_capstyle='butt')
ax.plot([bx,bx], [by-h*.005, by+h*.005], color='white', lw=1.5, zorder=6)
ax.plot([bx+deg_1km,bx+deg_1km], [by-h*.005, by+h*.005], color='white', lw=1.5, zorder=6)
ax.text(bx+deg_1km/2, by+h*.014, '1 km', color='white', ha='center',
        fontsize=7, fontweight='bold', zorder=6,
        bbox=dict(facecolor='black', alpha=0.4, pad=1, edgecolor='none'))

# 指北针
nx = img_lon_max - w*0.07
ny = img_lat_max - h*0.13
ax.annotate('', xy=(nx, ny+h*0.06), xytext=(nx, ny),
            arrowprops=dict(arrowstyle='->', color='white', lw=2), zorder=6)
ax.text(nx, ny+h*0.07, 'N', color='white', ha='center',
        fontsize=10, fontweight='bold', zorder=6)

# 图例
ax.legend(handles=[
    mpatches.Patch(facecolor='none', edgecolor='#FF4444', lw=2, label='研究区边界 (KML)'),
], loc='upper right', fontsize=8, framealpha=0.7,
   facecolor='#1a1a2e', edgecolor='#555555', labelcolor='white')

ax.set_title('津巴布韦金矿 — 高分辨率卫星影像图\n'
             f'ESRI World Imagery (Zoom {ZOOM} ≈ 1.2m/px) | KML研究区边界',
             fontsize=11, color='white', pad=12)
fig.text(0.99, 0.01,
         'Imagery © Esri, Maxar, Earthstar Geographics | KML boundary overlay',
         ha='right', va='bottom', fontsize=6, color='#888888')

OUT.parent.mkdir(parents=True, exist_ok=True)
plt.tight_layout()
plt.savefig(OUT, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()
print(f"✓ 已保存: {OUT}")
