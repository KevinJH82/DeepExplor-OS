"""门户级持久化:租户 / 用户 / 项目 / 运行(trace_id)/ 项目成员。

MVP 用单个 JSON 文件落盘(沿用 model3d 的 _tasks.json 注册表风格),
规模化时整体替换为 Postgres(接口保持不变)。所有写操作经进程锁保护。
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("PORTAL_DATA_DIR", Path(__file__).resolve().parent.parent / "_data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = DATA_DIR / "portal_db.json"
_lock = threading.RLock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _seed() -> dict:
    """初始骨架:仅一个空租户。首个管理员经 `python -m app.cli create-admin` 创建。

    P0 起不再内置明文演示账号(admin/admin 后门已移除);所有账号必须有 argon2 password_hash。
    """
    return {
        "tenants": {
            "t_demo": {"id": "t_demo", "name": "白银地勘院", "quota_gb": 500, "created_at": _now()},
        },
        "users": {},          # user_id -> user(含 password_hash)
        "projects": {},       # project_id -> project
        "members": [],        # {user_id, project_id, role, expires_at}
        "runs": {},           # trace_id -> run
        "refresh_tokens": {}, # jti -> {user_id, expires_at, revoked}
        "account_applications": {},  # app_id -> 开户申请
        "manual_evidence": {},       # me_id -> 手工提交证据
    }


def _load() -> dict:
    if not _DB_PATH.exists():
        db = _seed()
        _save(db)
        return db
    try:
        db = json.loads(_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        db = _seed()
        _save(db)
        return db
    # 向后兼容:旧 DB 可能缺新键
    db.setdefault("refresh_tokens", {})
    db.setdefault("audit_log", [])
    db.setdefault("account_applications", {})
    db.setdefault("manual_evidence", {})
    return db


def _save(db: dict) -> None:
    tmp = _DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_DB_PATH)


_STAGE_ORDER = ["plan", "data", "evidence", "model3d", "drill", "report"]
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


def _norm_service(name: str) -> str:
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
    if sid not in _STAGE_ORDER:
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
        services = [_norm_service(x) for x in (s.get("services") or [])]
        stages[sid] = {"status": "pending", "services": [x for x in services if x],
                       "sub_tasks": {}, "progress": 0}

    for phase in ((plan or {}).get("execution_plan") or {}).get("phases", []):
        for group in phase.get("parallel_groups", []) or []:
            service = _norm_service(group.get("service"))
            phase_text = str(phase.get("name") or "")
            is_data_phase = any(x in phase_text for x in ("数据", "获取")) or str(phase.get("phase") or "") == "1"
            sid = "data" if service == "insar" and is_data_phase else _SERVICE_STAGE.get(service)
            _merge_stage(stages, sid, service, group)

    _finalize_stage_status(stages)
    return {k: stages[k] for k in _STAGE_ORDER if k in stages}


def _ensure_run_stages(db: dict, run):
    if not run:
        return run
    if run.get("stages"):
        return run
    stages = normalize_stages(run.get("plan") or {})
    if stages:
        run["stages"] = stages
        _save(db)
    return run


# ─── 读 API ────────────────────────────────────────────────
def get_user_by_username(username: str):
    with _lock:
        db = _load()
        for u in db["users"].values():
            if u["username"] == username:
                return u
    return None


def get_user(user_id: str):
    with _lock:
        return _load()["users"].get(user_id)


def get_tenant(tenant_id: str):
    """取租户记录(替代外部对 _load() 的私有访问)。"""
    with _lock:
        return _load()["tenants"].get(tenant_id)


def list_projects(tenant_id: str, user_id: str):
    """返回该用户在本租户内有成员关系的项目(附其项目角色)。"""
    with _lock:
        db = _load()
        roles = {m["project_id"]: m["role"] for m in db["members"] if m["user_id"] == user_id}
        out = []
        for p in db["projects"].values():
            if p["tenant_id"] != tenant_id:
                continue
            if p["id"] in roles or p["creator_id"] == user_id:
                out.append({**p, "my_role": roles.get(p["id"], "geologist")})
        return sorted(out, key=lambda x: x["created_at"], reverse=True)


def get_project(project_id: str):
    with _lock:
        return _load()["projects"].get(project_id)


def project_role(user_id: str, project_id: str):
    """用户在某项目的角色;创建者兜底为 geologist;无关系返回 None。"""
    with _lock:
        db = _load()
        for m in db["members"]:
            if m["user_id"] == user_id and m["project_id"] == project_id:
                return m["role"]
        p = db["projects"].get(project_id)
        if p and p["creator_id"] == user_id:
            return "geologist"
    return None


def get_run(trace_id: str):
    with _lock:
        db = _load()
        return _ensure_run_stages(db, db["runs"].get(trace_id))


def runs_for_project(project_id: str):
    with _lock:
        db = _load()
        for r in db["runs"].values():
            if r["project_id"] == project_id:
                _ensure_run_stages(db, r)
        return sorted([r for r in db["runs"].values() if r["project_id"] == project_id],
                      key=lambda x: x["created_at"], reverse=True)


# ─── 写 API ────────────────────────────────────────────────
def create_project(tenant_id: str, user_id: str, name: str, mineral: str,
                   mineral_label: str = "", aoi_bbox=None):
    with _lock:
        db = _load()
        pid = _new_id("p")
        db["projects"][pid] = {
            "id": pid, "tenant_id": tenant_id, "name": name, "mineral": mineral,
            "mineral_label": mineral_label or mineral, "aoi_bbox": aoi_bbox,
            "thumb": "cu", "creator_id": user_id, "created_at": _now(), "current_run": None,
        }
        db["members"].append({"user_id": user_id, "project_id": pid,
                               "role": "geologist", "expires_at": None})
        _save(db)
        return db["projects"][pid]


def delete_project(project_id: str):
    """删除项目 + 其成员关系 + 其所有运行(仅门户记录)。"""
    with _lock:
        db = _load()
        if project_id not in db["projects"]:
            return False
        del db["projects"][project_id]
        db["members"] = [m for m in db["members"] if m["project_id"] != project_id]
        for tid in [t for t, r in db["runs"].items() if r["project_id"] == project_id]:
            del db["runs"][tid]
        _save(db)
        return True


def delete_run(trace_id: str):
    """删除单次运行;若为项目 current_run 则改指最近一次或置空。"""
    with _lock:
        db = _load()
        run = db["runs"].get(trace_id)
        if not run:
            return False
        pid = run["project_id"]
        del db["runs"][trace_id]
        proj = db["projects"].get(pid)
        if proj and proj.get("current_run") == trace_id:
            rest = sorted([r for r in db["runs"].values() if r["project_id"] == pid],
                          key=lambda x: x["created_at"], reverse=True)
            proj["current_run"] = rest[0]["trace_id"] if rest else None
        _save(db)
        return True


def update_project(project_id: str, patch: dict):
    with _lock:
        db = _load()
        p = db["projects"].get(project_id)
        if not p:
            return None
        p.update(patch)
        _save(db)
        return p


def create_run(project_id: str, trace_id: str, plan: dict):
    """绑定 trace_id 为项目运行主键,初始化 stages 状态。"""
    with _lock:
        db = _load()
        stages = normalize_stages(plan or {})
        run = {"trace_id": trace_id, "project_id": project_id, "plan": plan,
               "stages": stages, "version": 1, "created_at": _now()}
        db["runs"][trace_id] = run
        if project_id in db["projects"]:
            db["projects"][project_id]["current_run"] = trace_id
        _save(db)
        return run


def update_stage(trace_id: str, stage: str, patch: dict):
    with _lock:
        db = _load()
        run = db["runs"].get(trace_id)
        if not run:
            return None
        st = run["stages"].setdefault(str(stage), {"status": "pending", "sub_tasks": {}, "progress": 0})
        st.update(patch)
        _save(db)
        return run


def update_evidence_plan(trace_id: str, evidence_plan: dict):
    with _lock:
        db = _load()
        run = db["runs"].get(trace_id)
        if not run:
            return None
        run["evidence_plan"] = evidence_plan or {}
        _save(db)
        return run


# ─── 账号 / 密码 ────────────────────────────────────────────
def create_user(tenant_id: str, username: str, display: str,
                tenant_role: str, password_hash: str) -> dict:
    """创建用户(密码已哈希)。用户名在全库唯一,重复抛 ValueError。"""
    with _lock:
        db = _load()
        if any(u["username"] == username for u in db["users"].values()):
            raise ValueError(f"用户名已存在: {username}")
        uid = _new_id("u")
        db["users"][uid] = {
            "id": uid, "tenant_id": tenant_id, "username": username,
            "display": display or username, "tenant_role": tenant_role,
            "password_hash": password_hash, "status": "active", "created_at": _now(),
        }
        _save(db)
        return db["users"][uid]


def set_password(user_id: str, password_hash: str) -> bool:
    """重设某用户的密码哈希。"""
    with _lock:
        db = _load()
        u = db["users"].get(user_id)
        if not u:
            return False
        u["password_hash"] = password_hash
        _save(db)
        return True


def touch_last_login(user_id: str) -> None:
    with _lock:
        db = _load()
        u = db["users"].get(user_id)
        if u:
            u["last_login_at"] = _now()
            _save(db)


def set_user_email(user_id: str, email: str) -> bool:
    with _lock:
        db = _load()
        u = db["users"].get(user_id)
        if not u:
            return False
        u["email"] = email or None
        _save(db)
        return True


# ─── 账号申请(开户审核流) ──────────────────────────────────
def create_application(email: str, applicant: str = "", org_name: str = "",
                       phone: str = "", purpose: str = "",
                       desired_username: str = "") -> dict:
    with _lock:
        db = _load()
        aid = _new_id("app")
        a = {"id": aid, "email": email, "applicant": applicant, "org_name": org_name,
             "phone": phone, "purpose": purpose, "desired_username": desired_username,
             "status": "pending", "reason": "", "created_at": _now(),
             "reviewed_at": "", "reviewed_by": "", "created_user_id": ""}
        db.setdefault("account_applications", {})[aid] = a
        _save(db)
        return a


def list_applications(status: str = None) -> list:
    with _lock:
        rows = list((_load().get("account_applications", {}) or {}).values())
    if status:
        rows = [a for a in rows if a.get("status") == status]
    return sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)


def get_application(app_id: str):
    with _lock:
        return (_load().get("account_applications", {}) or {}).get(app_id)


def update_application(app_id: str, patch: dict):
    with _lock:
        db = _load()
        a = db.get("account_applications", {}).get(app_id)
        if not a:
            return None
        a.update(patch or {})
        _save(db)
        return a


def count_pending_applications_by_email(email: str) -> int:
    with _lock:
        rows = (_load().get("account_applications", {}) or {}).values()
    return sum(1 for a in rows if a.get("email") == email and a.get("status") == "pending")


# ─── 手工提交证据(项目级资产) ─────────────────────────────
def add_manual_evidence(project_id: str, category: str, label: str, filename: str,
                        path: str, size: int = 0, note: str = "", uploaded_by: str = "") -> dict:
    with _lock:
        db = _load()
        mid = _new_id("me")
        m = {"id": mid, "project_id": project_id, "category": category, "label": label,
             "filename": filename, "path": path, "size": int(size or 0), "note": note,
             "uploaded_by": uploaded_by, "created_at": _now()}
        db.setdefault("manual_evidence", {})[mid] = m
        _save(db)
        return m


def list_manual_evidence(project_id: str) -> list:
    with _lock:
        rows = [m for m in (_load().get("manual_evidence", {}) or {}).values()
                if m.get("project_id") == project_id]
    return sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)


def get_manual_evidence(me_id: str):
    with _lock:
        return (_load().get("manual_evidence", {}) or {}).get(me_id)


def delete_manual_evidence(me_id: str):
    with _lock:
        db = _load()
        rec = db.get("manual_evidence", {}).pop(me_id, None)
        if rec is not None:
            _save(db)
        return rec


# ─── refresh token 吊销表(jti) ─────────────────────────────
def save_refresh_token(jti: str, user_id: str, expires_at: int) -> None:
    with _lock:
        db = _load()
        db["refresh_tokens"][jti] = {
            "user_id": user_id, "expires_at": int(expires_at), "revoked": False,
            "created_at": _now(),
        }
        _save(db)


def revoke_refresh_token(jti: str) -> None:
    with _lock:
        db = _load()
        rec = db["refresh_tokens"].get(jti)
        if rec and not rec.get("revoked"):
            rec["revoked"] = True
            _save(db)


def is_refresh_active(jti: str) -> bool:
    """jti 已登记、未吊销、未过期才算有效。"""
    with _lock:
        rec = _load()["refresh_tokens"].get(jti)
    if not rec or rec.get("revoked"):
        return False
    return int(rec.get("expires_at", 0)) > int(time.time())


def revoke_all_user_refresh(user_id: str) -> None:
    """吊销该用户全部 refresh(改密/停用时调用)。"""
    with _lock:
        db = _load()
        changed = False
        for rec in db["refresh_tokens"].values():
            if rec.get("user_id") == user_id and not rec.get("revoked"):
                rec["revoked"] = True
                changed = True
        if changed:
            _save(db)


# ─── 管理 / 审计(JSON 后端,与 store_pg 平价) ───────────────
def list_tenants():
    with _lock:
        return list(_load()["tenants"].values())


def list_users():
    with _lock:
        return list(_load()["users"].values())


def list_tenant_users(tenant_id: str):
    with _lock:
        db = _load()
        rows = [u for u in db["users"].values() if u["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda x: x["created_at"])


def update_user_role(user_id: str, role: str):
    with _lock:
        db = _load()
        u = db["users"].get(user_id)
        if not u:
            return None
        u["tenant_role"] = role
        _save(db)
        return u


def set_user_status(user_id: str, status: str):
    with _lock:
        db = _load()
        u = db["users"].get(user_id)
        if not u:
            return None
        u["status"] = status
        _save(db)
        return u


def create_tenant(name: str, quota_gb: int = 0) -> dict:
    with _lock:
        db = _load()
        tid = _new_id("t")
        db["tenants"][tid] = {"id": tid, "name": name, "quota_gb": quota_gb,
                               "status": "active", "created_at": _now()}
        _save(db)
        return db["tenants"][tid]


def members_for_project(project_id: str):
    with _lock:
        db = _load()
        return [m for m in db["members"] if m["project_id"] == project_id]


def add_member(user_id: str, project_id: str, role: str, expires_at=None) -> dict:
    with _lock:
        db = _load()
        for m in db["members"]:
            if m["user_id"] == user_id and m["project_id"] == project_id:
                m["role"] = role
                m["expires_at"] = expires_at
                _save(db)
                return m
        m = {"user_id": user_id, "project_id": project_id, "role": role, "expires_at": expires_at}
        db["members"].append(m)
        _save(db)
        return m


def update_member_role(user_id: str, project_id: str, role: str):
    with _lock:
        db = _load()
        for m in db["members"]:
            if m["user_id"] == user_id and m["project_id"] == project_id:
                m["role"] = role
                _save(db)
                return m
        return None


def remove_member(user_id: str, project_id: str) -> bool:
    with _lock:
        db = _load()
        n = len(db["members"])
        db["members"] = [m for m in db["members"]
                         if not (m["user_id"] == user_id and m["project_id"] == project_id)]
        if len(db["members"]) == n:
            return False
        _save(db)
        return True


def write_audit(action: str, actor_user_id: str = None, tenant_id: str = None,
                target_type: str = None, target_id: str = None,
                details: dict = None, ip: str = None) -> None:
    with _lock:
        db = _load()
        log = db.setdefault("audit_log", [])
        log.append({"id": len(log) + 1, "ts": _now(), "tenant_id": tenant_id,
                    "actor_user_id": actor_user_id, "action": action,
                    "target_type": target_type, "target_id": target_id,
                    "details": details or {}, "ip": ip})
        _save(db)


def list_audit(tenant_id: str = None, limit: int = 200):
    with _lock:
        log = _load().get("audit_log", [])
    rows = [a for a in log if (tenant_id is None or a.get("tenant_id") == tenant_id)]
    return list(reversed(rows))[:limit]


# ─── 适配器任务终态(JSON 后端;postgres 配置时重绑定到 store_pg)──
def save_adapter_task(tid: str, data: dict) -> None:
    if not tid or data is None:
        return
    with _lock:
        db = _load()
        db.setdefault("adapter_tasks", {})[tid] = data
        _save(db)


def get_adapter_task(tid: str):
    with _lock:
        return _load().get("adapter_tasks", {}).get(tid)


def find_adapter_tasks(predicate: dict = None, limit: int = 50):
    predicate = predicate or {}
    with _lock:
        items = list((_load().get("adapter_tasks", {}) or {}).items())
    rows = []
    for tid, data in reversed(items):
        data = data or {}
        ok = all(str(data.get(k) or "") == str(v) for k, v in predicate.items())
        if ok:
            rows.append({"tid": tid, "data": data, "updated_at": ""})
            if len(rows) >= limit:
                break
    return rows


# ─── 后端切换:DATABASE_URL 配置则整体走 Postgres(接口不变) ──
def _use_pg_backend():
    """把本模块所有公共数据函数重绑定到 store_pg 的同名实现。

    DATA_DIR / normalize_stages 等非数据访问成员保留在本模块。
    """
    from . import store_pg
    store_pg.init()  # 建表(幂等)
    g = globals()
    for name in dir(store_pg):
        if name.startswith("_"):
            continue
        fn = getattr(store_pg, name)
        if callable(fn) and name in g:
            g[name] = fn


try:
    from .config import get_settings as _get_settings
    if _get_settings().database_url:
        _use_pg_backend()
        _BACKEND = "postgres"
    else:
        _BACKEND = "json"
except Exception as _e:  # PG 不可达等:明确失败,不静默退回 JSON 误导数据落点
    raise
