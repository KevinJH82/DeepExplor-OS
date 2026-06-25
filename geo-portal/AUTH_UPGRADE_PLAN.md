# DeepExplor 角色/权限/账户体系：从 MVP 桩升级到生产可用

> **实施进度（2026-06-18）**：✅ **P0 完成**｜✅ **P1 完成**｜✅ **P2 实质完成**（部署期运行验证待做）。
> P2：发现层隔离（lineage `filter_by_tenant`+10 broker tenant 参数+stamp 打标，**metadata 打标而非物理目录重构**）+ BFF 全通（13 处下游 httpx 带 `X-Internal-Key`，写盘类带 `X-Tenant-Id`；顺带补 P1 直连缺内部密钥）+ **全部 10 个生产者打标**（stru/analyser/geophys/geochem/drill/model3d/exploration/datacolle/7slow + insar 继承）+ **reporter 消费端 28 处 broker 全按租户过滤**（contextvar）。
> 验证：真实 analyser_broker 端到端隔离测试通过（两 bbox 相交、不同租户的产物互不可见）；12 改动文件 py_compile 全通；有 venv 的 stru/analyser/reporter/insar 导入通过。无 venv 的 7 个服务仅编译级验证，运行验证留待部署。
> 遗留人工项：① 下游各服务部署 env 配 `PORTAL_INTERNAL_KEY`（同 BFF）并重启方启用内部鉴权；② DeepSeek key 轮换；③ 现有 admin/geo 初始口令尽快用 `python -m app.cli set-password` 替换。

## Context（为什么做这件事）

DeepExplor 的身份体系当前**只是一套能跑通演示的 MVP 桩**，整套身份逻辑集中在 `geo-portal` BFF（FastAPI，:8100）一处，下游 11 个微服务零认证、完全信任 BFF 反代。经三路勘查 + Plan 设计 + 关键漏洞核实，确认现状：

**架构对、骨架在，但安全是空的**：
- 四级 RBAC 模型清晰（租户级 `platform_admin/org_admin/member`，项目级 `geologist/viewer/external`），门控 `require_project_read/write/admin` 到位 —— 这部分可保留。
- 但：密码 == 用户名、`admin/admin` 写死在 seed；token 是无过期的 HMAC 截断签名（非 JWT）、无刷新无登出；用户/租户/项目全在一个 JSON 文件；下游不注入身份、零租户隔离；用户管理 API 完全没有。

核实出的**两个真实安全漏洞**（非抽象担忧）：
1. **跨租户产物泄露** — `commons/trace/lineage.py:76` `return hits if hits else entries`：trace_id 未命中时回退返回全部 bbox 相交结果。多租户 AOI bbox 重叠时，A 租户报告会混入 B 租户的蚀变图/布孔成果。
2. **trace_id 越权** — trace_id 格式 `tr_<分钟>_<hex6>`（`main.py:831`）可猜测/伪造，前端可透传任意 trace_id，无归属校验。

明确的安全债：`geo-analyser/.env` 明文 DeepSeek key 已泄露；7 个下游 `config.py` 硬编码 `SECRET_KEY`。

**目标**：补齐到生产可用。**技术选型已定**（见下）。

---

## 技术选型（已确定）

| 领域 | 选型 | 理由 |
|---|---|---|
| 密码哈希 | **argon2-cffi** (`argon2.PasswordHasher`) | Argon2id 抗 GPU 暴破首选，纯 Python 绑定，无 bcrypt 72 字节截断坑 |
| Token | **PyJWT (HS256) + 旋转式 refresh** | 无外部 IdP，对称自签最省事；access 短时无状态、refresh 入库可吊销 |
| Token 传输 | access=内存(zustand)，refresh=**HttpOnly+Secure+SameSite=Lax cookie + 双提交 CSRF** | 杜绝 localStorage 被 XSS 盗 token |
| ORM / 迁移 | **SQLAlchemy 2.0（同步，psycopg）+ Alembic** | 同步实现规避 async 传染（关键，见难点 A） |
| 数据库 | **PostgreSQL 16** | 关系完整性 + JSONB（plan/stages 直接塞）+ RLS 兜底 |
| 配置/密钥 | **pydantic-settings + .env / 容器 secret** | 与 FastAPI 零摩擦，"启动即校验失败"，不引 Vault |
| 内部鉴权 | 共享内部密钥头 **`X-Internal-Key`** | 下游防直连绕过 BFF，最低成本，后续可升级 mTLS |
| 审计 | PG 表 `audit_log`（JSONB details） | 查询友好可追溯，不引 ELK |

版本方向：`argon2-cffi>=23`、`PyJWT>=2.8`、`sqlalchemy>=2.0`、`alembic>=1.13`、`pydantic-settings>=2.2`、`psycopg[binary]`。

---

## P0 — 先堵安全洞（1–2 天，不改架构、不引 PG）

止血密码/token/密钥三处，**不动 store.py 数据层、不动下游**。

- **新增 `geo-portal/backend/app/config.py`**：pydantic-settings `Settings`，集中 `PORTAL_JWT_SECRET / ACCESS_TTL / REFRESH_TTL / INTERNAL_KEY`，**缺失即启动报错**（删掉 `dev-portal-secret` 兜底）。
- **`auth.py`**：
  - `authenticate`：删 `password != username`，改 `PasswordHasher().verify(user["password_hash"], password)`；无 hash 直接拒。
  - `issue_token`/`_verify_token` → PyJWT。access 载荷 `{sub, tenant_id, tenant_role, type:"access", exp, iat, jti}` **15 分钟**；新增 `issue_refresh`（`{sub, type:"refresh", exp, jti}` **14 天**，jti 记录可吊销，P0 临时存进程内/JSON）。
  - `current_user`：校验签名+exp+`type=="access"`，过期 401。
- **新增 `geo-portal/backend/app/cli.py`**：`python -m app.cli create-admin` 交互建首个 `org_admin`（写 argon2 hash）。同步从 `store._seed()` **删除 admin/geo 明文演示账号**。
- **`main.py`**（login 在 ~840）：`/api/login` 改为 set HttpOnly refresh cookie + body 返 access；新增 `POST /api/refresh`（旋转 jti）、`POST /api/logout`（吊销 jti+清 cookie）。`_pub_user`（~849）**停止调 `store._load()`**，改调新增 `store.get_tenant()`。
- **前端** `src/api/client.js` + `src/store.js`：access 存内存不落 localStorage；`withCredentials:true`；401 先静默打 `/api/refresh` 重试一次再跳登录；`boot()` 改为先 refresh 再 `/api/me`。
- **密钥止血**：去 DeepSeek 控制台**作废并轮换**泄露 key（不是删文件了事）；`.env` 入 `.gitignore` + `git rm --cached geo-analyser/.env`；7 个下游 `config.py` 的 `SECRET_KEY` 改 `os.environ[...]` + 提供 `.env.example`。

**验收**：旧 token 全失效；非哈希账号无法登录；无 admin/admin 后门。

---

## P1 — 核心改造：存储迁移 + 身份贯穿 + 管理 API（1–2 周，工作量大头）

### 难点 A：store.py 迁 Postgres，保持接口不变

`store.py` 退化为"接口契约层"，内部 JSON → SQLAlchemy，**函数签名与返回 dict 形状逐字保留**，因此 `main.py`/`auth.py` 改动极小。三步：

1. **先消除私有耦合**：新增 `store.get_tenant()` 替换 `_pub_user` 里的 `_load()`；补齐新接口 `get/set_password、list_tenant_users、create_user、update_user_role、deactivate_user、add/remove_member、save/revoke/is_revoked_refresh_token、write_audit`。
2. **Alembic 初始 migration**，表（dict 形状 1:1，散字段进列、半结构进 JSONB）：
   ```
   tenants(id, name, quota_gb, status, created_at)
   users(id, tenant_id, email UNIQUE[全局登录标识], username, display,
         tenant_role, password_hash, status, created_at, last_login_at)
   members(user_id, project_id, role, expires_at, PK(user_id,project_id))
   projects(id, tenant_id, name, mineral, mineral_label, aoi_bbox JSONB,
            thumb, creator_id, current_run, created_at)
   runs(trace_id PK, project_id, tenant_id[去规范化—broker隔离锚点],
        plan JSONB, stages JSONB, evidence_plan JSONB, version, created_at)
   refresh_tokens(jti PK, user_id, issued_at, expires_at, revoked_at,
                  replaced_by[旋转链], user_agent, ip)
   audit_log(id, ts, tenant_id, actor_user_id, action, target_type,
             target_id, details JSONB, ip)
   ```
   关键：`runs.tenant_id` 冗余 = trace_id→tenant 权威映射；新增全局 `email` 登录标识规避跨租重名。
3. **一次性 ETL** `scripts/migrate_json_to_pg.py`（带 `--dry-run`）：读 `_data/portal_db.json` 灌库；老明文账号 `password_hash=NULL` + `status='disabled'`，强制走 CLI 重设。`DATABASE_URL` 是否配置决定走 PG 还是 JSON（保留回退分支灰度后删）。

**坑**：async 传染 → **坚持用同步 SQLAlchemy（psycopg）+ 连接池**，对外保持同步签名，FastAPI 端点本就跑线程池，零级联改动。

### 身份贯穿：proxy 注入 + 下游防绕过

- **`proxy.py`**：补 `Depends(auth.current_user)`，落实 line 23 TODO，注入 `X-Tenant-Id/X-User-Id/X-User-Role/X-Trace-Id/X-Internal-Key`；**剥离客户端伪造的同名头**（黑名单 + 显式 pop）。
- **trace_id 归属校验**（堵越权）：注入前 `store.get_run(trace_id)`，存在且 `tenant_id != user.tenant_id` → 403；不存在（新建）放行。
- **下游防绕过**：新增 `commons/internal_auth.py`，12 个服务各加 `before_request` 校验 `X-Internal-Key`（缺/错→403）。**每个服务只加 3 行 + 读一个 env**，非伤筋动骨；注意 orchestrator 服务间互调也要带 key。

### 用户/角色/成员管理 API（当前完全缺失）

新增 `routers/admin.py`，`org_admin/platform_admin` 门控（新增 `auth.require_org_admin/require_platform_admin`）：
```
POST/GET  /api/admin/users            建/列租户用户 [org_admin 限本租户]
PATCH     /api/admin/users/{id}/role  改 tenant_role
POST      /api/admin/users/{id}/disable | /enable
POST/PATCH/DELETE  /api/projects/{pid}/members[/{uid}]   项目成员增改删
POST      /api/admin/tenants          [platform_admin] 建租户
```
每个写操作末尾 `store.write_audit(...)`。

**验收**：换 PG 后现有功能不变；下游不带 `X-Internal-Key` 直连被拒；org_admin 能管本租户、碰不到他租户。

---

## P2 — 纵深：Broker 租户隔离（最难）+ 审计完善

### 难点 B：Broker 产物租户隔离（分三阶段，不可一步到位）

约束：12 个 broker 纯文件系统按 bbox/trace_id 扫散落产物，目录里**没有 tenant 维度**，bbox 可跨租相交。

- **P2-a 先堵漏（零目录改造，几天）**：改 `commons/trace/lineage.py:76`，删 bbox 回退 —— `return hits`（未命中返回空集，"宁可少给不可串租"）。配合 P1 的 proxy 归属校验，broker 只剩 trace_id 精确匹配，天然租户安全。未插桩 trace_id 的历史产物由已存在的 `commons/trace/harvest.py` 回填后恢复可见。
- **P2-b 物理隔离（1–2 周，大头里的大头）**：各服务产物根 `<service>/results/<AOI>/` → `<service>/results/<tenant_id>/<AOI>/`，服务读 `X-Tenant-Id` 决定写盘子目录；broker `scan_*` 按 tenant 子目录限定 root。抽 `commons` 里 `tenant_scoped_root(base, tenant_id)` helper 批量套用。历史产物按 manifest 的 trace_id → `runs.tenant_id` 一次性 `mv`，孤儿入 `_unassigned/`。
- **P2-c 兜底（可选/远期）**：`artifacts(tenant_id, trace_id, service, path, bbox)` 索引表，broker 查表替代裸扫；PG RLS 按 tenant 兜底。

### 审计完善
统一 `write_audit`：登录成功/失败、refresh 旋转、所有 admin 写、项目删除、跨租 403 命中；新增 `GET /api/admin/audit`（platform_admin）。

---

## 关键文件清单

| 文件 | 改动 |
|---|---|
| `geo-portal/backend/app/auth.py` | argon2、JWT、refresh、org/platform 门控 |
| `geo-portal/backend/app/store.py` | JSON→PG（接口契约不变）、新增管理/审计/refresh 接口 |
| `geo-portal/backend/app/proxy.py` | 注入身份头 + trace_id 归属校验 + 剥离伪造头 |
| `geo-portal/backend/app/main.py` | login/refresh/logout、`_pub_user` 去私有调用 |
| `geo-portal/backend/app/config.py` *(新)* | pydantic-settings 集中配置 |
| `geo-portal/backend/app/cli.py` *(新)* | create-admin |
| `geo-portal/backend/app/routers/admin.py` *(新)* | 用户/成员/租户管理 CRUD |
| `geo-portal/backend/scripts/migrate_json_to_pg.py` *(新)* | 一次性 ETL |
| `commons/trace/lineage.py` | **删 line 76 bbox 回退**（单行，安全关键） |
| `commons/internal_auth.py` *(新)* | 下游共享 `X-Internal-Key` 中间件 |
| 12 个下游服务入口 + 7 个 `config.py` | 加 before_request 校验 + SECRET_KEY 改 env |

## 工作量大头与坑
- **最大**：P2-b broker 物理隔离（12 服务写盘+扫描双改 + 历史迁移）。
- store.py PG 化：async 传染（→ 同步实现）、跨租重名（→ 全局 email）、私有 `_load()` 耦合（→ 先补 get_tenant）。
- trace_id 越权：lineage.py 一行 + proxy 归属校验，**二者缺一不可**。
- 密钥：泄露的 DeepSeek key 必须去控制台作废，非删文件了事。

---

## 端到端验证

- **登录/哈希**：CLI 建 admin → 正确密码登录拿 access+refresh cookie；admin/admin 旧后门应 401；DB `password_hash` 为 argon2 串。
- **token 过期/refresh**：`ACCESS_TTL=10s` → 10s 后旧 access 调 `/api/me` 401 → 前端静默 refresh 成功；`/api/logout` 后 refresh 应 401（jti 吊销）；篡改签名 401。
- **越权**：viewer 调写端点 403；非成员 `require_project_read` 404；他租户调本租户项目 404。
- **租户隔离（关键）**：建租户 A/B 两套 AOI **故意 bbox 相交**；A 跑蚀变、B 跑 reporter，B 报告不应含 A 的图（验 lineage.py + proxy 校验）。P2-b 后验 A 读不到 `results/<B_tenant>/`。
- **下游防绕过**：直连下游端口不带 `X-Internal-Key` 应 403；前端伪造 `X-Tenant-Id` 经 proxy 应被覆盖为真实身份。
- **审计**：上述每步在 `audit_log` 留痕。

沉淀为 `geo-portal/backend/tests/test_auth_e2e.py`（pytest + httpx.AsyncClient + testcontainers PG）。
