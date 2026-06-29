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
  const run = useWorkflow((s) => s.run)
  const datacolle = useWorkflow((s) => s.datacolleEvidence)
  const current = useProject((s) => s.current)
  const manualEvidence = useProject((s) => s.manualEvidence)
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
  const narrativeInput = { current, evidenceRows: rows, selectedSources, model3d, run, datacolle }
  const sections = buildProjectNarrative(narrativeInput)
  const summary = buildProjectSummary(narrativeInput)
  const activeKeys = ACTIVE_BY_STAGE[active] || []
  const lineLimit = (key) => {
    if (key === 'context' || key === 'logic') return 4
    if (key === 'chain' || key === 'target') return 6
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
    </div>
  )
}
