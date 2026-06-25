import { useState, useRef, useEffect } from 'react'
import { Slider, InputNumber, Button, message, Divider, Typography, Select } from 'antd'
import * as api from '../api/client'
import useStore from '../store/analysisStore'

const { Text } = Typography

const WEIGHT_LABELS = {
  stress: '① 地应力权重',
  redox: '② 氧逸度权重',
  fluid: '③ 流体超压权重',
  fault: '④ 断裂活动性权重',
  chem: '⑤ 化学势权重',
  temp_drive: '⑦ 温度驱动力权重',
}

export default function ParameterPanel() {
  const taskId = useStore(s => s.taskId)
  const uploadId = useStore(s => s.uploadId)
  const updateTask = useStore(s => s.updateTask)
  const setResults = useStore(s => s.setResults)
  const results = useStore(s => s.results)
  const geologicContext = useStore(s => s.geologicContext)
  const storeMineral = useStore(s => s.selectedMineral)
  const storeDepositType = useStore(s => s.selectedDepositType)
  const setStoreMineral = useStore(s => s.setSelectedMineral)
  const setStoreDepositType = useStore(s => s.setSelectedDepositType)

  const defaults = results?.params_used?.weights || {
    stress: 0.25, redox: 0.15, fluid: 0.20,
    fault: 0.20, chem: 0.15, temp_drive: 0.05,
    cap_rock: 0.50, temp_resist: 0.50,
  }

  const [weights, setWeights] = useState(defaults)
  const [threshold, setThreshold] = useState(results?.params_used?.delta_threshold || -5000)
  const [sigma, setSigma] = useState(results?.params_used?.sigma || 3)
  const [commodities, setCommodities] = useState([])
  const [selectedCommodity, setSelectedCommodity] = useState(storeMineral || results?.params_used?.mineral || null)
  const [selectedType, setSelectedType] = useState(storeDepositType || results?.params_used?.deposit_type || geologicContext?.deposit_type || null)
  const wsRef = useRef(null)

  // 拉取 矿种→矿床类型 预设清单
  useEffect(() => {
    let alive = true
    api.listDepositPresets()
      .then(d => { if (alive && d?.available) setCommodities(d.commodities || []) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  // 选矿种 → 清空已选矿床类型(二级随之刷新)
  const handleCommodityChange = (comm) => {
    setSelectedCommodity(comm)
    setStoreMineral(comm)
    setSelectedType(null)
    setStoreDepositType(null)
  }

  // 当前矿种下的矿床类型列表
  const typeOptions = commodities.find(c => c.commodity === selectedCommodity)?.deposit_types || []

  // 选矿床类型 → 载入该类型对应族的预设权重(可继续微调)
  const handleTypeChange = (typeName) => {
    setSelectedType(typeName)
    setStoreDepositType(typeName)
    const t = typeOptions.find(x => x.name === typeName)
    if (t?.weights) {
      setWeights({ ...t.weights })
      message.success(`已载入「${typeName}」(${t.family_label})预设权重，可再微调`)
    }
  }

  const selectedTypeMeta = typeOptions.find(x => x.name === selectedType)

  useEffect(() => {
    if (storeMineral && storeMineral !== selectedCommodity) {
      setSelectedCommodity(storeMineral)
    }
    if (storeDepositType && storeDepositType !== selectedType) {
      setSelectedType(storeDepositType)
    } else if (!storeDepositType && geologicContext?.deposit_type && !selectedType) {
      setSelectedType(geologicContext.deposit_type)
    }
  }, [storeMineral, storeDepositType, geologicContext, selectedCommodity, selectedType])

  const normalizeWeights = (updated) => {
    const driveKeys = ['stress', 'redox', 'fluid', 'fault', 'chem', 'temp_drive']
    const total = driveKeys.reduce((s, k) => s + (updated[k] || 0), 0)
    if (total > 0) {
      const norm = {}
      driveKeys.forEach(k => { norm[k] = updated[k] / total })
      setWeights({ ...updated, ...norm })
    }
  }

  const handleWeightChange = (key, value) => {
    normalizeWeights({ ...weights, [key]: value })
  }

  const handleRerun = async () => {
    try {
      const data = await api.startAnalysis(uploadId, {
        weights,
        delta_threshold: threshold,
        gaussian_sigma: sigma,
        ...(selectedCommodity ? { mineral: selectedCommodity } : {}),
        ...(selectedType ? { deposit_type: selectedType } : {}),
        ...(geologicContext ? { geologic_context: geologicContext } : {}),
      })
      useStore.getState().setTask(data.task_id)
      updateTask({ status: 'queued', progress: 0, current_step: '重新分析...' })

      if (wsRef.current) api.disconnectTaskWs(wsRef.current)
      wsRef.current = api.connectTaskWs(data.task_id, (msg) => {
        updateTask(msg)
        if (msg.status === 'completed' && msg.results) {
          setResults(msg.results)
          message.success('重新分析完成！')
        }
      })
    } catch (err) {
      message.error('重新分析失败')
    }
  }

  // ─── WebSocket清理 ──────────────────────────
  useEffect(() => {
    return () => {
      if (wsRef.current) api.disconnectTaskWs(wsRef.current)
    }
  }, [])

  return (
    <div>
      <div className="section-title">矿床类型(地质结构)</div>
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
        先选矿种,再选矿床类型 → 自动载入推荐权重(可不选=默认)
      </Text>
      <Select
        showSearch
        allowClear
        placeholder="① 选择矿种"
        style={{ width: '100%', marginBottom: 6 }}
        value={selectedCommodity}
        onChange={handleCommodityChange}
        optionFilterProp="label"
        options={commodities.map(c => ({ value: c.commodity, label: c.commodity }))}
      />
      <Select
        showSearch
        allowClear
        disabled={!selectedCommodity}
        placeholder={selectedCommodity ? '② 选择矿床类型' : '请先选矿种'}
        style={{ width: '100%', marginBottom: 4 }}
        value={selectedType}
        onChange={handleTypeChange}
        optionFilterProp="label"
        options={typeOptions.map(t => ({ value: t.name, label: t.name }))}
      />
      {selectedTypeMeta && (
        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 8 }}>
          成因族:{selectedTypeMeta.family_label}
          {selectedTypeMeta.applicability ? ` · 适用度 ${selectedTypeMeta.applicability}` : ''}
        </Text>
      )}
      {!selectedTypeMeta && selectedType && (
        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 8 }}>
          当前矿床类型:{selectedType}
          {geologicContext?.source ? ` · 来源 ${geologicContext.source}` : ''}
          {geologicContext?.deposit_type_confidence != null
            ? ` · 置信度 ${(geologicContext.deposit_type_confidence * 100).toFixed(0)}%`
            : ''}
        </Text>
      )}

      <Divider style={{ margin: '12px 0' }} />

      <div className="section-title">驱动力权重 (b)</div>
      <Text type="secondary" style={{ fontSize: 12 }}>
        调节滑块后权重自动归一化（总和=1）
      </Text>

      {Object.entries(WEIGHT_LABELS).map(([key, label]) => (
        <div key={key} style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
            <span>{label}</span>
            <span style={{ color: '#1a73e8' }}>{(weights[key] * 100).toFixed(1)}%</span>
          </div>
          <Slider
            min={0} max={100}
            value={Math.round(weights[key] * 100)}
            onChange={v => setWeights(prev => ({ ...prev, [key]: v / 100 }))}
            onAfterChange={v => normalizeWeights({ ...weights, [key]: v / 100 })}
          />
        </div>
      ))}

      <Divider />

      <div className="section-title">阻力权重 (a)</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
        <span>⑥ 盖层权重: {(weights.cap_rock * 100).toFixed(0)}%</span>
        <span>⑦ 温度阻力: {(weights.temp_resist * 100).toFixed(0)}%</span>
      </div>

      <Divider />

      <div className="section-title">分析参数</div>
      <div style={{ marginTop: 8 }}>
        <Text style={{ fontSize: 13 }}>Δ阈值（越负越严格）</Text>
        <InputNumber
          value={threshold}
          onChange={setThreshold}
          style={{ width: '100%', marginTop: 4 }}
          min={-100000} max={0} step={500}
        />
      </div>
      <div style={{ marginTop: 12 }}>
        <Text style={{ fontSize: 13 }}>高斯平滑Sigma</Text>
        <InputNumber
          value={sigma}
          onChange={setSigma}
          style={{ width: '100%', marginTop: 4 }}
          min={0.5} max={10} step={0.5}
        />
      </div>

      <Button
        type="primary"
        onClick={handleRerun}
        block
        style={{ marginTop: 20 }}
      >
        重新分析
      </Button>
    </div>
  )
}
