import { Progress, Tag } from 'antd'
import useStore from '../store/analysisStore'

const STATUS_MAP = {
  idle: { color: 'default', text: '就绪' },
  queued: { color: 'blue', text: '排队中' },
  running: { color: 'processing', text: '分析中' },
  completed: { color: 'success', text: '完成' },
  failed: { color: 'error', text: '失败' },
}

export default function TaskStatus() {
  const taskStatus = useStore(s => s.taskStatus)
  const taskProgress = useStore(s => s.taskProgress)
  const taskStep = useStore(s => s.taskStep)
  const taskError = useStore(s => s.taskError)

  const statusInfo = STATUS_MAP[taskStatus] || STATUS_MAP.idle

  return (
    <>
      <Tag color={statusInfo.color}>{statusInfo.text}</Tag>
      {taskStatus === 'running' && (
        <>
          <Progress
            percent={taskProgress}
            size="small"
            style={{ width: 200, margin: 0 }}
            strokeColor="#1a73e8"
          />
          <span style={{ fontSize: 12, color: '#666' }}>{taskStep}</span>
        </>
      )}
      {taskStatus === 'failed' && (
        <span style={{ fontSize: 12, color: '#ff4d4f' }}>{taskError}</span>
      )}
      {taskStatus === 'completed' && (
        <span style={{ fontSize: 12, color: '#52c41a' }}>分析完成，可在结果面板查看</span>
      )}
    </>
  )
}
