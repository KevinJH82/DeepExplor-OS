import { Descriptions, Button, Card, Divider, Typography } from 'antd'
import * as api from '../api/client'
import useStore from '../store/analysisStore'

const { Text, Title } = Typography

export default function ResultPanel() {
  const results = useStore(s => s.results)
  const taskId = useStore(s => s.taskId)

  if (!results) {
    return <Text type="secondary">尚无分析结果，请先上传数据并运行分析。</Text>
  }

  return (
    <div>
      <div className="section-title">分析结果概览</div>

      {results.target_area_km2 != null && (
        <Card size="small" style={{ marginBottom: 12 }}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="靶区总面积">
              <Text strong>{results.target_area_km2} km²</Text>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      <Divider style={{ margin: '8px 0' }}>各慢变量统计</Divider>

      {results.layers?.map(layer => (
        <Card
          key={layer.name}
          size="small"
          title={<Text style={{ fontSize: 13 }}>{layer.title}</Text>}
          style={{ marginBottom: 8 }}
          extra={
            taskId && (
              <Button
                size="small"
                onClick={() => {
                  const link = document.createElement('a')
                  link.href = api.getExportUrl(taskId, layer.name)
                  link.download = `${layer.name}.tif`
                  link.click()
                }}
              >
                下载 GeoTIFF
              </Button>
            )
          }
        >
          {layer.stats && layer.stats.valid_pixels > 0 ? (
            <div style={{ fontSize: 12 }}>
              <div className="stat-row">
                <span className="stat-label">最小值</span>
                <span className="stat-value">{layer.stats.min?.toFixed(4)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">最大值</span>
                <span className="stat-value">{layer.stats.max?.toFixed(4)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">均值</span>
                <span className="stat-value">{layer.stats.mean?.toFixed(4)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">标准差</span>
                <span className="stat-value">{layer.stats.std?.toFixed(4)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">有效像素</span>
                <span className="stat-value">{layer.stats.valid_pixels?.toLocaleString()}</span>
              </div>
            </div>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>无有效数据</Text>
          )}
        </Card>
      ))}

      <Divider style={{ margin: '8px 0' }}>使用参数</Divider>
      <div style={{ fontSize: 12, color: '#666' }}>
        <div>Δ阈值: {results.params_used?.delta_threshold}</div>
        <div>高斯Sigma: {results.params_used?.sigma}</div>
      </div>
    </div>
  )
}
