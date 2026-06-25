"""统一反向代理:/svc/<service>/<path> → 对应微服务内网地址。

消除浏览器跨源(对前端是同源)、隐藏端口分散,是注入 tenant/user/内部密钥上下文的唯一出口。
独立模式的 iframe 也经此同源转发。

P1 起:
- 必须认证(Authorization Bearer 或 portal_access cookie),关闭原先的开放代理。
- 注入 X-Tenant-Id / X-User-Id / X-User-Role(供下游做租户感知)+ X-Internal-Key(证明来自 BFF)。
- 剥离客户端自带的同名身份/密钥头,杜绝前端伪造越权。
"""
import httpx
from fastapi import APIRouter, Request, Response, HTTPException

from . import services, auth
from .config import get_settings

router = APIRouter()
_settings = get_settings()
_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}
# 客户端永远不允许自带的头:由 BFF 权威注入,先无条件剥离
_SPOOF = {"x-tenant-id", "x-user-id", "x-user-role", "x-internal-key"}


@router.api_route("/svc/{service}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(service: str, path: str, request: Request):
    if not services.is_known(service):
        raise HTTPException(status_code=404, detail=f"未知服务: {service}")
    # 认证:Authorization 头(axios)优先,回退 access cookie(<img>/<iframe>/window.open)
    user = auth.current_user_header_or_cookie(
        authorization=request.headers.get("authorization", ""),
        access_cookie=request.cookies.get(_settings.access_cookie_name, ""),
    )
    target = f"{services.base_url(service)}/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP and k.lower() not in _SPOOF}
    # 权威注入身份上下文 + 内部密钥(下游据此做租户隔离 / 拒绝直连绕过)
    headers["X-Tenant-Id"] = user["tenant_id"]
    headers["X-User-Id"] = user["id"]
    headers["X-User-Role"] = user["tenant_role"]
    if _settings.internal_key:
        headers["X-Internal-Key"] = _settings.internal_key
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.request(
                request.method, target,
                params=dict(request.query_params), content=body, headers=headers,
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"服务 {service} 不可达(未启动)")
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=resp_headers, media_type=upstream.headers.get("content-type"))
