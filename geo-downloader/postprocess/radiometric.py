"""
相对辐射归一化(RRN, Relative Radiometric Normalization)

拼接前让多景在重叠区"同名地物反射率一致",从而消除缝处辐射台阶——下游蚀变分析
(全局阈值 / PCA)对缝阶跃高度敏感。本模块逐波段做线性映射 y = a·x + b
(保物理量、保波段间比例),用稳健回归(Theil-Sen / 截断最小二乘)在重叠区拟合。

约束(对应参考文档 §4.3 红线):
  - 仅线性 a·x+b(per-band gain/offset),不做直方图拉伸 / Wallis / 任何视觉匀色;
  - 逐波段独立拟合,保持波段间相对关系(比值/吸收深度依赖此);
  - 重叠样本不足则跳过该景(强行拟合更危险)。

使用约定:
  - 仅在"多景拼接"前调用,单景不调用;
  - 默认由上层开关控制(默认关),先验证后再切默认;
  - **绝不抛出到调用方**:任何失败都回退为"原样返回输入路径"。
  - 就地归一化(写临时文件后原子替换),返回与输入相同的路径列表——
    这些是拼接后即删的中间波段文件,就地处理对最终交付无副作用。
"""

import os
from pathlib import Path
from typing import List, Optional

try:
    import numpy as np
    import rasterio
    from rasterio.vrt import WarpedVRT
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

try:
    from scipy.stats import theilslopes as _theilslopes
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

_FIT_MAX_DIM = 1024          # 拟合采样时的最大边长(抽稀,控内存/耗时)
_REF_PICK_DIM = 256          # 选参考景时的抽稀边长
_SLOPE_LO, _SLOPE_HI = 0.2, 5.0  # 斜率合理范围,超出视为拟合失败,跳过该波段


def _valid_mask(arr, nodata):
    """逐像元有效掩膜(shape 同 arr)。nodata / 非有限 / 0 均视为无效。"""
    if arr.dtype.kind == "f":
        m = np.isfinite(arr)
    else:
        m = np.ones(arr.shape, dtype=bool)
    if nodata is not None:
        try:
            m &= (arr != nodata)
        except Exception:
            pass
    m &= (arr != 0)  # 0 视为无效(边缘/填充),与 clip/merge 的 nodata 约定一致
    return m


def _robust_linear_fit(x, y):
    """返回 (a, b) 使 y ≈ a·x + b;稳健拟合 + 极端值截断,失败返回 None。"""
    if x.size < 2:
        return None
    # 2%-98% 截断去极端值(云/水/异常),双侧都要落在区间内
    try:
        xlo, xhi = np.percentile(x, [2, 98])
        ylo, yhi = np.percentile(y, [2, 98])
    except Exception:
        return None
    m = (x >= xlo) & (x <= xhi) & (y >= ylo) & (y <= yhi)
    x, y = x[m], y[m]
    if x.size < 2 or np.ptp(x) == 0:
        return None
    a = None
    if HAS_SCIPY:
        try:
            a = _theilslopes(y, x)[0]
        except Exception:
            a = None
    if a is None or not np.isfinite(a):
        try:
            a = np.polyfit(x, y, 1)[0]
        except Exception:
            return None
    if not np.isfinite(a) or not (_SLOPE_LO <= a <= _SLOPE_HI):
        return None
    # 稳健截距：截断内点上的 median(y - a·x)，比 median(y)-a·median(x) 更抗单侧离群
    b = float(np.median(y - a * x))
    if not np.isfinite(b):
        return None
    return float(a), b


def _pick_reference(paths) -> Optional[int]:
    """选参考景:有效像元最多者(最完整、辐射尺度最有代表性)。"""
    best_i, best_n = None, -1
    for i, p in enumerate(paths):
        try:
            with rasterio.open(p) as s:
                oh = max(1, min(s.height, _REF_PICK_DIM))
                ow = max(1, min(s.width, _REF_PICK_DIM))
                a = s.read(out_shape=(s.count, oh, ow))
                n = int(_valid_mask(a, s.nodata).sum())
        except Exception:
            n = -1
        if n > best_n:
            best_n, best_i = n, i
    return best_i


def _normalize_one(p, ref_crs, ref_transform, ref_w, ref_h, ref_count,
                   oh, ow, ref_arr, ref_valid, min_overlap_px) -> bool:
    """把单景 p 逐波段线性映射到参考辐射尺度,就地写回。成功返回 True。"""
    # 1) 把 p 投到参考网格,抽稀采样重叠区
    with rasterio.open(p) as src:
        if src.count != ref_count:
            return False
        nodata_i = src.nodata
        with WarpedVRT(src, crs=ref_crs, transform=ref_transform,
                       width=ref_w, height=ref_h) as vrt:
            vrt_arr = vrt.read(out_shape=(ref_count, oh, ow))
    vrt_valid = _valid_mask(vrt_arr, nodata_i)

    # 抽稀像元数 → 估算原始重叠像元数(用于 min_overlap_px 判据)
    scale_px = (ref_w / float(ow)) * (ref_h / float(oh))
    fits = {}
    for b in range(ref_count):
        m = ref_valid[b] & vrt_valid[b]
        if float(m.sum()) * scale_px < min_overlap_px:
            continue
        fit = _robust_linear_fit(vrt_arr[b][m].astype("float64"),
                                 ref_arr[b][m].astype("float64"))
        if fit is not None:
            fits[b] = fit
    if not fits:
        return False

    # 2) 应用到 p 的原生数据(只改辐射、不动几何),写临时文件后原子替换
    with rasterio.open(p) as src:
        meta = src.meta.copy()
        data = src.read()
        dtype = src.dtypes[0]
    out = data.astype("float64")
    for b, (a, c) in fits.items():
        valid = _valid_mask(data[b:b + 1], nodata_i)[0]
        out[b][valid] = a * out[b][valid] + c
    if np.dtype(dtype).kind in ("i", "u"):
        info = np.iinfo(dtype)
        out = np.clip(np.round(out), info.min, info.max)
    out = out.astype(dtype)

    tmp = p.with_suffix(".rrn_tmp.tif")
    try:
        with rasterio.open(tmp, "w", **meta) as dst:
            dst.write(out)
        os.replace(tmp, p)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return True


def normalize_to_reference(file_paths, *, ref_idx: Optional[int] = None,
                           min_overlap_px: int = 500,
                           method: str = "linear") -> List[Path]:
    """
    对一组同波段多景做相对辐射归一化(就地写回,返回相同路径列表)。

    Parameters
    ----------
    file_paths     : 同一波段(或同结构多波段)的多景栅格路径
    ref_idx        : 参考景下标;None=自动选(有效像元最多者)
    min_overlap_px : 与参考景重叠像元下限,低于则跳过该景(样本不足)
    method         : 预留,目前仅 "linear"(a·x+b)

    任何异常都被吞掉并回退为原样返回 file_paths,绝不影响拼接主流程。
    """
    if not HAS_DEPS or not file_paths or len(file_paths) < 2:
        return file_paths
    paths = [Path(p) for p in file_paths]
    try:
        if ref_idx is None:
            ref_idx = _pick_reference(paths)
        if ref_idx is None or not (0 <= ref_idx < len(paths)):
            return file_paths
        ref_path = paths[ref_idx]

        with rasterio.open(ref_path) as ref:
            ref_crs, ref_transform = ref.crs, ref.transform
            ref_w, ref_h, ref_count = ref.width, ref.height, ref.count
            ref_nodata = ref.nodata
            oh = max(1, min(ref_h, _FIT_MAX_DIM))
            ow = max(1, min(ref_w, _FIT_MAX_DIM))
            ref_arr = ref.read(out_shape=(ref_count, oh, ow))
        ref_valid = _valid_mask(ref_arr, ref_nodata)

        applied = 0
        for i, p in enumerate(paths):
            if i == ref_idx:
                continue
            try:
                if _normalize_one(p, ref_crs, ref_transform, ref_w, ref_h,
                                  ref_count, oh, ow, ref_arr, ref_valid,
                                  min_overlap_px):
                    applied += 1
            except Exception as e:
                print(f"    [RRN] {p.name} 归一化跳过: {e}")
        if applied:
            print(f"    [RRN] 已归一化 {applied}/{len(paths) - 1} 景到参考 {ref_path.name}")
    except Exception as e:
        print(f"    [RRN] 整体跳过(回退原始): {e}")
    return file_paths
