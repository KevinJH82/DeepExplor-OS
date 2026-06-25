"""
delivery — 按 ROI 空间重叠从"交付数据库"自动匹配并定位所需数据目录。

替代"上传 zip 数据"的新取数方式:用户只提供 ROI 坐标,系统扫描交付根目录下各项目根的
.ovkml/.kml ROI 文件,按空间重叠匹配出最佳项目,再定位其季节数据子目录
(默认冬季)作为 geo-exploration 读取器的 data_dir。

交付目录结构(已核实):
    <交付根>/<项目名>/<项目名>.ovkml                     # 项目 ROI(匹配键)
    <交付根>/<项目名>/data-矿权-冬季（11-3月）/{ASTER L2, Sentinel 2 L2*, Landsat 8 L2*, DEM.tif, ...}
    <交付根>/<项目名>/data-矿权-夏季（6-8月）/...

geo-exploration 现有 read_sentinel2/read_aster/... 可直接把"季节子目录"当 data_dir 读取。

零硬崩溃:重依赖在函数内 import;交付根不可达 / 无匹配 → 返回 (None, {}),调用方降级。
"""

import os
import glob

DEFAULT_DELIVERY_ROOT = os.environ.get(
    'DELIVERY_ROOT', '/Volumes/大硬盘可劲用/DeepExplor/数据留存/交付数据')


def _find_season_dir(project_dir, season, log=print):
    """在交付项目目录下定位季节数据子目录。season: 'winter'|'summer'|'auto'。"""
    winter = [d for d in glob.glob(os.path.join(project_dir, 'data*冬*')) if os.path.isdir(d)]
    summer = [d for d in glob.glob(os.path.join(project_dir, 'data*夏*')) if os.path.isdir(d)]
    order = {
        'winter': [winter, summer],
        'summer': [summer, winter],
        'auto':   [winter, summer],
    }.get(season, [winter, summer])
    for group in order:
        if group:
            return sorted(group)[0]
    # 兜底:任意 data* 目录
    other = [d for d in glob.glob(os.path.join(project_dir, 'data*')) if os.path.isdir(d)]
    return sorted(other)[0] if other else None


def resolve_delivery_data_dir(roi_poly_lonlat, dcfg, log=print):
    """按 ROI 空间重叠从交付库匹配项目,返回 (季节数据目录, info) 或 (None, {})。

    info = {'project','overlap','season','roi_file'}。
    """
    dcfg = dcfg or {}
    root = dcfg.get('root') or DEFAULT_DELIVERY_ROOT
    if not root or not os.path.isdir(root):
        log(f"交付根目录不可访问,跳过自动取数: {root}")
        return None, {}
    if roi_poly_lonlat is None or len(roi_poly_lonlat) < 3:
        log("ROI 坐标不足,无法做交付匹配")
        return None, {}

    try:
        from .geo_bridge_common import roi_overlap_frac
        from .geo_utils import _parse_kml_coordinates
    except Exception as e:
        log(f"交付匹配依赖缺失,跳过: {e}")
        return None, {}

    import numpy as np
    min_ov = float(dcfg.get('min_roi_overlap', 0.10))
    season = dcfg.get('season', 'winter')

    best = None  # (overlap, project_dir, project_name, roi_file)
    for proj in sorted(os.listdir(root)):
        pdir = os.path.join(root, proj)
        if not os.path.isdir(pdir):
            continue
        roi_files = (glob.glob(os.path.join(pdir, '*.ovkml')) +
                     glob.glob(os.path.join(pdir, '*.kml')))
        if not roi_files:
            continue
        try:
            lon, lat = _parse_kml_coordinates(roi_files[0])
            geom = {'type': 'Polygon',
                    'coordinates': [np.column_stack([lon, lat]).tolist()]}
            ov = roi_overlap_frac(roi_poly_lonlat, geom)
        except Exception:
            continue
        if best is None or ov > best[0]:
            best = (ov, pdir, proj, roi_files[0])

    if best is None or best[0] < min_ov:
        log(f"交付匹配:交付库中无 ROI 重叠达标的项目"
            f"(最佳 {best[0]:.3f} < {min_ov})" if best else "交付匹配:交付库中无可用 ROI 文件")
        return None, {}

    season_dir = _find_season_dir(best[1], season, log)
    if not season_dir:
        log(f"交付项目 {best[2]} 下未找到季节数据目录")
        return None, {}

    return season_dir, {
        'project': best[2], 'overlap': best[0],
        'season': os.path.basename(season_dir), 'roi_file': best[3],
    }
