import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import { Checkbox, Slider, Collapse } from 'antd'
import { createRoot } from 'react-dom/client'
import { getTileUrl } from '../api/client'
import useStore from '../store/analysisStore'

const COLORMAPS = {
  stress_gradient: { label: '① 地应力梯度', defaultChecked: false },
  redox_gradient: { label: '② 氧逸度突变带', defaultChecked: false },
  fluid_overpressure: { label: '③ 流体超压指数', defaultChecked: false },
  fault_activity: { label: '④ 断裂活动性', defaultChecked: false },
  chem_potential: { label: '⑤ 化学势梯度', defaultChecked: false },
  cap_rock_pressure: { label: '⑥ 盖层封闭性', defaultChecked: false },
  temp_gradient: { label: '⑦ 温度异常梯度', defaultChecked: false },
  delta_discriminant: { label: 'Δ 判别式', defaultChecked: true },
  target_zones: { label: '靶区', defaultChecked: true },
}

function TileLayerManager({ layerName, taskId, active, opacity }) {
  const map = useMap()
  const layerRef = useRef(null)
  useEffect(() => {
    if (layerRef.current) {
      map.removeLayer(layerRef.current)
      layerRef.current = null
    }
    if (active) {
      const url = getTileUrl(taskId, layerName)
      layerRef.current = L.tileLayer(url, {
        opacity,
        maxZoom: 18,
      }).addTo(map)
    }
    return () => {
      if (layerRef.current) {
        map.removeLayer(layerRef.current)
      }
    }
  }, [active, opacity, layerName, taskId, map])
  return null
}

function TileLayers({ layers, taskId }) {
  const activeLayers = useStore(s => s.activeLayers)
  const layerOpacity = useStore(s => s.layerOpacity)

  useEffect(() => {
    const active = {}
    const opacity = {}
    layers.forEach(l => {
      const cfg = COLORMAPS[l.name]
      active[l.name] = cfg?.defaultChecked || false
      opacity[l.name] = 0.6
    })
    useStore.setState({ activeLayers: active, layerOpacity: opacity })
  }, [layers])

  return (
    <>
      {layers.map(layer => (
        <TileLayerManager
          key={layer.name}
          layerName={layer.name}
          taskId={taskId}
          active={activeLayers[layer.name] || false}
          opacity={layerOpacity[layer.name] ?? 0.6}
        />
      ))}
    </>
  )
}

export default function LayerControls({ layers, taskId }) {
  const activeLayers = useStore(s => s.activeLayers)
  const layerOpacity = useStore(s => s.layerOpacity)
  const toggleLayer = useStore(s => s.toggleLayer)
  const setLayerOpacity = useStore(s => s.setLayerOpacity)

  const items = layers.map(layer => {
    const cfg = COLORMAPS[layer.name] || { label: layer.name }
    const isActive = activeLayers[layer.name] || false
    const opacity = layerOpacity[layer.name] ?? 0.6

    return {
      key: layer.name,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Checkbox
            checked={isActive}
            onChange={() => toggleLayer(layer.name)}
            onClick={e => e.stopPropagation()}
          />
          <span style={{ fontSize: 13 }}>{cfg.label}</span>
          {layer.stats?.valid_pixels > 0 && (
            <span style={{ fontSize: 11, color: '#999', marginLeft: 'auto' }}>
              {layer.stats.min?.toFixed(1)} ~ {layer.stats.max?.toFixed(1)}
            </span>
          )}
        </div>
      ),
      children: (
        <div>
          <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>透明度</div>
          <Slider
            min={0} max={100}
            value={Math.round(opacity * 100)}
            onChange={v => setLayerOpacity(layer.name, v / 100)}
          />
          {layer.stats && (
            <div style={{ fontSize: 11, color: '#888' }}>
              <div>最小值: {layer.stats.min?.toFixed(4)}</div>
              <div>最大值: {layer.stats.max?.toFixed(4)}</div>
              <div>均值: {layer.stats.mean?.toFixed(4)}</div>
              <div>标准差: {layer.stats.std?.toFixed(4)}</div>
            </div>
          )}
        </div>
      ),
    }
  })

  return (
    <>
      <TileLayers layers={layers} taskId={taskId} />
    </>
  )
}

// 图层控制面板 - 作为独立组件在MapContainer外部使用
export function LayerControlPanel({ layers }) {
  const activeLayers = useStore(s => s.activeLayers)
  const layerOpacity = useStore(s => s.layerOpacity)
  const toggleLayer = useStore(s => s.toggleLayer)
  const setLayerOpacity = useStore(s => s.setLayerOpacity)

  const items = layers.map(layer => {
    const cfg = COLORMAPS[layer.name] || { label: layer.name }
    const isActive = activeLayers[layer.name] || false
    const opacity = layerOpacity[layer.name] ?? 0.6

    return {
      key: layer.name,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Checkbox
            checked={isActive}
            onChange={() => toggleLayer(layer.name)}
            onClick={e => e.stopPropagation()}
          />
          <span style={{ fontSize: 13 }}>{cfg.label}</span>
        </div>
      ),
      children: (
        <div>
          <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>透明度</div>
          <Slider
            min={0} max={100}
            value={Math.round(opacity * 100)}
            onChange={v => setLayerOpacity(layer.name, v / 100)}
          />
          {layer.stats && (
            <div style={{ fontSize: 11, color: '#888' }}>
              <div>均值: {layer.stats.mean?.toFixed(4)} | 标准差: {layer.stats.std?.toFixed(4)}</div>
            </div>
          )}
        </div>
      ),
    }
  })

  return (
    <div style={{
      position: 'absolute', top: 10, right: 10, zIndex: 1000,
      background: 'rgba(255,255,255,0.95)', borderRadius: 8,
      boxShadow: '0 2px 8px rgba(0,0,0,0.15)', maxWidth: 300,
      maxHeight: '80vh', overflow: 'auto', padding: '8px 0',
    }}>
      <div style={{ padding: '4px 12px 8px', fontWeight: 600, fontSize: 14, borderBottom: '1px solid #f0f0f0' }}>
        图层控制
      </div>
      <Collapse ghost size="small" items={items} style={{ fontSize: 13 }} />
    </div>
  )
}
