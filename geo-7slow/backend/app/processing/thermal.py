"""ASTER 热红外定标:DN → 辐亮度 → 亮温(Planck),并合成窗区 LST 代理。

说明(重要):交付的 ASTER "L2" 热红外为 AST_09T 地表离开辐亮度(int16 DN)。
经核实,用 LP DAAC 公布的逐波段尺度因子做 Planck 反演,各波段亮温互不自洽
(窗区 B13/B14 亮温偏冷,绝对值不可信)——本批数据在盘尺度存在不确定。
因此本模块产出的亮温/LST 仅作**相对**用途(z-score、梯度),不主张绝对温度。
选择窗区波段(B13≈10.66µm、B14≈11.32µm)是因为其发射率稳定(~0.95),
相对热结构最干净;而 B10–B12 受硅酸盐发射率(reststrahlen)主导、会污染"温度"。
"""
import numpy as np

from app.config import NODATA

# ASTER TIR 波段有效波长(µm)
ASTER_TIR_WAVELENGTH_UM = {
    "aster_b10": 8.291,
    "aster_b11": 8.634,
    "aster_b12": 9.075,
    "aster_b13": 10.657,
    "aster_b14": 11.318,
}

# AST_09T 逐波段尺度因子:radiance(W/m²/sr/µm) = DN × scale(LP DAAC 公布值)
ASTER_TIR_SCALE = {
    "aster_b10": 0.006882,
    "aster_b11": 0.006780,
    "aster_b12": 0.006590,
    "aster_b13": 0.005693,
    "aster_b14": 0.005225,
}

# 窗区波段(发射率稳定,作 LST 代理首选)
WINDOW_BANDS = ["aster_b13", "aster_b14"]

# Planck 常数(λ 用 µm,L 用 W/m²/sr/µm)
_C1 = 1.19104e8   # 2hc²,W·µm⁴·m⁻²·sr⁻¹
_C2 = 1.43877e4   # hc/k,µm·K


def _valid(data: np.ndarray) -> np.ndarray:
    return (data != NODATA) & np.isfinite(data)


def brightness_temperature(dn: np.ndarray, band_key: str) -> np.ndarray:
    """单波段 DN → 亮温(K)。无效/非正辐亮度像元置 NODATA。

    相对用途:绝对值受在盘尺度不确定影响,仅供 z-score/梯度。
    """
    scale = ASTER_TIR_SCALE.get(band_key)
    lam = ASTER_TIR_WAVELENGTH_UM.get(band_key)
    if scale is None or lam is None:
        raise ValueError(f"非 ASTER TIR 波段: {band_key}")

    mask = _valid(dn)
    out = np.full(dn.shape, NODATA, dtype=np.float64)
    if not np.any(mask):
        return out

    L = dn.astype(np.float64) * scale          # 辐亮度 W/m²/sr/µm
    good = mask & (L > 0)
    # T_b = c2 / (λ · ln(1 + c1/(λ⁵·L)))
    lam5 = lam ** 5
    bt = _C2 / (lam * np.log(1.0 + _C1 / (lam5 * np.where(good, L, 1.0))))
    out[good] = bt[good]
    return out


def compute_lst_proxy(aligned: dict) -> np.ndarray:
    """合成 LST 代理(K,相对用途)。

    优先用窗区 B13/B14 的亮温均值(发射率稳定);窗区都缺失时退回任意可用 TIR 波段
    的亮温均值;全部缺失返回 None(让调用方按 NODATA 处理)。
    """
    # 收集可用波段及其亮温
    def _bt_stack(band_keys):
        bts, masks = [], []
        for bk in band_keys:
            arr = aligned.get(bk)
            if arr is None:
                continue
            bt = brightness_temperature(arr, bk)
            m = _valid(bt)
            if np.any(m):
                bts.append(np.where(m, bt, np.nan))
                masks.append(m)
        return bts, masks

    bts, masks = _bt_stack(WINDOW_BANDS)
    if not bts:
        # 退回:任意可用 TIR 波段
        bts, masks = _bt_stack(list(ASTER_TIR_WAVELENGTH_UM.keys()))
    if not bts:
        return None

    stack = np.stack(bts, axis=0)              # (n, H, W),无效为 nan
    finite = np.isfinite(stack)
    count = finite.sum(axis=0)
    ssum = np.where(finite, stack, 0.0).sum(axis=0)
    lst = np.full(stack.shape[1:], NODATA, dtype=np.float64)
    ok = count > 0
    lst[ok] = ssum[ok] / count[ok]
    return lst


def _ndvi(b4, b8):
    """NDVI = (B08−B04)/(B08+B04);缺波段返回 None。"""
    if b4 is None or b8 is None:
        return None
    m = _valid(b4) & _valid(b8)
    out = np.full(b4.shape, NODATA, dtype=np.float64)
    if not np.any(m):
        return out
    b4f = b4.astype(np.float64)
    b8f = b8.astype(np.float64)
    out[m] = (b8f[m] - b4f[m]) / (b8f[m] + b4f[m] + 1e-10)
    return out


def compute_seasonal_diff(aligned: dict) -> dict:
    """
    季节差分(冬−夏),相对用途。返回 {"dlst": arr|None, "dndvi": arr|None}。

    ΔLST:窗区亮温冬−夏(热季节振幅;振幅小→地下热缓冲,可能热液/流体活跃)。
    ΔNDVI:植被相态冬−夏。夏季波段缺失则对应项为 None。
    """
    out = {"dlst": None, "dndvi": None}

    def _diff(winter, summer):
        """冬−夏差分;退化(夏=冬副本→常数/方差≈0)时返回 None,不输出无效层。"""
        if winter is None or summer is None:
            return None
        m = _valid(winter) & _valid(summer)
        if not np.any(m):
            return None
        d = np.full(winter.shape, NODATA, dtype=np.float64)
        d[m] = winter[m] - summer[m]
        if float(np.std(d[m])) < 1e-9:        # 夏季数据是冬季副本等退化情况
            return None
        return d

    # ΔLST = 冬季窗区LST − 夏季窗区LST
    s_dict = {
        "aster_b13": aligned.get("aster_b13_summer"),
        "aster_b14": aligned.get("aster_b14_summer"),
    }
    if any(s_dict[k] is not None for k in ("aster_b13", "aster_b14")):
        out["dlst"] = _diff(compute_lst_proxy(aligned), compute_lst_proxy(s_dict))

    # ΔNDVI = 冬季NDVI − 夏季NDVI
    out["dndvi"] = _diff(
        _ndvi(aligned.get("s2_b04"), aligned.get("s2_b08")),
        _ndvi(aligned.get("s2_b04_summer"), aligned.get("s2_b08_summer")),
    )

    return out
