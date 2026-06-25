import { useState, useRef, useEffect } from 'react'
import { Upload, Button, message, Space, Tag, Typography, Divider, Collapse, Progress, Select, Alert } from 'antd'
import * as api from '../api/client'
import useStore from '../store/analysisStore'

const { Dragger } = Upload
const { Text } = Typography

const STEP_LABELS = [
  '解析KML边界', '对齐栅格数据',
  '① 地应力梯度', '② 氧逸度突变带', '③ 流体超压指数', '④ 断裂活动性',
  '⑤ 化学势梯度', '⑥ 盖层封闭性', '⑦ 温度异常梯度',
  'Z-score标准化', '计算Δ判别式', '提取靶区', '写入结果', '生成统计',
]

// 卫星数据 slot（不含 KML）
const SATELLITE_SLOTS = [
  { key: 'dem', label: 'DEM (SRTM/ASTER GDEM)', required: true, accept: '.tif,.tiff' },
  { key: 's2_b03', label: 'Sentinel-2 B03 (绿)', required: true, accept: '.tif,.tiff' },
  { key: 's2_b04', label: 'Sentinel-2 B04 (红)', required: true, accept: '.tif,.tiff' },
  { key: 's2_b08', label: 'Sentinel-2 B08 (近红外)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b05', label: 'ASTER B05 (SWIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b06', label: 'ASTER B06 (SWIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b07', label: 'ASTER B07 (SWIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b08', label: 'ASTER B08 (SWIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b10', label: 'ASTER B10 (TIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b11', label: 'ASTER B11 (TIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b12', label: 'ASTER B12 (TIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b13', label: 'ASTER B13 (TIR)', required: true, accept: '.tif,.tiff' },
  { key: 'aster_b14', label: 'ASTER B14 (TIR)', required: true, accept: '.tif,.tiff' },
  { key: 's2_b02', label: 'Sentinel-2 B02 (蓝/铁氧化)', required: false, accept: '.tif,.tiff' },
  { key: 's2_b11', label: 'Sentinel-2 B11 (SWIR1/Al-OH)', required: false, accept: '.tif,.tiff' },
  { key: 's2_b12', label: 'Sentinel-2 B12 (SWIR2/Al-OH)', required: false, accept: '.tif,.tiff' },
  { key: 'aster_b01', label: 'ASTER B1 (VNIR/铁参考)', required: false, accept: '.tif,.tiff' },
  { key: 'aster_b03n', label: 'ASTER B3N (VNIR/Fe³⁺)', required: false, accept: '.tif,.tiff' },
  { key: 'aster_b09', label: 'ASTER B9 (SWIR/碳酸盐)', required: false, accept: '.tif,.tiff' },
  { key: 'insar', label: 'InSAR 速度场 (可选)', required: false, accept: '.tif,.tiff' },
  { key: 'insar_coherence', label: 'InSAR 相干性 (可选)', required: false, accept: '.tif,.tiff' },
]

// 所有 slot（含 KML，用于查找标签）
const ALL_SLOTS = [
  { key: 'kml', label: 'KML / OVKML 研究区边界 / Excel 坐标', required: true, accept: '.kml,.ovkml,.xlsx,.xls' },
  ...SATELLITE_SLOTS,
]

export default function UploadPanel() {
  const [uploadingZip, setUploadingZip] = useState(false)
  const [uploadingKml, setUploadingKml] = useState(false)
  const [supplementing, setSupplementing] = useState({})
  const [kmlFile, setKmlFile] = useState(null)
  const [zipProcessed, setZipProcessed] = useState(false)
  const [manualMode, setManualMode] = useState(false)
  const [fileMap, setFileMap] = useState({})
  // 交付库自动获取
  const [deliveryRoot, setDeliveryRoot] = useState(null)
  const [deliveryMounted, setDeliveryMounted] = useState(true)
  const [projects, setProjects] = useState([])
  const [selectedProject, setSelectedProject] = useState(null)
  const [fetchingDelivery, setFetchingDelivery] = useState(false)
  const [deliveryInfo, setDeliveryInfo] = useState(null)
  const [detecting, setDetecting] = useState(false)
  const [commodities, setCommodities] = useState([])
  const wsRef = useRef(null)

  const uploadId = useStore(s => s.uploadId)
  const setUploadId = useStore(s => s.setUploadId)
  const setFileMetas = useStore(s => s.setFileMetas)
  const setTask = useStore(s => s.setTask)
  const updateTask = useStore(s => s.updateTask)
  const setResults = useStore(s => s.setResults)
  const matchResult = useStore(s => s.matchResult)
  const setMatchResult = useStore(s => s.setMatchResult)
  const clearMatchResult = useStore(s => s.clearMatchResult)
  const geologicContext = useStore(s => s.geologicContext)
  const selectedMineral = useStore(s => s.selectedMineral)
  const selectedDepositType = useStore(s => s.selectedDepositType)
  const setGeologicContext = useStore(s => s.setGeologicContext)
  const setSelectedMineral = useStore(s => s.setSelectedMineral)
  const setSelectedDepositType = useStore(s => s.setSelectedDepositType)
  const taskStatus = useStore(s => s.taskStatus)
  const taskProgress = useStore(s => s.taskProgress)
  const taskStep = useStore(s => s.taskStep)

  // KML 是否已上传（独立判断）
  const kmlReady = matchResult?.matched?.some(m => m.slot === 'kml') || false

  // ─── 交付库项目列表（挂载即拉取，可手动重新检测） ──────────────
  const loadDeliveryProjects = async () => {
    setDetecting(true)
    try {
      const data = await api.listDeliveryProjects()
      setDeliveryRoot(data.delivery_root)
      setDeliveryMounted(!!data.mounted)
      setProjects(data.projects || [])
      return !!data.mounted
    } catch {
      setDeliveryMounted(false)
      return false
    } finally {
      setDetecting(false)
    }
  }

  useEffect(() => { loadDeliveryProjects() }, [])

  useEffect(() => {
    let alive = true
    api.listDepositPresets()
      .then(d => {
        if (alive && d?.available) setCommodities(d.commodities || [])
      })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const typeOptions = commodities.find(c => c.commodity === selectedMineral)?.deposit_types || []

  const handleMineralChange = (mineral) => {
    setSelectedMineral(mineral)
    setSelectedDepositType(null)
  }

  const handleRedetect = async () => {
    const ok = await loadDeliveryProjects()
    if (ok) message.success('已检测到交付目录')
    else message.info('仍未检测到交付目录，请确认外置数据盘已连接')
  }

  // ─── 交付库自动获取：ROI -> 抓取数据 ────────────
  const applyDeliveryResult = (data) => {
    if (!uploadId) setUploadId(data.upload_id)
    else setUploadId(data.upload_id) // 交付每次生成新会话，直接覆盖
    setMatchResult(data)
    setZipProcessed(true)
    const metas = data.matched.map(m => m.meta)
    setFileMetas(metas)
    setDeliveryInfo({ project_name: data.project_name, bbox: data.bbox })
    const context = data.geologic_context || null
    setGeologicContext(context)
    if (context?.mineral_hint) setSelectedMineral(context.mineral_hint)
    if (context?.deposit_type) setSelectedDepositType(context.deposit_type)
    const satCount = data.matched.filter(m => m.slot !== 'kml').length
    if (data.all_required_filled) {
      message.success(`已从交付库获取 ${satCount} 个遥感数据，研究区就绪！`)
    } else {
      message.warning(`已获取 ${satCount} 个文件，但仍缺少必填数据`)
    }
  }

  const handleDeliverySelect = async () => {
    if (!selectedProject) { message.warning('请先选择交付项目'); return }
    setFetchingDelivery(true)
    try {
      const data = await api.prepareFromDelivery({ project: selectedProject })
      applyDeliveryResult(data)
    } catch (err) {
      message.error(err.response?.data?.detail || '交付库获取失败')
    } finally {
      setFetchingDelivery(false)
    }
  }

  const handleDeliveryRoi = async (file) => {
    const ext = file.name.toLowerCase().split('.').pop()
    if (!['ovkml', 'kml', 'geojson', 'json'].includes(ext)) {
      message.error('请上传 .ovkml / .kml / .geojson 格式的 ROI 文件')
      return false
    }
    setFetchingDelivery(true)
    try {
      const data = await api.prepareFromDelivery({ file })
      applyDeliveryResult(data)
    } catch (err) {
      message.error(err.response?.data?.detail || '交付库未找到匹配项目，请确认 ROI 文件名与项目目录名一致')
    } finally {
      setFetchingDelivery(false)
    }
    return false
  }

  // ─── KML/Excel 上传 ──────────────────────────
  const handleKmlUpload = async (file) => {
    // 校验文件类型
    const ext = file.name.toLowerCase().split('.').pop()
    if (!['kml', 'ovkml', 'xlsx', 'xls'].includes(ext)) {
      message.error('请上传 KML、OVKML 或 Excel (.xlsx/.xls) 文件')
      return false
    }

    setUploadingKml(true)
    try {
      let id = uploadId
      if (!id) {
        // 生成 upload_id（兼容性写法）
        id = Date.now().toString(36) + Math.random().toString(36).substring(2, 8)
        setUploadId(id)
      }
      const data = await api.supplementFile(id, 'kml', file)
      setMatchResult(data)
      const metas = data.matched.map(m => m.meta)
      setFileMetas(metas)
      setKmlFile(file)
      message.success('研究区边界已上传')
    } catch (err) {
      message.error(err.response?.data?.detail || '边界文件上传失败')
    } finally {
      setUploadingKml(false)
    }
    return false
  }

  // ─── ZIP 上传 ──────────────────────────────
  const handleZipUpload = async (file) => {
    setUploadingZip(true)
    try {
      const data = await api.uploadZip(file, uploadId || undefined)
      if (!uploadId) setUploadId(data.upload_id)
      setZipProcessed(true)
      setMatchResult(data)
      const metas = data.matched.map(m => m.meta)
      setFileMetas(metas)
      const satelliteCount = data.matched.filter(m => m.slot !== 'kml').length
      const missingCount = data.required_missing.filter(s => s !== 'kml').length
      if (missingCount === 0 && kmlReady) {
        message.success(`自动匹配 ${satelliteCount} 个卫星数据文件，全部就绪！`)
      } else if (missingCount === 0) {
        message.success(`自动匹配 ${satelliteCount} 个卫星数据文件`)
      } else {
        message.warning(`匹配 ${satelliteCount} 个卫星数据文件，还有 ${missingCount} 个需要补充`)
      }
    } catch (err) {
      message.error(err.response?.data?.detail || 'ZIP 上传失败')
    } finally {
      setUploadingZip(false)
    }
    return false
  }

  // ─── 补充上传单个卫星数据文件 ──────────────
  const handleSupplement = async (slotKey, file) => {
    setSupplementing(prev => ({ ...prev, [slotKey]: true }))
    try {
      const data = await api.supplementFile(uploadId, slotKey, file)
      setMatchResult(data)
      const metas = data.matched.map(m => m.meta)
      setFileMetas(metas)
      message.success(`${ALL_SLOTS.find(s => s.key === slotKey)?.label} 上传成功`)
    } catch (err) {
      message.error(err.response?.data?.detail || '补充上传失败')
    } finally {
      setSupplementing(prev => ({ ...prev, [slotKey]: false }))
    }
    return false
  }

  // ─── 开始分析 ──────────────────────────────
  const handleAnalyze = async () => {
    try {
      const params = {
        ...(selectedMineral || geologicContext?.mineral_hint
          ? { mineral: selectedMineral || geologicContext?.mineral_hint }
          : {}),
        ...(selectedDepositType || geologicContext?.deposit_type
          ? { deposit_type: selectedDepositType || geologicContext?.deposit_type }
          : {}),
        ...(geologicContext ? { geologic_context: geologicContext } : {}),
      }
      const data = await api.startAnalysis(uploadId, params)
      setTask(data.task_id)
      updateTask({ status: 'queued', progress: 0, current_step: '排队中...' })

      if (wsRef.current) api.disconnectTaskWs(wsRef.current)
      wsRef.current = api.connectTaskWs(data.task_id, (msg) => {
        updateTask(msg)
        if (msg.status === 'completed' && msg.results) {
          setResults(msg.results)
          message.success('分析完成！')
        }
        if (msg.status === 'failed') {
          message.error(`分析失败: ${msg.error}`)
        }
      })
    } catch (err) {
      message.error(err.response?.data?.detail || '启动分析失败')
    }
  }

  // ─── WebSocket清理 ──────────────────────────
  useEffect(() => {
    return () => {
      if (wsRef.current) api.disconnectTaskWs(wsRef.current)
    }
  }, [])

  // ─── 清除重传 ──────────────────────────────
  const handleClear = () => {
    setFileMap({})
    setKmlFile(null)
    setZipProcessed(false)
    setManualMode(false)
    setDeliveryInfo(null)
    setSelectedProject(null)
    setGeologicContext(null)
    setSelectedMineral(null)
    setSelectedDepositType(null)
    clearMatchResult()
    setUploadId(null)
    useStore.getState().resetTask()
  }

  // ─── 手动逐个上传 ──────────────────────────
  const handleManualUpload = async () => {
    setUploadingZip(true)
    try {
      const data = await api.uploadFiles(fileMap)
      setUploadId(data.upload_id)
      setFileMetas(data.files)
      message.success(`上传成功！共 ${data.files.length} 个文件`)
    } catch (err) {
      message.error(err.response?.data?.detail || '上传失败')
    } finally {
      setUploadingZip(false)
    }
  }

  const requiredFilled = ALL_SLOTS.filter(s => s.required).every(s => !!fileMap[s.key])

  // ─── 渲染卫星数据匹配结果 ──────────────────
  const renderSatelliteMatch = () => {
    if (!matchResult) return null

    const { matched, unmatched, errors, required_missing, warnings } = matchResult

    // 只展示卫星数据（排除 KML）
    const satMatched = matched.filter(m => m.slot !== 'kml')
    const satUnmatched = unmatched.filter(u => u.slot !== 'kml')
    const satErrors = errors.filter(e => e.slot !== 'kml')
    const satMissing = required_missing.filter(s => s !== 'kml')
    const satReady = satMissing.length === 0

    return (
      <div>
        <div style={{
          marginBottom: 10, padding: '6px 10px',
          background: satReady ? '#f6ffed' : '#fff7e6',
          borderRadius: 6,
        }}>
          <Text strong style={{ fontSize: 13 }}>
            {satReady
              ? `✅ 卫星数据 ${satMatched.length} 个文件已就绪`
              : `⚠️ 已匹配 ${satMatched.length}/14 个，缺少 ${satMissing.length} 个必填文件`}
          </Text>
        </div>

        {warnings?.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {warnings.map((w, i) => (
              <Text key={i} type="warning" style={{ fontSize: 11, display: 'block' }}>⚡ {w}</Text>
            ))}
          </div>
        )}

        {/* 已匹配 */}
        {satMatched.length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <Text strong style={{ fontSize: 12, color: '#52c41a' }}>已识别：</Text>
            {satMatched.map(m => (
              <div key={m.slot} style={{ display: 'flex', alignItems: 'center', padding: '2px 0', fontSize: 12 }}>
                <span style={{ color: '#52c41a', marginRight: 6 }}>✓</span>
                <span style={{ flex: 1 }}>{ALL_SLOTS.find(s => s.key === m.slot)?.label || m.slot}</span>
                <Text type="secondary" style={{ fontSize: 11 }}>{m.original_filename}</Text>
              </div>
            ))}
          </div>
        )}

        {/* 验证失败 */}
        {satErrors.length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <Text strong style={{ fontSize: 12, color: '#ff4d4f' }}>验证失败：</Text>
            {satErrors.map(e => (
              <div key={e.slot} style={{ padding: '2px 0', fontSize: 12 }}>
                <span style={{ color: '#ff4d4f', marginRight: 6 }}>✗</span>
                <span style={{ flex: 1 }}>{ALL_SLOTS.find(s => s.key === e.slot)?.label}</span>
                <Text type="danger" style={{ fontSize: 11 }}>{e.error}</Text>
              </div>
            ))}
          </div>
        )}

        {/* 缺失的必填文件 → 补充上传 */}
        {satUnmatched.filter(u => u.required).length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <Text strong style={{ fontSize: 12, color: '#faad14' }}>需要补充：</Text>
            {satUnmatched.filter(u => u.required).map(u => {
              const slotDef = ALL_SLOTS.find(s => s.key === u.slot)
              return (
                <div key={u.slot} style={{ marginTop: 4 }}>
                  <Text style={{ fontSize: 12, color: '#faad14' }}>! {u.label}</Text>
                  <Dragger
                    accept={slotDef?.accept || '.tif,.tiff'}
                    maxCount={1}
                    showUploadList={false}
                    beforeUpload={(file) => { handleSupplement(u.slot, file); return false }}
                    style={{ padding: '2px 8px' }}
                  >
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {supplementing[u.slot] ? '上传中...' : '点击或拖拽上传'}
                    </Text>
                  </Dragger>
                </div>
              )
            })}
          </div>
        )}

        {/* 缺失的可选文件 */}
        {satUnmatched.filter(u => !u.required).length > 0 && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>可选（未提供）：</Text>
            {satUnmatched.filter(u => !u.required).map(u => (
              <div key={u.slot} style={{ fontSize: 12, color: '#999', padding: '1px 0' }}>— {u.label}</div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // ─── 渲染手动上传模式 ──────────────────────
  const renderManualUpload = () => (
    <div>
      {ALL_SLOTS.map(slot => (
        <div key={slot.key} style={{ marginBottom: 8 }}>
          <Dragger
            accept={slot.accept}
            maxCount={1}
            showUploadList={!!fileMap[slot.key]}
            fileList={fileMap[slot.key] ? [{ uid: slot.key, name: fileMap[slot.key].name }] : []}
            beforeUpload={(file) => { setFileMap(prev => ({ ...prev, [slot.key]: file })); return false }}
            onRemove={() => {
              setFileMap(prev => { const n = { ...prev }; delete n[slot.key]; return n })
            }}
            style={{ padding: '4px 8px' }}
          >
            <p style={{ margin: 0, fontSize: 13 }}>
              {slot.label}
              {slot.required
                ? <Tag color="red" style={{ marginLeft: 6, fontSize: 10 }}>必填</Tag>
                : <Tag style={{ marginLeft: 6, fontSize: 10 }}>可选</Tag>}
            </p>
          </Dragger>
        </div>
      ))}
      <Button type="primary" loading={uploadingZip} disabled={!requiredFilled} onClick={handleManualUpload} block>
        上传所有文件
      </Button>
      {!requiredFilled && (
        <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12, textAlign: 'center' }}>
          请上传所有必填文件
        </Text>
      )}
    </div>
  )

  // ─── 主渲染 ────────────────────────────────
  const canStartAnalysis = matchResult?.all_required_filled

  return (
    <div>
      <div className="section-title">数据上传</div>

      {/* ── 方式一（主）：输入 ROI，从交付库自动获取 ── */}
      <div style={{ marginBottom: 12 }}>
        <Text strong style={{ fontSize: 13 }}>输入 ROI 自动获取数据</Text>

        {!deliveryMounted ? (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 6 }}
            title="未检测到交付目录"
            description={
              <div style={{ fontSize: 12 }}>
                <div>未连接外置数据盘时,可在下方「手动上传」直接提交 KML / ZIP / 单波段。</div>
                {deliveryRoot && (
                  <Text type="secondary" style={{ fontSize: 11, wordBreak: 'break-all' }}>
                    期望路径:{deliveryRoot}
                  </Text>
                )}
                <div style={{ marginTop: 8 }}>
                  <Button size="small" loading={detecting} onClick={handleRedetect}>
                    重新检测
                  </Button>
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                    插回数据盘后点此即可恢复,无需刷新
                  </Text>
                </div>
              </div>
            }
          />
        ) : (
          <div style={{ marginTop: 6 }}>
            <Space.Compact style={{ width: '100%' }}>
              <Select
                showSearch
                placeholder={`选择交付项目（共 ${projects.length} 个）`}
                style={{ flex: 1 }}
                value={selectedProject}
                onChange={setSelectedProject}
                optionFilterProp="label"
                options={projects.map(p => ({ value: p.name, label: p.name }))}
              />
              <Button type="primary" loading={fetchingDelivery} onClick={handleDeliverySelect}>
                获取
              </Button>
            </Space.Compact>

            <Dragger
              accept=".ovkml,.kml,.geojson,.json"
              maxCount={1}
              showUploadList={false}
              beforeUpload={handleDeliveryRoi}
              disabled={fetchingDelivery}
              style={{ marginTop: 8, padding: '6px 8px' }}
            >
              <Text type="secondary" style={{ fontSize: 12 }}>
                {fetchingDelivery ? '正在从交付库获取...' : '或上传 ROI 文件（.ovkml/.kml/.geojson）自动匹配项目'}
              </Text>
            </Dragger>

            {deliveryInfo && (
              <div style={{ marginTop: 8, padding: '6px 10px', background: '#f6ffed', borderRadius: 6, fontSize: 12 }}>
                <span style={{ color: '#52c41a', marginRight: 6 }}>✓</span>
                交付项目：{deliveryInfo.project_name}
                {deliveryInfo.bbox && (
                  <Text type="secondary" style={{ display: 'block', fontSize: 11, marginTop: 2 }}>
                    范围 [{deliveryInfo.bbox[0].toFixed(3)}, {deliveryInfo.bbox[1].toFixed(3)}] – [{deliveryInfo.bbox[2].toFixed(3)}, {deliveryInfo.bbox[3].toFixed(3)}]
                  </Text>
                )}
              </div>
            )}
            {geologicContext?.deposit_type && (
              <div style={{ marginTop: 8, padding: '6px 10px', background: '#fff7e6', borderRadius: 6, fontSize: 12 }}>
                <span style={{ color: '#d48806', marginRight: 6 }}>●</span>
                矿床类型：{geologicContext.deposit_type}
                <Text type="secondary" style={{ display: 'block', fontSize: 11, marginTop: 2 }}>
                  来源 {geologicContext.source || 'geo-stru'}
                  {geologicContext.deposit_type_confidence != null
                    ? ` · 置信度 ${(geologicContext.deposit_type_confidence * 100).toFixed(0)}%`
                    : ''}
                </Text>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 抓取 / 匹配结果详情 */}
      {zipProcessed && matchResult && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <Text strong style={{ fontSize: 13 }}>遥感数据</Text>
          {renderSatelliteMatch()}
        </>
      )}

      {matchResult?.all_required_filled && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <Text strong style={{ fontSize: 13 }}>地质上下文</Text>
          <Text type="secondary" style={{ display: 'block', fontSize: 12, margin: '4px 0 6px' }}>
            首轮分析会使用这里的矿种/矿床类型解析权重预设。
          </Text>
          <Select
            showSearch
            allowClear
            placeholder="选择矿种"
            style={{ width: '100%', marginBottom: 6 }}
            value={selectedMineral}
            onChange={handleMineralChange}
            optionFilterProp="label"
            options={commodities.map(c => ({ value: c.commodity, label: c.commodity }))}
          />
          <Select
            showSearch
            allowClear
            disabled={!selectedMineral}
            placeholder={selectedMineral ? '选择矿床类型' : '请先选择矿种'}
            style={{ width: '100%' }}
            value={selectedDepositType}
            onChange={setSelectedDepositType}
            optionFilterProp="label"
            options={typeOptions.map(t => ({ value: t.name, label: t.name }))}
          />
          {geologicContext?.deposit_type && (
            <Text type="secondary" style={{ display: 'block', fontSize: 11, marginTop: 6 }}>
              geo-stru 建议:{geologicContext.deposit_type}
              {geologicContext.deposit_type_confidence != null
                ? ` · 置信度 ${(geologicContext.deposit_type_confidence * 100).toFixed(0)}%`
                : ''}
            </Text>
          )}
        </>
      )}

      {/* ── 方式二（备用）：手动上传 KML / ZIP / 单文件 ── */}
      <Collapse
        ghost
        size="small"
        style={{ marginTop: 8 }}
        items={[{
          key: 'manual-all',
          label: <Text type="secondary" style={{ fontSize: 12 }}>手动上传（KML / ZIP / 单文件）</Text>,
          children: (
            <div>
              <div style={{ marginBottom: 12 }}>
                <Text strong style={{ fontSize: 12 }}>研究区边界 (KML / Excel)</Text>
                <Dragger
                  maxCount={1}
                  showUploadList={false}
                  beforeUpload={handleKmlUpload}
                  disabled={uploadingKml}
                  style={{ marginTop: 6, padding: '6px 8px' }}
                >
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {uploadingKml ? '上传中...' : (kmlReady ? `✓ ${matchResult.matched.find(m => m.slot === 'kml')?.original_filename || 'KML 已上传'}（点击更换）` : '点击或拖拽上传 KML / Excel 坐标文件')}
                  </Text>
                </Dragger>
              </div>

              <Dragger
                accept=".zip"
                maxCount={1}
                showUploadList={false}
                beforeUpload={handleZipUpload}
                disabled={uploadingZip}
                style={{ marginBottom: 12 }}
              >
                <p style={{ fontSize: 24, margin: '6px 0' }}>📦</p>
                <p style={{ margin: 0, fontSize: 13 }}>
                  {uploadingZip ? '正在解析...' : '拖拽卫星数据 ZIP 压缩包'}
                </p>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  系统自动识别压缩包中的 DEM、Sentinel-2、ASTER 等数据
                </Text>
              </Dragger>

              {uploadingZip && <Progress percent={100} status="active" format={() => '解析中...'} />}

              <Collapse
                ghost
                size="small"
                items={[{
                  key: 'manual',
                  label: <Text type="secondary" style={{ fontSize: 12 }}>手动逐个上传</Text>,
                  children: renderManualUpload(),
                }]}
              />
            </div>
          ),
        }]}
      />

      {/* ── 操作按钮 / 进度 ── */}
      {taskStatus === 'running' || taskStatus === 'queued' ? (
        <div style={{ marginTop: 16, padding: '12px', background: '#f0f5ff', borderRadius: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <Text strong style={{ fontSize: 13, color: '#1890ff' }}>分析进行中...</Text>
            <Text style={{ fontSize: 12, color: '#1890ff' }}>{Math.round(taskProgress)}%</Text>
          </div>
          <Progress
            percent={Math.round(taskProgress)}
            strokeColor="#1890ff"
            showInfo={false}
            size="small"
            status="active"
          />
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4, textAlign: 'center' }}>
            {taskStep || '准备中...'}
          </Text>
        </div>
      ) : taskStatus === 'failed' ? (
        <div style={{ marginTop: 16, padding: '12px', background: '#fff2f0', borderRadius: 8 }}>
          <Text type="danger" style={{ fontSize: 13 }}>分析失败，请检查数据后重试</Text>
          <Button onClick={handleClear} block style={{ marginTop: 8 }}>重新上传</Button>
        </div>
      ) : (
        <Space style={{ width: '100%', justifyContent: 'center', marginTop: 16 }}>
          <Button
            type="primary"
            onClick={handleAnalyze}
            disabled={!canStartAnalysis}
            block
          >
            开始分析
          </Button>
          {matchResult && (
            <Button onClick={handleClear} block>重新上传</Button>
          )}
        </Space>
      )}

      {!canStartAnalysis && matchResult && taskStatus === 'idle' && (
        <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 11, textAlign: 'center' }}>
          {!kmlReady ? '请上传研究区边界文件' : '请补充缺失的必填数据文件'}
        </Text>
      )}
    </div>
  )
}
