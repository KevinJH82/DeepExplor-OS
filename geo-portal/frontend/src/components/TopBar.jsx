import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dropdown, Popover, Popconfirm, message } from 'antd'
import { useAuth, useProject } from '../store'
import * as api from '../api/portal'

export default function TopBar({ crumb, traceId }) {
  const nav = useNavigate()
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const current = useProject((s) => s.current)
  const switchRun = useProject((s) => s.switchRun)
  const [runs, setRuns] = useState([])

  const isAdmin = ['org_admin', 'platform_admin'].includes(user?.tenant_role)
  const items = [
    { key: 'role', label: `角色:${roleLabel(user?.tenant_role)}`, disabled: true },
    { key: 'tenant', label: `租户:${user?.tenant_name || ''}`, disabled: true },
    { type: 'divider' },
    ...(isAdmin ? [{ key: 'admin', label: '用户管理' }] : []),
    { key: 'logout', label: '退出登录' },
  ]

  const loadRuns = async (open) => {
    if (open && current) {
      try { setRuns(await api.listRuns(current.id)) } catch { setRuns([]) }
    }
  }
  const delRun = async (tid) => {
    try { await api.deleteRun(tid); message.success('已删除运行'); setRuns(await api.listRuns(current.id)) }
    catch (e) { message.error(e?.response?.data?.detail || '删除失败') }
  }

  const runList = (
    <div style={{ width: 270, maxHeight: 320, overflow: 'auto' }}>
      {runs.length === 0 && <div style={{ color: 'var(--mut)', fontSize: 12 }}>暂无运行记录</div>}
      {runs.map((r) => (
        <div key={r.trace_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: '1px solid rgba(40,90,160,.08)' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'monospace', fontSize: 11.5, color: r.trace_id === traceId ? '#0a8aa3' : '#13324d', cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              onClick={() => switchRun(r.trace_id)}>{r.trace_id === traceId ? '● ' : ''}{r.trace_id}</div>
            <div style={{ fontSize: 10.5, color: 'var(--mut)' }}>{r.created_at}</div>
          </div>
          <Popconfirm title="删除该运行?" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => delRun(r.trace_id)}>
            <span style={{ cursor: 'pointer', color: 'var(--er)', fontSize: 12 }}>✕</span>
          </Popconfirm>
        </div>
      ))}
    </div>
  )

  return (
    <div className="top">
      <div className="brand" onClick={() => nav('/projects')}>⛰ DeepExplor OS<small> ◢</small></div>
      {crumb && (
        <div className="crumb">
          {crumb.projectName} · {crumb.mineralLabel} / <b>{crumb.stageTitle}</b>
        </div>
      )}
      {current && (
        <Popover content={runList} title="运行历史" trigger="click" onOpenChange={loadRuns} placement="bottom">
          <div className="tid" style={{ cursor: 'pointer' }}>{traceId ? `trace_id: ${traceId} ▾` : '运行历史 ▾'}</div>
        </Popover>
      )}
      {!current && <div className="tid">项目空间</div>}
      <Dropdown menu={{ items, onClick: ({ key }) => {
        if (key === 'logout') { logout(); nav('/login') }
        else if (key === 'admin') { nav('/admin') }
      } }}>
        <div className="av">{(user?.display || 'U').slice(0, 1)}</div>
      </Dropdown>
    </div>
  )
}

function roleLabel(r) {
  return { platform_admin: '平台管理员', org_admin: '租户管理员', member: '成员' }[r] || r || '-'
}
