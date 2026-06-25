"""布孔图件 PNG（matplotlib，CJK 字体）：有利度底图(深度方向最大) + 计划孔 + 见矿/无矿。"""

from __future__ import annotations

import os
from typing import Dict, List

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from pyproj import Transformer

_CJK = ["PingFang SC", "Heiti SC", "STHeiti", "Arial Unicode MS", "Microsoft YaHei",
        "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei"]
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _f in _CJK:
    if _f in _avail:
        plt.rcParams["font.sans-serif"] = [_f]
        break
plt.rcParams["axes.unicode_minus"] = False


def render_siting_map(out_dir: str, fav: Dict, holes: List[Dict],
                      judged: List[Dict] = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    prosp = fav["prospectivity"]
    best2d = np.nanmax(np.where(np.isfinite(prosp), prosp, np.nan), axis=0)
    x = fav["x"]; y = fav["y"]; epsg = int(fav["epsg"])
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)
    lon0, lat0 = tr.transform(float(x[0]), float(y[0]))
    lon1, lat1 = tr.transform(float(x[-1]), float(y[-1]))
    extent = [min(lon0, lon1), max(lon0, lon1), min(lat0, lat1), max(lat0, lat1)]

    fig, ax = plt.subplots(figsize=(7, 6))
    finite = best2d[np.isfinite(best2d)]
    vmin, vmax = (np.percentile(finite, [2, 98]) if finite.size else (0, 1))
    im = ax.imshow(best2d, extent=extent, origin="upper", cmap="turbo",
                   vmin=vmin, vmax=vmax, aspect="auto")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="三维有利度(深度方向最大)")
    # 计划孔
    if holes:
        ax.scatter([h["lon"] for h in holes], [h["lat"] for h in holes],
                   s=40, facecolors="none", edgecolors="white", linewidths=1.3,
                   marker="o", label="AI 计划孔")
        for h in holes[:10]:
            ax.annotate(str(h["rank"]), (h["lon"], h["lat"]), color="white",
                        fontsize=7, ha="center", va="center")
    # 见矿/无矿
    if judged:
        ore = [(r["lon"], r["lat"]) for r in judged if r.get("outcome") == "ore" and r.get("lon")]
        bar = [(r["lon"], r["lat"]) for r in judged if r.get("outcome") == "barren" and r.get("lon")]
        if ore:
            ax.scatter([p[0] for p in ore], [p[1] for p in ore], s=55, c="#2ecc71",
                       marker="*", edgecolors="black", linewidths=0.5, label="见矿")
        if bar:
            ax.scatter([p[0] for p in bar], [p[1] for p in bar], s=40, c="#e74c3c",
                       marker="x", linewidths=1.2, label="无矿")
    ax.set_title("AI 辅助布孔（有利度 + 不确定性）")
    ax.set_xlabel("经度"); ax.set_ylabel("纬度"); ax.legend(loc="upper right", fontsize=8)
    fn = os.path.join(out_dir, "siting_map.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn


def render_holes_table_png(out_dir: str, holes: List[Dict]) -> str:
    """钻孔信息一览表（matplotlib 表格图）：每行一个计划孔，列含坐标/方位/倾角/得分/优先级。
    风格与三维图统一，便于对照三维布孔图查看每个孔的具体参数。"""
    from outputs.writers import HOLE_TABLE_COLUMNS
    os.makedirs(out_dir, exist_ok=True)
    fn = os.path.join(out_dir, "holes_table.png")

    headers = [zh for (_k, zh, _fmt) in HOLE_TABLE_COLUMNS]
    rows = [[fmt(h.get(key)) for (key, _zh, fmt) in HOLE_TABLE_COLUMNS] for h in holes]
    # 行底色按优先级（与 Web UI 一致：A 绿 / B 黄 / C 灰），淡化为浅色底
    _pc = {"A": "#dff5ea", "B": "#fcf3da", "C": "#eceef2"}
    n = max(1, len(rows))

    fig, ax = plt.subplots(figsize=(11, 1.0 + 0.32 * n))
    ax.axis("off")
    if not rows:
        ax.text(0.5, 0.5, "无计划孔", ha="center", va="center")
        fig.savefig(fn, dpi=120); plt.close(fig)
        return fn

    tbl = ax.table(cellText=rows, colLabels=headers, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1.0, 1.4)
    pri_idx = len(HOLE_TABLE_COLUMNS) - 1  # 优先级列
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#cdd5df")
        if r == 0:                                   # 表头
            cell.set_facecolor("#222b36"); cell.set_text_props(color="white", weight="bold")
        else:
            pri = rows[r - 1][pri_idx]
            cell.set_facecolor(_pc.get(pri, "#ffffff"))

    ax.set_title("钻孔信息一览表（按优先级排序，对照三维布孔图查看）", fontsize=11, pad=12)
    fig.tight_layout(); fig.savefig(fn, dpi=120, bbox_inches="tight"); plt.close(fig)
    return fn


def render_siting_3d_png(out_dir: str, fav: Dict, holes: List[Dict],
                         judged: List[Dict] = None) -> str:
    """3D 静态图：有利度面铺在地表 + 计划孔作彩色钻杆向地下延伸（按 score 着色）。
    始终可用（不依赖 CDN），让页面立刻看到布孔的三维效果。"""
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    os.makedirs(out_dir, exist_ok=True)
    prosp = fav["prospectivity"]
    best2d = np.nanmax(np.where(np.isfinite(prosp), prosp, np.nan), axis=0)  # (ny,nx)
    x = np.asarray(fav["x"], float); y = np.asarray(fav["y"], float)
    epsg = int(fav["epsg"])
    ny, nx = best2d.shape
    cx, cy = 0.5 * (x[0] + x[-1]), 0.5 * (y[0] + y[-1])
    # 地表有利度面（下采样到 <=80×80，米制相对坐标，单位 km）
    sx = max(1, nx // 80); sy = max(1, ny // 80)
    E = (x[::sx] - cx) / 1000.0
    N = (y[::sy] - cy) / 1000.0
    EE, NN = np.meshgrid(E, N)
    Z2 = best2d[::sy, ::sx]
    Z2f = np.where(np.isfinite(Z2), Z2, 0.0)
    vmin, vmax = (np.nanpercentile(best2d, [2, 98]) if np.isfinite(best2d).any() else (0, 1))
    norm = plt.Normalize(vmin, vmax)
    colors = plt.cm.turbo(norm(Z2f))

    fig = plt.figure(figsize=(8, 6.5))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_surface(EE, NN, np.zeros_like(EE), facecolors=colors, rstride=1, cstride=1,
                    shade=False, alpha=0.55, linewidth=0, antialiased=False)

    # 计划孔 → 钻杆（地表 0 → 目标深度，向下为负 km），按 score 着色，标注 编号/深度/见矿
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    svals = [h.get("score", 0) for h in holes] or [0]
    snorm = plt.Normalize(min(svals), max(svals) if max(svals) > min(svals) else min(svals) + 1e-6)
    oc_map = {r.get("hole_id"): r.get("outcome") for r in (judged or [])}
    _cn = {"ore": "见矿", "barren": "无矿"}
    _lc = {"见矿": "#2ecc71", "无矿": "#e74c3c", "未钻": "#dfe7f5"}
    for h in holes:
        ux, uy = tr.transform(h["lon"], h["lat"])
        e = (ux - cx) / 1000.0; n = (uy - cy) / 1000.0
        depth = abs(h.get("target_depth_m", 0))
        traj = h.get("trajectory") or []
        if len(traj) >= 2:                       # 斜孔：孔底取轨迹终点
            bx, by = tr.transform(traj[-1]["lon"], traj[-1]["lat"])
            be = (bx - cx) / 1000.0; bn = (by - cy) / 1000.0
            bdz = -abs(traj[-1].get("depth_m", depth)) / 1000.0
        else:
            be, bn, bdz = e, n, -depth / 1000.0
        col = plt.cm.plasma(snorm(h.get("score", 0)))
        ax.plot([e, be], [n, bn], [0, bdz], color=col, lw=2.2, alpha=0.95)
        ax.scatter([e], [n], [0], color=col, s=28, edgecolors="white", linewidths=0.6, depthshade=False)
        ax.scatter([be], [bn], [bdz], color=col, s=14, marker="v", depthshade=False)
        oc = _cn.get(oc_map.get(h.get("hole_id")), "未钻")
        dip = h.get("dip_deg", -90)
        tag = f"{h.get('hole_id','')} {int(depth)}m {oc}" + ("" if abs(dip + 90) < 1 else f" {int(dip)}°")
        ax.text(e, n, 0.06, tag, fontsize=5.5, color=_lc.get(oc, "#dfe7f5"), ha="center", va="bottom")
    # 见矿/无矿标在孔口
    if judged:
        for r in judged:
            if r.get("lon") is None:
                continue
            ux, uy = tr.transform(r["lon"], r["lat"])
            e = (ux - cx) / 1000.0; n = (uy - cy) / 1000.0
            if r.get("outcome") == "ore":
                ax.scatter([e], [n], [0.02], color="#2ecc71", s=70, marker="*",
                           edgecolors="black", linewidths=0.5, depthshade=False)
            elif r.get("outcome") == "barren":
                ax.scatter([e], [n], [0.02], color="#e74c3c", s=45, marker="x",
                           linewidths=1.5, depthshade=False)

    ax.set_xlabel("东 (km)"); ax.set_ylabel("北 (km)"); ax.set_zlabel("深度 (km)")
    ax.set_title("三维布孔：有利度地表面 + 计划孔钻杆（按预测得分着色）")
    ax.view_init(elev=22, azim=-60)
    m = plt.cm.ScalarMappable(cmap="turbo", norm=norm)
    fig.colorbar(m, ax=ax, shrink=0.55, pad=0.08, label="地表有利度")
    fn = os.path.join(out_dir, "siting_3d.png")
    fig.tight_layout(); fig.savefig(fn, dpi=120); plt.close(fig)
    return fn
