"""运行阶段(stages)归一化:把本地 stages 与 orchestrator execution_plan 统一成门户阶段状态。

后端无关的纯函数(只处理 dict),JSON 与 Postgres 两套 store 后端共用,避免循环依赖。
"""

STAGE_ORDER = ["plan", "data", "evidence", "model3d", "drill", "report"]

_SERVICE_ALIASES = {
    "geo-downloader": "downloader",
    "geo-preprocess": "preprocess",
    "data-colle": "datacolle",
    "data_colle": "datacolle",
    "geo-analyser": "analyser",
    "geo-stru": "stru",
    "geo-geophys": "geophys",
    "geo-geochem": "geochem",
    "geo-insar": "insar",
    "geo-7slow": "slowvars",
    "geo-slowvars": "slowvars",
    "geo-exploration": "exploration",
    "geo-model3d": "model3d",
    "geo-drill": "drill",
    "geo-reporter": "reporter",
}
_SERVICE_STAGE = {
    "downloader": "data",
    "preprocess": "data",
    "datacolle": "data",
    "analyser": "evidence",
    "stru": "evidence",
    "geophys": "evidence",
    "geochem": "evidence",
    "insar": "evidence",
    "slowvars": "evidence",
    "exploration": "evidence",
    "model3d": "model3d",
    "drill": "drill",
    "reporter": "report",
}


def norm_service(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    key = raw.lower().replace("_", "-")
    return _SERVICE_ALIASES.get(key, key)


def _norm_status(group: dict) -> str:
    if group.get("skip"):
        return "completed" if group.get("skip_reason") else "skipped"
    return "pending"


def _merge_stage(stages: dict, sid: str, service: str = "", group=None) -> None:
    if sid not in STAGE_ORDER:
        return
    st = stages.setdefault(sid, {"status": "pending", "services": [], "sub_tasks": {}, "progress": 0})
    if service and service not in st["services"]:
        st["services"].append(service)
    if group:
        gst = _norm_status(group)
        if service:
            st["sub_tasks"][service] = {
                "status": gst,
                "required": bool(group.get("required", False)),
                "reason": group.get("reason") or "",
                "skip_reason": group.get("skip_reason") or "",
                "tasks": group.get("tasks") or [],
            }


def _finalize_stage_status(stages: dict) -> None:
    for st in stages.values():
        tasks = list((st.get("sub_tasks") or {}).values())
        if not tasks:
            continue
        pending_required = any(t.get("required") and t.get("status") == "pending" for t in tasks)
        pending_any = any(t.get("status") == "pending" for t in tasks)
        if pending_required or pending_any:
            st["status"] = "pending"
            st["progress"] = 0
        else:
            st["status"] = "completed"
            st["progress"] = 100


def normalize_stages(plan: dict) -> dict:
    """把本地 stages 与 orchestrator execution_plan 统一成门户阶段状态。"""
    stages = {}
    for s in (plan or {}).get("stages", []):
        sid = str(s.get("stage") or s.get("name") or "")
        if not sid:
            continue
        services = [norm_service(x) for x in (s.get("services") or [])]
        stages[sid] = {"status": "pending", "services": [x for x in services if x],
                       "sub_tasks": {}, "progress": 0}

    for phase in ((plan or {}).get("execution_plan") or {}).get("phases", []):
        for group in phase.get("parallel_groups", []) or []:
            service = norm_service(group.get("service"))
            phase_text = str(phase.get("name") or "")
            is_data_phase = any(x in phase_text for x in ("数据", "获取")) or str(phase.get("phase") or "") == "1"
            sid = "data" if service == "insar" and is_data_phase else _SERVICE_STAGE.get(service)
            _merge_stage(stages, sid, service, group)

    _finalize_stage_status(stages)
    return {k: stages[k] for k in STAGE_ORDER if k in stages}


# ─── 项目卡片进度概要 ──────────────────────────────────────
_EXEC_STAGES = ["data", "evidence", "model3d", "drill", "report"]
_STAGE_LABEL = {"data": "数据", "evidence": "证据", "model3d": "3D建模",
                "drill": "布孔", "report": "报告"}
_DONE = {"completed", "skipped", "degraded"}   # 视为"已通过"的状态


def summarize_progress(stages: dict) -> dict:
    """从 run.stages 提炼出项目卡片用的进度概要(纯函数,对缺失/异常 stages 安全)。

    处理"重跑导致的非单调进度":某未完成阶段后面还有已完成阶段(下游绿、上游灰)时,
    给该阶段打 reordered 标记,当前状态置 'reordered'(前端显示"已重新操作"而非矛盾的"等待X")。
    当前阶段判定优先级:全部完成 > 失败 > 进行中 > 乱序 > 等待。
    """
    stages = stages or {}
    items = [{"id": s, "label": _STAGE_LABEL[s],
              "status": (stages.get(s) or {}).get("status") or "pending",
              "progress": int((stages.get(s) or {}).get("progress") or 0)}
             for s in _EXEC_STAGES]
    n = len(items)
    done = sum(1 for it in items if it["status"] in _DONE)
    furthest = max((i for i, it in enumerate(items) if it["status"] in _DONE), default=-1)
    for i, it in enumerate(items):
        it["reordered"] = (it["status"] not in _DONE) and (i < furthest)
    reordered_any = any(it["reordered"] for it in items)

    all_done = done == n
    failed = next((it for it in items if it["status"] == "failed"), None)
    running = next((it for it in items if it["status"] == "running"), None)
    first_pending = next((it for it in items if it["status"] not in _DONE), None)
    if all_done:
        cur, cstatus = items[-1], "completed"
    elif failed:
        cur, cstatus = failed, "failed"
    elif running:
        cur, cstatus = running, "running"
    elif reordered_any:
        cur, cstatus = first_pending, "reordered"
    else:
        cur, cstatus = first_pending, "pending"

    frac = (cur["progress"] / 100.0) if cstatus == "running" else 0
    percent = 100 if all_done else round((done + frac) / n * 100)
    return {"stages": items, "current": cur["id"], "current_label": cur["label"],
            "current_status": cstatus, "percent": percent, "done": done, "total": n,
            "all_done": all_done, "reordered": reordered_any}
