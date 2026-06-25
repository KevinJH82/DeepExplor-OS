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
