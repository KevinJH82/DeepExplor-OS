import { useState } from 'react'
import { Modal, Form, Input, message } from 'antd'
import * as api from '../api/portal'

// 登录页「申请账号」弹窗:匿名提交开户申请,等待管理员审核开通。
export default function ApplyAccountModal({ open, onClose }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setBusy(true)
    try {
      await api.submitAccountApplication(values)
      message.success('申请已提交,管理员审核开通后会通过邮件通知您')
      form.resetFields()
      onClose?.()
    } catch (e) {
      message.error(e?.response?.data?.detail || '提交失败,请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="申请账号"
      open={open}
      onOk={submit}
      onCancel={() => { form.resetFields(); onClose?.() }}
      okText="提交申请"
      cancelText="取消"
      confirmLoading={busy}
      destroyOnClose
      maskClosable={false}
    >
      <div style={{ color: '#647c9d', fontSize: 13, margin: '4px 0 16px' }}>
        填写以下信息提交开户申请,管理员审核通过后将通过邮件把账号信息发给您。
      </div>
      <Form form={form} layout="vertical" requiredMark={false} preserve={false}>
        <Form.Item name="applicant" label="申请人姓名"
          rules={[{ required: true, message: '请填写申请人姓名' }]}>
          <Input placeholder="如:王工" maxLength={60} />
        </Form.Item>
        <Form.Item name="org_name" label="单位 / 组织">
          <Input placeholder="如:某地勘院(可选)" maxLength={120} />
        </Form.Item>
        <Form.Item name="email" label="邮箱(用于接收账号信息)"
          rules={[
            { required: true, message: '请填写邮箱' },
            { type: 'email', message: '邮箱格式不正确' },
          ]}>
          <Input placeholder="name@example.com" maxLength={200} />
        </Form.Item>
        <Form.Item name="phone" label="联系电话">
          <Input placeholder="可选" maxLength={40} />
        </Form.Item>
        <Form.Item name="desired_username" label="期望用户名">
          <Input placeholder="可选,最终以管理员分配为准" maxLength={60} />
        </Form.Item>
        <Form.Item name="purpose" label="申请用途">
          <Input.TextArea placeholder="简述用途,便于管理员审核(可选)" rows={3} maxLength={500} showCount />
        </Form.Item>
      </Form>
    </Modal>
  )
}
