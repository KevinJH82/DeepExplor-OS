import { useWorkflow } from '../store'
import { STAGES, stageIndex, isUnlocked } from '../lib/stages'

// 底部环形流程轨道(替代步骤清单)
export default function Dock() {
  const active = useWorkflow((s) => s.active)
  const stages = useWorkflow((s) => s.stages)
  const evidences = useWorkflow((s) => s.evidences)
  const selected = useWorkflow((s) => s.selectedEvidence)
  const evidencePlan = useWorkflow((s) => s.evidencePlan)
  const setActive = useWorkflow((s) => s.setActive)

  const curIdx = stageIndex(active)

  return (
    <div className="dock glass">
      {STAGES.map((st, i) => {
        const done = stages[st.id]?.status === 'completed'
        const cur = st.id === active
        const unlocked = isUnlocked(st.id, stages, evidences, selected, evidencePlan)
        const cls = ['node', cur && 'cur', done && 'done', !unlocked && !cur && 'lock'].filter(Boolean).join(' ')
        return (
          <div key={st.id} style={{ display: 'flex', alignItems: 'center' }}>
            <div className={cls} onClick={() => unlocked && setActive(st.id)} title={st.title}>
              <div className="o">{st.icon}</div>
              <span>{st.label}</span>
            </div>
            {i < STAGES.length - 1 && <div className={`link ${i < curIdx ? 'done' : ''}`} />}
          </div>
        )
      })}
    </div>
  )
}
