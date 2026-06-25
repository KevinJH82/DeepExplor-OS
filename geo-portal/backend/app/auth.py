"""认证(argon2 口令 + JWT access/refresh)+ RBAC。

P0 起:
- 口令用 argon2id 哈希校验(替代 MVP 的 password==username)。
- token 为标准 JWT:access 短时无状态(默认15分钟);refresh 长时(默认14天)且 jti 入库可吊销,
  支持登出/改密失效。
- RBAC 四级角色 + 按项目授权的服务端校验(沿用,接口不变)。
密钥与 TTL 全部来自 config.Settings(缺失 PORTAL_JWT_SECRET 则启动失败)。
"""
import time
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Header, HTTPException, Depends

from . import store
from .config import get_settings

_settings = get_settings()
_ph = PasswordHasher()  # argon2id,参数走库内 OWASP 默认

# 四级角色 + 项目角色
TENANT_ROLES = {"platform_admin", "org_admin", "member"}
PROJECT_ROLES = {"geologist", "viewer", "external"}


# ─── 口令哈希 ──────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _ph.hash(password)


def _verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def authenticate(username: str, password: str):
    """口令校验:用户存在、状态正常、argon2 匹配。任一不满足返回 None。"""
    user = store.get_user_by_username(username)
    if not user or user.get("status", "active") != "active":
        return None
    if not user.get("password_hash") or not _verify_password(user["password_hash"], password):
        return None
    return user


# ─── JWT 签发 / 校验 ───────────────────────────────────────
def _encode(payload: dict) -> str:
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_alg)


def _decode(token: str) -> dict:
    return jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_alg])


def issue_access(user: dict) -> str:
    now = int(time.time())
    return _encode({
        "sub": user["id"],
        "tenant_id": user["tenant_id"],
        "tenant_role": user["tenant_role"],
        "type": "access",
        "iat": now,
        "exp": now + _settings.access_ttl_seconds,
        "jti": uuid.uuid4().hex,
    })


def issue_refresh(user_id: str) -> str:
    """签发 refresh 并登记 jti(可吊销)。"""
    now = int(time.time())
    exp = now + _settings.refresh_ttl_seconds
    jti = uuid.uuid4().hex
    token = _encode({"sub": user_id, "type": "refresh", "iat": now, "exp": exp, "jti": jti})
    store.save_refresh_token(jti, user_id, exp)
    return token


def issue_pair(user: dict) -> tuple:
    """返回 (access, refresh)。"""
    return issue_access(user), issue_refresh(user["id"])


def rotate_refresh(token: str):
    """校验 refresh → 吊销旧 jti → 发新 access+refresh。失败返回 None。"""
    try:
        payload = _decode(token)
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "refresh":
        return None
    jti = payload.get("jti")
    if not jti or not store.is_refresh_active(jti):
        return None
    user = store.get_user(payload.get("sub"))
    if not user or user.get("status", "active") != "active":
        return None
    store.revoke_refresh_token(jti)            # 旋转:旧 refresh 立即失效
    return user, issue_access(user), issue_refresh(user["id"])


def revoke_refresh(token: str) -> None:
    """登出:吊销携带的 refresh jti(解析失败则忽略)。"""
    try:
        payload = _decode(token)
        if payload.get("jti"):
            store.revoke_refresh_token(payload["jti"])
    except jwt.PyJWTError:
        pass


def user_from_access(token: str):
    """解析 access JWT → 在职用户;任何异常返回 None(不抛)。"""
    if not token:
        return None
    try:
        payload = _decode(token)
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    user = store.get_user(payload.get("sub"))
    if not user or user.get("status", "active") != "active":
        return None
    return user


def _bearer(authorization: str) -> str:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def current_user(authorization: str = Header(default="")):
    """从 Authorization: Bearer <access-jwt> 解析当前用户;失败/过期 401。"""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未认证")
    try:
        payload = _decode(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="未认证")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="未认证")
    user = store.get_user(payload.get("sub"))
    if not user or user.get("status", "active") != "active":
        raise HTTPException(status_code=401, detail="未认证")
    return user


def current_user_header_or_cookie(authorization: str = Header(default=""),
                                  access_cookie: str = ""):
    """反代用:优先 Authorization 头(axios),回退 access cookie(浏览器原生子资源)。

    access_cookie 由调用方从请求 cookie 取出后传入(见 proxy.py)。失败 401。
    """
    user = user_from_access(_bearer(authorization)) or user_from_access(access_cookie)
    if not user:
        raise HTTPException(status_code=401, detail="未认证")
    return user


# ─── 权限断言 ──────────────────────────────────────────────
# 项目级操作所需的最低项目角色
_WRITE_ROLES = {"geologist"}                 # 运行/上传/调参/布孔/导出/生成报告
_READ_ROLES = {"geologist", "viewer", "external"}  # 查看


def require_org_admin(user=Depends(current_user)):
    if user["tenant_role"] not in ("org_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="需租户管理员权限")
    return user


def require_platform_admin(user=Depends(current_user)):
    if user["tenant_role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="需平台管理员权限")
    return user


def require_project_read(user, project_id: str):
    proj = store.get_project(project_id)
    if not proj or proj["tenant_id"] != user["tenant_id"]:
        raise HTTPException(status_code=404, detail="项目不存在")
    role = store.project_role(user["id"], project_id)
    if role is None and user["tenant_role"] not in ("org_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="无项目访问权限")
    return proj, role


def require_project_write(user, project_id: str):
    proj, role = require_project_read(user, project_id)
    ok = role in _WRITE_ROLES or user["tenant_role"] in ("org_admin",)
    if not ok:
        raise HTTPException(status_code=403, detail="无操作权限(需地质工程师)")
    return proj, role


def require_project_admin(user, project_id: str):
    """删项目级:仅创建者或租户管理员。"""
    proj, role = require_project_read(user, project_id)
    ok = proj["creator_id"] == user["id"] or user["tenant_role"] in ("org_admin", "platform_admin")
    if not ok:
        raise HTTPException(status_code=403, detail="无权限删除(需创建者或租户管理员)")
    return proj, role
