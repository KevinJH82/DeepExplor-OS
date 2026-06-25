import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端经此代理同源访问 BFF(消除 CORS / 隐藏端口)
const BFF = process.env.BFF_URL || 'http://127.0.0.1:8100'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BFF, changeOrigin: true },
      '/svc': { target: BFF, changeOrigin: true },
    },
  },
})
