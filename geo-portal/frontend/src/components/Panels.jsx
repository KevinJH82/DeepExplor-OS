import { useEffect, useState } from 'react'
import { message, Upload, Modal, Radio } from 'antd'
import { useWorkflow, useProject, dataSourcesFromPlan } from '../store'
import { EVIDENCES, DATA_SOURCES, SOURCE_FEEDS, SOURCE_SERVICES, evidenceDoneCount, EVIDENCE_GATE, isUnlocked, evidencePlanGateDetails } from '../lib/stages'
import { buildEvidenceRows, stageTrace, statusText, statusTone } from '../lib/evidenceChain'
import { buildProjectSummary } from '../lib/geologyNarrative'
import * as api from '../api/portal'

// 可勾选 chip
function Chip({ on, onClick, children }) {
  return (
    <b onClick={onClick} style={{
      cursor: 'pointer', userSelect: 'none',
      background: on ? 'rgba(10,162,192,.16)' : 'rgba(120,140,170,.08)',
      borderColor: on ? 'rgba(10,162,192,.5)' : 'var(--line)',
      color: on ? '#0a8aa3' : 'var(--mut)',
    }}>{on ? '☑' : '☐'} {children}</b>
  )
}

function StatusBadge({ status, children }) {
  return <span className={`badge ${statusTone(status)}`}>{children || statusText(status)}</span>
}

function HudPanel({ label, children }) {
  const [pinned, setPinned] = useState(false)
  return (
    <div className={`hud glass ${pinned ? 'pinned' : ''}`} tabIndex={0}>
      <span className="hud-handle">{label}</span>
      <button
        type="button"
        className="hud-pin"
        title={pinned ? '取消固定面板' : '固定面板'}
        aria-label={pinned ? '取消固定面板' : '固定面板'}
        aria-pressed={pinned}
        onClick={() => setPinned((v) => !v)}
      >
        {pinned ? '●' : '○'}
      </button>
      {children}
    </div>
  )
}

function MiniList({ title, items }) {
  const vals = (items || []).filter(Boolean)
  return (
    <div className="trace-sec">
      <h6>{title}</h6>
      {vals.length
        ? vals.slice(0, 8).map((x, i) => <div className="trace-line" key={`${title}-${i}`}>{x}</div>)
        : <div className="trace-line muted">-</div>}
    </div>
  )
}

function StageTrace({ active }) {
  const stages = useWorkflow((s) => s.stages)
  const selectedSources = useWorkflow((s) => s.selectedSources)
  const sourceMeta = useWorkflow((s) => s.sourceMeta)
  const run = useWorkflow((s) => s.run)
  const selectedEvidence = useWorkflow((s) => s.selectedEvidence)
  const model3d = useWorkflow((s) => s.model3d)
  const drill = useWorkflow((s) => s.drill)
  const traceId = useProject((s) => s.traceId)
  const current = useProject((s) => s.current)
  const planSources = run?.plan ? dataSourcesFromPlan(run.plan) : { keys: [], meta: {} }
  const effectiveSources = mergeSourceKeys(selectedSources, planSources.keys)
  const trace = stageTrace(active, {
    stages,
    selectedSources: effectiveSources,
    sourceMeta: { ...planSources.meta, ...sourceMeta },
    selectedEvidence,
    model3d,
    drill,
    traceId,
    current,
  })
  return (
    <div className="tracebox">
      <MiniList title="输入" items={trace.input} />
      <MiniList title="处理" items={trace.process} />
      <MiniList title="输出" items={trace.output} />
      <MiniList title="审计" items={trace.audit} />
    </div>
  )
}

function mergeSourceKeys(...lists) {
  return Array.from(new Set(lists.flat().filter(Boolean)))
}

function EvidenceMatrix({ rows }) {
  return (
    <div className="ev-matrix">
      <div className="ev-head">
        <span>证据</span><span>状态</span><span>级别</span><span>权重</span><span>贡献</span>
      </div>
      {rows.map((r) => (
        <div key={r.key}>
          <div className="ev-row" title={`${r.relation}${r.taskId ? ` · task_id=${r.taskId}` : ''}`}>
            <span>{r.label}</span>
            <span><StatusBadge status={r.status} /></span>
            <span>{r.requiredLevel || r.quality}</span>
            <span>{r.weight != null ? Number(r.weight).toFixed(2) : r.coverage}</span>
            <span className={r.contribution === '强支撑' ? 'support strong' : r.contribution === '缺失' ? 'support weak' : 'support'}>{r.contribution}</span>
          </div>
          {r.error && <div className="ev-reason">原因: {r.error}</div>}
        </div>
      ))}
    </div>
  )
}

function useProjectSummary(rows, target = null) {
  const selectedSources = useWorkflow((s) => s.selectedSources)
  const model3d = useWorkflow((s) => s.model3d)
  const run = useWorkflow((s) => s.run)
  const current = useProject((s) => s.current)
  return buildProjectSummary({ current, evidenceRows: rows, selectedSources, model3d, target, run })
}

function SummaryNote({ text }) {
  if (!text) return null
  return <div className="summary-note">{text}</div>
}

export default function Panels() {
  const active = useWorkflow((s) => s.active)
  switch (active) {
    case 'plan': return <PlanPanel />
    case 'data': return <DataPanel />
    case 'evidence': return <><SubTab /><EvidenceHud /></>
    case 'model3d': return <Model3DHud />
    case 'drill': return <DrillHud />
    case 'report': return <ReportHud />
    default: return null
  }
}

// ① 编排单 —— 中央工作面板
function PlanPanel() {
  const { current, traceId, setTrace, patchCurrent } = useProject()
  const { hydrate, selectedSources, selectedEvidence, setSelection } = useWorkflow()
  const [kml, setKml] = useState(current?.kml_name || null)
  const [delivery, setDelivery] = useState(
    current?.delivery_id ? { id: current.delivery_id, name: current.delivery_name } : null)
  const [picker, setPicker] = useState(null)       // {list:[...]} 打开消歧弹窗
  const [pickChoice, setPickChoice] = useState(null)
  const validEvidenceKeys = EVIDENCES.map((e) => e.key)
  const activeEvidence = selectedEvidence.filter((k) => validEvidenceKeys.includes(k))

  useEffect(() => {
    setKml(current?.kml_name || null)
    setDelivery(current?.delivery_id ? { id: current.delivery_id, name: current.delivery_name } : null)
  }, [current?.id, current?.kml_name, current?.delivery_id])

  // 打开交付选择器:有候选用候选,否则拉全部交付供手动选
  const openPicker = async (cands) => {
    let list = cands && cands.length ? cands : null
    if (!list) {
      try { list = await api.listDeliveries() } catch { list = [] }
    }
    setPickChoice(delivery?.id || (list[0] && list[0].delivery_id) || null)
    setPicker({ list: list || [] })
  }
  const confirmPick = async () => {
    if (!pickChoice) return
    try {
      const r = await api.bindDelivery(current.id, pickChoice)
      setDelivery({ id: r.delivery.id, name: r.delivery.name })
      patchCurrent({ delivery_id: r.delivery.id, delivery_name: r.delivery.name })
      message.success(`已绑定交付:${r.delivery.name}`)
      setPicker(null)
    } catch { message.error('绑定失败') }
  }

  // 切换数据源;若该源喂某证据(物探/化探),联动启用/停用对应证据
  const toggleSource = (k) => {
    const on = selectedSources.includes(k)
    const nextSrc = on ? selectedSources.filter((x) => x !== k) : [...selectedSources, k]
    const patch = { selectedSources: nextSrc }
    const feeds = SOURCE_FEEDS[k]
    if (feeds) {
      const stillFed = nextSrc.some((s) => SOURCE_FEEDS[s] === feeds)  // 同类还有其它源?
      let ev = selectedEvidence.filter((x) => x !== feeds)
      if (stillFed) ev = [...ev, feeds]
      patch.selectedEvidence = ev
    }
    setSelection(patch)
  }
  const toggleEvidence = (k) => setSelection({
    selectedEvidence: selectedEvidence.includes(k)
      ? selectedEvidence.filter((x) => x !== k) : [...selectedEvidence, k],
  })

  const uploadProps = {
    accept: '.kml,.kmz,.ovkml,.csv,.xlsx',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        const res = await api.uploadKml(current.id, file)
        setKml(res.filename)
        const patch = { kml_name: res.filename }
        if (res.bbox) patch.aoi_bbox = res.bbox
        const d = res.delivery || {}
        if (d.id) { patch.delivery_id = d.id; patch.delivery_name = d.name }
        patchCurrent(patch)
        setDelivery(d.id ? { id: d.id, name: d.name } : null)
        message.success(`已上传 ${res.filename}${res.bbox ? ` · bbox ${res.bbox.map((n) => n.toFixed(2)).join(', ')}` : ''}`)
        onSuccess(res)
        // 消歧:未命中(有候选)或多个交付强覆盖 → 弹出让用户确认/选择
        const cands = d.candidates || []
        const strong = cands.filter((c) => (c.coverage || 0) >= 0.6)
        if ((d.method === 'none' && cands.length) || strong.length >= 2) openPicker(cands)
      } catch (e) { message.error('上传失败'); onError(e) }
    },
  }

  const rsSensors = selectedSources.filter((k) => SOURCE_SERVICES[k] === 'downloader')
  const datacolleSrc = selectedSources.filter((k) => SOURCE_SERVICES[k] === 'datacolle')
  const insarSrc = selectedSources.filter((k) => SOURCE_SERVICES[k] === 'insar')
  const dataServices = [
    ...(rsSensors.length ? ['downloader'] : []),
    ...(insarSrc.length ? ['insar'] : []),
    ...(datacolleSrc.length ? ['datacolle'] : []),
    'preprocess',
  ]
  const defaultPlan = {
    mineral: current?.mineral,
    sources: selectedSources,
    stages: [
      { stage: 'data', services: dataServices, sensors: rsSensors, datacolle: datacolleSrc },
      { stage: 'evidence', services: activeEvidence },
      { stage: 'model3d', services: ['model3d'] },
      { stage: 'drill', services: ['drill'] },
      { stage: 'report', services: ['reporter'] },
    ],
  }

  const generate = async () => {
    let realTrace = null, realPlan = null
    try {
      const r = await api.planReal(current.id)   // 调真实 orchestrator
      if (r.ok) { realTrace = r.trace_id; realPlan = r.plan }
    } catch { /* orchestrator 不可达,走本地兜底 */ }
    const finalPlan = realPlan && Object.keys(realPlan).length
      ? { ...realPlan, ui_sources: selectedSources, ui_evidence: activeEvidence }
      : defaultPlan
    try {
      const run = await api.createRun(current.id, finalPlan, realTrace)
      setTrace(run.trace_id)
      patchCurrent({ current_run: run.trace_id })
      hydrate(run)
      message.success(`编排单就绪 · trace_id=${run.trace_id} · ${realTrace ? 'orchestrator 实跑' : '本地兜底'}`)
    } catch (e) {
      message.error('生成失败:' + (e?.response?.data?.detail || e.message))
    }
  }

  return (
    <div className="work glass">
      <h4>新建勘探任务 · {current?.name}</h4>
      <div className="plan-roi-upload">
        <Upload {...uploadProps}>
          <div className={`drop ${kml ? 'ready' : ''}`}>
            {kml ? (
              <>
                <b>✓ 项目 ROI 已绑定</b>
                <span>{kml}</span>
                <em>编排将直接使用该 KML，点击可替换 ROI</em>
              </>
            ) : '⬆ 上传 KML / KMZ / ovKML / CSV / XLSX'}
          </div>
        </Upload>
      </div>
      <div className="kv">
        <span>交付库</span>
        {delivery ? (
          <b>📦 {delivery.name}
            <a style={{ marginLeft: 8, color: '#0a8aa3', cursor: 'pointer' }}
               onClick={() => openPicker([])}>换</a>
          </b>
        ) : kml ? (
          <b style={{ color: '#c47a3a' }}>未匹配交付
            <a style={{ marginLeft: 8, color: '#0a8aa3', cursor: 'pointer' }}
               onClick={() => openPicker([])}>手动选</a>
          </b>
        ) : <b style={{ color: 'var(--mut)' }}>—</b>}
      </div>
      <div className="kv"><span>矿种</span><b>{current?.mineral_label}</b></div>
      <div className="kv"><span>AOI bbox</span><b>{current?.aoi_bbox ? current.aoi_bbox.map((n) => n.toFixed(2)).join(', ') : '样例'}</b></div>
      <Modal open={!!picker} title="选择交付库(数据来源)"
             onCancel={() => setPicker(null)} onOk={confirmPick}
             okText="绑定" cancelText="取消" okButtonProps={{ disabled: !pickChoice }}>
        <div style={{ color: 'var(--mut)', fontSize: 12, marginBottom: 8 }}>
          多个交付与本 ROI 重叠(或未自动命中),请确认用哪一个的数据:
        </div>
        <Radio.Group value={pickChoice} onChange={(e) => setPickChoice(e.target.value)}>
          {(picker?.list || []).map((c) => (
            <Radio key={c.delivery_id} value={c.delivery_id}
                   style={{ display: 'block', padding: '5px 0' }}>
              {c.name}
              {c.coverage != null && (
                <em style={{ color: 'var(--mut)', marginLeft: 6 }}>
                  覆盖 {Math.round(c.coverage * 100)}%{c.iou != null ? ` · 贴合 ${Math.round(c.iou * 100)}%` : ''}
                </em>
              )}
            </Radio>
          ))}
          {!(picker?.list || []).length && <div style={{ color: 'var(--mut)' }}>交付库为空或不可访问</div>}
        </Radio.Group>
      </Modal>
      <StageTrace active="plan" />
      <div style={{ marginTop: 10, color: 'var(--mut)', fontSize: 12 }}>数据源(可改推荐)</div>
      {DATA_SOURCES.map((g) => (
        <div key={g.group} style={{ marginBottom: 4 }}>
          <div style={{ fontSize: 10.5, color: 'var(--mut)', margin: '4px 0 2px' }}>{g.group} · {g.note}</div>
          <div className="chips">
            {g.items.map((s) => (
              <Chip key={s.key} on={selectedSources.includes(s.key)} onClick={() => toggleSource(s.key)}>{s.label}</Chip>
            ))}
          </div>
        </div>
      ))}
      <div style={{ marginTop: 6, color: 'var(--mut)', fontSize: 12 }}>参与证据分析</div>
      <div className="chips">
        {EVIDENCES.map((e) => (
          <Chip key={e.key} on={selectedEvidence.includes(e.key)} onClick={() => toggleEvidence(e.key)}>{e.label}</Chip>
        ))}
      </div>
      <button className="pbtn" disabled={!activeEvidence.length} onClick={generate}>
        {activeEvidence.length ? '▶ 生成编排单并进入数据准备' : '至少选 1 项证据'}
      </button>
    </div>
  )
}

// ② 数据准备
function DataPanel() {
  const { stages, setActive, setStage, runStageReal, selectedSources, run: runRecord, evidencePlan, generateEvidencePlan } = useWorkflow()
  const { traceId } = useProject()
  const st = stages.data || {}
  const dlSub = st.sub_tasks?.downloader || {}
  const sensors = Array.isArray(dlSub.sensors) ? dlSub.sensors : []
  const activeSensor = dlSub.active_sensor || ''
  const planSources = runRecord?.plan ? dataSourcesFromPlan(runRecord.plan) : { keys: [] }
  const effectiveSources = mergeSourceKeys(selectedSources || [], planSources.keys || [])
  const rs = effectiveSources.filter((k) => SOURCE_SERVICES[k] === 'downloader')
  const insar = effectiveSources.filter((k) => SOURCE_SERVICES[k] === 'insar')
  const dc = effectiveSources.filter((k) => SOURCE_SERVICES[k] === 'datacolle')
  const dataIncomplete = Object.values(st.sub_tasks || {}).some((t) => (
    t?.required && !['completed', 'skipped'].includes(t.status)
  ))
  const finishData = async () => {
    const liveData = useWorkflow.getState().stages.data || st
    const subTasks = { ...(liveData.sub_tasks || {}) }
    if (rs.length || subTasks.downloader) {
      subTasks.downloader = { ...(subTasks.downloader || {}), status: rs.length ? 'completed' : 'skipped', required: !!rs.length }
    }
    if (dc.length || subTasks.datacolle) {
      subTasks.datacolle = { ...(subTasks.datacolle || {}), status: dc.length ? 'completed' : 'skipped', required: !!dc.length }
    }
    if (insar.length || subTasks.insar) {
      const cur = subTasks.insar || {}
      subTasks.insar = {
        ...cur,
        status: insar.length ? (['completed', 'skipped'].includes(cur.status) ? cur.status : 'pending') : 'skipped',
        required: !!insar.length,
        reason: insar.length ? 'InSAR 数据准备已纳入数据阶段门控' : 'InSAR 未选择',
      }
    }
    const requiredDone = Object.values(subTasks).every((t) => !t?.required || ['completed', 'skipped'].includes(t.status))
    const patch = requiredDone
      ? { status: 'completed', progress: 100, sub_tasks: subTasks, error: '' }
      : { status: 'running', progress: Math.min(99, liveData.progress || 0), sub_tasks: subTasks }
    setStage('data', patch)
    syncStage(traceId, 'data', { sub_tasks: subTasks })
    if (requiredDone) {
      try { await generateEvidencePlan(traceId) } catch {}
    }
    return requiredDone
  }
  // 数据链:downloader → datacolle → insar → finishData。提到组件作用域,run() 与 resumeData() 共用。
  const runInsar = () => {
    if (!insar.length) return finishData()
    return runStageReal(traceId, 'data', 'insar', {
      onDone: finishData,
      params: { sources: insar.join(','), phase: 'data' },
    })
  }
  const runDatacolle = () => {
    if (!dc.length) return runInsar()
    return runStageReal(traceId, 'data', 'datacolle', {
      onDone: runInsar,
      params: { sources: dc.join(',') },
    })
  }
  const run = async () => {
    if (!rs.length) return runDatacolle()
    return runStageReal(traceId, 'data', 'downloader', {
      onDone: runDatacolle,
      params: { sensors: rs.join(',') },
    })
  }
  // 重开/刷新后续接:数据阶段仍 running 时,把仍在跑的子任务接回轮询并续跑后续链路,
  // 避免"下载其实已完成、门户却卡在 running"(前端停止轮询导致状态没同步)。
  useEffect(() => {
    if (st.status !== 'running' || !traceId) return
    const subs = (useWorkflow.getState().stages.data || {}).sub_tasks || {}
    const poll = useWorkflow.getState().pollStage
    const dl = subs.downloader, dcS = subs.datacolle, inS = subs.insar
    if (dl?.status === 'running' && dl.task_id) poll(traceId, 'data', 'downloader', dl.task_id, { onDone: runDatacolle })
    else if (dcS?.status === 'running' && dcS.task_id) poll(traceId, 'data', 'datacolle', dcS.task_id, { onDone: runInsar })
    else if (inS?.status === 'running' && inS.task_id) poll(traceId, 'data', 'insar', inS.task_id, { onDone: finishData })
    else if (dl && ['completed', 'skipped'].includes(dl.status)) runDatacolle()  // 某环节已完成但链路没收尾 → 续跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traceId])
  const enterEvidence = async () => {
    if (!evidencePlan) {
      try {
        await generateEvidencePlan(traceId)
        message.success('证据编排单已生成')
      } catch (e) {
        message.error('证据编排生成失败:' + (e?.response?.data?.detail || e.message))
        return
      }
    }
    setActive('evidence')
  }
  return (
    <HudPanel label="数据准备">
      <h5>数据准备</h5>
      <div style={{ marginBottom: 6 }}><StatusBadge status={st.status} /></div>
      <div className="kv"><span>遥感下载(downloader)</span><b>{st.status === 'completed' ? '✓' : `${st.progress || 0}%`}</b></div>
      <div className="gauge"><i style={{ width: `${st.progress || 0}%` }} /></div>
      {st.status === 'running' && sensors.length > 0 && (
        <div style={{ margin: '8px 0 2px' }}>
          {sensors.map((s) => {
            const isActive = !s.finished && s.label === activeSensor
            return (
              <div key={s.sensor} className="kv" style={{ padding: '3px 0', fontSize: 11.5 }}>
                <span>{s.finished ? '✓' : isActive ? '⬇' : '○'} {s.label}</span>
                <b className={s.finished ? 'ok' : isActive ? 'run' : 'wt'}>
                  {s.finished ? '完成' : isActive ? '下载中…' : '排队'}
                </b>
              </div>
            )
          })}
        </div>
      )}
      <div className="kv" style={{ marginTop: 6 }}><span>遥感源</span><b>{rs.length} 项</b></div>
      <div className="kv"><span>InSAR 数据</span><b>{insar.length ? `${insar.length} 项` : '未选'}</b></div>
      <div className="kv"><span>data-colle 资料</span><b>{dc.length ? `${dc.length} 项(物探/化探)` : '未选'}</b></div>
      <StageTrace active="data" />
      {st.status === 'completed' && !dataIncomplete ? (
        <button className="pbtn" onClick={enterEvidence}>{evidencePlan ? '进入 2D 证据 →' : '生成证据编排 →'}</button>
      ) : st.status === 'running' ? (
        <>
          <button className="pbtn" disabled>
            {activeSensor ? `⬇ ${activeSensor} 下载中…（${st.progress || 0}%）` : `数据准备中…（${st.progress || 0}%）`}
          </button>
        </>
      ) : (
        <button className="pbtn" onClick={run}>▶ 开始下载 / 用交付库</button>
      )}
    </HudPanel>
  )
}
// ③ 证据子tab
function SubTab() {
  const selected = useWorkflow((s) => s.selectedEvidence)
  const focus = useWorkflow((s) => s.focusEvidence)
  const setFocus = useWorkflow((s) => s.setFocusEvidence)
  const evidences = useWorkflow((s) => s.evidences)
  const tabs = EVIDENCES.filter((e) => selected.includes(e.key))
  const hi = focus || tabs[0]?.key
  return (
    <div className="subtab glass">
      {tabs.map((t) => {
        const ev = evidences[t.key] || {}
        const dot = ev.status === 'completed' ? '●' : ev.status === 'running' ? '◐' : ev.status === 'failed' ? '✕' : '○'
        return (
          <b key={t.key} className={hi === t.key ? 'on' : ''} onClick={() => setFocus(t.key)}
            title={focus === t.key ? '点此取消聚焦(显示全部)' : '点此只看该证据图层'}>
            <span style={{ opacity: 0.6, marginRight: 4 }}>{dot}</span>{t.label}
          </b>
        )
      })}
    </div>
  )
}

// ③ 证据 HUD
function EvidenceHud() {
  const { evidences, stages, setActive, runEvidencesReal, runOneEvidenceReal, skipEvidence, degradeEvidence, selectedEvidence, setEvidenceVis, evidencePlan, generateEvidencePlan, saveEvidencePlan, setEvidencePlan, executeEvidencePlan } = useWorkflow()
  const { traceId } = useProject()
  const validEvidenceKeys = EVIDENCES.map((e) => e.key)
  const sel = (selectedEvidence || []).filter((k) => validEvidenceKeys.includes(k))
  const layerEvs = EVIDENCES.filter((e) => sel.includes(e.key) && evidences[e.key]?.layerUrl)
  const started = sel.some((k) => evidences[k]?.status !== 'pending')
  const done = evidenceDoneCount(evidences, sel)
  const canFuse = isUnlocked('model3d', stages, evidences, sel, evidencePlan)
  const failed = EVIDENCES.filter((e) => sel.includes(e.key) && evidences[e.key]?.status === 'failed')
  const pending = EVIDENCES.filter((e) => sel.includes(e.key) && evidences[e.key]?.status === 'pending')
  const running = sel.some((k) => evidences[k]?.status === 'running')
  const rows = buildEvidenceRows(evidences, sel)
  const summary = useProjectSummary(rows)
  const planTasks = evidencePlan?.evidence_tasks || []
  const gate = evidencePlanGateDetails(evidences, evidencePlan, sel)
  const gateMin = gate?.minDone || evidencePlan?.gate?.min_completed || EVIDENCE_GATE
  const requiredAny = gate?.requiredAny || evidencePlan?.gate?.required_any || []
  const requiredAnyOk = gate ? !gate.missingRequiredAny.length : (!requiredAny.length || requiredAny.some((k) => evidences[k]?.status === 'completed' || evidences[k]?.degraded))
  const missingRequiredLabels = (gate?.missingRequiredAny || []).map((k) => EVIDENCES.find((e) => e.key === k)?.label || k).join(' / ')
  const runnablePlanTasks = planTasks.filter((t) => t.enabled !== false && ['pending', 'failed'].includes(t.status || 'pending'))
  const updatePlanTask = async (key, patch) => {
    if (!evidencePlan) return
    const next = {
      ...evidencePlan,
      evidence_tasks: planTasks.map((t) => (t.key === key ? { ...t, ...patch } : t)),
    }
    setEvidencePlan(next)
    try { await saveEvidencePlan(traceId, next) } catch {}
  }
  const refreshPlan = async () => {
    try {
      await generateEvidencePlan(traceId)
      message.success('证据编排单已刷新')
    } catch (e) {
      message.error('证据编排生成失败:' + (e?.response?.data?.detail || e.message))
    }
  }
  const executePlan = async () => {
    try {
      await executeEvidencePlan(traceId)
      message.success('已按证据编排单启动待执行证据')
    } catch (e) {
      message.error('执行失败:' + (e?.response?.data?.detail || e.message))
    }
  }

  return (
    <HudPanel label="证据面板">
      <h5>图层 · 证据</h5>
      {!evidencePlan && (
        <button className="pbtn" onClick={refreshPlan}>生成证据编排单</button>
      )}
      {evidencePlan && (
        <div className="planbox">
          <div className="planbox-head">
            <b>证据编排单</b>
            <a onClick={refreshPlan}>重算</a>
          </div>
          <div className="plan-rationale">{evidencePlan.model} · {evidencePlan.rationale}</div>
          {(planTasks || []).map((t) => (
            <div className="plan-task" key={t.key} title={t.reason}>
              <label>
                <input type="checkbox" checked={t.enabled !== false}
                  onChange={(e) => updatePlanTask(t.key, { enabled: e.target.checked })} />
                <span>{t.label}</span>
              </label>
              <span className={`req ${t.required_level}`}>{t.required_level}</span>
              <input type="number" min="0" max="1" step="0.01" value={t.weight}
                onChange={(e) => updatePlanTask(t.key, { weight: Number(e.target.value) || 0 })} />
            </div>
          ))}
        </div>
      )}
      {layerEvs.length === 0 && <div style={{ color: 'var(--mut)', fontSize: 11.5, marginBottom: 4 }}>运行后显示真实证据图层</div>}
      {layerEvs.map((e) => {
        const ev = evidences[e.key]
        return (
          <div className="lr" key={e.key}>
            <input type="checkbox" checked={ev.visible !== false}
              onChange={(x) => setEvidenceVis(e.key, { visible: x.target.checked })} />
            {e.label}
            <input type="range" min={0} max={100} value={Math.round((ev.opacity ?? 0.75) * 100)}
              onChange={(x) => setEvidenceVis(e.key, { opacity: +x.target.value / 100 })} />
          </div>
        )
      })}
      <div className="kv" style={{ marginTop: 6 }}><span>已完成证据</span><b>{done} / {sel.length}</b></div>
      {evidencePlan && (
        <div className="kv"><span>关键门控</span><b>{requiredAnyOk ? '已满足' : `缺少 ${missingRequiredLabels}`}</b></div>
      )}
      <SummaryNote text={summary} />
      <h5 style={{ marginTop: 14 }}>证据矩阵</h5>
      <EvidenceMatrix rows={rows} />
      <StageTrace active="evidence" />
      {failed.map((f) => (
        <div className="failbox" key={f.key}>
          <div className="kv">
            <span style={{ color: 'var(--er)' }}>{f.label} 失败</span>
            <b>
              <a onClick={() => runOneEvidenceReal(traceId, f.key)} style={{ color: 'var(--cy)', cursor: 'pointer' }}>重试</a>
              {' / '}
              <a onClick={() => degradeEvidence(f.key, traceId)} style={{ color: 'var(--gd)', cursor: 'pointer' }}>降级</a>
              {' / '}
              <a onClick={() => skipEvidence(f.key, traceId)} style={{ color: 'var(--mut)', cursor: 'pointer' }}>跳过</a>
            </b>
          </div>
          {evidences[f.key]?.error && <div className="fail-reason">原因: {evidences[f.key].error}</div>}
        </div>
      ))}
      {!started && !evidencePlan && <button className="pbtn" onClick={() => runEvidencesReal(traceId)}>▶ 并行运行所选证据</button>}
      {!started && evidencePlan && <button className="pbtn" onClick={executePlan}>▶ 执行证据编排</button>}
      {started && (evidencePlan ? runnablePlanTasks.length > 0 : pending.length > 0) && (
        <button className="pbtn"
          onClick={() => (evidencePlan ? runnablePlanTasks : pending).forEach((e) => runOneEvidenceReal(traceId, e.key))}>
          ▶ 继续运行未完成证据 ({evidencePlan ? runnablePlanTasks.length : pending.length})
        </button>
      )}
      <button className="pbtn" disabled={!canFuse}
        onClick={() => { syncStage(traceId, 'evidence'); setActive('model3d') }}>
        {canFuse ? `≥${gateMin} 证据完成 → 融合 3D` : (missingRequiredLabels ? `缺少关键证据:${missingRequiredLabels}` : `需 ≥${gateMin} 证据`)}
      </button>
    </HudPanel>
  )
}



// ④ 3D 建模 HUD
function Model3DHud() {
  const { stages, evidences, selectedEvidence, setActive, runStageReal, model3d, evidencePlan } = useWorkflow()
  const { traceId } = useProject()
  const st = stages.model3d || {}
  const targets = model3d?.targets
  const top = targets?.[0]
  const stats = model3d?.stats
  const layers = stats?.available_layers
  const real = !!top
  const rows = buildEvidenceRows(evidences, selectedEvidence)
  const summary = useProjectSummary(rows)
  const modelParams = () => {
    if (!evidencePlan) return {}
    const tasks = (evidencePlan.evidence_tasks || []).filter((t) => {
      const ev = evidences[t.key] || {}
      return t.enabled !== false && (ev.status === 'completed' || ev.degraded)
    })
    return {
      evidence_keys: tasks.map((t) => t.key).join(','),
      evidence_weights: JSON.stringify(Object.fromEntries(tasks.map((t) => [t.key, Number(t.weight || 0)]))),
      evidence_model: evidencePlan.model || '',
    }
  }
  return (
    <HudPanel label="3D建模">
      <h5>靶点 #{top?.rank ?? 3} {real && <span style={{ color: 'var(--ok)', fontSize: 10 }}>真实</span>}</h5>
      <div style={{ marginBottom: 6 }}><StatusBadge status={st.status} /></div>
      <div className="big">{top ? Number(top.score).toFixed(2) : '0.82'}</div>
      <div style={{ color: 'var(--mut)', fontSize: 11 }}>有利度评分</div>
      <div className="kv"><span>深度</span><b>{top ? `${top.depth_m} m` : '1800 m'}</b></div>
      <div className="kv"><span>不确定性</span><b>{top ? Number(top.uncertainty).toFixed(2) : '0.15'}</b></div>
      {real && <div className="kv"><span>靶点总数</span><b>{targets.length}</b></div>}
      <h5 style={{ marginTop: 14 }}>模型概况</h5>
      <div className="kv"><span>证据层</span><b>{layers?.length ? layers.join('+') : '蚀变+构造'}</b></div>
      <div className="kv"><span>矿床类型</span><b style={{ fontSize: 11 }}>{stats?.deposit_type || '—'}</b></div>
      <div className="kv"><span>融合法</span><b>{stats?.fusion_method || 'knowledge'}</b></div>
      <SummaryNote text={summary} />
      <StageTrace active="model3d" />

      {real && (
        <>
          <h5 style={{ marginTop: 14 }}>深度剖面</h5>
          <img alt="depth profile"
            src={`/svc/model3d/api/result/${model3d.taskId}/figures/depth_profile.png`}
            onError={(e) => { e.currentTarget.style.display = 'none' }}
            style={{ width: '100%', borderRadius: 8, border: '1px solid var(--line)', background: '#fff' }} />
          <h5 style={{ marginTop: 12 }}>Top 靶点</h5>
          <div style={{ maxHeight: 116, overflow: 'auto' }}>
            {targets.slice(0, 8).map((t) => (
              <div className="kv" key={t.rank}>
                <span>#{t.rank} · {t.depth_m}m</span>
                <b>{Number(t.score).toFixed(2)} <span style={{ color: 'var(--mut)', fontSize: 10 }}>±{Number(t.uncertainty).toFixed(2)}</span></b>
              </div>
            ))}
          </div>
          <button className="pbtn" onClick={() => window.open(`/svc/model3d/api/result/${model3d.taskId}/viewer_3d.html`, '_blank')}>↗ 打开完整 3D 报告</button>
        </>
      )}

      {st.status === 'failed' && (
        <div style={{ color: 'var(--er)', fontSize: 11.5, marginTop: 10, lineHeight: 1.5 }}>
          建模失败:该 AOI 无可用上游证据(蚀变/构造/形变均缺)。请确认已上传匹配 AOI 的 KML、且该区有证据数据,或先在 ③ 跑出证据。
        </div>
      )}

      {st.status !== 'completed'
        ? <button className="pbtn" onClick={() => runStageReal(traceId, 'model3d', 'model3d', { onDone: () => syncStage(traceId, 'model3d'), params: modelParams() })} disabled={st.status === 'running'}>
            {st.status === 'running' ? '融合中…' : (st.status === 'failed' ? '↻ 重试 3D 融合' : '▶ 运行 3D 融合')}</button>
        : <button className="pbtn" onClick={() => setActive('drill')}>进入 AI 布孔 →</button>}
    </HudPanel>
  )
}

// ⑤ 布孔 + 闭环 HUD
function DrillHud() {
  const { stages, setActive, runStageReal, setStage, model3d, drill } = useWorkflow()
  const { traceId, current } = useProject()
  const [dlDrill, setDlDrill] = useState(false)
  const st = stages.drill || {}
  // 前置门控:布孔硬依赖 geo-model3d 三维有利度产物(targets 由历史产物或本次真实建模填充;
  // mock 回退的"完成"不会填 targets)。无产物则禁用,避免点了必失败。
  const hasModel3d = (model3d?.targets?.length || 0) > 0
  // 真实派生(不再写死):孔数/A级/斜孔来自 planned_holes;见矿来自 drill_feedback(需岩芯编录,无则"待编录")
  const holes = drill?.holes || []
  const nHoles = holes.length
  const nA = holes.filter((h) => h.priority === 'A').length
  const nIncl = holes.filter((h) => Math.abs((h.dip_deg ?? -90) + 90) > 1).length
  const fb = drill?.feedback || []
  const hasCore = fb.length > 0
  const nOre = fb.filter((f) => (f.outcome || f.result) === 'ore').length
  const rerun = () => { setStage('model3d', { status: 'completed' }); message.success('已回灌 drill_feedback,model3d 将重算(同 trace_id)') }
  // 下载设计好的 AI 布孔数据表(holes_table.csv:孔号/经纬/深度/方位/倾角/得分/优先级)
  const downloadDrill = async () => {
    if (!current?.id) return
    setDlDrill(true)
    try {
      const blob = await api.downloadDrillData(current.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `AI布孔数据_${current.name || '布孔'}.csv`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      message.error(e?.response?.data?.detail || '布孔数据下载失败')
    } finally { setDlDrill(false) }
  }
  return (
    <HudPanel label="AI布孔">
      <h5>AI 布孔 + 闭环</h5>
      <div style={{ marginBottom: 6 }}><StatusBadge status={st.status} /></div>
      <div className="kv"><span>计划孔</span><b>{nHoles || '—'}</b></div>
      <div className="kv"><span>A级靶孔 / 斜孔</span><b>{nHoles ? `${nA} / ${nIncl}` : '—'}</b></div>
      <div className="drop" style={{ margin: '8px 0' }}>⬆ 岩芯编录 CSV</div>
      <div className="kv"><span>见矿判定</span>
        {hasCore
          ? <b style={{ color: 'var(--ok)' }}>{nOre} 孔见矿</b>
          : <b style={{ color: 'var(--mut)' }} title="需上传岩芯编录 CSV(collar/SWIR/XRF)才能判定见矿">待岩芯编录</b>}
      </div>
      <StageTrace active="drill" />
      {st.status === 'failed' && (
        <div className="failbox" style={{ margin: '6px 0' }}>
          <span style={{ color: 'var(--er)' }}>{st.error || '布孔失败：该研究区暂无 geo-model3d 三维有利度产物，请先在 ④ 3D 建模出三维成矿预测，再来布孔。'}</span>
        </div>
      )}
      {st.status !== 'completed' && !hasModel3d && (
        <div className="failbox" style={{ margin: '6px 0' }}>
          <span style={{ color: 'var(--gd)' }}>本研究区尚无 geo-model3d 三维有利度产物，布孔无依据。请先在 ④ 3D 建模跑出三维成矿预测。</span>
          <div style={{ marginTop: 4 }}>
            <a onClick={() => setActive('model3d')} style={{ color: 'var(--cy)', cursor: 'pointer' }}>← 去 3D 建模</a>
          </div>
        </div>
      )}
      {st.status !== 'completed'
        ? <button className="pbtn" onClick={() => runStageReal(traceId, 'drill', 'drill', { onDone: () => syncStage(traceId, 'drill') })} disabled={st.status === 'running' || !hasModel3d}>
            {st.status === 'running' ? '布孔中…' : '▶ AI 布孔'}</button>
        : <>
            <button className="pbtn" onClick={downloadDrill} disabled={dlDrill || !nHoles}>
              {dlDrill ? '下载中…' : '⬇ 下载 AI 布孔数据'}</button>
            <button className="pbtn" onClick={rerun}>♻ 回灌重算(同 trace_id)</button>
            <button className="pbtn" onClick={() => setActive('report')}>生成报告 →</button>
          </>}
    </HudPanel>
  )
}

// ⑥ 报告 HUD
function ReportHud() {
  const { stages, evidences, selectedEvidence, runStageReal } = useWorkflow()
  const { traceId } = useProject()
  const st = stages.report || {}
  const reportTaskId = st.taskId || st.sub_tasks?.reporter?.task_id
  const [reportDownloading, setReportDownloading] = useState('')
  const rows = buildEvidenceRows(evidences, selectedEvidence)
  const summary = useProjectSummary(rows)
  const downloadReport = async (fmt = 'docx') => {
    if (!reportTaskId) {
      message.success('报告已就绪')
      return
    }
    setReportDownloading(fmt)
    try {
      const blob = await api.downloadAdapterReport(reportTaskId, fmt)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `报告.${fmt}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      message.error(e?.response?.data?.detail || '报告下载失败')
    } finally {
      setReportDownloading('')
    }
  }
  return (
    <HudPanel label="综合报告">
      <h5>综合报告</h5>
      <div style={{ marginBottom: 6 }}><StatusBadge status={st.status} /></div>
      <div className="kv"><span>地理+8类资料</span><b style={{ color: 'var(--ok)' }}>✓</b></div>
      <div className="kv"><span>蚀变/构造/物探图</span><b style={{ color: 'var(--ok)' }}>✓</b></div>
      <div className="kv"><span>3D预测+布孔章</span><b>{st.status === 'completed' ? '✓' : `${st.progress || 0}%`}</b></div>
      <div className="gauge"><i style={{ width: `${st.progress || 0}%` }} /></div>
      <SummaryNote text={summary} />
      <StageTrace active="report" />
      {st.status !== 'completed'
        ? <button className="pbtn" onClick={() => runStageReal(traceId, 'report', 'reporter', { onDone: () => syncStage(traceId, 'report') })} disabled={st.status === 'running'}>
            {st.status === 'running' ? '生成中…' : '▶ 生成报告'}</button>
        : <div style={{ display: 'grid', gap: 8 }}>
            <button className="pbtn" onClick={() => downloadReport('docx')} disabled={!!reportDownloading}>
              {reportDownloading === 'docx' ? '下载中…' : '⬇ 下载 报告.docx'}
            </button>
            <button className="pbtn" onClick={() => downloadReport('pptx')} disabled={!!reportDownloading}>
              {reportDownloading === 'pptx' ? '下载中…' : '⬇ 下载 报告.pptx'}
            </button>
            <button className="pbtn ghost" disabled={!!reportDownloading}
              onClick={() => runStageReal(traceId, 'report', 'reporter', { onDone: () => syncStage(traceId, 'report') })}
              title="若下载提示产物已丢失(服务重启等),点此重新生成报告">↻ 重新生成报告</button>
          </div>}
    </HudPanel>
  )
}

// 把阶段完成状态回写 BFF run(失败静默,不阻塞)
function syncStage(traceId, stage, patch = {}) {
  if (!traceId) return
  api.patchStage(traceId, stage, { status: 'completed', progress: 100, ...patch }).catch(() => {})
}
