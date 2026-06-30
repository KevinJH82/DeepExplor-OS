"""
光谱波形匹配 SAM (Spectral Angle Mapper)  —— 路线图 P1-b

书《遥感图像处理技术及应用》(张晔, 2024) §9.5.2。把高光谱(EnMAP/PRISMA)逐像元光谱
与 USGS 权威波谱库 splib07 的矿物参考谱比对,以光谱夹角(SAM)度量波形相似度,
逐像元识别矿物。与 P1-a(SASP,看吸收形态)互补:SAM 看整体波形与已知矿物的吻合度。

参考库: geo-analyser/data/splib07_reflib.json —— 由 USGS Spectral Library Version 7
(Kokaly et al. 2017, DOI:10.5066/F7RR1WDJ) splib07a ASD 谱抽取 19 种关键蚀变矿物,
重采样到 0.40–2.50µm@10nm 统一网格。运行时再插值到影像实际波长。
可经环境变量 SPLIB07_REFLIB 指向自建/更全的库。

SAM 定义: α = arccos( <x,r> / (|x|·|r|) ),α∈[0,π/2],越小越像。
输出相似度 sim = 1 - α/(π/2) ∈ [0,1],越大越匹配(作异常高值)。SAM 与向量模无关,
对地形/光照增益不敏感。
"""

from __future__ import annotations

import os
import json
import numpy as np
from typing import Dict, Optional, Tuple

_REFLIB_PATH = os.environ.get(
    "SPLIB07_REFLIB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "splib07_reflib.json"),
)

# 中文矿物名(DB) → splib 参考库键。多对一(同族归并)。
_MINERAL_TO_SPLIB = {
    "绢云母": "muscovite", "白云母": "muscovite", "云母": "muscovite",
    "伊利石": "illite",
    "高岭石": "kaolinite", "高岭土": "kaolinite", "粘土": "kaolinite",
    "蒙脱石": "montmorillonite",
    "明矾石": "alunite",
    "叶腊石": "pyrophyllite",
    "地开石": "dickite",
    "埃洛石": "halloysite", "埃洛": "halloysite",
    "绿泥石": "chlorite",
    "绿帘石": "epidote",
    "角闪石": "actinolite", "阳起石": "actinolite", "次闪石": "actinolite",
    "蛇纹石": "serpentine",
    "滑石": "talc",
    "方解石": "calcite", "碳酸盐": "calcite",
    "白云石": "dolomite", "铁白云石": "dolomite",
    "黄钾铁矾": "jarosite",
    "赤铁矿": "hematite",
    "褐铁矿": "goethite", "针铁矿": "goethite",
    "菱铁矿": "siderite",
}

_LIB_CACHE: Optional[Dict] = None


def _load_lib() -> Dict:
    global _LIB_CACHE
    if _LIB_CACHE is None:
        with open(_REFLIB_PATH, "r", encoding="utf-8") as f:
            _LIB_CACHE = json.load(f)
    return _LIB_CACHE


def resolve_splib_key(mineral_name: str) -> Optional[str]:
    """中文矿物名 → splib 库键。先精确,再按关键词子串匹配。"""
    if not mineral_name:
        return None
    lib = _load_lib()["minerals"]
    if mineral_name in _MINERAL_TO_SPLIB and _MINERAL_TO_SPLIB[mineral_name] in lib:
        return _MINERAL_TO_SPLIB[mineral_name]
    for kw, key in _MINERAL_TO_SPLIB.items():
        if kw in mineral_name and key in lib:
            return key
    return None


def reference_spectrum(splib_key: str, wavelengths_um: np.ndarray) -> Optional[np.ndarray]:
    """取某矿物参考谱并插值到给定影像波长(µm)。波长超出库网格的端点用边界值。"""
    lib = _load_lib()
    m = lib["minerals"].get(splib_key)
    if not m:
        return None
    grid = np.asarray(lib["grid_um"], dtype=np.float64)
    refl = np.asarray(m["reflectance"], dtype=np.float64)
    return np.interp(np.asarray(wavelengths_um, dtype=np.float64), grid, refl).astype(np.float32)


def match_sam(
    cube: np.ndarray,
    wavelengths_um: np.ndarray,
    ref: np.ndarray,
    roi_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """逐像元 SAM 相似度图 = 1 - α/(π/2) ∈[0,1]。cube:(B,H,W), ref:(B,)。
    仅用同时有限的波段;有效波段<3 或像元全零→NaN。"""
    B, H, W = cube.shape
    x = cube.reshape(B, -1).astype(np.float32)          # (B, N)
    r = np.asarray(ref, dtype=np.float32).reshape(B, 1)  # (B,1)
    finite = np.isfinite(x) & np.isfinite(r)
    x = np.where(finite, x, 0.0)
    rr = np.where(finite, r, 0.0)

    dot = (x * rr).sum(axis=0)                            # (N,)
    nx = np.sqrt((x * x).sum(axis=0))
    nr = np.sqrt((rr * rr).sum(axis=0))
    nband = finite.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cos = dot / (nx * nr)
    cos = np.clip(cos, -1.0, 1.0)
    angle = np.arccos(cos)                                # [0,π/2]
    sim = 1.0 - angle / (np.pi / 2.0)
    sim = np.where((nband >= 3) & (nx > 1e-6) & (nr > 1e-6), sim, np.nan)
    sim = sim.reshape(H, W).astype(np.float32)
    if roi_mask is not None:
        sim = np.where(roi_mask, sim, np.nan).astype(np.float32)
    return sim


def sam_index(
    cube: np.ndarray,
    wavelengths_um: np.ndarray,
    mineral_name: str,
    roi_mask: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, Optional[str]]:
    """高层入口:按矿物名取 splib 参考谱并算 SAM 相似度图。
    返回 (sim_map, splib_key)。矿物不在库 / 无波长 → 全 NaN, key=None。"""
    H, W = cube.shape[1], cube.shape[2]
    nan = np.full((H, W), np.nan, dtype=np.float32)
    if wavelengths_um is None:
        return nan, None
    key = resolve_splib_key(mineral_name)
    if key is None:
        return nan, None
    ref = reference_spectrum(key, wavelengths_um)
    if ref is None:
        return nan, None
    return match_sam(cube, wavelengths_um, ref, roi_mask), key


def available_minerals() -> list:
    return sorted(_load_lib()["minerals"].keys())
