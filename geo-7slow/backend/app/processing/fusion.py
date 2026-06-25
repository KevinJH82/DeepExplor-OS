"""Z-score标准化、a/b融合、Δ判别式、靶区提取"""
import numpy as np
from scipy.ndimage import binary_opening, binary_closing, binary_fill_holes, label
from skimage.morphology import disk

from app.config import (
    NODATA, DEFAULT_WEIGHTS, DEFAULT_DELTA_THRESHOLD, DEFAULT_DELTA_PERCENTILE,
)

# 尖点突变(cusp catastrophe)分叉集系数:平衡态 x³+ax+b=0 的判别式 27b²+4a³,
# 归一即 Δ = b² + (4/27)a³。教科书值为 4/27(原代码误用 8/27,P4 订正)。
CUSP_A3_COEF = 4.0 / 27.0


def zscore_normalize(data: np.ndarray) -> np.ndarray:
    """Z-score标准化，排除NODATA像素"""
    valid = (data != NODATA) & np.isfinite(data)
    if not np.any(valid):
        return data.copy()

    vals = data[valid]
    mean = np.mean(vals)
    std = np.std(vals)

    result = np.full_like(data, NODATA)
    if std > 1e-10:
        result[valid] = (data[valid] - mean) / std
    else:
        result[valid] = 0.0
    return result


def compute_fusion(
    stress_z: np.ndarray,
    redox_z: np.ndarray,
    fluid_z: np.ndarray,
    fault_z: np.ndarray,
    cap_rock_z: np.ndarray,
    temp_z: np.ndarray,
    chem_z: np.ndarray,
    weights: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    尖点突变(cusp catastrophe)成矿判别式。

    成矿系统视为突变系统:势函数 V(x)=x⁴/4 + a·x²/2 + b·x,平衡态满足 x³+ax+b=0。
      a = 劈裂因子(splitting/正则相反):本系统取"阻力" = ⑥盖层封闭性 + ⑦温度梯度
      b = 正则因子(normal/分叉):本系统取"驱动力" = ①应力 +②氧逸度 +③流体 +④断裂 +⑤化学势
    分叉集(出现多稳态/突跳的临界面)为 27b²+4a³=0,归一即:

        Δ = b² + (4/27)·a³        (CUSP_A3_COEF,教科书值;原代码误用 8/27,P4 订正)

    Δ < 0 → 落在尖点内部(双稳/可突跳区)= 有利成矿区。
    注:b 取平方是突变论本身的性质(分叉集对正则因子 b 符号对称),非笔误。

    NODATA 防泄漏:各 z 变量在无效像素填 0(z=0 即均值,中性贡献),避免 −9999
    进入 a³/b²;仅在"每个变量都有真实数据"的公共像素输出,其余置 NODATA。

    返回 (a, b, delta)
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    zvars = [stress_z, redox_z, fluid_z, fault_z, cap_rock_z, temp_z, chem_z]

    # 公共有效掩码:仅在 7 个变量都有真实数据处输出 Δ
    common = np.ones(stress_z.shape, dtype=bool)
    for v in zvars:
        common &= (v != NODATA) & np.isfinite(v)

    # 中性填充:无效像素 → 0(z 均值),避免 NODATA 哨兵值进入算术
    def _neutral(v):
        return np.where((v != NODATA) & np.isfinite(v), v, 0.0)

    sz, rz, flz, faz, cz, tz, chz = (_neutral(v) for v in zvars)

    a = w["cap_rock"] * cz + w["temp_resist"] * tz
    b = (w["stress"] * sz +
         w["redox"] * rz +
         w["fluid"] * flz +
         w["fault"] * faz +
         w["chem"] * chz +
         w["temp_drive"] * tz)

    delta = b ** 2 + CUSP_A3_COEF * a ** 3

    # 非公共像素置 NODATA(不臆造边界 Δ)
    a = np.where(common, a, NODATA)
    b = np.where(common, b, NODATA)
    delta = np.where(common, delta, NODATA)
    return a, b, delta


def extract_target_zones(
    delta: np.ndarray,
    threshold: float | None = DEFAULT_DELTA_THRESHOLD,
    min_pixels: int | None = None,
    percentile: float = DEFAULT_DELTA_PERCENTILE,
) -> np.ndarray:
    """
    从Δ中提取靶区。Δ < threshold 且经过形态学处理。

    threshold=None 时使用自适应阈值:取有效Δ的低分位(percentile),Δ越负越有利。
    min_pixels=None 时按研究区大小自适应(有效像元的~1%,下限30),避免固定阈值在
    小ROI上把全部靶区清空(原固定500px+disk(5)对4~5km²的ROI过激)。

    返回 uint8 掩膜（1=靶区，0=非靶区，NODATA标记无效区域）
    """
    valid = (delta != NODATA) & np.isfinite(delta)
    n_valid = int(np.sum(valid))

    # 自适应阈值:未显式给数值时取有效Δ的低分位
    if threshold is None:
        threshold = float(np.percentile(delta[valid], percentile)) if n_valid else 0.0

    # 自适应最小连通域:随研究区面积缩放
    if min_pixels is None:
        min_pixels = max(30, round(0.01 * n_valid))

    target = np.zeros_like(delta, dtype=np.uint8)
    target[valid & (delta < threshold)] = 1

    # 形态学开闭去椒盐噪声(disk(2)半径≈60m@30m,对小ROI更稳;大场景可调大)
    struct = disk(2).astype(np.uint8)
    target = binary_opening(target, structure=struct).astype(np.uint8)
    target = binary_closing(target, structure=struct).astype(np.uint8)

    # 填充孔洞（论文: imfill）
    target = binary_fill_holes(target).astype(np.uint8)

    # 移除小于min_pixels的连通域（论文: bwareaopen）
    labeled, num_features = label(target)
    for i in range(1, num_features + 1):
        if np.sum(labeled == i) < min_pixels:
            target[labeled == i] = 0

    # NODATA区域标记
    target[~valid] = 255
    return target


def compute_stats(data: np.ndarray) -> dict:
    """计算有效像素的统计摘要"""
    valid = (data != NODATA) & np.isfinite(data)
    if not np.any(valid):
        return {"min": None, "max": None, "mean": None, "std": None, "valid_pixels": 0}

    vals = data[valid]
    return {
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "mean": float(np.mean(vals)),
        "std": float(np.std(vals)),
        "valid_pixels": int(np.sum(valid)),
    }
