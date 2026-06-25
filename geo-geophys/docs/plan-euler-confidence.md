# 方向一：磁源深度不确定度 + 聚类（全闭环）

> 实施方案副本。源自规划讨论，已批准执行。

## Context（为什么做）

当前 geo-geophys 的欧拉反褶积只输出散乱的磁源点 `{x, y, depth_m, si}`，**丢弃了每个解的拟合残差与稳定性信息**，深度可信度仅靠一句硬编码的「区域级估计，非精确」表达（`geophys_engine.py:148`）。下游 geo-model3d 因此只能对所有实测深度用**写死的统一参数**：`measured_depth_gate` 的 `sigma_z_m=300` 和 `blend_depth_gate` 的 `w_measured=0.7`（`geo-model3d/core/geophys.py:123,136`）——稳的解和飘的解一视同仁。

这与「埋深越深越不准」是同一个缺口：geophys 没有把"这个深度有多可信"量化出来，model3d 也就无法据此调权。本方向给每个欧拉解算出**置信度**、把散乱点云**聚类**成"N 个磁源 + 深度带"，并让 model3d 的深度门控权重**由置信度与逐点深度带驱动**，从而把两侧的不确定度逻辑真正闭环。

预期结果：报告从「97 个磁性体，深度中位 2.3 km」升级为「N 个磁源聚类，深度 2.3 km（带 IQR/±σ），平均置信度 X」；model3d 在高置信磁源处深度约束更强、低置信处更保守。

## 设计概览

```
euler_deconvolution ★算misfit/稳定性→confidence
  → ★cluster_euler_sources（新函数，scipy 层次聚类，无新依赖）
  → write_euler_geojson ★加 confidence/misfit/cluster_id
  → ★euler_clusters.geojson（新产物：质心+深度+depth_sigma_m+confidence）
  → metadata["euler"] ★加 depth IQR/n_clusters/mean_confidence
  → render_euler_depth_hist ★加 IQR 带、按置信度着色
  → plain_summary ★改写
[下游 geo-model3d]
  load_euler_sources ★透传 confidence/depth_sigma_m
  → measured_depth_gate ★用逐点 sigma_z_m、按 confidence 缩放
  → blend_depth_gate ★w_measured 由 confidence 驱动
```

聚类用 `scipy.cluster.hierarchy`（scipy 已是依赖，**不引入 sklearn**）。

## 改动清单

### A. geo-geophys — 每个欧拉解算置信度
`core/potential_field.py::euler_deconvolution`
- `lstsq` 后算 `misfit = rms(A@sol−d)/rms(d)`、稳定性（奇异值比）、解-窗贴合度 `fit_pos`。
- `confidence = clip(w1*(1-misfit_norm) + w2*stability + w3*(1-fit_pos), 0, 1)`，经验权重 0.5/0.3/0.2。
- 点 dict 加 `confidence/misfit`（旧字段不动）。`max_points` 截断按置信度优先。

### B. geo-geophys — 聚类成"磁源"
`core/potential_field.py` 新增 `cluster_euler_sources(points, dx, dy)`
- `scipy.cluster.hierarchy.linkage(ward)` + `fcluster` 对归一化 `(x,y,depth)` 切簇。
- 每簇 → 置信度加权质心、中位深度、`depth_sigma_m`(MAD/IQR)、`confidence`(点数+均值+离散度)、`n_members`。
- 点数 < 3 每点成簇；无点返回空。

### C. geo-geophys — 产物/报告透传
- `outputs/writers.py`：`write_euler_geojson` 加 `confidence/misfit/cluster_id`；新增 `write_euler_clusters_geojson`。
- `core/geophys_engine.py`：调聚类+写 `euler_clusters.geojson`；`model_stats.euler` 加 `depth_p25_m/p75_m/n_clusters/mean_confidence`；`plain_summary` 改写。
- `outputs/render.py`：`render_euler_depth_hist` 加 IQR 阴影带。

### D. geo-model3d — 消费置信度
- `commons/geophys_broker.py::load_euler_sources`：透传 `confidence/depth_sigma_m`，旧产物默认兜底。
- `geo-model3d/core/geophys.py::measured_depth_gate`：逐点 `sigma_z_m`，`wxy` 乘 `confidence`。
- `geo-model3d/core/geophys.py::blend_depth_gate`：`w_measured` 改逐列置信度驱动。

## 复用点
现有 `Tx,Ty,Tz`/`A,d`、窗-解约束(178-180)、`scipy`、下游 `zprof`。新字段全为追加，旧产物兼容。

## 验证
1. 单元(pytest)：合成点源→高置信低 misfit，加噪→置信下降；两分离簇→聚成 2 簇。
2. 端到端：`verify_p1.py` 断言每点 `confidence∈[0,1]`、`euler_clusters.geojson` 存在、metadata 新字段齐全。
3. 闭环：model3d 读到 confidence，高置信列 `w` > 低置信列。
4. 回归：旧 `euler_sources.geojson`（无新字段）跑 model3d 不报错。

## 风险
聚类阈值/归一化需调参；置信度权重经验值（注释标注可调）；跨服务靠默认值保兼容。
