"""
生成津巴布韦金矿2D专业地图
图层叠加：Sentinel-2真彩色 + DEM晕渲 + KML边界 + 比例尺/坐标标注
"""

import matplotlib
matplotlib.rcParams['font.family'] = ['PingFang HK', 'Heiti TC', 'DejaVu Sans']
import numpy as np
import rasterio
from rasterio.plot import reshape_as_image
from rasterio.warp import reproject, Resampling
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance

# ── 路径配置 ──────────────────────────────────────────────────────────────────
BASE = Path("/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-downloader")
TCI_PATH = BASE / "downloads/津巴布韦金矿/sentinel2/S2B_MSIL2A_20230426T074619_N0510_R135_T35KQV_20240903T235133.SAFE/S2B_MSIL2A_20230426T074619_N0510_R135_T35KQV_20240903T235133.SAFE/GRANULE/L2A_T35KQV_A032050_20230426T081153/IMG_DATA/R10m/T35KQV_20230426T074619_TCI_10m_clipped.jp2"
DEM_PATH = BASE / "downloads/津巴布韦金矿/dem/CopDEM_30m_S19_E029_clipped.tif"
KML_PATH = BASE / "uploads/kml/津巴布韦金矿.kml"
OUT_PATH = BASE / "delivery/津巴布韦金矿/map_2d.png"

# ── 解析KML多边形坐标 ─────────────────────────────────────────────────────────
def parse_kml(kml_path):
    tree = ET.parse(kml_path)
    root = tree.getroot()
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    coords_text = root.find('.//kml:coordinates', ns).text.strip()
    coords = []
    for part in coords_text.split():
        lon, lat, *_ = map(float, part.split(','))
        coords.append((lon, lat))
    return coords

# ── 读取TCI影像 ───────────────────────────────────────────────────────────────
print("读取 Sentinel-2 TCI 影像...")
with rasterio.open(TCI_PATH) as src:
    tci_data = src.read()          # (3, H, W)
    tci_transform = src.transform
    tci_crs = src.crs
    tci_bounds = src.bounds

# 归一化到0-1
tci_rgb = reshape_as_image(tci_data).astype(np.float32) / 255.0
# 对比度拉伸
p2, p98 = np.percentile(tci_rgb, 2), np.percentile(tci_rgb, 98)
tci_rgb = np.clip((tci_rgb - p2) / (p98 - p2 + 1e-6), 0, 1)

# ── 超分辨率：Lanczos 4x + Unsharp Mask 锐化 ──────────────────────────────────
print("超分辨率处理（4x Lanczos + 锐化）...")
H0, W0 = tci_rgb.shape[:2]
pil_img = Image.fromarray((tci_rgb * 255).astype(np.uint8))
# 4x Lanczos 放大
pil_up = pil_img.resize((W0 * 4, H0 * 4), Image.LANCZOS)
# Unsharp Mask 锐化（radius=2, percent=150, threshold=3）
pil_sharp = pil_up.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
# 轻微增强饱和度
pil_sharp = ImageEnhance.Color(pil_sharp).enhance(1.2)
tci_rgb = np.array(pil_sharp).astype(np.float32) / 255.0
print(f"  原始: {W0}x{H0} → 放大后: {tci_rgb.shape[1]}x{tci_rgb.shape[0]}")

# ── 读取DEM并重投影到TCI的坐标系/分辨率 ─────────────────────────────────────
print("读取 DEM 并处理晕渲...")
with rasterio.open(DEM_PATH) as dem_src:
    dem_raw = dem_src.read(1).astype(np.float32)
    dem_raw[dem_raw == dem_src.nodata] = np.nan if dem_src.nodata else dem_raw[dem_raw == dem_src.nodata]
    dem_crs = dem_src.crs
    dem_transform = dem_src.transform
    dem_bounds = dem_src.bounds

# 重投影DEM到TCI空间（匹配尺寸）
H, W = tci_rgb.shape[:2]
dem_reproj = np.empty((H, W), dtype=np.float32)
with rasterio.open(DEM_PATH) as dem_src:
    reproject(
        source=rasterio.band(dem_src, 1),
        destination=dem_reproj,
        src_transform=dem_src.transform,
        src_crs=dem_src.crs,
        dst_transform=tci_transform,
        dst_crs=tci_crs,
        resampling=Resampling.bilinear
    )

# 计算山体阴影
def hillshade(dem, azimuth=315, altitude=45):
    azimuth_rad = np.radians(360 - azimuth + 90)
    altitude_rad = np.radians(altitude)
    dy, dx = np.gradient(dem)
    slope = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(-dy, dx)
    shade = (np.sin(altitude_rad) * np.cos(slope) +
             np.cos(altitude_rad) * np.sin(slope) * np.cos(azimuth_rad - aspect))
    shade = np.clip(shade, 0, 1)
    return shade

hs = hillshade(dem_reproj)
# 归一化晕渲到0-1
hs = (hs - hs.min()) / (hs.max() - hs.min() + 1e-6)

# ── 确定地图范围（WGS84经纬度）────────────────────────────────────────────────
# TCI可能是UTM，转换范围到经纬度
from rasterio.warp import transform_bounds
bounds_wgs84 = transform_bounds(tci_crs, 'EPSG:4326', *tci_bounds)
lon_min, lat_min, lon_max, lat_max = bounds_wgs84

# ── 绘图 ──────────────────────────────────────────────────────────────────────
print("绘制地图...")
fig, ax = plt.subplots(figsize=(12, 10), dpi=200)
fig.patch.set_facecolor('#1a1a2e')

# 图层1：Sentinel-2真彩色底图
extent = [lon_min, lon_max, lat_min, lat_max]
ax.imshow(tci_rgb, extent=extent, origin='upper', aspect='auto', zorder=1)

# 图层2：DEM晕渲叠加（透明度混合）
hs_rgba = np.zeros((*hs.shape, 4), dtype=np.float32)
hs_rgba[..., 0] = hs_rgba[..., 1] = hs_rgba[..., 2] = hs
hs_rgba[..., 3] = 0.35  # 35%透明度晕渲
ax.imshow(hs_rgba, extent=extent, origin='upper', aspect='auto', zorder=2)

# 图层3：KML边界
kml_coords = parse_kml(KML_PATH)
kml_lons = [c[0] for c in kml_coords]
kml_lats = [c[1] for c in kml_coords]
ax.plot(kml_lons, kml_lats, color='#FF4444', linewidth=2.5, zorder=4, label='研究区边界')
ax.fill(kml_lons, kml_lats, color='#FF4444', alpha=0.08, zorder=3)

# 标注研究区中心
cx = (min(kml_lons) + max(kml_lons)) / 2
cy = (min(kml_lats) + max(kml_lats)) / 2
ax.annotate('津巴布韦金矿\n研究区', xy=(cx, cy),
            fontsize=9, color='white', fontweight='bold',
            ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#FF4444', alpha=0.7, edgecolor='white', linewidth=0.8),
            zorder=5)

# ── 坐标轴格式化 ──────────────────────────────────────────────────────────────
import matplotlib.ticker as mticker

def fmt_lon(x, pos):
    return f"{x:.3f}°E"

def fmt_lat(y, pos):
    return f"{abs(y):.3f}°{'S' if y < 0 else 'N'}"

ax.xaxis.set_major_formatter(mticker.FuncFormatter(fmt_lon))
ax.yaxis.set_major_formatter(mticker.FuncFormatter(fmt_lat))
ax.xaxis.set_major_locator(mticker.MaxNLocator(5))
ax.yaxis.set_major_locator(mticker.MaxNLocator(5))
ax.tick_params(axis='both', labelsize=8, colors='#cccccc', labelcolor='#cccccc')
ax.set_xlabel('经度 (WGS84)', fontsize=9, color='#aaaaaa')
ax.set_ylabel('纬度 (WGS84)', fontsize=9, color='#aaaaaa')

# 网格线
ax.grid(True, color='white', alpha=0.15, linewidth=0.5, linestyle='--', zorder=6)
for spine in ax.spines.values():
    spine.set_edgecolor('#555555')

# ── 比例尺 ────────────────────────────────────────────────────────────────────
# 手动比例尺（1km）
bar_lon = lon_min + (lon_max - lon_min) * 0.05
bar_lat = lat_min + (lat_max - lat_min) * 0.05
lat_rad = np.radians(abs(bar_lat))
deg_per_km = 1.0 / (111.32 * np.cos(lat_rad))
bar_len = deg_per_km * 1  # 1km
ax.plot([bar_lon, bar_lon + bar_len], [bar_lat, bar_lat],
        color='white', linewidth=3, zorder=7,
        solid_capstyle='butt')
ax.plot([bar_lon, bar_lon], [bar_lat - (lat_max-lat_min)*0.005, bar_lat + (lat_max-lat_min)*0.005],
        color='white', linewidth=1.5, zorder=7)
ax.plot([bar_lon + bar_len, bar_lon + bar_len],
        [bar_lat - (lat_max-lat_min)*0.005, bar_lat + (lat_max-lat_min)*0.005],
        color='white', linewidth=1.5, zorder=7)
ax.text(bar_lon + bar_len/2, bar_lat + (lat_max - lat_min)*0.015,
        '1 km', color='white', ha='center', fontsize=7, fontweight='bold',
        zorder=7, bbox=dict(facecolor='black', alpha=0.4, pad=1, edgecolor='none'))

# ── 指北针 ────────────────────────────────────────────────────────────────────
arrow_x = lon_max - (lon_max - lon_min) * 0.08
arrow_y = lat_max - (lat_max - lat_min) * 0.12
ax.annotate('', xy=(arrow_x, arrow_y + (lat_max - lat_min)*0.06),
            xytext=(arrow_x, arrow_y),
            arrowprops=dict(arrowstyle='->', color='white', lw=2),
            zorder=7)
ax.text(arrow_x, arrow_y + (lat_max - lat_min)*0.07, 'N',
        color='white', ha='center', fontsize=10, fontweight='bold', zorder=7)

# ── 图例 ──────────────────────────────────────────────────────────────────────
legend_elements = [
    mpatches.Patch(facecolor='none', edgecolor='#FF4444', linewidth=2, label='研究区边界 (KML)'),
    mpatches.Patch(facecolor='gray', alpha=0.4, label='DEM地形晕渲'),
]
legend = ax.legend(handles=legend_elements, loc='upper right',
                   fontsize=8, framealpha=0.7,
                   facecolor='#1a1a2e', edgecolor='#555555',
                   labelcolor='white')

# ── 标题和元数据 ──────────────────────────────────────────────────────────────
ax.set_title('津巴布韦金矿 — 卫星影像综合分析图\n'
             'Sentinel-2 TCI (2023-04-26) | Copernicus DEM 30m | KML研究区',
             fontsize=11, color='white', pad=12,
             fontfamily='sans-serif')

# 右下角数据源注记
fig.text(0.99, 0.01,
         'Data: ESA Sentinel-2 L2A · Copernicus DEM GLO-30 · KML boundary',
         ha='right', va='bottom', fontsize=6, color='#888888')

# ── 保存 ──────────────────────────────────────────────────────────────────────
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
plt.tight_layout()
plt.savefig(OUT_PATH, dpi=200, bbox_inches='tight',
            facecolor=fig.get_facecolor())
plt.close()
print(f"✓ 已保存: {OUT_PATH}")
