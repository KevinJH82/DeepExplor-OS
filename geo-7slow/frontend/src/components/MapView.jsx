import { useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import { Select, Upload, message, Typography } from 'antd'
import LayerControls, { LayerControlPanel } from './LayerControls'
import useStore from '../store/analysisStore'
import * as api from '../api/client'
import 'leaflet/dist/leaflet.css'

const { Text } = Typography
const { Dragger } = Upload

const BASEMAPS = [
  {
    key: 'topo',
    label: '🗺️ 地形图',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenTopoMap',
    maxZoom: 17,
  },
  {
    key: 'osm',
    label: '📋 街道地图',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap',
    maxZoom: 19,
  },
  {
    key: 'satellite',
    label: '🛰️ 卫星影像',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '&copy; Esri',
    maxZoom: 18,
    noSubdomains: true,
  },
  {
    key: 'satellite_label',
    label: '🛰️ 卫星+标注',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '&copy; Esri',
    maxZoom: 18,
    noSubdomains: true,
    labelUrl: 'https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}.png',
  },
  {
    key: 'dark',
    label: '🌙 暗色地图',
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '&copy; CartoDB',
    maxZoom: 19,
  },
]

function BasemapLayers({ basemap, customBasemapInfo }) {
  const map = useMap()
  const tileRef = useRef(null)
  const labelRef = useRef(null)

  useEffect(() => {
    if (tileRef.current) {
      map.removeLayer(tileRef.current)
      tileRef.current = null
    }
    if (labelRef.current) {
      map.removeLayer(labelRef.current)
      labelRef.current = null
    }

    // 自定义底图
    if (basemap === 'custom' && customBasemapInfo) {
      const url = `/tiles/${customBasemapInfo.id}/basemap/{z}/{x}/{y}.png`
      tileRef.current = L.tileLayer(url, {
        maxZoom: 20,
        bounds: customBasemapInfo.bounds,
      }).addTo(map)

      if (customBasemapInfo.bounds) {
        map.fitBounds(customBasemapInfo.bounds, { padding: [20, 20] })
      }
      return
    }

    const cfg = BASEMAPS.find(b => b.key === basemap)
    if (!cfg) return

    if (cfg.noSubdomains) {
      tileRef.current = L.tileLayer(cfg.url, {
        attribution: cfg.attribution,
        maxZoom: cfg.maxZoom,
      }).addTo(map)
    } else {
      tileRef.current = L.tileLayer(cfg.url, {
        attribution: cfg.attribution,
        maxZoom: cfg.maxZoom,
        subdomains: 'abc',
      }).addTo(map)
    }

    if (cfg.labelUrl) {
      labelRef.current = L.tileLayer(cfg.labelUrl, {
        maxZoom: cfg.maxZoom,
        subdomains: 'abcd',
      }).addTo(map)
    }
  }, [basemap, customBasemapInfo, map])

  return null
}

function ZoomControlBottom() {
  const map = useMap()
  useEffect(() => {
    L.control.zoom({ position: 'bottomright' }).addTo(map)
  }, [map])
  return null
}

function FitBounds({ bounds }) {
  const map = useMap()
  useEffect(() => {
    if (bounds) {
      map.fitBounds(bounds, { padding: [20, 20] })
    }
  }, [bounds, map])
  return null
}

export default function MapView() {
  const results = useStore(s => s.results)
  const taskId = useStore(s => s.taskId)
  const fileMetas = useStore(s => s.fileMetas)
  const kmlBounds = useRef(null)
  const [basemap, setBasemap] = useState('topo')
  const [customBasemapInfo, setCustomBasemapInfo] = useState(null)
  const [uploadingBasemap, setUploadingBasemap] = useState(false)

  useEffect(() => {
    const kmlMeta = fileMetas.find(f => f.slot === 'kml')
    if (kmlMeta?.bounds) {
      const [xmin, ymin, xmax, ymax] = kmlMeta.bounds
      kmlBounds.current = [[ymin, xmin], [ymax, xmax]]
    }
  }, [fileMetas])

  const handleBasemapUpload = async (file) => {
    setUploadingBasemap(true)
    try {
      const data = await api.uploadBasemap(file)
      setCustomBasemapInfo(data)
      setBasemap('custom')
      message.success('底图上传成功')
    } catch (err) {
      message.error(err.response?.data?.detail || '底图上传失败')
    } finally {
      setUploadingBasemap(false)
    }
    return false
  }

  const basemapOptions = [
    ...BASEMAPS.map(b => ({ value: b.key, label: b.label })),
  ]
  if (customBasemapInfo) {
    basemapOptions.push({ value: 'custom', label: '📐 自定义底图' })
  }

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <MapContainer
        center={[40, 110]}
        zoom={5}
        style={{ width: '100%', height: '100%' }}
        zoomControl={false}
      >
        <BasemapLayers basemap={basemap} customBasemapInfo={customBasemapInfo} />
        <ZoomControlBottom />
        {kmlBounds.current && basemap !== 'custom' && <FitBounds bounds={kmlBounds.current} />}

        {results?.layers && taskId && (
          <LayerControls layers={results.layers} taskId={taskId} />
        )}
      </MapContainer>

      {/* 底图切换 + 上传 */}
      <div style={{
        position: 'absolute', top: 10, left: 10, zIndex: 1000,
        display: 'flex', gap: 4, alignItems: 'center',
      }}>
        <div style={{
          background: 'rgba(255,255,255,0.9)', borderRadius: 4,
          padding: '2px 4px', boxShadow: '0 1px 4px rgba(0,0,0,0.2)',
        }}>
          <Select
            value={basemap}
            onChange={setBasemap}
            size="small"
            style={{ width: 150 }}
            options={basemapOptions}
          />
        </div>
        <div style={{
          background: 'rgba(255,255,255,0.9)', borderRadius: 4,
          padding: '2px 6px', boxShadow: '0 1px 4px rgba(0,0,0,0.2)',
        }}>
          <Dragger
            accept=".tif,.tiff,.jpg,.jpeg,.png"
            maxCount={1}
            showUploadList={false}
            beforeUpload={handleBasemapUpload}
            disabled={uploadingBasemap}
            style={{ padding: 0, background: 'transparent' }}
          >
            <Text style={{ fontSize: 12, color: '#555' }}>
              {uploadingBasemap ? '上传中...' : '上传底图'}
            </Text>
          </Dragger>
        </div>
      </div>

      {results?.layers && taskId && (
        <LayerControlPanel layers={results.layers} />
      )}
    </div>
  )
}
