"""集中配置:所有密钥/TTL/开关单点收敛,缺失关键密钥即启动失败(fail-fast)。

P0 起 PORTAL_JWT_SECRET 为必填,删除原 auth.py 里 `dev-portal-secret` 的危险兜底。
读取顺序:进程环境变量 > backend/.env(本地开发) > 默认值。生产由容器 secret 注入。
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    # ── 认证密钥 ──
    # 必填:缺失则 Settings() 构造即抛错,服务无法带着弱默认密钥启动
    jwt_secret: str = Field(..., alias="PORTAL_JWT_SECRET")
    jwt_alg: str = Field("HS256", alias="PORTAL_JWT_ALG")

    # access 短时无状态;refresh 长时但 jti 可吊销
    access_ttl_seconds: int = Field(900, alias="PORTAL_ACCESS_TTL")      # 15 分钟
    refresh_ttl_seconds: int = Field(1209600, alias="PORTAL_REFRESH_TTL")  # 14 天

    # ── cookie 行为 ──
    refresh_cookie_name: str = Field("portal_refresh", alias="PORTAL_REFRESH_COOKIE")
    # access 也写一份 HttpOnly cookie:供 <img>/<iframe>/window.open 等浏览器原生
    # 请求(带不上 Authorization 头)经 /svc 反代时鉴权用;axios 仍优先用 Bearer 头。
    access_cookie_name: str = Field("portal_access", alias="PORTAL_ACCESS_COOKIE")
    cookie_secure: bool = Field(False, alias="PORTAL_COOKIE_SECURE")  # 生产置 True(仅 HTTPS)
    cookie_samesite: str = Field("lax", alias="PORTAL_COOKIE_SAMESITE")

    # ── 存储 ──
    # 配置则 store 走 Postgres;留空则回退 JSON 文件(本地零依赖)
    database_url: str = Field("", alias="DATABASE_URL")

    # ── 下游内部鉴权 ──
    internal_key: str = Field("", alias="PORTAL_INTERNAL_KEY")


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()
