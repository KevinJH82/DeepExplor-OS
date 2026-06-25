"""DrillEngine —— 编排 AI布孔 / 数字岩芯编录 / 见矿判定 / 闭环反馈产出。

闭环：geo-model3d 有利度 → 布孔 →（钻探/编录）→ 见矿判定 → drill_feedback.geojson
→（/api/chain）回灌 model3d 带 drill_feedback_path 重算 → 有利度更新 → 下一轮布孔。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

from config.config import Config
from core.ingest import load_model3d_favorability, load_slowvars_targets
from core.siting import propose_holes
from core.slowvars_prior import apply_slowvars
from core.corelog import ingest_core_logs
from core.judge import judge_intersection, resolve_cutoff
from outputs import writers, render
from utils.logger import get_logger

logger = get_logger(__name__)


def _noop(msg, level="INFO"):
    pass


class DrillEngine:
    @staticmethod
    def run(aoi_name: str, mineral_type: str, bbox: List[float], output_dir: str,
            params: Optional[Dict] = None, log_callback: Optional[Callable] = None,
            roots: Optional[Dict[str, str]] = None) -> Dict:
        params = params or {}
        log = log_callback or _noop
        roots = roots or Config.upstream_roots()
        os.makedirs(output_dir, exist_ok=True)
        created_at = datetime.now().isoformat(timespec="seconds")
        fig_dir = os.path.join(output_dir, "figures")
        products: Dict = {}
        warnings: List[str] = []

        # 1) 取 geo-model3d 三维有利度体（按 覆盖度→矿种一致→最新 选；无产物→拒绝布孔，不臆造）
        fav = load_model3d_favorability(bbox, roots, mineral_type)
        if fav is None:
            raise RuntimeError("未发现本 AOI 的 geo-model3d 三维有利度产物。"
                               "请先在 geo-model3d(8086) 对该研究区出三维成矿预测，再来布孔。")
        log(f"取得 model3d 有利度体（run={fav.get('run_id')}, 覆盖度={fav.get('coverage')}, "
            f"矿种一致={fav.get('mineral_match')}, 矿床类型={fav.get('deposit_type')}）")
        if mineral_type and not fav.get("mineral_match"):
            warnings.append(f"所用 model3d 成果矿床类型({fav.get('deposit_type')})与目标矿种({mineral_type})"
                            "可能不一致——该区暂无矿种匹配的三维成果，已用覆盖度最佳者，请留意。")
            log("⚠ " + warnings[-1], "WARNING")

        # 决策轨迹 trace_id：显式入参优先（P2 orchestrator 注入），否则继承上游 model3d（P1 血缘）
        trace_id = None
        try:
            from commons.trace import resolve_trace_id
            _ups = [{"trace_id": fav.get("trace_id")}] if fav.get("trace_id") else None
            trace_id, _, _ = resolve_trace_id(params.get("trace_id"), _ups)
        except Exception:
            trace_id = params.get("trace_id")

        # 1.5) 取 geo-7slow 慢变量靶区（软先验 + 可解释性；无产物→降级为纯 model3d 布孔，不阻断）
        slowvars = load_slowvars_targets(bbox, roots, mineral_type, trace_id=trace_id)
        if slowvars:
            log(f"取得 geo-7slow 慢变量靶区（run={slowvars.get('run_id')}, "
                f"靶区 {slowvars.get('target_count')} 个 / {slowvars.get('target_area_km2')} km²）")
        else:
            log("未发现本 AOI 的 geo-7slow 慢变量靶区 → 仅按 model3d 有利度布孔。")

        # 2) AI 辅助布孔（P2: VOI 期望信息增益 / P1: 线性贪心）+ 斜孔轨迹优化
        # 默认 targets:直接在 model3d 预测靶点上布孔,与 3D 靶点视觉对齐(用户期望"钻在靶点上")。
        # voi:VOI 信息增益散开布孔;greedy:有利度贪心。无靶点时 targets 自动回退贪心。
        siting_mode = (params.get("siting_mode") or "targets").lower()
        top_n = int(params.get("top_n", Config.TOP_N))
        min_sep_m = float(params.get("min_sep_m", Config.MIN_SEP_M))
        allow_incline = str(params.get("allow_incline", "1")).lower() not in ("0", "false", "no")
        if siting_mode in ("targets", "target", "target-following"):
            from core.siting import propose_holes_at_targets
            holes = propose_holes_at_targets(fav, top_n=top_n, min_sep_m=min_sep_m)
            if not holes:
                log("model3d 无 targets_3d 靶点 → 回退有利度贪心布孔。")
                siting_mode = "greedy"
                holes = propose_holes(fav, top_n=top_n, min_sep_m=min_sep_m,
                                      explore_weight=float(params.get("explore_weight", Config.EXPLORE_WEIGHT)))
        elif siting_mode == "voi":
            from core.siting import propose_holes_voi
            holes = propose_holes_voi(fav, top_n=top_n, min_sep_m=min_sep_m,
                                      allow_incline=allow_incline)
        else:
            holes = propose_holes(fav, top_n=top_n, min_sep_m=min_sep_m,
                                  explore_weight=float(params.get("explore_weight", Config.EXPLORE_WEIGHT)))
        # 2.5) 慢变量软先验：标注每孔所属 geo-7slow 靶区(主控因子/Δ)，并按靶区置信度温和重排
        slowvars_weight = float(params.get("slowvars_weight", Config.SLOWVARS_WEIGHT))
        slowvars_stats = apply_slowvars(holes, slowvars, weight=slowvars_weight)
        if slowvars and slowvars_stats["n_in_target"] > 0:
            log(f"慢变量先验：{slowvars_stats['n_in_target']}/{len(holes)} 孔落入有利靶区"
                + ("（已按靶区置信度重排）" if slowvars_stats["reranked"] else "（仅标注未重排）"))
        # 斜孔/轨迹：为每孔优化方位/倾角（allow_incline 关→竖直）
        from core.trajectory import optimize_trajectory
        n_incl = 0
        for h in holes:
            tj = optimize_trajectory(fav, h["lon"], h["lat"],
                                     max_depth_m=h.get("target_depth_m"), allow_incline=allow_incline)
            h["azimuth_deg"] = tj["azimuth_deg"]; h["dip_deg"] = tj["dip_deg"]
            h["trajectory"] = tj["trajectory"]
            if tj.get("target_depth_m"):
                h["target_depth_m"] = tj["target_depth_m"]
            if abs(h["dip_deg"] + 90.0) > 1.0:
                n_incl += 1
        writers.write_planned_holes(os.path.join(output_dir, "planned_holes.geojson"), holes)
        products["planned_holes"] = "planned_holes.geojson"
        writers.write_holes_table_csv(os.path.join(output_dir, "holes_table.csv"), holes)
        products["holes_table_csv"] = "holes_table.csv"
        log(f"AI 布孔（{siting_mode}）：{len(holes)} 个计划孔（A级 "
            f"{sum(1 for h in holes if h.get('priority')=='A')}，斜孔 {n_incl}）")

        # 3) 数字岩芯编录（可选）+ 见矿判定 → 闭环反馈
        intersection_stats = {"status": "no_core_logs"}
        feedback_path_rel = None
        judged = None
        if params.get("collar_path") or params.get("swir_path") or params.get("xrf_path"):
            holes_db = ingest_core_logs(params.get("collar_path"), params.get("survey_path"),
                                        params.get("intervals_path"),
                                        swir_path=params.get("swir_path"),
                                        xrf_path=params.get("xrf_path"),
                                        instr_hole_id=params.get("instr_hole_id") or "DH-INSTR-1")
            writers.write_holes_db(os.path.join(output_dir, "holes_db.json"), holes_db)
            products["holes_db"] = "holes_db.json"

            element = (params.get("element") or Config.main_element_for(mineral_type)
                       or (holes_db.get("grade_elements") or [""])[0])
            cutoff = resolve_cutoff(mineral_type, element, roots, bbox, params.get("cutoff"))
            judged = judge_intersection(holes_db, element, cutoff)
            n_fb = writers.write_drill_feedback(os.path.join(output_dir, "drill_feedback.geojson"), judged)
            if n_fb > 0:
                products["drill_feedback"] = "drill_feedback.geojson"
                feedback_path_rel = "drill_feedback.geojson"
            n_ore = sum(1 for r in judged if r["outcome"] == "ore")
            n_bar = sum(1 for r in judged if r["outcome"] == "barren")
            n_unk = sum(1 for r in judged if r["outcome"] == "unknown")
            intersection_stats = {"status": "ok", "element": element, "cutoff": cutoff,
                                  "n_ore": n_ore, "n_barren": n_bar, "n_unknown": n_unk,
                                  "n_feedback": n_fb}
            if cutoff is None:
                warnings.append("无截止品位(cutoff)且 data-colle 无该元素阈值 → 见矿判定全部 unknown，未产出反馈。")
            log(f"见矿判定：见矿 {n_ore} / 无矿 {n_bar} / 未知 {n_unk}（元素 {element}, cutoff {cutoff}）")

            # ── D9 闭环金标签：把实钻见矿判定写进决策轨迹库（稀缺 ground-truth）──
            # 见架构蓝图 §2.6/§5：ore/barren 是平台唯一物理真值，与上游有利度预测同 trace_id。
            if trace_id:
                try:
                    from commons.trace import (get_writer, DECISION_ORE_JUDGEMENT,
                                               OUTCOME_GROUND_TRUTH)
                    w = get_writer()
                    run_id = os.path.basename(output_dir.rstrip("/"))
                    d9 = w.record_decision(
                        trace_id, parent_span_id=None, service="geo-drill", run_id=run_id,
                        decision_type=DECISION_ORE_JUDGEMENT,
                        state={"element": element, "cutoff": cutoff, "n_holes": len(judged),
                               "model3d_run": fav.get("run_id")},
                        decision={"action": "见矿判定",
                                  "params": {"n_ore": n_ore, "n_barren": n_bar, "n_unknown": n_unk}},
                        rationale=f"逐孔取主指示元素最大区间品位 vs 截止品位 {cutoff}：≥则见矿，<则真负。",
                        rationale_source="rule", decision_maker="drill_judge")
                    # 逐孔 ground-truth（仅 ore/barren —— unknown 不是标签，不入库）
                    for r in judged:
                        if r.get("outcome") in ("ore", "barren"):
                            w.record_outcome(
                                trace_id, parent_span_id=d9, service="geo-drill", run_id=run_id,
                                outcome_type=OUTCOME_GROUND_TRUTH, ground_truth=r)
                    log(f"D9 金标签已入轨迹库：{n_ore+n_bar} 条 ground-truth (trace_id={trace_id})")
                except Exception as _e:
                    log(f"D9 金标签记录跳过：{_e}", "WARNING")
        else:
            warnings.append("未上传岩芯编录 → 仅出 AI 布孔建议，无闭环反馈（钻后回填编录再判见矿）。")

        # 4) 图件：2D 布孔图 + 3D 静态图(始终可见) + 3D 交互查看器(three.js)
        figs = []
        try:
            figs.append(render.render_siting_map(fig_dir, fav, holes, judged))
        except Exception as e:
            log(f"2D 图渲染跳过：{e}", "WARNING")
        try:
            p3 = render.render_siting_3d_png(fig_dir, fav, holes, judged)
            figs.append(p3); products["siting_3d_png"] = os.path.relpath(p3, output_dir)
        except Exception as e:
            log(f"3D 静态图渲染跳过：{e}", "WARNING")
        if figs:
            products["figures"] = [os.path.relpath(p, output_dir) for p in figs]
        try:
            pt = render.render_holes_table_png(fig_dir, holes)
            products["holes_table_png"] = os.path.relpath(pt, output_dir)
        except Exception as e:
            log(f"钻孔信息表图渲染跳过：{e}", "WARNING")
        try:
            from outputs.viewer3d import write_drill_viewer
            vp = write_drill_viewer(os.path.join(output_dir, "viewer_3d.html"), fav, holes, judged,
                                    aoi_name=aoi_name)
            products["viewer_3d_html"] = os.path.relpath(vp, output_dir)
            log("已生成三维交互查看器 viewer_3d.html")
        except Exception as e:
            log(f"3D 交互查看器跳过：{e}", "WARNING")

        # 5) metadata
        model_stats = {
            "siting": {"method": siting_mode, "allow_incline": allow_incline,
                       "n_inclined": n_incl, "top_n": top_n, "min_sep_m": min_sep_m,
                       "n_holes": len(holes)},
            "model3d_run": fav.get("run_id"), "model3d_aoi": fav.get("aoi_name"),
            "model3d_selection": {"coverage": fav.get("coverage"),
                                  "mineral_match": fav.get("mineral_match"),
                                  "deposit_type": fav.get("deposit_type")},
            "slowvars": ({"run_id": slowvars.get("run_id"),
                          "target_count": slowvars.get("target_count"),
                          "target_area_km2": slowvars.get("target_area_km2"),
                          "deposit_type": slowvars.get("deposit_type"),
                          **slowvars_stats} if slowvars else {"status": "not_found"}),
            "intersection": intersection_stats,
            "mineral_type": mineral_type,
            "warnings": warnings,
            "plain_summary": _plain(holes, intersection_stats),
        }
        meta_path = writers.write_metadata(output_dir, aoi_name, bbox, fav.get("crs", "EPSG:4326"),
                                           products, model_stats, created_at, trace_id=trace_id, tenant_id=params.get("tenant_id"))
        # 闭环达成：产出 drill_feedback 即标记该 trace 的 Run 已闭环（供导出高价值样本子集）
        if trace_id and feedback_path_rel:
            try:
                from commons.trace import get_writer
                get_writer().close_run(trace_id, closed_by="drill_feedback", service="geo-drill")
            except Exception:
                pass
        log("完成。")
        return {"result_dir": output_dir, "metadata_path": meta_path, "products": products,
                "model_stats": model_stats, "n_holes": len(holes),
                "drill_feedback_path": (os.path.join(output_dir, feedback_path_rel)
                                        if feedback_path_rel else None)}


def _plain(holes, isx) -> List[str]:
    out = [f"AI 在三维有利度体上布了 {len(holes)} 个孔，"
           "兼顾『有利度高=直接奔矿』与『不确定性高=钻它最能减少未知（信息增益）』，并保证最小孔距。"]
    if isx.get("status") == "ok":
        out.append(f"已据岩芯编录判定：见矿 {isx['n_ore']} 孔 / 无矿 {isx['n_barren']} 孔 / 未知 {isx['n_unknown']} 孔。"
                   "见矿/无矿已写成 drill_feedback，可回灌 geo-model3d 让下一轮预测与布孔更准（螺旋上升）。")
    else:
        out.append("钻后把岩芯编录（孔位/区间/品位 CSV）传回，即可自动判见矿并回灌 geo-model3d 形成闭环。")
    out.append("👉 计划孔仅为建议，实际钻探/取芯为野外作业；见矿率取决于上游证据质量。")
    return out
