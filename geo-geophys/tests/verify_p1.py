#!/usr/bin/env python3
"""geo-geophys P1 端到端验证。

测试 AOI：辽宁阜新市铁矿（铁/磁铁矿——磁法最相关；prospector 物探产物齐全）。
断言：位场处理 GeoTIFF/欧拉磁源深度/ANT 速度体接入/broker 发现。
"""

import os
import sys
import json
import glob
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.dirname(HERE)
REPO = os.path.dirname(SVC)
sys.path.insert(0, SVC)
sys.path.insert(0, REPO)

import numpy as np
import rasterio
import xarray as xr

from core.geophys_engine import GeophysEngine
from config.config import Config

AOI = "辽宁阜新市铁矿"


def _aoi_bbox():
    mp = glob.glob(os.path.join(REPO, "geo-stru", "results", AOI, "structural", "*", "metadata.json"))
    return json.load(open(mp[0], encoding="utf-8"))["aoi_bbox"]


def main():
    bbox = _aoi_bbox()
    root = os.path.join(tempfile.gettempdir(), "geophys_verify")
    shutil.rmtree(root, ignore_errors=True)
    out = os.path.join(root, AOI, "geophys", "20260609_000000_t")

    # 合成 ANT 速度体（测接入槽）
    lon = np.linspace(bbox[0]-0.25, bbox[2]+0.25, 16)
    lat = np.linspace(bbox[1]-0.18, bbox[3]+0.18, 14)
    depth = np.linspace(0, 2000, 9)
    rng = np.random.default_rng(2)
    vs = 2800 + 300*np.sin(np.linspace(0, 6, 9))[:, None, None] + 150*rng.standard_normal((9, 14, 16))
    vnc = os.path.join(tempfile.gettempdir(), "verify_vs.nc")
    xr.Dataset({"vs": (("depth", "lat", "lon"), vs.astype("float32"))},
               coords={"depth": depth, "lat": lat, "lon": lon}).to_netcdf(vnc)

    res = GeophysEngine.run(AOI, "铁", bbox, out,
                            params={"euler_si": 1.0, "velocity_path": vnc},
                            roots=Config.upstream_roots())
    ms = res["model_stats"]
    print("磁数据:", ms["data_sources"]["magnetic"].get("status"),
          ms["data_sources"]["magnetic"].get("shape"))
    print("IGRF:", ms["igrf"], "Euler:", ms["euler"])

    # 1) 位场处理 GeoTIFF 含地理参考
    for key in ("magnetic_rtp", "magnetic_analytic_signal", "magnetic_tilt"):
        p = os.path.join(out, res["products"][key])
        assert os.path.exists(p), p
        with rasterio.open(p) as ds:
            assert ds.crs is not None and ds.transform is not None
    print("位场 GeoTIFF + 地理参考 OK")

    # 2) 欧拉磁源深度非空且量级合理（区域 km 级）+ 置信度/聚类字段
    assert ms["euler"]["n_points"] > 0
    fc = json.load(open(os.path.join(out, "euler_sources.geojson"), encoding="utf-8"))
    depths = [f["properties"]["depth_m"] for f in fc["features"]]
    assert all(100 <= d <= 20000 for d in depths)
    # 每个点带 confidence∈[0,1]、cluster_id
    confs = [f["properties"]["confidence"] for f in fc["features"]]
    assert all(0.0 <= c <= 1.0 for c in confs), "confidence 越界"
    assert all("cluster_id" in f["properties"] for f in fc["features"]), "缺 cluster_id"
    # metadata 新字段
    assert ms["euler"]["n_clusters"] >= 1
    assert ms["euler"]["mean_confidence"] is not None
    assert ms["euler"]["depth_p25_m"] <= ms["euler"]["depth_median_m"] <= ms["euler"]["depth_p75_m"]
    # 簇产物存在，每簇带深度带 σ + 置信度
    cl_fc = json.load(open(os.path.join(out, "euler_clusters.geojson"), encoding="utf-8"))
    assert len(cl_fc["features"]) == ms["euler"]["n_clusters"]
    for cf in cl_fc["features"]:
        assert cf["properties"]["depth_sigma_m"] > 0
        assert 0.0 <= cf["properties"]["confidence"] <= 1.0
    print(f"欧拉磁源: {len(depths)} 点 → {ms['euler']['n_clusters']} 簇，深度 "
          f"{min(depths):.0f}~{max(depths):.0f}m 中位 {np.median(depths):.0f}m "
          f"(IQR {ms['euler']['depth_p25_m']}~{ms['euler']['depth_p75_m']}m)，"
          f"平均置信度 {ms['euler']['mean_confidence']}")

    # 3) AS≥0（解析信号物理约束）
    with rasterio.open(os.path.join(out, res["products"]["magnetic_analytic_signal"])) as ds:
        a = ds.read(1)
        assert np.nanmin(a) >= -1e-6
    print("解析信号 AS≥0 ✓")

    # 4) ANT 速度体接入
    assert ms["velocity_model"]["status"] == "ok"
    vp = os.path.join(out, res["products"]["velocity_volume_nc"])
    dv = xr.open_dataset(vp)
    assert "vs" in dv and "vs_favorability" in dv
    assert float(dv["vs_favorability"].max()) <= 1.0 + 1e-6
    print("ANT 速度体接入 OK，shape:", tuple(dv["vs_favorability"].shape))

    # 5) broker 发现 + 闭环：load_euler_sources 优先返回簇，带 confidence/depth_sigma_m
    from commons.geophys_broker import find_geophys_for_bbox, load_euler_sources, get_product_path
    found = find_geophys_for_bbox(tuple(bbox), root)
    assert found and found[0]["aoi_name"] == AOI
    srcs = load_euler_sources(found[0])
    assert len(srcs) == ms["euler"]["n_clusters"], "应返回磁源簇"
    assert all(s.get("confidence") is not None for s in srcs), "簇缺 confidence"
    assert all(s.get("depth_sigma_m") is not None for s in srcs), "簇缺 depth_sigma_m"
    assert get_product_path(found[0], "magnetic_rtp")
    print(f"geophys_broker 发现 OK，磁源簇 {len(srcs)} 个（带置信度/深度带 σ）")

    print("\n✅ geo-geophys P1 端到端验证全部通过")


if __name__ == "__main__":
    main()
