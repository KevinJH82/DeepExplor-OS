"""七慢变量计算函数

所有变量使用边缘校正的高斯滤波，避免NODATA边界处的0值扩散效应。
"""
import numpy as np
from scipy.ndimage import gaussian_filter

from app.config import NODATA
from app.processing import structural


def _valid_mask(data: np.ndarray) -> np.ndarray:
    """返回有效像素掩码（非NODATA且非NaN）"""
    return (data != NODATA) & np.isfinite(data)


def _empty_result(ref: np.ndarray) -> np.ndarray:
    """返回全 NODATA 的结果数组"""
    return np.full_like(ref, NODATA)


def _zscore_on_mask(data: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """在掩码上做 Z-score(无效像元置0),用于把不同量纲的项放到同一尺度再组合"""
    out = np.zeros_like(data, dtype=np.float64)
    vals = data[mask]
    if vals.size == 0:
        return out
    std = float(vals.std())
    if std > 1e-10:
        out[mask] = (data[mask] - float(vals.mean())) / std
    return out


# ─── P2 蚀变指数辅助:诊断比值 + 多端元 z-score 组合 ───────────────
def _ratio_term(a: np.ndarray, b: np.ndarray, base: np.ndarray):
    """诊断比值项 a/b。缺波段或无有效像元返回 None;否则返回 (ratio, mask)。

    比值在两波段同尺度时与定标缩放无关(L2 反射率/辐亮度均适用)。
    """
    if a is None or b is None:
        return None
    bb = b.astype(np.float64)
    m = base & _valid_mask(a) & _valid_mask(b) & (np.abs(bb) > 1e-6)
    if not np.any(m):
        return None
    ratio = np.zeros(a.shape, dtype=np.float64)
    ratio[m] = a.astype(np.float64)[m] / bb[m]
    return (ratio, m)


def _composite_zsum(terms: list, base: np.ndarray):
    """把若干诊断比值项在公共掩码上各自 z-score 后等权相加。

    terms: [(ratio, mask) | None];缺失项自动跳过(优雅降级)。
    返回 (composite, common_mask)。无有效项时 common 为全 False。
    """
    terms = [t for t in terms if t is not None]
    if not terms:
        return np.zeros(base.shape, dtype=np.float64), np.zeros(base.shape, dtype=bool)
    common = base.copy()
    for _, m in terms:
        common &= m
    comp = np.zeros(base.shape, dtype=np.float64)
    if np.any(common):
        for arr, _ in terms:
            comp += _zscore_on_mask(arr, common)
    return comp, common


def _gaussian_filter_valid(data: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    """边缘校正高斯滤波：用有效像素权重归一化，避免NODATA边界的0值扩散"""
    valid = np.where(mask, data.astype(np.float64), 0.0)
    weights = np.where(mask, 1.0, 0.0)
    sum_valid = gaussian_filter(valid, sigma=sigma)
    sum_weights = gaussian_filter(weights, sigma=sigma)
    return np.where(sum_weights > 1e-10, sum_valid / sum_weights, 0.0)


def _gaussian_gradient_valid(data: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    """边缘校正高斯梯度幅值：先平滑再求导，避免边界伪梯度"""
    smoothed = _gaussian_filter_valid(data, mask, sigma)
    gx = gaussian_filter(smoothed, sigma=sigma, order=[1, 0])
    gy = gaussian_filter(smoothed, sigma=sigma, order=[0, 1])
    return np.sqrt(gx**2 + gy**2)


# ─── 变量①：地应力异常梯度 (τ) ────────────────────────────────
def compute_stress_gradient(dem: np.ndarray, insar: np.ndarray = None,
                            insar_coherence: np.ndarray = None,
                            sigma: float = 5.0,
                            pixel_size_m=(30.0, 30.0)) -> np.ndarray:
    """
    地形应力集中代理 = z(坡度) + z(|曲率|)(复用 geo-stru TerrainProcessor)。

    P3改:由原"单纯 DEM 梯度幅值"升级为坡度+曲率的应力集中代理——曲率(坡度二阶导)
    在坡折/脊谷放大,更贴近构造应力集中带。
    InSAR(相干性加权速度梯度)覆盖区优先用动态应力(交付库暂无 InSAR,接口保留);
    两者 z-score 后同尺度融合。无 InSAR 时即地形应力代理。
    """
    mask = _valid_mask(dem)
    if not np.any(mask):
        return _empty_result(dem)

    # 静态:地形应力集中(坡度+|曲率|)
    dem_stress, sm = structural.topographic_stress(dem, pixel_size_m)
    out_mask = sm.copy()
    stress = dem_stress

    if insar is not None and np.any(_valid_mask(insar)):
        insar_mask = _valid_mask(insar)
        if insar_coherence is not None and np.any(_valid_mask(insar_coherence)):
            coh = np.where(_valid_mask(insar_coherence),
                           insar_coherence.astype(np.float64), 0.0)
            src_mask = (coh >= 0.3) & insar_mask          # 低相干(<0.3)剔除
            src = insar.astype(np.float64) * coh          # 速度×相干性
        else:
            src = insar.astype(np.float64)
            src_mask = insar_mask
        insar_grad = _gaussian_gradient_valid(src, src_mask, sigma=sigma)
        insar_z = _zscore_on_mask(insar_grad, src_mask)   # 与地形应力同尺度
        stress = np.where(src_mask, insar_z, dem_stress)
        out_mask = sm | src_mask

    result = np.full_like(dem, NODATA)
    result[out_mask] = stress[out_mask]
    return result


# ─── 变量②：氧逸度突变带强度 (Δlog fO₂) ───────────────────────
def compute_redox_gradient(aster_b01=None, aster_b03n=None,
                           s2_b02=None, s2_b04=None,
                           veg_mask: np.ndarray = None,
                           sigma: float = 3.0) -> np.ndarray:
    """
    氧逸度突变带 = 铁氧化(Fe³⁺)指数的梯度。

    多传感器铁氧化诊断比值(复用 geo-analyser 蚀变科学):
      ASTER 赤铁矿/Fe³⁺ = B3N/B1;  Sentinel-2 Fe³⁺ = B04/B02。
    高铁氧化=氧化环境;其空间突变(梯度)= 氧化还原过渡带,成矿有利。
    （P2改:由原 ASTER SWIR 经验式 |B6−mean(B5,B7)| 改为铁氧化诊断比值,
      概念与"氧逸度/fO₂"对齐;植被像元剔除。)
    """
    ref = aster_b03n if aster_b03n is not None else s2_b04
    if ref is None:
        return None
    base = np.ones(ref.shape, dtype=bool)
    if veg_mask is not None:
        base &= ~veg_mask

    terms = [
        _ratio_term(aster_b03n, aster_b01, base),   # ASTER Fe³⁺(赤铁矿)
        _ratio_term(s2_b04, s2_b02, base),          # S2 Fe³⁺
    ]
    composite, common = _composite_zsum(terms, base)
    if not np.any(common):
        return _empty_result(ref)

    grad = _gaussian_gradient_valid(composite, common, sigma=sigma)
    result = np.full(ref.shape, NODATA, dtype=np.float64)
    result[common] = grad[common]
    return result


# ─── 变量③：流体超压指数 (λ) ──────────────────────────────────
def compute_fluid_overpressure(
    lst: np.ndarray,
    s2_b04: np.ndarray,
    s2_b08: np.ndarray,
    sigma: float = 2.0,
    seasonal_lst_diff: np.ndarray = None,
) -> np.ndarray:
    """
    流体超压代理 = z(LST) + z(植被亏缺 1−NDVI) [+ z(季节热缓冲)]。

    热异常(高亮温)+ 植被胁迫(低NDVI)共同指示热液/流体活动。两项量纲不同,
    先在公共掩码上各自 Z-score 再等权相加(P1 改:去掉原"原始TIR + 3·(1−NDVI)"
    的量纲不一致与魔法常数 3.0)。LST 为窗区亮温相对代理(见 thermal.py)。

    P4 季节项(可选):ΔLST=冬−夏,|ΔLST| 小=季节热振幅被地下热缓冲→流体有利,
    故加 z(−|ΔLST|)。夏季数据缺失时该项省略(优雅降级)。
    NDVI = (B08 − B04) / (B08 + B04)
    """
    mask = (_valid_mask(lst) & _valid_mask(s2_b04) & _valid_mask(s2_b08))
    if not np.any(mask):
        return _empty_result(lst)

    b4 = np.where(mask, s2_b04.astype(np.float64), 0.0)
    b8 = np.where(mask, s2_b08.astype(np.float64), 0.0)

    ndvi = np.where(mask, (b8 - b4) / (b8 + b4 + 1e-10), 0.0)
    veg_deficit = 1.0 - ndvi

    lst_z = _zscore_on_mask(lst.astype(np.float64), mask)
    veg_z = _zscore_on_mask(veg_deficit, mask)
    fluid_raw = lst_z + veg_z

    # P4 季节热缓冲项:|ΔLST| 越小越有利(地下热缓冲)
    if seasonal_lst_diff is not None:
        sm = mask & _valid_mask(seasonal_lst_diff)
        if np.any(sm):
            damp = np.where(sm, -np.abs(seasonal_lst_diff.astype(np.float64)), 0.0)
            fluid_raw = fluid_raw + _zscore_on_mask(damp, sm)

    fluid_smooth = _gaussian_filter_valid(fluid_raw, mask, sigma=sigma)

    result = np.full_like(lst, NODATA)
    result[mask] = fluid_smooth[mask]
    return result


# ─── 变量④：控矿断裂活动性指数 (A) ────────────────────────────
def compute_fault_activity(dem: np.ndarray, stress: np.ndarray, transform,
                           pixel_size_m=(30.0, 30.0),
                           insar_velocity: np.ndarray = None) -> np.ndarray:
    """
    控矿断裂活动性 = 线性构造密度 × 地形应力(复用 geo-stru 线性体提取)。

    P3改:由原"应力梯度上硬阈值 Canny(0.05/0.25)+逐瓦片 min-max 归一(脆)"升级为
    多方位山体阴影 + 自适应分位 Canny + 概率 Hough 线段提取(structural.py),
    得到线性体密度场,再以地形应力归一加权(高应力处断裂更活跃)。
    InSAR 速度场不连续可进一步标定活动性(交付库暂无,接口保留)。
    """
    mask = _valid_mask(dem)
    if not np.any(mask):
        return _empty_result(dem)

    density = structural.fault_lineament_density(
        dem, transform, pixel_size_m, valid_mask=mask)
    dmask = _valid_mask(density)
    if not np.any(dmask):
        return _empty_result(dem)

    result = np.full_like(dem, NODATA)
    smask = _valid_mask(stress)
    # 应力归一到[0,1]作权重:高地形应力区的线性体活动性更高
    common = dmask & smask
    if np.any(common):
        s = stress[common]
        rng = float(s.max() - s.min())
        snorm = (s - s.min()) / rng if rng > 1e-10 else np.zeros_like(s)
        result[common] = density[common] * (0.5 + 0.5 * snorm)
    only_d = dmask & ~smask
    result[only_d] = density[only_d] * 0.5
    return result


# ─── 变量⑤：成矿元素化学势梯度 (∇μ) ──────────────────────────
def compute_chemical_potential(
    aster_b01=None, aster_b03n=None, aster_b05=None, aster_b06=None,
    aster_b08=None, aster_b12=None, aster_b14=None,
    s2_b02=None, s2_b04=None, s2_b11=None, s2_b12=None,
    veg_mask: np.ndarray = None,
    sigma: float = 4.0,
) -> np.ndarray:
    """
    成矿元素化学势梯度 = 多端元蚀变强度的梯度(∇μ)。

    蚀变端元诊断比值(复用 geo-analyser):
      绢云母/Al-OH  ASTER B5/B6、S2 B11/B12
      绿泥石/Mg-OH  ASTER B6/B8
      铁氧化(Fe³⁺)  ASTER B3N/B1、S2 B04/B02
      硅化/石英(TIR) ASTER B14/B12
    各端元 z-score 后等权组合成"蚀变强度",其梯度=元素迁移势梯度。
    （P2改:由原 铁(B4/B3)+黏土(B5-B7) 两项扩为多端元多传感器;植被像元剔除。)
    """
    ref = aster_b05 if aster_b05 is not None else s2_b04
    if ref is None:
        return None
    base = np.ones(ref.shape, dtype=bool)
    if veg_mask is not None:
        base &= ~veg_mask

    terms = [
        _ratio_term(aster_b05, aster_b06, base),    # 绢云母 Al-OH (ASTER)
        _ratio_term(s2_b11, s2_b12, base),          # 绢云母 Al-OH (S2)
        _ratio_term(aster_b06, aster_b08, base),    # 绿泥石 Mg-OH (ASTER)
        _ratio_term(aster_b03n, aster_b01, base),   # 铁氧化 Fe³⁺ (ASTER)
        _ratio_term(s2_b04, s2_b02, base),          # 铁氧化 Fe³⁺ (S2)
        _ratio_term(aster_b14, aster_b12, base),    # 硅化/石英 (ASTER TIR)
    ]
    composite, common = _composite_zsum(terms, base)
    if not np.any(common):
        return _empty_result(ref)

    grad = _gaussian_gradient_valid(composite, common, sigma=sigma)
    result = np.full(ref.shape, NODATA, dtype=np.float64)
    result[common] = grad[common]
    return result


# ─── 变量⑥：盖层封闭性 (ΔP) ─────────────────────────────────
def compute_cap_rock_pressure(aster_b08=None, aster_b09=None,
                              aster_b12=None, aster_b13=None, aster_b14=None,
                              veg_mask: np.ndarray = None,
                              sigma: float = 2.0) -> np.ndarray:
    """
    盖层封闭性 = 封盖矿物(碳酸盐 + 硅化)强度,平滑后作代理。

    封盖矿物诊断比值(复用 geo-analyser):
      碳酸盐 ASTER TIR B13/B14、ASTER SWIR B8/B9
      硅化/石英 ASTER TIR B14/B12
    碳酸盐胶结/硅化致密层=低渗封盖。各端元 z-score 组合后高斯平滑(静态指数,不取梯度)。
    （P2改:由原单一 (B6+B8)/B7 扩为碳酸盐(TIR+SWIR)+硅化多端元;植被像元剔除。)
    """
    ref = aster_b14 if aster_b14 is not None else aster_b08
    if ref is None:
        return None
    base = np.ones(ref.shape, dtype=bool)
    if veg_mask is not None:
        base &= ~veg_mask

    terms = [
        _ratio_term(aster_b13, aster_b14, base),    # 碳酸盐 (TIR)
        _ratio_term(aster_b08, aster_b09, base),    # 碳酸盐 (SWIR)
        _ratio_term(aster_b14, aster_b12, base),    # 硅化/石英 (TIR)
    ]
    composite, common = _composite_zsum(terms, base)
    if not np.any(common):
        return _empty_result(ref)

    cap_smooth = _gaussian_filter_valid(composite, common, sigma=sigma)
    result = np.full(ref.shape, NODATA, dtype=np.float64)
    result[common] = cap_smooth[common]
    return result


# ─── 变量⑦：温度异常梯度 (∇T) ────────────────────────────────
def compute_temperature_gradient(lst: np.ndarray,
                                 sigma: float = 5.0) -> np.ndarray:
    """
    地表热异常梯度:窗区亮温 LST 代理的梯度幅值。

    P1 改:输入由"5波段TIR原始均值"换为窗区(B13/B14)亮温LST代理(thermal.py),
    避免 B10–B12 硅酸盐发射率主导污染"温度"信号;LST 为相对亮温(绝对值不主张)。
    """
    mask = _valid_mask(lst)
    if not np.any(mask):
        return _empty_result(lst)

    grad = _gaussian_gradient_valid(lst, mask, sigma=sigma)

    result = np.full_like(lst, NODATA)
    result[mask] = grad[mask]
    return result
