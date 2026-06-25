import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendUrl = process.env.VITE_BACKEND_URL || 'http://localhost:8001'
const backendWs = process.env.VITE_BACKEND_WS || 'ws://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': backendUrl,
      '/tiles': backendUrl,
      '/ws': {
        target: backendWs,
        ws: true,
      },
    },
  },
})
