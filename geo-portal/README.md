# geo-portal · DeepExplor 统一前端门户

3D 沉浸指挥中心 + 浅色玻璃 + 自适应演化舞台。以 `trace_id` 为主线串起 11 个微服务的勘探流水线;
BFF 统一反向代理(消除 CORS/隐藏端口)+ 认证 + 四级角色按项目授权 + 项目/运行托管。

设计依据见 `~/.claude/plans/ui-snazzy-kitten.md`。

## 结构

```
geo-portal/
├── backend/            FastAPI BFF
│   ├── app/services.py   11 服务端口注册表(SVC_<NAME>_PORT 可覆盖)
│   ├── app/proxy.py      /svc/<service>/* 反向代理
│   ├── app/store.py      租户/用户/项目/运行(trace_id) JSON 持久化
│   ├── app/auth.py       认证桩 + 四级角色 RBAC
│   └── app/main.py       入口:登录/项目/运行/代理
└── frontend/           Vite + React19 + antd6 + zustand
    └── src/
        ├── lib/stages.js   6 阶段定义 + 门控状态机
        ├── store.js        auth/project/workflow stores
        ├── components/      Canvas(2D↔3D 演化)/Dock(环形轨道)/EviStack/Panels(各阶段浮层)
        └── views/           Login / Projects / Workspace(指挥中心)
```

## 运行

```bash
# 1) BFF(默认 8100)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
bash run.sh          # 或 uvicorn app.main:app --port 8100 --reload

# 2) 前端(默认 5173,经 vite proxy 同源访问 BFF)
cd frontend
npm install
npm run dev          # 打开 http://localhost:5173
```

演示账号:`admin/admin`(租户管理员)、`geo/geo`(成员)。

## MVP 说明与后续

- **画布**:MVP 用 CSS 伪 3D 体 + depth 切片滑条(视觉已达定稿);后续替换为 Cesium/Resium 真三维 + 真实 GeoTIFF 瓦片(2D)与 depth_slices 解析。
- **流程驱动(已接真实服务)**:前端编排状态机实现门控/并行/重试/降级。
  - 编排单:`PlanPanel` 调真实 `orchestrator /api/plan`(BFF 注入样例 KML),拿**真实 trace_id + execution_plan**;orchestrator 不可达则本地兜底生成 trace。
  - 各阶段:`runStageReal` / `runEvidencesReal` 经 BFF `POST /api/runs/{trace}/start`(真实 `/api/start`)+ 轮询 `svcstatus`(归一化 `/api/status`);**真实完成驱动状态,合成进度填充进度条**;服务不可达(503)自动回退模拟进度。
  - BFF 端点:`POST /api/projects/{id}/plan`、`POST /api/runs/{trace}/start`、`GET /api/runs/{trace}/svcstatus`,KML multipart 由 BFF 承载(样例 `app/sample_aoi.kml`,后续替换为用户上传缓存)。
  - 已实测:orchestrator(:8090)实跑返回真实 trace_id;其余服务未启时 503 → 前端模拟,流程不中断。
- **trace_id**:已作项目运行主键(前端+BFF 逻辑串联);后端血缘闭合需下游 `/api/start` 加可选 `trace_id` 透传(见计划「后置」)。
- **安全**:认证 + 四级角色按项目授权 + BFF 服务端校验已落地;字段级加密/审计/broker 租户感知改造为后续。
