import { useState } from 'react'
import { Upload, Tag, Popconfirm, message } from 'antd'
import { useProject } from '../store'
import { MANUAL_EVIDENCE_TYPES, NMR_RESERVED } from '../lib/stages'
import * as api from '../api/portal'

// 数据准备阶段:手工提交证据(预留入口)。物探/化探/历史钻孔 + 靶向超弱核磁。
// 本期仅上传留存 + 标注"已接收·待融合";融合算法待未来接入。
export default function ManualEvidence() {
  const current = useProject((s) => s.current)
  const items = useProject((s) => s.manualEvidence)
  const reload = useProject((s) => s.loadManualEvidence)
  const [busy, setBusy] = useState('')

  if (!current) return null
  const byCat = (k) => (items || []).filter((m) => m.category === k)

  const doUpload = async (category, file) => {
    setBusy(category)
    try {
      await api.uploadManualEvidence(current.id, category, file)
      message.success('已提交,将纳入证据链(待融合)')
      await reload(current.id)
    } catch (e) { message.error(e?.response?.data?.detail || '上传失败') }
    finally { setBusy('') }
  }
  const doDelete = async (meId) => {
    try { await api.deleteManualEvidence(current.id, meId); await reload(current.id); message.success('已删除') }
    catch (e) { message.error(e?.response?.data?.detail || '删除失败') }
  }
  const doDownload = async (m) => {
    try {
      const blob = await api.downloadManualEvidence(current.id, m.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = m.filename || 'evidence'
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { message.error('下载失败') }
  }

  const renderRow = (type, reserved) => {
    const files = byCat(type.key)
    return (
      <div key={type.key} className={`me-row${reserved ? ' reserved' : ''}`}>
        <div className="me-row-head">
          <span className="me-label">{type.label}{reserved && <em className="me-rsv">预留</em>}</span>
          <Upload showUploadList={false} multiple={false}
            beforeUpload={(f) => { doUpload(type.key, f); return false }} disabled={busy === type.key}>
            <a className="me-up">{busy === type.key ? '上传中…' : '⬆ 上传'}</a>
          </Upload>
        </div>
        {reserved && !files.length && (
          <div className="me-hint">未来作为强支撑证据接入,结论将融入证据链</div>
        )}
        {files.map((m) => (
          <div key={m.id} className="me-file">
            <span className="me-fn" title={m.filename}>{m.filename}</span>
            <Tag color="gold" style={{ marginInlineEnd: 0 }}>已接收·待融合</Tag>
            <a onClick={() => doDownload(m)}>下载</a>
            <Popconfirm title="删除该证据?" okText="删除" cancelText="取消"
              okButtonProps={{ danger: true }} onConfirm={() => doDelete(m.id)}>
              <a className="me-del">删除</a>
            </Popconfirm>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="manual-ev">
      <div className="me-note">手工提交的物探/化探/历史钻孔等证据将纳入证据链融合分析(融合能力建设中)。</div>
      {MANUAL_EVIDENCE_TYPES.map((t) => renderRow(t, false))}
      {renderRow(NMR_RESERVED, true)}
    </div>
  )
}
