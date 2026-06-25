import axios from 'axios'

// access token 只存内存(不落 localStorage,避免 XSS 窃取);
// refresh token 是 HttpOnly cookie,由浏览器随同源请求自动携带(withCredentials)。
let accessToken = null
export function setAccessToken(t) { accessToken = t || null }
export function getAccessToken() { return accessToken }

// 同源经 vite proxy → BFF;withCredentials 让 refresh cookie 随请求发出
const client = axios.create({ baseURL: '', withCredentials: true })

client.interceptors.request.use((cfg) => {
  if (accessToken) cfg.headers.Authorization = `Bearer ${accessToken}`
  return cfg
})

// 单飞:并发 401 只触发一次 refresh
let refreshing = null
function doRefresh() {
  if (!refreshing) {
    refreshing = axios.post('/api/refresh', null, { withCredentials: true })
      .then((r) => { setAccessToken(r.data.token); return r.data })
      .finally(() => { refreshing = null })
  }
  return refreshing
}

function gotoLogin() {
  setAccessToken(null)
  if (location.hash !== '#/login') location.hash = '#/login'
}

client.interceptors.response.use(
  (r) => r,
  async (err) => {
    const { response, config } = err
    const url = config?.url || ''
    // 登录/刷新端点自身的 401 不再尝试刷新,直接交回上层
    if (response && response.status === 401 && config && !config._retried &&
        !url.includes('/api/refresh') && !url.includes('/api/login')) {
      config._retried = true
      try {
        await doRefresh()
        return client(config)          // 用新 access 重放原请求
      } catch {
        gotoLogin()
      }
    } else if (response && response.status === 401 && !url.includes('/api/login')) {
      gotoLogin()
    }
    return Promise.reject(err)
  }
)

export default client
