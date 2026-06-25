# ==============================================================================
# 深部矿产预测可视化与 KMZ 导出系统 (学术高级终极版)
# 包含: 动态多矿种色彩匹配、cKDTree 交互提速、高质量平滑凸包、UTM 智能投影、动态计算核心
# ==============================================================================

import scipy.io as sio
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from pyproj import Transformer
import matplotlib.font_manager as fm
import simplekml
import os
import io
import sys
from scipy.spatial import ConvexHull, cKDTree
from scipy.interpolate import splprep, splev

# ==================== 强制 UTF-8 输出，防止 Windows 控制台 GBK 报错 ====================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ==================== ★ 0. 核心全局配置 (学术高级感设置) ★ ====================
BASE_THRESHOLD = 0.4  # 基础阈值
KEY_AREA_THRESHOLD = 0.6  # 重点区域阈值 (将优先从 MATLAB 数据中动态读取)
STEP_NUM = 24  # 24阶梯度，兼顾平滑与地貌层次感

TOP_AREA_FILL_ALPHA = 100  # 靶区覆盖透明度
TOP_AREA_LINE_WIDTH = 3  # 靶区边界线宽
TOP_AREA_SMOOTH_POINTS = 100  # 靶区平滑点数

# ==================== 1. 配置与路径设置 ====================
if len(sys.argv) > 2:
    data_path = sys.argv[1]  # 接收 MATLAB 的 dataFile 路径
    output_dir = sys.argv[2]  # 接收 MATLAB 的 outDir 路径
else:
    # 默认路径备用
    data_path = r"C:\Users\Deep-Lei\Desktop\gold\Intrinsic_Result_gold_20260213_1049\gold_Result.mat"
    output_dir = r"C:\Users\Deep-Lei\Desktop"

# ==================== ★ 新增：在这里直接输入你的矿体计算类型 ★ ====================
# 如果为 ''，则走默认逻辑；如果输入特定字符串（如 'Au_vein', 'petroleum'），则走专属计算和专属配色
ORE_CALC_TYPE = ''
# ====================================================================================

# --- 配置中文字体 ---
plt.rcParams['font.size'] = 10
plt.rcParams['axes.unicode_minus'] = False
try:
    font_path = 'C:/Windows/Fonts/msyh.ttc'
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['font.family'] = font_prop.get_name()
except:
    plt.rcParams['font.family'] = ['SimHei', 'Arial', 'sans-serif']

os.makedirs(output_dir, exist_ok=True)

# ==================== 2. 数据读取与矿种解析 ====================
try:
    mat_data = sio.loadmat(data_path)
except Exception as e:
    print(f"读取 MAT 文件失败: {e}")
    sys.exit(1)

lonGrid = mat_data['lonGrid']
latGrid = mat_data['latGrid']
Au_deep = mat_data['Au_deep']

has_roi = 'lonROI' in mat_data and 'latROI' in mat_data
lonROI = mat_data['lonROI'].flatten() if has_roi else np.array([])
latROI = mat_data['latROI'].flatten() if has_roi else np.array([])

lonTop = mat_data['lonTop'].flatten() if 'lonTop' in mat_data else np.array([])
latTop = mat_data['latTop'].flatten() if 'latTop' in mat_data else np.array([])
redIdx = mat_data['redIdx'].flatten() if 'redIdx' in mat_data else np.array([])

# 解析矿种以匹配颜色
mineral_type_raw = mat_data['mineral_type'][0] if 'mineral_type' in mat_data else 'other'
if isinstance(mineral_type_raw, np.ndarray):
    mineral_type_raw = mineral_type_raw[0]
mineral_type = str(mineral_type_raw).lower()

# ==================== ★ 核心修复：读取 MATLAB 透传过来的置信度 ★ ====================
if 'kmz_threshold' in mat_data:
    threshold_raw = mat_data['kmz_threshold']
    if isinstance(threshold_raw, np.ndarray):
        KEY_AREA_THRESHOLD = float(threshold_raw.flatten()[0])
    else:
        KEY_AREA_THRESHOLD = float(threshold_raw)
    print(f"✅ 从 MATLAB 成功读取到自定义 KMZ 导出置信度阈值: {KEY_AREA_THRESHOLD}")
else:
    print(f"⚠️ 未检测到自定义阈值，使用默认值: {KEY_AREA_THRESHOLD}")
# ==================================================================================

# 让 ORE_CALC_TYPE 同时接管色彩样式
if ORE_CALC_TYPE != '':
    mineral_type = ORE_CALC_TYPE.lower()


# ==================== 3. 核心矩阵计算逻辑分支 ====================
print(f"当前设置的计算类型为: '{ORE_CALC_TYPE}'")
if ORE_CALC_TYPE == '':
    print("--> 正在执行默认矩阵计算 (确保与 untitled2.m 逻辑完全一致)...")
else:
    print(f"--> 检测到特定矿体 '{ORE_CALC_TYPE}'，正在执行专属矩阵运算...")

    if ORE_CALC_TYPE.lower() == 'petroleum':
        print("--> 匹配为: 石油 (Petroleum) 专属计算逻辑")
    elif ORE_CALC_TYPE == 'Au_vein':
        print("--> 匹配为: 金矿脉专属逻辑")
    else:
        print(f"--> 匹配为: '{ORE_CALC_TYPE}' 通用专属逻辑")

# ==================== 4. 动态矿种/资源色彩体系配置 ====================
RESOURCE_STYLES = {
    'gold': {
        'cmap': 'jet',
        'key_line_color': 'red',
        'top_fill': 'purple',
        'top_point': 'red',
        'top_edge': 'yellow'
    },
    'copper': {
        'cmap': 'magma',
        'key_line_color': '#00FFFF',
        'top_fill': '#2E8B57',
        'top_point': '#FF8C00',
        'top_edge': 'white'
    },
    'petroleum': {
        'cmap': 'bone_r',
        'key_line_color': '#FFD700',
        'top_fill': '#DAA520',
        'top_point': '#FFFFFF',
        'top_edge': '#FFD700'
    },
    'gas': {
        'cmap': 'GnBu',
        'key_line_color': '#FF1493',
        'top_fill': '#00BFFF',
        'top_point': '#0000FF',
        'top_edge': 'white'
    },
    'other': {
        'cmap': 'viridis',
        'key_line_color': '#FF00FF',
        'top_fill': '#8A2BE2',
        'top_point': '#FFFFFF',
        'top_edge': '#FF0000'
    }
}

current_style = RESOURCE_STYLES.get(mineral_type, RESOURCE_STYLES['other'])
COLOR_THEME = current_style['cmap']
KEY_LINE_COLOR = current_style['key_line_color']
TOP_FILL_COLOR = current_style['top_fill']
TOP_POINT_COLOR = current_style['top_point']
TOP_EDGE_COLOR = current_style['top_edge']

# ==================== 5. 坐标投影与 KDTree 空间索引 ====================
avg_lon = np.mean(lonGrid)
utm_zone = int((avg_lon + 180) / 6) + 1
crs_utm = f'EPSG:326{utm_zone}'
transformer_ll2utm = Transformer.from_crs("EPSG:4326", crs_utm, always_xy=True)
transformer_utm2ll = Transformer.from_crs(crs_utm, "EPSG:4326", always_xy=True)

n_points = min(10, len(lonTop)) if len(lonTop) > 0 else 0
utm_x_Top, utm_y_Top = np.array([]), np.array([])
if n_points > 0:
    utm_x_Top, utm_y_Top = transformer_ll2utm.transform(lonTop[:n_points], latTop[:n_points])

utm_x_grid, utm_y_grid = transformer_ll2utm.transform(lonGrid, latGrid)

Au_deep_flip = np.flipud(Au_deep)
utm_x_ROI, utm_y_ROI = (transformer_ll2utm.transform(lonROI, latROI) if has_roi and len(lonROI) > 0 else (None, None))

points_grid = np.column_stack((utm_x_grid.ravel(), utm_y_grid.ravel()))
kdtree = cKDTree(points_grid)

def smooth_polygon(x, y, num_points=100):
    x, y = x[:-1], y[:-1]
    tck, u = splprep([x, y], k=3, per=True, s=0)
    u_new = np.linspace(u.min(), u.max(), num_points)
    x_new, y_new = splev(u_new, tck)
    return np.append(x_new, x_new[0]), np.append(y_new, y_new[0])

def get_top_convex_hull(lon_arr, lat_arr, transformer, smooth_num=TOP_AREA_SMOOTH_POINTS):
    utm_x, utm_y = transformer.transform(lon_arr, lat_arr)
    points_utm = np.column_stack((utm_x, utm_y))
    hull = ConvexHull(points_utm)
    hull_utm_x, hull_utm_y = points_utm[hull.vertices, 0], points_utm[hull.vertices, 1]
    hull_utm_x, hull_utm_y = np.append(hull_utm_x, hull_utm_x[0]), np.append(hull_utm_y, hull_utm_y[0])
    hull_utm_x_smooth, hull_utm_y_smooth = smooth_polygon(hull_utm_x, hull_utm_y, smooth_num)
    hull_lon_smooth, hull_lat_smooth = transformer.transform(hull_utm_x_smooth, hull_utm_y_smooth, direction='INVERSE')
    return (hull_utm_x_smooth, hull_utm_y_smooth), (hull_lon_smooth, hull_lat_smooth)


# ==================== 6. 核心绘图函数 ====================
auto_cmap = plt.get_cmap(COLOR_THEME)
step_levels = np.linspace(BASE_THRESHOLD, 1.0, STEP_NUM + 1)

def create_figure(transparent=False):
    fig, ax = plt.subplots(figsize=(14, 11), facecolor='none' if transparent else 'white')

    contour_plot = ax.contourf(utm_x_grid, utm_y_grid, Au_deep_flip, levels=step_levels, cmap=auto_cmap, extend='both',
                               zorder=0)

    ax.contour(utm_x_grid, utm_y_grid, Au_deep_flip, levels=step_levels, colors='black', linewidths=0.5, alpha=0.4,
               zorder=1)

    # 绘制高亮起点线 (避免浮点数导致找不到对应的 level)
    actual_key_level = min(step_levels, key=lambda x: abs(x - KEY_AREA_THRESHOLD))
    ax.contour(utm_x_grid, utm_y_grid, Au_deep_flip, levels=[actual_key_level], colors=KEY_LINE_COLOR, linewidths=2.5,
               zorder=3)

    if has_roi and utm_x_ROI is not None:
        ax.plot(utm_x_ROI, utm_y_ROI, 'k-', linewidth=3, zorder=5)

    if n_points > 0:
        hull_utm, _ = get_top_convex_hull(lonTop[:n_points], latTop[:n_points], transformer_ll2utm)
        ax.fill(hull_utm[0], hull_utm[1], color=TOP_FILL_COLOR, alpha=TOP_AREA_FILL_ALPHA / 255, zorder=8)
        ax.plot(hull_utm[0], hull_utm[1], color='white', linewidth=TOP_AREA_LINE_WIDTH, zorder=9)

        ax.scatter(utm_x_Top, utm_y_Top, s=8 ** 2, facecolor=TOP_POINT_COLOR, edgecolor='black', linewidth=1.0,
                   zorder=10)

        if len(redIdx) > 0:
            redIdx_py = (redIdx - 1)[(redIdx - 1) < n_points]
            ax.scatter(utm_x_Top[redIdx_py], utm_y_Top[redIdx_py], s=14 ** 2,
                       facecolor=TOP_POINT_COLOR, edgecolor=TOP_EDGE_COLOR, linewidth=2.0, zorder=11)

    if transparent:
        ax.axis('off')
        ax.set_position([0, 0, 1, 1])
    else:
        ax.set_aspect('equal')
        mineral_name = mineral_type.capitalize()
        logic_title = "默认逻辑" if ORE_CALC_TYPE == '' else f"{ORE_CALC_TYPE} 专属逻辑"
        ax.set_title(f'2026 {mineral_name} 资源深部预测 - 学术高级制图 ({logic_title})', fontsize=20, pad=15)
        cbar = fig.colorbar(contour_plot, ax=ax, location='bottom', shrink=0.8,
                            ticks=np.linspace(BASE_THRESHOLD, 1.0, 7))
        cbar.ax.tick_params(labelsize=12)

    return fig, ax


# ==================== 7. 交互式显示 ====================
print("正在启动交互式窗口...")
fig_interactive, ax_interactive = create_figure(transparent=False)

info_text = ax_interactive.text(
    0.98, 0.02, '', transform=ax_interactive.transAxes, fontsize=12,
    verticalalignment='bottom', horizontalalignment='right',
    bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.95, edgecolor='black', linewidth=1.5)
)
info_text.set_visible(False)

def get_value_at_xy(x, y):
    lon, lat = transformer_utm2ll.transform(x, y)
    dist, idx = kdtree.query([x, y])
    min_idx = np.unravel_index(idx, Au_deep_flip.shape)
    return lon, lat, round(Au_deep_flip[min_idx], 4)

def on_hover(event):
    if event.inaxes == ax_interactive and event.xdata is not None:
        lon, lat, value = get_value_at_xy(event.xdata, event.ydata)
        info_text.set_text(
            f'UTM坐标: ({event.xdata:.0f}, {event.ydata:.0f})\n经纬度: ({lon:.6f}, {lat:.6f})\n预测值: {value}')
        info_text.set_visible(True)
    else:
        info_text.set_visible(False)
    fig_interactive.canvas.draw_idle()

fig_interactive.canvas.mpl_connect('motion_notify_event', on_hover)
plt.tight_layout()
plt.show(block=True)

# ==================== 8. 生成 KMZ ====================
print("正在生成地图叠加包 (KMZ)...")
fig_kml, ax_kml = create_figure(transparent=True)

logic_suffix = "Default" if ORE_CALC_TYPE == '' else ORE_CALC_TYPE
img_filename = f"【{mineral_type.capitalize()}】预测图_学术高级版_{logic_suffix}.png"
img_path = os.path.join(output_dir, img_filename)
plt.savefig(img_path, dpi=300, transparent=True, bbox_inches='tight', pad_inches=0)
plt.close(fig_kml)

kml = simplekml.Kml()
kml.document.name = f"{mineral_type.capitalize()} 资源深部预测 - 学术标准 (阈值≥{KEY_AREA_THRESHOLD})"

# 底图层
ground = kml.newgroundoverlay(name="预测图层")
ground.icon.href = img_filename
ground.latlonbox.north, ground.latlonbox.south = np.max(latGrid), np.min(latGrid)
ground.latlonbox.east, ground.latlonbox.west = np.max(lonGrid), np.min(lonGrid)
ground.color = 'CC000000'

# 矢量层 (重点成矿区)
fol_zones = kml.newfolder(name=f"高潜力区 (≥{KEY_AREA_THRESHOLD})")

# 【核心修复】引入 -1e-4 补偿浮点数精度误差，确保 0.7 能够正确识别
kmz_levels = [lvl for lvl in step_levels if lvl >= KEY_AREA_THRESHOLD - 1e-4]

if kmz_levels:
    cnt_kmz = ax_kml.contour(utm_x_grid, utm_y_grid, Au_deep_flip, levels=kmz_levels, alpha=0)
    for i, level in enumerate(cnt_kmz.levels):
        paths = cnt_kmz.allsegs[i]
        color_ratio = (level - BASE_THRESHOLD) / (1.0 - BASE_THRESHOLD)
        rgba = auto_cmap(max(0.0, min(1.0, color_ratio)))
        r, g, b = int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255)
        hex_color_kml = f"99{b:02X}{g:02X}{r:02X}"
        for path in paths:
            if len(path) > 2:
                lons, lats = transformer_utm2ll.transform(path[:, 0], path[:, 1])
                pol = fol_zones.newpolygon(name=f"预测梯度 ≥ {level:.2f}", outerboundaryis=list(zip(lons, lats)))
                pol.style.polystyle.color = hex_color_kml
                pol.style.linestyle.color = 'FFFFFFFF'
                pol.style.linestyle.width = 1

# ROI 边界层
if has_roi and len(lonROI) > 0:
    fol_roi = kml.newfolder(name="探测范围边界 (ROI)")
    roi_coords = list(zip(lonROI, latROI))
    if roi_coords[0] != roi_coords[-1]: roi_coords.append(roi_coords[0])
    pol = fol_roi.newpolygon(name="ROI边界", outerboundaryis=roi_coords)
    pol.style.polystyle.color = '00FFFFFF'
    pol.style.linestyle.color = 'FF000000'
    pol.style.linestyle.width = 4

# Top 点位层
if n_points > 0:
    fol_top = kml.newfolder(name="Top 靶区核心点")
    for i in range(n_points):
        pnt = fol_top.newpoint(name=f"Target_{i + 1}", coords=[(lonTop[i], latTop[i])])
        pnt.style.iconstyle.scale = 1.0 if (i + 1) in redIdx else 0.7

kmz_path = os.path.join(output_dir, f"【{mineral_type.capitalize()}】预测图_学术高级版_阈值{KEY_AREA_THRESHOLD}.kmz")
kml.savekmz(kmz_path)

print(f"处理完成！")
print(f"当前资源样式：{mineral_type.capitalize()}")
print(f"运算逻辑状态：{'默认 (untitled2.m同步)' if ORE_CALC_TYPE == '' else f'专属 ({ORE_CALC_TYPE})'}")
print(f"输出阈值基准：{KEY_AREA_THRESHOLD}")
print(f"输出目录：{output_dir}")