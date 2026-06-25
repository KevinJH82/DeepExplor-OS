#!/usr/bin/env python3
"""按 bbox 下载 Copernicus DEM GLO-30 瓦片(公开 S3,无需凭证)→ 镶嵌裁剪 → 重采样到
size×size 高程网格 → 输出 JSON(供前端 Three.js 做地形顶点位移)。用系统 python3 运行
(需 requests+rasterio+numpy)。**不写交付库**,与 fetch_dem.py 给 geo-stru 的契约互不影响。

用法: fetch_terrain.py <minLon> <minLat> <maxLon> <maxLat> <size> <out_json>
退出码: 0=成功, 2=无瓦片(海洋/无覆盖), 3=异常
JSON: {bbox, size, min_m, max_m, heights:[size*size 行优先 北→南/西→东]}
"""
import json
import math
import os
import sys
import tempfile

import numpy as np
import requests
import rasterio
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.merge import merge

_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/"
        "Copernicus_DSM_COG_10_{la}_00_{lo}_00_DEM/"
        "Copernicus_DSM_COG_10_{la}_00_{lo}_00_DEM.tif")


def _tag(lat, lon):
    return (f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}",
            f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}")


def main():
    min_lon, min_lat, max_lon, max_lat = map(float, sys.argv[1:5])
    size = max(16, min(512, int(sys.argv[5])))
    out_json = sys.argv[6]

    tiles = [(lat, lon)
             for lat in range(math.floor(min_lat), math.floor(max_lat) + 1)
             for lon in range(math.floor(min_lon), math.floor(max_lon) + 1)]
    tmp = tempfile.mkdtemp()
    s = requests.Session(); s.trust_env = False
    paths = []
    for lat, lon in tiles:
        la, lo = _tag(lat, lon)
        url = _URL.format(la=la, lo=lo)
        try:
            h = s.head(url, timeout=30, allow_redirects=True)
            if h.status_code == 404:
                continue
            h.raise_for_status()
            r = s.get(url, timeout=180, allow_redirects=True)
            r.raise_for_status()
            p = os.path.join(tmp, f"{la}_{lo}.tif")
            with open(p, "wb") as f:
                f.write(r.content)
            paths.append(p)
        except Exception as e:  # noqa: BLE001
            print(f"tile {la}{lo} fail: {e}", file=sys.stderr)
    if not paths:
        print("NO_TILES")
        sys.exit(2)

    srcs = [rasterio.open(p) for p in paths]
    # 镶嵌并裁到 bbox
    mosaic, transform = merge(srcs, bounds=(min_lon, min_lat, max_lon, max_lat))
    nodata = srcs[0].nodata
    meta = srcs[0].meta.copy()
    meta.update(driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2],
                transform=transform)
    for sc in srcs:
        sc.close()
    # 经内存数据集双线性重采样到 size×size
    with MemoryFile() as mf:
        with mf.open(**meta) as ds:
            ds.write(mosaic)
        with mf.open() as ds:
            arr = ds.read(1, out_shape=(size, size), resampling=Resampling.bilinear)

    arr = np.asarray(arr, dtype="float64")
    mask = np.isfinite(arr)
    if nodata is not None:
        mask &= (arr != nodata)
    # nodata/海面 → 填该网格有效高程的最小值(无有效值则 0)
    fill = float(arr[mask].min()) if mask.any() else 0.0
    arr = np.where(mask, arr, fill)
    mn, mx = float(arr.min()), float(arr.max())

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "bbox": [min_lon, min_lat, max_lon, max_lat],
            "size": size,
            "min_m": mn,
            "max_m": mx,
            "heights": [round(v, 1) for v in arr.ravel().tolist()],
        }, f)
    print(f"TERRAIN_OK {out_json} {mn:.1f}~{mx:.1f}m")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(3)
