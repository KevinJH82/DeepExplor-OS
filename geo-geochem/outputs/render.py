"""化探异常图件 PNG（matplotlib，CJK 字体）。"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

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


def render_anomaly_map(out_dir: str, name: str, arr2d: np.ndarray, grid,
                       title: str, cmap: str = "turbo") -> str:
    os.makedirs(out_dir, exist_ok=True)
    bbox = grid.bbox_wgs84
    extent = [bbox[0], bbox[2], bbox[1], bbox[3]]
    fig, ax = plt.subplots(figsize=(6, 5))
    finite = arr2d[np.isfinite(arr2d)]
    vmin, vmax = (np.percentile(finite, [2, 98]) if finite.size else (0, 1))
    im = ax.imshow(arr2d, extent=extent, origin="upper", cmap=cmap,
                   vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_title(title)
    ax.set_xlabel("经度"); ax.set_ylabel("纬度")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fn = os.path.join(out_dir, f"{name}.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn


def render_ca_curve(out_dir: str, element: str, ca_curve: Dict) -> Optional[str]:
    """C-A 含量-面积双对数图 + 异常下限位置。"""
    lx = ca_curve.get("log_conc"); la = ca_curve.get("log_area")
    if not lx or not la:
        return None
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(lx, la, "o-", ms=3, color="#2980b9", lw=1)
    thr = ca_curve.get("threshold")
    if thr and thr > 0:
        ax.axvline(np.log10(thr), color="#c0392b", ls="--",
                   label=f"异常下限 {thr:.3g}（{ca_curve.get('method','')}）")
        ax.legend()
    ax.set_xlabel("log10(含量)"); ax.set_ylabel("log10(面积≥含量)")
    ax.set_title(f"{element} 含量-面积(C-A)分形")
    fn = os.path.join(out_dir, f"ca_{element}.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn
