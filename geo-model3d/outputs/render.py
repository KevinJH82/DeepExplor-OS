"""深度切片 / 剖面 PNG 预览（matplotlib）。"""

from __future__ import annotations

import os
from typing import Dict, List

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# 选一个系统可用的 CJK 字体，避免中文显示成方框
_CJK_CANDIDATES = ["PingFang SC", "Heiti SC", "STHeiti", "Arial Unicode MS",
                   "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei"]
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _f in _CJK_CANDIDATES:
    if _f in _avail:
        plt.rcParams["font.sans-serif"] = [_f]
        break
plt.rcParams["axes.unicode_minus"] = False


def render_depth_slices(out_dir: str, score: np.ndarray, grid,
                        slice_depths_m=(250, 750, 1500), targets: List[Dict] = None) -> List[str]:
    """渲染若干代表深度的有利度切片 PNG。"""
    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []
    bbox = grid.bbox_wgs84
    extent = [bbox[0], bbox[2], bbox[1], bbox[3]]
    for dm in slice_depths_m:
        z = grid.depth_index(dm)
        fig, ax = plt.subplots(figsize=(6, 5))
        im = ax.imshow(score[z], extent=extent, origin="upper", cmap="turbo",
                       vmin=0, vmax=1, aspect="auto")
        ax.set_title(f"三维成矿有利度 · 深度 {int(round(abs(grid.depths()[z])))} m")
        ax.set_xlabel("经度"); ax.set_ylabel("纬度")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="有利度")
        if targets:
            tl = [t for t in targets if abs(t["depth_m"] - abs(grid.depths()[z])) < grid.dz_m]
            if tl:
                ax.scatter([t["lon"] for t in tl], [t["lat"] for t in tl],
                           c="white", edgecolors="black", s=40, marker="^")
        fn = os.path.join(out_dir, f"slice_-{int(round(abs(grid.depths()[z]))):04d}m.png")
        fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
        paths.append(fn)
    return paths


def render_depth_profile(out_dir: str, score: np.ndarray, uncertainty: np.ndarray, grid) -> str:
    """渲染随深度的平均有利度/不确定性剖面曲线。"""
    os.makedirs(out_dir, exist_ok=True)
    depths = np.abs(grid.depths())
    # nanmean：按层只对有覆盖体元求均值；否则任一 NaN 列会让整层均值变 NaN，曲线消失
    s = np.nanmean(np.where(np.isfinite(score), score, np.nan).reshape(grid.nz, -1), axis=1)
    u = np.nanmean(np.where(np.isfinite(uncertainty), uncertainty, np.nan).reshape(grid.nz, -1), axis=1)
    fig, ax = plt.subplots(figsize=(4, 6))
    ax.plot(s, depths, "-o", color="#c0392b", label="平均有利度", ms=3)
    ax.plot(u, depths, "-s", color="#2980b9", label="平均不确定性", ms=3)
    ax.invert_yaxis(); ax.set_xlabel("数值 [0,1]"); ax.set_ylabel("深度 (m)")
    ax.set_title("有利度/不确定性—深度剖面"); ax.legend(); ax.grid(alpha=0.3)
    fn = os.path.join(out_dir, "depth_profile.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn
