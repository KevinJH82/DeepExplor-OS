"""ICGEM 本地重力场计算 — 球谐综合 (Spherical Harmonic Synthesis)

从 EIGEN-6C4 GGM 球谐系数文件本地计算 ROI 区域内的重力场参数，
无需依赖 ICGEM 在线服务。使用纯 numpy 实现，无 Fortran 依赖。
"""

import threading
from pathlib import Path
from typing import Dict, Any, Optional

import numpy as np
import rasterio
from rasterio.transform import from_bounds as rio_from_bounds
from shapely.geometry import box

from .logger import get_logger
from .http_client import download_file
from config import CACHE_DIR, ICGEM_GFC_URL, ICGEM_DEFAULT_MAX_DEGREE

logger = get_logger("icgem")

# 全局下载锁
_gfc_lock = threading.Lock()

# WGS84 参数
_WGS84_A = 6378137.0          # 半长轴 (m)
_WGS84_B = 6356752.314245     # 半短轴 (m)
_WGS84_GM = 3.986004418e14    # 地球引力常数 (m³/s²)
_WGS84_GAMMA_E = 9.7803253359 # 赤道正常重力 (m/s²)
_WGS84_GAMMA_P = 9.8321849378 # 极正常重力 (m/s²)


def ensure_gfc_cached() -> Path:
    """下载并缓存 EIGEN-6C4 .gfc 系数文件（线程安全）

    GFZ 提供 zip 格式下载 (~55MB)，自动解压获取 .gfc 文件。
    """
    import zipfile
    cache_dir = Path(CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    gfc_path = cache_dir / "EIGEN-6C4.gfc"

    with _gfc_lock:
        if not gfc_path.exists():
            zip_path = cache_dir / "eigen-6c4.zip"
            if not zip_path.exists():
                logger.info("下载 EIGEN-6C4 球谐系数文件 (~55MB zip)...")
                download_file(ICGEM_GFC_URL, zip_path)
                logger.info("EIGEN-6C4 下载完成")

            # 解压 zip 获取 .gfc 文件
            logger.info("解压系数文件...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                gfc_files = [n for n in zf.namelist() if n.endswith('.gfc')]
                if gfc_files:
                    with zf.open(gfc_files[0]) as src, open(gfc_path, 'wb') as dst:
                        dst.write(src.read())
                    logger.info("解压完成: %s → %s", gfc_files[0], gfc_path.name)
                else:
                    # zip 里没有 .gfc，可能文件名不同，直接用 zip 当作文本处理
                    logger.warning("zip 中未找到 .gfc 文件，尝试直接解压第一个文件")
                    first = zf.namelist()[0]
                    with zf.open(first) as src, open(gfc_path, 'wb') as dst:
                        dst.write(src.read())

            # 解压后删除 zip 节省空间
            try:
                zip_path.unlink()
            except Exception:
                pass

    return gfc_path


def parse_gfc_coefficients(
    gfc_path: Path,
    max_degree: int = ICGEM_DEFAULT_MAX_DEGREE,
) -> Dict[str, Any]:
    """
    解析 ICGEM .gfc 系数文件，提取截断到 max_degree 的 C/S 系数

    .gfc 格式:
      - 头部: 从 begin_of_head 到 end_of_head
      - 数据行: gfc L M C S [sigma_C sigma_S ...]

    Returns:
        {"C": ndarray(N+1, N+1), "S": ndarray(N+1, N+1),
         "GM": float, "radius": float, "model_name": str}
    """
    C = np.zeros((max_degree + 1, max_degree + 1), dtype=np.float64)
    S = np.zeros((max_degree + 1, max_degree + 1), dtype=np.float64)
    gm = _WGS84_GM
    radius = _WGS84_A
    model_name = "EIGEN-6C4"
    in_header = False

    with open(gfc_path, 'r', encoding='latin-1') as f:
        for line in f:
            line = line.strip()

            if line.startswith('begin_of_head'):
                in_header = True
                continue
            if line.startswith('end_of_head'):
                in_header = False
                continue

            if in_header:
                if line.startswith('earth_gravity_constant'):
                    try:
                        gm = float(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                elif line.startswith('radius'):
                    try:
                        radius = float(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                elif line.startswith('modelname'):
                    model_name = line.split()[1] if line.split()[1:] else model_name
                continue

            # 数据行
            if not line.startswith('gfc'):
                continue

            parts = line.split()
            if len(parts) < 5:
                continue

            try:
                n = int(parts[1])
                m = int(parts[2])
            except ValueError:
                continue

            if n > max_degree:
                break  # 系数按 n 排序，超出即可停止

            if n < 0 or m < 0 or m > n:
                continue

            try:
                C[n, m] = float(parts[3])
                S[n, m] = float(parts[4])
            except (IndexError, ValueError):
                continue

    logger.info("解析 %s 系数: %d 阶截断, GM=%.6e, R=%.1f m",
                model_name, max_degree, gm, radius)

    return {
        "C": C, "S": S,
        "GM": gm, "radius": radius,
        "model_name": model_name,
        "max_degree": max_degree,
    }


def compute_legendre(sin_phi: float, max_degree: int) -> np.ndarray:
    """
    计算完全规格化缔合勒让德函数 P̃_nm(sinφ)

    使用向量化递推: 对 m 向量化，只循环 n。
    比 Python 双循环快 ~50-100x。

    Returns:
        ndarray of shape (max_degree+1, max_degree+1), P[n, m]
    """
    cos_phi = np.sqrt(max(0.0, 1.0 - sin_phi ** 2))
    N = max_degree
    P = np.zeros((N + 1, N + 1), dtype=np.float64)

    P[0, 0] = 1.0
    if N == 0:
        return P

    # 对角线: P(n,n)
    # P(1,1) = sqrt(3) * cosφ (特殊)
    # P(n,n) = sqrt((2n+1)/(2n)) * cosφ * P(n-1,n-1) for n >= 2
    all_factors = np.empty(N, dtype=np.float64)
    all_factors[0] = np.sqrt(3.0) * cos_phi
    if N >= 2:
        ns = np.arange(2, N + 1, dtype=np.float64)
        all_factors[1:] = np.sqrt((2.0 * ns + 1.0) / (2.0 * ns)) * cos_phi
    diag = np.empty(N + 1, dtype=np.float64)
    diag[0] = 1.0
    np.cumprod(all_factors, out=diag[1:])
    idx = np.arange(N + 1)
    P[idx, idx] = diag

    # 超对角线: P(n, n-1)
    # P(1,0) = sqrt(3) * sinφ (特殊，不是 sqrt(3)*sinφ*P(1,1))
    # P(n,n-1) = sqrt(2n+1) * sinφ * P(n,n) for n >= 2
    P[1, 0] = np.sqrt(3.0) * sin_phi
    if N >= 2:
        n_arr = np.arange(2, N + 1, dtype=np.float64)
        super_f = np.sqrt(2.0 * n_arr + 1.0) * sin_phi
        idx2 = np.arange(2, N + 1)
        P[idx2, idx2 - 1] = super_f * diag[idx2]

    # 其余: 对 n 循环, 对 m 向量化
    for n in range(2, N + 1):
        m_max = n - 2
        if m_max < 0:
            continue
        m = np.arange(0, m_max + 1, dtype=np.float64)

        a_nm = np.sqrt(
            (2.0 * n - 1.0) * (2.0 * n + 1.0) /
            ((n - m) * (n + m))
        )
        b_nm = np.zeros(m_max + 1, dtype=np.float64)
        valid = (n + m - 1.0) > 0
        if valid.any():
            mv = m[valid]
            b_nm[valid] = np.sqrt(
                (2.0 * n + 1.0) * (n + mv - 1.0) * (n - mv - 1.0) /
                ((2.0 * n - 3.0) * (n + mv) * (n - mv))
            )

        P[n, :m_max + 1] = (a_nm * sin_phi * P[n - 1, :m_max + 1]
                             - b_nm * P[n - 2, :m_max + 1])

    return P


def _normal_gravity(lat_rad: float) -> float:
    """Somigliana 公式计算椭球面正常重力 (m/s²)"""
    sin2 = np.sin(lat_rad) ** 2
    cos2 = np.cos(lat_rad) ** 2
    num = _WGS84_A * _WGS84_GAMMA_E * cos2 + _WGS84_B * _WGS84_GAMMA_P * sin2
    den = np.sqrt(_WGS84_A ** 2 * cos2 + _WGS84_B ** 2 * sin2)
    return num / den


def synthesize_functional(
    coeffs: Dict[str, Any],
    lats: np.ndarray,
    lons: np.ndarray,
    functional: str = "gravity_disturbance",
) -> np.ndarray:
    """
    球谐综合: 在 lat/lon 网格上计算重力场参数

    使用矩阵乘法替代 n,m 双重循环:
      对每个纬度: sum_m [sum_n C*P * cos(mλ)] + [sum_n S*P * sin(mλ)]
    比双重循环快 10-50x。

    Returns:
        2D ndarray of shape (len(lats), len(lons))
    """
    C = coeffs["C"]
    S_coeff = coeffs["S"]
    GM = coeffs["GM"]
    a = coeffs["radius"]
    N = coeffs["max_degree"]

    nlat = len(lats)
    nlon = len(lons)
    grid = np.zeros((nlat, nlon), dtype=np.float64)

    # 预计算 (n-1) 权重: 重力扰动用 (n-1), 大地水准面用 1
    n_factor = np.ones(N + 1, dtype=np.float64)
    n_factor[0] = 0.0
    n_factor[1] = 0.0
    if functional == "gravity_disturbance":
        for n in range(2, N + 1):
            n_factor[n] = n - 1

    # 预计算 m × lon 三角函数: shape (N+1, nlon)
    m_arr = np.arange(N + 1, dtype=np.float64)
    cos_ml = np.cos(np.outer(m_arr, lons))  # (N+1, nlon)
    sin_ml = np.sin(np.outer(m_arr, lons))  # (N+1, nlon)

    # 预计算 C, S 的 n_factor 加权: weighted_C[n,m] = n_factor[n] * C[n,m]
    wC = n_factor[:, np.newaxis] * C  # (N+1, N+1)
    wS = n_factor[:, np.newaxis] * S_coeff

    for i, lat_rad in enumerate(lats):
        sin_phi = np.sin(lat_rad)
        P = compute_legendre(sin_phi, N)  # (N+1, N+1)

        # A[n,m] = weighted_C[n,m] * P[n,m], sum over n → SA[m]
        A = wC * P
        B = wS * P
        SA = A.sum(axis=0)  # (N+1,)
        SB = B.sum(axis=0)

        # result = SA @ cos_ml + SB @ sin_ml → (nlon,)
        grid[i, :] = SA @ cos_ml + SB @ sin_ml

    # 单位转换
    if functional == "gravity_disturbance":
        grid *= (GM / a ** 2) * 1e5  # → mGal
    elif functional == "geoid_height":
        for i, lat_rad in enumerate(lats):
            gamma = _normal_gravity(lat_rad)
            grid[i, :] *= (a * GM) / (gamma * a ** 2)

    return grid


def save_gravity_geotiff(
    data: np.ndarray,
    lats_deg: np.ndarray,
    lons_deg: np.ndarray,
    output_path: Path,
) -> str:
    """将重力场网格保存为 GeoTIFF"""
    south = float(lats_deg.min())
    north = float(lats_deg.max())
    west = float(lons_deg.min())
    east = float(lons_deg.max())

    transform = rio_from_bounds(west, south, east, north,
                                data.shape[1], data.shape[0])

    profile = {
        "driver": "GTiff",
        "dtype": "float64",
        "width": data.shape[1],
        "height": data.shape[0],
        "count": 1,
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "lzw",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(str(output_path), 'w', **profile) as dst:
        dst.write(data.astype(np.float64), 1)

    return str(output_path)


def generate_gravity_map(
    raster_path: Path,
    output_path: Path,
    roi: Dict[str, Any],
    title: str = "重力场分布",
    cmap: str = "RdBu_r",
    unit: str = "mGal",
) -> Optional[str]:
    """
    生成重力场分布热力图 PNG（复用磁异常图模式）
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        matplotlib.rcParams['font.sans-serif'] = [
            'Hiragino Sans GB', 'Lantinghei SC', 'Heiti TC',
            'STHeiti', 'SimHei', 'Noto Sans CJK SC', 'DejaVu Sans',
        ]
        matplotlib.rcParams['axes.unicode_minus'] = False
    except ImportError:
        return None

    try:
        with rasterio.open(raster_path) as src:
            data = src.read(1).astype(np.float64)
            rows, cols = data.shape
            lon = np.linspace(src.bounds.left, src.bounds.right, cols)
            lat = np.linspace(src.bounds.top, src.bounds.bottom, rows)

        if rows < 2 or cols < 2:
            return None

        fig, ax = plt.subplots(figsize=(12, 9), dpi=150)

        if cmap in ("RdBu_r", "coolwarm", "seismic"):
            vlim = max(abs(np.nanmin(data)), abs(np.nanmax(data)))
            if vlim < 0.01:
                vlim = 1.0
            im = ax.pcolormesh(lon, lat, data, cmap=cmap,
                               vmin=-vlim, vmax=vlim, shading='auto')
        else:
            im = ax.pcolormesh(lon, lat, data, cmap=cmap, shading='auto')

        # ROI 边界
        try:
            from .roi_parser import shape_from_geojson
            roi_shape = shape_from_geojson(roi.get('geometry'))
            if roi_shape is not None:
                polys = list(roi_shape.geoms) if roi_shape.geom_type == 'MultiPolygon' else [roi_shape]
                for poly in polys:
                    x, y = poly.exterior.xy
                    ax.plot(list(x), list(y), 'k-', linewidth=1.5)
        except Exception:
            pass

        # 中心点
        center = roi.get('center', {})
        if center.get('lon') and center.get('lat'):
            ax.plot(center['lon'], center['lat'],
                    marker='*', color='yellow', markersize=12,
                    markeredgecolor='black', markeredgewidth=0.8, zorder=5)

        cbar = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.02)
        cbar.set_label(unit, fontsize=10)
        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.set_title(title)
        ax.grid(True, linestyle=':', alpha=0.4)

        fig.tight_layout()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(output_path), bbox_inches='tight', dpi=150)
        plt.close(fig)

        return str(output_path)

    except Exception as e:
        logger.warning("重力场分布图生成失败: %s", e)
        return None


def compute_icgem_gravity(
    roi: Dict[str, Any],
    output_dir: Path,
    max_degree: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    流水线入口: 对 ROI 区域进行 ICGEM 重力场本地球谐综合

    自适应分辨率: 根据 ROI 面积自动选择阶次，保持计算时间 ~10-30 秒
      - 小 ROI (<2°): N=2190 (~9km)
      - 中 ROI (2-5°): N=1440 (~14km)
      - 大 ROI (5-10°): N=720 (~28km)
      - 超大 ROI (>10°): N=360 (~55km)

    Args:
        roi: parse_roi + expand_bbox 的输出
        output_dir: 输出目录
        max_degree: 球谐截断阶数 (0=自动, 或指定值)

    Returns:
        结果字典或 None（失败时）
    """
    try:
        # 1. 自适应分辨率选择
        # 优先使用扩展后的 bbox（含 buffer），否则用原始 bbox
        b = roi.get('expanded_bbox', roi['bbox'])
        roi_width = abs(b['east'] - b['west'])
        roi_height = abs(b['north'] - b['south'])
        roi_max_dim = max(roi_width, roi_height)

        if max_degree <= 0:
            # 自适应: 目标 ~60-80 个网格点/维度, 计算时间 ~10-30s
            target_points = 70
            max_degree = max(180, min(2190, int(target_points * 180 / roi_max_dim)))
            # 钳制到常用阶次
            for cap in [360, 720, 1080, 1440, 2190]:
                if max_degree <= cap:
                    max_degree = cap
                    break
            else:
                max_degree = 2190

        logger.info("自适应分辨率: ROI %.1f°×%.1f° → N=%d (~%.0fkm)",
                    roi_width, roi_height, max_degree, 180.0 / max_degree * 111)

        # 2. 获取系数文件
        gfc_path = ensure_gfc_cached()

        # 3. 解析系数
        coeffs = parse_gfc_coefficients(gfc_path, max_degree)

        # 4. 构建 ROI 网格
        west, east = b['west'], b['east']
        south, north = b['south'], b['north']

        # Clamp 纬度避免极区数值问题
        south = max(south, -89.9)
        north = min(north, 89.9)

        # 网格间距: 由 max_degree 决定 (180/N 度)
        grid_spacing = 180.0 / max_degree
        lats_deg = np.arange(south, north + grid_spacing * 0.5, grid_spacing)
        lons_deg = np.arange(west, east + grid_spacing * 0.5, grid_spacing)

        nlat = len(lats_deg)
        nlon = len(lons_deg)
        if nlat < 2 or nlon < 2:
            logger.warning("ROI 网格太小 (%d×%d), 跳过 ICGEM 计算", nlat, nlon)
            return None

        lats_rad = np.deg2rad(lats_deg)
        lons_rad = np.deg2rad(lons_deg)

        logger.info("ICGEM 球谐综合: %d阶, 网格 %d×%d, 间距 %.2f°",
                    max_degree, nlat, nlon, grid_spacing)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        functionals = {}

        # 4. 计算各功能量
        for func_name, func_config in [
            ("gravity_disturbance", {
                "title": f"EIGEN-6C4 重力扰动 (N={max_degree})",
                "cmap": "RdBu_r", "unit": "mGal", "suffix": "gravity_disturbance",
            }),
            ("geoid_height", {
                "title": f"EIGEN-6C4 大地水准面高 (N={max_degree})",
                "cmap": "terrain", "unit": "m", "suffix": "geoid_height",
            }),
        ]:
            try:
                logger.info("计算 %s...", func_config["title"])
                grid = synthesize_functional(coeffs, lats_rad, lons_rad, func_name)

                tif_path = output_dir / f"{func_config['suffix']}.tif"
                save_gravity_geotiff(grid, lats_deg, lons_deg, tif_path)

                png_path = output_dir / f"{func_config['suffix']}_map.png"
                map_file = generate_gravity_map(
                    tif_path, png_path, roi,
                    title=func_config["title"],
                    cmap=func_config["cmap"],
                    unit=func_config["unit"],
                )

                functionals[func_name] = {
                    "file": str(tif_path),
                    "map": map_file,
                    "unit": func_config["unit"],
                    "min": round(float(np.nanmin(grid)), 2),
                    "max": round(float(np.nanmax(grid)), 2),
                    "mean": round(float(np.nanmean(grid)), 2),
                }

                logger.info("%s: %.2f ~ %.2f %s (均值 %.2f)",
                            func_config["title"],
                            functionals[func_name]["min"],
                            functionals[func_name]["max"],
                            func_config["unit"],
                            functionals[func_name]["mean"])

            except Exception as e:
                logger.warning("%s 计算失败: %s", func_name, e)
                continue

        if not functionals:
            logger.warning("ICGEM 所有功能量计算失败")
            return None

        return {
            "source": f"ICGEM {coeffs['model_name']} (本地球谐综合, {max_degree}阶)",
            "resolution": f"~{grid_spacing:.2f} arc-degrees (~{grid_spacing * 111:.0f} km)",
            "model": coeffs["model_name"],
            "max_degree": max_degree,
            "functionals": functionals,
        }

    except Exception as e:
        logger.warning("ICGEM 本地重力场计算失败: %s", e)
        return None
