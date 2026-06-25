import { useEffect, useState } from 'react'
import { useWorkflow, useProject } from '../store'
import { buildEvidenceRows } from '../lib/evidenceChain'
import { buildProjectNarrative, buildProjectSummary } from '../lib/geologyNarrative'

const ACTIVE_BY_STAGE = {
  plan: ['project', 'data'],
  data: ['data'],
  evidence: ['structure', 'alteration', 'geo', 'risk'],
  model3d: ['target', 'risk'],
  drill: ['target'],
  report: ['project', 'target', 'risk'],
}

export default function EvidenceStoryline() {
  const [mode, setMode] = useState(() => localStorage.getItem('storyline_mode') || 'collapsed')
  const active = useWorkflow((s) => s.active)
  const evidences = useWorkflow((s) => s.evidences)
  const selectedEvidence = useWorkflow((s) => s.selectedEvidence)
  const selectedSources = useWorkflow((s) => s.selectedSources)
  const model3d = useWorkflow((s) => s.model3d)
  const run = useWorkflow((s) => s.run)
  const current = useProject((s) => s.current)
  const setStoryMode = (next) => {
    setMode(next)
    localStorage.setItem('storyline_mode', next)
  }

  useEffect(() => {
    if (!['expanded', 'collapsed', 'hidden'].includes(mode)) setStoryMode('collapsed')
  }, [mode])

  if (!current || active === 'plan') return null

  const rows = buildEvidenceRows(evidences, selectedEvidence)
  const sections = buildProjectNarrative({ current, evidenceRows: rows, selectedSources, model3d, run })
  const summary = buildProjectSummary({ current, evidenceRows: rows, selectedSources, model3d, run })
  const activeKeys = ACTIVE_BY_STAGE[active] || []
  const lineLimit = (key) => {
    if (key === 'project' || key === 'data') return 3
    if (key === 'structure') return 4
    if (key === 'target') return 4
    if (key === 'risk') return 3
    return 2
  }

  if (mode === 'hidden') {
    return (
      <button className="storyline-chip glass" onClick={() => setStoryMode('collapsed')} title="显示证据链">
        证据链
      </button>
    )
  }

  return (
    <div className={`storyline glass ${mode === 'collapsed' ? 'compact' : 'expanded'}`}>
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
