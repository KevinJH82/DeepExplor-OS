"""store 的 Postgres 后端实现。

函数签名与返回 dict 形状与 JSON 版逐字一致(main.py/auth.py/cli.py 无感知)。
同步 SQLAlchemy,不对外暴露 session。
"""
import time
import uuid

from sqlalchemy import select, delete, update, text

from . import db
from .runstages import normalize_stages


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def init():
    """建表(幂等)。"""
    db.create_all()
    with db.engine().begin() as conn:
        cols = {row[0] for row in conn.execute(text(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name='projects'"
        ))}
        for _c in ("kml_name", "delivery_id", "delivery_name"):
            if _c not in cols:
                conn.execute(text(f"alter table projects add column {_c} varchar"))


# ─── 读:账号/租户 ──────────────────────────────────────────
def get_user_by_username(username: str):
    with db.Session() as s:
        u = s.scalar(select(db.User).where(db.User.username == username))
        return u.as_dict() if u else None


def get_user(user_id: str):
    with db.Session() as s:
        u = s.get(db.User, user_id)
        return u.as_dict() if u else None


def get_tenant(tenant_id: str):
    with db.Session() as s:
        t = s.get(db.Tenant, tenant_id)
        return t.as_dict() if t else None


def list_tenants():
    with db.Session() as s:
        return [t.as_dict() for t in s.scalars(select(db.Tenant))]


def list_users():
    with db.Session() as s:
        return [u.as_dict() for u in s.scalars(select(db.User))]


def list_tenant_users(tenant_id: str):
    with db.Session() as s:
        rows = s.scalars(select(db.User).where(db.User.tenant_id == tenant_id))
        return sorted([u.as_dict() for u in rows], key=lambda x: x["created_at"])


# ─── 读:项目/成员/运行 ─────────────────────────────────────
def list_projects(tenant_id: str, user_id: str):
    with db.Session() as s:
        roles = {m.project_id: m.role for m in
                 s.scalars(select(db.Member).where(db.Member.user_id == user_id))}
        out = []
        for p in s.scalars(select(db.Project).where(db.Project.tenant_id == tenant_id)):
            if p.id in roles or p.creator_id == user_id:
                out.append({**p.as_dict(), "my_role": roles.get(p.id, "geologist")})
        return sorted(out, key=lambda x: x["created_at"], reverse=True)


def get_project(project_id: str):
    with db.Session() as s:
        p = s.get(db.Project, project_id)
        return p.as_dict() if p else None


def project_role(user_id: str, project_id: str):
    with db.Session() as s:
        m = s.get(db.Member, (user_id, project_id))
        if m:
            return m.role
        p = s.get(db.Project, project_id)
        if p and p.creator_id == user_id:
            return "geologist"
        return None


def members_for_project(project_id: str):
    with db.Session() as s:
        rows = s.scalars(select(db.Member).where(db.Member.project_id == project_id))
        return [m.as_dict() for m in rows]


def _ensure_stages(s, run: "db.Run"):
    """run 无 stages 时由 plan 派生并持久化。返回 dict。"""
    d = run.as_dict()
    if not d.get("stages"):
        stages = normalize_stages(run.plan or {})
        if stages:
            run.stages = stages
            s.commit()
            d["stages"] = stages
    return d


def get_run(trace_id: str):
    with db.Session() as s:
        r = s.get(db.Run, trace_id)
        return _ensure_stages(s, r) if r else None


def runs_for_project(project_id: str):
    with db.Session() as s:
        rows = list(s.scalars(select(db.Run).where(db.Run.project_id == project_id)))
        out = [_ensure_stages(s, r) for r in rows]
        return sorted(out, key=lambda x: x["created_at"], reverse=True)


# ─── 写:项目/运行 ──────────────────────────────────────────
def create_project(tenant_id: str, user_id: str, name: str, mineral: str,
                   mineral_label: str = "", aoi_bbox=None):
    with db.Session() as s:
        pid = _new_id("p")
        p = db.Project(id=pid, tenant_id=tenant_id, name=name, mineral=mineral,
                       mineral_label=mineral_label or mineral, aoi_bbox=aoi_bbox,
                       thumb="cu", creator_id=user_id, created_at=_now(), current_run=None)
        s.add(p)
        s.add(db.Member(user_id=user_id, project_id=pid, role="geologist", expires_at=None))
        s.commit()
        return p.as_dict()


def delete_project(project_id: str):
    with db.Session() as s:
        p = s.get(db.Project, project_id)
        if not p:
            return False
        s.execute(delete(db.Member).where(db.Member.project_id == project_id))
        s.execute(delete(db.Run).where(db.Run.project_id == project_id))
        s.delete(p)
        s.commit()
        return True


def delete_run(trace_id: str):
    with db.Session() as s:
        r = s.get(db.Run, trace_id)
        if not r:
            return False
        pid = r.project_id
        s.delete(r)
        s.flush()
        p = s.get(db.Project, pid)
        if p and p.current_run == trace_id:
            rest = sorted(s.scalars(select(db.Run).where(db.Run.project_id == pid)),
                          key=lambda x: x.created_at, reverse=True)
            p.current_run = rest[0].trace_id if rest else None
        s.commit()
        return True


def update_project(project_id: str, patch: dict):
    with db.Session() as s:
        p = s.get(db.Project, project_id)
        if not p:
            return None
        for k, v in (patch or {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        s.commit()
        return p.as_dict()


def create_run(project_id: str, trace_id: str, plan: dict):
    with db.Session() as s:
        proj = s.get(db.Project, project_id)
        stages = normalize_stages(plan or {})
        r = db.Run(trace_id=trace_id, project_id=project_id,
                   tenant_id=proj.tenant_id if proj else None,
                   plan=plan, stages=stages, evidence_plan=None,
                   version=1, created_at=_now())
        s.merge(r)
        if proj:
            proj.current_run = trace_id
        s.commit()
        return r.as_dict()


def update_stage(trace_id: str, stage: str, patch: dict):
    with db.Session() as s:
        r = s.get(db.Run, trace_id)
        if not r:
            return None
        stages = dict(r.stages or {})
        st = dict(stages.get(str(stage)) or {"status": "pending", "sub_tasks": {}, "progress": 0})
        st.update(patch or {})
        stages[str(stage)] = st
        r.stages = stages
        s.commit()
        return r.as_dict()


def update_evidence_plan(trace_id: str, evidence_plan: dict):
    with db.Session() as s:
        r = s.get(db.Run, trace_id)
        if not r:
            return None
        r.evidence_plan = evidence_plan or {}
        s.commit()
        return r.as_dict()


# ─── 写:账号/角色/成员 ─────────────────────────────────────
def create_user(tenant_id: str, username: str, display: str,
                tenant_role: str, password_hash: str) -> dict:
    with db.Session() as s:
        if s.scalar(select(db.User).where(db.User.username == username)):
            raise ValueError(f"用户名已存在: {username}")
        uid = _new_id("u")
        u = db.User(id=uid, tenant_id=tenant_id, username=username,
                    display=display or username, tenant_role=tenant_role,
                    password_hash=password_hash, status="active", created_at=_now())
        s.add(u)
        s.commit()
        return u.as_dict()


def set_password(user_id: str, password_hash: str) -> bool:
    with db.Session() as s:
        u = s.get(db.User, user_id)
        if not u:
            return False
        u.password_hash = password_hash
        s.commit()
        return True


def update_user_role(user_id: str, role: str):
    with db.Session() as s:
        u = s.get(db.User, user_id)
        if not u:
            return None
        u.tenant_role = role
        s.commit()
        return u.as_dict()


def set_user_status(user_id: str, status: str):
    with db.Session() as s:
        u = s.get(db.User, user_id)
        if not u:
            return None
        u.status = status
        s.commit()
        return u.as_dict()


def touch_last_login(user_id: str) -> None:
    with db.Session() as s:
        u = s.get(db.User, user_id)
        if u:
            u.last_login_at = _now()
            s.commit()


def set_user_email(user_id: str, email: str) -> bool:
    with db.Session() as s:
        u = s.get(db.User, user_id)
        if not u:
            return False
        u.email = email or None
        s.commit()
        return True


# ─── 账号申请(开户审核流) ──────────────────────────────────
def create_application(email: str, applicant: str = "", org_name: str = "",
                       phone: str = "", purpose: str = "",
                       desired_username: str = "") -> dict:
    with db.Session() as s:
        aid = _new_id("app")
        a = db.AccountApplication(
            id=aid, email=email, applicant=applicant, org_name=org_name,
            phone=phone, purpose=purpose, desired_username=desired_username,
            status="pending", created_at=_now())
        s.add(a)
        s.commit()
        return a.as_dict()


def list_applications(status: str = None) -> list:
    with db.Session() as s:
        q = select(db.AccountApplication)
        if status:
            q = q.where(db.AccountApplication.status == status)
        rows = [a.as_dict() for a in s.scalars(q)]
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)


def get_application(app_id: str):
    with db.Session() as s:
        a = s.get(db.AccountApplication, app_id)
        return a.as_dict() if a else None


def update_application(app_id: str, patch: dict):
    with db.Session() as s:
        a = s.get(db.AccountApplication, app_id)
        if not a:
            return None
        for k, v in (patch or {}).items():
            if hasattr(a, k):
                setattr(a, k, v)
        s.commit()
        return a.as_dict()


def count_pending_applications_by_email(email: str) -> int:
    with db.Session() as s:
        return len(s.scalars(select(db.AccountApplication).where(
            db.AccountApplication.email == email,
            db.AccountApplication.status == "pending")).all())


# ─── 手工提交证据(项目级资产) ─────────────────────────────
def add_manual_evidence(project_id: str, category: str, label: str, filename: str,
                        path: str, size: int = 0, note: str = "", uploaded_by: str = "") -> dict:
    with db.Session() as s:
        mid = _new_id("me")
        m = db.ManualEvidence(id=mid, project_id=project_id, category=category, label=label,
                              filename=filename, path=path, size=int(size or 0), note=note,
                              uploaded_by=uploaded_by, created_at=_now())
        s.add(m)
        s.commit()
        return m.as_dict()


def list_manual_evidence(project_id: str) -> list:
    with db.Session() as s:
        rows = [m.as_dict() for m in s.scalars(
            select(db.ManualEvidence).where(db.ManualEvidence.project_id == project_id))]
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)


def get_manual_evidence(me_id: str):
    with db.Session() as s:
        m = s.get(db.ManualEvidence, me_id)
        return m.as_dict() if m else None


def delete_manual_evidence(me_id: str):
    with db.Session() as s:
        m = s.get(db.ManualEvidence, me_id)
        if not m:
            return None
        rec = m.as_dict()
        s.delete(m)
        s.commit()
        return rec


def create_tenant(name: str, quota_gb: int = 0) -> dict:
    with db.Session() as s:
        tid = _new_id("t")
        t = db.Tenant(id=tid, name=name, quota_gb=quota_gb, status="active", created_at=_now())
        s.add(t)
        s.commit()
        return t.as_dict()


def add_member(user_id: str, project_id: str, role: str, expires_at=None) -> dict:
    with db.Session() as s:
        m = s.get(db.Member, (user_id, project_id))
        if m:
            m.role = role
            m.expires_at = expires_at
        else:
            m = db.Member(user_id=user_id, project_id=project_id, role=role, expires_at=expires_at)
            s.add(m)
        s.commit()
        return m.as_dict()


def update_member_role(user_id: str, project_id: str, role: str):
    with db.Session() as s:
        m = s.get(db.Member, (user_id, project_id))
        if not m:
            return None
        m.role = role
        s.commit()
        return m.as_dict()


def remove_member(user_id: str, project_id: str) -> bool:
    with db.Session() as s:
        m = s.get(db.Member, (user_id, project_id))
        if not m:
            return False
        s.delete(m)
        s.commit()
        return True


# ─── 适配器任务终态(供 BFF 重启后恢复栅格 layer/状态)─────────
def save_adapter_task(tid: str, data: dict) -> None:
    if not tid or data is None:
        return
    with db.Session() as s:
        s.merge(db.AdapterTask(tid=tid, data=dict(data), updated_at=_now()))
        s.commit()


def get_adapter_task(tid: str):
    with db.Session() as s:
        rec = s.get(db.AdapterTask, tid)
    return dict(rec.data) if rec and rec.data else None


def find_adapter_tasks(predicate: dict = None, limit: int = 50):
    predicate = predicate or {}
    rows = []
    with db.Session() as s:
        q = s.query(db.AdapterTask).order_by(db.AdapterTask.updated_at.desc())
        for rec in q.limit(500):
            data = dict(rec.data or {})
            ok = all(str(data.get(k) or "") == str(v) for k, v in predicate.items())
            if ok:
                rows.append({"tid": rec.tid, "data": data, "updated_at": rec.updated_at})
                if len(rows) >= limit:
                    break
    return rows


# ─── refresh token ─────────────────────────────────────────
def save_refresh_token(jti: str, user_id: str, expires_at: int) -> None:
    with db.Session() as s:
        s.merge(db.RefreshToken(jti=jti, user_id=user_id, expires_at=int(expires_at),
                                revoked=False, created_at=_now()))
        s.commit()


def revoke_refresh_token(jti: str) -> None:
    with db.Session() as s:
        rec = s.get(db.RefreshToken, jti)
        if rec and not rec.revoked:
            rec.revoked = True
            s.commit()


def is_refresh_active(jti: str) -> bool:
    with db.Session() as s:
        rec = s.get(db.RefreshToken, jti)
    if not rec or rec.revoked:
        return False
    return int(rec.expires_at) > int(time.time())


def revoke_all_user_refresh(user_id: str) -> None:
    with db.Session() as s:
        s.execute(update(db.RefreshToken)
                  .where(db.RefreshToken.user_id == user_id, db.RefreshToken.revoked == False)  # noqa: E712
                  .values(revoked=True))
        s.commit()


# ─── 审计 ──────────────────────────────────────────────────
def write_audit(action: str, actor_user_id: str = None, tenant_id: str = None,
                target_type: str = None, target_id: str = None,
                details: dict = None, ip: str = None) -> None:
    with db.Session() as s:
        s.add(db.AuditLog(ts=_now(), tenant_id=tenant_id, actor_user_id=actor_user_id,
                          action=action, target_type=target_type, target_id=target_id,
                          details=details or {}, ip=ip))
        s.commit()


def list_audit(tenant_id: str = None, limit: int = 200):
    with db.Session() as s:
        q = select(db.AuditLog).order_by(db.AuditLog.id.desc()).limit(limit)
        if tenant_id:
            q = q.where(db.AuditLog.tenant_id == tenant_id)
        return [a.as_dict() for a in s.scalars(q)]
