import { useEffect, useState } from 'react'
import { Modal, Input, Select, Button, message, Upload, Popconfirm } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useProject } from '../store'
import * as api from '../api/portal'
import TopBar from '../components/TopBar'

const MINERAL_OPTIONS = [
  { label: '贵金属', options: [
    { value: 'gold', label: '金 (Au)' },
    { value: 'silver', label: '银 (Ag)' },
    { value: 'platinum_group', label: '铂族金属 (PGE)' },
  ] },
  { label: '有色金属', options: [
    { value: 'copper', label: '铜 (Cu)' },
    { value: 'leadzinc', label: '铅锌 (Pb-Zn)' },
    { value: 'lead', label: '铅 (Pb)' },
    { value: 'zinc', label: '锌 (Zn)' },
    { value: 'nickel', label: '镍 (Ni)' },
    { value: 'cobalt', label: '钴 (Co)' },
    { value: 'tin', label: '锡 (Sn)' },
    { value: 'tungsten', label: '钨 (W)' },
    { value: 'molybdenum', label: '钼 (Mo)' },
    { value: 'antimony', label: '锑 (Sb)' },
    { value: 'mercury', label: '汞 (Hg)' },
    { value: 'bismuth', label: '铋 (Bi)' },
  ] },
  { label: '黑色金属', options: [
    { value: 'iron', label: '铁 (Fe)' },
    { value: 'manganese', label: '锰 (Mn)' },
    { value: 'chromium', label: '铬 (Cr)' },
    { value: 'titanium', label: '钛 (Ti)' },
    { value: 'vanadium', label: '钒 (V)' },
  ] },
  { label: '稀有 / 稀土 / 稀散', options: [
    { value: 'rare_earth', label: '稀土 (REE)' },
    { value: 'lithium', label: '锂 (Li)' },
    { value: 'beryllium', label: '铍 (Be)' },
    { value: 'niobium_tantalum', label: '铌钽 (Nb-Ta)' },
    { value: 'zirconium_hafnium', label: '锆铪 (Zr-Hf)' },
    { value: 'rubidium_cesium', label: '铷铯 (Rb-Cs)' },
    { value: 'gallium', label: '镓 (Ga)' },
    { value: 'germanium', label: '锗 (Ge)' },
    { value: 'indium', label: '铟 (In)' },
  ] },
  { label: '能源矿产 / 油气', options: [
    { value: 'oil_gas', label: '油气 (Oil & Gas)' },
    { value: 'oil', label: '石油 (Oil)' },
    { value: 'gas', label: '天然气 (Gas)' },
    { value: 'shale_gas', label: '页岩气 (Shale gas)' },
    { value: 'coalbed_methane', label: '煤层气 (CBM)' },
    { value: 'coal', label: '煤 (Coal)' },
    { value: 'uranium', label: '铀 (U)' },
    { value: 'geothermal', label: '地热 (Geothermal)' },
  ] },
  { label: '非金属 / 工业矿物', options: [
    { value: 'phosphate', label: '磷 (Phosphate)' },
    { value: 'potash', label: '钾盐 (Potash)' },
    { value: 'salt', label: '岩盐 / 卤水 (Salt/Brine)' },
    { value: 'fluorite', label: '萤石 (Fluorite)' },
    { value: 'barite', label: '重晶石 (Barite)' },
    { value: 'graphite', label: '石墨 (Graphite)' },
    { value: 'quartz', label: '石英 / 硅质原料 (Quartz)' },
    { value: 'limestone', label: '石灰岩 (Limestone)' },
    { value: 'dolomite', label: '白云岩 (Dolomite)' },
    { value: 'gypsum', label: '石膏 (Gypsum)' },
    { value: 'kaolin', label: '高岭土 (Kaolin)' },
    { value: 'bauxite', label: '铝土矿 (Bauxite)' },
  ] },
  { label: '其他', options: [
    { value: 'diamond', label: '金刚石 (Diamond)' },
    { value: 'gemstone', label: '宝玉石 (Gemstone)' },
    { value: 'multi_mineral', label: '多金属 / 综合找矿' },
  ] },
]
const MINERALS = MINERAL_OPTIONS.flatMap((g) => g.options)
const THUMBS = {
  cu: 'linear-gradient(135deg,#8fd3e6,#bfe3c9)',
  au: 'linear-gradient(135deg,#f1d18a,#bfe3c9)',
  default: 'linear-gradient(135deg,#a9c8ef,#d7c2ec)',
}

export default function Projects() {
  const nav = useNavigate()
  const { projects, refresh, forget } = useProject()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [mineral, setMineral] = useState('copper')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { refresh().catch(() => message.error('加载项目失败')) }, [refresh])

  const closeModal = () => { setOpen(false); setName(''); setFile(null) }

  const create = async () => {
    if (!name.trim()) return message.warning('请输入项目名称')
    setBusy(true)
    try {
      const ml = MINERALS.find((m) => m.value === mineral)
      const p = await api.createProject({ name, mineral, mineral_label: ml?.label })
      if (file) {
        try {
          const res = await api.uploadKml(p.id, file)
          message.success(`项目已创建,KML 已上传${res.bbox ? ` · bbox ${res.bbox.map((n) => n.toFixed(2)).join(', ')}` : ''}`)
        } catch { message.warning('项目已创建,但 KML 上传失败,可在编排单重传') }
      }
      closeModal()
      nav(`/projects/${p.id}`)
    } catch (e) {
      message.error('创建失败:' + (e?.response?.data?.detail || e.message))
    } finally { setBusy(false) }
  }

  const del = async (id) => {
    try { await api.deleteProject(id); forget(id); message.success('已删除'); refresh() }
    catch (e) { message.error(e?.response?.data?.detail || '删除失败') }
  }

  const uploadProps = {
    accept: '.kml,.kmz,.ovkml,.csv,.xlsx',
    showUploadList: false,
    maxCount: 1,
    beforeUpload: (f) => { setFile(f); return false },  // 仅捕获文件,创建项目后再上传
  }

  return (
    <>
      <TopBar />
      <div className="center-wrap">
        <div style={{ maxWidth: 1000, margin: '0 auto 18px', display: 'flex', alignItems: 'center' }}>
          <h2 style={{ color: '#13324d', margin: 0 }}>项目空间</h2>
          <Button type="primary" style={{ marginLeft: 'auto' }} onClick={() => setOpen(true)}>+ 新建项目</Button>
        </div>
        <div className="pgrid">
          {projects.map((p) => (
            <div key={p.id} className="proj glass" style={{ position: 'relative' }} onClick={() => nav(`/projects/${p.id}`)}>
              <Popconfirm title="删除该项目?" description="将移除项目及其所有运行记录(不影响各服务产物)"
                okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                onConfirm={() => del(p.id)}>
                <span onClick={(e) => e.stopPropagation()} title="删除项目"
                  style={{ position: 'absolute', top: 8, right: 10, zIndex: 3, cursor: 'pointer', color: '#fff', background: 'rgba(224,85,110,.9)', borderRadius: 6, padding: '0 7px', fontSize: 12, lineHeight: '18px' }}>✕</span>
              </Popconfirm>
              <div className="thumb" style={{ background: THUMBS[p.thumb] || THUMBS.default }} />
              <div className="pb">
                <div className="pn">{p.name}</div>
                <div style={{ color: 'var(--mut)', fontSize: 12 }}>{p.mineral_label}</div>
                <div className="proj-meta">
                  <span className={p.current_run ? 'ok' : 'wt'}>{p.current_run ? '● 有运行' : '○ 未开始'}</span>
                  <span>{p.kml_name || p.aoi_bbox ? 'AOI 已就绪' : 'AOI 样例'}</span>
                </div>
                <div className="proj-meta">
                  <span>角色:{roleZh(p.my_role)}</span>
                  <span>{p.created_at ? p.created_at.slice(0, 10) : '-'}</span>
                </div>
                {p.current_run && (
                  <div style={{ marginTop: 5, fontSize: 10.5, color: 'var(--mut)', fontFamily: 'ui-monospace,monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    trace: {p.current_run}
                  </div>
                )}
              </div>
            </div>
          ))}
          <div className="proj glass" onClick={() => setOpen(true)}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 180, color: 'var(--mut)', fontSize: 30 }}>+</div>
        </div>
      </div>

      <Modal title="新建勘探项目" open={open} onOk={create} onCancel={closeModal}
        okText="创建" confirmLoading={busy}>
        <div style={{ margin: '12px 0 6px' }}>项目名称</div>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如:白银铜矿" />
        <div style={{ margin: '14px 0 6px' }}>矿种</div>
        <Select showSearch optionFilterProp="label" value={mineral} onChange={setMineral} options={MINERAL_OPTIONS} style={{ width: '100%' }} />
        <div style={{ margin: '14px 0 6px' }}>研究区 ROI</div>
        <Upload.Dragger {...uploadProps}>
          <p style={{ padding: '14px', margin: 0, color: file ? 'var(--cy)' : 'var(--mut)' }}>
            {file ? `✓ ${file.name}` : '点击或拖入 KML / KMZ / ovKML / CSV / XLSX(可选)'}
          </p>
        </Upload.Dragger>
      </Modal>
    </>
  )
}

const roleZh = (r) => ({ geologist: '地质工程师', viewer: '只读', external: '外部专家' }[r] || r)
