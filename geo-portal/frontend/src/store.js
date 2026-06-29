import { create } from 'zustand'
import * as api from './api/portal'
import { setAccessToken } from './api/client'
import { STAGES, EVIDENCES, DEFAULT_SOURCE_KEYS, ALL_EVIDENCE_KEYS, SOURCE_SERVICES } from './lib/stages'
import { loadSliceTexture, loadEvidenceRaster } from './lib/sliceTexture'

// 正在轮询的任务键集合(traceId:stage:taskId),防止"重开续接"重复起轮询
const _activePolls = new Set()

// ── 认证 ──
// access token 存内存(client.js),refresh 是 HttpOnly cookie。
// 刷新页面后内存丢失 → boot() 用 refresh cookie 静默换回 access。
export const useAuth = create((set) => ({
  user: null,
  ready: false,
  async boot() {
    try {
      const { token, user } = await api.refresh()
      setAccessToken(token)
      set({ user, ready: true })
    } catch {
      setAccessToken(null)
      set({ user: null, ready: true })
    }
  },
  async login(username, password) {
    const { token, user } = await api.login(username, password)
    setAccessToken(token)
    set({ user })
    return user
  },
  async logout() {
    try { await api.logout() } catch {}
    setAccessToken(null)
    set({ user: null })
  },
}))

// ── 管理员角标:全局待审账号申请数(TopBar 轮询 + 审核后刷新)──
export const useAdminBadge = create((set) => ({
  pending: 0,
  async refresh() {
    try {
      const list = await api.adminListApplications('pending')
      set({ pending: Array.isArray(list) ? list.length : 0 })
    } catch { /* 非管理员/未登录:静默,不影响界面 */ }
  },
}))

// ── 项目 + 当前运行(trace_id 主线)──
export const useProject = create((set, get) => ({
  projects: [],
  current: null,        // 当前项目
  traceId: null,        // 当前运行主键
  manualEvidence: [],   // 手工提交证据(项目级,预留入口)
  async refresh() { set({ projects: await api.listProjects() }) },
  async loadManualEvidence(projectId) {
    const id = projectId || get().current?.id
    if (!id) return
    try { set({ manualEvidence: await api.listManualEvidence(id) }) }
    catch { set({ manualEvidence: [] }) }
  },
  async open(id) {
    const p = await api.getProject(id)
    set({ current: p, traceId: p.current_run || null, manualEvidence: [] })
    get().loadManualEvidence(id).catch(() => {})
    if (p.current_run) {
      try {
        const run = await api.getRun(p.current_run)
        const workflow = useWorkflow.getState()
        workflow.hydrate(run)
        workflow.setActive('plan')
        workflow.loadExistingModel3d(id).catch(() => {})
        workflow.loadExistingDrill(id).catch(() => {})
      } catch {}
    } else {
      useWorkflow.getState().reset()
    }
    return p
  },
  setTrace(traceId) { set({ traceId }) },
  patchCurrent(patch) { set((s) => ({ current: s.current ? { ...s.current, ...patch } : s.current })) },
  forget(id) { set((s) => (s.current?.id === id ? { current: null, traceId: null } : {})) },
  async switchRun(traceId) {
    set({ traceId })
    try {
      const run = await api.getRun(traceId)
      const workflow = useWorkflow.getState()
      workflow.hydrate(run)
      workflow.loadExistingModel3d(run.project_id).catch(() => {})
      workflow.loadExistingDrill(run.project_id).catch(() => {})
    } catch {}
  },
}))

// 初始阶段状态
function freshStages() {
  const s = {}
  STAGES.forEach((st) => { s[st.id] = { status: 'pending', progress: 0 } })
  return s
}
function freshEvidences() {
  const e = {}
  EVIDENCES.forEach((ev) => { e[ev.key] = { status: 'pending', progress: 0 } })
  return e
}

const SERVICE_ALIASES = {
  'geo-downloader': 'downloader',
  'geo-preprocess': 'preprocess',
  'data-colle': 'datacolle',
  'data_colle': 'datacolle',
  'geo-analyser': 'analyser',
  'geo-stru': 'stru',
  'geo-geophys': 'geophys',
  'geo-geochem': 'geochem',
  'geo-insar': 'insar',
  'geo-7slow': 'slowvars',
  'geo-slowvars': 'slowvars',
  'geo-exploration': 'exploration',
  'geo-model3d': 'model3d',
  'geo-drill': 'drill',
  'geo-reporter': 'reporter',
}

const SERVICE_STAGE = {
  downloader: 'data',
  preprocess: 'data',
  datacolle: 'data',
  analyser: 'evidence',
  stru: 'evidence',
  geophys: 'evidence',
  geochem: 'evidence',
  insar: 'evidence',
  slowvars: 'evidence',
  exploration: 'evidence',
  model3d: 'model3d',
  drill: 'drill',
  reporter: 'report',
}

const SERVICE_EVIDENCE = {
  analyser: 'analyser',
  stru: 'stru',
  geophys: 'geophys',
  geochem: 'geochem',
  insar: 'insar',
}
const SENSOR_ALIASES = {
  sentinel2: 'sentinel2',
  'sentinel-2': 'sentinel2',
  s2: 'sentinel2',
  landsat: 'landsat8',
  landsat8: 'landsat8',
  'landsat-8': 'landsat8',
  landsat9: 'landsat8',
  sentinel1: 'sentinel1',
  'sentinel-1': 'sentinel1',
  s1: 'sentinel1',
  aster: 'aster',
  dem: 'dem',
  copernicus_dem: 'dem',
  'copernicus-dem': 'dem',
  copernicusdem: 'dem',
  emag2: 'emag2',
  magnetic: 'emag2',
  gravity: 'gravity',
  wgm: 'gravity',
  icgem: 'gravity',
  geochem_bg: 'geochem_bg',
  'geochem-bg': 'geochem_bg',
  geochem: 'geochem_bg',
  mineral_kb: 'mineral_kb',
  'mineral-kb': 'mineral_kb',
  mineral: 'mineral_kb',
  insar_stack: 'insar_stack',
  'insar-stack': 'insar_stack',
  insar: 'insar_stack',
  'geo-insar': 'insar_stack',
}

const MODEL_STATS_EVIDENCE = {
  alteration: 'analyser',
  analyser: 'analyser',
  structure: 'stru',
  stru: 'stru',
  deformation: 'insar',
  insar: 'insar',
  geophys: 'geophys',
  geophysics: 'geophys',
  magnetic: 'geophys',
  gravity: 'geophys',
  geochem: 'geochem',
  geochemical: 'geochem',
}

const MODEL_STATS_REASON = {
  analyser: '三维融合统计显示蚀变层已作为输入证据参与靶点评分',
  stru: '三维融合统计显示构造层已作为输入证据参与靶点评分',
  insar: '三维融合统计显示形变层已作为输入证据参与靶点评分',
  geophys: '三维融合统计显示物探层已作为输入约束参与靶点评分',
  geochem: '三维融合统计显示化探资料已作为输入约束参与靶点评分',
}

function modelEvidenceKey(name) {
  return MODEL_STATS_EVIDENCE[String(name || '').trim().toLowerCase().replaceAll('-', '_')]
}

function modelSourceStatusOk(status) {
  return ['ok', 'completed', 'complete', 'available', 'used'].includes(String(status || '').toLowerCase())
}

function modelSourceStatusKnown(status) {
  return ['no_raster', 'summary_only', 'regional', 'weak', 'used_without_raster'].includes(String(status || '').toLowerCase())
}

function patchesFromModelStats(stats = {}) {
  const patches = {}
  const mark = (key, patch = {}) => {
    if (!key || !patches[key]) patches[key] = { key }
    patches[key] = { ...patches[key], ...patch }
  }

  ;(stats.available_layers || []).forEach((layer) => {
    const key = modelEvidenceKey(layer)
    if (key) mark(key, {
      modelLayer: String(layer),
      reason: MODEL_STATS_REASON[key],
      noLayer: false,
    })
  })

  Object.entries(stats.data_sources || {}).forEach(([source, detail]) => {
    const key = modelEvidenceKey(source)
    if (!key) return
    const status = typeof detail === 'string' ? detail : detail?.status
    const note = typeof detail === 'object' ? (detail.note || detail.reason || detail.kind || '') : ''
    if (modelSourceStatusOk(status)) {
      mark(key, {
        modelLayer: source,
        modelSourceStatus: status,
        reason: note || MODEL_STATS_REASON[key],
        noLayer: false,
      })
    } else if (modelSourceStatusKnown(status)) {
      mark(key, {
        modelLayer: source,
        modelSourceStatus: status,
        reason: note || MODEL_STATS_REASON[key],
        noLayer: true,
      })
    }
  })

  return patches
}

function mergeEvidenceFromModelStats(evidences, stats = {}) {
  const patches = patchesFromModelStats(stats)
  const next = { ...evidences }
  Object.entries(patches).forEach(([key, patch]) => {
    const cur = next[key] || {}
    if (cur.status === 'running') return
    const shouldProbeLayer = patch.noLayer && cur.status === 'completed' && cur.taskId && !cur.layerLoadFailed
    next[key] = {
      ...cur,
      status: 'completed',
      progress: 100,
      modelDerived: true,
      modelLayer: patch.modelLayer || cur.modelLayer,
      modelSourceStatus: patch.modelSourceStatus || cur.modelSourceStatus,
      modelNoLayer: patch.noLayer ?? cur.modelNoLayer,
      reason: cur.reason || patch.reason || MODEL_STATS_REASON[key],
      noLayer: cur.layerUrl ? false : (shouldProbeLayer ? false : (patch.noLayer ?? cur.noLayer ?? true)),
    }
  })
  return next
}

function evidenceKeysFromModelStats(stats = {}) {
  return Object.keys(patchesFromModelStats(stats)).filter((key) => ALL_EVIDENCE_KEYS.includes(key))
}

function normService(name) {
  const key = String(name || '').trim().toLowerCase().replaceAll('_', '-')
  return SERVICE_ALIASES[key] || key
}

function normSourceKey(name) {
  const key = String(name || '').trim().toLowerCase().replaceAll('_', '-')
  return SENSOR_ALIASES[key] || key
}

function normGroupStatus(group = {}) {
  if (group.skip) return group.skip_reason ? 'completed' : 'skipped'
  return 'pending'
}

function finalizeStageStatus(stages) {
  Object.values(stages).forEach((st) => {
    const tasks = Object.values(st.sub_tasks || {})
    if (!tasks.length) return
    const pending = tasks.some((t) => t.status === 'pending')
    st.status = pending ? 'pending' : 'completed'
    st.progress = pending ? 0 : 100
  })
}

function stagesFromPlan(plan = {}) {
  const stages = {}
  ;(plan.stages || []).forEach((s) => {
    const sid = String(s.stage || s.name || '')
    if (!sid) return
    stages[sid] = {
      status: 'pending',
      progress: 0,
      services: (s.services || []).map(normService).filter(Boolean),
      sub_tasks: {},
    }
  })
  ;(plan.execution_plan?.phases || []).forEach((phase) => {
    ;(phase.parallel_groups || []).forEach((group) => {
      const service = normService(group.service)
      const phaseText = `${phase.name || ''}`
      const isDataPhase = /数据|获取/.test(phaseText) || Number(phase.phase) === 1
      const sid = service === 'insar' && isDataPhase ? 'data' : SERVICE_STAGE[service]
      if (!sid) return
      const st = stages[sid] || { status: 'pending', progress: 0, services: [], sub_tasks: {} }
      if (service && !st.services.includes(service)) st.services.push(service)
      st.sub_tasks[service] = {
        status: normGroupStatus(group),
        required: !!group.required,
        reason: group.reason || '',
        skip_reason: group.skip_reason || '',
        tasks: group.tasks || [],
      }
      stages[sid] = st
    })
  })
  finalizeStageStatus(stages)
  return stages
}

export function dataSourcesFromPlan(plan = {}) {
  const fallbackKeys = []
  const fallbackMeta = {}
  const execKeys = []
  const execMeta = {}
  const add = (list, meta, key, patch = {}) => {
    const sourceKey = normSourceKey(key)
    if (!SOURCE_SERVICES[sourceKey]) return
    if (!list.includes(sourceKey)) list.push(sourceKey)
    meta[sourceKey] = { ...(meta[sourceKey] || {}), ...patch }
  }

  ;(plan.sources || []).forEach((key) => add(fallbackKeys, fallbackMeta, key, { source: 'plan' }))
  ;(plan.ui_sources || []).forEach((key) => add(fallbackKeys, fallbackMeta, key, { source: 'ui' }))
  ;(plan.stages || []).forEach((stage) => {
    ;(stage.sensors || []).forEach((key) => add(fallbackKeys, fallbackMeta, key, { service: 'downloader', source: 'stage' }))
    ;(stage.datacolle || []).forEach((key) => add(fallbackKeys, fallbackMeta, key, { service: 'datacolle', source: 'stage' }))
  })
  ;(plan.execution_plan?.phases || []).forEach((phase) => {
    ;(phase.parallel_groups || []).forEach((group) => {
      const service = normService(group.service)
      if (service === 'insar' && group.skip !== true) {
        add(execKeys, execMeta, 'insar_stack', {
          service,
          required: !!group.required,
          reason: group.reason || '',
          phase: phase.name,
        })
      }
      ;(group.tasks || []).forEach((task) => {
        if (task.required === false) return
        const sensor = task.sensor || task.source || task.dataset || task.type
        if (sensor) add(execKeys, execMeta, sensor, {
          service,
          required: task.required !== false,
          seasons: task.seasons || [],
          reason: task.reason || '',
          phase: phase.name,
        })
      })
    })
  })
  if (execKeys.length) {
    fallbackKeys.forEach((key) => add(execKeys, execMeta, key, fallbackMeta[key] || { source: 'ui' }))
    return { keys: execKeys, meta: execMeta }
  }
  return { keys: fallbackKeys.length ? fallbackKeys : DEFAULT_SOURCE_KEYS, meta: fallbackMeta }
}

function nextActiveStage(stages) {
  return STAGES.find((st) => st.id !== 'plan' && stages[st.id]?.status !== 'completed')?.id || 'report'
}

function hydrateEvidenceFromStages(stages, evidences) {
  Object.entries(stages.evidence?.sub_tasks || {}).forEach(([service, sub]) => {
    const key = SERVICE_EVIDENCE[service]
    if (!key || !evidences[key]) return
    evidences[key] = {
      ...evidences[key],
      status: sub.status || 'pending',
      progress: ['completed', 'skipped'].includes(sub.status) ? 100 : 0,
      archived: sub.status === 'completed' && !!sub.skip_reason,
      skipReason: sub.skip_reason || '',
      reason: sub.reason || '',
      noLayer: false,
    }
  })
}

function evidenceKeysFromPlan(evidencePlan = {}) {
  return (evidencePlan.evidence_tasks || [])
    .filter((t) => t.enabled !== false)
    .map((t) => t.key)
    .filter((k) => ALL_EVIDENCE_KEYS.includes(k))
}

function planWithTaskPatch(evidencePlan, key, patch) {
  if (!evidencePlan) return null
  return {
    ...evidencePlan,
    evidence_tasks: (evidencePlan.evidence_tasks || []).map((t) => (
      t.key === key ? { ...t, ...patch } : t
    )),
  }
}

function mergeEvidencePlanStatuses(evidences, evidencePlan = {}) {
  const next = { ...evidences }
  ;(evidencePlan.evidence_tasks || []).forEach((task) => {
    const key = task.key
    if (!key || !next[key]) return
    if (task.enabled === false) {
      next[key] = { ...next[key], status: next[key].status === 'completed' ? 'completed' : 'skipped', progress: next[key].progress || 0, planTask: task }
      return
    }
    const degraded = task.status === 'degraded' || task.degraded
    const status = degraded ? 'completed' : (task.status || next[key].status)
    const hasTaskIdField = Object.prototype.hasOwnProperty.call(task, 'task_id')
    const taskId = hasTaskIdField ? (task.task_id || '') : (next[key].taskId || '')
    const clearLayer = status !== 'completed' || !taskId || (next[key].taskId && taskId !== next[key].taskId)
    next[key] = {
      ...next[key],
      planTask: task,
      weight: task.weight,
      reason: task.reason || next[key].reason,
      taskId,
      status,
      progress: task.progress ?? next[key].progress,
      error: task.error || next[key].error || '',
      degraded,
      skipReason: task.skip_reason || next[key].skipReason || '',
      layerUrl: clearLayer ? undefined : next[key].layerUrl,
      visible: clearLayer ? false : next[key].visible,
      layerLoading: clearLayer ? false : next[key].layerLoading,
      layerLoadFailed: clearLayer ? false : next[key].layerLoadFailed,
      noLayer: degraded ? true : (clearLayer ? false : next[key].noLayer),
    }
  })
  return next
}

function hydrateCompletedEvidenceLayers(get, evidencePlan = {}) {
  ;(evidencePlan.evidence_tasks || []).forEach((task) => {
    if (task.enabled === false || task.status !== 'completed' || !task.task_id) return
    const ev = EVIDENCES.find((e) => e.key === task.key)
    if (!ev) return
    const cur = get().evidences[task.key] || {}
    if (cur.layerUrl || cur.layerLoading || cur.noLayer) return
    get().loadEvidenceLayer(task.key, ev.svc, task.task_id)
  })
}

// 证据阶段:所选证据全部 settled 则标完成
function checkEvidenceSettle(get) {
  const evs = get().evidences
  const valid = new Set(ALL_EVIDENCE_KEYS)
  const sel = (get().selectedEvidence || ALL_EVIDENCE_KEYS).filter((k) => valid.has(k))
  const settled = sel.every((k) => ['completed', 'failed', 'skipped'].includes(evs[k]?.status) || evs[k]?.degraded)
  if (settled) get().setStage('evidence', { status: 'completed', progress: 100 })
}
// 正在轮询的证据任务(key:taskId),防重复轮询(主动运行 + 重载恢复同时触发)
const _pollingTasks = new Set()

// 单个证据的模拟进度(服务不可达时回退;geochem 演示失败以展示降级)
function simulateEvidence(get, ev, traceId = null) {
  const fail = ev.key === 'geochem'
  let p = 0
  const t = setInterval(() => {
    p += 12 + Math.random() * 10
    if (p >= 100) {
      clearInterval(t)
      const status = fail ? 'failed' : 'completed'
      get().setEvidence(ev.key, { status, progress: 100 })
      get().persistEvidenceTask(traceId, ev.key, {
        status,
        progress: 100,
        error: fail ? '回退模拟任务失败' : '',
      })
      checkEvidenceSettle(get)
    } else get().setEvidence(ev.key, { progress: Math.round(p) })
  }, 300)
}

// ── 编排状态机 ──
export const useWorkflow = create((set, get) => ({
  active: 'plan',                 // 当前查看的阶段
  stages: freshStages(),
  evidences: freshEvidences(),
  focusEvidence: null,            // ③证据 子tab 聚焦的证据(null=全部叠加)
  selectedEvidence: [...ALL_EVIDENCE_KEYS],          // 本次参与的证据
  selectedSources: [...DEFAULT_SOURCE_KEYS],         // 选中的数据源(遥感+data-colle)
  sourceMeta: {},                  // 真实编排单里的数据源 required/seasons/reason
  run: null,                         // 当前运行完整记录(含 orchestrator plan)
  evidencePlan: null,                 // 数据后生成的证据二级编排单
  model3d: { taskId: null, targets: null, slices: [], stats: null, slicePngs: [] }, // 真实产物
  drill: { taskId: null, holes: null, feedback: null }, // 真实钻孔
  datacolleEvidence: null,            // 本 ROI 的 data-colle 成矿模型+文献佐证(证据链叙事卡片)
  reset() { set({ active: 'plan', stages: freshStages(), evidences: freshEvidences(), focusEvidence: null, selectedEvidence: [...ALL_EVIDENCE_KEYS], selectedSources: [...DEFAULT_SOURCE_KEYS], sourceMeta: {}, run: null, evidencePlan: null, datacolleEvidence: null, model3d: { taskId: null, targets: null, slices: [], stats: null, slicePngs: [] }, drill: { taskId: null, holes: null, feedback: null } }) },
  async loadDatacolleEvidence(traceId) {
    if (!traceId) return
    try { set({ datacolleEvidence: await api.getDatacolleEvidence(traceId) }) }
    catch { set({ datacolleEvidence: null }) }
  },
  setSelection(patch) { set(patch) },
  setEvidencePlan(plan) {
    set((s) => {
      const planKeys = evidenceKeysFromPlan(plan)
      return {
        evidencePlan: plan,
        run: s.run ? { ...s.run, evidence_plan: plan } : s.run,
        selectedEvidence: planKeys.length ? planKeys : s.selectedEvidence,
        evidences: mergeEvidencePlanStatuses(s.evidences, plan),
      }
    })
  },
  setEvidencePlanTask(key, patch) {
    set((s) => {
      if (!s.evidencePlan) return {}
      const nextPlan = planWithTaskPatch(s.evidencePlan, key, patch)
      return {
        evidencePlan: nextPlan,
        run: s.run ? { ...s.run, evidence_plan: nextPlan } : s.run,
        evidences: mergeEvidencePlanStatuses(s.evidences, nextPlan),
      }
    })
  },
  async persistEvidenceTask(traceId, key, patch) {
    let nextPlan = null
    set((s) => {
      if (!s.evidencePlan) return {}
      nextPlan = planWithTaskPatch(s.evidencePlan, key, patch)
      return {
        evidencePlan: nextPlan,
        run: s.run ? { ...s.run, evidence_plan: nextPlan } : s.run,
        evidences: mergeEvidencePlanStatuses(s.evidences, nextPlan),
      }
    })
    if (!traceId || !nextPlan) return nextPlan
    try {
      const saved = await api.patchEvidencePlan(traceId, nextPlan)
      get().setEvidencePlan(saved)
      return saved
    } catch {
      return nextPlan
    }
  },
  async generateEvidencePlan(traceId) {
    if (!traceId) return null
    const plan = await api.makeEvidencePlan(traceId)
    get().setEvidencePlan(plan)
    hydrateCompletedEvidenceLayers(get, plan)
    set((s) => ({ run: s.run ? { ...s.run, evidence_plan: plan } : s.run }))
    return plan
  },
  async saveEvidencePlan(traceId, plan) {
    if (!traceId || !plan) return null
    const saved = await api.patchEvidencePlan(traceId, plan)
    get().setEvidencePlan(saved)
    hydrateCompletedEvidenceLayers(get, saved)
    set((s) => ({ run: s.run ? { ...s.run, evidence_plan: saved } : s.run }))
    return saved
  },
  async executeEvidencePlan(traceId) {
    if (!traceId) return
    const res = await api.executeEvidencePlan(traceId)
    if (res?.evidence_plan) get().setEvidencePlan(res.evidence_plan)
    const keys = (res?.tasks || []).map((t) => t.key).filter((k) => ALL_EVIDENCE_KEYS.includes(k))
    keys.forEach((key) => get().runOneEvidenceReal(traceId, key))
  },
  setM3d(patch) { set((s) => ({ model3d: { ...s.model3d, ...patch } })) },
  async loadModel3dResults(taskId) {
    if (!taskId) return
    const [t, sl, full, meta] = await Promise.allSettled([
      api.model3dTargets(taskId), api.model3dSlices(taskId), api.model3dStatusFull(taskId), api.model3dMeta(taskId),
    ])
    const targets = t.status === 'fulfilled' ? (t.value?.targets || (Array.isArray(t.value) ? t.value : null)) : null
    const slices = sl.status === 'fulfilled' ? (sl.value?.slices || []) : []
    const stats = full.status === 'fulfilled' ? (full.value?.task?.results?.model_stats || null) : null
    // 真实彩色切片 PNG → 贴到 3D 分层
    let slicePngs = []
    if (meta.status === 'fulfilled') {
      const pngs = meta.value?.products?.slice_pngs || []
      slicePngs = pngs.map((rel) => {
        const m = String(rel).match(/(-?\d+)\s*m/)
        return { depth: m ? Math.abs(+m[1]) : 0, url: `/svc/model3d/api/result/${taskId}/${rel}` }
      }).sort((a, b) => a.depth - b.depth)
    }
    set((s) => {
      const nextEvidences = stats ? mergeEvidenceFromModelStats(s.evidences, stats) : s.evidences
      const modelKeys = stats ? evidenceKeysFromModelStats(stats) : []
      return {
        model3d: { taskId, targets, slices, stats, slicePngs },
        evidences: nextEvidences,
        selectedEvidence: Array.from(new Set([...(s.selectedEvidence || []), ...modelKeys])),
        stages: {
          ...s.stages,
          evidence: modelKeys.length
            ? { ...s.stages.evidence, status: 'completed', progress: 100 }
            : s.stages.evidence,
          model3d: { ...s.stages.model3d, status: 'completed', progress: 100, taskId },
        },
      }
    })
  },
  async loadExistingModel3d(projectId) {
    if (!projectId) return
    const res = await api.existingModel3d(projectId)
    if (!res?.ok || !res.targets?.length) return
    set((s) => {
      const stats = res.stats || s.model3d.stats
      const modelKeys = stats ? evidenceKeysFromModelStats(stats) : []
      const nextEvidences = stats ? mergeEvidenceFromModelStats(s.evidences, stats) : s.evidences
      const taskId = res.run_id || s.model3d.taskId
      return {
        model3d: {
          ...s.model3d,
          taskId,
          targets: res.targets,
          stats,
          existing: true,
          existingAoi: res.aoi_name,
        },
        evidences: nextEvidences,
        selectedEvidence: Array.from(new Set([...(s.selectedEvidence || []), ...modelKeys])),
        stages: {
          ...s.stages,
          evidence: modelKeys.length
            ? { ...s.stages.evidence, status: 'completed', progress: 100 }
            : s.stages.evidence,
          model3d: { ...s.stages.model3d, status: 'completed', progress: 100, taskId },
        },
      }
    })
    const taskId = res.run_id || get().model3d.taskId
    if (taskId) get().loadModel3dResults(taskId).catch(() => {})
  },
  async loadDrillResults(taskId) {
    if (!taskId) return
    const [hg, fb] = await Promise.allSettled([api.drillHoles(taskId), api.drillFeedback(taskId)])
    const parse = (r) => (r.status === 'fulfilled' ? (r.value?.features || []).map((f) => ({
      ...f.properties,
      lon: f.geometry?.coordinates?.[0], lat: f.geometry?.coordinates?.[1],
    })) : null)
    set({ drill: { taskId, holes: parse(hg), feedback: parse(fb) } })
  },
  // 刷新后恢复最新布孔产物(否则只有 model3d 靶点恢复、钻孔丢失或陈旧 → 3D 里孔靶不一致)
  async loadExistingDrill(projectId) {
    if (!projectId) return
    const res = await api.existingDrill(projectId).catch(() => null)
    if (!res?.ok || !res.holes?.length) return
    const parse = (feats) => (feats || []).map((f) => ({
      ...f.properties,
      lon: f.geometry?.coordinates?.[0], lat: f.geometry?.coordinates?.[1],
    }))
    set((s) => ({
      drill: { taskId: res.run_id || s.drill.taskId, holes: parse(res.holes), feedback: parse(res.feedback) },
      stages: { ...s.stages, drill: { ...s.stages.drill, status: 'completed', progress: 100 } },
    }))
  },
  setActive(id) { set({ active: id }) },
  setFocusEvidence(k) { set((s) => ({ focusEvidence: s.focusEvidence === k ? null : k })) },
  setStage(id, patch) {
    set((s) => ({ stages: { ...s.stages, [id]: { ...s.stages[id], ...patch } } }))
  },
  setEvidence(key, patch) {
    set((s) => {
      const next = { evidences: { ...s.evidences, [key]: { ...s.evidences[key], ...patch } } }
      if (s.evidencePlan && (patch.status || patch.progress != null || patch.taskId)) {
        next.evidencePlan = {
          ...s.evidencePlan,
          evidence_tasks: (s.evidencePlan.evidence_tasks || []).map((t) => (
            t.key === key
              ? { ...t, status: patch.status || t.status, progress: patch.progress ?? t.progress, task_id: patch.taskId || t.task_id }
              : t
          )),
        }
      }
      return next
    })
  },
  // 从 BFF run 还原(刷新/历史回看)
  hydrate(run) {
    const stages = freshStages()
    const incoming = Object.keys(run?.stages || {}).length ? run.stages : stagesFromPlan(run?.plan || {})
    stages.plan = { ...stages.plan, status: 'completed', progress: 100 }
    Object.entries(incoming || {}).forEach(([k, v]) => {
      if (stages[k]) stages[k] = { ...stages[k], ...v }
    })
    const planStages = stagesFromPlan(run?.plan || {})
    Object.entries(planStages || {}).forEach(([k, v]) => {
      if (!stages[k]) return
      const services = Array.from(new Set([...(stages[k].services || []), ...(v.services || [])]))
      stages[k] = {
        ...stages[k],
        services,
        sub_tasks: { ...(v.sub_tasks || {}), ...(stages[k].sub_tasks || {}) },
      }
    })
    const evidences = freshEvidences()
    hydrateEvidenceFromStages(stages, evidences)
    const evidencePlan = run?.evidence_plan || null
    const nextEvidences = evidencePlan ? mergeEvidencePlanStatuses(evidences, evidencePlan) : evidences
    const planKeys = evidencePlan ? evidenceKeysFromPlan(evidencePlan) : []
    const plannedSources = dataSourcesFromPlan(run?.plan || {})
    set({
      run: run || null,
      stages,
      evidences: nextEvidences,
      evidencePlan,
      selectedEvidence: planKeys.length ? planKeys : get().selectedEvidence,
      selectedSources: plannedSources.keys,
      sourceMeta: plannedSources.meta,
      active: nextActiveStage(stages),
    })
    // 重载后:对"运行中且有 taskId"的证据恢复轮询(尤其 InSAR 数小时异步,用户离开再回来)
    const traceId = run?.trace_id
    // 一次性拉取本 ROI 的 data-colle 成矿/文献证据(非阻塞,供证据链叙事卡片)
    get().loadDatacolleEvidence(traceId)
    if (traceId) {
      EVIDENCES.forEach((e) => {
        const cur = nextEvidences[e.key]
        if (cur?.status === 'running' && cur.taskId) {
          get().pollEvidenceTask(traceId, e.key, e.svc, cur.taskId)
        }
      })
      if (evidencePlan) hydrateCompletedEvidenceLayers(get, evidencePlan)
    }
    const modelTaskId = stages.model3d?.taskId
      || stages.model3d?.task_id
      || stages.model3d?.sub_tasks?.model3d?.task_id
    if (stages.model3d?.status === 'completed' && modelTaskId) {
      get().loadModel3dResults(modelTaskId).catch(() => {})
    }
  },
  // MVP:模拟某阶段执行(带真实 svc 调用钩子,失败回退 mock 进度)
  runStage(id, { onDone } = {}) {
    const { setStage } = get()
    setStage(id, { status: 'running', progress: 5 })
    const timer = setInterval(() => {
      const cur = get().stages[id]
      const next = Math.min(100, (cur.progress || 0) + 12)
      if (next >= 100) {
        clearInterval(timer)
        setStage(id, { status: 'completed', progress: 100 })
        onDone && onDone()
      } else {
        setStage(id, { progress: next })
      }
    }, 350)
  },
  // 证据并行执行(化探演示为失败,可降级)
  runEvidences() {
    const { setEvidence, setStage } = get()
    setStage('evidence', { status: 'running', progress: 5 })
    const sel = get().selectedEvidence || ALL_EVIDENCE_KEYS
    EVIDENCES.filter((e) => sel.includes(e.key)).forEach((ev, i) => {
      const fail = ev.key === 'geochem'
      setEvidence(ev.key, { status: 'running', progress: 0 })
      let p = 0
      const t = setInterval(() => {
        p += 10 + Math.random() * 12
        if (p >= 100) {
          clearInterval(t)
          setEvidence(ev.key, { status: fail ? 'failed' : 'completed', progress: 100 })
          checkEvidenceSettle(get)   // 仅按所选证据判定阶段完成
        } else {
          setEvidence(ev.key, { progress: Math.round(p) })
        }
      }, 300 + i * 120)
    })
  },
  // 取某证据的 metadata,找第一个 .tif 产物 → 解码上色 → 作地图叠加层
  async loadEvidenceLayer(key, svc, taskId) {
    if (!taskId) return
    get().setEvidence(key, { layerLoading: true, layerLoadFailed: false })
    try {
      let tex
      if (svc === 'analyser' || svc === 'stru' || svc === 'insar' || svc === 'geophys') {
        // 适配器服务:产物路径异构,经 BFF 统一取代表性栅格(无栅格则 404 → 抛错)
        const projectId = useProject.getState().current?.id
        const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
        const r = await loadEvidenceRaster(`/api/adapter-raster/${taskId}${qs}`)
        tex = r.dataURL
        // 裁到 AOI 退化 → BFF 回退区域级原图,记录真实范围 + 标注供 Canvas 按真实范围摆放
        if (r.scope === 'regional' && r.bounds) {
          get().setEvidence(key, { regional: true, regionalBounds: r.bounds, regionalNote: r.note || '区域级' })
        } else {
          get().setEvidence(key, { regional: false, regionalBounds: undefined, regionalNote: '' })
        }
      } else {
        let meta = null
        for (const name of ['metadata.json', 'manifest.json']) {
          try { meta = await api.svc(svc, `api/result/${taskId}/${name}`); if (meta) break } catch {}
        }
        const tifs = Object.entries(meta?.products || {}).filter(([, v]) => typeof v === 'string' && v.endsWith('.tif'))
        if (!tifs.length) { get().setEvidence(key, { noLayer: true, layerLoading: false, layerLoadFailed: true }); return }
        const PREFER = ['composite', 'score', 'prospect', 'anomaly', 'distance', 'density', 'velocity', 'rtp', 'analytic', 'tilt', 'band']
        const AVOID = ['hillshade', 'slope', 'aspect', 'dem', 'mask', 'rgb']
        const rank = ([k, v]) => {
          const s = (k + ' ' + v).toLowerCase()
          if (AVOID.some((a) => s.includes(a))) return -1
          return PREFER.reduce((acc, p, i) => (s.includes(p) ? Math.max(acc, PREFER.length - i) : acc), 0)
        }
        const rel = tifs.slice().sort((a, b) => rank(b) - rank(a))[0][1]
        tex = await loadSliceTexture(`/svc/${svc}/api/result/${taskId}/${rel}`)
      }
      if (!tex) { get().setEvidence(key, { noLayer: true, layerLoading: false, layerLoadFailed: true }); return }
      get().setEvidence(key, { layerUrl: tex, visible: true, opacity: 0.75, noLayer: false, layerLoading: false, layerLoadFailed: false })
    } catch {
      get().setEvidence(key, { noLayer: true, layerLoading: false, layerLoadFailed: true })   // 完成但无可叠栅格
    }
  },
  setEvidenceVis(key, patch) { get().setEvidence(key, patch) },
  retryEvidence(key) {
    const { setEvidence } = get()
    setEvidence(key, { status: 'running', progress: 0 })
    let p = 0
    const t = setInterval(() => {
      p += 18
      if (p >= 100) { clearInterval(t); setEvidence(key, { status: 'completed', progress: 100 }) }
      else setEvidence(key, { progress: p })
    }, 280)
  },
  skipEvidence(key, traceId = null) {
    get().setEvidence(key, { status: 'skipped', progress: 100, skipReason: '用户跳过' })
    get().persistEvidenceTask(traceId, key, {
      status: 'skipped',
      progress: 100,
      skip_reason: '用户跳过',
      error: '',
      degraded: false,
    })
    checkEvidenceSettle(get)
  },
  degradeEvidence(key, traceId = null) {
    const cur = get().evidences[key] || {}
    get().setEvidence(key, {
      status: 'completed',
      progress: 100,
      degraded: true,
      noLayer: true,
      visible: false,
      error: cur.error || '真实服务失败,已降级为低置信缺失证据',
    })
    get().persistEvidenceTask(traceId, key, {
      status: 'degraded',
      progress: 100,
      degraded: true,
      fallback_action: 'degrade',
      error: cur.error || '真实服务失败,已降级为低置信缺失证据',
    })
    checkEvidenceSettle(get)
  },

  // ── 真实服务调用:start + 轮询 status;真实完成驱动,合成进度填充;不可达回退模拟 ──
  async runStageReal(traceId, id, service, { onDone, params } = {}) {
    const { setStage, runStage } = get()
    if (!traceId) return runStage(id, { onDone })
    setStage(id, { status: 'running', progress: 3 })
    let taskId
    try {
      taskId = (await api.startSvc(traceId, service, params || {})).task_id
    } catch (e) {
      // 服务起不来 → 如实报失败(不再回退模拟"完成",避免假象);HUD 给重试入口
      const msg = e?.response?.data?.detail || e?.message || '服务不可达'
      setStage(id, { status: 'failed', error: `启动失败: ${msg}` })
      api.patchStage(traceId, id, { status: 'failed', progress: 0, error: msg }).catch(() => {})
      return
    }
    if (!taskId) {
      setStage(id, { status: 'failed', error: '服务未返回任务 ID' })
      api.patchStage(traceId, id, { status: 'failed', progress: 0, error: '服务未返回任务 ID' }).catch(() => {})
      return
    }
    setStage(id, { taskId })
    if (id === 'data') {
      const subs = { ...(get().stages.data?.sub_tasks || {}) }
      subs[service] = {
        ...(subs[service] || {}),
        status: 'running',
        progress: 3,
        required: true,
        task_id: taskId,
        reason: service === 'insar' ? 'InSAR 数据准备中' : (subs[service]?.reason || ''),
      }
      setStage('data', { sub_tasks: subs })
      api.patchStage(traceId, 'data', { sub_tasks: subs }).catch(() => {})
    }
    if (service === 'model3d') get().setM3d({ taskId })
    get().pollStage(traceId, id, service, taskId, { onDone })
  },

  // 轮询一个【已启动】的任务直到终态(不再 startSvc),供 runStageReal 与"重开续接"复用。
  pollStage(traceId, id, service, taskId, { onDone } = {}) {
    const { setStage } = get()
    const key = `${traceId}:${id}:${taskId}`
    if (_activePolls.has(key)) return   // 已在轮询同一任务 → 幂等,避免重开续接时重复
    _activePolls.add(key)
    const stop = () => _activePolls.delete(key)
    setStage(id, { status: 'running', taskId })
    // 持久化"运行中+taskId":离开项目空间再回来时 hydrate 不会把它重置成 pending,可续接轮询。
    // 仅对 report 开启(它有重开续接;data 走自己的 sub_tasks;model3d/drill 无续接,持久化会卡住反成回归)。
    if (traceId && id === 'report') api.patchStage(traceId, id, { status: 'running', progress: get().stages[id]?.progress || 3, taskId }).catch(() => {})
    const useSynth = service !== 'insar' && service !== 'downloader'  // 下载用真实分传感器进度,不再假性爬条
    const interval = service === 'insar' ? 8000 : 1600
    let synth = Math.max(3, get().stages[id]?.progress || 3)
    const poll = async () => {
      if (get().stages[id]?.status !== 'running' || get().stages[id]?.taskId !== taskId) { stop(); return }
      if (useSynth) synth = Math.min(92, synth + 7)
      let done = false, failed = false, realp = 0, errMsg = '', download = null, warning = '', note = '', noLayer = false, sensors = null, active = ''
      try {
        const s = await api.svcStatus(traceId, service, taskId)
        realp = s.progress || 0
        download = s.download || null
        warning = s.warning || ''
        note = s.note || ''
        noLayer = !!s.no_layer
        sensors = s.sensors || null
        active = s.active_sensor || ''
        if (s.status === 'completed') done = true
        else if (s.status === 'failed') { failed = true; errMsg = s.error || s.raw || '' }
      } catch { /* 瞬时错误,继续轮询 */ }
      if (done) {
        if (id === 'data') {
          const subs = { ...(get().stages.data?.sub_tasks || {}) }
          subs[service] = { ...(subs[service] || {}), status: 'completed', progress: 100, required: true, task_id: taskId, warning, note: '', no_layer: noLayer, sensors, active_sensor: '' }
          setStage('data', { sub_tasks: subs })
          api.patchStage(traceId, 'data', { sub_tasks: subs }).catch(() => {})
        }
        setStage(id, { status: 'completed', progress: 100, taskId, download })
        if (traceId) {
          api.patchStage(traceId, id, { status: 'completed', progress: 100, taskId, download }).catch(() => {})
        }
        if (service === 'model3d') { try { await get().loadModel3dResults(taskId) } catch {} }
        if (service === 'drill') { try { await get().loadDrillResults(taskId) } catch {} }
        stop(); onDone && onDone(); return
      }
      if (failed) {
        if (id === 'data') {
          const subs = { ...(get().stages.data?.sub_tasks || {}) }
          subs[service] = { ...(subs[service] || {}), status: 'failed', progress: realp || 0, required: true, task_id: taskId, error: errMsg }
          setStage('data', { sub_tasks: subs })
          api.patchStage(traceId, 'data', { sub_tasks: subs }).catch(() => {})
        }
        setStage(id, { status: 'failed', error: errMsg })
        stop(); return
      }
      const progress = useSynth ? Math.max(synth, realp) : Math.max(10, realp)
      if (id === 'data') {
        const subs = { ...(get().stages.data?.sub_tasks || {}) }
        subs[service] = { ...(subs[service] || {}), status: 'running', progress, required: true, task_id: taskId, note, warning, sensors, active_sensor: active }
        setStage('data', { sub_tasks: subs })
      }
      setStage(id, { progress })
      setTimeout(poll, interval)
    }
    poll()
  },

  // 单个证据:真实 start+poll+取图;不可达回退模拟。供批量与单独"重跑"复用。
  async runOneEvidenceReal(traceId, key) {
    const ev = EVIDENCES.find((e) => e.key === key)
    if (!ev || !traceId) return
    const { setEvidence } = get()
    setEvidence(key, { status: 'running', progress: 0, layerUrl: undefined, noLayer: false })
    get().persistEvidenceTask(traceId, key, {
      status: 'running',
      progress: 0,
      task_id: '',
      error: '',
      degraded: false,
      fallback_action: '',
      skip_reason: '',
    })
    let taskId
    try {
      taskId = (await api.startSvc(traceId, ev.svc, ev.svc === 'insar' ? { phase: 'evidence' } : {})).task_id
    } catch (e) {
      // 服务起不来 → 如实报失败(不再回退模拟"完成");失败框已有 重试/降级/跳过
      const msg = e?.response?.data?.detail || e?.message || '服务不可达'
      get().setEvidence(key, { status: 'failed', progress: 100, error: `启动失败: ${msg}` })
      get().persistEvidenceTask(traceId, key, { status: 'failed', progress: 100, error: msg })
      checkEvidenceSettle(get)
      return
    }
    if (!taskId) {
      get().setEvidence(key, { status: 'failed', progress: 100, error: '服务未返回任务 ID' })
      get().persistEvidenceTask(traceId, key, { status: 'failed', progress: 100, error: '服务未返回任务 ID' })
      checkEvidenceSettle(get)
      return
    }
    setEvidence(key, { taskId })
    get().persistEvidenceTask(traceId, key, {
      status: 'running',
      progress: 5,
      task_id: taskId,
      error: '',
    })
    get().pollEvidenceTask(traceId, key, ev.svc, taskId)
  },

  // 轮询单个证据任务到终态(供主动运行 + 页面重载恢复共用)。
  // 采集 svcstatus 的 note(如 InSAR"云处理中,可离开稍后回来");insar 数小时:用真实进度、放慢轮询。
  pollEvidenceTask(traceId, key, svc, taskId) {
    if (!traceId || !taskId) return
    const fk = `${key}:${taskId}`
    if (_pollingTasks.has(fk)) return     // 已在轮询,避免重复
    _pollingTasks.add(fk)
    const { setEvidence } = get()
    const useSynth = svc !== 'insar'      // insar 异步数小时 → 用真实进度,不做合成膨胀
    const interval = svc === 'insar' ? 8000 : 1700
    let synth = 0
    const stop = () => _pollingTasks.delete(fk)
    const poll = async () => {
      if (get().evidences[key]?.status !== 'running') { stop(); return }  // 被跳过/降级/外部改 → 停
      if (useSynth) synth = Math.min(92, synth + 9)
      let done = false, failed = false, realp = 0, errMsg = '', note = ''
      try {
        const s = await api.svcStatus(traceId, svc, taskId)
        realp = s.progress || 0; note = s.note || ''
        if (s.status === 'completed') done = true
        else if (s.status === 'failed') { failed = true; errMsg = s.error || s.raw || '' }
      } catch { /* 瞬时错误,继续 */ }
      if (done) {
        setEvidence(key, { status: 'completed', progress: 100, note: '' })
        get().persistEvidenceTask(traceId, key, { status: 'completed', progress: 100, task_id: taskId, error: '', degraded: false })
        get().loadEvidenceLayer(key, svc, taskId)
        checkEvidenceSettle(get); stop(); return
      }
      if (failed) {
        setEvidence(key, { status: 'failed', progress: 100, error: errMsg, note: '' })
        get().persistEvidenceTask(traceId, key, { status: 'failed', progress: 100, task_id: taskId, error: errMsg || '服务报告失败', degraded: false })
        checkEvidenceSettle(get); stop(); return
      }
      const prog = useSynth ? Math.max(synth, realp) : Math.max(10, realp)
      setEvidence(key, { progress: prog, note })
      get().persistEvidenceTask(traceId, key, { status: 'running', progress: prog, task_id: taskId })
      setTimeout(poll, interval)
    }
    poll()
  },

  // 5 证据并行
  async runEvidencesReal(traceId) {
    const { setStage, runEvidences } = get()
    if (!traceId) return runEvidences()
    setStage('evidence', { status: 'running', progress: 5 })
    const planTasks = get().evidencePlan?.evidence_tasks || []
    const planned = planTasks
      .filter((t) => t.enabled !== false && ['pending', 'failed'].includes(t.status || 'pending'))
      .map((t) => t.key)
      .filter((k) => ALL_EVIDENCE_KEYS.includes(k))
    const sel = planned.length ? planned : (get().selectedEvidence || ALL_EVIDENCE_KEYS)
    EVIDENCES.filter((e) => sel.includes(e.key)).forEach((ev) => get().runOneEvidenceReal(traceId, ev.key))
  },
}))
