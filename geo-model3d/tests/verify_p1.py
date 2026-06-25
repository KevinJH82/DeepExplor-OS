#!/usr/bin/env python3
"""geo-model3d P1 端到端验证。

测试 AOI：辽宁本溪市铜钼矿（geo-analyser+geo-stru 有 run、geo-exploration 无 → 验证降级）。
断言：体形状/metadata 契约/深度切片地理参考/targets_3d/不确定性诚实性/broker 发现。
"""

import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.dirname(HERE)
REPO = os.path.dirname(SVC)
sys.path.insert(0, SVC)
sys.path.insert(0, REPO)

import numpy as np
import rasterio

from core.model3d_engine import Model3DEngine
from config.config import Config

AOI = "辽宁本溪市铜钼矿"
BBOX = [125.43555556, 41.155, 125.4475, 41.16388889]


def main():
    out = os.path.join(tempfile.gettempdir(), "m3d_verify", AOI, "model3d", "20260609_000000_t")
    shutil.rmtree(os.path.join(tempfile.gettempdir(), "m3d_verify"), ignore_errors=True)

    res = Model3DEngine.run(AOI, "铜钼", BBOX, out, params={"top_n": 15},
                            roots=Config.upstream_roots())
    ms = res["model_stats"]
    print("family:", ms["family"], "| applicability:", ms["family_applicability"],
          "| depth_km:", ms["depth_km_band"])

    # 1) family 正确（斑岩型铜钼矿 → porphyry）
    assert ms["family"] == "porphyry", ms["family"]
    assert ms["deposit_type"] == "斑岩型铜钼矿"

    # 2) metadata 契约
    meta = json.load(open(res["metadata_path"], encoding="utf-8"))
    for k in ("source", "source_version", "aoi_name", "aoi_bbox", "crs", "created_at",
              "products", "model_stats"):
        assert k in meta, f"metadata 缺 {k}"
    assert meta["source"] == "geo-model3d"
    # data_sources 如实记录（深度缺失）
    assert meta["model_stats"]["data_sources"]["exploration"]["status"] == "missing"
    assert meta["model_stats"]["data_sources"]["alteration"]["status"] == "ok"
    print("data_sources:", {k: v.get("status") for k, v in meta["model_stats"]["data_sources"].items()})

    # 3) 深度切片 GeoTIFF 含地理参考
    slice_dir = os.path.join(out, "depth_slices")
    tifs = sorted([f for f in os.listdir(slice_dir) if f.endswith(".tif")])
    assert len(tifs) >= 10, len(tifs)
    with rasterio.open(os.path.join(slice_dir, tifs[0])) as ds:
        assert ds.crs is not None and ds.crs.to_epsg() == 32651, ds.crs
        assert ds.transform is not None
    print(f"深度切片: {len(tifs)} 张，CRS={ds.crs}")

    # 4) NetCDF 体形状
    import xarray as xr
    ds = xr.open_dataset(os.path.join(out, meta["products"]["prospectivity_volume_nc"]))
    assert "prospectivity" in ds and "uncertainty" in ds
    nz, ny, nx = ds["prospectivity"].shape
    print(f"体形状 (nz,ny,nx)=({nz},{ny},{nx})")
    assert nz == 20

    # 5) targets_3d 非空且字段完整
    tj = json.load(open(os.path.join(out, "targets_3d.json"), encoding="utf-8"))
    assert tj["n"] > 0
    t0 = tj["targets"][0]
    for k in ("rank", "lon", "lat", "depth_m", "score", "uncertainty"):
        assert k in t0
    print(f"靶点: {tj['n']} 个，rank1 深度 {t0['depth_m']}m score {t0['score']}")

    # 6) 诚实性：深部不确定性 > 浅部；靶点深度落在/接近成矿深度带(porphyry 1-4km)
    us = ms["uncertainty_stats"]
    assert us["deepest_mean"] > us["surface_mean"], us
    depths = [t["depth_m"] for t in tj["targets"]]
    assert max(depths) >= 800, depths  # 靶点不再全堆在地表
    print(f"不确定性: 地表 {us['surface_mean']} < 最深 {us['deepest_mean']} ✓；靶点深度 {sorted(set(depths))}")

    # 7) broker 发现（把 run 放到一个临时 results 根，用 find_model3d_for_bbox）
    fake_root = os.path.join(tempfile.gettempdir(), "m3d_verify")
    sys.path.insert(0, REPO)
    from commons.model3d_broker import find_model3d_for_bbox, get_product_path
    found = find_model3d_for_bbox(tuple(BBOX), fake_root)
    assert found, "broker 未发现产物"
    e = found[0]
    assert e["aoi_name"] == AOI and e["model_stats"]["family"] == "porphyry"
    pp = get_product_path(e, "depth_profile_png")
    assert pp and os.path.exists(pp)
    print(f"broker 发现 OK：aoi={e['aoi_name']} bbox={e['aoi_bbox']}")

    print("\n✅ T10 端到端验证全部通过")


if __name__ == "__main__":
    main()
