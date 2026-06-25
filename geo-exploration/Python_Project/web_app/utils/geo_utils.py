"""
GeoUtils - 地理处理工具函数

严格按照 Matlab GeoUtils.m 实现
"""

import numpy as np
from typing import Tuple, Optional, Callable, Dict
import os
import glob
from shapely.geometry import Polygon, Point


def calculate_s2rep_from_dn(B4: np.ndarray, B5: np.ndarray, B6: np.ndarray, B7: np.ndarray,
                           scale_factors: np.ndarray, offsets: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    计算 S2REP (Sentinel-2 Red Edge Position)

    严格按照 Matlab GeoUtils.calculate_S2REP_from_DN() 逻辑

    Args:
        B4: Red 波段 (Band 3)
        B5: Red Edge 1 波段 (Band 7)
        B6: Red Edge 2 波段 (Band 8)
        B7: NIR 波段 (Band 9)
        scale_factors: 尺度因子 [1.9997e-05, 1.9998e-05, 1.9998e-05, 1.9999e-05]
        offsets: 偏移量 [-0.1, -0.1, -0.1, -0.1]

    Returns:
        (S2REP, REP_QA): 红边位置和质量标志
            S2REP: 红边位置矩阵
            REP_QA: 质量标志 (1=有效, 2=零分母, 3=无效反射率, 4=超出范围)
    """
    # 转换为反射率
    B4_val = B4 * 10000.0 * scale_factors[0] + offsets[0]
    B5_val = B5 * 10000.0 * scale_factors[1] + offsets[1]
    B6_val = B6 * 10000.0 * scale_factors[2] + offsets[2]
    B7_val = B7 * 10000.0 * scale_factors[3] + offsets[3]

    # 检查无效反射率 (0-1 范围外或 NaN)
    invalid_reflect = (
        (B4_val < 0) | (B4_val > 1) |
        (B5_val < 0) | (B5_val > 1) |
        (B6_val < 0) | (B6_val > 1) |
        (B7_val < 0) | (B7_val > 1) |
        np.isnan(B4_val) | np.isnan(B5_val) | np.isnan(B6_val) | np.isnan(B7_val)
    )

    H, W = B4.shape
    S2REP = np.full((H, W), np.nan, dtype=np.float32)
    REP_QA = np.zeros((H, W), dtype=np.uint8)

    # 无效像素标记为 3
    REP_QA[invalid_reflect] = 3
    valid_pixel = ~invalid_reflect

    # 计算 S2REP
    numerator = ((B4_val + B7_val) / 2.0) - B5_val
    denominator = (B6_val - B5_val) + 1e-8

    # 零分母检测
    zero_denominator = valid_pixel & (np.abs(denominator) < 1e-6)
    REP_QA[zero_denominator] = 2
    valid_pixel[zero_denominator] = False

    # 计算 S2REP
    S2REP[valid_pixel] = 705.0 + 35.0 * (numerator[valid_pixel] / denominator[valid_pixel])

    # 超出范围检测 (680-760 nm)
    rep_out_range = valid_pixel & ((S2REP < 680) | (S2REP > 760))
    REP_QA[rep_out_range] = 4
    S2REP[rep_out_range] = np.nan

    # 有效像素标记为 1
    REP_QA[valid_pixel & ~rep_out_range] = 1

    return S2REP, REP_QA


def calc_local_sum_with_nan(Z: np.ndarray) -> np.ndarray:
    """
    计算局部邻域和 (忽略 NaN)

    严格按照 Matlab GeoUtils.calc_local_sum_with_nan() 逻辑

    Args:
        Z: 输入矩阵

    Returns:
        local_sum: 局部和矩阵
    """
    rows, cols = Z.shape
    pad = 1
    Z_padded = np.pad(Z, pad, mode='constant', constant_values=np.nan)
    local_sum = np.full((rows, cols), np.nan, dtype=np.float64)

    # 3x3 模板，不包含中心点
    w = np.ones((3, 3), dtype=np.float64)
    w[1, 1] = 0.0

    for i in range(rows):
        for j in range(cols):
            if not np.isnan(Z[i, j]):
                neigh = Z_padded[i:i+3, j:j+3]
                mask = ~np.isnan(neigh)

                if np.any(mask):
                    w_mask = w * mask
                    w_sum = np.sum(w_mask)

                    if w_sum > 0:
                        w_mask = w_mask / w_sum
                        local_sum[i, j] = np.nansum(neigh * w_mask)
                    else:
                        local_sum[i, j] = 0.0
                else:
                    local_sum[i, j] = 0.0

    # 处理无穷大
    local_sum[np.isinf(local_sum)] = np.nan

    return local_sum


def mat2gray_roi(img: np.ndarray, inROI: np.ndarray,
                 min_val: Optional[float] = None,
                 max_val: Optional[float] = None) -> np.ndarray:
    """
    ROI 内归一化到 [0, 1]

    严格按照 Matlab GeoUtils.mat2gray_roi() 逻辑

    Args:
        img: 输入图像
        inROI: ROI 掩码
        min_val: 最小值 (可选，默认 ROI 内最小值)
        max_val: 最大值 (可选，默认 ROI 内最大值)

    Returns:
        img_norm: 归一化后的图像
    """
    img_norm = np.full(img.shape, np.nan, dtype=np.float64)

    # 获取 ROI 内有效像素
    img_roi = img[inROI]
    img_roi = img_roi[~np.isnan(img_roi) & ~np.isinf(img_roi)]

    if len(img_roi) == 0:
        return img_norm

    if min_val is None:
        min_val = np.min(img_roi)
    if max_val is None:
        max_val = np.max(img_roi)

    if max_val - min_val < 1e-10:
        img_norm[inROI] = 0.5
    else:
        val = (img[inROI] - min_val) / (max_val - min_val)
        val[val < 0] = 0
        val[val > 1] = 1
        img_norm[inROI] = val

    return img_norm


def compute_intrinsic_absorption(ast: np.ndarray, mineral_type: str) -> np.ndarray:
    """
    计算本征吸收强度

    严格按照 Matlab GeoUtils.computeIntrinsicAbsorption() 逻辑

    Args:
        ast: ASTER 数据 (H, W, 14)
        mineral_type: 矿物类型

    Returns:
        F_abs: 本征吸收强度
    """
    eps_val = 1e-6
    H, W = ast.shape[:2]
    F_abs = np.full((H, W), np.nan, dtype=np.float64)

    mineral_type = mineral_type.lower()

    if mineral_type == 'gold':
        # 黄铁矿(Fe-S:0.8-0.9um) + Al-OH(2.2um)
        cont = (ast[:, :, 2] + ast[:, :, 4]) / 2  # B3(0.81um) + B5(2.1um)
        target = ast[:, :, 2]  # B3(0.81um) Fe-S吸收
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.5 * (ast[:, :, 5] / (ast[:, :, 4] + eps_val))  # B6/B5 补充Al-OH吸收

    elif mineral_type == 'copper':
        # Cu²⁺(0.8-0.9um) + OH(2.2um)
        cont = (ast[:, :, 2] + ast[:, :, 4]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.5 * (ast[:, :, 5] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'iron':
        # 铁氧化物(0.8um)
        cont = (ast[:, :, 1] + ast[:, :, 3]) / 2  # B2(0.66um) + B4(1.6um)
        target = ast[:, :, 2]  # B3(0.81um)
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'coal':
        # 有机质(2.3um)
        cont = (ast[:, :, 4] + ast[:, :, 7]) / 2  # B5(2.1um) + B8(2.5um)
        target = ast[:, :, 6]  # B7(2.3um)
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'rare_earth':
        # REE电子跃迁(2.2um)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2  # B5(2.1um) + B7(2.3um)
        target = ast[:, :, 5]  # B6(2.2um)
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'silver':
        # 方铅矿(Pb-S:1.0um) + OH(2.2um)
        cont = (ast[:, :, 2] + ast[:, :, 3]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.4 * (ast[:, :, 5] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'aluminum':
        # 高岭石(Al-OH:2.2um)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2
        target = ast[:, :, 5]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.3 * (ast[:, :, 3] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'lead':
        # 方铅矿(Pb-S:1.0um)
        cont = (ast[:, :, 2] + ast[:, :, 3]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'zinc':
        # 闪锌矿(Zn-Fe:0.9-1.1um)
        cont = (ast[:, :, 2] + ast[:, :, 3]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.2 * (ast[:, :, 5] / (ast[:, :, 6] + eps_val))

    elif mineral_type == 'nickel':
        # 硅镁镍矿(Ni-OH/Mg-OH:1.8-2.3um)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2
        target = ast[:, :, 5]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.3 * (ast[:, :, 3] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'cobalt':
        # 异极矿(Co²⁺:0.5-0.6um)
        cont = (ast[:, :, 0] + ast[:, :, 1]) / 2  # B1(0.56um) + B2(0.66um)
        target = ast[:, :, 0]
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'molybdenum':
        # 辉钼矿(Fe相关:0.9um)
        cont = (ast[:, :, 1] + ast[:, :, 2]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'fluorite':
        # 萤石(弱OH:1.4um，ASTER无1.4um用2.2um替代)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2
        target = ast[:, :, 5]
        F_abs = (cont - target) / (cont + eps_val) * 0.5

    elif mineral_type == 'tin':
        # 锡石(Sn-Fe:1.0um + OH:2.2um)
        cont = (ast[:, :, 2] + ast[:, :, 4]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.4 * (ast[:, :, 5] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'tungsten':
        # 黑钨矿(W-Fe:0.9-1.0um)
        cont = (ast[:, :, 1] + ast[:, :, 2]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'petroleum':
        # 石油(C-H:1.7-1.75um + 2.3-2.5um)
        cont = (ast[:, :, 3] + ast[:, :, 7]) / 2  # B4(1.6um) + B8(2.5um)
        target = ast[:, :, 6]  # B7(2.3um)
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.3 * (ast[:, :, 3] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'gas':
        # 天然气(CH₄:1.65-1.7um + 2.3um)
        cont = (ast[:, :, 3] + ast[:, :, 6]) / 2
        target = ast[:, :, 3]
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'coalbed_gas':
        # 煤层气(C-H:1.7um + 2.3um)
        cont = (ast[:, :, 4] + ast[:, :, 7]) / 2
        target = ast[:, :, 6]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.2 * (ast[:, :, 3] / (ast[:, :, 4] + eps_val))

    elif mineral_type == 'helium':
        # 氦气(无直接吸收，用围岩OH替代)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2
        target = ast[:, :, 5]
        F_abs = (cont - target) / (cont + eps_val) * 0.2

    elif mineral_type == 'lithium':
        # 锂辉石(Li-OH/Al-OH:2.2-2.4um)
        cont = (ast[:, :, 5] + ast[:, :, 7]) / 2
        target = ast[:, :, 6]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.3 * (ast[:, :, 5] / (ast[:, :, 6] + eps_val))

    elif mineral_type == 'natural_hydrogen':
        # 自然氢气(无直接吸收，用围岩Fe替代)
        cont = (ast[:, :, 1] + ast[:, :, 2]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val) * 0.2

    elif mineral_type == 'potassium':
        # 钾长石(弱OH:1.4um，用2.2um替代)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2
        target = ast[:, :, 5]
        F_abs = (cont - target) / (cont + eps_val) * 0.3

    elif mineral_type == 'uranium':
        # 沥青铀矿(U⁴⁺/U⁶⁺:0.8-1.0um)
        cont = (ast[:, :, 1] + ast[:, :, 3]) / 2
        target = ast[:, :, 2]
        F_abs = (cont - target) / (cont + eps_val)

    elif mineral_type == 'cave':
        # 洞穴(无矿物吸收)
        F_abs = np.full((H, W), np.nan, dtype=np.float32)

    elif mineral_type == 'offshore_petroleum':
        # 海底石油
        cont = (ast[:, :, 3] + ast[:, :, 7]) / 2
        target = ast[:, :, 6]
        F_abs = (cont - target) / (cont + eps_val)
        F_abs = F_abs + 0.4 * (ast[:, :, 7] / (ast[:, :, 6] + eps_val))

    else:
        # 通用模式：OH吸收(2.2um)
        cont = (ast[:, :, 4] + ast[:, :, 6]) / 2
        target = ast[:, :, 5]
        F_abs = (cont - target) / (cont + eps_val)

    # 处理无穷大
    F_abs[np.isinf(F_abs)] = np.nan

    return F_abs


def get_mineral_thresholds(mineral_type: str) -> Tuple[float, float, float, Optional[Callable]]:
    """
    获取矿物阈值和增强函数

    严格按照 Matlab GeoUtils.getMineralThresholds() 逻辑

    Args:
        mineral_type: 矿物类型

    Returns:
        (F_thr, delta_thr, Moran_thr, enh_func):
            F_thr: F_map 阈值
            delta_thr: 红边偏移阈值
            Moran_thr: Moran I 阈值
            enh_func: 增强函数
    """
    mineral_type = mineral_type.lower()

    # 默认增强函数 (金矿)
    def _default_enh(Ferric, Fe_anomaly, Hydroxy_anomaly, Clay, NDVI_inv):
        return 0.45*Ferric + 0.25*Fe_anomaly + 0.15*Hydroxy_anomaly + 0.10*Clay + 0.05*NDVI_inv

    # 矿物阈值表 + 增强函数 (严格按照 Matlab GeoUtils.getMineralThresholds)
    thresholds = {
        'gold': (0.018, -2, 0.20, lambda F,Fe,H,C,N: 0.45*F + 0.25*Fe + 0.15*H + 0.10*C + 0.05*N),
        'copper': (0.020, -3, 0.25, lambda F,Fe,H,C,N: 0.40*C + 0.30*H + 0.20*F + 0.10*Fe),
        'iron': (0.030, -4, 0.35, lambda F,Fe,H,C,N: 0.60*F + 0.40*Fe),
        'lead': (0.025, -3, 0.30, lambda F,Fe,H,C,N: 0.40*H + 0.30*C + 0.30*F),
        'zinc': (0.024, -3, 0.28, lambda F,Fe,H,C,N: 0.40*H + 0.30*C + 0.30*F),
        'molybdenum': (0.028, -4, 0.32, lambda F,Fe,H,C,N: 0.50*H + 0.30*C + 0.20*F),
        'copper_gold': (0.019, -2.5, 0.22, lambda F,Fe,H,C,N: 0.40*F + 0.40*C + 0.20*H),
        'coal': (0.032, -4.5, 0.38, lambda F,Fe,H,C,N: 0.60*N + 0.40*H),
        'tin': (0.023, -2.5, 0.26, lambda F,Fe,H,C,N: 0.50*H + 0.30*C + 0.20*F),
        'petroleum': (0.035, -5, 0.40, lambda F,Fe,H,C,N: 0.70*N + 0.30*H),
        'gas': (0.033, -4.5, 0.38, lambda F,Fe,H,C,N: 0.60*N + 0.40*H),
        'lithium': (0.022, -2, 0.25, lambda F,Fe,H,C,N: 0.60*C + 0.40*H),
        'nickel': (0.026, -3.5, 0.30, lambda F,Fe,H,C,N: 0.50*H + 0.30*F + 0.20*C),
        'fluorite': (0.029, -4, 0.35, lambda F,Fe,H,C,N: 0.50*H + 0.30*C + 0.20*F),
        'phosphate': (0.027, -3.5, 0.32, lambda F,Fe,H,C,N: 0.40*H + 0.30*C + 0.30*N),
        'rare_earth': (0.026, -3, 0.28, lambda F,Fe,H,C,N: 0.40*H + 0.30*C + 0.20*F + 0.10*N),
        'helium': (0.031, -4, 0.36, lambda F,Fe,H,C,N: 0.50*N + 0.30*H + 0.20*C),
        'uranium': (0.028, -4.5, 0.32, lambda F,Fe,H,C,N: 0.40*Fe + 0.30*F + 0.20*N + 0.10*H),
        'natural_hydrogen': (0.032, -4, 0.37, lambda F,Fe,H,C,N: 0.50*N + 0.30*C + 0.20*H),
        'potassium': (0.025, -3, 0.28, lambda F,Fe,H,C,N: 0.40*H + 0.30*C + 0.20*F + 0.10*N),
        'cave': (0.025, -3, 0.30, None),  # cave模式需要slope和curvature，特殊处理
        'offshore_petroleum': (0.030, -4, 0.35, None),  # 需要OSI和SDS，特殊处理
        'silver': (0.019, -2, 0.22, lambda F,Fe,H,C,N: 0.45*F + 0.25*Fe + 0.15*H + 0.10*C + 0.05*N),
        'aluminum': (0.020, -2, 0.23, lambda F,Fe,H,C,N: 0.45*F + 0.25*Fe + 0.15*H + 0.10*C + 0.05*N),
        'cobalt': (0.021, -2, 0.24, lambda F,Fe,H,C,N: 0.45*F + 0.25*Fe + 0.15*H + 0.10*C + 0.05*N),
        'tungsten': (0.022, -2, 0.25, lambda F,Fe,H,C,N: 0.45*F + 0.25*Fe + 0.15*H + 0.10*C + 0.05*N),
        'coalbed_gas': (0.032, -4, 0.38, lambda F,Fe,H,C,N: 0.60*N + 0.40*H),
    }

    if mineral_type in thresholds:
        return thresholds[mineral_type]
    else:
        print(f"警告: 未知矿种 '{mineral_type}'，使用默认金矿阈值")
        return (0.018, -2, 0.20, _default_enh)


def get_yakymchuk_params(mineral_type: str) -> Dict:
    """
    获取 Yakymchuk 共振参数

    严格按照 Matlab GeoUtils.getYakymchukParams() 逻辑
    """
    mineral_type = mineral_type.lower()
    param_table = {
        'gold':             {'a': 50, 'b': 150, 'c': 20},
        'silver':           {'a': 45, 'b': 135, 'c': 19},
        'copper':           {'a': 40, 'b': 120, 'c': 18},
        'iron':             {'a': 35, 'b': 100, 'c': 15},
        'aluminum':         {'a': 48, 'b': 140, 'c': 19},
        'coal':             {'a': 32, 'b': 80,  'c': 16},
        'lead':             {'a': 42, 'b': 125, 'c': 18},
        'zinc':             {'a': 42, 'b': 125, 'c': 18},
        'nickel':           {'a': 35, 'b': 105, 'c': 16},
        'cobalt':           {'a': 38, 'b': 115, 'c': 17},
        'molybdenum':       {'a': 48, 'b': 140, 'c': 20},
        'rare_earth':       {'a': 45, 'b': 140, 'c': 18},
        'fluorite':         {'a': 55, 'b': 170, 'c': 22},
        'tin':              {'a': 52, 'b': 155, 'c': 21},
        'tungsten':         {'a': 52, 'b': 155, 'c': 21},
        'petroleum':        {'a': 30, 'b': 70,  'c': 15},
        'gas':              {'a': 28, 'b': 75,  'c': 14},
        'coalbed_gas':      {'a': 32, 'b': 80,  'c': 16},
        'helium':           {'a': 25, 'b': 85,  'c': 14},
        'lithium':          {'a': 40, 'b': 110, 'c': 17},
        'natural_hydrogen': {'a': 30, 'b': 80,  'c': 15},
        'potassium':        {'a': 45, 'b': 135, 'c': 19},
        'uranium':          {'a': 40, 'b': 130, 'c': 19},
        'cave':             {'a': 40, 'b': 120, 'c': 18},
    }

    if mineral_type in param_table:
        return param_table[mineral_type]
    else:
        print(f"警告: 未知矿种 '{mineral_type}'，使用默认金矿参数")
        return {'a': 50, 'b': 150, 'c': 20}


# ====================== 数据加载函数 ======================

def read_sentinel2(data_dir: str, target_size: tuple = None) -> Tuple[np.ndarray, dict, str]:
    """
    读取 Sentinel-2 数据，严格按照 Matlab readSentinel2 逻辑

    Args:
        data_dir: 数据目录路径
        target_size: 可选目标网格尺寸 (H, W)，用于匹配低分辨率参考

    Returns:
        (s2, R, ref_tif_path):
            s2: Sentinel-2 数据 (H, W, 10) - float32
            R: 地理参考信息 (类似于 Matlab 的 R 结构)
            ref_tif_path: 参考影像文件路径
    """
    import rasterio
    # 查找 Sentinel-2 目录
    s2_dirs = glob.glob(os.path.join(data_dir, '*', 'Sentinel*2 L2*'))
    if not s2_dirs:
        s2_dirs = glob.glob(os.path.join(data_dir, 'Sentinel*2 L2*'))
    if not s2_dirs:
        s2_dirs = glob.glob(os.path.join(data_dir, 'Sentinel*2*L2*'))
    if not s2_dirs:
        s2_dirs = [data_dir]  # 如果是直接目录，尝试直接使用

    if not s2_dirs or not os.path.exists(s2_dirs[0]):
        # 检查是否是扁平结构（直接包含波段文件）
        flat_files = glob.glob(os.path.join(data_dir, 'B0*.tif')) + \
                     glob.glob(os.path.join(data_dir, 'B0*.tiff'))
        if flat_files:
            s2_dir = data_dir
        else:
            raise ValueError(f"未找到Sentinel-2 L2A文件目录: {data_dir}")

    s2_dir = s2_dirs[0]

    s2_dir = s2_dirs[0]

    # 查找文件
    files = glob.glob(os.path.join(s2_dir, '*B08*.tif')) + \
            glob.glob(os.path.join(s2_dir, '*B08*.tiff')) + \
            glob.glob(os.path.join(s2_dir, '*.jp2'))

    if not files:
        raise ValueError(f"未找到Sentinel-2文件: {s2_dir}")

    # 使用第一个文件作为参考
    ref_tif_path = files[0]

    # 读取参考影像
    with rasterio.open(ref_tif_path) as src:
        R = {
            'RasterSize': target_size if target_size else src.shape,
            'LongitudeLimits': (src.bounds.left, src.bounds.right),
            'LatitudeLimits': (src.bounds.bottom, src.bounds.top),
            'transform': src.transform,
            'crs': src.crs,  # 参考影像坐标系(可能是 UTM 等投影坐标系,非经纬度)
        }

        # Sentinel-2 波段映射 (Matlab: B4=Band 3, B5=Band 7, B6=Band 8, B7=Band 9)
        # 0-based index: B02, B03, B04, B08, B11, B12, B05, B06, B07
        s2_patterns = [
            ('B02',), ('B03',), ('B04',), ('B08',), ('B11',), ('B12',), ('B05',), ('B06',), ('B07',)
        ]

        s2_raw = _read_multi_bands_smart(s2_dir, s2_patterns, R, 9)

        # Sentinel-2 数据乘以 0.0001 转换为反射率
        s2 = s2_raw * 0.0001

    return s2, R, ref_tif_path


def read_landsat8(data_dir: str, R: dict) -> np.ndarray:
    """
    读取 Landsat-8 数据，严格按照 Matlab readLandsat8 逻辑

    Args:
        data_dir: 数据目录路径
        R: 地理参考信息（用于重采样）

    Returns:
        lan: Landsat-8 数据 (H, W, 7) - float32
    """
    # 查找 Landsat-8 目录
    lan_l1 = glob.glob(os.path.join(data_dir, '*', 'Landsat*8*L1*'))
    lan_l2 = glob.glob(os.path.join(data_dir, '*', 'Landsat*8*L2*'))
    if not lan_l1 and not lan_l2:
        lan_l1 = glob.glob(os.path.join(data_dir, '*Landsat*8*L1*'))
        lan_l2 = glob.glob(os.path.join(data_dir, '*Landsat*8*L2*'))

    if lan_l1:
        lan_dir = lan_l1[0]
    elif lan_l2:
        lan_dir = lan_l2[0]
    else:
        # 检查是否是扁平结构
        flat_files = glob.glob(os.path.join(data_dir, 'B*.tif')) + \
                     glob.glob(os.path.join(data_dir, 'B*.tiff'))
        if flat_files:
            lan_dir = data_dir
        else:
            raise ValueError(f"未找到Landsat 8数据目录: {data_dir}")

    # Landsat-8 波段映射 (B2, B3, B4, B5, B6, B7, B8)
    lan_patterns = [
        ('B2',), ('B3',), ('B4',), ('B5',), ('B6',), ('B7',), ('B8',)
    ]

    lan = _read_multi_bands_smart(lan_dir, lan_patterns, R, 7)

    return lan


def read_aster(data_dir: str, R: dict) -> np.ndarray:
    """
    读取 ASTER 数据，严格按照 Matlab readASTER 逻辑

    Args:
        data_dir: 数据目录路径
        R: 地理参考信息（用于重采样）

    Returns:
        ast: ASTER 数据 (H, W, 14) - float32
    """
    # 查找 ASTER 目录
    aster_dirs = glob.glob(os.path.join(data_dir, '*', '*ASTER*L2*')) + \
                 glob.glob(os.path.join(data_dir, '*', '*ASTER*L1*'))
    if not aster_dirs:
        aster_dirs = glob.glob(os.path.join(data_dir, '*ASTER*L2*')) + \
                     glob.glob(os.path.join(data_dir, '*ASTER*L1*'))

    if not aster_dirs:
        # 检查是否是扁平结构
        flat_files = glob.glob(os.path.join(data_dir, 'B0*.tif')) + \
                     glob.glob(os.path.join(data_dir, 'B0*.tiff'))
        if flat_files:
            aster_dir = data_dir
        else:
            raise ValueError(f"未找到ASTER数据目录: {data_dir}")

    aster_dir = aster_dirs[0]

    # ASTER 波段映射
    aster_pat = [
        ('B01', 'B1'), ('B02', 'B2'), ('B3N', 'B03N'),
        ('B04', 'B4'), ('B05', 'B5'), ('B06', 'B6'),
        ('B07', 'B7'), ('B08', 'B8'), ('B09',), ('B10',),
        ('B11',), ('B12',), ('B13',), ('B14',)
    ]

    H, W = R['RasterSize']
    ast = np.full((H, W, 14), np.nan, dtype=np.float32)

    for b in range(14):
        single_band = _read_any_smart(aster_dir, aster_pat[b], R)

        if b <= 9:  # B01-B09
            single_band = single_band * 0.01
            single_band[np.isinf(single_band)] = np.nan
        else:  # B10-B14
            single_band = single_band * 0.1 + 300
            single_band[np.isinf(single_band)] = 300

        ast[:, :, b] = single_band

    return ast


def read_dem_and_roi(data_dir: str, roi_file: str, R: dict) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    读取 DEM 和 ROI，严格按照 Matlab readDEMandROI 逻辑

    Args:
        data_dir: 数据目录路径
        roi_file: ROI 文件路径
        R: 地理参考信息

    Returns:
        (dem, inROI, lonGrid, latGrid, lonROI, latROI)
    """
    H, W = R['RasterSize']
    print(f"DEBUG: Image dimensions H={H}, W={W}")
    print(f"DEBUG: R['RasterSize']={R['RasterSize']}")

    # 读取 ROI 文件
    try:
        roi_data = read_roi_robust(roi_file)
        if not roi_data:
            raise ValueError(f"ROI 文件解析失败: {roi_file}")

        roiPoly = roi_data['roi_poly']
        lonROI = roi_data['lon_roi']
        latROI = roi_data['lat_roi']

        if len(lonROI) == 0 or len(latROI) == 0:
            raise ValueError(f"ROI 文件中没有有效坐标: {roi_file}")
    except Exception as e:
        print(f"ROI 读取错误: {e}")
        # 创建默认的 ROI
        lonROI = np.array([0.0])
        latROI = np.array([0.0])
        roiPoly = np.array([[0.0, 0.0]])

    print(f"DEBUG: ROI loaded: {len(lonROI)} points")

    import rasterio
    from rasterio.warp import reproject
    from rasterio.enums import Resampling

    # 生成 lonGrid 和 latGrid(先在参考影像原生坐标系下建网格)
    lonVec = np.linspace(R['LongitudeLimits'][0], R['LongitudeLimits'][1], W)
    latVec = np.linspace(R['LatitudeLimits'][0], R['LatitudeLimits'][1], H)
    lonGrid, latGrid = np.meshgrid(lonVec, latVec)

    # 若参考影像是投影坐标系(如 Sentinel-2 的 UTM/EPSG:326xx),原生网格是米而非度,
    # 与经纬度 ROI 不在同一坐标系。这里把网格重投影到 EPSG:4326,使掩码判断与所有
    # 下游地理输出(靶点经纬度/KMZ/metadata bbox)都正确。地理坐标系则原样不动(零回归)。
    crs = R.get('crs')
    try:
        import rasterio as _rio
        is_geographic = (crs is None) or _rio.crs.CRS.from_user_input(crs).is_geographic
    except Exception:
        is_geographic = True
    if not is_geographic:
        from pyproj import Transformer
        _tr = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
        _lon = np.empty((H, W), dtype=np.float64)
        _lat = np.empty((H, W), dtype=np.float64)
        _chunk = 512  # 分块重投影,控制 1.2 亿点的瞬时内存峰值
        for _r0 in range(0, H, _chunk):
            _r1 = min(_r0 + _chunk, H)
            _lo, _la = _tr.transform(lonGrid[_r0:_r1], latGrid[_r0:_r1])
            _lon[_r0:_r1] = _lo
            _lat[_r0:_r1] = _la
        lonGrid, latGrid = _lon, _lat
        print(f"DEBUG: 参考影像为投影坐标系 {crs},网格已重投影到 EPSG:4326 "
              f"(lon[{lonGrid.min():.4f},{lonGrid.max():.4f}] "
              f"lat[{latGrid.min():.4f},{latGrid.max():.4f}])")

    print(f"DEBUG: lonGrid.shape={lonGrid.shape}, latGrid.shape={latGrid.shape}")
    print(f"DEBUG: lonROI len={len(lonROI)}, latROI len={len(latROI)}")

    # 计算 inROI 掩码（判断网格点是否在多边形内部）
    # 向量化点-多边形判断：先用 ROI 经纬度包络框预筛候选点，再对候选点批量判断。
    # ROI 通常仅占整幅瓦片极小一块，避免在 H*W(≈1.2 亿像素)上逐点调 shapely。
    # 确保 lonROI 和 latROI 大小一致
    min_len = min(len(lonROI), len(latROI))
    if min_len < 3:
        print(f"警告: ROI 点数不足，只有 {min_len} 个点")

    from matplotlib.path import Path as _MplPath
    poly_xy = np.column_stack([lonROI[:min_len], latROI[:min_len]])
    roi_path = _MplPath(poly_xy)

    # bbox 预筛：只在 ROI 经纬度范围内的网格点上做点-多边形判断
    lon_min, lon_max = poly_xy[:, 0].min(), poly_xy[:, 0].max()
    lat_min, lat_max = poly_xy[:, 1].min(), poly_xy[:, 1].max()
    bbox_mask = ((lonGrid >= lon_min) & (lonGrid <= lon_max) &
                 (latGrid >= lat_min) & (latGrid <= lat_max))

    inROI_grid = np.zeros((H, W), dtype=bool)
    if bbox_mask.any():
        cand_pts = np.column_stack([lonGrid[bbox_mask], latGrid[bbox_mask]])
        inROI_grid[bbox_mask] = roi_path.contains_points(cand_pts)
    print(f"DEBUG: inROI 候选点 {int(bbox_mask.sum())}, 命中 {int(inROI_grid.sum())}")

    inROI = np.flipud(inROI_grid)

    # 读取 DEM
    dem_files = glob.glob(os.path.join(data_dir, 'DEM.tif')) + \
                glob.glob(os.path.join(data_dir, 'DEM.tiff'))

    if dem_files:
        dem_file = dem_files[0]
        with rasterio.open(dem_file) as src:
            dem_raw = src.read(1)
            dem = dem_raw.astype(np.float32)
            dem[np.isinf(dem)] = np.nan

            # 如果尺寸不匹配，进行重采样
            if dem.shape != (H, W):
                # 创建目标坐标
                dst_transform = rasterio.Affine(
                    (R['LongitudeLimits'][1] - R['LongitudeLimits'][0]) / W, 0, R['LongitudeLimits'][0],
                    0, (R['LatitudeLimits'][0] - R['LatitudeLimits'][1]) / H, R['LatitudeLimits'][1]
                )

                dem_resampled = np.full((H, W), np.nan, dtype=np.float32)
                reproject(
                    source=dem,
                    destination=dem_resampled,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=src.crs,  # 使用相同的坐标系
                    resampling=Resampling.bilinear
                )
                dem = dem_resampled
    else:
        dem = np.full((H, W), np.nan, dtype=np.float32)

    return dem, inROI, lonGrid, latGrid, lonROI, latROI


def _parse_kml_coordinates(filepath: str):
    """
    从 KML/OVKML 文件(均为 OGC KML XML)中提取 ROI 经纬度。

    KML 的 <coordinates> 文本是空白分隔的 "经度,纬度[,高程]" 元组序列，
    例如: "116.3,39.9,0 116.4,39.9,0 116.4,40.0,0"。

    Args:
        filepath: .kml / .ovkml 文件路径

    Returns:
        (lon_arr, lat_arr): 经度、纬度 np.ndarray
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(filepath).getroot()
    except ET.ParseError as e:
        raise ValueError(f"KML/OVKML 解析失败，文件不是合法的 XML: {e}")

    lon_list, lat_list = [], []
    # 命名空间无关：按本地标签名(去掉 {ns} 前缀)匹配 <coordinates>
    for elem in root.iter():
        if elem.tag.rsplit('}', 1)[-1] != 'coordinates' or not elem.text:
            continue
        for tuple_str in elem.text.split():
            parts = tuple_str.split(',')
            if len(parts) < 2:
                continue
            try:
                lon_list.append(float(parts[0]))
                lat_list.append(float(parts[1]))
            except ValueError:
                continue

    if len(lon_list) < 3:
        raise ValueError("KML/OVKML 文件中未找到有效的 ROI 坐标"
                         "（<coordinates> 标签缺失或点数不足 3 个）")

    return np.asarray(lon_list, dtype=float), np.asarray(lat_list, dtype=float)


def read_roi_robust(filepath: str) -> dict:
    """
    智能读取ROI坐标文件

    Args:
        filepath: ROI文件路径

    Returns:
        roi_data: {
            'roi_poly': ROI 多边形坐标,
            'lon_roi': 经度数组,
            'lat_roi': 纬度数组
        }
    """
    import pandas as pd

    # 如果 filepath 是目录，查找其中的文件
    if os.path.isdir(filepath):
        files = os.listdir(filepath)
        # 查找可能的 Excel 或 CSV 文件
        for file in files:
            file_path = os.path.join(filepath, file)
            if os.path.isfile(file_path):
                filepath = file_path
                break

    # 文件扩展名判断
    ext = os.path.splitext(filepath)[1].lower()

    raw = None  # 初始化 raw 变量

    if ext in ('.kml', '.ovkml'):
        # KML/OVKML(OGC KML XML)：直接从 <coordinates> 标签读取经纬度，
        # 不走下面的表格(经纬度列自动识别)逻辑。
        lon_roi, lat_roi = _parse_kml_coordinates(filepath)
        roi_poly = np.column_stack([lon_roi, lat_roi])
        if len(roi_poly) > 1 and not np.array_equal(roi_poly[0], roi_poly[-1]):
            roi_poly = np.vstack([roi_poly, roi_poly[0]])
        return {
            'roi_poly': roi_poly,
            'lon_roi': lon_roi,
            'lat_roi': lat_roi
        }

    if ext in ('.xlsx', '.xls'):
        # 读取 Excel 文件
        raw = pd.read_excel(filepath, header=None)
    elif ext == '.csv':
        # 读取 CSV 文件
        for encoding in ['utf-8', 'gbk', 'gb2312', 'latin1']:
            try:
                raw = pd.read_csv(filepath, header=None, encoding=encoding)
                break
            except:
                continue
        else:
            raise ValueError("无法读取CSV文件")
    else:
        # 尝试检测文件类型
        header = None
        try:
            with open(filepath, 'rb') as f:
                header = f.read(8)
            if header and header.startswith(b'PK\x03\x04'):
                # ZIP 文件
                import zipfile
                import tempfile

                with tempfile.TemporaryDirectory() as temp_dir:
                    with zipfile.ZipFile(filepath, 'r') as zip_ref:
                        zip_ref.extractall(temp_dir)

                    # 查找 Excel 或 CSV 文件
                    for file in os.listdir(temp_dir):
                        if file.lower().endswith(('.xlsx', '.xls', '.csv')):
                            new_filepath = os.path.join(temp_dir, file)
                            return read_roi_robust(new_filepath)
        except:
            pass

        # 如果不是 ZIP 或读取失败，尝试直接读取
        try:
            raw = pd.read_excel(filepath, header=None)
        except:
            # 如果 Excel 读取失败，尝试 CSV
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin1']:
                try:
                    raw = pd.read_csv(filepath, header=None, encoding=encoding)
                    break
                except:
                    continue
            else:
                raise ValueError(f"无法读取文件: {filepath}")

    if raw is None:
        raise ValueError(f"无法读取文件: {filepath}")

    # 检测经纬度列
    raw_data = raw.values
    lon_col, lat_col = None, None

    # 如果第一行有标题，跳过
    first_row_text = any(
        isinstance(raw.iloc[0, c], str) and
        any(kw in str(raw.iloc[0, c]) for kw in ['经度', '纬度', 'longitude', 'latitude', '经度（W°）', '纬度（N°）'])
        for c in range(raw.shape[1])
    )

    if first_row_text:
        raw_data = raw.iloc[1:].values

    # 转换为数值
    numeric_data = pd.DataFrame(raw_data).apply(pd.to_numeric, errors='coerce')
    numeric_data = numeric_data.dropna(how='all', axis=1)

    if len(numeric_data) < 2:
        raise ValueError("文件中没有有效的数值数据")

    # 检测经纬度列
    for c in range(numeric_data.shape[1]):
        col = numeric_data.iloc[:, c].dropna()
        if len(col) < 3:
            continue

        mean_val = col.mean()
        range_val = col.max() - col.min()

        # 经度特征：60-160度，范围<20
        if 60 <= mean_val <= 160 and range_val < 20 and lon_col is None:
            lon_col = c
        # 纬度特征：0-60度，范围<20
        elif 0 <= mean_val <= 60 and range_val < 20 and lat_col is None:
            lat_col = c

    # 回退策略
    if lon_col is None and len(numeric_data.columns) >= 2:
        lon_col = 0
    if lat_col is None and len(numeric_data.columns) >= 2:
        lat_col = 1

    if lon_col is None or lat_col is None:
        raise ValueError("无法检测到经纬度列")

    lon_roi = numeric_data.iloc[:, lon_col].dropna().values
    lat_roi = numeric_data.iloc[:, lat_col].dropna().values

    # 确保坐标形成闭合多边形
    roi_poly = np.column_stack([lon_roi, lat_roi])

    # 如果没有闭合，添加第一个点
    if len(roi_poly) > 1 and not np.array_equal(roi_poly[0], roi_poly[-1]):
        roi_poly = np.vstack([roi_poly, roi_poly[0]])

    return {
        'roi_poly': roi_poly,
        'lon_roi': lon_roi,
        'lat_roi': lat_roi
    }


def _read_multi_bands_smart(dir_path: str, patterns: list, R: dict, num_bands: int) -> np.ndarray:
    """
    智能读取多个波段（内部函数）

    Args:
        dir_path: 目录路径
        patterns: 波段模式列表，每个元素是包含可能名称的元组
        R: 地理参考信息
        num_bands: 波段数量

    Returns:
        cube: 波段数据立方体 (H, W, num_bands)
    """
    import rasterio
    from rasterio.warp import reproject
    from rasterio.enums import Resampling

    H, W = R['RasterSize']
    cube = np.full((H, W, num_bands), np.nan, dtype=np.float32)

    # 查找所有影像文件
    files = (glob.glob(os.path.join(dir_path, '*.tif')) +
             glob.glob(os.path.join(dir_path, '*.tiff')) +
             glob.glob(os.path.join(dir_path, '*.jp2')))

    for b in range(num_bands):
        pattern = patterns[b]
        found = False

        for file_path in files:
            fname = os.path.basename(file_path).upper()

            # 检查是否匹配任何模式
            if any(p.upper() in fname for p in pattern):
                # 读取文件
                with rasterio.open(file_path) as src:
                    A = src.read(1)
                    A = A.astype(np.float32)

                    # 如果需要，进行重采样
                    if A.shape != (H, W):
                        A_resampled = np.full((H, W), np.nan, dtype=np.float32)

                        # 使用地理坐标插值 (匹配 MATLAB readMultiBands_smart 的 interp2)
                        lon_min, lon_max = R['LongitudeLimits']
                        lat_min, lat_max = R['LatitudeLimits']
                        lon_src = np.linspace(lon_min, lon_max, A.shape[1])
                        lat_src = np.linspace(lat_min, lat_max, A.shape[0])
                        lon_dst = np.linspace(lon_min, lon_max, W)
                        lat_dst = np.linspace(lat_min, lat_max, H)

                        from scipy.interpolate import RectBivariateSpline

                        # NaN 掩码插值 (匹配 MATLAB interp2 的 NaN 传播行为)
                        # MATLAB interp2 会对包含 NaN 的邻域输出 NaN
                        nan_mask = np.isnan(A).astype(np.float64)
                        has_nan = np.any(nan_mask > 0)

                        A_float = A.astype(np.float64)
                        A_float = np.nan_to_num(A_float, nan=0.0)

                        # 插值数据
                        f = RectBivariateSpline(lat_src, lon_src, A_float, kx=1, ky=1)
                        A_resampled = f(lat_dst, lon_dst).astype(np.float32)

                        # 插值 NaN 掩码，标记应为 NaN 的区域
                        if has_nan:
                            f_nan = RectBivariateSpline(lat_src, lon_src, nan_mask, kx=1, ky=1)
                            nan_interp = f_nan(lat_dst, lon_dst)
                            # 任何原始 NaN 的邻域像素也应为 NaN
                            A_resampled[nan_interp > 0.01] = np.nan

                        A = A_resampled

                    cube[:, :, b] = A
                    found = True
                    break

        if not found:
            print(f"警告: 未找到波段 {b+1} 的数据，模式: {pattern}")

    return cube


def _read_any_smart(dir_path: str, patterns: list, R: dict) -> np.ndarray:
    """
    读取单个波段（内部函数）

    Args:
        dir_path: 目录路径
        patterns: 波段模式
        R: 地理参考信息

    Returns:
        band: 单波段数据 (H, W)
    """
    cube = _read_multi_bands_smart(dir_path, [patterns], R, 1)
    return cube[:, :, 0]


def get_band(*args) -> np.ndarray:
    """
    从多个数据源中获取指定波段

    Args:
        *args: 数据源列表，最后一个参数是波段索引

    Returns:
        band: 波段数据
    """
    idx = args[-1]

    for data in args[:-1]:
        if data is not None and data.shape[2] >= idx and np.any(data[:, :, idx] > 0):
            return data[:, :, idx]

    return np.full(data.shape[:2], np.nan, dtype=np.float32)


def fill_aster_nan(ast: np.ndarray, inROI: np.ndarray) -> np.ndarray:
    """
    填充 ASTER 数据的 NaN 值（类似于 Matlab fillAsterNaN）

    Args:
        ast: ASTER 数据 (H, W, 14)
        inROI: ROI 掩码

    Returns:
        ast_filled: 填充后的 ASTER 数据
    """
    ast_filled = ast.copy()

    for b in range(ast.shape[2]):
        band_data = ast[:, :, b]
        roi_vals = band_data[inROI]
        mean_val = np.nanmean(roi_vals)

        if not np.isnan(mean_val):
            mask = inROI & np.isnan(band_data)
            band_data[mask] = mean_val
            ast_filled[:, :, b] = band_data

    return ast_filled


def export_kmz_from_mat(mat_file: str, output_dir: str):
    """
    从 .mat 结果文件导出 KMZ

    简化版 KMZ 导出，生成 GroundOverlay + ROI 边界 + Top 靶点。
    无第三方 KMZ 库依赖:手写 KML + zip 成 KMZ(KMZ 本质是含 doc.kml 的 zip),
    任何环境都能出图(此前依赖 simplekml,未装则失败)。
    """
    from scipy.io import loadmat
    import zipfile
    from xml.sax.saxutils import escape

    mat = loadmat(mat_file)

    lonGrid = mat.get('lonGrid')
    latGrid = mat.get('latGrid')
    Au_deep = mat.get('Au_deep')
    # loadmat 把标量存成数组(numpy 2.x 下 float(1元素数组) 会报错),统一 ravel 取首元素
    mineral_type = str(np.asarray(mat['mineral_type']).ravel()[0]) if 'mineral_type' in mat else 'unknown'
    kmz_threshold = float(np.asarray(mat['kmz_threshold']).ravel()[0]) if 'kmz_threshold' in mat else 0.6

    if lonGrid is None or latGrid is None or Au_deep is None:
        print("警告: .mat 文件缺少必要字段，跳过 KMZ 生成")
        return

    # 生成预测图 PNG (GroundOverlay)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    Au_deep_clean = Au_deep.copy()
    Au_deep_clean[Au_deep_clean == 0] = np.nan

    fig, ax = plt.subplots(figsize=(12, 10))
    ax.contourf(lonGrid, latGrid, Au_deep_clean, levels=np.linspace(0.4, 1.0, 25), cmap='jet', extend='both')
    ax.axis('off')
    fig.patch.set_alpha(0)
    ax.set_position([0, 0, 1, 1])

    img_filename = f"{mineral_type}_prediction_overlay.png"
    img_path = os.path.join(output_dir, img_filename)
    plt.savefig(img_path, dpi=300, transparent=True, bbox_inches='tight', pad_inches=0)
    plt.close(fig)

    north = float(np.nanmax(latGrid)); south = float(np.nanmin(latGrid))
    east = float(np.nanmax(lonGrid)); west = float(np.nanmin(lonGrid))

    p = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
         f'<name>{escape(mineral_type.capitalize())} 资源深部预测 (阈值≥{kmz_threshold})</name>',
         # GroundOverlay:color=ccffffff(白色不染色 + ~80% 不透明),呈现真实 jet 配色
         '<GroundOverlay><name>预测图层</name><color>ccffffff</color>',
         f'<Icon><href>{escape(img_filename)}</href></Icon>',
         f'<LatLonBox><north>{north}</north><south>{south}</south>'
         f'<east>{east}</east><west>{west}</west></LatLonBox></GroundOverlay>']

    # ROI 边界
    lonROI = mat.get('lonROI')
    latROI = mat.get('latROI')
    if lonROI is not None and latROI is not None:
        lonROI = lonROI.flatten().tolist()
        latROI = latROI.flatten().tolist()
        if len(lonROI) > 2:
            coords = list(zip(lonROI, latROI))
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            coord_str = ' '.join(f'{x},{y},0' for x, y in coords)
            p.append('<Folder><name>探测范围边界 (ROI)</name><Placemark><name>ROI边界</name>'
                     '<Style><LineStyle><color>ff000000</color><width>4</width></LineStyle>'
                     '<PolyStyle><color>00ffffff</color></PolyStyle></Style>'
                     '<Polygon><outerBoundaryIs><LinearRing>'
                     f'<coordinates>{coord_str}</coordinates>'
                     '</LinearRing></outerBoundaryIs></Polygon></Placemark></Folder>')

    # Top 靶点
    lonTop = mat.get('lonTop')
    latTop = mat.get('latTop')
    if lonTop is not None and latTop is not None:
        lonTop = lonTop.flatten().tolist()
        latTop = latTop.flatten().tolist()
        p.append('<Folder><name>Top 靶区核心点</name>')
        for i in range(min(len(lonTop), 20)):
            p.append(f'<Placemark><name>Target_{i+1}</name><Point>'
                     f'<coordinates>{float(lonTop[i])},{float(latTop[i])},0</coordinates>'
                     '</Point></Placemark>')
        p.append('</Folder>')

    p.append('</Document></kml>')
    kml_str = '\n'.join(p)

    # 打包 KMZ(zip: doc.kml + 叠加图 PNG)
    kmz_filename = f"{mineral_type}_prediction_kmz_threshold{kmz_threshold}.kmz"
    kmz_path = os.path.join(output_dir, kmz_filename)
    with zipfile.ZipFile(kmz_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('doc.kml', kml_str)
        if os.path.exists(img_path):
            z.write(img_path, img_filename)
    print(f"KMZ 已导出: {kmz_path}")
