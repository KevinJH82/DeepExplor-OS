"""编排阶段的决策轨迹记录 —— 把 orchestrator 的规划过程写进决策轨迹库。

orchestrator 是 trace_id 的诞生点（蓝图 §1）。本模块把 ROI 分析 / 矿种推荐 / 任务编排
三步映射为 D1/D2/D3 决策事件，写入 commons.trace。

铁律：全部容错，任何异常都不得影响编排主流程（trace 失败只记日志）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("geo-orchestrator.trace")


def _roi_summary(roi_ctx) -> dict:
    """ROIContext → 紧凑结构化摘要（不内联重数据）。"""
    try:
        return {
            "area_km2": getattr(roi_ctx, "area_km2", None),
            "elevation_range": list(getattr(roi_ctx, "elevation_range", []) or []),
            "climate_zone": getattr(roi_ctx, "climate_zone", None),
            "vegetation_cover": getattr(roi_ctx, "vegetation_cover", None),
            "cloud_coverage": getattr(roi_ctx, "cloud_coverage", None),
            "tectonic_setting": getattr(roi_ctx, "tectonic_setting", None),
            "existing_products": getattr(roi_ctx, "existing_products", {}),
        }
    except Exception:
        return {}


def record_planning_trace(trace_id: str, mineral: str, roi_ctx, recommendation,
                          plan: dict, planner_mode: str = "deterministic") -> None:
    """记录一次编排规划的 Run + D1/D2/D3 决策事件。

    planner_mode: "llm" | "deterministic" —— 决定 D3 的 decision_maker / rationale_source。
    """
    try:
        from commons.trace import (
            get_writer, ORIGIN_SELF,
            DECISION_ROI_TO_MINERAL_FAMILY, DECISION_SENSOR_SELECTION,
            DECISION_SERVICE_ORCHESTRATION,
        )
    except Exception as e:  # commons.trace 不可用时静默跳过
        logger.debug("commons.trace 不可用，跳过轨迹记录: %s", e)
        return

    try:
        w = get_writer()
        bbox = list(getattr(roi_ctx, "bbox", []) or []) or None
        roi_sum = _roi_summary(roi_ctx)
        rationale = getattr(recommendation, "rationale", {}) or {}

        # ── Run：一次端到端 ROI 请求的起点（orchestrator 生成 trace_id）──
        w.start_run(trace_id, aoi_name=plan.get("roi", {}).get("aoi_name", ""),
                    mineral=mineral, bbox=bbox,
                    area_km2=roi_sum.get("area_km2"),
                    service="geo-orchestrator", trace_origin=ORIGIN_SELF)

        stage = w.begin_stage(trace_id, phase=0, name="编排规划",
                              services=["geo-orchestrator"])

        # ── D1：ROI + 矿种 → 成因族 ──
        w.record_decision(
            trace_id, parent_span_id=stage, service="geo-orchestrator", run_id=None,
            decision_type=DECISION_ROI_TO_MINERAL_FAMILY,
            state={"mineral": mineral, "tectonic_setting": roi_sum.get("tectonic_setting"),
                   "elevation_range": roi_sum.get("elevation_range")},
            decision={"action": "确定成因族",
                      "params": {"family": getattr(recommendation, "family", None),
                                 "family_weights": getattr(recommendation, "family_weights", {}),
                                 "depth_km_band": getattr(recommendation, "depth_km_band", None)}},
            rationale=rationale.get("family_determination", ""),
            rationale_source="rule", decision_maker="mineral_engine")

        # ── D2：ROI 特征 → 传感器组合 ──
        sensors = [{"sensor": s.sensor, "seasons": s.seasons, "required": s.required}
                   for s in getattr(recommendation, "sensors", [])]
        w.record_decision(
            trace_id, parent_span_id=stage, service="geo-orchestrator", run_id=None,
            decision_type=DECISION_SENSOR_SELECTION,
            state={"vegetation_cover": roi_sum.get("vegetation_cover"),
                   "cloud_coverage": roi_sum.get("cloud_coverage"),
                   "climate_zone": roi_sum.get("climate_zone")},
            decision={"action": "选择传感器组合", "params": {"sensors": sensors}},
            rationale="; ".join(v for k, v in rationale.items()
                                if k in ("roi_adjustment", "sensor_count") and v),
            rationale_source="rule", decision_maker="mineral_engine")

        # ── D3：生成任务编排单（LLM 或确定性）──
        phases_summary = []
        for ph in (plan.get("execution_plan", {}) or {}).get("phases", []):
            groups = ph.get("parallel_groups", []) or []
            phases_summary.append({
                "phase": ph.get("phase"), "name": ph.get("name"),
                "services": [g.get("service") for g in groups],
                "skipped": [g.get("service") for g in groups if g.get("skip")],
            })
        dr = plan.get("decision_rationale", {}) or {}
        alternatives = [{"action": f"跳过 {s}", "rejected_reason": "见 skipped_services"}
                        for s in (dr.get("skipped_services") or [])]
        is_llm = planner_mode == "llm"
        w.record_decision(
            trace_id, parent_span_id=stage, service="geo-orchestrator", run_id=None,
            decision_type=DECISION_SERVICE_ORCHESTRATION,
            state={"available_evidence": [s.service for s in getattr(recommendation, "services", [])],
                   "existing_products": roi_sum.get("existing_products", {})},
            decision={"action": "生成5阶段任务编排单", "params": {"phases": phases_summary}},
            rationale=_compact_rationale(dr),
            rationale_source="llm" if is_llm else "deterministic",
            alternatives_considered=alternatives,
            decision_maker="deepseek" if is_llm else "rule_engine")

        w.end_stage(stage, status="ok")
        logger.info("决策轨迹已记录: trace_id=%s (planner=%s)", trace_id, planner_mode)
    except Exception as e:
        logger.debug("record_planning_trace 失败(忽略): %s", e)


def _compact_rationale(dr: dict) -> str:
    """把 decision_rationale 各字段压成一段可读依据文本。"""
    keys = ("family_determination", "sensor_priority", "insar_decision",
            "geophys_decision", "geochem_decision", "roi_specific_notes")
    parts = [f"{k}: {dr[k]}" for k in keys if dr.get(k)]
    return " | ".join(parts)
