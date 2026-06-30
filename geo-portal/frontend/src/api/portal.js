import client from './client'

// ── 认证 ──
export const login = (username, password) =>
  client.post('/api/login', { username, password }).then((r) => r.data)
export const refresh = () => client.post('/api/refresh').then((r) => r.data)
export const logout = () => client.post('/api/logout').then((r) => r.data)
export const getMe = () => client.get('/api/me').then((r) => r.data)
export const listServices = () => client.get('/api/services').then((r) => r.data)

// ── 管理(org_admin / platform_admin)──
export const adminListUsers = () => client.get('/api/admin/users').then((r) => r.data)
export const adminCreateUser = (body) => client.post('/api/admin/users', body).then((r) => r.data)
export const adminSetRole = (userId, role) =>
  client.patch(`/api/admin/users/${userId}/role`, { role }).then((r) => r.data)
export const adminDisableUser = (userId) =>
  client.post(`/api/admin/users/${userId}/disable`).then((r) => r.data)
export const adminEnableUser = (userId) =>
  client.post(`/api/admin/users/${userId}/enable`).then((r) => r.data)

// ── 账号申请(开户审核流)──
// 公开提交:登录页无 token,client 不会带 Authorization,即匿名请求
export const submitAccountApplication = (body) =>
  client.post('/api/public/account-applications', body).then((r) => r.data)
export const adminListApplications = (status = 'pending') =>
  client.get('/api/admin/applications', { params: { status } }).then((r) => r.data)
export const adminApproveApplication = (appId, body) =>
  client.post(`/api/admin/applications/${appId}/approve`, body).then((r) => r.data)
export const adminRejectApplication = (appId, reason) =>
  client.post(`/api/admin/applications/${appId}/reject`, { reason }).then((r) => r.data)
export const adminListTenants = () => client.get('/api/admin/tenants').then((r) => r.data)

// ── 项目 ──
export const listProjects = () => client.get('/api/projects').then((r) => r.data)
export const createProject = (body) => client.post('/api/projects', body).then((r) => r.data)
export const getProject = (id) => client.get(`/api/projects/${id}`).then((r) => r.data)
export const existingModel3d = (id) => client.get(`/api/projects/${id}/model3d-existing`).then((r) => r.data)
export const existingDrill = (id) => client.get(`/api/projects/${id}/drill-existing`).then((r) => r.data)
export const downloadDrillData = (id) => client.get(`/api/projects/${id}/drill-data`, { responseType: 'blob' }).then((r) => r.data)
export const deleteProject = (id) => client.delete(`/api/projects/${id}`).then((r) => r.data)
export const deleteRun = (traceId) => client.delete(`/api/runs/${traceId}`).then((r) => r.data)

// ── 运行(trace_id 主线)──
export const createRun = (projectId, plan, traceId) =>
  client.post(`/api/projects/${projectId}/runs`, { plan, trace_id: traceId }).then((r) => r.data)
export const getRun = (traceId) => client.get(`/api/runs/${traceId}`).then((r) => r.data)
export const getDatacolleEvidence = (traceId) =>
  client.get(`/api/runs/${traceId}/datacolle-evidence`).then((r) => r.data)
export const patchStage = (traceId, stage, patch) =>
  client.patch(`/api/runs/${traceId}/stage`, { stage, patch }).then((r) => r.data)
export const listRuns = (projectId) =>
  client.get(`/api/projects/${projectId}/runs`).then((r) => r.data)
export const makeEvidencePlan = (traceId) =>
  client.post(`/api/runs/${traceId}/evidence-plan`).then((r) => r.data)
// 证据链综合研判(LLM):前端把本 run 真实数据 POST 过去,BFF 调 claude 出研判
export const runSynthesis = (traceId, facts, refresh = false) =>
  client.post(`/api/runs/${traceId}/synthesis`, { facts, refresh }).then((r) => r.data)
export const patchEvidencePlan = (traceId, evidencePlan) =>
  client.patch(`/api/runs/${traceId}/evidence-plan`, { evidence_plan: evidencePlan }).then((r) => r.data)
export const executeEvidencePlan = (traceId) =>
  client.post(`/api/runs/${traceId}/evidence-plan/execute`).then((r) => r.data)

// ── 真实服务调用(BFF 承载 KML 转发 + 状态归一)──
export const planReal = (projectId) =>
  client.post(`/api/projects/${projectId}/plan`).then((r) => r.data)
export const startSvc = (traceId, service, params = {}) =>
  client.post(`/api/runs/${traceId}/start`, { service, params }).then((r) => r.data)
export const svcStatus = (traceId, service, taskId) =>
  client.get(`/api/runs/${traceId}/svcstatus`, { params: { service, task_id: taskId } }).then((r) => r.data)
export const downloadAdapterReport = (taskId, fmt = 'docx') =>
  client.get(`/api/adapter-report/${taskId}`, { params: { fmt }, responseType: 'blob' }).then((r) => r.data)

export const uploadKml = (projectId, file) => {
  const fd = new FormData()
  fd.append('file', file)
  return client.post(`/api/projects/${projectId}/kml`, fd).then((r) => r.data)
}
// 经济参数表上传(新版报告价值评估章用,CSV/JSON)
export const uploadEconParams = (projectId, file) => {
  const fd = new FormData()
  fd.append('file', file)
  return client.post(`/api/projects/${projectId}/econ-params`, fd).then((r) => r.data)
}
// ── 手工提交证据(预留入口:物探/化探/历史钻孔/靶向超弱核磁)──
export const listManualEvidence = (projectId) =>
  client.get(`/api/projects/${projectId}/manual-evidence`).then((r) => r.data)
export const uploadManualEvidence = (projectId, category, file, note = '') => {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('category', category)
  if (note) fd.append('note', note)
  return client.post(`/api/projects/${projectId}/manual-evidence`, fd).then((r) => r.data)
}
export const deleteManualEvidence = (projectId, meId) =>
  client.delete(`/api/projects/${projectId}/manual-evidence/${meId}`).then((r) => r.data)
export const downloadManualEvidence = (projectId, meId) =>
  client.get(`/api/projects/${projectId}/manual-evidence/${meId}/file`, { responseType: 'blob' }).then((r) => r.data)

export const listDeliveries = () => client.get('/api/deliveries').then((r) => r.data)
export const bindDelivery = (projectId, deliveryId) =>
  client.post(`/api/projects/${projectId}/delivery`, { delivery_id: deliveryId }).then((r) => r.data)

// ── 经 BFF 反代访问某微服务(独立模式/拉产物用)──
export const svc = (service, path, opts = {}) =>
  client.request({ url: `/svc/${service}/${path}`, ...opts }).then((r) => r.data)

// model3d 真实产物
export const model3dTargets = (taskId) => svc('model3d', `api/result/${taskId}/targets_3d.json`)
export const model3dSlices = (taskId) => svc('model3d', `api/slices/${taskId}`)
export const model3dStatusFull = (taskId) => svc('model3d', `api/status/${taskId}`)
export const model3dMeta = (taskId) => svc('model3d', `api/result/${taskId}/metadata.json`)

// geo-drill 真实钻孔
export const drillHoles = (taskId) => svc('drill', `api/result/${taskId}/planned_holes.geojson`)
export const drillFeedback = (taskId) => svc('drill', `api/result/${taskId}/drill_feedback.geojson`)

// ── ROI 3D 卫星地形(中间 3D 图)──
// 高程网格 JSON（Copernicus DEM）。失败/无瓦片时 BFF 返回 {flat:true,...}
export const terrain = (projectId, size = 128) =>
  client.get('/api/terrain', { params: { project_id: projectId, size } }).then((r) => r.data)
// 卫星底图 PNG → 经 axios(带 token) 取 blob → objectURL（TextureLoader 不走拦截器,不能直接给 URL）
export const basemapObjectUrl = (projectId) =>
  client.get('/api/basemap', { params: { project_id: projectId }, responseType: 'blob' })
    .then((r) => URL.createObjectURL(r.data))
