import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './styles.css'

const theme = {
  token: {
    colorPrimary: '#0aa2c0',
    colorInfo: '#0aa2c0',
    borderRadius: 9,
    fontFamily: '-apple-system,"PingFang SC","Microsoft YaHei",sans-serif',
  },
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>
)
