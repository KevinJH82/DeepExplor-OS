import { useState } from 'react'
import { Input, Button, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../store'
import ApplyAccountModal from '../components/ApplyAccountModal'

export default function Login() {
  const nav = useNavigate()
  const doLogin = useAuth((s) => s.login)
  const [u, setU] = useState('admin')
  const [p, setP] = useState('admin')
  const [loading, setLoading] = useState(false)
  const [applyOpen, setApplyOpen] = useState(false)

  const submit = async () => {
    setLoading(true)
    try { await doLogin(u, p); nav('/projects') }
    catch (e) {
      if (e?.response?.status === 401) message.error('用户名或密码错误')
      else message.error('登录服务不可用,请确认后端 BFF 已启动')
    }
    finally { setLoading(false) }
  }

  return (
    <div className="login-split">
      {/* 左:卫星找矿配图 + 标语 */}
      <div className="login-hero">
        <div className="login-hero-cap">
          <h1>深层探索<br />智能找矿指挥中心</h1>
          <p>卫星遥感 → 多源证据融合 → 三维成矿预测 → AI 布孔决策</p>
        </div>
      </div>

      {/* 右:登录 */}
      <div className="login-panel">
        <div className="login-form">
          <div className="brand">⛰ DeepExplor OS<small> ◢</small></div>
          <h2>欢迎回来</h2>
          <div className="sub">登录以进入智能成矿预测工作台</div>
          <label className="fld-lab">用户名</label>
          <Input size="large" placeholder="请输入用户名" value={u}
            onChange={(e) => setU(e.target.value)} style={{ marginBottom: 14 }} />
          <label className="fld-lab">密码</label>
          <Input.Password size="large" placeholder="请输入密码" value={p}
            onChange={(e) => setP(e.target.value)} onPressEnter={submit} style={{ marginBottom: 22 }} />
          <Button type="primary" size="large" block loading={loading} onClick={submit}
            className="login-btn">登 录</Button>
          <div className="login-apply">
            还没有账号?
            <a onClick={() => setApplyOpen(true)}>申请账号</a>
          </div>
          <div className="login-foot">DeepExplor · 深层探索智能系统 © 2026</div>
        </div>
      </div>

      <ApplyAccountModal open={applyOpen} onClose={() => setApplyOpen(false)} />
    </div>
  )
}
