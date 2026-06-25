# geo-7slow 作为证据层服务的接入与首轮矿床类型上下文修正方案

## Summary

将 geo-7slow 定位为 `slow variables evidence provider`,对外输出 7 个慢变量、综合证据层和解释层;同时修正当前首轮分析缺少矿种/矿床类型上下文的问题。

新的分析流程应为:ROI/项目进入后,先获取遥感数据和地质上下文,再启动第一轮分析。矿床类型默认来自 geo-stru 的 `deposit_inference.primary_model`,并在第一轮就用于权重预设;用户后续仍可在参数面板中调整并重跑。

## Key Changes

- 增加“地质上下文解析”步骤:
  - 首选来源:geo-stru 输出的 `deposit_inference`
  - 关键字段:`primary_model`、`primary_confidence`、`candidates`、`mineral_hint`、`structural_control_summary`
  - geo-7slow 使用 `primary_model` 作为 `deposit_type`
  - 使用 `mineral_hint` 或用户选择矿种作为 `mineral`
  - 通过 `deposit_presets.resolve_preset_weights()` 解析第一轮权重
- 调整上下文优先级:
  - 显式用户权重 `weights`
  - 用户显式选择的 `deposit_type/family`
  - geo-stru 推理的 `deposit_type`
  - 用户选择或 geo-stru 提供的 `mineral`
  - 默认权重
- 修正当前参数链路问题:
  - 前端第一轮 `startAnalysis(uploadId)` 改为带 `params`
  - `commodity` 字段统一改为后端已有的 `mineral`,后端可兼容接收 `commodity`
  - 后端 `pipeline.py` 调用 `resolve_preset_weights()` 时传入 `mineral`
  - `metadata.json` / `manifest.json` 记录本轮使用的 `deposit_type`、`family`、`mineral`、`geologic_context_source`、`geo_struct_confidence`

## Implementation Changes

- 后端交付准备:
  - 扩展 `delivery.prepare_session()` 返回 `geologic_context`
  - 在项目目录、geo-stru 产物目录或共享 broker 中查找 geo-stru `metadata.json`
  - 若发现 `deposit_inference.primary_model`,写入 `deposit_type`、`deposit_type_confidence`、`deposit_candidates`、`mineral_hint`、`structural_control_summary`、`source`
  - `/api/delivery/prepare` 将 `geologic_context` 一并返回前端
- 后端分析入口:
  - `/api/analyze` 接收首轮 `params.deposit_type/mineral/family`
  - `/api/start` 保持兼容,并同样支持从交付项目自动补 geo-stru 上下文
  - `run_pipeline()` 权重解析优先级为 `weights > deposit_type/family/mineral > DEFAULT_WEIGHTS`
  - 解析出的 `family` 和权重写入结果元数据
- 前端状态:
  - 在 `analysisStore` 增加 `geologicContext`、`selectedMineral`、`selectedDepositType`
  - `UploadPanel.applyDeliveryResult()` 保存后端返回的 `geologic_context`
  - 第一轮点击分析时调用 `startAnalysis(uploadId, { mineral, deposit_type, family })`
  - 若 geo-stru 给出矿床类型,页面展示来源和置信度
- 前端参数面板:
  - 初始值读取 `results.params_used` 和 store 中的 `geologicContext`
  - 用户手动改矿种/矿床类型后,后续重跑以用户选择为准
  - `handleRerun()` 发送 `mineral`,不再发送后端未使用的 `commodity`
  - 若未取得 geo-stru 推理结果,允许用户在首次分析前手动选择矿种/矿床类型;不选择则使用默认权重
- 证据层输出:
  - 保留现有 7 个慢变量 COG 文件
  - 扩展 `manifest.json` 中每个 layer 的语义字段
  - 增加 `evidence_catalog`,说明 7 个变量如何作为其他子系统证据使用
  - 新增或对接 `slowvars_broker`,按 bbox / AOI 返回 COG、GeoJSON、stats、上下文和图层语义

## Consumer Integration

- `geo-model3d`:消费 7 个慢变量或 `delta_discriminant/driving_force_b`,并将 geo-7slow 结果作为软证据。
- `geo-drill`:使用 `target_zones.geojson` 作为候选区预过滤,用 `dominant_driver`、`delta_discriminant`、`driving_force_b` 解释和排序孔位。
- `geo-exploration`:实现 `SlowVarsDetector`,输入 7 个慢变量与综合层,输出 prospectivity grid 与 Top targets。
- `geo-reporter`:新增慢变量综合证据章节,报告矿床类型来源、geo-stru 置信度、权重预设、主控慢变量和靶区解释。

## Test Plan

- 首轮分析测试:
  - ROI/项目准备后返回 `geologic_context`
  - 首轮 `/api/analyze` 请求包含 `deposit_type/mineral`
  - `params_used.weights` 与该矿床类型预设一致
  - `metadata.json` 记录矿床类型来源和 geo-stru 置信度
- 回退测试:
  - geo-stru 无推理结果时,用户可手动选择矿种/矿床类型
  - 用户不选择时,使用默认权重且流程不中断
  - 用户二次调整权重时,显式 `weights` 优先于 geo-stru 推理
- 字段兼容测试:
  - `mineral` 和旧前端可能传入的 `commodity` 至少一段过渡期内都能解析
  - `/api/start` 和 `/api/analyze` 输出的 metadata 字段一致
- 证据层测试:
  - 7 个慢变量、综合层、`dominant_driver`、`target_zones.geojson` 正常写出
  - `manifest.json` 图层语义完整
  - `slowvars_broker` 能按 bbox 返回对应证据层
  - 下游缺少 geo-7slow 结果时保持降级运行

## Assumptions

- geo-stru 的 `deposit_inference.primary_model` 是矿床类型首选来源。
- geo-stru 推理属于结构证据驱动的间接判断,低置信度时仍可作为默认建议,但前端应显示来源和置信度。
- 用户显式选择矿床类型或权重时优先级高于 geo-stru 自动推理。
- 本轮方案不重新设计圈靶算法,只修正第一轮上下文进入时机,并把 7 个慢变量扩展为跨子系统证据产品。
