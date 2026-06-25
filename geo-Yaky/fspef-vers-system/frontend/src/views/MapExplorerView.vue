<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as api from '../api'
import type { Substance } from '../types'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const props = defineProps<{ jobId: string }>()
const router = useRouter()
const mapContainer = ref<HTMLDivElement>()
let map: L.Map | null = null
let anomalyMarkers: L.Marker[] = []
const substances = ref<Substance[]>([])
const selectedSubstance = ref<string>('')
const summary = ref<any>(null)
const heatmapFeatures = ref<any[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const substanceColors: Record<string, string> = {
  gold: '#FFD700', silver: '#C0C0C0', copper: '#B87333', lead_zinc: '#7B8B6F',
  iron: '#A0522D', uranium: '#00FF7F', ree: '#9B59B6', lithium: '#E74C3C',
  tungsten: '#708090', tin: '#D4AF37',
  oil: '#8B4513', gas: '#FF6600', hydrogen: '#00AAFF', coal: '#2C3E50',
  fluorite: '#00CED1', water: '#0066FF', geothermal: '#FF4500',
}

const substanceNames: Record<string, string> = {
  gold: '金矿', silver: '银矿', copper: '铜矿', lead_zinc: '铅锌矿',
  iron: '铁矿', uranium: '铀矿', ree: '稀土矿', lithium: '锂矿',
  tungsten: '钨矿', tin: '锡矿',
  oil: '石油', gas: '天然气', hydrogen: '氢气', coal: '煤矿',
  fluorite: '萤石', water: '地下水', geothermal: '地热',
}

function buildMap() {
  if (!mapContainer.value || !heatmapFeatures.value.length) return

  // Destroy old map if exists
  if (map) { map.remove(); map = null }

  const feats = heatmapFeatures.value
  const lats = feats.map((f: any) => f.geometry.coordinates[1])
  const lons = feats.map((f: any) => f.geometry.coordinates[0])
  const center: [number, number] = [
    (Math.min(...lats) + Math.max(...lats)) / 2,
    (Math.min(...lons) + Math.max(...lons)) / 2,
  ]

  map = L.map(mapContainer.value).setView(center, 12)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
  }).addTo(map)

  addHeatmapLayer(feats)
  map.fitBounds(L.featureGroup().addLayer(L.circleMarker(center)).getBounds().pad(0.1))

  // Fit to actual data bounds
  const group = L.featureGroup()
  for (const f of feats) {
    if (f.properties.confidence > 0.3) {
      L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], { radius: 1 }).addTo(group)
    }
  }
  if (group.getLayers().length) {
    map.fitBounds(group.getBounds().pad(0.1))
  }

  // Add anomaly markers
  anomalyMarkers = []
  if (summary.value?.anomalies?.length) {
    for (const anom of summary.value.anomalies) {
      const color = substanceColors[anom.substance_id] || '#3b82f6'
      const name = substanceNames[anom.substance_id] || anom.substance_id
      const marker = L.marker([anom.center_lat, anom.center_lon])
        .bindPopup(`
          <div style="font-size:13px; min-width:160px;">
            <strong style="color:${color}">${name}</strong><br>
            置信度: ${(anom.confidence * 100).toFixed(1)}%<br>
            深度: ${anom.depth_mean?.toFixed(0) || '?'} m<br>
            面积: ${anom.area_m2?.toFixed(0) || '?'} m&sup2;
          </div>
        `)
        .addTo(map!)
      // Store substance_id on the marker for filtering
      ;(marker as any)._substance_id = anom.substance_id
      anomalyMarkers.push(marker)
    }
  }
}

function addHeatmapLayer(feats: any[]) {
  if (!map) return
  // Remove old circle markers (keep tile layer)
  map.eachLayer(l => {
    if (l instanceof L.CircleMarker) map!.removeLayer(l)
  })

  for (const feature of feats) {
    const [lon, lat] = feature.geometry.coordinates
    const conf = feature.properties.confidence
    const sid = feature.properties.substance_id
    const color = substanceColors[sid] || '#3b82f6'

    if (conf > 0.3) {
      const radius = Math.max(3, conf * 14)
      L.circleMarker([lat, lon], {
        radius,
        fillColor: color,
        fillOpacity: Math.min(conf * 0.8, 0.85),
        color: color,
        weight: 1,
        opacity: conf * 0.5,
      }).bindPopup(`
        <div style="font-size:12px;">
          <strong style="color:${color}">${substanceNames[sid] || sid}</strong><br>
          置信度: ${(conf * 100).toFixed(1)}%<br>
          坐标: ${lat.toFixed(4)}, ${lon.toFixed(4)}
        </div>
      `).addTo(map!)
    }
  }
}

onMounted(async () => {
  try {
    substances.value = await api.getSubstances()
    summary.value = await api.getResultSummary(props.jobId)
    const heatmapData = await api.getHeatmap(props.jobId)
    heatmapFeatures.value = heatmapData.features || []
  } catch (e: any) {
    error.value = '加载数据失败: ' + (e.message || e)
    console.error('[MapExplorer] load failed:', e)
  }

  loading.value = false

  // Wait for Vue to render the map container div (v-if becomes true)
  await nextTick()

  if (heatmapFeatures.value.length) {
    buildMap()
  }
})

onBeforeUnmount(() => { map?.remove() })

watch(selectedSubstance, async (sid) => {
  if (!map) return
  try {
    const data = await api.getHeatmap(props.jobId, sid || undefined)
    const feats = data.features || []
    if (sid) {
      const filtered = feats.filter((f: any) => {
        const topSub = f.properties.substance_id
        const subScore = f.properties.scores?.[sid] || 0
        return topSub === sid || subScore > 0.35
      })
      addHeatmapLayer(filtered)
    } else {
      addHeatmapLayer(feats)
    }

    // Filter anomaly markers by substance
    for (const marker of anomalyMarkers) {
      const markerSid = (marker as any)._substance_id
      if (sid && markerSid !== sid) {
        map!.removeLayer(marker)
      } else {
        if (!map!.hasLayer(marker)) {
          marker.addTo(map!)
        }
      }
    }
  } catch (e) {
    console.error('[MapExplorer] filter failed:', e)
  }
})
</script>

<template>
  <div>
    <router-link to="/" class="back-link">&larr; 返回仪表盘</router-link>
    <div class="page-header">
      <h2>异常区分布图</h2>
      <p>任务 {{ jobId.slice(0, 8) }}... — Leaflet 地图 + 异常热力图叠加</p>
    </div>

    <!-- Error banner -->
    <div v-if="error" class="card" style="border-color: var(--danger);">
      <div style="color: var(--danger); font-size: 13px;">{{ error }}</div>
      <button class="btn btn-secondary" style="margin-top: 8px; font-size: 12px;" @click="router.push('/analysis')">
        返回新建分析
      </button>
    </div>

    <!-- Toolbar -->
    <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center;">
      <button class="btn btn-secondary" @click="selectedSubstance = ''" :style="!selectedSubstance ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}">
        全部
      </button>
      <button v-for="s in substances" :key="s.id" class="btn btn-secondary"
        :style="selectedSubstance === s.id ? { borderColor: s.color, color: s.color } : {}"
        @click="selectedSubstance = s.id">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;" :style="{ background: s.color }"></span>
        {{ s.name }}
      </button>
      <span style="flex:1;"></span>
      <button class="btn btn-secondary" @click="router.push('/spectrum/' + jobId)">频谱分析</button>
      <button class="btn btn-secondary" @click="router.push('/model3d/' + jobId)">3D模型</button>
    </div>

    <!-- Map -->
    <div class="card">
      <div ref="mapContainer" class="map-container" v-if="!loading && !error"></div>
      <div v-else-if="loading" class="map-container" style="display: flex; align-items: center; justify-content: center; color: var(--text-dim);">
        加载分析结果中...
      </div>
    </div>

    <!-- Legend -->
    <div class="card" v-if="!loading && !error">
      <div style="display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px;">
        <div style="color: var(--text-dim);">图例：</div>
        <div v-for="(color, sid) in substanceColors" :key="sid" style="display: flex; align-items: center; gap: 4px;">
          <span style="display:inline-block;width:12px;height:12px;border-radius:50%;" :style="{ background: color }"></span>
          <span>{{ substanceNames[sid] || sid }}</span>
        </div>
        <span style="margin-left: 12px; color: var(--text-dim);">圆点越大 = 置信度越高</span>
      </div>
    </div>

    <!-- Anomaly table -->
    <div class="card" v-if="summary && summary.anomalies?.length">
      <div class="card-title">检测到的异常体 ({{ summary.anomalies.length }})</div>
      <table>
        <thead>
          <tr><th>物质</th><th>置信度</th><th>中心坐标</th><th>深度 (m)</th><th>面积 (m&sup2;)</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in summary.anomalies" :key="a.id">
            <td>
              <span :style="{ color: substanceColors[a.substance_id] || '#fff' }">
                {{ substanceNames[a.substance_id] || a.substance_id }}
              </span>
            </td>
            <td>{{ (a.confidence * 100).toFixed(1) }}%</td>
            <td>{{ a.center_lat.toFixed(4) }}, {{ a.center_lon.toFixed(4) }}</td>
            <td>{{ a.depth_mean?.toFixed(0) || '-' }}</td>
            <td>{{ a.area_m2?.toFixed(0) || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- No anomalies found -->
    <div class="card" v-if="!loading && summary && !summary.anomalies?.length">
      <div style="text-align: center; padding: 20px; color: var(--text-dim);">
        未检测到高置信度异常体。热力图显示的是各点的匹配分布。
      </div>
    </div>
  </div>
</template>
