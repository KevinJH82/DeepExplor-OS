"""公开(无鉴权)接口:仅承载登录页「申请账号」的匿名提交。

注意:本路由下的端点不挂任何 auth 依赖,故只允许写入"待审申请"这类
无副作用、无信息回吐的操作;任何账号/凭据信息一律不在此返回。
"""
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from . import store

router = APIRouter(prefix="/api/public")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PublicApplyIn(BaseModel):
    email: str
    applicant: str = ""
    org_name: str = ""
    phone: str = ""
    purpose: str = ""
    desired_username: str = ""

    @field_validator("email")
    @classmethod
    def _email_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v) or len(v) > 200:
            raise ValueError("邮箱格式不正确")
        return v


@router.post("/account-applications")
def submit_application(body: PublicApplyIn):
    """匿名提交开户申请。同邮箱存在待审申请则拒绝(防重复刷)。"""
    applicant = (body.applicant or "").strip()[:60]
    if not applicant:
        raise HTTPException(status_code=400, detail="请填写申请人姓名")
    if store.count_pending_applications_by_email(body.email) > 0:
        raise HTTPException(status_code=409, detail="该邮箱已有待审核的申请,请勿重复提交")
    store.create_application(
        email=body.email,
        applicant=applicant,
        org_name=(body.org_name or "").strip()[:120],
        phone=(body.phone or "").strip()[:40],
        purpose=(body.purpose or "").strip()[:500],
        desired_username=(body.desired_username or "").strip()[:60],
    )
    return {"ok": True}
