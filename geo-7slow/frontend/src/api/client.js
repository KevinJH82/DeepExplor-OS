import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export async function getSlots() {
  const res = await api.get('/slots')
  return res.data.slots
}

export async function uploadFiles(fileMap) {
  const form = new FormData()
  for (const [slot, file] of Object.entries(fileMap)) {
    if (file) form.append(slot, file)
  }
  const res = await api.post('/upload', form)
  return res.data
}

export async function startAnalysis(uploadId, params = {}) {
  const res = await api.post('/analyze', { upload_id: uploadId, params })
  return res.data
}

export async function getTaskStatus(taskId) {
  const res = await api.get(`/tasks/${taskId}`)
  return res.data
}

export async function getTaskResults(taskId) {
  const res = await api.get(`/tasks/${taskId}/results`)
  return res.data
}

export function getTileUrl(taskId, layerName) {
  return `/tiles/${taskId}/${layerName}/{z}/{x}/{y}.png`
}

export function getExportUrl(taskId, layerName) {
  return `/api/export/${taskId}/${layerName}`
}

export function connectTaskWs(taskId, onMessage) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/tasks/${taskId}`)
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      onMessage(msg)
      if (msg.status === 'completed' || msg.status === 'failed') {
        ws.close()
      }
    } catch {}
  }
  return ws
}

export function disconnectTaskWs(ws) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close()
  }
}

export async function uploadZip(file, existingId) {
  const form = new FormData()
  form.append('zipfile_upload', file)
  const url = existingId ? `/upload/zip?existing_id=${existingId}` : '/upload/zip'
  const res = await api.post(url, form)
  return res.data
}

export async function supplementFile(uploadId, slot, file) {
  const form = new FormData()
  form.append('file', file)
  const res = await api.post(`/upload/${uploadId}/supplement?slot=${slot}`, form)
  return res.data
}

export async function getUploadStatus(uploadId) {
  const res = await api.get(`/upload/${uploadId}/status`)
  return res.data
}

export async function uploadBasemap(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await api.post('/basemap', form)
  return res.data
}

// ─── 矿床类型权重预设 ──────────────────────────────
export async function listDepositPresets() {
  const res = await api.get('/deposit-presets')
  return res.data
}

// ─── 交付库自动获取 ──────────────────────────────
export async function listDeliveryProjects() {
  const res = await api.get('/delivery/projects')
  return res.data
}

export async function prepareFromDelivery({ project, file }) {
  const form = new FormData()
  if (file) form.append('file', file)
  if (project) form.append('project', project)
  const res = await api.post('/delivery/prepare', form)
  return res.data
}
