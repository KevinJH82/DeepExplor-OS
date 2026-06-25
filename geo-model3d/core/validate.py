"""相对验证（无真值）：
- surface_consistency：模型地表层与 geo-exploration 2D 有利度的一致性（缺则跳过）。
- weight_sensitivity：扰动知识权重，靶点稳定性（top 靶点保留率）。
不报命中率/C-A（无真实已知矿点，报了即造假）。
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from core.scorers import fuse_surface_2d


def loo_hit_rate(score2d: np.ndarray, known_rowcol, grid) -> Dict:
    """已知矿点捕获率（方向四，仅 WofE 模式有意义）：
    前 X% 有利度面积捕获了多少已知矿点 + 提升度 lift。X% 面积本应随机捕获 X% 点，
    lift>1 即模型优于随机。这是诚实的相对验证（非真留一重训，P1 足够）。"""
    if not known_rowcol:
        return {"status": "no_labels"}
    valid = np.isfinite(score2d)
    vals = score2d[valid]
    if vals.size == 0:
        return {"status": "no_valid_score"}
    ny, nx = score2d.shape
    pt_scores = [float(score2d[r, c]) for (r, c) in known_rowcol
                 if 0 <= r < ny and 0 <= c < nx and np.isfinite(score2d[r, c])]
    if not pt_scores:
        return {"status": "points_outside_valid"}
    out = {"status": "ok", "n_points": len(pt_scores)}
    for top in (10, 20, 30):
        thr = float(np.percentile(vals, 100 - top))
        cap = float(np.mean([1.0 if s >= thr else 0.0 for s in pt_scores]))
        out[f"capture_top{top}pct"] = round(cap, 3)
    out["lift_top10"] = round(out["capture_top10pct"] / 0.10, 2)
    return out


def surface_consistency(score: np.ndarray, grid, es) -> Dict:
    """模型地表层(z=0)与外部 2D 参考(若有)的空间相关。当前 P1：exploration 缺则跳过。"""
    ref = getattr(es, "au_deep_2d", None)
    if ref is None or not np.isfinite(np.asarray(ref, dtype=float)).any():
        return {"status": "no_2d_reference",
                "note": "geo-exploration 2D 有利度缺失，跳过一致性对照（不影响建模）"}
    a = score[0].ravel()
    b = np.asarray(ref, dtype=float).ravel()
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 10:
        return {"status": "insufficient_overlap"}
    r = float(np.corrcoef(a[m], b[m])[0, 1])
    return {"status": "ok", "pearson_r": round(r, 3), "n": int(m.sum())}


def weight_sensitivity(surface_layers: Dict[str, np.ndarray], weights: Dict[str, float],
                       base_targets: List[Dict], grid, perturb: float = 0.2,
                       top_k: int = 10) -> Dict:
    """对各权重 ±perturb 扰动，统计 top_k 靶点 xy 位置保留率（稳健性）。
    靶点 xy 仅由 2D 融合 F_xy 决定（深度门控不改 xy 排序），故在 F_xy 上做。"""
    if not base_targets:
        return {"status": "no_targets"}
    base_set = {(t["lon"], t["lat"]) for t in base_targets[:top_k]}

    def _top_locs(w):
        F, _ = fuse_surface_2d(surface_layers, w)
        flat = F.copy().astype(float); flat[~np.isfinite(flat)] = -1
        idx = np.argsort(flat, axis=None)[::-1][:top_k]
        locs = set()
        for i in idx:
            r, c = divmod(int(i), grid.nx)
            lon, lat = grid.colrow_to_lonlat(c, r)
            locs.add((round(lon, 6), round(lat, 6)))
        return locs

    retentions = []
    for layer in [k for k in weights if weights.get(k, 0) > 0]:
        for sign in (1 + perturb, 1 - perturb):
            w2 = dict(weights); w2[layer] = weights[layer] * sign
            locs = _top_locs(w2)
            # 用网格邻近(±1格)算保留
            keep = 0
            for (lon, lat) in base_set:
                rc = grid.lonlat_to_rowcol(lon, lat)
                if rc is None:
                    continue
                r0, c0 = rc
                hit = any(grid.lonlat_to_rowcol(L[0], L[1]) is not None and
                          abs(grid.lonlat_to_rowcol(L[0], L[1])[0] - r0) <= 1 and
                          abs(grid.lonlat_to_rowcol(L[0], L[1])[1] - c0) <= 1 for L in locs)
                keep += int(hit)
            retentions.append(keep / max(len(base_set), 1))
    return {"status": "ok", "mean_retention": round(float(np.mean(retentions)), 3),
            "n_perturbations": len(retentions)}
