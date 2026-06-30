# DeepExplor 版本清单（VERSIONS）

本仓库自 **v0.1.0** 起实行版本管理。每个系统与子系统各自携带一个 `VERSION` 文件（前端子系统同时以 `package.json` 的 `version` 字段承载）；本文件为中央总清单，git tag 记录 monorepo 级里程碑。

- **基线**：所有系统/子系统统一从 `0.1.0` 起步（与对外宣传的 V0.1 一致）。
- **规范**：语义化版本 `MAJOR.MINOR.PATCH`。
  - `PATCH`：向后兼容的修复 / 微调。
  - `MINOR`：向后兼容的新能力。
  - `MAJOR`：不兼容的接口或数据契约变更（Broker 事件、产物 schema、API）。
- **改动哪个系统就只升哪个系统**的版本号；跨系统的协调里程碑用 monorepo 级 git tag（如 `v0.1.0`）标记。
- 升级时**三处同步**：该系统/子系统的 `VERSION` 文件、（若为前端）`package.json`、本清单表格。

> 当前 monorepo 基线 tag：`v0.1.0`

## 系统（11 + 扩展服务）

| 系统 | 角色 | 版本 |
|---|---|---|
| `commons` | 共享库：Broker、trace 决策血缘、光谱索引等 | 0.1.0 |
| `geo-orchestrator` | 编排（P1，依矿种/ROI 生成技术执行方案，签发 trace_id） | 0.1.0 |
| `geo-downloader` | 多源数据获取（40+ 传感器：光学/高光谱/热红外/全色） | 0.2.0 |
| `geo-preprocess` | 预处理（辐射/几何/大气校正、镶嵌、裁剪） | 0.1.0 |
| `geo-insar` | InSAR 时序形变（相干/速度聚类/线性体） | 0.1.0 |
| `geo-analyser` | 遥感证据解译（蚀变/构造加权/解混/异常） | 0.2.0 |
| `geo-geochem` | 化探背景与异常 | 0.1.0 |
| `geo-geophys` | 物探位场（磁/重力） | 0.1.0 |
| `geo-stru` | 构造解译（坡向/曲率/活动断裂/InSAR 融合） | 0.1.0 |
| `data-colle` | 数据汇集与成矿/文献先验（best_model/pathfinder/papers） | 0.1.0 |
| `geo-model3d` | 二维证据→三维地质体，深度切片成矿有利度，靶点 | 0.1.0 |
| `geo-drill` | 验证工程（布孔）与岩芯回灌 | 0.1.0 |
| `geo-reporter` | 证据链叙事 + 综合勘查报告（DOCX/PPTX） | 0.1.0 |
| `geo-exploration` | 勘查 web 应用（矿种引擎/慢变量检测） | 0.1.0 |
| `geo-7slow` | 七要素慢变量子系统 | 0.1.0 |
| `geo-Yaky` | 形变/位场扩展系统（fspef-vers-system） | 0.1.0 |
| `geo-portal` | 统一门户 BFF + 前端（租户/RBAC/项目/运行主线） | 1.1.0 |

## 子系统

| 子系统 | 隶属 | 角色 | 版本 |
|---|---|---|---|
| `geo-portal/backend` | geo-portal | FastAPI BFF（鉴权/反代/状态归一/血缘） | 1.1.0 |
| `geo-portal/frontend` | geo-portal | React 门户前端 | 1.1.0 |
| `geo-7slow/backend` | geo-7slow | 后端服务 | 0.1.0 |
| `geo-7slow/frontend` | geo-7slow | 前端 | 0.1.0 |
| `geo-Yaky/fspef-vers-system` | geo-Yaky | 形变/位场子系统 | 0.1.0 |
| `geo-exploration/Python_Project` | geo-exploration | 勘查应用主代码 | 0.1.0 |

## 变更日志（monorepo 级）

### 2026-06-30 — geo-analyser 0.2.0 / geo-downloader 0.2.0（遥感蚀变升级 v2）
基于《遥感图像处理技术及应用》(张晔2024) + 知网两篇文章(王生礼2023 综述 / 孙娅琴2017 WV-3 论文)的系统升级。
- **geo-analyser 0.2.0**：
  - 异常分级（张玉君门限化 X̄+kδ，羟基 2/2.5/3·铁染 1.5/2/2.5）+ 3×3 中值滤波去噪；分级经 API 暴露。
  - ASTER 矿物指数文献化（122 处）+ 修复碳酸盐 TIR(B13/B14) 比值算不出的 bug。
  - WorldView-3 接入 analyser 侧：传感器注册 + `_build_pca_spec` WV3 自包含分支 + DB 122 ratio/122 PCA。
  - **P0 解锁已下载数据**：金属矿床补 `enmap_feature`（122 处）→ EnMAP/PRISMA band_depth 对金属矿可用；
    `load_sensor_data` 把非主分辨率组（ASTER TIR 90m/VNIR 15m）重采样并入而非丢弃 → TIR 硅化指数可算。
- **geo-downloader 0.2.0**：WorldView-3 检索 VNIR+SWIR 两集合并按产物打标；打包 MS→B1..B8·SWIR→B9..B16 拆波段；schema 新增 wv3 16 波段条目。

### v1.1.0 — 2026-06-30
- `geo-portal` 系统及其 backend/frontend 子系统升至 **1.1.0**（其余系统保持 0.1.0）。
- 证据链融合增强：门户证据链叙事/地质叙事/store 联动重构，后端 main 配套
  （+247 行），新增设计文档 `geo-portal/docs/证据链融合方案.md`。

### v0.1.0 — 2026-06-29
- 版本管理起点：为 17 个系统与 6 个子系统建立统一 0.1.0 基线。
- 既有能力快照：Broker 零耦合协同、trace_id 全链决策血缘、门户身份体系生产化
  （RBAC/租户/开户审核流）、多源 broker 接通、证据链叙事与综合报告（DOCX/PPTX）。
