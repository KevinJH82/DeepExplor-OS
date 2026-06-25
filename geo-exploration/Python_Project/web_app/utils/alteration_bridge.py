"""
alteration_bridge — geo-analyser 蚀变结果接入(B 地表项升级 + A 旁证重排)。

因果纪律(不可违背)
--------------------
蚀变是地表/近地表"结果",不是深部"驱动"。本模块只在两处生效:
- **B**:升级后处理"地表潜力"项里的糙代理(Ferric/Clay/Hydroxy/Fe/NDVI_inv)——因果层级相同。
- **A**:生成"地表蚀变是否佐证深部靶区"的一致性叠层,据此重排 Top-20——不回写 Au_deep、
  不进 final_mask、不进任何深部物理。

所有重依赖(rasterio/shapely/geo_utils)在函数内 import,模块永远可导入;
顶层 API 全部 try/except 由调用方降级:任何失败 → 返回 None / 原值,深部结果照常出。
"""

import os
import json
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List

from .geo_bridge_common import (
    reproject_to_grid, normalize_deposit_key, roi_overlap_frac,
)

# 矿种 → geo-analyser deposit_type 中文键(已从真实 latest.json 核实;含全/半角括号)
DEPOSIT_MAP = {
    'petroleum':   ['常规油藏(微渗漏蚀变模式)', '致密油藏(微渗漏蚀变)'],
    'gas':         ['常规油藏(微渗漏蚀变模式)', '致密油藏(微渗漏蚀变)'],
    'coalbed_gas': ['常规油藏(微渗漏蚀变模式)', '致密油藏(微渗漏蚀变)'],
    'copper':      ['斑岩型铜钼矿'],
    'molybdenum':  ['斑岩型铜钼矿'],
    'copper_gold': ['斑岩型铜钼矿'],
    'iron':        ['矽卡岩型铁矿', 'BIF型铁矿（条带状铁建造）'],
}

PROXY_KEYS = ['Ferric', 'Clay', 'Hydroxy_anomaly', 'Fe_anomaly', 'NDVI_inv']


def _proxies_for(anomaly_type, mineral):
    """由 anomaly_type(自由文本,如 'Fe³⁺ → Fe²⁺ 还原褪色')+ 矿物名,子串匹配出可升级代理。

    anomaly_type 是描述短语而非干净代码,故用子串规则(结合中文矿物名兜底),
    跨矿种通用:Al-OH→Hydroxy、Mg-OH/碳酸盐→Clay、烃类胁迫/红边→NDVI_inv、
    Fe/铁染/褪色→Ferric+Fe_anomaly。无法归类(TIR/钾长石/石榴子石等)→ 空集,跳过。
    """
    s = f"{anomaly_type or ''} {mineral or ''}".lower().replace('（', '(').replace('）', ')')
    out = set()
    if 'al-oh' in s:
        out.add('Hydroxy_anomaly')
    if 'mg-oh' in s:
        out.add('Clay')
    if 'co₃' in s or 'co3' in s or '碳酸盐' in s or 'carbonate' in s:
        out.add('Clay')
    if any(t in s for t in ('红边', 'ndre', '叶绿素', '烃类胁迫', 'vegetation', '植被')):
        out.add('NDVI_inv')
    if any(t in s for t in ('fe³⁺', 'fe3+', 'fe-s', '铁染', '褪色', '赤铁', '黄铁',
                            '磁铁', '黄钾铁矾')):
        out.add('Ferric')
        out.add('Fe_anomaly')
    return out


@dataclass
class AlterationLayers:
    """重采样到 geo-exploration (H,W) 网格(row0=北、ROI 外 NaN)后的蚀变层。"""
    composite_score: Optional[np.ndarray] = None      # A 用:多传感器符号归一聚合分 [0,1]
    proxies: Dict[str, np.ndarray] = field(default_factory=dict)  # B 用:proxy名→定向归一场
    run_id: str = ''
    run_dir: str = ''
    matched_sensors: List[str] = field(default_factory=list)
    coverage_frac: float = 0.0
    weak: bool = False              # 稀疏度门控命中 → A 不重排
    source: str = 'none'            # 'matched' | 'explicit'
    consistency_weight: float = 0.25
    min_mineral_coverage: float = 0.30
    notes: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# run 匹配
# ----------------------------------------------------------------------------
def _read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _find_matching_run(mineral_type, roi_poly_lonlat, alt_cfg, log=print):
    """返回匹配 run 的绝对目录,或 None。优先级:显式指定 > deposit_type+ROI 重叠。"""
    root = alt_cfg.get('results_root')
    if not root or not os.path.isdir(root):
        log(f"蚀变 results_root 不存在: {root}")
        return None, None

    # 1. 显式指定
    explicit = alt_cfg.get('explicit_run_id')
    if explicit:
        cand = explicit if os.path.isabs(explicit) else os.path.join(root, explicit)
        if os.path.exists(os.path.join(cand, 'manifest.json')):
            return cand, 'explicit'
        log(f"蚀变 explicit_run_id 无效: {explicit}")

    # 2. deposit_type 映射 + ROI 重叠
    want_keys = {normalize_deposit_key(k) for k in DEPOSIT_MAP.get((mineral_type or '').lower(), [])}
    if not want_keys:
        log(f"矿种 {mineral_type} 无 deposit_type 映射,跳过蚀变匹配")
        return None, None

    import glob as _glob
    best = None  # (overlap, run_dir)
    for latest_path in _glob.glob(os.path.join(root, '*', 'latest.json')):
        try:
            latest = _read_json(latest_path)
        except Exception:
            continue
        proj_dir = os.path.dirname(latest_path)
        for dep_key, run_rel in latest.items():
            if normalize_deposit_key(dep_key) not in want_keys:
                continue
            run_dir = run_rel if os.path.isabs(run_rel) else os.path.join(root, run_rel)
            man_path = os.path.join(run_dir, 'manifest.json')
            if not os.path.exists(man_path):
                continue
            try:
                man = _read_json(man_path)
                overlap = roi_overlap_frac(roi_poly_lonlat, man.get('roi_geojson'))
            except Exception:
                overlap = 0.0
            if best is None or overlap > best[0]:
                best = (overlap, run_dir)

    if best is None:
        log("未找到 deposit_type 命中的蚀变 run")
        return None, None
    min_ov = float(alt_cfg.get('min_roi_overlap', 0.15))
    if best[0] < min_ov:
        log(f"蚀变 run ROI 重叠 {best[0]:.3f} < {min_ov},跳过")
        return None, None
    return best[1], 'matched'


# ----------------------------------------------------------------------------
# 顶层加载
# ----------------------------------------------------------------------------
def load_alteration_for_run(mineral_type, lonGrid, latGrid, inROI, roi_poly_lonlat,
                            alt_cfg, log=print) -> Optional[AlterationLayers]:
    """匹配 + 重采样 geo-analyser run 到当前网格。未启用/无匹配/异常 → None。"""
    try:
        if not alt_cfg or not alt_cfg.get('enabled'):
            return None
        run_dir, source = _find_matching_run(mineral_type, roi_poly_lonlat, alt_cfg, log=log)
        if run_dir is None:
            return None
        man = _read_json(os.path.join(run_dir, 'manifest.json'))
        shape = inROI.shape

        layers = AlterationLayers(
            run_id=man.get('run_id', os.path.basename(run_dir)),
            run_dir=run_dir, source=source,
            consistency_weight=float(alt_cfg.get('consistency_weight', 0.25)),
            min_mineral_coverage=float(alt_cfg.get('min_mineral_coverage', 0.30)),
        )

        # 稀疏度门控
        total_px = int(man.get('total_roi_pixels', 0) or 0)
        high_px = int(man.get('high_confidence_total_pixels', 0) or 0)
        frac = (high_px / total_px) if total_px > 0 else 0.0
        if total_px < int(alt_cfg.get('min_run_pixels', 2000)) or \
           frac < float(alt_cfg.get('min_high_conf_frac', 0.001)):
            layers.weak = True
            layers.notes.append(f"弱 run(ROI {total_px}px, 高置信占比 {frac:.4f}):A 不重排")
            log(f"蚀变 {layers.run_id}:{layers.notes[-1]}")

        usable = man.get('usable_sensors') or man.get('available_sensors') or []
        layers.matched_sensors = list(usable)

        # ---- A 用:多传感器 composite__score 融合 ----
        comp = man.get('composites') or {}
        score_stack = []
        for sensor in usable:
            rel = (comp.get(sensor) or {}).get('score_tif')
            if not rel:
                continue
            tif = os.path.join(run_dir, rel)
            if not os.path.exists(tif):
                continue
            try:
                arr = reproject_to_grid(tif, lonGrid, latGrid, shape, inROI=inROI,
                                        resampling='bilinear')
                score_stack.append(arr)
            except Exception as e:
                log(f"蚀变 score 重投影失败 {sensor}: {e}")
        if score_stack:
            import warnings
            with np.errstate(all='ignore'), warnings.catch_warnings():
                warnings.simplefilter('ignore', RuntimeWarning)
                fused = np.nanmean(np.stack(score_stack, axis=0), axis=0)
            layers.composite_score = _mat2gray_roi(fused, inROI)
            layers.coverage_frac = float(np.isfinite(layers.composite_score[inROI]).mean())

        # ---- B 用:按 anomaly_type 构建 proxy 升级场 ----
        layers.proxies = _build_proxy_fields(man, run_dir, lonGrid, latGrid, inROI, shape, log)

        if layers.composite_score is None and not layers.proxies:
            log(f"蚀变 {layers.run_id}:无可用 score/proxy 层,降级")
            return None
        log(f"蚀变接入成功:run={layers.run_id} 传感器={layers.matched_sensors} "
            f"覆盖={layers.coverage_frac:.2f} proxy={list(layers.proxies.keys())}")
        return layers
    except Exception as e:
        log(f"蚀变加载异常,降级: {e}")
        return None


def _build_proxy_fields(man, run_dir, lonGrid, latGrid, inROI, shape, log):
    """按 anomaly_type 把 results[] 的 index 定向归一,合并成 proxy→field。"""
    buckets = {k: [] for k in PROXY_KEYS}   # proxy → list[(priority, field)]
    for r in (man.get('results') or []):
        if r.get('data_status') not in (None, 'ok') or r.get('warning'):
            continue
        proxies = _proxies_for(r.get('anomaly_type', ''), r.get('mineral', ''))
        if not proxies:
            continue
        idx_rel = r.get('index_tif')
        if not idx_rel:
            continue
        idx_path = os.path.join(run_dir, idx_rel)
        if not os.path.exists(idx_path):
            continue
        try:
            idx = reproject_to_grid(idx_path, lonGrid, latGrid, shape, inROI=inROI,
                                    resampling='bilinear')
        except Exception as e:
            log(f"蚀变 index 重投影失败 {idx_rel}: {e}")
            continue
        oriented = _orient_index(idx, r, run_dir, lonGrid, latGrid, inROI, shape, log)
        if oriented is None:
            continue
        norm = _mat2gray_roi(oriented, inROI)
        pr = int(r.get('priority', 2) or 2)
        for p in proxies:
            buckets[p].append((pr, norm))

    out = {}
    for p, items in buckets.items():
        if not items:
            continue
        # 按 priority 加权(priority 越小越高,权重 = 1/priority)
        ws = np.array([1.0 / max(pr, 1) for pr, _ in items])
        stack = np.stack([f for _, f in items], axis=0)
        with np.errstate(all='ignore'):
            wsum = (stack * ws[:, None, None])
            denom = np.sum(np.where(np.isfinite(stack), ws[:, None, None], 0.0), axis=0)
            num = np.nansum(wsum, axis=0)
            merged = np.where(denom > 0, num / denom, np.nan)
        out[p] = _mat2gray_roi(merged, inROI)
    return out


def _orient_index(idx, r, run_dir, lonGrid, latGrid, inROI, shape, log):
    """把 index 定向到"蚀变越强=值越高"。优先用 mask 内/外均值,回退 anomaly_ratio/sign。"""
    # 1. mask 定向(method 无关,最稳健)
    mask_rel = r.get('mask_tif')
    if mask_rel:
        mpath = os.path.join(run_dir, mask_rel)
        if os.path.exists(mpath):
            try:
                m = reproject_to_grid(mpath, lonGrid, latGrid, shape, inROI=inROI,
                                      resampling='nearest')
                inside = (m > 0.5) & inROI & np.isfinite(idx)
                outside = (m <= 0.5) & inROI & np.isfinite(idx)
                if inside.sum() >= 5 and outside.sum() >= 5:
                    if np.nanmean(idx[inside]) < np.nanmean(idx[outside]):
                        return -idx
                    return idx
            except Exception as e:
                log(f"蚀变 mask 定向失败 {mask_rel}: {e}")
    # 2. 回退:anomaly_ratio<1 或 pca sign<0 → 取反
    ratio = r.get('anomaly_ratio')
    sign = r.get('sign')
    if (ratio is not None and ratio < 1.0) or (sign is not None and sign < 0):
        return -idx
    return idx


# ----------------------------------------------------------------------------
# B:地表项升级
# ----------------------------------------------------------------------------
def apply_surface_upgrade(proxies, layers, mineral_type, inROI, log=print):
    """用蚀变 proxy 场替换糙代理。覆盖率不足则逐项回退。返回 (新proxies, report)。"""
    report = {}
    out = dict(proxies)
    if layers is None or not layers.proxies:
        return out, report
    for k in PROXY_KEYS:
        alt = layers.proxies.get(k)
        if alt is None:
            report[k] = 'original'
            continue
        cov = float(np.isfinite(alt[inROI]).mean()) if inROI.any() else 0.0
        if cov >= layers.min_mineral_coverage:
            out[k] = alt
            report[k] = f'altered(cov={cov:.2f})'
            log(f"  -> B: {k} ← 蚀变图(覆盖 {cov:.2f})")
        else:
            report[k] = f'original(cov={cov:.2f}<{layers.min_mineral_coverage})'
    return out, report


# ----------------------------------------------------------------------------
# A:一致性叠层 + 重排
# ----------------------------------------------------------------------------
def compute_consistency_overlay(Au_deep, layers, inROI):
    """返回 {alteration_score, corroboration, rerank_score} 或 None。不修改 Au_deep。"""
    if layers is None or layers.composite_score is None:
        return None
    alt = layers.composite_score
    with np.errstate(all='ignore'):
        corroboration = Au_deep * alt
    corr_norm = _mat2gray_roi(corroboration, inROI)
    w = layers.consistency_weight
    rerank = np.full(Au_deep.shape, np.nan, dtype=np.float64)
    m = inROI & np.isfinite(Au_deep)
    # 无蚀变覆盖处 corr_norm 为 NaN → 中性:该处仅用 Au_deep(等价 corr 贡献=Au_deep)
    cn = np.where(np.isfinite(corr_norm), corr_norm, Au_deep)
    rerank[m] = Au_deep[m] * (1.0 - w) + cn[m] * w
    return {'alteration_score': alt, 'corroboration': corroboration, 'rerank_score': rerank}


def rerank_top_anomalies(score, inROI, lonGrid, latGrid, n_top=20):
    """按 score 取 Top-N(复刻 mineral_engine Top-20 逻辑)。返回坐标与索引。"""
    H, W = inROI.shape
    temp = np.array(score, dtype=np.float64, copy=True)
    temp[~inROI] = 0
    temp[np.isnan(temp)] = 0
    flat = temp.ravel()
    n = min(int(n_top), flat.size)
    top_idx = np.argpartition(flat, -n)[-n:]
    top_idx = top_idx[np.argsort(flat[top_idx])[::-1]]
    topY, topX = np.unravel_index(top_idx, (H, W))
    latG_corr = np.flipud(latGrid)   # 与 mineral_engine 一致:图像 row0=北
    lonTop = lonGrid[topY, topX]
    latTop = latG_corr[topY, topX]
    return {'topY': topY, 'topX': topX, 'lonTop': lonTop, 'latTop': latTop, 'redIdx': top_idx}


# ----------------------------------------------------------------------------
def _mat2gray_roi(img, inROI):
    """复用 geo_utils.mat2gray_roi(ROI 内归一 [0,1],外 NaN);导入失败则本地实现。"""
    try:
        from .geo_utils import mat2gray_roi
        return mat2gray_roi(img, inROI)
    except Exception:
        out = np.full(img.shape, np.nan, dtype=np.float64)
        roi = img[inROI]
        roi = roi[np.isfinite(roi)]
        if roi.size == 0:
            return out
        lo, hi = float(np.min(roi)), float(np.max(roi))
        if hi - lo < 1e-12:
            out[inROI] = 0.0
            return out
        out[inROI] = np.clip((img[inROI] - lo) / (hi - lo), 0, 1)
        return out
