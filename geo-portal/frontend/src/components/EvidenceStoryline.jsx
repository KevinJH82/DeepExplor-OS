import { useEffect, useRef, useState } from 'react'
import { useWorkflow, useProject } from '../store'
import { buildEvidenceRows } from '../lib/evidenceChain'
import { buildProjectNarrative, buildProjectSummary } from '../lib/geologyNarrative'

const ACTIVE_BY_STAGE = {
  plan: ['context', 'logic'],
  data: ['context', 'logic'],
  evidence: ['chain', 'risk'],
  model3d: ['target', 'risk'],
  drill: ['target', 'next'],
  report: ['context', 'chain', 'target', 'next', 'risk'],
}

export default function EvidenceStoryline() {
  const [mode, setMode] = useState(() => localStorage.getItem('storyline_mode') || 'collapsed')
  const active = useWorkflow((s) => s.active)
  const evidences = useWorkflow((s) => s.evidences)
  const selectedEvidence = useWorkflow((s) => s.selectedEvidence)
  const selectedSources = useWorkflow((s) => s.selectedSources)
  const model3d = useWorkflow((s) => s.model3d)
  const drill = useWorkflow((s) => s.drill)
  const stages = useWorkflow((s) => s.stages)
  const run = useWorkflow((s) => s.run)
  const datacolle = useWorkflow((s) => s.datacolleEvidence)
  const current = useProject((s) => s.current)
  const traceId = useProject((s) => s.traceId)
  const manualEvidence = useProject((s) => s.manualEvidence)
  const synthesis = useWorkflow((s) => s.synthesis)
  const synthesisLoading = useWorkflow((s) => s.synthesisLoading)
  const loadSynthesis = useWorkflow((s) => s.loadSynthesis)
  const panelRef = useRef(null)
  const setStoryMode = (next) => {
    setMode(next)
    localStorage.setItem('storyline_mode', next)
  }

  useEffect(() => {
    if (!['expanded', 'collapsed', 'hidden'].includes(mode)) setStoryMode('collapsed')
  }, [mode])

  // 展开态下点击证据链以外的空白处 → 收起
  useEffect(() => {
    if (mode !== 'expanded') return
    const onDown = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setStoryMode('collapsed')
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [mode])

  if (!current || active === 'plan') return null

  const rows = buildEvidenceRows(evidences, selectedEvidence)
  const narrativeInput = { current, evidenceRows: rows, selectedSources, model3d, drill, run, datacolle }
  const sections = buildProjectNarrative(narrativeInput)
  const summary = buildProjectSummary(narrativeInput)
  const activeKeys = ACTIVE_BY_STAGE[active] || []
  // 综合研判需在 3D 建模(证据整合出靶点)之后才有意义;此前置灰
  const model3dReady = stages?.model3d?.status === 'completed' || (model3d?.targets?.length > 0)
  const lineLimit = (key) => {
    if (key === 'context' || key === 'logic') return 4
    if (key === 'target') return 7   // 含新增"权重分配逻辑"句,放宽一行避免挤掉靶点置信
    if (key === 'chain') return 6
    if (key === 'next' || key === 'risk') return 4
    return 3
  }

  if (mode === 'hidden') {
    return (
      <button className="storyline-chip glass" onClick={() => setStoryMode('collapsed')} title="显示证据链">
        证据链
      </button>
    )
  }

  return (
    <div ref={panelRef} className={`storyline glass ${mode === 'collapsed' ? 'compact' : 'expanded'}`}>
      <div className="storyline-head">
        <b>证据链叙事</b>
        <span>{current.name} · {current.mineral_label}</span>
        <div className="story-actions">
          {mode === 'collapsed'
            ? <button onClick={() => setStoryMode('expanded')}>展开</button>
            : <button onClick={() => setStoryMode('collapsed')}>收起</button>}
          <button onClick={() => setStoryMode('hidden')}>隐藏</button>
        </div>
      </div>
      {manualEvidence?.length > 0 && (
        <div className="story-manual">
          ↻ 手工证据 {manualEvidence.length} 项 · 待融合:{[...new Set(manualEvidence.map((m) => m.label))].join('、')}
        </div>
      )}
      {mode === 'collapsed'
        ? <div className="story-summary">{summary || sections.find((s) => s.key === 'target')?.lines?.[0]}</div>
        : (
          <div className="story-cards">
            {sections.map((s) => (
              <section key={s.key} className={`story-card ${s.weight} ${activeKeys.includes(s.key) ? 'on' : ''}`}>
                <h6>{s.title}</h6>
                {s.lines.slice(0, lineLimit(s.key)).map((line, i) => <p key={`${s.key}-${i}`}>{line}</p>)}
              </section>
            ))}
          </div>
        )}
      {mode === 'expanded' && (
        <div className="story-synth">
          <div className="ss-head">
            <b>证据链综合研判 (AI)</b>
            {synthesis?.available && synthesis.grade && <span className={`ss-grade g-${synthesis.grade}`}>{synthesis.grade} 级</span>}
            <button disabled={synthesisLoading || !model3dReady}
              title={model3dReady ? '' : '需先完成 3D 建模(证据整合)后再研判'}
              onClick={() => loadSynthesis(traceId, !!synthesis?.available)}>
              {synthesisLoading ? '研判中…' : synthesis ? '↻ 重新研判' : '▷ 生成研判'}
            </button>
          </div>
          {synthesis?.available ? (
            <>
              {synthesis.summary && <p className="ss-sum">{synthesis.summary}</p>}
              {(synthesis.dimensions || []).map((d, i) => (
                <div key={i} className="ss-dim"><em>{d.name}</em><span className={`lv lv-${d.level}`}>{d.level}</span><span>{d.evidence}</span></div>
              ))}
              {(synthesis.target_assessment || []).length > 0 && <div className="ss-tg-h">靶点研判</div>}
              {(synthesis.target_assessment || []).map((t, i) => (
                <div key={i} className="ss-tg">#{t.rank} <b>{t.grade}</b> {t.reason}</div>
              ))}
            </>
          ) : synthesis ? (
            <p className="ss-na">{synthesis.reason || '未启用'}</p>
          ) : !model3dReady ? (
            <p className="ss-na">综合研判需在 3D 建模(证据整合出靶点)之后进行;当前证据尚未整合,暂不可研判。</p>
          ) : (
            <p className="ss-na">点「生成研判」,基于本项目真实数据产出 AI 综合研判(需 LLM)。</p>
          )}
        </div>
      )}
    </div>
  )
}
