import { EVIDENCES, DATA_SOURCES, SOURCE_SERVICES } from './stages'

export const STATUS_TEXT = {
  pending: '待运行',
  running: '运行中',
  completed: '完成',
  failed: '失败',
  skipped: '跳过',
}

export function statusText(status) {
  return STATUS_TEXT[status] || status || '待运行'
}

export function statusTone(status) {
  return {
    completed: 'ok',
    running: 'run',
    failed: 'er',
    skipped: 'wt',
    pending: 'wt',
  }[status] || 'wt'
}

export function evidenceSourceLabel(ev = {}) {
  if (ev.status === 'failed') return '失败'
  if (ev.status === 'skipped') return '已跳过'
  if (ev.status === 'running') return '真实服务中'
  if (ev.modelDerived) return '3D融合输入'
  if (ev.archived) return '已有产物/待接入'
  if (ev.degraded) return '降级完成/无栅格'
  if (ev.taskId) return ev.noLayer ? '真实结果/无栅格' : '真实结果'
  if (ev.status === 'completed') return ev.noLayer ? '回退模拟/无栅格' : '回退模拟'
  return '未生成'
}

export function evidenceQuality(ev = {}) {
  if (ev.status === 'failed') return '不可用'
  if (ev.status === 'skipped') return '不参与'
  if (ev.status === 'running') return '待评估'
  if (ev.status !== 'completed') return '缺失'
  if (ev.modelDerived) return ev.noLayer ? '已入模/待核图' : '已入模'
  if (ev.archived) return '待复核'
  if (ev.degraded) return '低置信'
  if (ev.layerUrl) return ev.taskId ? '高' : '演示'
  if (ev.noLayer) return '低'
  return ev.taskId ? '中' : '演示'
}

export function evidenceCoverage(ev = {}) {
  if (ev.layerUrl) return 'AOI 栅格'
  if (ev.modelDerived) return ev.noLayer ? '模型输入摘要' : '3D 输入层'
  if (ev.archived) return '历史产物'
  if (ev.noLayer) return '无可视栅格'
  if (ev.status === 'completed') return '产物待接入'
  if (ev.status === 'running') return `${ev.progress || 0}%`
  return '-'
}

export function evidenceContribution(key, ev = {}) {
  if (ev.status === 'failed') return '缺失'
  if (ev.status === 'skipped') return '不参与'
  if (ev.status === 'running') return '待判定'
  if (ev.status !== 'completed') return '缺失'
  if (ev.weight != null && ev.status === 'completed') return Number(ev.weight) >= 0.25 ? '强支撑' : '中支撑'
  if (ev.modelDerived) return ['analyser', 'stru'].includes(key) ? '强支撑' : '中支撑'
  if (ev.archived) return '待核验'
  if (ev.degraded) return '弱支撑'
  if (ev.noLayer) return key === 'geochem' ? '弱支撑' : '弱支撑'
  if (ev.layerUrl) return ['analyser', 'stru'].includes(key) ? '强支撑' : '中支撑'
  return ev.taskId ? '中支撑' : '演示支撑'
}

export function evidenceRelation(key, ev = {}) {
  if (ev.status === 'failed') return ev.error ? '运行失败' : '证据缺失'
  if (ev.status === 'skipped') return '本次不参与'
  if (ev.status === 'running') return '正在生成异常证据'
  if (ev.modelDerived) return ev.reason || '已作为 3D 融合输入参与靶点评分'
  if (ev.archived) return ev.skipReason || '已有历史产物, 当前视图待接入'
  if (ev.degraded) return '服务失败,按低置信缺失证据纳入'
  if (ev.noLayer) return key === 'geochem' ? '点/矢量证据待接入' : '无可叠加栅格'
  if (ev.layerUrl) return '异常层已叠加'
  if (ev.status === 'completed') return '产物已完成待解析'
  return '未参与推理'
}

export function buildEvidenceRows(evidences, selected) {
  const selectedKeys = selected?.length ? selected : EVIDENCES.map((e) => e.key)
  return EVIDENCES.filter((e) => selectedKeys.includes(e.key)).map((e) => {
    const ev = evidences[e.key] || {}
    return {
      key: e.key,
      label: e.label,
      status: ev.status || 'pending',
      source: evidenceSourceLabel(ev),
      quality: evidenceQuality(ev),
      coverage: evidenceCoverage(ev),
      relation: evidenceRelation(e.key, ev),
      contribution: evidenceContribution(e.key, ev),
      weight: ev.weight,
      requiredLevel: ev.planTask?.required_level,
      recommended: ev.planTask?.recommended,
      taskId: ev.taskId,
      layerUrl: ev.layerUrl,
      noLayer: ev.noLayer,
      degraded: ev.degraded,
      archived: ev.archived,
      modelDerived: ev.modelDerived,
      modelLayer: ev.modelLayer,
      modelSourceStatus: ev.modelSourceStatus,
      reason: ev.reason,
      skipReason: ev.skipReason,
      error: ev.error,
    }
  })
}

export function sourceLabels(keys = [], sourceMeta = {}) {
  const byKey = new Map(DATA_SOURCES.flatMap((g) => g.items.map((i) => [i.key, i.label])))
  return keys.map((k) => {
    const meta = sourceMeta[k] || {}
    const seasons = (meta.seasons || []).join('/')
    const req = meta.required ? '必选' : ''
    const suffix = [seasons, req].filter(Boolean).join(' · ')
    return suffix ? `${byKey.get(k) || k} · ${suffix}` : (byKey.get(k) || k)
  })
}

export function stageTrace(active, ctx) {
  const selectedSources = ctx.selectedSources || []
  const sourceMeta = ctx.sourceMeta || {}
  const selectedEvidence = ctx.selectedEvidence || []
  const rs = sourceLabels(selectedSources.filter((k) => SOURCE_SERVICES[k] === 'downloader'), sourceMeta)
  const insar = sourceLabels(selectedSources.filter((k) => SOURCE_SERVICES[k] === 'insar'), sourceMeta)
  const dc = sourceLabels(selectedSources.filter((k) => SOURCE_SERVICES[k] === 'datacolle'), sourceMeta)
  const evLabels = EVIDENCES.filter((e) => selectedEvidence.includes(e.key)).map((e) => e.label)
  const stage = ctx.stages?.[active] || {}
  const taskId = stage.taskId ||
    (active === 'model3d' ? ctx.model3d?.taskId : active === 'drill' ? ctx.drill?.taskId : null)

  const map = {
    plan: {
      input: [ctx.current?.kml_name || '样例 AOI', ctx.current?.mineral_label || '矿种待定'],
      process: ['orchestrator 编排', '本地兜底计划'],
      output: [ctx.traceId || 'trace_id 待生成'],
    },
    data: {
      input: [...rs, ...insar, ...dc],
      process: [
        rs.length ? 'downloader' : 'downloader 未启用',
        insar.length ? 'geo-insar' : 'geo-insar 未启用',
        dc.length ? 'data-colle' : 'data-colle 未启用',
        'preprocess',
      ],
      output: ['遥感/DEM/先验资料', '预处理输入包'],
    },
    evidence: {
      input: [...rs, ...dc],
      process: evLabels,
      output: ['证据栅格/矢量', '证据质量与贡献矩阵'],
    },
    model3d: {
      input: evLabels,
      process: [ctx.model3d?.stats?.fusion_method || 'knowledge fusion'],
      output: [`靶点 ${ctx.model3d?.targets?.length || 0} 个`, '深度切片', '模型统计'],
    },
    drill: {
      input: ['3D 靶点', '孔距/深度约束'],
      process: ['drill service', '闭环反馈'],
      output: [`钻孔 ${ctx.drill?.holes?.length || 0} 个`, 'drill_feedback'],
    },
    report: {
      input: ['项目 AOI', '证据矩阵', '3D 靶点', '钻孔建议'],
      process: ['reporter'],
      output: ['可审计综合报告'],
    },
  }[active] || { input: [], process: [], output: [] }

  return {
    ...map,
    audit: [
      `trace_id: ${ctx.traceId || '-'}`,
      `task_id: ${taskId || '-'}`,
      `状态: ${statusText(stage.status)}`,
      `来源: ${taskId ? '真实服务' : stage.status === 'completed' ? '本地/回退' : '待运行'}`,
    ],
  }
}
