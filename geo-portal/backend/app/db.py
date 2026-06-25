"""SQLAlchemy(同步)引擎 + 模型。

设计原则:列与现 JSON store 的 dict 形状 1:1 对应,
- 时间戳沿用 ISO "....Z" 字符串(与 store._now() 一致),避免 tz 序列化差异;
- 半结构(plan/stages/evidence_plan/aoi_bbox/details)用 JSONB;
- runs.tenant_id 去规范化冗余,作为 P2 broker 租户隔离的权威锚点。
同步实现:对外保持同步函数签名,不让 async 传染到 main.py 的 23 处端点。
"""
from sqlalchemy import (
    create_engine, String, Integer, BigInteger, Boolean, ForeignKey, JSON,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, sessionmaker,
)

from .config import get_settings

_settings = get_settings()

# JSONB(PG) / JSON(其它,便于测试)自适应
_JSON = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    quota_gb: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[str] = mapped_column(String)

    def as_dict(self):
        return {"id": self.id, "name": self.name, "quota_gb": self.quota_gb,
                "status": self.status, "created_at": self.created_at}


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String, nullable=True, unique=True)
    username: Mapped[str] = mapped_column(String, index=True)
    display: Mapped[str] = mapped_column(String, default="")
    tenant_role: Mapped[str] = mapped_column(String, default="member")
    password_hash: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[str] = mapped_column(String)
    last_login_at: Mapped[str] = mapped_column(String, nullable=True)

    def as_dict(self):
        d = {"id": self.id, "tenant_id": self.tenant_id, "username": self.username,
             "display": self.display, "tenant_role": self.tenant_role,
             "password_hash": self.password_hash, "status": self.status,
             "created_at": self.created_at}
        if self.email is not None:
            d["email"] = self.email
        if self.last_login_at is not None:
            d["last_login_at"] = self.last_login_at
        return d


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    mineral: Mapped[str] = mapped_column(String, default="")
    mineral_label: Mapped[str] = mapped_column(String, default="")
    aoi_bbox = mapped_column(_JSON, nullable=True)
    kml_name: Mapped[str] = mapped_column(String, nullable=True)
    delivery_id: Mapped[str] = mapped_column(String, nullable=True)    # 绑定的交付库(命名一次,按 ID 引用)
    delivery_name: Mapped[str] = mapped_column(String, nullable=True)  # 交付目录名(展示用)
    thumb: Mapped[str] = mapped_column(String, default="cu")
    creator_id: Mapped[str] = mapped_column(String, index=True)
    current_run: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)

    def as_dict(self):
        return {"id": self.id, "tenant_id": self.tenant_id, "name": self.name,
                "mineral": self.mineral, "mineral_label": self.mineral_label,
                "aoi_bbox": self.aoi_bbox, "kml_name": self.kml_name,
                "delivery_id": self.delivery_id, "delivery_name": self.delivery_name,
                "thumb": self.thumb,
                "creator_id": self.creator_id, "current_run": self.current_run,
                "created_at": self.created_at}


class Member(Base):
    __tablename__ = "members"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    role: Mapped[str] = mapped_column(String, default="geologist")
    expires_at: Mapped[str] = mapped_column(String, nullable=True)

    def as_dict(self):
        return {"user_id": self.user_id, "project_id": self.project_id,
                "role": self.role, "expires_at": self.expires_at}


class Run(Base):
    __tablename__ = "runs"
    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    plan = mapped_column(_JSON, nullable=True)
    stages = mapped_column(_JSON, nullable=True)
    evidence_plan = mapped_column(_JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)

    def as_dict(self):
        d = {"trace_id": self.trace_id, "project_id": self.project_id,
             "plan": self.plan or {}, "stages": self.stages or {},
             "version": self.version, "created_at": self.created_at}
        if self.tenant_id is not None:
            d["tenant_id"] = self.tenant_id
        if self.evidence_plan is not None:
            d["evidence_plan"] = self.evidence_plan
        return d


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    jti: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    actor_user_id: Mapped[str] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, index=True)
    target_type: Mapped[str] = mapped_column(String, nullable=True)
    target_id: Mapped[str] = mapped_column(String, nullable=True)
    details = mapped_column(_JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String, nullable=True)

    def as_dict(self):
        return {"id": self.id, "ts": self.ts, "tenant_id": self.tenant_id,
                "actor_user_id": self.actor_user_id, "action": self.action,
                "target_type": self.target_type, "target_id": self.target_id,
                "details": self.details or {}, "ip": self.ip}


class AdapterTask(Base):
    """证据适配器任务(analyser/stru/geophys/insar)的终态快照(状态 + 栅格 layer 引用)。
    BFF 适配器任务原本只活在内存 _ADAPTER_TASKS,重启即丢 → 已完成证据"未取到栅格"/卡住。
    此表持久化终态,供 adapter-raster/svcstatus 在内存缺失时从库恢复。"""
    __tablename__ = "adapter_tasks"
    tid: Mapped[str] = mapped_column(String, primary_key=True)
    data = mapped_column(_JSON, nullable=True)
    updated_at: Mapped[str] = mapped_column(String)


_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        if not _settings.database_url:
            raise RuntimeError("DATABASE_URL 未配置")
        _engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def Session():
    if _Session is None:
        engine()
    return _Session()


def create_all():
    Base.metadata.create_all(engine())
