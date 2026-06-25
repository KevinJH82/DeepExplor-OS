# geo-7slow 上下游数据整合分析

> 归档日期:2026-06-12
> 主题:从**输入(上游)**与**输出(下游)**两个方面评估 geo-7slow 在 deepexplor 服务群中的数据耦合
> ——上游能多吃哪些数据让结果更准、下游能供给哪些产出帮别的系统提升。
> 状态:**分析与路线图(尚未实施)**。结论基于对 geo-orchestrator 编排 DAG、commons broker、各 geo-* 服务 I/O 的梳理。

---

## 0. 背景:geo-7slow 当前是服务群里的"孤岛"

- **服务群有一条编排 DAG**(geo-orchestrator `core/system_knowledge.py:229-244`):
  - Phase 1 数据采集:`geo-downloader`(30+ 传感器)、`data-colle`(文献 + EMAG2/WGM2012 磁重场)
  - Phase 2 并行处理:`geo-analyser`(蚀变)、`geo-stru`(构造/线性体)、`geo-insar`(形变)、`geo-geophys`(磁重)、`geo-geochem`(化探)、`geo-exploration`(深部靶)
  - Phase 3 三维综合:`geo-model3d`(融合上述 → 3D 成矿性体 + 不确定性)
  - Phase 4 钻探指导:`geo-drill`(AI 布孔 + 钻探闭环回 model3d)
  - Phase 5 报告:`geo-reporter`(汇总各 broker → GB/T 9704 Word 报告)
- **整合机制 = commons 里的 per-service broker**:`analyser_broker` / `geochem_broker` / `geophys_broker` / `insar_broker` / `structural_broker` / `model3d_broker` / `drill_broker` / `exploration_broker` / `deposits_broker` / `datacolle_broker` 等,按 bbox 互相发现产出(COG 栅格 + GeoJSON 矢量 + stats JSON + PNG 图)。
- **geo-7slow 目前完全游离**:不在 DAG、commons 里没有 `slowvars_broker`、只有 Web/瓦片消费;输入靠手动上传或(本会话新增的)交付目录自动取数,产出无人对接。

> 这正是上下游讨论的支点:把 geo-7slow 接入"编排 DAG + broker 网"即可双向打通。

---

## 一、上游:geo-7slow 可多获取哪些数据让结果更准

### A. 直接吃同族服务的"派生产品"(最大增益,且能解掉 P1/P3/P4 的数据阻塞)

geo-7slow 现在很多变量是**遥感代理 / 自己重算**,而同族服务已产出**验证过的成品**——改成 broker 摄取即可:

| 上游服务 | 可摄取产品(格式) | 喂给 geo-7slow 的哪个变量 / 解掉什么 |
|---|---|---|
| **geo-insar** | LOS 速度场 `velocity_mm_per_year.tif` + 相干性(COG) | **①地应力、④断裂**的动态项 —— 解 **P3 的 InSAR 阻塞**(接口已预留,`insar_velocity` 传入即生效) |
| **geo-geophys** | 磁解析信号 / 倾斜角 / THD、Euler 源深 `euler_clusters.geojson`、低 Vs favorability 体(NetCDF) | **④断裂**:磁边缘可揭示**埋藏断裂**(现仅 DEM 地表线性体);Euler 深度 → 深度调制;低 Vs → 独立成矿性证据 |
| **geo-geochem** | 元素异常栅格(Au/Cu/As)、`multi_element_factor.tif`、`anomalies.geojson` | **⑤化学势**:现为光谱代理 → 换成真化探证据;多元素因子 = 矿致组合异常 |
| **geo-analyser** | 蚀变矿物图(绢云母 / 绿泥石 / 铁氧化 / 硅化)成品 | **②氧逸度、⑤化学势、⑥盖层** —— P2 我们**重算了**这些诊断比值,可改吃 geo-analyser 验证过的蚀变成品 |
| **geo-stru** | 线性体密度 / 距断裂距离、走向玫瑰图 | **①应力、④断裂** —— P3 已**复用其代码**,可进一步直接吃其产出栅格 |
| **data-colle / deposits_broker** | 已知矿点多边形 | **解 P4 的权重监督标定阻塞** —— 提供正 / 负样本标签 |

> **关键洞察**:geo-7slow 当前在"重新发明"邻居已有的成品(蚀变、线性体),并被三个数据缺口卡住
> (真 LST、InSAR、已知矿点标签);而这三者恰好分别由 geo-insar / data-colle / AST_08 等上游提供。
> 接 broker 后,②④⑤ 可从"单源遥感代理"升级为"多源证据融合",P1/P3/P4 三处阻塞同时解开。

### B. 原始 / 对地观测数据缺口(geo-downloader / data-colle 能下,但交付包未纳入)

- **AST_08 地表动力温度 / ECOSTRESS LST(70m)** → 真地表温度,替代 P1 现用的 AST_09T 辐亮度(绝对温度不可信,只能做相对)。
- **高光谱 EnMAP / PRISMA / EMIT / AVIRIS(200+ 波段,400–2500nm)** → ②⑤⑥ 直接矿物识别(吸收深度反演),远胜多光谱比值代理;部分交付项目里已有 EnMAP / PRISMA。
- **L-band SAR(ALOS-2 PALSAR,3m)** → ④ 更深断裂几何(现仅 C-band Sentinel-1)。
- **GEDI LiDAR(冠层高度)** → ⑥盖层与植被掩膜用真实冠层高度替代 NDVI 代理。
- **化探点数据(XRF / ICP-MS CSV)** → 验证 / 约束 ⑤化学势。
- **EMAG2 / WGM2012 磁重网格(经 data-colle)** → 区域构造 / 岩性背景。

---

## 二、下游:geo-7slow 可供给哪些产出帮别的系统

geo-7slow 现产出:7 个慢变量 z-栅格、`driving_force_b` / `resistance_a` / `delta_discriminant` COG、
`target_zones`(uint8 掩膜)、季节差分层、逐层 stats、XYZ 瓦片。本质上它**自己就是一台"多证据成矿性融合机"**
(7 变量 → 尖点突变 Δ → 靶区),这恰是下游想要的证据。

| 下游消费者 | 它怎么用 | geo-7slow 应供给 |
|---|---|---|
| **geo-model3d**(融合枢纽) | `gather_evidence` 摄取 alteration / geochem / structure / deformation 等 2D 证据 → F_xy × DepthGate 融合 | 把 `delta_discriminant.tif` / `driving_force_b.tif` 作**第 8 个证据层**(它本身就是多源融合的成矿性,且有突变论物理依据) |
| **geo-drill**(布孔) | value = prospectivity + explore_w × uncertainty,需成矿性体 / 靶区 | **矢量化靶区 GeoJSON**(多边形 + 每靶 mean Δ / 主控变量 / 排名)→ 布孔预过滤或软权重 |
| **geo-exploration** | 探测器集成,代码里有**未接线**的 `SlowVarsDetector`(`mineral_engine.py:44`) | 实现该 detector:输出成矿性格网 + Top 靶 |
| **geo-reporter** | 按"章节-来源"契约从 broker 取数 → GB/T Word | 新增"慢变量综合"章节素材:7 驱动 + Δ + 靶区面积 + 主控因子解释 |

### 下游整合的接口缺口(要补的产物)

1. **靶区矢量化**:`target_zones.tif`(栅格)→ GeoJSON 多边形 + 逐靶属性(面积、mean/min Δ、mean b、**主控慢变量**、排名)。几乎所有下游都要这个。
2. **`slowvars_broker.py`**(commons):按 bbox 发现 Δ / 靶区 / stats —— 目前缺这块"接线"。
3. **主控驱动栅格(可解释性)**:逐像元 argmax(加权 z-变量)= 该处由哪个慢变量主导 → 报告与布孔的"为什么是这"。
4. **不确定性**:Δ 的分位 / 敏感度(P4 已有 `/api/sensitivity` 端点)→ 供 model3d / drill 做风险加权。
5. **接入 orchestrator DAG**:把 geo-7slow 注册为 Phase-2.5 证据生产者(Phase 1 数据就绪后跑,产出喂 model3d)。

---

## 三、建议优先级(若决定实施)

- **P-up1**:`geo-insar` 速度场接入 ①④(解 P3 阻塞,接口已就绪,改交付 / broker 摄取)。
- **P-down1**:靶区矢量化 GeoJSON + 逐靶属性 + 主控驱动栅格(下游通吃、改动自洽,**最高杠杆**)。
- **P-down2**:`slowvars_broker` + geo-model3d 把 Δ 当第 8 证据层。
- **P-up2**:`geo-geophys` / `geo-geochem` 产品接入 ④⑤(埋藏断裂、真化探)。
- **P-up3**:AST_08 / ECOSTRESS 真 LST、高光谱(解 P1、升级 ②⑤⑥)。
- **P-cal**:data-colle 已知矿点标签 → 解 P4 权重监督标定;闭环用 geo-drill 钻探结果回标定权重。

> 两步即可把 geo-7slow 从"孤岛"接入整条成矿链:**上游接 geo-insar(解 P3)** + **下游做靶区矢量化 + slowvars_broker + Δ 当 model3d 第 8 证据层**。

## 四、验证方式

- 接入任一上游证据层后:跑招远 / 阜新,对比靶区与 Δ 是否更聚焦于已知矿化 / 异常;用 `/api/sensitivity` 看新证据层的重要性。
- 下游矢量化:确认 GeoJSON 多边形数、属性完整,能被 geo-model3d / geo-drill 的 broker 正确发现与摄取。

---

## 附:本会话已完成的 P0→P4 升级(背景参照)

| 阶段 | 成果 |
|---|---|
| P0 | 修融合 NODATA 泄漏(Δ 从 −2.9e11 回到 O(1~10))、TIR 回退、对齐 nodata、自适应靶区阈值 |
| P1 | ASTER 辐亮度 → Planck 亮温,窗区(B13/B14)LST 代理升级 ③⑦(绝对温度不可信,仅相对) |
| P2 | ②⑤⑥ 多端元蚀变诊断比值(解锁 S2 B02/B11/B12、ASTER B1/B3N/B9)+ 植被掩膜(NDVI>0.5) |
| P3 | ① 坡度+曲率应力代理;④ 复用 geo-stru 的自适应 Canny + Hough 线性构造密度 |
| P4 | 尖点突变模型订正(8/27→4/27)、`/api/sensitivity` 敏感性分析、季节差分(冬−夏 ΔLST/ΔNDVI) |

**贯穿性数据阻塞(跨服务,非代码可解)**:P1 的 AST_08 真 LST、P3 的真实 InSAR、P4 的已知矿点监督标定——
均需上游交付包纳入相应产品 / 标签,正是本文"上游整合"要打通的部分。
