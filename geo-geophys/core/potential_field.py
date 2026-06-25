"""位场（重磁）处理 —— FFT 频率域滤波 + 欧拉反褶积（自实现，标准公式）。

输入：已重投影到 UTM 米制、等间距、无 NaN 的二维场 (ny,nx) + 像元 dx,dy(米)。
所有滤波在频率域：建波数 → 乘算子 → 反变换。为压制小网格边缘效应，统一做
去趋势 + 余弦窗(Tukey) + 反射填充。

⚠ 现实尺度：本服务输入是全球 EMAG2(~4km,且已上延4km)等区域网格，故所有产物为
**区域尺度**——磁源深度为 km 级区域估计，非矿体尺度。
"""

from __future__ import annotations

from typing import List, Tuple, Dict

import numpy as np


# ─────────────────────────────────────────────
# 频率域基础设施
# ─────────────────────────────────────────────
def _tukey2d(ny: int, nx: int, alpha: float = 0.25) -> np.ndarray:
    def w1(n):
        if n <= 1:
            return np.ones(n)
        x = np.linspace(0, 1, n)
        w = np.ones(n)
        a = alpha / 2.0
        lo = x < a
        hi = x > 1 - a
        w[lo] = 0.5 * (1 + np.cos(np.pi * (x[lo] / a - 1)))
        w[hi] = 0.5 * (1 + np.cos(np.pi * ((x[hi] - 1 + a) / a)))
        return w
    return np.outer(w1(ny), w1(nx))


def _detrend_plane(f: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """去一阶平面趋势，返回 (残差, 趋势面)。"""
    ny, nx = f.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    A = np.column_stack([xx.ravel(), yy.ravel(), np.ones(f.size)])
    with np.errstate(all='ignore'):   # 静默 macOS Accelerate BLAS 伪警告
        coef, *_ = np.linalg.lstsq(A, f.ravel(), rcond=None)
        trend = (A @ coef).reshape(f.shape)
    if not np.all(np.isfinite(trend)):
        trend = np.full_like(f, float(np.mean(f)))
    return f - trend, trend


def _wavenumbers(ny: int, nx: int, dy: float, dx: float):
    kx = 2 * np.pi * np.fft.fftfreq(nx, d=dx)
    ky = 2 * np.pi * np.fft.fftfreq(ny, d=dy)
    KX, KY = np.meshgrid(kx, ky)
    K = np.sqrt(KX ** 2 + KY ** 2)
    return KX, KY, K


def _apply_operator(field: np.ndarray, dy: float, dx: float, op_func) -> np.ndarray:
    """去趋势+加窗+反射填充 → FFT → 乘算子 → IFFT → 裁回原尺寸。op_func(KX,KY,K)->复算子。"""
    res, trend = _detrend_plane(field)
    ny, nx = res.shape
    win = _tukey2d(ny, nx, 0.25)
    res_w = res * win
    # 反射填充到 ~2x，减小卷绕
    py, px = ny // 2 + 1, nx // 2 + 1
    padded = np.pad(res_w, ((py, py), (px, px)), mode='reflect')
    PNY, PNX = padded.shape
    KX, KY, K = _wavenumbers(PNY, PNX, dy, dx)
    op = op_func(KX, KY, K)
    out = np.real(np.fft.ifft2(np.fft.fft2(padded) * op))
    out = out[py:py + ny, px:px + nx]
    return out


# ─────────────────────────────────────────────
# 各类滤波
# ─────────────────────────────────────────────
def vertical_derivative(field, dy, dx, order: int = 1) -> np.ndarray:
    return _apply_operator(field, dy, dx, lambda KX, KY, K: K ** order)


def horizontal_derivatives(field, dy, dx) -> Tuple[np.ndarray, np.ndarray]:
    dfdx = _apply_operator(field, dy, dx, lambda KX, KY, K: 1j * KX)
    dfdy = _apply_operator(field, dy, dx, lambda KX, KY, K: 1j * KY)
    return dfdx, dfdy


def total_horizontal_derivative(field, dy, dx) -> np.ndarray:
    dfdx, dfdy = horizontal_derivatives(field, dy, dx)
    return np.hypot(dfdx, dfdy)


def analytic_signal(field, dy, dx) -> np.ndarray:
    """解析信号(总梯度幅值) AS = sqrt(fx²+fy²+fz²)。"""
    dfdx, dfdy = horizontal_derivatives(field, dy, dx)
    dfdz = vertical_derivative(field, dy, dx, 1)
    return np.sqrt(dfdx ** 2 + dfdy ** 2 + dfdz ** 2)


def tilt_angle(field, dy, dx) -> np.ndarray:
    """倾斜角 TDR = atan2(fz, THD)，弧度→度。"""
    dfdx, dfdy = horizontal_derivatives(field, dy, dx)
    dfdz = vertical_derivative(field, dy, dx, 1)
    thd = np.hypot(dfdx, dfdy)
    return np.degrees(np.arctan2(dfdz, thd))


def upward_continuation(field, dy, dx, height_m: float) -> np.ndarray:
    h = abs(float(height_m))
    return _apply_operator(field, dy, dx, lambda KX, KY, K: np.exp(-K * h))


def reduction_to_pole(field, dy, dx, inc_deg: float, dec_deg: float) -> np.ndarray:
    """RTP 化极（Blakely 公式；感应磁化，磁化方向=地磁场方向）。
    KX=东向波数, KY=北向波数, 偏角 dec 自北顺时针。"""
    I = np.radians(inc_deg)
    D = np.radians(dec_deg)
    mx, my, mz = np.cos(I) * np.sin(D), np.cos(I) * np.cos(D), np.sin(I)

    def op(KX, KY, K):
        Ksafe = np.where(K == 0, 1.0, K)
        theta = (mz + 1j * (mx * KX + my * KY) / Ksafe)
        denom = theta * theta
        filt = np.where(np.abs(denom) < 1e-6, 1.0, 1.0 / denom)
        filt[K == 0] = 1.0   # DC 保留
        return filt
    return _apply_operator(field, dy, dx, op)


# ─────────────────────────────────────────────
# 欧拉反褶积（滑动窗最小二乘，求磁源三维位置）
# ─────────────────────────────────────────────
def euler_deconvolution(field, dy, dx, x_origin: float, y_origin: float,
                        si: float = 1.0, window: int = 10, stride: int = None,
                        depth_min_m: float = 100.0, depth_max_m: float = 20000.0,
                        max_points: int = 300) -> List[Dict]:
    """
    标准欧拉方程： (x-x0)Tx+(y-y0)Ty+(z-z0)Tz = N(B-T)，z 向下为正、观测面 z=0。
    解每窗 [x0,y0,z0,B]。返回 [{x,y,depth_m,si,confidence,misfit}]（x,y 为 UTM 米，含 origin）。
    confidence∈[0,1] 由拟合残差(misfit，局部尺度归一化)与解-窗贴合度合成，供下游据此调权。
    field 索引 (row=y 北→南? )：约定 row0 对应北边(y 最大)，与栅格一致由调用方保证。
    """
    ny, nx = field.shape
    win = max(3, int(window))
    win = min(win, ny, nx)
    if stride is None:
        stride = max(1, win // 2)

    Tx, Ty = horizontal_derivatives(field, dy, dx)
    Tz = vertical_derivative(field, dy, dx, 1)   # ∂T/∂z（向上为正方向的 z）

    # 像元中心 UTM 坐标：col→x 东向；row→y 北向（row 0 = 北边 y 最大）
    xs = x_origin + (np.arange(nx) + 0.5) * dx
    ys = y_origin + (ny - 0.5 - np.arange(ny)) * dy
    XX, YY = np.meshgrid(xs, ys)

    pts: List[Dict] = []
    N = float(si)
    for r0 in range(0, ny - win + 1, stride):
        for c0 in range(0, nx - win + 1, stride):
            sl = (slice(r0, r0 + win), slice(c0, c0 + win))
            tx, ty, tz = Tx[sl].ravel(), Ty[sl].ravel(), Tz[sl].ravel()
            T = field[sl].ravel()
            xx, yy = XX[sl].ravel(), YY[sl].ravel()
            # A·[x0,y0,z0,B] = d ; 行: x0*Tx+y0*Ty+z0*Tz - N*B = x*Tx+y*Ty+N*T
            A = np.column_stack([tx, ty, tz, -N * np.ones_like(tx)])
            d = xx * tx + yy * ty + N * T
            # 梯度太小的窗跳过（无明显异常）
            if np.sqrt(np.mean(tx**2 + ty**2 + tz**2)) < 1e-9:
                continue
            try:
                sol, *_ = np.linalg.lstsq(A, d, rcond=None)
            except Exception:
                continue
            x0, y0, z0, B = sol
            depth = abs(float(z0))
            if not (depth_min_m <= depth <= depth_max_m):
                continue
            # 解应落在窗附近
            if not (xs[c0] - win * dx <= x0 <= xs[c0] + 2 * win * dx and
                    ys[r0 + win - 1] - win * dy <= y0 <= ys[r0] + win * dy):
                continue

            # ── 置信度：拟合残差 + 解-窗贴合度（经验权重，可调）──
            # 注：条件数"稳定性"会被随机噪声虚高（噪声→良态但拟合差），故不纳入。
            xc = 0.5 * (xs[c0] + xs[c0 + win - 1])
            yc = 0.5 * (ys[r0] + ys[r0 + win - 1])
            hx, hy = 0.5 * win * dx, 0.5 * win * dy
            # 残差按**局部**数据尺度归一化（坐标相对窗心，消去 ~1e6 的 UTM 偏移虚高）
            resid = A @ sol - d
            d_local = (xx - xc) * tx + (yy - yc) * ty + N * T
            scale = float(np.sqrt(np.mean(d_local ** 2)))
            misfit = float(np.sqrt(np.mean(resid ** 2)) / (scale + 1e-12))
            misfit_norm = min(misfit, 1.0)
            # 贴合度：解距窗中心的归一化距离（越居中越可信）
            fit_pos = min(np.hypot((x0 - xc) / hx, (y0 - yc) / hy) / np.sqrt(2.0), 1.0)
            confidence = float(np.clip(
                0.7 * (1.0 - misfit_norm) + 0.3 * (1.0 - fit_pos), 0.0, 1.0))

            pts.append({"x": float(x0), "y": float(y0), "depth_m": depth, "si": N,
                        "confidence": confidence, "misfit": round(misfit, 4)})

    # 截断优先保留高置信解（避免被深度排序挤掉），最终仍按深度排序输出
    if len(pts) > max_points:
        pts.sort(key=lambda p: p["confidence"], reverse=True)
        pts = pts[:max_points]
    pts.sort(key=lambda p: p["depth_m"])
    return pts


def cluster_euler_sources(points: List[Dict], dx: float, dy: float,
                          horiz_scale: float = None, depth_scale: float = None,
                          t: float = 1.5) -> List[Dict]:
    """把欧拉散点云聚成"磁源"簇（scipy 层次聚类 ward，无新依赖）。

    对 (x, y, depth) 归一化后聚类：水平默认按 ~8 个像元(≈欧拉窗footprint，使同一源的
    共位解合并)、深度按点云自身尺度，使两者可比。调用方可传 horiz_scale=window*像元更贴合。
    每簇汇总为一个磁源：置信度加权质心 + 中位深度 + 深度带 σ(MAD) + 簇置信度 + 成员数。
    返回 [{x,y,depth_m,depth_sigma_m,confidence,n_members}]（x,y UTM 米，按深度排序）。
    退化：点数 < 3 时每点自成一簇。阈值 t 与尺度为经验值，可调；metadata 暴露 n_clusters 便于观察。
    """
    if not points:
        return []
    arr_x = np.array([p["x"] for p in points], float)
    arr_y = np.array([p["y"] for p in points], float)
    arr_z = np.array([p["depth_m"] for p in points], float)
    arr_c = np.array([p.get("confidence", 0.5) for p in points], float)
    n = len(points)

    if n < 3:
        labels = np.arange(n)
    else:
        hs = horiz_scale if horiz_scale else 8.0 * 0.5 * (abs(dx) + abs(dy))
        zs = depth_scale if depth_scale else max(float(np.std(arr_z)), 1.0)
        feats = np.column_stack([arr_x / hs, arr_y / hs, arr_z / zs])
        from scipy.cluster.hierarchy import linkage, fcluster
        Z = linkage(feats, method="ward")
        labels = fcluster(Z, t=t, criterion="distance")

    # 把簇号回写到输入点（供点级 GeoJSON 带 cluster_id）
    for i, lab in enumerate(labels):
        points[i]["cluster_id"] = int(lab)

    clusters: List[Dict] = []
    for lab in np.unique(labels):
        m = labels == lab
        w = arr_c[m]
        wsum = float(w.sum()) or 1.0
        zc = arr_z[m]
        med = float(np.median(zc))
        n_mem = int(m.sum())
        # 深度带 σ：MAD→σ；单点簇无内部离散，给保守默认（沿用下游历史 300m）
        if n_mem >= 2:
            mad = float(np.median(np.abs(zc - med)))
            depth_sigma = max(1.4826 * mad, 50.0)
        else:
            depth_sigma = 300.0
        # 簇置信度：成员多 + 各自可信 + 深度集中 → 高
        size_term = min(n_mem / 5.0, 1.0)
        mean_c = float(w.mean())
        spread_term = float(np.exp(-(depth_sigma / (abs(med) + 1e-6))))
        conf = float(np.clip(0.4 * size_term + 0.4 * mean_c + 0.2 * spread_term, 0.0, 1.0))
        clusters.append({
            "x": float((arr_x[m] * w).sum() / wsum),
            "y": float((arr_y[m] * w).sum() / wsum),
            "depth_m": med,
            "depth_sigma_m": round(depth_sigma, 1),
            "confidence": round(conf, 3),
            "n_members": n_mem,
        })
    clusters.sort(key=lambda c: c["depth_m"])
    return clusters
