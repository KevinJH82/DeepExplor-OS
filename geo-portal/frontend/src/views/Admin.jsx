import { useEffect, useState } from 'react'
import { Table, Modal, Input, Select, Button, Tag, message, Popconfirm } from 'antd'
import { useAuth } from '../store'
import * as api from '../api/portal'
import TopBar from '../components/TopBar'

const ROLE_LABEL = { platform_admin: '平台管理员', org_admin: '租户管理员', member: '成员' }
// org_admin 不能授予 platform_admin(后端 _guard_role 也会拦),按角色裁剪可选项
const roleOptions = (isPlatform) =>
  Object.entries(ROLE_LABEL)
    .filter(([v]) => isPlatform || v !== 'platform_admin')
    .map(([value, label]) => ({ value, label }))

export default function Admin() {
  const me = useAuth((s) => s.user)
  const isPlatform = me?.tenant_role === 'platform_admin'
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ username: '', display: '', role: 'member', password: '' })
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setRows(await api.adminListUsers()) }
    catch (e) { message.error(e?.response?.data?.detail || '加载用户失败') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const setRole = async (u, role) => {
    try { await api.adminSetRole(u.id, role); message.success('已更新角色'); load() }
    catch (e) { message.error(e?.response?.data?.detail || '更新失败') }
  }
  const toggleStatus = async (u) => {
    try {
      if (u.status === 'disabled') { await api.adminEnableUser(u.id); message.success('已启用') }
      else { await api.adminDisableUser(u.id); message.success('已停用(并踢下线)') }
      load()
    } catch (e) { message.error(e?.response?.data?.detail || '操作失败') }
  }
  const create = async () => {
    if (!form.username.trim()) return message.warning('请输入用户名')
    if (form.password.length < 8) return message.warning('口令至少 8 位')
    setBusy(true)
    try {
      await api.adminCreateUser(form)
      message.success('已创建用户')
      setOpen(false); setForm({ username: '', display: '', role: 'member', password: '' })
      load()
    } catch (e) { message.error(e?.response?.data?.detail || '创建失败') }
    finally { setBusy(false) }
  }

  const columns = [
    { title: '用户名', dataIndex: 'username', render: (v, u) => (
      <span>{v}{u.id === me?.id && <Tag style={{ marginLeft: 6 }}>我</Tag>}</span>
    ) },
    { title: '显示名', dataIndex: 'display', render: (v) => v || '-' },
    { title: '角色', dataIndex: 'tenant_role', render: (role, u) => (
      <Select size="small" value={role} style={{ width: 130 }}
        disabled={u.id === me?.id}
        options={roleOptions(isPlatform)}
        onChange={(r) => setRole(u, r)} />
    ) },
    { title: '状态', dataIndex: 'status', render: (s) => (
      <Tag color={s === 'disabled' ? 'red' : 'green'}>{s === 'disabled' ? '已停用' : '正常'}</Tag>
    ) },
    { title: '最近登录', dataIndex: 'last_login_at', render: (v) => v ? v.replace('T', ' ').slice(0, 19) : '从未' },
    { title: '操作', render: (_, u) => (
      u.id === me?.id ? <span style={{ color: 'var(--mut)' }}>—</span> : (
        <Popconfirm
          title={u.status === 'disabled' ? '启用该用户?' : '停用该用户?'}
          description={u.status === 'disabled' ? '' : '该用户现有会话会被立即吊销'}
          okText={u.status === 'disabled' ? '启用' : '停用'} cancelText="取消"
          okButtonProps={{ danger: u.status !== 'disabled' }}
          onConfirm={() => toggleStatus(u)}>
          <Button size="small" danger={u.status !== 'disabled'}>
            {u.status === 'disabled' ? '启用' : '停用'}
          </Button>
        </Popconfirm>
      )
    ) },
  ]

  return (
    <>
      <TopBar />
      <div className="center-wrap">
        <div style={{ maxWidth: 1000, margin: '0 auto 18px', display: 'flex', alignItems: 'center' }}>
          <h2 style={{ color: '#13324d', margin: 0 }}>用户管理</h2>
          <span style={{ marginLeft: 12, color: 'var(--mut)', fontSize: 12 }}>租户:{me?.tenant_name || ''}</span>
          <Button type="primary" style={{ marginLeft: 'auto' }} onClick={() => setOpen(true)}>+ 新建用户</Button>
        </div>
        <div className="glass" style={{ maxWidth: 1000, margin: '0 auto', padding: 12 }}>
          <Table rowKey="id" size="middle" loading={loading} columns={columns} dataSource={rows} pagination={false} />
        </div>
      </div>

      <Modal title="新建用户" open={open} onOk={create} onCancel={() => setOpen(false)}
        okText="创建" confirmLoading={busy}>
        <div style={{ margin: '12px 0 6px' }}>用户名</div>
        <Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="登录用用户名" />
        <div style={{ margin: '14px 0 6px' }}>显示名</div>
        <Input value={form.display} onChange={(e) => setForm({ ...form, display: e.target.value })} placeholder="如:王工(可选)" />
        <div style={{ margin: '14px 0 6px' }}>角色</div>
        <Select value={form.role} onChange={(r) => setForm({ ...form, role: r })}
          options={roleOptions(isPlatform)} style={{ width: '100%' }} />
        <div style={{ margin: '14px 0 6px' }}>初始口令(≥8 位)</div>
        <Input.Password value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="至少 8 位" />
      </Modal>
    </>
  )
}
