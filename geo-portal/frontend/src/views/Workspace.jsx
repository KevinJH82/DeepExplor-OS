import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useProject, useWorkflow } from '../store'
import { STAGES } from '../lib/stages'
import TopBar from '../components/TopBar'
import Canvas from '../components/Canvas'
import Dock from '../components/Dock'
import EviStack from '../components/EviStack'
import Panels from '../components/Panels'
import EvidenceStoryline from '../components/EvidenceStoryline'
import ErrorBoundary from '../components/ErrorBoundary'

export default function Workspace() {
  const { id } = useParams()
  const nav = useNavigate()
  const open = useProject((s) => s.open)
  const current = useProject((s) => s.current)
  const traceId = useProject((s) => s.traceId)
  const active = useWorkflow((s) => s.active)
  const [flash, setFlash] = useState(false)

  useEffect(() => {
    open(id).catch(() => { nav('/projects', { replace: true }) })  // 项目不存在/已删除 → 静默跳回项目空间
  }, [id, open, nav])

  // 阶段切换时一道转场辉光
  useEffect(() => { setFlash(true); const t = setTimeout(() => setFlash(false), 380); return () => clearTimeout(t) }, [active])

  const stageTitle = STAGES.find((s) => s.id === active)?.title || ''
  const crumb = current
    ? { projectName: current.name, mineralLabel: current.mineral_label, stageTitle }
    : null

  return (
    <>
      <div className={`flash ${flash ? 'on' : ''}`} />
      <TopBar crumb={crumb} traceId={traceId} />
      <Canvas />
      <EviStack />
      <Panels />
      <ErrorBoundary fallback={null}>
        <EvidenceStoryline />
      </ErrorBoundary>
      <div className="hint">
        点底部轨道切换阶段 · 画布随推演 2D→3D 演化
        {['model3d', 'drill'].includes(active) && ' · 拖拽旋转 / 滚轮缩放 / 双击复位'}
      </div>
      <Dock />
    </>
  )
}
