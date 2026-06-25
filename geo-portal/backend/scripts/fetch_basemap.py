#!/usr/bin/env python3
"""按 bbox 拉 ROI 卫星底图(复用 geo-analyser/basemap_provider, Esri World Imagery,公开免 token)
→ 裁到 bbox → 存 PNG。用系统 python3 运行(需 requests+Pillow+numpy)。

用法: fetch_basemap.py <minLon> <minLat> <maxLon> <maxLat> <out_png>
退出码: 0=成功(stdout 打印实际 bbox/zoom JSON), 2=取图失败(海域/无覆盖/外网不可达), 3=异常
"""
import json
import os
import sys

# 复用 geo-analyser 的卫星瓦片拼合实现(不重写取图逻辑)
_GEO_ANALYSER = os.environ.get("GEO_ANALYSER_DIR", "/opt/deepexplor-services/geo-analyser")
for cand in (_GEO_ANALYSER, "/opt/Project/deepexplor-services/geo-analyser"):
    if os.path.isdir(cand) and cand not in sys.path:
        sys.path.insert(0, cand)


def main():
    min_lon, min_lat, max_lon, max_lat = map(float, sys.argv[1:5])
    out_png = sys.argv[5]

    from basemap_provider import fetch_satellite_basemap
    res = fetch_satellite_basemap((min_lon, min_lat, max_lon, max_lat))
    if not res or res.get("image") is None:
        print("NO_BASEMAP", file=sys.stderr)
        sys.exit(2)

    from PIL import Image as PILImage
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    PILImage.fromarray(res["image"]).save(out_png, format="PNG")
    print(json.dumps({"bbox": list(res.get("bbox") or []),
                      "zoom": res.get("zoom"),
                      "source": res.get("source")}))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(3)
