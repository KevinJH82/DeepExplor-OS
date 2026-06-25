import { useMemo, useState, useEffect, Suspense, lazy } from 'react'
import { useWorkflow, useProject, dataSourcesFromPlan } from '../store'
import { stageIndex, EVIDENCES, DATA_SOURCES, SOURCE_FEEDS, SOURCE_SERVICES } from '../lib/stages'
import { buildEvidenceRows } from '../lib/evidenceChain'
import { buildProjectNarrative } from '../lib/geologyNarrative'
import * as api from '../api/portal'

const Terrain3D = lazy(() => import('./Terrain3D'))   // 懒加载隔离 three 体积(仅 3D 阶段加载)

const SOURCE_ITEMS = DATA_SOURCES.flatMap((g) => g.items.map((item) => ({ ...item, group: g.group, note: g.note })))
const SOURCE_TO_EVIDENCE = {
  sentinel2: ['analyser', 'stru'],
  landsat8: ['analyser', 'stru'],
  sentinel1: ['insar', 'stru'],
  aster: ['analyser'],
  dem: ['stru'],
  emag2: ['geophys'],
  gravity: ['geophys'],
  geochem_bg: ['geochem'],
  mineral_kb: [],
}

export default function Canvas() {
  const active = useWorkflow((s) => s.active)
  const stages = useWorkflow((s) => s.stages)
  const m3d = useWorkflow((s) => s.model3d)
  const run = useWorkflow((s) => s.run)
  const drill = useWorkflow((s) => s.drill)
  const evidences = useWorkflow((s) => s.evidences)
  const selectedEvidence = useWorkflow((s) => s.selectedEvidence)
  const selectedSources = useWorkflow((s) => s.selectedSources)
  const sourceMeta = useWorkflow((s) => s.sourceMeta)
  const evidencePlan = useWorkflow((s) => s.evidencePlan)
  const focus = useWorkflow((s) => s.focusEvidence)
  const setFocus = useWorkflow((s) => s.setFocusEvidence)
  const runOne = useWorkflow((s) => s.runOneEvidenceReal)
  const loadEvidenceLayer = useWorkflow((s) => s.loadEvidenceLayer)
  const current = useProject((s) => s.current)
  const bbox = useProject((s) => s.current?.aoi_bbox)
  const projectId = useProject((s) => s.current?.id)
  const traceId = useProject((s) => s.traceId)

  const idx = stageIndex(active)
  const is3d = idx >= 3                                  // model3d/drill/report

  const showDrill = active === 'drill' || active === 'report'

  const [sel, setSel] = useState(null)
  useEffect(() => { setSel(null) }, [active])

  // ROI 卫星底图(复用 /api/basemap,与 3D 地形同源):证据栅格叠在真实底图上,不再投在空画布
  const [roiBase, setRoiBase] = useState(null)
  useEffect(() => {
    if (!projectId || !bbox) { setRoiBase(null); return }
    let alive = true; let obj = null
    api.basemapObjectUrl(projectId).then((u) => { if (alive) { obj = u; setRoiBase(u) } }).catch(() => {})
    return () => { alive = false; if (obj) URL.revokeObjectURL(obj) }
  }, [projectId, bbox])
  useEffect(() => {
    if (!is3d) return
    EVIDENCES.forEach((e) => {
      const ev = evidences[e.key] || {}
      const taskId = ev.taskId || ev.planTask?.task_id
      if (ev.status === 'completed' && taskId && !ev.layerUrl && !ev.noLayer && !ev.layerLoading && !ev.layerLoadFailed) {
        loadEvidenceLayer(e.key, e.svc, taskId)
      }
    })
  }, [is3d, evidences, loadEvidenceLayer])
  const evidenceRows = useMemo(
    () => buildEvidenceRows(evidences, selectedEvidence),
    [evidences, selectedEvidence],
  )
  const targetSections = useMemo(() => {
    if (!sel) return []
    return buildProjectNarrative({
      current,
      evidenceRows,
      selectedSources: [],
      model3d: m3d,
      target: sel,
      run,
    }).filter((s) => ['project', 'target', 'risk'].includes(s.key))
  }, [current, evidenceRows, m3d, run, sel])
  const plannedSources = useMemo(() => (run?.plan ? dataSourcesFromPlan(run.plan) : { keys: [], meta: {} }), [run])
  const effectiveSources = useMemo(
    () => Array.from(new Set([...(selectedSources || []), ...(plannedSources.keys || [])])),
    [selectedSources, plannedSources],
  )
  const effectiveSourceMeta = useMemo(
    () => ({ ...(plannedSources.meta || {}), ...(sourceMeta || {}) }),
    [plannedSources, sourceMeta],
  )

  return (
    <div id="stage" className={`stage-${active}`}>
      {/* 2D 地图层 */}
      <div className={`canv ${is3d ? 'hide' : ''}`} id="map2d"
        style={{ transform: is3d ? 'scale(.6) translateY(40px)' : 'scale(1)' }}>
        {active === 'data' ? (
          <DataCanvas
            current={current}
            bbox={bbox}
            stage={stages.data || {}}
            selectedSources={effectiveSources}
            sourceMeta={effectiveSourceMeta}
            selectedEvidence={selectedEvidence}
          />
        ) : (
          <div className="mapbox">
          <div className="aoi">
            <span className="lab">{current?.name || 'AOI'}</span>
            {/* ROI 真实卫星底图(垫底):证据栅格叠在其上,避免投在空画布显得假 */}
            {active === 'evidence' && roiBase && (
              <img src={roiBase} alt="ROI 卫星底图" style={{
                position: 'absolute', inset: 0, width: '100%', height: '100%',
                objectFit: 'fill', pointerEvents: 'none', borderRadius: 6,
              }} />
            )}
            {/* ③证据:真实证据栅格叠加(铺满 AOI);聚焦时只看该层 */}
            {active === 'evidence' && EVIDENCES.map((e) => {
              const ev = evidences[e.key]
              if (!ev?.layerUrl || ev.visible === false) return null
              if (focus && focus !== e.key) return null   // solo 聚焦证据
              return (
                <img key={e.key} src={ev.layerUrl} alt={e.label} style={{
                  position: 'absolute', inset: 0, width: '100%', height: '100%',
                  objectFit: 'fill', opacity: ev.opacity ?? 0.75, pointerEvents: 'none',
                }} />
              )
            })}
            {active === 'evidence' && (() => {
              const validKeys = EVIDENCES.map((e) => e.key)
              const selectedKeys = (selectedEvidence?.length ? selectedEvidence : validKeys).filter((k) => validKeys.includes(k))
              const fk = (focus && validKeys.includes(focus) ? focus : null) || selectedKeys[0]
              const fe = evidences[fk] || {}
              const label = EVIDENCES.find((e) => e.key === fk)?.label || ''
              // 该聚焦证据有可见图层 → 显示图层,不显示占位
              if (fe.layerUrl && fe.visible !== false && (!focus || focus === fk)) return null
              const archivedMsg = `${label}:已有产物记录, 当前视图尚未接入代表性栅格`
              const noRasterMsg = fk === 'geochem'
                ? `${label}:已完成,本区无栅格产物(化探异常为矢量,需点数据)`
                : `${label}:已完成,但未取到栅格`
              const txt = {
                completed: fe.archived ? archivedMsg : noRasterMsg,
                running: fe.note ? `${label}:${fe.note}` : `${label}:运行中…`,
                failed: `${label}:${fe.error || '运行失败'}`,
                skipped: `${label}:已跳过`,
              }[fe.status] || (evidencePlan
                ? `${label}:待执行 — 按右侧证据编排单运行`
                : `${label}:未运行 — 点右侧「并行运行所选证据」`)
              const canRerun = traceId && ['completed', 'failed'].includes(fe.status) && fk !== 'geochem'
              return (
                <div style={{
                  position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', gap: 8,
                  alignItems: 'center', justifyContent: 'center', color: 'var(--mut)', fontSize: 13, textAlign: 'center', padding: 20,
                }}>
                  <div>{txt}</div>
                  {canRerun && (
                    <span onClick={() => runOne(traceId, fk)}
                      style={{ cursor: 'pointer', color: '#0a8aa3', border: '1px solid rgba(10,162,192,.4)', borderRadius: 8, padding: '3px 12px', fontSize: 12 }}>
                      ↻ 重跑「{label}」
                    </span>
                  )}
                </div>
              )
            })()}
          </div>
          </div>
        )}
      </div>

      {/* 3D 真实卫星地形层(DEM 起伏 + 卫星纹理 + 靶点/钻孔/切片) */}
      {is3d && (
        <Suspense fallback={<div className="canv" style={{ width: 'min(72vw,900px)', height: 'min(66vh,620px)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--mut)', fontSize: 13 }}>加载三维场景…</div>}>
          <Terrain3D
            bbox={bbox} projectId={projectId} model3d={m3d} drill={drill} evidences={evidences}
            focusEvidence={focus}
            onFocusEvidence={setFocus}
            showDrill={showDrill} report={active === 'report'}
            selectedIndex={sel?._i}
            onSelectTarget={(data, i) => setSel({ ...data, _i: i })}
          />
        </Suspense>
      )}

      {sel && (
        <div className="glass" style={{
          position: 'fixed', left: 18, bottom: 96, width: 300, maxHeight: 'calc(100vh - 140px)', overflow: 'auto', padding: 14, zIndex: 19,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
            <h5 style={{ margin: 0, color: '#13324d' }}>靶点 #{sel.rank} {sel.demo && <span style={{ color: 'var(--mut)', fontSize: 10 }}>示意</span>}</h5>
            <span onClick={() => setSel(null)} style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--mut)' }}>✕</span>
          </div>
          <div className="kv"><span>有利度评分</span><b>{Number(sel.score).toFixed(3)}</b></div>
          <div className="kv"><span>深度</span><b>{sel.depth_m} m</b></div>
          <div className="kv"><span>不确定性</span><b>{sel.uncertainty != null ? Number(sel.uncertainty).toFixed(3) : '—'}</b></div>
          {sel.lon != null && <div className="kv"><span>经度</span><b>{Number(sel.lon).toFixed(5)}</b></div>}
          {sel.lat != null && <div className="kv"><span>纬度</span><b>{Number(sel.lat).toFixed(5)}</b></div>}
          <h5 style={{ margin: '12px 0 8px', color: '#13324d' }}>为何选中</h5>
          <div className="target-rationale">
            {targetSections.map((s) => (
              <section key={s.key}>
                <b>{s.title}</b>
                {s.lines.slice(0, 2).map((line, i) => <p key={`${s.key}-${i}`}>{line}</p>)}
              </section>
            ))}
          </div>
          <h5 style={{ margin: '12px 0 8px', color: '#13324d' }}>证据链</h5>
          <div className="target-chain">
            {evidenceRows.map((r) => (
              <div className="target-ev" key={r.key}>
                <div>
                  <b>{r.label}</b>
                  <span>{r.relation}</span>
                </div>
                <strong className={r.contribution === '强支撑' ? 'support strong' : r.contribution === '缺失' ? 'support weak' : 'support'}>
                  {r.contribution}
                </strong>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function DataCanvas({ current, bbox, stage, selectedSources = [], sourceMeta = {}, selectedEvidence = [] }) {
  const progress = Math.max(0, Math.min(100, stage.progress || 0))
  const status = stage.status || 'pending'
  const remote = selectedSources.filter((k) => SOURCE_SERVICES[k] === 'downloader')
  const insar = selectedSources.filter((k) => SOURCE_SERVICES[k] === 'insar')
  const datacolle = selectedSources.filter((k) => SOURCE_SERVICES[k] === 'datacolle')
  const selectedSet = new Set(selectedSources)
  const evidenceSet = new Set(selectedEvidence)
  const tiles = useMemo(() => Array.from({ length: 24 }, (_, i) => i), [])
  const litCount = status === 'completed' ? tiles.length : Math.round((progress / 100) * tiles.length)
  const sourceRows = SOURCE_ITEMS.filter((s) => selectedSet.has(s.key)).map((s) => ({ ...s, meta: sourceMeta[s.key] || {} }))
  const evidenceLinks = EVIDENCES.filter((e) => evidenceSet.has(e.key)).map((e) => ({
    ...e,
    sources: sourceRows.filter((s) => {
      const feeds = SOURCE_TO_EVIDENCE[s.key] || (SOURCE_FEEDS[s.key] ? [SOURCE_FEEDS[s.key]] : [])
      return feeds.includes(e.key)
    }),
  }))

  return (
    <div className={`data-canvas ${status}`}>
      <div className="data-topline">
        <div>
          <b>{current?.name || '研究区'}</b>
          <span>{bbox?.length === 4 ? bbox.map((n) => Number(n).toFixed(3)).join(', ') : 'AOI 样例范围'}</span>
        </div>
        <strong>{statusText(status, progress)}</strong>
      </div>

      <div className="data-grid">
        <div className="data-sources">
          {sourceRows.map((s) => (
            <div className={`data-source ${sourceClass(s.key, stage)}`} key={s.key} title={s.meta.reason || ''}>
              <span>{s.label}</span>
              <i>{sourceBadge(s)}</i>
            </div>
          ))}
        </div>

        <div className="data-roi">
          <div className="data-roi-head">
            <b>ROI 数据包</b>
            <span>{remote.length} 遥感 · {insar.length} InSAR · {datacolle.length} 资料</span>
          </div>
          <div className="data-tiles">
            {tiles.map((t) => (
              <i key={t} className={t < litCount ? 'on' : ''} style={{ transitionDelay: `${(t % 6) * 40}ms` }} />
            ))}
          </div>
          <div className="data-progress"><i style={{ width: `${progress}%` }} /></div>
        </div>

        <div className="data-evidence-targets">
          {evidenceLinks.map((e) => (
            <div className={`data-target ${e.sources.length ? 'linked' : ''}`} key={e.key}>
              <b>{e.label}</b>
              <span>{e.sources.length ? e.sources.map((s) => s.label).slice(0, 2).join(' / ') : '待补数据'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function sourceClass(key, stage = {}) {
  const service = SOURCE_SERVICES[key]
  const kind = service === 'downloader' ? 'remote' : service === 'insar' ? 'insar' : 'archive'
  const sub = (stage.sub_tasks || {})[service]
  const status = sub?.status || stage.status
  const state = status === 'completed' ? 'done' : status === 'failed' ? 'failed' : status === 'skipped' ? 'skipped' : ''
  return [kind, state].filter(Boolean).join(' ')
}

function sourceBadge(source) {
  const service = SOURCE_SERVICES[source.key]
  const seasons = (source.meta?.seasons || []).join('/')
  const req = source.meta?.required ? '必选' : source.meta?.source ? '编排' : ''
  const type = service === 'downloader' ? '遥感' : service === 'insar' ? 'InSAR' : '资料'
  return [type, seasons, req].filter(Boolean).join(' · ')
}

function statusText(status, progress) {
  if (status === 'completed') return '数据就绪'
  if (status === 'running') return `${progress}%`
  if (status === 'failed') return '准备失败'
  return '待准备'
}
