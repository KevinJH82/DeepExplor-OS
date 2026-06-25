"""
Postprocess: 衍生产品计算模块
从已下载的卫星影像计算常用地质勘探分析产品：

  LST  地表温度（Land Surface Temperature）
         来源：Landsat 8/9 Band10 或 ASTER AST_08 + AST_09T
  TEMP_GRAD   温度梯度（相邻像元温度变化率）
  TEMP_ANOM   温度异常梯度（局部温度偏差，突出热液/地热异常）
  OTCI        Ocean & Land Colour Instrument Chlorophyll Index 近似值
                （用 Sentinel-2 B05/B04/B03 近似计算，替代 Sentinel-3 OLCI）

输出均为 GeoTIFF，与输入同坐标系。
"""

from pathlib import Path
from typing import Optional, Tuple

try:
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def _check_deps():
    if not HAS_DEPS:
        raise ImportError("缺少依赖: numpy rasterio\n请运行: pip install numpy rasterio")


def _read_band(path: Path) -> Tuple[np.ndarray, dict]:
    """读取单波段 GeoTIFF，返回 (data_float32, profile)"""
    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
        # 将 nodata 替换为 nan
        if src.nodata is not None:
            data[data == src.nodata] = np.nan
        profile = src.profile.copy()
    return data, profile


def _write_band(data: np.ndarray, profile: dict, output_path: Path):
    """将单波段 float32 数组写出为 GeoTIFF，并写入 STATISTICS 元数据"""
    out_profile = profile.copy()
    out_profile.update({
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "compress": "deflate",
        "predictor": 3,   # float predictor
        "nodata": np.nan,
    })
    with rasterio.open(output_path, "w", **out_profile) as dst:
        dst.write(data, 1)
    # 写入统计元数据（QGIS/ArcGIS 自动拉伸显示，避免预览全黑）
    try:
        valid = data[~np.isnan(data)]
        if valid.size > 0:
            with rasterio.open(output_path, "r+") as dst:
                dst.update_tags(1,
                                STATISTICS_MINIMUM=str(float(np.percentile(valid, 2))),
                                STATISTICS_MAXIMUM=str(float(np.percentile(valid, 98))),
                                STATISTICS_MEAN=str(float(valid.mean())),
                                STATISTICS_STDDEV=str(float(valid.std())))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 1. 地表温度 LST
# ─────────────────────────────────────────────────────────────

def lst_from_landsat(band10_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    从 Landsat 8/9 Band10（热红外，DN值）反演地表温度（开尔文）。

    Landsat Collection 2 L2 Band10 已完成大气校正，
    DN → 亮温公式：T = K2 / ln(K1 / DN + 1)
    K1 = 774.8853, K2 = 1321.0789（Landsat 8 TIRS Band10）

    输出单位：摄氏度（°C）
    """
    _check_deps()

    if output_path is None:
        output_path = band10_path.parent.parent / "地表温度.tif"

    data, profile = _read_band(band10_path)

    # Landsat Collection 2 L2 ST 产品的 Scale Factor
    # L2 ST 波段已是地表温度（单位 0.00341802 K + 149.0 K 偏移）
    scale = 0.00341802
    offset = 149.0
    lst_k = data * scale + offset       # 开尔文
    lst_c = lst_k - 273.15              # 摄氏度

    # 合理值范围过滤（-50°C ~ 70°C）
    lst_c[(lst_c < -50) | (lst_c > 70)] = np.nan

    _write_band(lst_c, profile, output_path)
    print(f"    [LST] 地表温度已生成: {output_path.name}")
    return output_path


def lst_from_aster(ast08_path: Path, ast09t_path: Optional[Path] = None,
                   output_path: Optional[Path] = None) -> Path:
    """
    从 ASTER AST_08（地表动力温度，已含发射率校正）直接读取地表温度。
    AST_08 提供的是 Kinetic Temperature（K），Scale = 0.1。

    若同时提供 AST_09T（发射率），则用温度辐射方程精化（TES方法，可选）。
    输出单位：摄氏度（°C）
    """
    _check_deps()

    if output_path is None:
        output_path = ast08_path.parent.parent / "地表温度.tif"

    data, profile = _read_band(ast08_path)

    # AST_08 Scale Factor = 0.1，单位 K
    lst_k = data * 0.1
    lst_c = lst_k - 273.15

    lst_c[(lst_c < -50) | (lst_c > 70)] = np.nan

    _write_band(lst_c, profile, output_path)
    print(f"    [LST] 地表温度（ASTER）已生成: {output_path.name}")
    return output_path


# ─────────────────────────────────────────────────────────────
# 2. 温度梯度（Temperature Gradient）
# ─────────────────────────────────────────────────────────────

def temperature_gradient(lst_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    计算地表温度的空间梯度（Sobel算子，°C/pixel）。
    突出温度快速变化区域，用于识别构造断裂带边界。

    输出：梯度幅值（magnitude），单位 °C/pixel。
    """
    _check_deps()

    if output_path is None:
        output_path = lst_path.parent / "温度梯度.tif"

    data, profile = _read_band(lst_path)

    # 用 nan 处理：先填充均值用于梯度计算
    fill_val = np.nanmean(data)
    data_filled = np.where(np.isnan(data), fill_val, data)

    # Sobel 梯度
    from scipy.ndimage import sobel
    gx = sobel(data_filled, axis=1)
    gy = sobel(data_filled, axis=0)
    grad = np.sqrt(gx**2 + gy**2).astype(np.float32)
    grad[np.isnan(data)] = np.nan

    _write_band(grad, profile, output_path)
    print(f"    [GRAD] 温度梯度已生成: {output_path.name}")
    return output_path


# ─────────────────────────────────────────────────────────────
# 3. 温度异常梯度（Temperature Anomaly Gradient）
# ─────────────────────────────────────────────────────────────

def temperature_anomaly_gradient(lst_path: Path,
                                  window_size: int = 21,
                                  output_path: Optional[Path] = None) -> Path:
    """
    计算温度异常梯度：局部均值偏差 + 梯度，突出热液/地热异常。

    算法：
      1. 计算局部背景温度（滑动窗口均值，window_size×window_size）
      2. 异常 = 原始LST − 背景LST（局部偏差，°C）
      3. 再对异常图计算 Sobel 梯度，突出异常边界

    输出：异常梯度幅值（°C/pixel）。
    """
    _check_deps()

    if output_path is None:
        output_path = lst_path.parent / "温度异常梯度.tif"

    data, profile = _read_band(lst_path)

    fill_val = np.nanmean(data)
    data_filled = np.where(np.isnan(data), fill_val, data)

    # 局部背景（均匀滤波近似滑动窗口均值）
    from scipy.ndimage import uniform_filter, sobel
    background = uniform_filter(data_filled, size=window_size).astype(np.float32)

    # 局部温度异常
    anomaly = (data_filled - background).astype(np.float32)
    anomaly[np.isnan(data)] = np.nan

    # 异常的空间梯度
    anomaly_filled = np.where(np.isnan(anomaly), 0.0, anomaly)
    gx = sobel(anomaly_filled, axis=1)
    gy = sobel(anomaly_filled, axis=0)
    anom_grad = np.sqrt(gx**2 + gy**2).astype(np.float32)
    anom_grad[np.isnan(data)] = np.nan

    _write_band(anom_grad, profile, output_path)
    print(f"    [ANOM] 温度异常梯度已生成: {output_path.name}")
    return output_path


# ─────────────────────────────────────────────────────────────
# 4. OTCI（Terrestrial Chlorophyll Index，基于 Sentinel-2 近似）
# ─────────────────────────────────────────────────────────────

def otci_from_sentinel2(b03_path: Path, b04_path: Path, b05_path: Path,
                         output_path: Optional[Path] = None) -> Path:
    """
    基于 Sentinel-2 波段计算 OTCI 近似值。

    原始 OTCI 由 Sentinel-3 OLCI 定义：
      OTCI = (B10 - B09) / (B09 - B08)   [OLCI 波段]
    对应波长：753.75nm、708.75nm、665nm

    Sentinel-2 近似（波长最接近映射）：
      B05（705nm） → 红边1，≈ OLCI B09（708.75nm）
      B04（665nm） → 红光，  ≈ OLCI B08（665nm）
      B06（740nm） → 红边2，≈ OLCI B10（753.75nm）
    建议优先传入 B05/B04/B06，此函数参数为 b03=B06, b04=B05, b05=B04（按位置命名）。

    实际调用时请按如下顺序传参（名称仅为接口兼容）：
      otci_from_sentinel2(B06_path, B05_path, B04_path)
    即：otci_high=B06(740nm), otci_mid=B05(705nm), otci_low=B04(665nm)

    OTCI 值域参考：
      < 1.0  植被极稀疏/裸岩
      1~2    低覆盖
      2~3    中等覆盖
      > 3    高覆盖/密集植被

    输出：OTCI 无量纲指数（float32）
    """
    _check_deps()

    if output_path is None:
        output_path = b03_path.parent.parent / "OTCI.tiff"

    # 三个波段路径对应：b03_path=高波段(B06/740nm), b04_path=中波段(B05/705nm), b05_path=低波段(B04/665nm)
    b_high, profile = _read_band(b03_path)    # ~740nm（红边2）
    b_mid, _        = _read_band(b04_path)    # ~705nm（红边1）
    b_low, _        = _read_band(b05_path)    # ~665nm（红光）

    # B04(10m)/B05(20m)/B06(20m) 分辨率不同，裁剪后尺寸不一致，统一 resample 到 b_high 的形状
    target_shape = b_high.shape
    if b_mid.shape != target_shape:
        from scipy.ndimage import zoom
        b_mid = zoom(b_mid, (target_shape[0] / b_mid.shape[0],
                             target_shape[1] / b_mid.shape[1]), order=1)
    if b_low.shape != target_shape:
        from scipy.ndimage import zoom
        b_low = zoom(b_low, (target_shape[0] / b_low.shape[0],
                             target_shape[1] / b_low.shape[1]), order=1)

    # Sentinel-2 L2A Scale Factor（DN → 反射率，Collection 数值已是0-10000）
    b_high = b_high / 10000.0
    b_mid  = b_mid  / 10000.0
    b_low  = b_low  / 10000.0

    denom = b_mid - b_low
    # 避免除零
    denom_safe = np.where(np.abs(denom) < 1e-6, np.nan, denom)
    otci = (b_high - b_mid) / denom_safe
    otci = otci.astype(np.float32)

    # 合理值过滤（极端值通常是噪声或阴影）
    otci[(otci < -5) | (otci > 10)] = np.nan

    _write_band(otci, profile, output_path)
    print(f"    [OTCI] OTCI（植被指数）已生成: {output_path.name}")
    return output_path


# ─────────────────────────────────────────────────────────────
# 批量入口：对一个区域目录自动计算所有衍生产品
# ─────────────────────────────────────────────────────────────

def derive_all(area_dir: Path, sensor_dirs: dict = None) -> dict:
    """
    对下载目录中的数据自动计算所有衍生产品。

    Parameters
    ----------
    area_dir     : 区域根目录（如 downloads/twz/）
    sensor_dirs  : 各传感器子目录名映射，默认：
                   {"landsat": "landsat", "aster": "aster",
                    "sentinel2": "sentinel2"}

    Returns
    -------
    dict: {"lst": Path, "grad": Path, "anom": Path, "otci": Path}
    """
    _check_deps()

    if sensor_dirs is None:
        sensor_dirs = {
            "landsat":   "landsat",
            "aster":     "aster",
            "sentinel2": "sentinel2",
        }

    results = {}

    # ── 1. 地表温度 ────────────────────────────────────────
    lst_path = None

    # 优先用 ASTER AST_08（90m，专用热红外，更准确）
    # 三套命名都试: (a) 旧分景 AST_08/*.hdf|h5
    #              (b) 新 ROI mosaic 顶层 *_mosaic_SKT.tif
    #              (c) downloader 当前实际输出 AST_08/AST_08_*_SKT*.tif (已 clipped)
    aster_dir = area_dir / sensor_dirs.get("aster", "aster")
    ast08_candidates: list = []
    if aster_dir.exists():
        ast08_candidates += sorted(aster_dir.glob("*_mosaic_SKT.tif"))
        ast08_subdir = aster_dir / "AST_08"
        if ast08_subdir.exists():
            ast08_candidates += sorted(ast08_subdir.glob("*.hdf"))
            ast08_candidates += sorted(ast08_subdir.glob("*.h5"))
            # 当前 downloader 命名: AST_08_<id>_SKT_clipped.tif 或 *SKT*.tif
            ast08_candidates += sorted(ast08_subdir.glob("*SKT*.tif"))
            ast08_candidates += sorted(ast08_subdir.glob("*SKT*.tiff"))
    if ast08_candidates:
        try:
            lst_path = lst_from_aster(ast08_candidates[0],
                                      output_path=area_dir / "地表温度.tif")
            results["lst"] = lst_path
        except Exception as e:
            print(f"    [警告] ASTER LST 计算失败: {e}")

    # 回退：用 Landsat 热红外波段(30m 重采样到 TIR 分辨率)
    # 三套命名都试: *_ST_B10.TIF / *B10*.tif / B10.tif (老)
    #              + 当前 downloader: <scene>/*_lwir1[01]_clipped.TIF (rglob,在 scene 子目录里)
    if lst_path is None:
        landsat_dir = area_dir / sensor_dirs.get("landsat", "landsat")
        if landsat_dir.exists():
            b10_candidates = (
                list(landsat_dir.glob("*_ST_B10.TIF")) +
                list(landsat_dir.glob("*B10*.tif")) +
                list(landsat_dir.glob("B10.tif")) +
                # 当前 downloader: scene_dir 下的 lwir11(优先) / lwir12 已 clipped TIF
                list(landsat_dir.rglob("*_lwir11_clipped.TIF")) +
                list(landsat_dir.rglob("*_lwir11_clipped.tif")) +
                list(landsat_dir.rglob("*_lwir12_clipped.TIF")) +
                list(landsat_dir.rglob("*_lwir12_clipped.tif"))
            )
            if b10_candidates:
                try:
                    lst_path = lst_from_landsat(b10_candidates[0],
                                                output_path=area_dir / "地表温度.tif")
                    results["lst"] = lst_path
                except Exception as e:
                    print(f"    [警告] Landsat LST 计算失败: {e}")

    if lst_path is None:
        print("    [跳过] 未找到热红外波段，无法计算地表温度")

    # ── 2. 温度梯度 ────────────────────────────────────────
    if lst_path and lst_path.exists():
        try:
            grad_path = temperature_gradient(
                lst_path, output_path=area_dir / "温度梯度.tif"
            )
            results["grad"] = grad_path
        except Exception as e:
            print(f"    [警告] 温度梯度计算失败: {e}")

    # ── 3. 温度异常梯度 ────────────────────────────────────
    if lst_path and lst_path.exists():
        try:
            anom_path = temperature_anomaly_gradient(
                lst_path, output_path=area_dir / "温度异常梯度.tif"
            )
            results["anom"] = anom_path
        except Exception as e:
            print(f"    [警告] 温度异常梯度计算失败: {e}")

    # ── 4. OTCI ────────────────────────────────────────────
    s2_dir = area_dir / sensor_dirs.get("sentinel2", "sentinel2")
    if s2_dir.exists():
        # 查找 B04/B05/B06（名称可能带日期前缀）
        def find_band(d, patterns):
            for p in patterns:
                matches = list(d.glob(p))
                if matches:
                    return matches[0]
            return None

        b04 = find_band(s2_dir, ["B04.tiff", "B04.tif", "*_B04_*.tif*", "*B04*.tif*"])
        b05 = find_band(s2_dir, ["B05.tiff", "B05.tif", "*_B05_*.tif*", "*B05*.tif*"])
        b06 = find_band(s2_dir, ["B06.tiff", "B06.tif", "*_B06_*.tif*", "*B06*.tif*"])

        if b04 and b05 and b06:
            try:
                otci_path = otci_from_sentinel2(
                    b06, b05, b04,
                    output_path=area_dir / "OTCI.tiff"
                )
                results["otci"] = otci_path
            except Exception as e:
                print(f"    [警告] OTCI 计算失败: {e}")
        else:
            missing = [n for n, f in [("B04", b04), ("B05", b05), ("B06", b06)] if f is None]
            print(f"    [跳过 OTCI] 缺少 Sentinel-2 波段: {missing}")

    return results
