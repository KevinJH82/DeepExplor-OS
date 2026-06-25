"""用户 / 角色 / 成员 / 租户 管理 API + 审计查询。

权限:
- 用户/成员管理:org_admin(限本租户) 或 platform_admin。
- 建租户:仅 platform_admin。
- org_admin 不得创建或提升到 platform_admin(防提权)。
所有写操作落 audit_log。
"""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import store, auth

router = APIRouter(prefix="/api/admin")


def _pub_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "display": u.get("display", ""),
            "tenant_id": u["tenant_id"], "tenant_role": u["tenant_role"],
            "status": u.get("status", "active"),
            "has_password": bool(u.get("password_hash")),
            "last_login_at": u.get("last_login_at")}


def _ip(req: Request) -> str:
    return req.client.host if req.client else ""


def _target_in_scope(actor: dict, target: dict):
    """org_admin 仅能操作本租户用户;platform_admin 不限。"""
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if actor["tenant_role"] != "platform_admin" and target["tenant_id"] != actor["tenant_id"]:
        raise HTTPException(status_code=404, detail="用户不存在")


def _guard_role(actor: dict, role: str):
    if role not in auth.TENANT_ROLES:
        raise HTTPException(status_code=400, detail=f"非法角色: {role}")
    if role == "platform_admin" and actor["tenant_role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="无权授予平台管理员")


# ─── 用户 ──────────────────────────────────────────────────
@router.get("/users")
def list_users(user=Depends(auth.require_org_admin)):
    return [_pub_user(u) for u in store.list_tenant_users(user["tenant_id"])]


class UserIn(BaseModel):
    username: str
    password: str
    display: str = ""
    role: str = "member"


@router.post("/users")
def create_user(body: UserIn, request: Request, user=Depends(auth.require_org_admin)):
    _guard_role(user, body.role)
    if not body.password or len(body.password) < 8:
        raise HTTPException(status_code=400, detail="口令至少 8 位")
    try:
        u = store.create_user(user["tenant_id"], body.username, body.display,
                              body.role, auth.hash_password(body.password))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    store.write_audit("user.create", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="user", target_id=u["id"],
                      details={"username": body.username, "role": body.role}, ip=_ip(request))
    return _pub_user(u)


class RoleIn(BaseModel):
    role: str


@router.patch("/users/{user_id}/role")
def change_role(user_id: str, body: RoleIn, request: Request, user=Depends(auth.require_org_admin)):
    target = store.get_user(user_id)
    _target_in_scope(user, target)
    _guard_role(user, body.role)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    updated = store.update_user_role(user_id, body.role)
    store.write_audit("user.role", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="user", target_id=user_id,
                      details={"role": body.role}, ip=_ip(request))
    return _pub_user(updated)


@router.post("/users/{user_id}/disable")
def disable_user(user_id: str, request: Request, user=Depends(auth.require_org_admin)):
    target = store.get_user(user_id)
    _target_in_scope(user, target)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="不能停用自己")
    updated = store.set_user_status(user_id, "disabled")
    store.revoke_all_user_refresh(user_id)   # 立即踢下线
    store.write_audit("user.disable", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="user", target_id=user_id, ip=_ip(request))
    return _pub_user(updated)


@router.post("/users/{user_id}/enable")
def enable_user(user_id: str, request: Request, user=Depends(auth.require_org_admin)):
    target = store.get_user(user_id)
    _target_in_scope(user, target)
    updated = store.set_user_status(user_id, "active")
    store.write_audit("user.enable", actor_user_id=user["id"], tenant_id=user["tenant_id"],
                      target_type="user", target_id=user_id, ip=_ip(request))
    return _pub_user(updated)


# ─── 租户(平台级) ──────────────────────────────────────────
class TenantIn(BaseModel):
    name: str
    quota_gb: int = 0


@router.post("/tenants")
def create_tenant(body: TenantIn, request: Request, user=Depends(auth.require_platform_admin)):
    t = store.create_tenant(body.name, body.quota_gb)
    store.write_audit("tenant.create", actor_user_id=user["id"], tenant_id=t["id"],
                      target_type="tenant", target_id=t["id"],
                      details={"name": body.name}, ip=_ip(request))
    return t


# ─── 审计查询 ──────────────────────────────────────────────
@router.get("/audit")
def get_audit(limit: int = 200, user=Depends(auth.require_org_admin)):
    # org_admin 仅本租户;platform_admin 全量
    tid = None if user["tenant_role"] == "platform_admin" else user["tenant_id"]
    return store.list_audit(tenant_id=tid, limit=min(limit, 1000))
