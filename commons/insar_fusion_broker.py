"""
insar_fusion_broker.py — 订阅 geo-stru 的 InSAR 形变 × 构造融合输出

与 structural_broker 同思路:纯文件系统订阅,零消息队列,高失败容忍。
下游(geo-exploration / geo-model3d / geo-reporter / geo-orchestrator / trace)
通过本模块按 bbox 相交发现 insar_fusion 产物。

布局现实:geo-stru 的 run_fusion(insar_dir, out_dir, ...) 的 out_dir 由调用方任意指定,
存量产物散落在 results/ 顶层、下划线前缀、命名随意的目录里
(如 results/_东安_insar_fusion/metadata.json)。因此本 broker:
  - 扫描 results/ 下「所有」目录(含 `_` 前缀,这点与 structural_broker 相反);
  - 唯一判定依据是 metadata.json 的 source == "geo-stru-insar-fusion",不依赖目录名;
  - 同时兼容未来规范化的嵌套布局 <dir>/insar_fusion/<run_id>/metadata.json。

被各下游系统共用,放在 commons/。所有发现逻辑只读,不修改 geo-stru 的输出。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_GEO_STRU_OUTPUTS = "/opt/deepexplor-services/geo-stru/results"

_SOURCE_PREFIX = "geo-stru-insar-fusion"


def _load_metadata(fusion_dir: Path) -> Optional[Dict]:
    """读取目录下 metadata.json;仅当 source 为 insar_fusion 时返回,否则 None。"""
    mp = fusion_dir / "metadata.json"
    if not mp.exists():
        return None
    try:
        with open(mp, "r", encoding="utf-8") as f:
            md = json.load(f)
        if not (md.get("source") or "").startswith(_SOURCE_PREFIX):
            return None
        return md
    except Exception:
        return None


def _resolve_product_dir(base_dir: Path):
    """
    定位某 AOI/目录下实际的 insar_fusion 产品目录,兼容三种布局:
      1. 扁平(存量):<base_dir>/metadata.json 自身即融合产物。
      2. 规范嵌套(未来):<base_dir>/insar_fusion/<run_id>/metadata.json,取最新 run。
      3. 半嵌套:<base_dir>/insar_fusion/metadata.json。
    返回 (product_dir, metadata, n_runs);找不到返回 (None, None, 0)。
    """
    if not base_dir.is_dir():
        return None, None, 0

    # 1) 目录自身即扁平融合产物
    flat = _load_metadata(base_dir)
    if flat is not None:
        return base_dir, flat, 1

    # 2/3) 规范嵌套布局 <base_dir>/insar_fusion/...
    sub = base_dir / "insar_fusion"
    if sub.is_dir():
        runs = sorted((d for d in sub.iterdir()
                       if d.is_dir() and (d / "metadata.json").exists()),
                      key=lambda d: d.name, reverse=True)
        if runs:
            latest = runs[0]
            md = _load_metadata(latest)
            if md is not None:
                return latest, md, len(runs)
        sub_flat = _load_metadata(sub)
        if sub_flat is not None:
            return sub, sub_flat, 1

    return None, None, 0


def _entry_from_metadata(product_dir: Path, md: Dict, fallback_name: str,
                         n_runs: int) -> Dict:
    return {
        "aoi_name": md.get("aoi_name") or fallback_name,
        "aoi_bbox": md.get("aoi_bbox"),
        "crs": md.get("crs", "EPSG:4326"),
        "fusion_dir": str(product_dir),
        "metadata_path": str(product_dir / "metadata.json"),
        "products": md.get("products", {}),
        "fusion_stats": md.get("fusion_stats", {}),
        "insar_provenance": md.get("insar_provenance", {}),
        "deposit_inference": md.get("deposit_inference"),
        "data_caveat": md.get("data_caveat"),
        "run_id": md.get("run_id"),
        "n_runs": n_runs,
        "trace_id": md.get("trace_id"),
        "linked_trace_ids": md.get("linked_trace_ids", []),
        "tenant_id": md.get("tenant_id"),
    }


def scan_insar_fusion_outputs(geo_stru_outputs: str = DEFAULT_GEO_STRU_OUTPUTS) -> List[Dict]:
    """
    扫描 geo-stru 输出根,返回所有 InSAR 融合产物(每个目录取最新 run)。
    注意:与 structural_broker 不同,这里「不跳过 `_` 前缀目录」,因为存量融合
    产物正是以 `_` 前缀的顶层目录形式存在,且仅凭 source 串判定。

    Returns
    -------
    [{aoi_name, aoi_bbox, crs, fusion_dir, metadata_path, products,
      fusion_stats, insar_provenance, deposit_inference, data_caveat,
      run_id, n_runs, trace_id, linked_trace_ids, tenant_id}, ...]
    """
    root = Path(geo_stru_outputs)
    if not root.exists():
        return []

    out = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        product_dir, md, n_runs = _resolve_product_dir(d)
        if md is None:
            continue
        out.append(_entry_from_metadata(product_dir, md, d.name, n_runs))
    return out


def _bbox_intersects(a, b) -> bool:
    """两个 [min_lon,min_lat,max_lon,max_lat] 是否相交。"""
    if not a or not b or len(a) < 4 or len(b) < 4:
        return False
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def find_insar_fusion_for_bbox(
    bbox: Tuple[float, float, float, float],
    geo_stru_outputs: str = DEFAULT_GEO_STRU_OUTPUTS,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[Dict]:
    """返回与给定 bbox 相交的所有 geo-stru insar_fusion 产物。

    trace_id（可选）：优先按 trace_id 精确匹配,未命中回退 bbox(与 structural_broker 一致)。
    """
    matches = [a for a in scan_insar_fusion_outputs(geo_stru_outputs)
               if _bbox_intersects(a.get("aoi_bbox"), bbox)]
    try:
        from commons.trace import filter_by_trace_id, filter_by_tenant
        return filter_by_trace_id(filter_by_tenant(matches, tenant_id), trace_id)
    except Exception:
        return matches


def get_product_path(entry: Dict, key: str) -> Optional[str]:
    """从 scan 结果项取某产品(如 'velocity_gradient')的绝对路径,不存在返回 None。

    条件产物(如 goaf_polygons_geojson / deformation_attribution_geojson / ew_* )
    可能未生成 —— 此时返回 None,调用方应优雅降级。
    """
    rel = (entry.get("products") or {}).get(key)
    if not rel:
        return None
    p = Path(entry["fusion_dir"]) / rel
    return str(p) if p.exists() else None
