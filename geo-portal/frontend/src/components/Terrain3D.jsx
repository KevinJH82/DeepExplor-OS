import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { makeProjector } from '../lib/terrainProjection'
import { loadSliceTexture } from '../lib/sliceTexture'
import { EVIDENCES } from '../lib/stages'
import * as api from '../api/portal'

// 构建按 DEM 起伏位移的地形几何体(基础卫星层与各证据叠加层共用,故天然贴合地形)
function buildTerrainGeometry(proj, terrain) {
  const N = (terrain && !terrain.flat && terrain.heights?.length === terrain.size * terrain.size) ? terrain.size : 2
  const g = new THREE.PlaneGeometry(proj.S, proj.S, N - 1, N - 1)
  g.rotateX(-Math.PI / 2)   // 躺平：北→-Z，y 为上
  if (N > 2) {
    const pos = g.attributes.position
    const hs = terrain.heights
    for (let i = 0; i < hs.length; i++) pos.setY(i, proj.elevToWorldY(hs[i]))
    pos.needsUpdate = true
    g.computeVertexNormals()
  }
  return g
}

// ROI 3D 卫星地形：DEM 顶点位移 + Esri 卫星纹理；证据图层/有利度切片/靶点/钻孔渲染其上。
export default function Terrain3D({ bbox, projectId, model3d, drill, evidences, focusEvidence, onFocusEvidence, showDrill, report, onSelectTarget, selectedIndex }) {
  const [terrain, setTerrain] = useState(null)   // {size,min_m,max_m,heights} | {flat:true}
  const [baseUrl, setBaseUrl] = useState(null)
  const [err, setErr] = useState(null)
  const [compassAngle, setCompassAngle] = useState(0)

  useEffect(() => {
    if (!projectId || !bbox) return
    let alive = true; let obj = null
    setTerrain(null); setBaseUrl(null); setErr(null)
    api.terrain(projectId, 128).then((t) => alive && setTerrain(t)).catch(() => alive && setTerrain({ flat: true }))
    api.basemapObjectUrl(projectId).then((u) => { if (alive) { obj = u; setBaseUrl(u) } }).catch(() => alive && setErr('basemap'))
    return () => { alive = false; if (obj) URL.revokeObjectURL(obj) }
  }, [projectId, bbox])

  const proj = useMemo(() => (bbox ? makeProjector(bbox, terrain?.flat ? null : terrain) : null), [bbox, terrain])
  const geom = useMemo(() => (proj ? buildTerrainGeometry(proj, terrain) : null), [proj, terrain])
  useEffect(() => () => geom?.dispose(), [geom])
  const rootStyle = report ? { ...hudStyle, filter: 'blur(3px) opacity(.75)' } : hudStyle
  const evidenceLayers = useMemo(() => visibleEvidenceLayers(evidences, focusEvidence), [evidences, focusEvidence])

  if (!bbox) {
    return <div className="canv" style={hudStyle}><div style={msgStyle}>该项目暂无 AOI 区域（请先上传 KML），无法渲染 3D 地形。</div></div>
  }

  return (
    <div className="canv" style={rootStyle}>
      <Canvas flat camera={{ position: [0, 205, 230], fov: 42, near: 1, far: 6000 }} dpr={[1, 2]}>
        <color attach="background" args={['#aebccb']} />
        <ambientLight intensity={1.4} />
        <directionalLight position={[140, 240, 120]} intensity={1.1} castShadow={false} />
        <directionalLight position={[-120, 120, -80]} intensity={0.4} />
        {geom && <TerrainMesh geom={geom} terrain={terrain} baseUrl={baseUrl} />}
        {geom && <EvidenceDrapes geom={geom} layers={evidenceLayers} />}
        {proj && <Targets proj={proj} targets={model3d?.targets} selectedIndex={selectedIndex} onSelect={onSelectTarget} />}
        {proj && showDrill && <DrillHoles proj={proj} drill={drill} />}
        {proj && <SliceStack proj={proj} model3d={model3d} />}
        <CompassTracker onChange={setCompassAngle} />
        <OrbitControls enableDamping dampingFactor={0.1} target={[0, 25, 0]}
          minDistance={60} maxDistance={520} maxPolarAngle={Math.PI / 2.05} />
      </Canvas>
      <EvidenceOverlay layers={evidenceLayers} evidences={evidences} focusEvidence={focusEvidence} onFocusEvidence={onFocusEvidence} />
      <Compass angle={compassAngle} />
      {!terrain && <div style={loadingStyle}>加载地形与卫星底图…</div>}
    </div>
  )
}

function CompassTracker({ onChange }) {
  const { camera } = useThree()
  const lastAngle = useRef(null)
  const frameCount = useRef(0)
  const origin = useMemo(() => new THREE.Vector3(0, -10, 0), [])
  const north = useMemo(() => new THREE.Vector3(0, -10, -80), [])

  useFrame(() => {
    frameCount.current = (frameCount.current + 1) % 6
    if (frameCount.current !== 0) return
    const a = origin.clone().project(camera)
    const b = north.clone().project(camera)
    const dx = b.x - a.x
    const dy = b.y - a.y
    if (!Number.isFinite(dx) || !Number.isFinite(dy) || Math.hypot(dx, dy) < 0.001) return
    const next = THREE.MathUtils.radToDeg(Math.atan2(dx, dy))
    if (lastAngle.current == null || Math.abs(next - lastAngle.current) > 0.35) {
      lastAngle.current = next
      onChange(next)
    }
  })

  return null
}

function Compass({ angle }) {
  return (
    <div style={compassStyle} aria-label="方位指南针">
      <div style={compassDialStyle(angle)}>
        <span style={{ ...compassCardinalStyle, ...compassNorthStyle }}>N</span>
        <span style={{ ...compassCardinalStyle, right: 8, top: '50%', transform: 'translateY(-50%)' }}>E</span>
        <span style={{ ...compassCardinalStyle, bottom: 6, left: '50%', transform: 'translateX(-50%)' }}>S</span>
        <span style={{ ...compassCardinalStyle, left: 8, top: '50%', transform: 'translateY(-50%)' }}>W</span>
        <i style={compassNeedleStyle} />
      </div>
      <span style={compassPinStyle} />
    </div>
  )
}

// ── DEM 地形 mesh（基础卫星层）──
function TerrainMesh({ geom, terrain, baseUrl }) {
  const [tex, setTex] = useState(null)
  useEffect(() => {
    if (!baseUrl) { setTex(null); return }
    let alive = true
    new THREE.TextureLoader().load(baseUrl, (t) => {
      t.colorSpace = THREE.SRGBColorSpace
      if (alive) setTex(t); else t.dispose()
    })
    return () => { alive = false }
  }, [baseUrl])

  useEffect(() => () => { if (tex) tex.dispose() }, [tex])

  return (
    <mesh geometry={geom}>
      {tex
        // 不受光照的真彩贴图：卫星影像本身含真实山体阴影,直接显示原图明亮色彩
        ? <meshBasicMaterial map={tex} toneMapped={false} />
        : <meshStandardMaterial color="#7d8ea1" roughness={1} wireframe={!terrain || terrain.flat} />}
    </mesh>
  )
}

// ── 证据图层叠加（蚀变/构造/物探/化探/形变）：与 2D 同源 dataURL,贴合地形起伏堆叠在卫星面之上 ──
function visibleEvidenceLayers(evidences, focusEvidence) {
  const all = EVIDENCES
    .map((e, idx) => ({ e, idx, ev: evidences?.[e.key] }))
    .filter(({ ev }) => ev?.layerUrl && ev.visible !== false)
  if (!focusEvidence) return all
  const focused = all.filter(({ e }) => e.key === focusEvidence)
  return focused.length ? focused : all
}

function EvidenceDrapes({ geom, layers }) {
  if (!layers.length) return null
  return layers.map(({ e, idx, ev }, orderIndex) => (
    <EvidenceDrape key={e.key} geom={geom} url={ev.layerUrl}
      opacity={Math.max(0.38, ev.opacity ?? 0.75)} order={orderIndex + 1} />
  ))
}

function EvidenceOverlay({ layers, evidences, focusEvidence, onFocusEvidence }) {
  const available = new Set(layers.map(({ e }) => e.key))
  const focusedMissing = focusEvidence && !available.has(focusEvidence)
  const readyCount = EVIDENCES.filter((e) => {
    const ev = evidences?.[e.key]
    return ev?.layerUrl && ev.visible !== false
  }).length
  const clearFocus = () => {
    if (focusEvidence && onFocusEvidence) onFocusEvidence(focusEvidence)
  }
  return (
    <div style={overlayStyle} onPointerDown={(e) => e.stopPropagation()}>
      <div style={overlayHeadStyle}>
        <b>3D 证据投影</b>
        <button type="button" style={allLayerButtonStyle(!focusEvidence)}
          disabled={!readyCount || !focusEvidence} onClick={clearFocus}>
          全部
        </button>
      </div>
      <span>{readyCount ? `已叠加 ${layers.length}/${readyCount}` : '暂无可投影栅格'}</span>
      <div style={chipsStyle}>
        {EVIDENCES.map((e) => {
          const ev = evidences?.[e.key] || {}
          const on = available.has(e.key)
          const suffix = !on && ev.layerLoading ? ' 加载中' : (ev.noLayer && !on ? ' 无栅格' : '')
          const clickable = !!ev.layerUrl && ev.visible !== false && !!onFocusEvidence
          const onClick = clickable ? () => onFocusEvidence(e.key) : undefined
          return (
            <button key={e.key} type="button" disabled={!clickable} onClick={onClick}
              title={clickable ? (focusEvidence === e.key ? '取消单层聚焦' : '单看该证据层') : ''}
              style={{
              ...chipStyle,
              color: on ? '#0a8aa3' : '#7f97b8',
              borderColor: on ? 'rgba(10,162,192,.44)' : 'rgba(127,151,184,.24)',
              background: on ? 'rgba(10,162,192,.12)' : 'rgba(255,255,255,.38)',
              cursor: clickable ? 'pointer' : 'default',
              opacity: clickable || suffix ? 1 : 0.78,
            }}>{e.label}{suffix}</button>
          )
        })}
      </div>
      {focusedMissing && <em style={noteStyle}>聚焦层暂无栅格，已回退显示所有可用证据层。</em>}
    </div>
  )
}

function EvidenceDrape({ geom, url, opacity, order }) {
  const [tex, setTex] = useState(null)
  useEffect(() => {
    let alive = true
    new THREE.TextureLoader().load(url, (t) => { t.colorSpace = THREE.SRGBColorSpace; alive ? setTex(t) : t.dispose() })
    return () => { alive = false }
  }, [url])
  useEffect(() => () => { if (tex) tex.dispose() }, [tex])
  if (!tex) return null
  // 微抬高 + renderOrder 叠放,避免与卫星面/相互 z-fighting；纹理低值已透明
  return (
    <mesh geometry={geom} position={[0, 0.55 * order, 0]} renderOrder={20 + order}>
      <meshBasicMaterial map={tex} transparent opacity={opacity} toneMapped={false}
        depthWrite={false} depthTest={false} side={THREE.DoubleSide}
        polygonOffset polygonOffsetFactor={-order} polygonOffsetUnits={-order} />
    </mesh>
  )
}

// ── 靶点球 ──
function Targets({ proj, targets, selectedIndex, onSelect }) {
  const items = useMemo(() => {
    if (!targets?.length) return []
    return targets.slice(0, 20).map((t, i) => {
      const v = proj.toWorld(t.lon, t.lat, t.depth_m || 0)
      const s = t.score ?? 0.5
      return { i, v, s, data: t }
    })
  }, [targets, proj])

  return items.map(({ i, v, s, data }) => {
    const col = s > 0.66 ? '#ff7a3c' : s > 0.4 ? '#ffcf5a' : '#ffe49a'
    const r = 2.2 + s * 3.2
    const on = selectedIndex === i
    return (
      <group key={i} position={[v.x, v.y, v.z]}>
        {/* 落到地表的引线 */}
        <Stem proj={proj} lon={data.lon} lat={data.lat} from={v} color={col} />
        <mesh onClick={(e) => { e.stopPropagation(); onSelect && onSelect({ ...data }, i) }}>
          <sphereGeometry args={[r, 24, 24]} />
          <meshStandardMaterial color={col} emissive={col} emissiveIntensity={on ? 0.9 : 0.45}
            roughness={0.35} metalness={0.1} />
        </mesh>
        {on && (
          <mesh>
            <sphereGeometry args={[r + 1.2, 20, 20]} />
            <meshBasicMaterial color="#ffffff" wireframe transparent opacity={0.5} />
          </mesh>
        )}
      </group>
    )
  })
}

// 靶点 → 地表的竖直引线
function Stem({ proj, lon, lat, from, color }) {
  const geom = useMemo(() => {
    if (lon == null || lat == null) return null
    const top = proj.surfaceWorld(lon, lat)
    const g = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, top.y - from.y, 0),
    ])
    return g
  }, [proj, lon, lat, from])
  useEffect(() => () => geom?.dispose(), [geom])
  if (!geom) return null
  return <line geometry={geom}><lineBasicMaterial color={color} transparent opacity={0.5} /></line>
}

// ── 钻孔管线 ──
function DrillHoles({ proj, drill }) {
  const holes = drill?.holes
  const fbByHole = useMemo(() => {
    const m = {}
    ;(drill?.feedback || []).forEach((f) => { if (f.hole_id) m[f.hole_id] = f })
    return m
  }, [drill])

  const tubes = useMemo(() => {
    if (!holes?.length) return []
    const PCOL = { A: '#e0556e', B: '#d9921f', C: '#7f97b8' }
    return holes.map((h) => {
      const pts = (h.trajectory || []).map((p) => proj.toWorld(p.lon, p.lat, p.depth_m || 0))
      if (pts.length < 2) return null
      const fb = fbByHole[h.hole_id]
      const outcome = fb?.outcome || fb?.result
      const color = outcome === 'ore' ? '#12b07a' : outcome === 'barren' ? '#9aa6b8' : (PCOL[h.priority] || '#7f97b8')
      const curve = new THREE.CatmullRomCurve3(pts)
      const geom = new THREE.TubeGeometry(curve, Math.max(8, pts.length * 4), 1.1, 8, false)
      return { geom, color, collar: pts[0], h }
    }).filter(Boolean)
  }, [holes, proj, fbByHole])

  useEffect(() => () => tubes.forEach((t) => t.geom.dispose()), [tubes])

  return tubes.map((t, i) => (
    <group key={i}>
      <mesh geometry={t.geom}>
        <meshStandardMaterial color={t.color} emissive={t.color} emissiveIntensity={0.3} roughness={0.5} />
      </mesh>
      <mesh position={[t.collar.x, t.collar.y, t.collar.z]}>
        <sphereGeometry args={[2.4, 16, 16]} />
        <meshStandardMaterial color={t.color} emissive={t.color} emissiveIntensity={0.5} />
      </mesh>
    </group>
  ))
}

// ── 深度切片半透明平面堆叠 ──
function SliceStack({ proj, model3d }) {
  const [slices, setSlices] = useState([])
  useEffect(() => {
    const sp = model3d?.slices, tid = model3d?.taskId
    if (!tid || !sp?.length) { setSlices([]); return }
    let alive = true
    const step = Math.max(1, Math.floor(sp.length / 7))
    const sampled = sp.filter((_, i) => i % step === 0).slice(0, 7)
    Promise.all(sampled.map(async (s) => {
      const url = await loadSliceTexture(`/svc/model3d/api/result/${tid}/${s.rel}`)
      return { depth: s.depth_m, url }
    })).then((r) => { if (alive) setSlices(r) }).catch(() => { if (alive) setSlices([]) })
    return () => { alive = false }
  }, [model3d])

  return slices.map((s, i) => (
    <SlicePlane key={i} proj={proj} url={s.url} depth={s.depth} />
  ))
}

function SlicePlane({ proj, url, depth }) {
  const [tex, setTex] = useState(null)
  useEffect(() => {
    let alive = true
    new THREE.TextureLoader().load(url, (t) => { t.colorSpace = THREE.SRGBColorSpace; alive ? setTex(t) : t.dispose() })
    return () => { alive = false }
  }, [url])
  const geom = useMemo(() => { const g = new THREE.PlaneGeometry(proj.S, proj.S); g.rotateX(-Math.PI / 2); return g }, [proj])
  useEffect(() => () => geom.dispose(), [geom])
  useEffect(() => () => { if (tex) tex.dispose() }, [tex])
  if (!tex) return null
  const y = -proj.depthToWorldY(depth)
  return (
    <mesh geometry={geom} position={[0, y, 0]}>
      <meshBasicMaterial map={tex} transparent opacity={0.62} depthWrite={false} side={THREE.DoubleSide} />
    </mesh>
  )
}

const hudStyle = { width: 'min(72vw, 900px)', height: 'min(66vh, 620px)', borderRadius: 16, overflow: 'hidden', border: '1px solid var(--line)' }
const msgStyle = { width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--mut)', fontSize: 13, textAlign: 'center', padding: 24 }
const loadingStyle = { position: 'absolute', left: '50%', bottom: 14, transform: 'translateX(-50%)', color: 'var(--mut)', fontSize: 12, background: 'rgba(10,20,34,.6)', padding: '3px 10px', borderRadius: 8 }
const overlayStyle = {
  position: 'absolute', left: 12, top: 12, zIndex: 2, maxWidth: 310,
  display: 'grid', gap: 6, padding: '8px 10px', borderRadius: 10,
  background: 'rgba(255,255,255,.72)', border: '1px solid rgba(40,90,160,.16)',
  boxShadow: '0 8px 22px rgba(30,70,130,.14)', backdropFilter: 'blur(12px)',
  color: '#13324d', fontSize: 11.5,
}
const overlayHeadStyle = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
}
const allLayerButtonStyle = (active) => ({
  border: `1px solid ${active ? 'rgba(10,162,192,.42)' : 'rgba(127,151,184,.26)'}`,
  background: active ? 'rgba(10,162,192,.12)' : 'rgba(255,255,255,.46)',
  color: active ? '#0a8aa3' : '#607a9a',
  borderRadius: 999, padding: '2px 9px', fontSize: 10.5, fontWeight: 700,
  lineHeight: '16px', fontFamily: 'inherit',
  cursor: active ? 'default' : 'pointer',
})
const chipsStyle = { display: 'flex', flexWrap: 'wrap', gap: 5 }
const chipStyle = {
  fontStyle: 'normal', fontFamily: 'inherit', padding: '2px 6px', border: '1px solid', borderRadius: 999,
  fontSize: 10.5, lineHeight: '16px', whiteSpace: 'nowrap',
}
const noteStyle = { color: '#8a641a', fontStyle: 'normal', fontSize: 10.5, lineHeight: 1.4 }
const compassStyle = {
  position: 'absolute', right: 14, top: 14, zIndex: 3, width: 86, height: 86,
  pointerEvents: 'none', borderRadius: '50%',
  background: 'rgba(255,255,255,.72)', border: '1px solid rgba(40,90,160,.18)',
  boxShadow: '0 8px 22px rgba(30,70,130,.16)', backdropFilter: 'blur(12px)',
}
const compassDialStyle = (angle) => ({
  position: 'absolute', inset: 7, borderRadius: '50%',
  border: '1px solid rgba(10,162,192,.32)',
  background: 'radial-gradient(circle, rgba(10,162,192,.14), rgba(255,255,255,.1) 58%, rgba(10,162,192,.08))',
  transform: `rotate(${angle}deg)`, transition: 'transform .12s linear',
})
const compassCardinalStyle = {
  position: 'absolute', color: '#31506f', fontSize: 10.5, fontWeight: 700,
  lineHeight: 1, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
}
const compassNorthStyle = {
  left: '50%', top: 5, transform: 'translateX(-50%)', color: '#e0556e', fontSize: 12,
}
const compassNeedleStyle = {
  position: 'absolute', left: '50%', top: 15, width: 0, height: 0,
  transform: 'translateX(-50%)',
  borderLeft: '6px solid transparent', borderRight: '6px solid transparent',
  borderBottom: '24px solid #e0556e', filter: 'drop-shadow(0 2px 4px rgba(224,85,110,.32))',
}
const compassPinStyle = {
  position: 'absolute', left: '50%', top: '50%', width: 7, height: 7,
  transform: 'translate(-50%,-50%)', borderRadius: '50%',
  background: '#13324d', boxShadow: '0 0 0 3px rgba(19,50,77,.08)',
}
