"""
用 Real-ESRGAN x4 对 Sentinel-2 TCI 做超分辨率，然后生成2D地图
"""
import numpy as np
import rasterio
from rasterio.plot import reshape_as_image
from rasterio.warp import reproject, Resampling, transform_bounds
from pathlib import Path
import xml.etree.ElementTree as ET
import cv2

import torch
import spandrel
from spandrel import ModelLoader

import matplotlib
matplotlib.rcParams['font.family'] = ['PingFang HK', 'Heiti TC', 'DejaVu Sans']
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker

# ── 路径 ──────────────────────────────────────────────────────────────────────
BASE    = Path("/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-downloader")
TCI     = BASE / "downloads/津巴布韦金矿/sentinel2/S2B_MSIL2A_20230426T074619_N0510_R135_T35KQV_20240903T235133.SAFE/S2B_MSIL2A_20230426T074619_N0510_R135_T35KQV_20240903T235133.SAFE/GRANULE/L2A_T35KQV_A032050_20230426T081153/IMG_DATA/R10m/T35KQV_20230426T074619_TCI_10m_clipped.jp2"
DEM     = BASE / "downloads/津巴布韦金矿/dem/CopDEM_30m_S19_E029_clipped.tif"
KML     = BASE / "uploads/kml/津巴布韦金矿.kml"
MODEL   = BASE / "models/RealESRGAN_x4plus.pth"
OUT     = BASE / "delivery/津巴布韦金矿/map_2d.png"

# ── 读取TCI ───────────────────────────────────────────────────────────────────
print("读取 Sentinel-2 TCI 10m...")
with rasterio.open(TCI) as src:
    tci_data      = src.read()          # (3, H, W) uint8
    tci_transform = src.transform
    tci_crs       = src.crs
    tci_bounds    = src.bounds

H0, W0 = tci_data.shape[1], tci_data.shape[2]
print(f"  原始尺寸: {W0}x{H0}")

# ── Real-ESRGAN x4 超分 ───────────────────────────────────────────────────────
print("加载 Real-ESRGAN x4 模型...")
device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
print(f"  使用设备: {device}")

model = ModelLoader().load_from_file(str(MODEL))
model.eval().to(device)

# 转为 BGR uint8 (cv2格式) → float32 CHW [0,1]
bgr = cv2.cvtColor(reshape_as_image(tci_data), cv2.COLOR_RGB2BGR)
inp = torch.from_numpy(bgr.astype(np.float32) / 255.0).permute(2,0,1).unsqueeze(0).to(device)

print("推理中（MPS加速）...")
with torch.no_grad():
    out = model(inp)

# 转回 numpy RGB
sr_np = out.squeeze(0).permute(1,2,0).clamp(0,1).cpu().numpy()
sr_rgb = cv2.cvtColor((sr_np * 255).astype(np.uint8), cv2.COLOR_BGR2RGB)
H4, W4 = sr_rgb.shape[:2]
print(f"  超分后尺寸: {W4}x{H4}")

# 对比度轻拉伸
tci_float = sr_rgb.astype(np.float32) / 255.0
p2, p98 = np.percentile(tci_float, 2), np.percentile(tci_float, 98)
tci_float = np.clip((tci_float - p2) / (p98 - p2 + 1e-6), 0, 1)

# ── DEM 晕渲 ──────────────────────────────────────────────────────────────────
print("DEM 晕渲...")
dem_reproj = np.empty((H4, W4), dtype=np.float32)
# 超分后的transform（像素缩小4x）
from rasterio.transform import from_bounds
sr_transform = from_bounds(*tci_bounds, W4, H4)

with rasterio.open(DEM) as dem_src:
    reproject(
        source=rasterio.band(dem_src, 1),
        destination=dem_reproj,
        src_transform=dem_src.transform,
        src_crs=dem_src.crs,
        dst_transform=sr_transform,
        dst_crs=tci_crs,
        resampling=Resampling.bilinear
    )

def hillshade(dem, azimuth=315, altitude=45):
    az = np.radians(360 - azimuth + 90)
    al = np.radians(altitude)
    dy, dx = np.gradient(dem)
    slope  = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(-dy, dx)
    shade  = np.sin(al)*np.cos(slope) + np.cos(al)*np.sin(slope)*np.cos(az - aspect)
    return np.clip(shade, 0, 1)

hs = hillshade(dem_reproj)
hs = (hs - hs.min()) / (hs.max() - hs.min() + 1e-6)

# ── KML ───────────────────────────────────────────────────────────────────────
def parse_kml(path):
    tree = ET.parse(path)
    ns   = {'k': 'http://www.opengis.net/kml/2.2'}
    txt  = tree.getroot().find('.//k:coordinates', ns).text.strip()
    return [(float(p.split(',')[0]), float(p.split(',')[1])) for p in txt.split()]

kml_coords = parse_kml(KML)
kml_lons   = [c[0] for c in kml_coords]
kml_lats   = [c[1] for c in kml_coords]

# ── 地图范围 ──────────────────────────────────────────────────────────────────
lon_min, lat_min, lon_max, lat_max = transform_bounds(tci_crs, 'EPSG:4326', *tci_bounds)
extent = [lon_min, lon_max, lat_min, lat_max]
cx = (min(kml_lons)+max(kml_lons))/2
cy = (min(kml_lats)+max(kml_lats))/2

# ── 绘图 ──────────────────────────────────────────────────────────────────────
print("绘制地图...")
fig, ax = plt.subplots(figsize=(14, 12), dpi=200)
fig.patch.set_facecolor('#1a1a2e')

ax.imshow(tci_float, extent=extent, origin='upper', aspect='auto', zorder=1)

hs_rgba = np.zeros((*hs.shape, 4), dtype=np.float32)
hs_rgba[..., :3] = hs[..., None]
hs_rgba[...,  3] = 0.30
ax.imshow(hs_rgba, extent=extent, origin='upper', aspect='auto', zorder=2)

ax.plot(kml_lons, kml_lats, color='#FF4444', linewidth=2.5, zorder=4)
ax.fill(kml_lons, kml_lats, color='#FF4444', alpha=0.06, zorder=3)
ax.annotate('津巴布韦金矿\n研究区', xy=(cx, cy), fontsize=9, color='white',
            fontweight='bold', ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#FF4444',
                      alpha=0.75, edgecolor='white', linewidth=0.8), zorder=5)

# 坐标轴
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.3f}°E"))
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y,_: f"{abs(y):.3f}°{'S' if y<0 else 'N'}"))
ax.xaxis.set_major_locator(mticker.MaxNLocator(5))
ax.yaxis.set_major_locator(mticker.MaxNLocator(5))
ax.tick_params(colors='#cccccc', labelsize=8)
ax.set_xlabel('经度 (WGS84)', fontsize=9, color='#aaaaaa')
ax.set_ylabel('纬度 (WGS84)', fontsize=9, color='#aaaaaa')
ax.grid(True, color='white', alpha=0.12, linewidth=0.5, linestyle='--', zorder=6)
for sp in ax.spines.values(): sp.set_edgecolor('#555555')

# 比例尺
lat_rad  = np.radians(abs(cy))
deg_1km  = 1.0 / (111.32 * np.cos(lat_rad))
bx = lon_min + (lon_max-lon_min)*0.05
by = lat_min + (lat_max-lat_min)*0.04
ax.plot([bx, bx+deg_1km], [by, by], color='white', lw=3, zorder=7, solid_capstyle='butt')
ax.plot([bx,bx], [by-(lat_max-lat_min)*.005, by+(lat_max-lat_min)*.005], color='white', lw=1.5, zorder=7)
ax.plot([bx+deg_1km,bx+deg_1km], [by-(lat_max-lat_min)*.005, by+(lat_max-lat_min)*.005], color='white', lw=1.5, zorder=7)
ax.text(bx+deg_1km/2, by+(lat_max-lat_min)*.014, '1 km',
        color='white', ha='center', fontsize=7, fontweight='bold', zorder=7,
        bbox=dict(facecolor='black', alpha=0.4, pad=1, edgecolor='none'))

# 指北针
nx = lon_max - (lon_max-lon_min)*0.07
ny = lat_max - (lat_max-lat_min)*0.13
ax.annotate('', xy=(nx, ny+(lat_max-lat_min)*0.06), xytext=(nx, ny),
            arrowprops=dict(arrowstyle='->', color='white', lw=2), zorder=7)
ax.text(nx, ny+(lat_max-lat_min)*0.07, 'N',
        color='white', ha='center', fontsize=10, fontweight='bold', zorder=7)

# 图例
legend = ax.legend(handles=[
    mpatches.Patch(facecolor='none', edgecolor='#FF4444', lw=2, label='研究区边界 (KML)'),
    mpatches.Patch(facecolor='gray', alpha=0.4, label='DEM地形晕渲'),
], loc='upper right', fontsize=8, framealpha=0.7,
   facecolor='#1a1a2e', edgecolor='#555555', labelcolor='white')

ax.set_title('津巴布韦金矿 — 卫星影像综合分析图\n'
             'Sentinel-2 TCI 10m (2023-04-26) + Real-ESRGAN x4 | Copernicus DEM 30m',
             fontsize=11, color='white', pad=12)
fig.text(0.99, 0.01, 'Data: ESA Sentinel-2 L2A · Copernicus DEM GLO-30 | SR: Real-ESRGAN x4',
         ha='right', va='bottom', fontsize=6, color='#888888')

OUT.parent.mkdir(parents=True, exist_ok=True)
plt.tight_layout()
plt.savefig(OUT, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()
print(f"✓ 已保存: {OUT}")
