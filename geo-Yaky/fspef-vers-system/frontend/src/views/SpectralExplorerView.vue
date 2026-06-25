<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import * as api from '../api'
import Plotly from 'plotly.js-dist-min'

const props = defineProps<{ jobId: string }>()
const loading = ref(true)
const spectrumData = ref<any>(null)
const refSpectra = ref<any[]>([])

onMounted(async () => {
  try {
    spectrumData.value = await api.getSpectrum(props.jobId)
    refSpectra.value = await api.getReferences()
  } catch (e) {
    console.error('[Spectrum] load failed:', e)
  }
  loading.value = false

  // Wait for Vue to render the v-else template with #spectrum-plot
  await nextTick()

  if (spectrumData.value && spectrumData.value.frequencies?.length) {
    const freqs = spectrumData.value.frequencies
    const amps = spectrumData.value.amplitudes

    const traces: any[] = [{
      x: freqs,
      y: amps,
      type: 'scatter',
      mode: 'lines',
      name: '观测频谱',
      line: { color: '#3b82f6', width: 2 },
    }]

    // Overlay reference spectra (first of each substance)
    const addedSubstances = new Set<string>()
    for (const ref of refSpectra.value) {
      if (!addedSubstances.has(ref.substance_id)) {
        addedSubstances.add(ref.substance_id)
        const subColors: Record<string, string> = {
          oil: '#8B4513', gas: '#FF6600', hydrogen: '#00AAFF', gold: '#FFD700', water: '#0066FF',
	          silver: '#C0C0C0', copper: '#B87333', lead_zinc: '#7B8B6F', iron: '#A0522D',
	          uranium: '#00FF7F', ree: '#9B59B6', lithium: '#E74C3C', tungsten: '#708090',
	          tin: '#D4AF37', coal: '#2C3E50', fluorite: '#00CED1', geothermal: '#FF4500',
        }
        traces.push({
          x: ref.freq_data.slice(0, 200),
          y: ref.amp_data.slice(0, 200),
          type: 'scatter',
          mode: 'lines',
          name: ref.substance_id + ' 参考',
          line: { color: subColors[ref.substance_id] || '#888', width: 1, dash: 'dash' },
        })
      }
    }

    const el = document.getElementById('spectrum-plot')
    if (el) {
      Plotly.newPlot(el, traces, {
        title: { text: '频谱分析 — 观测 vs 参考', font: { color: '#e2e8f0', size: 14 } },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        xaxis: { title: '频率 (Hz)', gridcolor: '#1e293b', color: '#94a3b8' },
        yaxis: { title: '幅度', gridcolor: '#1e293b', color: '#94a3b8' },
        legend: { font: { color: '#94a3b8' } },
        margin: { t: 40, b: 50, l: 60, r: 20 },
      }, { responsive: true })
    }

    // Power spectrum (squared amplitudes)
    const psdEl = document.getElementById('psd-plot')
    if (psdEl) {
      Plotly.newPlot(psdEl, [{
        x: freqs,
        y: amps.map((a: number) => a * a),
        type: 'scatter',
        fill: 'tozeroy',
        name: '功率谱密度',
        line: { color: '#818cf8', width: 1 },
        fillcolor: 'rgba(129, 140, 248, 0.15)',
      }], {
        title: { text: '功率谱密度 (PSD)', font: { color: '#e2e8f0', size: 14 } },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        xaxis: { title: '频率 (Hz)', gridcolor: '#1e293b', color: '#94a3b8' },
        yaxis: { title: '功率', gridcolor: '#1e293b', color: '#94a3b8', type: 'log' },
        margin: { t: 40, b: 50, l: 60, r: 20 },
      }, { responsive: true })
    }
  }
})
</script>

<template>
  <div>
    <router-link to="/" class="back-link">← 返回</router-link>
    <div class="page-header">
      <h2>频谱分析</h2>
      <p>任务 {{ jobId.slice(0, 8) }}... — FFT 频谱与参考光谱对比</p>
    </div>

    <div v-if="loading" class="empty-state">
      <div class="icon">📊</div>
      <div>加载频谱数据...</div>
    </div>

    <template v-else>
      <div class="card">
        <div class="card-title">观测频谱 vs 参考光谱</div>
        <div id="spectrum-plot" class="chart-container"></div>
      </div>

      <div class="card">
        <div class="card-title">功率谱密度</div>
        <div id="psd-plot" class="chart-container"></div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">深度公式</div>
          <div style="font-size: 24px; font-weight: 700; color: var(--accent); text-align: center; padding: 20px;">
            H = C / (2f)
          </div>
          <p style="font-size: 12px; color: var(--text-dim); text-align: center;">
            当探测频率的半波长等于目标深度时，驻波干涉最强
          </p>
        </div>
        <div class="card">
          <div class="card-title">参考光谱库</div>
          <div v-for="ref in refSpectra" :key="ref.id" style="padding: 6px 0; font-size: 12px; border-bottom: 1px solid var(--border);">
            <span :style="{ color: ({
              gold: '#FFD700', silver: '#C0C0C0', copper: '#B87333', lead_zinc: '#7B8B6F',
              iron: '#A0522D', uranium: '#00FF7F', ree: '#9B59B6', lithium: '#E74C3C',
              tungsten: '#708090', tin: '#D4AF37',
              oil: '#8B4513', gas: '#FF6600', hydrogen: '#00AAFF', coal: '#2C3E50',
              fluorite: '#00CED1', water: '#0066FF', geothermal: '#FF4500',
            } as any)[ref.substance_id] || '#888' }">
              {{ ref.substance_id }}
            </span>
            — {{ ref.name }} ({{ ref.freq_min.toFixed(1) }}–{{ ref.freq_max.toFixed(1) }} Hz)
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
