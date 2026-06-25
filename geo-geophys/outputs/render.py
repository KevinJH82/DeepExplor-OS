"""位场处理图件 PNG（matplotlib，CJK 字体）。"""

from __future__ import annotations

import os
from typing import Dict, List

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

_CJK = ["PingFang SC", "Heiti SC", "STHeiti", "Arial Unicode MS", "Microsoft YaHei",
        "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei"]
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _f in _CJK:
    if _f in _avail:
        plt.rcParams["font.sans-serif"] = [_f]
        break
plt.rcParams["axes.unicode_minus"] = False


def render_field_map(out_dir: str, name: str, arr2d: np.ndarray, fg,
                     title: str, cmap: str = "turbo", euler=None, epsg=None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    bbox = fg.bbox_wgs84
    extent = [bbox[0], bbox[2], bbox[1], bbox[3]]
    fig, ax = plt.subplots(figsize=(6, 5))
    finite = arr2d[np.isfinite(arr2d)]
    vmin, vmax = (np.percentile(finite, [2, 98]) if finite.size else (0, 1))
    im = ax.imshow(arr2d, extent=extent, origin="upper", cmap=cmap,
                   vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_title(title)
    ax.set_xlabel("经度"); ax.set_ylabel("纬度")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    if euler and epsg:
        from pyproj import Transformer
        tr = Transformer.from_crs(epsg, 4326, always_xy=True)
        lons, lats = [], []
        for p in euler:
            lo, la = tr.transform(p["x"], p["y"]); lons.append(lo); lats.append(la)
        ax.scatter(lons, lats, s=14, c="black", marker="x", linewidths=0.8, alpha=0.7)
    fn = os.path.join(out_dir, f"{name}.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn


def render_euler_depth_hist(out_dir: str, euler: List[Dict]) -> str:
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 4))
    if euler:
        d = np.array([p["depth_m"] for p in euler]) / 1000.0
        ax.hist(d, bins=20, color="#2980b9", edgecolor="white")
        p25, p75 = np.percentile(d, [25, 75])
        ax.axvspan(p25, p75, color="#c0392b", alpha=0.12,
                   label=f"IQR {p25:.1f}–{p75:.1f} km")
        ax.axvline(np.median(d), color="#c0392b", ls="--", label=f"中位 {np.median(d):.1f} km")
        ax.legend()
    ax.set_xlabel("磁源深度 (km)"); ax.set_ylabel("点数")
    ax.set_title("欧拉反褶积磁源深度分布（区域尺度）")
    fn = os.path.join(out_dir, "euler_depth_hist.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn
