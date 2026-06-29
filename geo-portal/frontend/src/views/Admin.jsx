import { useEffect, useState } from 'react'
import { Table, Modal, Input, Select, Button, Tag, message, Popconfirm, Segmented, Badge } from 'antd'
import { useAuth, useAdminBadge } from '../store'
import * as api from '../api/portal'
import TopBar from '../components/TopBar'

const ROLE_LABEL = { platform_admin: '平台管理员', org_admin: '租户管理员', member: '成员' }
// org_admin 不能授予 platform_admin(后端 _guard_role 也会拦),按角色裁剪可选项
const roleOptions = (isPlatform) =>
  Object.entries(ROLE_LABEL)
    .filter(([v]) => isPlatform || v !== 'platform_admin')
    .map(([value, label]) => ({ value, label }))

const APP_STATUS = { pending: { c: 'gold', t: '待审核' }, approved: { c: 'green', t: '已通过' }, rejected: { c: 'red', t: '已拒绝' } }

export default function Admin() {
  const me = useAuth((s) => s.user)
  const isPlatform = me?.tenant_role === 'platform_admin'
  const [tab, setTab] = useState('users')

  // —— 用户管理 ——
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ username: '', display: '', role: 'member', password: '' })
  const [busy, setBusy] = useState(false)

  // —— 账号申请 ——
  const [apps, setApps] = useState([])
  const [appsLoading, setAppsLoading] = useState(false)
  const [appStatus, setAppStatus] = useState('pending')
  const pendingCount = useAdminBadge((s) => s.pending)
  const refreshPending = useAdminBadge((s) => s.refresh)
  const [tenants, setTenants] = useState([])
  const [approveApp, setApproveApp] = useState(null)         // 正在审核通过的申请
  const [approveForm, setApproveForm] = useState({ username: '', role: 'member', tenant_id: '' })
  const [approveBusy, setApproveBusy] = useState(false)
  const [rejectApp, setRejectApp] = useState(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectBusy, setRejectBusy] = useState(false)
  const [result, setResult] = useState(null)                 // 通过结果:账号+初始密码

  const load = async () => {
    setLoading(true)
    try { setRows(await api.adminListUsers()) }
    catch (e) { message.error(e?.response?.data?.detail || '加载用户失败') }
    finally { setLoading(false) }
  }
  const loadApps = async () => {
    setAppsLoading(true)
    try { setApps(await api.adminListApplications(appStatus)) }
    catch (e) { message.error(e?.response?.data?.detail || '加载申请失败') }
    finally { setAppsLoading(false) }
  }
  useEffect(() => { load(); refreshPending() }, [])
  useEffect(() => { if (tab === 'apps') loadApps() }, [tab, appStatus])
  useEffect(() => { if (isPlatform) api.adminListTenants().then(setTenants).catch(() => {}) }, [isPlatform])

  // —— 用户操作 ——
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

  // —— 申请审核操作 ——
  const openApprove = (a) => {
    setApproveApp(a)
    setApproveForm({ username: a.desired_username || '', role: 'member', tenant_id: me?.tenant_id || '' })
  }
  const doApprove = async () => {
    if (!approveForm.username.trim()) return message.warning('请填写用户名')
    setApproveBusy(true)
    try {
      const body = { username: approveForm.username.trim(), role: approveForm.role }
      if (isPlatform && approveForm.tenant_id) body.tenant_id = approveForm.tenant_id
      const r = await api.adminApproveApplication(approveApp.id, body)
      setApproveApp(null)
      setResult({ ...r, applicant: approveApp.applicant })
      loadApps(); refreshPending(); load()
    } catch (e) { message.error(e?.response?.data?.detail || '审核失败') }
    finally { setApproveBusy(false) }
  }
  const doReject = async () => {
    setRejectBusy(true)
    try {
      await api.adminRejectApplication(rejectApp.id, rejectReason.trim())
      message.success('已拒绝该申请')
      setRejectApp(null); setRejectReason('')
      loadApps(); refreshPending()
    } catch (e) { message.error(e?.response?.data?.detail || '操作失败') }
    finally { setRejectBusy(false) }
  }

  // —— 结果:复制 / 邮件 ——
  const loginUrl = `${window.location.origin}/#/login`
  const credText = (r) =>
    `深层探索智能成矿系统 — 账号信息\n登录地址:${loginUrl}\n用户名:${r.user?.username}\n初始密码:${r.initial_password}\n\n请首次登录后尽快修改密码。`
  const copyCred = async (r) => {
    try { await navigator.clipboard.writeText(credText(r)); message.success('账号信息已复制') }
    catch { message.error('复制失败,请手动选择文本') }
  }
  const mailtoHref = (r) => {
    const subject = '您的深层探索智能成矿系统账号已开通'
    return `mailto:${r.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(credText(r))}`
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

  const appColumns = [
    { title: '申请人', dataIndex: 'applicant', render: (v) => v || '-' },
    { title: '单位', dataIndex: 'org_name', render: (v) => v || '-' },
    { title: '邮箱', dataIndex: 'email' },
    { title: '电话', dataIndex: 'phone', render: (v) => v || '-' },
    { title: '期望用户名', dataIndex: 'desired_username', render: (v) => v || '-' },
    { title: '用途', dataIndex: 'purpose', ellipsis: true, render: (v) => v || '-' },
    { title: '状态', dataIndex: 'status', render: (s, a) => (
      <span>
        <Tag color={APP_STATUS[s]?.c}>{APP_STATUS[s]?.t || s}</Tag>
        {s === 'rejected' && a.reason && <div style={{ fontSize: 11, color: 'var(--mut)' }}>{a.reason}</div>}
      </span>
    ) },
    { title: '提交时间', dataIndex: 'created_at', render: (v) => v ? v.replace('T', ' ').slice(0, 19) : '-' },
    { title: '操作', render: (_, a) => (
      a.status === 'pending' ? (
        <span style={{ whiteSpace: 'nowrap' }}>
          <Button size="small" type="primary" onClick={() => openApprove(a)}>通过</Button>
          <Button size="small" danger style={{ marginLeft: 8 }}
            onClick={() => { setRejectApp(a); setRejectReason('') }}>拒绝</Button>
        </span>
      ) : <span style={{ color: 'var(--mut)' }}>—</span>
    ) },
  ]

  return (
    <>
      <TopBar />
      <div className="center-wrap">
        <div style={{ maxWidth: 1100, margin: '0 auto 18px', display: 'flex', alignItems: 'center', gap: 16 }}>
          <h2 style={{ color: '#13324d', margin: 0 }}>用户管理</h2>
          <Segmented value={tab} onChange={setTab}
            options={[
              { label: '用户列表', value: 'users' },
              { label: <Badge count={pendingCount} size="small" offset={[10, -2]}>账号申请</Badge>, value: 'apps' },
            ]} />
          <span style={{ marginLeft: 4, color: 'var(--mut)', fontSize: 12 }}>租户:{me?.tenant_name || ''}</span>
          {tab === 'users'
            ? <Button type="primary" style={{ marginLeft: 'auto' }} onClick={() => setOpen(true)}>+ 新建用户</Button>
            : <Select size="small" style={{ marginLeft: 'auto', width: 120 }} value={appStatus} onChange={setAppStatus}
                options={[{ value: 'pending', label: '待审核' }, { value: 'all', label: '全部' }]} />}
        </div>

        <div className="glass" style={{ maxWidth: 1100, margin: '0 auto', padding: 12 }}>
          {tab === 'users'
            ? <Table rowKey="id" size="middle" loading={loading} columns={columns} dataSource={rows} pagination={false} />
            : <Table rowKey="id" size="middle" loading={appsLoading} columns={appColumns} dataSource={apps} pagination={false} />}
        </div>
      </div>

      {/* 新建用户 */}
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

      {/* 审核通过 */}
      <Modal title="审核通过 · 创建账号" open={!!approveApp} onOk={doApprove}
        onCancel={() => setApproveApp(null)} okText="创建账号" confirmLoading={approveBusy}>
        {approveApp && (
          <div style={{ background: 'rgba(40,90,160,.06)', borderRadius: 8, padding: '10px 12px', margin: '6px 0 14px', fontSize: 13 }}>
            申请人:<b>{approveApp.applicant || '-'}</b> · {approveApp.org_name || '无单位'}<br />
            邮箱:{approveApp.email}
          </div>
        )}
        <div style={{ margin: '4px 0 6px' }}>用户名</div>
        <Input value={approveForm.username} onChange={(e) => setApproveForm({ ...approveForm, username: e.target.value })}
          placeholder="登录用用户名(默认取申请人期望)" />
        <div style={{ margin: '14px 0 6px' }}>角色</div>
        <Select value={approveForm.role} onChange={(r) => setApproveForm({ ...approveForm, role: r })}
          options={roleOptions(isPlatform)} style={{ width: '100%' }} />
        {isPlatform && (
          <>
            <div style={{ margin: '14px 0 6px' }}>归属租户</div>
            <Select value={approveForm.tenant_id} onChange={(t) => setApproveForm({ ...approveForm, tenant_id: t })}
              options={tenants.map((t) => ({ value: t.id, label: t.name || t.id }))} style={{ width: '100%' }} />
          </>
        )}
        <div style={{ marginTop: 14, fontSize: 12, color: 'var(--mut)' }}>
          初始密码将由系统自动生成,创建后展示一次,请及时复制并发送给申请人。
        </div>
      </Modal>

      {/* 审核拒绝 */}
      <Modal title="拒绝该申请" open={!!rejectApp} onOk={doReject}
        onCancel={() => setRejectApp(null)} okText="确认拒绝" okButtonProps={{ danger: true }} confirmLoading={rejectBusy}>
        <div style={{ margin: '6px 0 6px' }}>拒绝原因(可选,将记录在案)</div>
        <Input.TextArea value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} rows={3} maxLength={300} placeholder="如:信息不全 / 非目标客户" />
      </Modal>

      {/* 通过结果:账号 + 初始密码(仅此一次) */}
      <Modal title="账号已创建" open={!!result} onCancel={() => setResult(null)} footer={null}>
        {result && (
          <div>
            <div style={{ background: '#fff7e6', border: '1px solid #ffd591', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#ad6800', marginBottom: 14 }}>
              ⚠ 初始密码仅显示这一次,关闭后无法再次查看。请立即复制或通过邮件发送给申请人。
            </div>
            <div style={{ lineHeight: 2, fontSize: 14 }}>
              用户名:<b>{result.user?.username}</b><br />
              初始密码:<b style={{ fontFamily: 'monospace' }}>{result.initial_password}</b><br />
              邮箱:{result.email || '-'}
              {result.email_set === false && <Tag color="orange" style={{ marginLeft: 8 }}>邮箱已被占用,未绑定</Tag>}
            </div>
            <div style={{ marginTop: 18, display: 'flex', gap: 10 }}>
              <Button onClick={() => copyCred(result)}>复制账号信息</Button>
              <Button type="primary" href={mailtoHref(result)}>用邮件发送</Button>
              <Button style={{ marginLeft: 'auto' }} onClick={() => setResult(null)}>完成</Button>
            </div>
          </div>
        )}
      </Modal>
    </>
  )
}
