// 6 阶段定义 + 编排状态机门控逻辑(纯函数,可单测)

export const STAGES = [
  { id: 'plan',     icon: '◷', label: '编排',   title: '编排单' },
  { id: 'data',     icon: '⬇', label: '数据',   title: '数据准备' },
  { id: 'evidence', icon: '◫', label: '证据',   title: '2D 证据层' },
  { id: 'model3d',  icon: '◉', label: '3D建模', title: '3D 融合建模' },
  { id: 'drill',    icon: '⛏', label: '布孔',   title: 'AI 布孔 + 闭环' },
  { id: 'report',   icon: '▤', label: '报告',   title: '综合报告' },
]

export const EVIDENCES = [
  { key: 'analyser', svc: 'analyser', label: '蚀变' },
  { key: 'stru',     svc: 'stru',     label: '构造' },
  { key: 'geophys',  svc: 'geophys',  label: '物探' },
  { key: 'geochem',  svc: 'geochem',  label: '化探' },
  { key: 'insar',    svc: 'insar',    label: '形变' },
]

// 数据源分组:遥感(geo-downloader) + 地球物理/化探先验(data-colle)
// feeds = 该数据源喂给哪个证据服务(用于"选数据源→启用对应证据"的联动)
export const DATA_SOURCES = [
  { group: '遥感影像', note: 'geo-downloader', items: [
    { key: 'sentinel2', label: 'Sentinel-2', def: true, service: 'downloader' },
    { key: 'landsat8',  label: 'Landsat-8',  def: true, service: 'downloader' },
    { key: 'sentinel1', label: 'Sentinel-1', def: true, service: 'downloader' },
    { key: 'aster',     label: 'ASTER',      def: false, service: 'downloader' },
    { key: 'dem',       label: 'DEM',        def: true, service: 'downloader' },
  ] },
  { group: 'InSAR 形变', note: 'geo-insar', items: [
    { key: 'insar_stack', label: 'InSAR 时序', def: false, service: 'insar', feeds: 'insar' },
  ] },
  { group: '地球物理位场', note: 'data-colle', items: [
    { key: 'emag2',   label: 'EMAG2 磁',        def: true, service: 'datacolle', feeds: 'geophys' },
    { key: 'gravity', label: 'WGM/ICGEM 重力',  def: true, service: 'datacolle', feeds: 'geophys' },
  ] },
  { group: '化探·地质先验', note: 'data-colle', items: [
    { key: 'geochem_bg', label: '化探背景值', def: true, service: 'datacolle', feeds: 'geochem' },
    { key: 'mineral_kb', label: '矿种知识库', def: true, service: 'datacolle' },
  ] },
]

const _SRC_ITEMS = DATA_SOURCES.flatMap((g) => g.items)
export const SOURCE_ITEMS = _SRC_ITEMS
export const SOURCE_SERVICES = Object.fromEntries(_SRC_ITEMS.map((i) => [i.key, i.service || 'datacolle']))
export const DEFAULT_SOURCE_KEYS = _SRC_ITEMS.filter((i) => i.def).map((i) => i.key)
export const REMOTE_SENSING_KEYS = _SRC_ITEMS.filter((i) => i.service === 'downloader').map((i) => i.key)  // 透传 downloader
export const INSAR_SOURCE_KEYS = _SRC_ITEMS.filter((i) => i.service === 'insar').map((i) => i.key)
export const DATACOLLE_KEYS = _SRC_ITEMS.filter((i) => i.service === 'datacolle').map((i) => i.key)
// 数据源 → 所喂证据服务
export const SOURCE_FEEDS = Object.fromEntries(_SRC_ITEMS.filter((i) => i.feeds).map((i) => [i.key, i.feeds]))

export const ALL_EVIDENCE_KEYS = EVIDENCES.map((e) => e.key)

export const stageIndex = (id) => STAGES.findIndex((s) => s.id === id)

// 进入 model3d 需要"足够多"证据完成(默认 ≥3,允许降级;选得少则按所选数)
export const EVIDENCE_GATE = 3

export function evidenceDoneCount(evidences, selected = ALL_EVIDENCE_KEYS) {
  const valid = new Set(ALL_EVIDENCE_KEYS)
  return selected.filter((k) => valid.has(k) && (
    evidences[k]?.status === 'completed' || evidences[k]?.degraded
  )).length
}

export function evidencePlanGateDetails(evidences, evidencePlan, selected = ALL_EVIDENCE_KEYS) {
  if (!evidencePlan?.gate) return null
  const enabled = (evidencePlan.evidence_tasks || [])
    .filter((t) => t.enabled !== false)
    .map((t) => t.key)
    .filter((k) => ALL_EVIDENCE_KEYS.includes(k))
  const scope = enabled.length ? enabled : selected
  const minDone = Math.min(evidencePlan.gate.min_completed || EVIDENCE_GATE, scope.length || EVIDENCE_GATE)
  const done = evidenceDoneCount(evidences, scope)
  const requiredAny = (evidencePlan.gate.required_any || []).filter((k) => scope.includes(k))
  const requiredDone = requiredAny.filter((k) => evidences[k]?.status === 'completed' || evidences[k]?.degraded)
  const requiredOk = !requiredAny.length || requiredDone.length > 0
  return {
    passed: done >= minDone && requiredOk,
    done,
    minDone,
    scope,
    requiredAny,
    requiredDone,
    missingRequiredAny: requiredOk ? [] : requiredAny,
  }
}

export function evidencePlanGatePassed(evidences, evidencePlan, selected = ALL_EVIDENCE_KEYS) {
  const details = evidencePlanGateDetails(evidences, evidencePlan, selected)
  return details ? details.passed : null
}

// 某阶段是否解锁(门控);selected 为本次参与的证据 key 列表
export function isUnlocked(id, stages, evidences, selected = ALL_EVIDENCE_KEYS, evidencePlan = null) {
  const i = stageIndex(id)
  if (i <= 0) return true
  if (id === 'model3d') {
    const planGate = evidencePlanGatePassed(evidences, evidencePlan, selected)
    if (planGate !== null) return stages.evidence?.status === 'completed' || planGate
    const valid = new Set(ALL_EVIDENCE_KEYS)
    const validSelected = selected.filter((k) => valid.has(k))
    const gate = Math.min(EVIDENCE_GATE, validSelected.length || EVIDENCE_GATE)
    return stages.evidence?.status === 'completed' ||
           evidenceDoneCount(evidences, validSelected) >= gate
  }
  const prev = STAGES[i - 1].id
  return stages[prev]?.status === 'completed'
}
