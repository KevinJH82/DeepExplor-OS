import { useState } from 'react'
import { Tabs } from 'antd'
import UploadPanel from './UploadPanel'
import ParameterPanel from './ParameterPanel'
import ResultPanel from './ResultPanel'
import MapView from './MapView'
import TaskStatus from './TaskStatus'
import HelpPanel from './HelpPanel'
import useStore from '../store/analysisStore'

export default function Layout() {
  const taskStatus = useStore(s => s.taskStatus)
  const [activeTab, setActiveTab] = useState('upload')

  return (
    <div className="app-layout">
      <div className="sidebar">
        <div className="sidebar-header">
          <h1>七个慢变量分析系统</h1>
          <p>基于尖点突变理论的深部资源预测</p>
        </div>
        <div className="sidebar-content">
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            size="small"
            items={[
              {
                key: 'upload',
                label: '📤 数据上传',
                children: <UploadPanel />,
              },
              {
                key: 'params',
                label: '⚙️ 参数设置',
                children: <ParameterPanel />,
                disabled: !['running', 'completed'].includes(taskStatus),
              },
              {
                key: 'results',
                label: '📊 分析结果',
                children: <ResultPanel />,
                disabled: taskStatus !== 'completed',
              },
              {
                key: 'help',
                label: '📖 使用说明',
                children: <HelpPanel />,
              },
            ]}
          />
        </div>
      </div>

      <div className="main-area">
        <div className="map-container">
          <MapView />
        </div>
        <div className="status-bar">
          <TaskStatus />
        </div>
      </div>
    </div>
  )
}
