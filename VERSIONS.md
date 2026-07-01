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
| `geo-downloader` | 多源数据获取（40+ 传感器：光学/高光谱/热红外/全色） | 0.3.1 |
| `geo-preprocess` | 预处理（辐射/几何/大气校正、镶嵌、裁剪、多源配准、全色融合） | 0.3.0 |
| `geo-insar` | InSAR 时序形变（相干/速度聚类/线性体） | 0.1.0 |
| `geo-analyser` | 遥感证据解译（蚀变/构造加权/解混/异常） | 0.7.0 |
| `geo-geochem` | 化探背景与异常 | 0.1.0 |
| `geo-geophys` | 物探位场（磁/重力） | 0.1.0 |
| `geo-stru` | 构造解译（坡向/曲率/活动断裂/InSAR 融合） | 0.1.0 |
| `data-colle` | 数据汇集与成矿/文献先验（best_model/pathfinder/papers） | 0.1.0 |
| `geo-model3d` | 二维证据→三维地质体，深度切片成矿有利度，靶点 | 0.1.0 |
| `geo-drill` | 验证工程（布孔）与岩芯回灌 | 0.1.0 |
| `geo-reporter` | 证据链叙事 + 综合勘查报告（DOCX/PPTX） | 0.3.0 |
| `geo-exploration` | 勘查 web 应用（矿种引擎/慢变量检测） | 0.1.0 |
| `geo-7slow` | 七要素慢变量子系统 | 0.1.0 |
| `geo-Yaky` | 形变/位场扩展系统（fspef-vers-system） | 0.1.0 |
| `geo-portal` | 统一门户 BFF + 前端（租户/RBAC/项目/运行主线） | 1.2.0 |

## 子系统

| 子系统 | 隶属 | 角色 | 版本 |
|---|---|---|---|
| `geo-portal/backend` | geo-portal | FastAPI BFF（鉴权/反代/状态归一/血缘） | 1.2.0 |
| `geo-portal/frontend` | geo-portal | React 门户前端 | 1.2.0 |
| `geo-7slow/backend` | geo-7slow | 后端服务 | 0.1.0 |
| `geo-7slow/frontend` | geo-7slow | 前端 | 0.1.0 |
| `geo-Yaky/fspef-vers-system` | geo-Yaky | 形变/位场子系统 | 0.1.0 |
| `geo-exploration/Python_Project` | geo-exploration | 勘查应用主代码 | 0.1.0 |

## 变更日志（monorepo 级）

### 2026-07-01 — geo-downloader 0.3.1（launchd 托管：开机自启 + 崩溃自愈）
- 新增 `deploy/com.deepexplor.geodownloader.web.plist`(macOS LaunchAgent,RunAtLoad+KeepAlive) +
  `deploy/README.md`(安装/运维步骤)。根治"进程停了没人拉起来 → 8090 代理 502"。
- `run_web.sh` 增强:无 venv 时自动用系统 python3,且用 `python -m gunicorn` 避免 PATH 找不到。
- 记录线上事实:192.168.112.57 部署路径 /opt/deproject/geo-downloader、app 端口 8086
  (8090 是反向代理→8086)、该机无 venv 用 Homebrew python3。

### 2026-06-30 — geo-downloader 0.3.0（Web 服务稳健化：gunicorn + gevent 跑 SSE）
根治 SSE 长连接抖动(原 Werkzeug 开发服务器每连接占一线程,并发下重置/刷屏):
- 新增 `web/wsgi.py`(gevent monkey.patch_all 后再导入 app + 调 bootstrap)、`web/gunicorn_conf.py`
  (**workers=1**——任务状态在进程内存,多 worker 会不一致;`worker_class=gevent` 协程承载并发 SSE;
  preload_app=False 让后台线程/子进程在 worker 内启动)、`run_web.sh`(PORT 可配,默认 8080)。
- app.py 把任务恢复 + 后台守护线程抽成幂等 `bootstrap()`(dev __main__ 与 gunicorn 共用,因 gunicorn
  不走 __main__);端口改 PORT 环境变量。requirements 加 gunicorn/gevent。
- 验证: gunicorn gevent worker 启动正常,SSE 初始数据瞬时 flush 并保持长连接(对比 dev server 3s 0 字节)。
- 部署: 线上 `pip install -r requirements.txt` 后改用 `PORT=8090 ./run_web.sh` 启动(替代 python web/app.py)。

### 2026-06-30 — geo-downloader 0.2.1（修复 SSE 重连刷屏 WARN）
- Web UI 运行日志反复出现 "SSE 连接异常，将自动重连…" WARN：根因是 `index.html` 的
  `EventSource.onerror` 无条件告警,而浏览器在**正常自动重连**期间(readyState=CONNECTING)也会
  反复触发 onerror(叠加 Werkzeug dev server 长连接抖动 → 刷屏)。修复:仅当连接彻底关闭
  (readyState===CLOSED,终止重连)时才告警,正常重连静默(恢复老版本已有、现版本丢失的守卫)。
  下载本身不受影响(任务跑在独立子进程,SSE 仅日志推送通道)。

### 2026-06-30 — geo-portal 1.2.0 / geo-reporter 0.3.0（门户证据链增强 + 报告 v2）
- **geo-portal 1.2.0**（系统 + backend/frontend 子系统）：证据链/地质叙事增强 —— 后端 main.py/db.py +
  前端 Panels/evidenceChain/geologyNarrative/store/EvidenceStoryline/portal.js 联动。
- **geo-reporter 0.3.0**：报告 v2 构建器(`report_builder_v2.py`/`pptx_builder_v2.py`) + 价值评估
  模块(`value_assessment.py`) + pptx_builder/report_builder/web 配套。

### 2026-06-30 — geo-analyser 0.7.0 / geo-reporter 0.2.0（升级 v2 · UI/编排最后一公里）
把已实现的新分析法接进分析路由与 Word 报告展示:
- **geo-analyser**：`/api/analyze_batch` 多光谱 sensor 块新增**整景级证据层** RX(多变量异常) +
  tir_silica(ASTER-TES 硅化),每传感器跑一次、随 results[] 落盘(纯证据行,不改评分);
  高光谱块的 sasp/sam 早已并入 results。`alteration_store` 的 manifest result entry 写出 `grade`
  (异常分级)字段。
- **geo-reporter**：`data_sources.fetch_alteration_local` 加方法名中文映射(rx→RX多变量异常 /
  sam→光谱波形匹配 / tir_silica→热红外硅化指数 等)+ 渲染分级措辞(一/二/三级像元),
  报告自动展示新方法行与分级 —— 此前分级在报告里完全不可见。
- 端元(VCA/N-FINDR)为就绪能力(linear_unmix endmember_method 参数),当前路由无 unmix 生产调用方,
  待高光谱矿物解混被调用时启用。

### 2026-06-30 — geo-analyser 0.6.0（升级 v2 · P3-a N-FINDR/VCA 端元提取）
- **P3-a 端元自动提取**（书 §9.3.2，`endmember_extraction.py`）：VCA(Nascimento&Dias2005) +
  N-FINDR(Winter1999) 几何法从数据自动提纯真·端元(单形体顶点),替代 spectral_unmix 的
  NDVI/BSI 启发式 3 端元,使 NNLS 解混丰度具地质含义。linear_unmix 加 endmember_method="vca"/"nfindr"。
- 合成验证: VCA/N-FINDR 恢复 3 已知端元光谱角 <2°; VCA 解混丰度 vs 真值相关 0.992。

### 2026-06-30 — geo-preprocess 0.3.0（升级 v2 · P2-c 多源配准 + P2-d 全色融合）
- **P2-c 多源像元级配准**（书 §10.5.2，`core/coregistration.py`）：把不同分辨率/来源栅格重采样到
  统一参考格网 + 相位相关(FFT)估计并校正残余整数像元错位。融合定量正确性前置(合成验证:
  配准前 corr 0.45→配准后 1.00)。
- **P2-d PCA 全色融合**（书 §10.5.3，`core/pansharpen.py`）：多光谱 PCA→PC1 用直方图匹配的
  全色替换→逆变换,空间分辨率提升到全色级(Landsat B8 / WV3 PAN)。合成验证(pan 为同场景高分版):
  光谱保真相关 0.87、细节注入 1.00。

### 2026-06-30 — geo-analyser 0.5.0（升级 v2 · P2-b ASTER-TES 硅化 + P2-e 融合层次）
- **P2-b ASTER-TES 硅化指数**（书 §10.2.3，`thermal_emissivity.py`）：发射率归一化 + 石英指数
  QI=B11²/(B10·B12) + SiO₂% 回归 2.76·log10[6.56·B13·B14/(B10·B12)] + 碳酸盐 TIR 指数 B13/B14。
  analyze_single 加 method="tir_silica"(需 P0-b 加载的 TIR 波段)。硅化=斑岩/浅成低温金核心向量。
- **P2-e 融合层次形式化**（书 §10.5.1，`prospectivity.py`）：文档化 像元/特征/决策 三级,
  `FUSION_LEVELS`+`classify_fusion_level`,把 SASP/SAM/RX/TES 定位为特征级、fuse_evidence 为决策级。

### 2026-06-30 — geo-analyser 0.4.0（升级 v2 · P2-a RX 多变量异常）
- **P2-a RX/马氏异常**（书 §9.2.1，`calc_rx` + analyze_single method="rx"）：对整幅多波段图算
  像元相对背景(μ/Σ)的马氏距离平方 RXD,捕捉单波段阈值漏掉的波段间联合(协方差)异常,产出
  与矿物无关的"光谱异常"证据层;协方差对角正则防奇异;复用分级/中值滤波/API。

### 2026-06-30 — geo-analyser 0.3.0（升级 v2 · P1 高光谱波形/吸收特征）
- **P1-a SASP 光谱吸收特征**（书 §7.2.2，新增 `spectral_absorption.py`）：高光谱诊断窗口内提取
  吸收位置 P(质心,种属判别核心)/深度 D/宽度 W(FWHM)/不对称 A(偏度),深度加权矩全向量化;
  `sasp_index` = 深度×位置匹配权重。analyze_single 加 method="sasp"。
- **P1-b SAM 光谱波形匹配**（书 §9.5.2，新增 `spectral_match.py`）：与 USGS splib07
  (Kokaly2017, DOI:10.5066/F7RR1WDJ) 19 种关键蚀变矿物参考谱做光谱角匹配;参考库
  `data/splib07_reflib.json`(splib07a ASD→0.40-2.50µm@10nm)。analyze_single 加 method="sam"。
- app.py 高光谱块改为三法并跑 band_depth + sasp + sam。

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
