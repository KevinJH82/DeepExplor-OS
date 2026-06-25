# geo-7slow:矿床类型驱动的权重预设方案

> 归档日期:2026-06-12
> 主题:给 geo-7slow 的参数设置功能增加"按矿床类型/地质结构切换权重预设"的能力。
> 已定方向:矿床类型来源 = **先手动下拉 + API 留 `deposit_type` 自动接口**;范围 = **仅权重预设**。

---

## 1. 动机:权重是地质先验注入模型的唯一入口

geo-7slow 的 8 个权重决定尖点突变模型里 a(阻力 = ⑥盖层 + ⑦温度)与 b(驱动 = ①②③④⑤ + ⑦)的构成,从而塑造 Δ 与靶区。P4 敏感性已实证其影响极大(cap_rock 清零 → 靶区归零)。但现状是**全局单一默认 + 手动覆盖**,**不按矿床类型区分**——而不同成矿系统控矿要素差异很大(斑岩=蚀变主导、造山金=剪切构造主导……),且下游 `geo-model3d` 本身就是**按矿床族加权**的(`core/knowledge.py` 的 `FAMILY_WEIGHTS`),geo-7slow 用一套默认会与生态不一致。

## 2. 复用资产

- **矿床族权重 + taxonomy**:`/opt/deepexplor-services/geo-model3d/core/knowledge.py`
  - `FAMILY_WEIGHTS`:18 个成因族(porphyry / skarn / epithermal / carlin / vms / iocg / orogenic_gold / …),每族对 `alteration / structure / deformation / depth_consistency` 的权重 + `depth_km` + `applicability` + `note`。
  - `DEPOSIT_TYPE_TO_FAMILY`(geo-analyser 55 类矿床名 → 族)、`COMMODITY_DEFAULT_FAMILY`(矿种 → 族)、`resolve_family()`(优先级解析)。模块自洽(仅 typing 依赖),可 importlib 零污染加载。
- **矿床类型名单**:`/opt/deepexplor-services/geo-analyser/alteration_deposit_db.json`(55 类)。

## 3. 设计

### 3.1 预设来源与派生原则(model3d 4 层证据 → geo-7slow 7 慢变量)
新建 `backend/app/processing/deposit_presets.py`:
- **importlib 复用** model3d `knowledge.py` 的 `DEPOSIT_TYPE_TO_FAMILY` / `COMMODITY_DEFAULT_FAMILY` / `resolve_family`(与 model3d 同一族解析,保证跨服务一致;复用模式同 `delivery.py` / `structural.py`)。
- **本地新作** `FAMILY_SLOW_WEIGHTS: {family -> 8 权重}`,由 model3d 的 `FAMILY_WEIGHTS{A,S,D,P}` 按下述原则派生:
  - 结构组 **①stress + ④fault** ← `structure`(+ `deformation` 并入 stress 的动态项)
  - 蚀变组 **②redox + ⑤chem + ⑥cap_rock** ← `alteration`(redox/chem 入驱动 b,cap_rock 入阻力 a 的封盖项)
  - 热/流体组 **③fluid + ⑦temp** ← 各族热液强度(斑岩/浅成低温/矽卡岩/卡林高;造山/岩浆硫化物/沉积低)
  - 驱动 b 的 6 权重归一到 1(与前端 normalize 一致);阻力 a 的 cap_rock/temp_resist 按族给(默认 0.5/0.5)。

代表性方向(完整 18 族数值在实现时拟定,**标注初版草案、待地质复核**,与 model3d 同口径免责):

| 族 | 方向(相对默认) |
|---|---|
| 斑岩 porphyry | 蚀变 + 构造 + 流体均衡(≈默认),略升 ⑤chem / ③fluid |
| 浅成低温 epithermal | 升 ④fault / ③fluid(脉-断裂 + 热液)、⑥cap_rock(硅化盖) |
| 造山金 orogenic_gold | 大升 ①stress / ④fault(剪切带),降 ②redox / ⑤chem(蚀变弱) |
| 矽卡岩 skarn | 升 ②⑤⑥(接触交代)、④fault、③⑦(接触热) |
| VMS / IOCG | 升 ⑤⑥ / ④(蚀变筒 + 构造);IOCG 强磁 → 留物探方向二 |
| 沉积/红土 sedimentary / laterite | applicability 低,预设保守、UI 标注"适用度低" |

- `resolve_preset_weights(deposit_type=None, family=None, commodity=None) -> dict`:resolve_family → 取 `FAMILY_SLOW_WEIGHTS`(缺则默认)。
- `list_presets() -> {families:[{key,label,note,applicability,weights}], deposit_types:[{name,family}]}`(供前端下拉)。

### 3.2 后端接入
- `app/models/schemas.py`:`AnalysisParams` 增 `deposit_type`(及可选 `family`)——**自动接口**:上游(将来 geo-analyser 推断的矿床类型)经 `/api/analyze` 传入即生效。
- `app/processing/pipeline.py`:权重解析优先级 = `显式 params.weights > deposit_type/family 预设 > DEFAULT_WEIGHTS`。
- `app/api/analysis.py`:新增 `GET /api/deposit-presets` → 返回 `list_presets()`。

### 3.3 前端接入(`ParameterPanel.jsx` + `api/client.js`)
- `client.js` 加 `listDepositPresets()`。
- ParameterPanel 顶部加**矿床类型下拉**(~18 族,中文标签 + applicability 提示);`onChange` → 把该族预设 8 权重写入 `weights` state(6 个驱动滑块随之更新、可继续微调;阻力 cap_rock/temp_resist 显示预设值)。
- `handleRerun` 维持发送 `weights`;可附带 `deposit_type` 备记录。
- 手动 = 选族 → 载入 → 可调 → 重算;自动 = 调用方直接传 `deposit_type`(不传 weights)由后端解析预设。

## 4. 待修改文件
- 新增 `backend/app/processing/deposit_presets.py`
- `backend/app/models/schemas.py`(AnalysisParams 加 deposit_type/family)
- `backend/app/processing/pipeline.py`(权重解析优先级)
- `backend/app/api/analysis.py`(GET /api/deposit-presets)
- `frontend/src/api/client.js`(listDepositPresets)
- `frontend/src/components/ParameterPanel.jsx`(矿床类型下拉 + 载入预设)

## 5. 验证
- `GET /api/deposit-presets` 返回 18 族 + 55 类型映射,字段完整。
- 后端解析:`/api/analyze` 传 `{deposit_type:"造山型金矿"}`(不传 weights)→ orogenic_gold 预设(①④ 显著高于 ②⑤);传 `{deposit_type:"斑岩型铜矿"}` → porphyry;都不传 → 默认。端到端跑招远,确认权重按族切换、Δ/靶区随之变化且合理。
- 前端:下拉选不同族 → 滑块即时载入对应预设;微调后重算正常。
- 回归:不传矿床类型时,与现状默认完全一致(向后兼容)。

## 6. 备注/边界
- 预设数值为**初版草案,待地质专家复核**(沿用 model3d `knowledge.py` 同口径免责);后续可用 P4 `/api/sensitivity` + 已知矿点(需上游标签)做标定。
- **不在本次范围**:plumb/移除 `target_resolution`、把 `ndvi_veg_threshold`/`delta_percentile` 暴露到 UI。
- 上下游整合分析见同目录 `geo-7slow_上下游数据整合分析.md`。
