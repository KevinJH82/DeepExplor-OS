import { useWorkflow } from '../store'
import { EVIDENCES } from '../lib/stages'

const LED = { completed: 'ok', running: 'run', failed: 'er', skipped: 'wt', pending: 'wt' }
const PC = { completed: '✓', failed: '失败', skipped: '跳过', pending: '待' }

// 左侧证据融合栈(③证据 / ④融合 时显示)
export default function EviStack() {
  const active = useWorkflow((s) => s.active)
  const evidences = useWorkflow((s) => s.evidences)
  const selected = useWorkflow((s) => s.selectedEvidence)
  if (active !== 'evidence' && active !== 'model3d') return null
  return (
    <div className="evi glass">
      <h6>{active === 'model3d' ? '证据融合' : '证据层'}</h6>
      {EVIDENCES.filter((e) => selected.includes(e.key)).map((e) => {
        const st = evidences[e.key] || {}
        return (
          <div key={e.key} className="ev">
            <span className={`led ${LED[st.status] || 'wt'}`}>●</span>
            <span className="nm">{e.label}</span>
            <span className="pc">{st.status === 'running' ? `${st.progress || 0}%` : (PC[st.status] || '')}</span>
          </div>
        )
      })}
      <div className="fuse" />
      <div className="fuselbl">▼ 融合入体</div>
    </div>
  )
}
