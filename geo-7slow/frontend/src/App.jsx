import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import Layout from './components/Layout'
import './App.css'

function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{
      token: { colorPrimary: '#1a73e8', borderRadius: 6 },
    }}>
      <Layout />
    </ConfigProvider>
  )
}

export default App
