"""不确定性量化 —— 与有利度体同形的不确定性体 [0,1]（越高越不可信）。

诚实性原则（P1 核心）：无地下约束区不确定性必须显著偏高。
认知不确定性三因子合成（无真值、无地下观测）：
  1) 证据覆盖度：参与融合的地表证据层越少/像元覆盖越差 → 越不确定。
  2) 深度惩罚：越深、越偏离知识深度带（无真实地下约束）→ 越不确定。
  3) 证据离散度：地表各证据层取值越不一致 → 越不确定。
"""

from __future__ import annotations

from typing import Dict

import numpy as np

from core.evidence import depth_consistency_profile


def uncertainty_volume(surface_layers: Dict[str, np.ndarray], coverage: Dict[str, np.ndarray],
                       grid, depth_km_band, weak_structure: bool = False) -> np.ndarray:
    """合成不确定性体 (nz,ny,nx) ∈ [0,1]。"""
    nz, ny, nx = grid.shape

    # ── 1) 证据覆盖度（xy）──
    surf = [k for k in ("alteration", "structure", "deformation") if k in surface_layers]
    if surf:
        cov_stack = np.stack([coverage.get(k, np.zeros((ny, nx), bool)).astype(np.float32)
                              for k in surf], axis=0)
        cov_frac = cov_stack.sum(axis=0) / 3.0      # 覆盖层数/最大可能(3)
    else:
        cov_frac = np.zeros((ny, nx), dtype=np.float32)
    coverage_uncert = (1.0 - np.clip(cov_frac, 0.0, 1.0))

    # ── 2) 深度惩罚（z）：随深度增长 + 远离知识深度带额外惩罚 ──
    d = np.abs(grid.depths())
    depth_lin = d / max(d.max(), 1.0)               # 0→1
    dc = depth_consistency_profile(grid, depth_km_band)   # (nz,)
    band_miss = 1.0 - dc                            # (nz,)
    depth_uncert = (0.5 * depth_lin + 0.5 * band_miss)[:, None, None] * np.ones((nz, ny, nx), np.float32)

    # ── 3) 证据离散度（xy 层间不一致，广播到 z）──
    if len(surf) >= 2:
        stack = np.stack([np.where(np.isfinite(surface_layers[k]), surface_layers[k], 0.0)
                          for k in surf], axis=0)
        disagree2d = np.clip(stack.std(axis=0) / 0.5, 0.0, 1.0)   # (ny,nx)
        disagree = disagree2d[None, :, :] * np.ones((nz, ny, nx), np.float32)
    else:
        disagree = np.zeros((nz, ny, nx), dtype=np.float32)

    u = (0.40 * coverage_uncert[None, :, :]
         + 0.45 * depth_uncert
         + 0.15 * disagree).astype(np.float32)

    if weak_structure:
        u = np.clip(u + 0.10, 0.0, 1.0)

    return np.clip(u, 0.0, 1.0).astype(np.float32)
