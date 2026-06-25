"""见矿/无矿判定 —— 闭环回灌的关键一步。

每孔取主指示元素的最大区间品位 vs 截止品位(cutoff)：
  ≥ cutoff           → ore（见矿，正样本）
  有该元素品位但 < cutoff → barren（无矿，真负样本）
  无该元素品位/无 cutoff → unknown（不臆断，不进反馈）
"""

from __future__ import annotations

from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


def _hole_max_grade(hole: Dict, element: str):
    best = None
    for iv in hole.get("intervals", []):
        g = (iv.get("grades") or {}).get(element)
        if g is not None and (best is None or g > best):
            best = g
    return best


def judge_intersection(holes_db: Dict, element: str, cutoff: Optional[float]) -> List[Dict]:
    """holes_db=ingest_core_logs 返回。element=主指示元素；cutoff=截止品位。
    返回 [{hole_id,lon,lat,outcome,max_grade,element,cutoff}]。"""
    out: List[Dict] = []
    holes = (holes_db or {}).get("holes", {})
    for hid, h in holes.items():
        collar = h.get("collar", {})
        mg = _hole_max_grade(h, element) if element else None
        if mg is None or cutoff is None:
            outcome = "unknown"
        elif mg >= cutoff:
            outcome = "ore"
        else:
            outcome = "barren"
        out.append({"hole_id": hid, "lon": collar.get("lon"), "lat": collar.get("lat"),
                    "outcome": outcome, "max_grade": mg, "element": element, "cutoff": cutoff})
    n_ore = sum(1 for r in out if r["outcome"] == "ore")
    n_bar = sum(1 for r in out if r["outcome"] == "barren")
    n_unk = sum(1 for r in out if r["outcome"] == "unknown")
    logger.info(f"judge_intersection: 见矿{n_ore}/无矿{n_bar}/未知{n_unk}（元素={element} cutoff={cutoff}）")
    return out


def resolve_cutoff(mineral: str, element: str, roots: Dict[str, str],
                   bbox, user_cutoff: Optional[float] = None) -> Optional[float]:
    """截止品位：优先用户给定；否则取 data-colle geochem_thresholds 的 moderate_anomaly。"""
    if user_cutoff is not None:
        return float(user_cutoff)
    if not element:
        return None
    try:
        import os, sys
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for repo in (here, "/opt/deepexplor-services"):
            if repo not in sys.path:
                sys.path.insert(0, repo)
        from commons.datacolle_broker import find_datacolle_for_bbox
        ents = find_datacolle_for_bbox(tuple(bbox), roots.get("datacolle", ""))
        if ents:
            th = (ents[0].get("geochem_thresholds") or {}).get(element) \
                or (ents[0].get("geochem_thresholds") or {}).get(element.upper())
            if isinstance(th, dict):
                v = th.get("moderate_anomaly") or th.get("weak_anomaly")
                if v is not None:
                    return float(v)
    except Exception as e:
        logger.info(f"resolve_cutoff 跳过: {e}")
    return None
