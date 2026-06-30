"""
端元自动提取 N-FINDR / VCA  —— 路线图 P3-a

书《遥感图像处理技术及应用》(张晔, 2024) §9.3.2。线性混合模型把像元反射率看作若干"端元"
(纯地物光谱)按丰度的线性组合。现有 spectral_unmix.estimate_endmembers 用 NDVI/BSI 极值
启发式取 绿植/土岩/阴影 三端元 —— 语义固定、非真·矿物端元。本模块用几何方法从数据本身
自动提纯端元(单形体顶点 = 最纯像元),使 NNLS 解混的丰度具地质含义。

  - VCA (Vertex Component Analysis, Nascimento&Dias 2005): 迭代取与已选端元张成子空间正交
    方向上的极值像元为新端元。鲁棒、快,默认。
  - N-FINDR (Winter 1999): 在降维空间内最大化端元构成的单形体体积。本实现对候选像元做凸包式
    极值子采样以控成本。

依赖: 仅 numpy。叶子模块。返回的端元为影像原始波段空间的纯像元光谱 (B, q)。
"""

from __future__ import annotations

import numpy as np
from typing import List, Optional, Tuple


def _prep(image: np.ndarray, roi_mask: Optional[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    """(B,H,W) → (B,N_valid) 有限且 ROI 内的像元,返回 (Y, valid_flat_idx)。"""
    B = image.shape[0]
    flat = image.reshape(B, -1).astype(np.float64)
    valid = np.isfinite(flat).all(axis=0)
    if roi_mask is not None:
        valid &= roi_mask.reshape(-1)
    return flat[:, valid], np.where(valid)[0]


def vca(image: np.ndarray, q: int, roi_mask: Optional[np.ndarray] = None,
        seed: int = 0) -> Tuple[np.ndarray, List[int]]:
    """VCA 端元提取。返回 (E (B,q) 端元光谱, 像元在 flatten 后的索引列表)。"""
    Y, vidx = _prep(image, roi_mask)
    L, N = Y.shape
    if N < q:
        raise ValueError(f"有效像元 {N} < 端元数 {q}")
    rng = np.random.default_rng(seed)

    Y_m = Y.mean(axis=1, keepdims=True)
    Y_o = Y - Y_m
    cov = np.cov(Y_o) + 1e-9 * np.eye(L)
    U = np.linalg.svd(cov)[0]

    # SNR 估计决定投影方式
    Ud = U[:, :q]
    x_p = Ud.T @ Y_o
    P_y = np.mean(np.sum(Y ** 2, axis=0))
    P_x = np.mean(np.sum(x_p ** 2, axis=0)) + np.sum(Y_m ** 2)
    denom = (P_y - P_x)
    SNR = 10 * np.log10((P_x - (q / L) * P_y) / denom) if denom > 1e-12 else 1e3
    SNR_th = 15.0 + 10.0 * np.log10(q)

    if SNR < SNR_th:                       # 低 SNR:仿射投影(q-1 维 + 常数行)
        Ud = U[:, :q - 1]
        x = Ud.T @ Y_o
        c = np.max(np.sqrt(np.sum(x ** 2, axis=0)))
        y = np.vstack([x, c * np.ones((1, N))])
    else:                                   # 高 SNR:射影
        Ud = U[:, :q]
        x = Ud.T @ Y
        u = x.mean(axis=1)
        scale = (u[None, :] @ x).ravel()
        y = x / (scale + 1e-12)

    indices = np.zeros(q, dtype=int)
    A = np.zeros((q, q)); A[-1, 0] = 1.0
    for i in range(q):
        w = rng.random(q)
        f = w - A @ np.linalg.pinv(A) @ w
        nf = np.linalg.norm(f)
        f = f / (nf + 1e-12)
        proj = f @ y
        idx = int(np.argmax(np.abs(proj)))
        A[:, i] = y[:, idx]
        indices[i] = idx

    E = Y[:, indices].astype(np.float32)             # (B,q)
    return E, [int(vidx[i]) for i in indices]


def nfindr(image: np.ndarray, q: int, roi_mask: Optional[np.ndarray] = None,
           seed: int = 0, max_iter: int = 3, candidate_cap: int = 4000) -> Tuple[np.ndarray, List[int]]:
    """N-FINDR 端元提取(候选像元子采样控成本)。返回 (E (B,q), 索引)。"""
    Y, vidx = _prep(image, roi_mask)
    L, N = Y.shape
    if N < q:
        raise ValueError(f"有效像元 {N} < 端元数 {q}")
    rng = np.random.default_rng(seed)

    # 降到 q-1 维
    Y_m = Y.mean(axis=1, keepdims=True)
    Ud = np.linalg.svd(np.cov(Y - Y_m) + 1e-9 * np.eye(L))[0][:, :q - 1]
    X = Ud.T @ (Y - Y_m)                              # (q-1, N)

    # 候选像元:取各主轴极值 + 随机子采样(凸包顶点多在极值处)
    cand = set()
    for d in range(q - 1):
        cand.add(int(np.argmin(X[d]))); cand.add(int(np.argmax(X[d])))
    if N > candidate_cap:
        cand.update(rng.choice(N, candidate_cap, replace=False).tolist())
    else:
        cand.update(range(N))
    cand = np.array(sorted(cand))
    Xc = X[:, cand]                                   # (q-1, M)

    sel = rng.choice(len(cand), q, replace=False)     # 初始 q 顶点(候选内索引)

    def aug(cols):
        return np.vstack([np.ones((1, len(cols))), Xc[:, cols]])       # (q,q)

    vol = abs(np.linalg.det(aug(sel)))
    for _ in range(max_iter):
        improved = False
        for j in range(q):
            best_idx, best_vol = sel[j], vol
            for m in range(len(cand)):
                trial = sel.copy(); trial[j] = m
                v = abs(np.linalg.det(aug(trial)))
                if v > best_vol:
                    best_vol, best_idx = v, m
            if best_idx != sel[j]:
                sel[j] = best_idx; vol = best_vol; improved = True
        if not improved:
            break

    flat_idx = cand[sel]
    E = Y[:, flat_idx].astype(np.float32)
    return E, [int(vidx[i]) for i in flat_idx]


def extract_endmembers(image: np.ndarray, q: int, method: str = "vca",
                       roi_mask: Optional[np.ndarray] = None, seed: int = 0
                       ) -> Tuple[np.ndarray, List[str], List[int]]:
    """统一入口。返回 (E (B,q), names, flat_indices)。names 为 endmember_1..q(几何端元无先验语义)。"""
    if method == "nfindr":
        E, idx = nfindr(image, q, roi_mask, seed)
    else:
        E, idx = vca(image, q, roi_mask, seed)
    names = [f"endmember_{i+1}" for i in range(q)]
    return E, names, idx
