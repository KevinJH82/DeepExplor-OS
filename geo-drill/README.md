# geo-drill —— 钻探验证与"预测-验证-优化"闭环 P1

补齐平台"钻探为验证"一环，把一次性管线改造成**螺旋上升的学习循环**：在 geo-model3d 三维有利度上做
**AI 辅助布孔**，管理**钻孔数据库 + 数字岩芯编录**，把钻探实测（见矿/无矿）**回灌** geo-model3d。
独立 Flask 服务，端口 **8089**，零耦合订阅上游、回写 `drill_broker`。

## P1 范围与诚实边界

- **没有真实钻机/岩芯硬件**：P1 做的是**钻孔数据库 + AI 布孔建议 + 岩芯编录 + 闭环接线**，**不是物理钻探**。实际钻孔/取芯是野外作业。
- **无 geo-model3d 有利度产物 → 拒绝布孔**并提示先跑 model3d，**不臆造孔位**。
- **品位缺失 / 无截止品位(cutoff) → 见矿判定 unknown**，**不进反馈、不臆断见矿**。
- **计划孔 ≠ 已验证反馈**：`drill_feedback` 只含真实编录判出的 ore/barren。
- "AI 布孔见矿率 20–30% → 60–70%" 是文档引用目标，真实命中率取决于上游证据质量，服务只提供优化，**不夸大**。

## 用法

```bash
cd geo-drill && pip install -r requirements.txt
python3 run.py            # http://0.0.0.0:8089
```

Web：上传研究区 KML/KMZ + 选矿种（专业选项：布孔参数 + 钻孔编录 collar/intervals CSV）
→ 取 geo-model3d 三维有利度 → AI 布孔 →（钻后传编录）→ 见矿判定 → `drill_feedback` → 一键回灌。

## 功能（P1）

- **AI 辅助布孔**：价值 = `prospectivity + explore_weight × uncertainty`——**有利度高(exploitation)** + **不确定性高(exploration / value-of-information)**，深度方向取最优体元 + 最小孔距 NMS（复用 geo-model3d `_nms_targets_2d`/`write_targets_3d` 思路）。输出计划孔(孔位/目标深度/优先级 A/B/C，P1 直孔)。
- **数字岩芯编录**：collar/survey/intervals CSV → 机读钻孔库（"可长期复用可机读"）；SWIR/XRF 留 P2。
- **见矿判定**：主指示元素最大区间品位 vs cutoff（用户给定或取 data-colle 阈值）→ ore/barren/unknown。
- **闭环回灌**：见矿/无矿 → `drill_feedback.geojson`（严格对齐 geo-model3d `load_drill_feedback`）→ `/api/chain` 触发 model3d 带 `drill_feedback_path` 重算 → 有利度更新 → 下一轮布孔（螺旋上升）。

## 输出（`results/<AOI>/drill/<run_id>/`）

- `planned_holes.geojson`：AI 计划孔 `{rank,hole_id,lon,lat,target_depth_m,score,uncertainty,priority}`
- `drill_feedback.geojson`：见矿/无矿 `{hole_id,outcome(ore/barren),element,max_grade,cutoff}`（喂 model3d）
- `holes_db.json`：钻孔库（collar/survey/intervals）
- `figures/siting_map.png` + `metadata.json`（`source=geo-drill`）

下游 `commons/drill_broker.py`：`find_drill_for_bbox / get_holes / get_feedback`。

## 与 geo-model3d 打通（闭环）

```
geo-model3d 预测(有利度+不确定性)
  → geo-drill AI布孔 → 钻探/编录 → 见矿判定 → drill_feedback.geojson
  → /api/chain 回灌 geo-model3d(方向四 P4 的 load_drill_feedback：见矿→正样本 / 无矿→真负样本)
  → 有利度更新 → 下一轮布孔(孔位随之变化＝螺旋上升)
```
回灌半端在方向四 P4 已建好（`geo-model3d/core/labels.py`），geo-drill 只需按其格式产出反馈即闭环。

## 分阶段

- **P1**（本期）：钻孔库 + AI 布孔 + 编录接入 + 见矿判定 + drill_feedback + drill_broker + `/api/chain` 回灌。
- **P2**：value-of-information 期望信息增益布孔 + 斜孔/轨迹设计 + SWIR/XRF 数字岩芯解析。
- **P3**：多轮自动迭代螺旋（geo-drill ↔ model3d）+ 见矿率统计回顾。
- **P4**：成本约束优化（钻探成本 vs 期望价值）+ HELM 现场高光谱岩芯对接。
