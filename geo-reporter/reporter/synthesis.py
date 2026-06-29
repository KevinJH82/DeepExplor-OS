"""
synthesis.py — 报告综合环节：靶区推荐图 + A-B-C-D 置信评价（统一研判）

逻辑闭合要点：
- 靶区评级与综合置信由【同一次 claude -p 研判】产出，共用同一套证据输入，二者自洽。
- 仅当存在 geo-model3d 三维成矿预测靶点（真实坐标+有利度+深度）时，才输出带坐标的靶区；
  否则退化为「找矿有利地段/远景区」定性表述，绝不凭空生成精确坐标与套话。
- 研判等级经 _cap_grade 与各维度对账：多数维度低/缺或仅网络资料时不得高于 C。
- LLM 失败时退化为确定性兜底（只用真实靶区；无则给定性说明），不伪造证据。
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader

from .categories import Figure, get_all_categories
from .basemap import render_basemap
from .data_sources import (_import_commons, current_tenant, GEO_STRU_OUTPUTS, DATACOLLE_OUTPUTS,
                           GEO_ANALYSER_OUTPUTS, GEO_EXPLORATION_OUTPUTS,
                           GEO_GEOPHYS_OUTPUTS, GEO_GEOCHEM_OUTPUTS, GEO_MODEL3D_OUTPUTS,
                           GEO_DEPOSITS_OUTPUTS, GEO_DRILL_OUTPUTS,
                           fetch_datacolle_metallogenic, fetch_datacolle_section)


def _bbox(location):
    return (location.min_lon, location.min_lat, location.max_lon, location.max_lat)


# ---------------------------------------------------------------------------
# 真实三维预测靶区
# ---------------------------------------------------------------------------

def _point_in_bbox(lon, lat, bbox, pad: float = 0.0) -> bool:
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return False
    min_lon, min_lat, max_lon, max_lat = bbox
    return (min_lon - pad) <= lon <= (max_lon + pad) and (min_lat - pad) <= lat <= (max_lat + pad)


def _target_view_bounds(location, targets: Optional[List[dict]] = None) -> Tuple[float, float, float, float]:
    """报告靶区图取景范围：至少覆盖 ROI，并纳入所有有效靶区点位。"""
    lons = [location.min_lon, location.max_lon]
    lats = [location.min_lat, location.max_lat]
    for t in targets or []:
        lon, lat = t.get("longitude"), t.get("latitude")
        if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
            lons.append(lon)
            lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


def get_model3d_targets(location) -> List[dict]:
    """取与本研究区相交的最新 geo-model3d 三维成矿预测靶点。"""
    _import_commons()
    try:
        from commons.model3d_broker import find_model3d_for_bbox
    except Exception:
        return []
    matches = find_model3d_for_bbox(_bbox(location), GEO_MODEL3D_OUTPUTS, tenant_id=current_tenant())
    if not matches:
        return []

    bbox = _bbox(location)
    matches = sorted(matches, key=lambda e: e.get("created_at") or e.get("run_id", ""), reverse=True)
    for entry in matches:
        products = entry.get("products") or {}
        rel = products.get("targets_3d") or "targets_3d.json"
        path = Path(entry.get("model3d_dir", "")) / rel
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw_targets = payload.get("targets") if isinstance(payload, dict) else payload
        targets = []
        for i, t in enumerate(raw_targets or [], 1):
            lon, lat = t.get("lon"), t.get("lat")
            if not _point_in_bbox(lon, lat, bbox):
                continue
            targets.append({
                "rank": t.get("rank", i),
                "longitude": lon,
                "latitude": lat,
                "value": t.get("score", 0),
                "score": t.get("score"),
                "uncertainty": t.get("uncertainty"),
                "target_depth_m": t.get("depth_m"),
                "source": "model3d",
            })
        if targets:
            return sorted(targets, key=lambda t: t.get("value") or 0, reverse=True)
    return []


def get_known_deposits(location) -> List[dict]:
    """取与本研究区相交的已知矿点（deposits 标签）→ [{name,commodity,deposit_type,lon,lat}]，无则空。"""
    _import_commons()
    try:
        from commons.deposits_broker import find_deposits_for_bbox, get_points
    except Exception:
        return []
    try:
        matches = find_deposits_for_bbox(_bbox(location), GEO_DEPOSITS_OUTPUTS, tenant_id=current_tenant())
    except Exception:
        return []
    return get_points(matches[0]) if matches else []


def get_drill_evidence(location) -> dict:
    """取与本研究区相交的钻探布孔/反馈（geo-drill）→ {holes:[...], feedback:[...]}，无则空 dict。"""
    _import_commons()
    try:
        from commons.drill_broker import find_drill_for_bbox, get_holes, get_feedback
    except Exception:
        return {}
    try:
        matches = find_drill_for_bbox(_bbox(location), GEO_DRILL_OUTPUTS, tenant_id=current_tenant())
    except Exception:
        return {}
    if not matches:
        return {}
    entry = matches[0]
    return {"holes": get_holes(entry), "feedback": get_feedback(entry)}


def get_drill_targets(location) -> List[dict]:
    """
    把 geo-drill AI 计划孔（planned_holes）转为与深部靶区同构的点位，作为靶区推荐主源。
    按评分降序取前若干、重新编号，保留真实坐标/目标深度，供分级与制图。无计划孔→空。
    """
    holes = (get_drill_evidence(location).get("holes") or [])
    if not holes:
        return []

    def _score(h):
        s = h.get("score")
        return s if isinstance(s, (int, float)) else 0.0

    out = []
    for i, h in enumerate(sorted(holes, key=_score, reverse=True), 1):
        lon, lat = h.get("lon"), h.get("lat")
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        out.append({"rank": i, "longitude": lon, "latitude": lat,
                    "value": _score(h), "hole_id": h.get("hole_id"),
                    "target_depth_m": h.get("target_depth_m"), "source": "drill"})
    return out


def _reason_for(grade: str, value=None, source: str = "model3d") -> str:
    """确定性兜底理由（仅在 LLM 未给出 target 理由时使用）。"""
    if source == "drill":
        vtxt = f"钻探布孔综合评分 {value:.3f}，" if isinstance(value, (int, float)) else ""
        return {
            "A": f"{vtxt}处于区内最高档，AI 布孔优先级最高，建议优先实施验证。",
            "B": f"{vtxt}处于较高档，布孔优先级较高，建议安排实施。",
            "C": f"{vtxt}处于中等档，需补充查证后再定取舍。",
            "D": f"{vtxt}处于偏低档，支撑有限，建议先行地表查证。",
        }.get(grade, "")
    vtxt = f"三维成矿有利度 {value:.3f}，" if isinstance(value, (int, float)) else ""
    return {
        "A": f"{vtxt}处于区内最高档，三维融合证据突出，建议优先工程验证。",
        "B": f"{vtxt}处于较高档，三维预测有利度较好，建议安排验证。",
        "C": f"{vtxt}处于中等档，需补充查证后再定取舍。",
        "D": f"{vtxt}处于偏低档，支撑有限，建议先行地表查证。",
    }.get(grade, "")


def _grade_from_potential(raw: List[dict], max_targets: int = 6,
                          source: str = "model3d") -> List[dict]:
    """确定性兜底：对真实靶区按评分归一化分级 A-D（含真实坐标）。"""
    items = raw[:max_targets]
    vals = [t.get("value") or 0 for t in items]
    vmax, vmin = (max(vals), min(vals)) if vals else (1.0, 0.0)
    out = []
    for i, t in enumerate(items, 1):
        v = t.get("value") or 0
        nv = (v - vmin) / (vmax - vmin) if vmax > vmin else 1.0
        grade = "A" if nv >= 0.8 else "B" if nv >= 0.6 else "C" if nv >= 0.35 else "D"
        out.append({
            "rank": t.get("rank", i),
            "longitude": t.get("longitude"),
            "latitude": t.get("latitude"),
            "value": v,
            "grade": grade,
            "reason": _reason_for(grade, v, source),
            "hole_id": t.get("hole_id"),
            "target_depth_m": t.get("target_depth_m"),
        })
    return out


# ---------------------------------------------------------------------------
# 证据汇总与可信度
# ---------------------------------------------------------------------------

def _gather_evidence(search_results: Dict) -> List[dict]:
    """
    从 search_results 提取各章节证据摘要，并标注【报告章节号】（证据章从第二章起）、
    来源层级与勘探影响，供统一研判 prompt 使用，便于结论回引"见第X章"。
    """
    evidence = []
    for idx, cat in enumerate(get_all_categories()):
        r = search_results.get(cat.id)
        if r is None:
            continue
        evidence.append({
            "chapter_no": idx + 2,  # 第一章为基本信息，证据章从第二章起
            "name": cat.name,
            "has_data": bool(r and not r.error and r.summary),
            "evidence_level": getattr(r, "evidence_level", "") or "",
            "summary": (r.summary or "") if r else "",
            "key_findings": (r.key_findings or []) if r else [],
            "exploration_impact": getattr(r, "exploration_impact", "") or "",
        })
    return evidence


def _subsystem_availability(location) -> dict:
    """探测各子系统对本研究区是否有匹配数据，作为可信度依据。"""
    _import_commons()
    bbox = _bbox(location)
    avail = {"structural": False, "datacolle": False, "alteration": False,
             "exploration": False, "geophys": False, "geochem": False,
             "model3d": False, "deposits": False, "drill": False, "n_targets": 0}
    try:
        from commons.structural_broker import find_structural_for_bbox
        avail["structural"] = bool(find_structural_for_bbox(bbox, GEO_STRU_OUTPUTS, tenant_id=current_tenant()))
    except Exception:
        pass
    try:
        from commons.datacolle_broker import find_datacolle_for_bbox
        avail["datacolle"] = bool(find_datacolle_for_bbox(bbox, DATACOLLE_OUTPUTS, tenant_id=current_tenant()))
    except Exception:
        pass
    try:
        from commons.analyser_broker import find_alteration_for_bbox
        avail["alteration"] = bool(find_alteration_for_bbox(bbox, GEO_ANALYSER_OUTPUTS, tenant_id=current_tenant()))
    except Exception:
        pass
    try:
        from commons.exploration_broker import find_exploration_for_bbox
        m = find_exploration_for_bbox(bbox, GEO_EXPLORATION_OUTPUTS, tenant_id=current_tenant())
        avail["exploration"] = bool(m)
    except Exception:
        pass
    try:
        from commons.geophys_broker import find_geophys_for_bbox
        avail["geophys"] = bool(find_geophys_for_bbox(bbox, GEO_GEOPHYS_OUTPUTS, tenant_id=current_tenant()))
    except Exception:
        pass
    try:
        from commons.geochem_broker import find_geochem_for_bbox
        avail["geochem"] = bool(find_geochem_for_bbox(bbox, GEO_GEOCHEM_OUTPUTS, tenant_id=current_tenant()))
    except Exception:
        pass
    try:
        from commons.model3d_broker import find_model3d_for_bbox
        m = find_model3d_for_bbox(bbox, GEO_MODEL3D_OUTPUTS, tenant_id=current_tenant())
        avail["model3d"] = bool(m)
        if m:
            ms = m[0].get("model_stats") or {}
            avail["n_targets"] = ms.get("n_targets") or avail["n_targets"]
    except Exception:
        pass
    try:
        from commons.deposits_broker import find_deposits_for_bbox, get_points
        m = find_deposits_for_bbox(bbox, GEO_DEPOSITS_OUTPUTS, tenant_id=current_tenant())
        avail["deposits"] = bool(m and get_points(m[0]))
    except Exception:
        pass
    try:
        from commons.drill_broker import find_drill_for_bbox, get_holes, get_feedback
        m = find_drill_for_bbox(bbox, GEO_DRILL_OUTPUTS, tenant_id=current_tenant())
        avail["drill"] = bool(m and (get_holes(m[0]) or get_feedback(m[0])))
    except Exception:
        pass
    return avail


# ---------------------------------------------------------------------------
# 等级 ↔ 维度自洽校验
# ---------------------------------------------------------------------------

_GRADE_ORDER = ["A", "B", "C", "D"]


def _cap_grade(grade: str, dimensions: List[dict], avail: dict) -> Tuple[str, bool, List[str]]:
    """
    依据各维度有利程度与子系统可用性回拉置信等级，消除"维度多为低/缺却给 A/B"的不自洽。
    返回 (最终等级, 是否被回拉, 回拉原因列表)。
    """
    g = grade if grade in _GRADE_ORDER else "C"
    levels = [str(d.get("level", "")).strip() for d in (dimensions or [])]
    weak = sum(1 for l in levels if l in ("低", "缺"))
    has_local = any(avail.get(k) for k in ("structural", "datacolle", "alteration", "exploration",
                                            "geophys", "geochem", "model3d", "deposits", "drill"))

    best_allowed = "A"
    reasons: List[str] = []
    if levels and weak > len(levels) / 2:
        best_allowed = "C"
        reasons.append("多数研判维度为低/缺")
    if not has_local:
        if _GRADE_ORDER.index(best_allowed) < _GRADE_ORDER.index("C"):
            best_allowed = "C"
        reasons.append("缺少子系统本地实证、主要依赖网络资料")

    final = _GRADE_ORDER[max(_GRADE_ORDER.index(g), _GRADE_ORDER.index(best_allowed))]
    return final, (final != g), reasons


# ---------------------------------------------------------------------------
# 统一研判：置信评价 + 靶区/有利地段（同一次 LLM）
# ---------------------------------------------------------------------------

def _run_llm(prompt: str, timeout: int) -> Optional[Dict]:
    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", prompt],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8"
        )
    except Exception as e:
        print(f"[Synthesis] claude 调用失败：{e}")
        return None
    if result.returncode != 0:
        print(f"[Synthesis] claude 返回非零：{(result.stderr or result.stdout)[:200]}")
        return None
    from .search_engine import _parse_llm_json
    parsed = _parse_llm_json((result.stdout or "").strip())
    if parsed is None:
        print(f"[Synthesis] JSON 解析失败，原始输出前 300 字：{(result.stdout or '')[:300]}")
    return parsed


def evaluate_synthesis(location, mineral_type: str, search_results: Dict,
                       templates_dir: str, timeout: int = 300
                       ) -> Tuple[Optional[Figure], Optional[Dict]]:
    """
    统一综合研判：单次 claude -p 同时产出置信评价与靶区/有利地段研判，二者自洽。
    返回 (target_figure, confidence)；任一失败时各自容错降级。
    """
    evidence = _gather_evidence(search_results)
    avail = _subsystem_availability(location)
    known_deposits = get_known_deposits(location)       # 已知矿点（靶区套合佐证）
    drill_evidence = get_drill_evidence(location)       # 钻探布孔/反馈（验证闭环）
    # 靶区点位以 geo-model3d 三维成矿预测靶点为准；geo-exploration 仅可作为其上游证据。
    raw_targets, target_source = get_model3d_targets(location), "model3d"
    has_deep = bool(raw_targets)

    # 传给 LLM 的真实靶区（仅 rank+坐标，等级/理由由 LLM 结合证据给出）
    deep_targets_in = [
        {"rank": t.get("rank", i),
         "longitude": t.get("longitude"),
         "latitude": t.get("latitude"),
         "score": t.get("score", t.get("value")),
         "depth_m": t.get("target_depth_m"),
         "uncertainty": t.get("uncertainty")}
        for i, t in enumerate(raw_targets[:6], 1)
    ]

    # data-colle 成矿先验 + 综合结论/靶区建议——作为成矿结论锚点注入研判 prompt
    dc_metallogenic = fetch_datacolle_metallogenic(*_bbox(location))
    dc_conclusion = "\n".join(fetch_datacolle_section("conclusion", *_bbox(location)) or [])

    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    parsed = None
    try:
        template = env.get_template("confidence_prompt.j2")
        prompt = template.render(
            location_str=location.location_str,
            country=location.country,
            coords_str=location.coords_str,
            area_name=location.area_name,
            mineral_type=mineral_type,
            evidence=evidence,
            subsystems=avail,
            has_deep_targets=has_deep,
            deep_targets=deep_targets_in,
            known_deposits=known_deposits[:12],
            drill_holes=(drill_evidence.get("holes") or [])[:8],
            drill_feedback=(drill_evidence.get("feedback") or [])[:8],
            target_source=target_source,
            metallogenic=dc_metallogenic,
            datacolle_conclusion=dc_conclusion,
        )
        parsed = _run_llm(prompt, timeout)
    except Exception as e:
        print(f"[Synthesis] 模板渲染/调用异常：{e}")

    confidence = _finalize_confidence(parsed, avail) if parsed else None
    target_figure = _build_figure(location, parsed, raw_targets, has_deep, target_source)
    return target_figure, confidence


def _finalize_confidence(parsed: Dict, avail: dict) -> Dict:
    """对 LLM 置信结果做等级自洽回拉，并补记回拉说明。"""
    conf = dict(parsed)
    grade = str(conf.get("grade", "")).strip().upper()[:1]
    final, capped, reasons = _cap_grade(grade, conf.get("dimensions", []), avail)
    if capped:
        conf["grade"] = final
        reason_txt = "；".join(reasons)
        conf["summary"] = (conf.get("summary", "") or "") + \
            f"（注：综合证据按自洽性校验后定为 {final} 级——{reason_txt}。）"
        # 等级标签可能与新等级不符，保留原标签但以等级为准
    else:
        conf["grade"] = final
    return conf


def _build_figure(location, parsed: Optional[Dict], raw_targets: List[dict],
                  has_deep: bool, target_source: str = "model3d") -> Optional[Figure]:
    """
    构建靶区推荐图：
    - 有真实靶区（geo-model3d targets_3d）：底图叠加热力靶区点（坐标真实），
      等级/理由优先取 LLM，缺则确定性兜底。
    - 无任何真实靶区：仅画研究区框，输出「有利地段」定性条目（无精确坐标），标注待验证。
    """
    if has_deep and raw_targets:
        targets = _grade_from_potential(raw_targets, source=target_source)  # 兜底坐标+等级
        # 用 LLM 的 target_assessment 覆盖等级/理由（按 rank 对齐）
        if parsed:
            assess = {a.get("rank"): a for a in (parsed.get("target_assessment") or [])}
            for t in targets:
                a = assess.get(t["rank"])
                if a:
                    if a.get("grade") in _GRADE_ORDER:
                        t["grade"] = a["grade"]
                    if a.get("reason"):
                        t["reason"] = a["reason"]
        path = render_basemap(
            location,
            width_px=900,
            height_px=720,
            targets=targets,
            fit_bounds=_target_view_bounds(location, targets),
            fit_padding_ratio=0.16,
            max_zoom=15,
        )
        if not path:
            return None
        basis = "三维成矿预测（geo-model3d）"
        fig = Figure(
            path=path,
            caption=f"靶区推荐图（基于{basis}，共圈定 {len(targets)} 个靶区，按置信等级 A>B>C>D 排序）",
            source="",
        )
        fig.mode = "targets"
        fig.targets = targets
        fig.favorable_areas = []
        fig.target_source = target_source
        return fig

    # 无深部靶区：定性有利地段，绝不伪造坐标
    areas = (parsed.get("favorable_areas") or []) if parsed else []
    path = render_basemap(
        location,
        width_px=900,
        height_px=720,
        targets=None,
        fit_bounds=_target_view_bounds(location),
        fit_padding_ratio=0.16,
        max_zoom=15,
    )
    if not path:
        return None
    fig = Figure(
        path=path,
        caption="找矿有利地段示意（基于多源证据综合研判，未经三维建模靶点验证，"
                "具体靶区坐标待野外查证厘定）",
        source="",
    )
    fig.mode = "areas"
    fig.targets = None
    fig.favorable_areas = areas
    return fig


# ---------------------------------------------------------------------------
# 兼容旧接口（如有外部调用）
# ---------------------------------------------------------------------------

def build_target_zone_figure(location, search_results: Optional[Dict] = None,
                             mineral_type: str = "", templates_dir: Optional[str] = None,
                             timeout: int = 300) -> Optional[Figure]:
    """兼容旧签名：内部走统一研判（若提供 templates_dir 与 search_results）。"""
    if templates_dir and search_results is not None:
        fig, _ = evaluate_synthesis(location, mineral_type, search_results, templates_dir, timeout)
        return fig
    # 退化：仅用真实三维预测靶区，无则给定性框图
    raw = get_model3d_targets(location)
    return _build_figure(location, None, raw, bool(raw))


def evaluate_confidence(location, mineral_type: str, search_results: Dict,
                        templates_dir: str, timeout: int = 300) -> Optional[Dict]:
    """兼容旧签名：返回置信评价 dict（内部走统一研判）。"""
    _, conf = evaluate_synthesis(location, mineral_type, search_results, templates_dir, timeout)
    return conf
