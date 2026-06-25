#!/usr/bin/env python3
"""
一次性脚本：为 delivery 目录下所有 GeoTIFF 写入 p2~p98 统计元数据。
运行后 macOS Finder 缩略图、QGIS 等将自动拉伸显示，不再全黑。

用法：
    python3 fix_tiff_statistics.py [delivery目录路径]

默认目录：./delivery
"""

import sys
from pathlib import Path

try:
    import rasterio
    import numpy as np
except ImportError:
    print("缺少依赖，请运行: pip install rasterio numpy")
    sys.exit(1)


def write_statistics(tiff_path: Path, p_low: float = 2.0, p_high: float = 98.0) -> bool:
    try:
        with rasterio.open(tiff_path, "r+") as ds:
            for band_idx in range(1, ds.count + 1):
                data = ds.read(band_idx).astype(np.float64)
                nd = ds.nodata
                if nd is not None:
                    if np.isnan(nd):
                        mask = ~np.isnan(data)
                    else:
                        mask = ~np.isnan(data) & (data != nd)
                else:
                    mask = np.ones(data.shape, dtype=bool)
                valid = data[mask]
                if valid.size == 0:
                    continue
                vmin = float(np.percentile(valid, p_low))
                vmax = float(np.percentile(valid, p_high))
                vmean = float(valid.mean())
                vstd = float(valid.std())
                ds.update_tags(band_idx,
                               STATISTICS_MINIMUM=str(vmin),
                               STATISTICS_MAXIMUM=str(vmax),
                               STATISTICS_MEAN=str(vmean),
                               STATISTICS_STDDEV=str(vstd))
        return True
    except Exception as e:
        print(f"  [错误] {tiff_path.name}: {e}")
        return False


def main():
    delivery_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("delivery")
    if not delivery_dir.exists():
        print(f"目录不存在: {delivery_dir}")
        sys.exit(1)

    tiffs = list(delivery_dir.rglob("*.tiff")) + list(delivery_dir.rglob("*.tif"))
    if not tiffs:
        print("未找到任何 GeoTIFF 文件")
        sys.exit(0)

    print(f"找到 {len(tiffs)} 个 GeoTIFF 文件，开始写入统计元数据...")
    ok, fail = 0, 0
    for i, p in enumerate(tiffs, 1):
        print(f"  [{i}/{len(tiffs)}] {p.relative_to(delivery_dir)}", end=" ... ", flush=True)
        if write_statistics(p):
            print("OK")
            ok += 1
        else:
            fail += 1

    print(f"\n完成：{ok} 成功，{fail} 失败")


if __name__ == "__main__":
    main()
