#!/usr/bin/env python3
"""按 bbox 下载 Copernicus DEM GLO-30 瓦片(公开 S3,无需凭证)→ 镶嵌裁剪 →
写入交付库冬季子目录 dem.tif,供 geo-stru 读取。用系统 python3 运行(需 requests+rasterio)。

用法: fetch_dem.py <minx> <miny> <maxx> <maxy> <delivery_project_dir>
退出码: 0=成功, 2=无瓦片(海洋/无覆盖), 3=异常
"""
import math
import os
import sys
import tempfile

import requests
import rasterio
from rasterio.merge import merge

_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/"
        "Copernicus_DSM_COG_10_{la}_00_{lo}_00_DEM/"
        "Copernicus_DSM_COG_10_{la}_00_{lo}_00_DEM.tif")


def _tag(lat, lon):
    return (f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}",
            f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}")


def main():
    minx, miny, maxx, maxy = map(float, sys.argv[1:5])
    proj_dir = sys.argv[5]
    tiles = [(lat, lon)
             for lat in range(math.floor(miny), math.floor(maxy) + 1)
             for lon in range(math.floor(minx), math.floor(maxx) + 1)]
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
        except Exception as e:
            print(f"tile {la}{lo} fail: {e}", file=sys.stderr)
    if not paths:
        print("NO_TILES"); sys.exit(2)

    srcs = [rasterio.open(p) for p in paths]
    mosaic, transform = merge(srcs, bounds=(minx, miny, maxx, maxy))
    meta = srcs[0].meta.copy()
    meta.update(driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2],
                transform=transform, compress="deflate")
    for sc in srcs:
        sc.close()

    winter = None
    for c in sorted(os.listdir(proj_dir)):
        if "冬季" in c and os.path.isdir(os.path.join(proj_dir, c)):
            winter = os.path.join(proj_dir, c); break
    if not winter:
        winter = os.path.join(proj_dir, "data-矿权-冬季（11-3月）")
        os.makedirs(winter, exist_ok=True)
    out = os.path.join(winter, "dem.tif")
    with rasterio.open(out, "w", **meta) as d:
        d.write(mosaic)
    print(f"DEM_OK {out}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERR {e}", file=sys.stderr); sys.exit(3)
