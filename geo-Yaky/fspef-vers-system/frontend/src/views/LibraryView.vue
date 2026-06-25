<script setup lang="ts">
import { ref, onMounted } from 'vue'
import * as api from '../api'
import type { Substance, SpectralRef } from '../types'
import Plotly from 'plotly.js-dist-min'

const substances = ref<Substance[]>([])
const references = ref<SpectralRef[]>([])
const selectedRef = ref<SpectralRef | null>(null)
const loading = ref(true)
const selectedSubstance = ref<string>('')

onMounted(async () => {
  substances.value = await api.getSubstances()
  references.value = await api.getReferences()
  loading.value = false
})

async function filterBySubstance(sid: string) {
  selectedSubstance.value = sid
  references.value = await api.getReferences(sid)
}

function showRef(ref: SpectralRef) {
  selectedRef.value = ref
  const el = document.getElementById('ref-plot')
  if (!el) return

  const subColors: Record<string, string> = {
    oil: '#8B4513', gas: '#FF6600', hydrogen: '#00AAFF', gold: '#FFD700', water: '#0066FF',
  }

  Plotly.newPlot(el, [{
    x: ref.freq_data,
    y: ref.amp_data,
    type: 'scatter',
    mode: 'lines',
    name: ref.name,
    line: { color: subColors[ref.substance_id] || '#3b82f6', width: 2 },
    fill: 'tozeroy',
    fillcolor: `rgba(59, 130, 246, 0.1)`,
  }], {
    title: { text: ref.name, font: { color: '#e2e8f0', size: 13 } },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    xaxis: { title: '频率 (Hz)', gridcolor: '#1e293b', color: '#94a3b8' },
    yaxis: { title: '幅度', gridcolor: '#1e293b', color: '#94a3b8' },
    margin: { t: 40, b: 50, l: 60, r: 20 },
  }, { responsive: true })
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>参考光谱库</h2>
      <p>管理物质特征共振频率参考光谱</p>
    </div>

    <div style="display: flex; gap: 8px; margin-bottom: 16px;">
      <button class="btn btn-secondary" @click="filterBySubstance('')">全部</button>
      <button v-for="s in substances" :key="s.id" class="btn btn-secondary"
        :style="selectedSubstance === s.id ? { borderColor: s.color, color: s.color } : {}"
        @click="filterBySubstance(s.id)">
        {{ s.name }}
      </button>
    </div>

    <div class="grid grid-2">
      <div>
        <div class="card">
          <div class="card-title">参考光谱列表 ({{ references.length }})</div>
          <div v-for="ref in references" :key="ref.id"
            @click="showRef(ref)"
            style="padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; cursor: pointer; transition: all 0.15s;"
            :style="selectedRef?.id === ref.id ? { borderColor: 'var(--accent)' } : {}">
            <div style="font-size: 13px; font-weight: 600;">{{ ref.name }}</div>
            <div style="font-size: 11px; color: var(--text-dim); margin-top: 2px;">
              {{ ref.freq_min.toFixed(1) }}–{{ ref.freq_max.toFixed(1) }} Hz | {{ ref.n_points }} 点 | {{ ref.source }}
            </div>
          </div>
        </div>
      </div>

      <div>
        <div class="card">
          <div class="card-title">光谱详情</div>
          <div v-if="selectedRef">
            <div id="ref-plot" class="chart-container" style="min-height: 350px;"></div>
            <div style="margin-top: 12px; font-size: 12px; color: var(--text-dim); line-height: 1.8;">
              <div><strong>名称:</strong> {{ selectedRef.name }}</div>
              <div><strong>物质:</strong> {{ selectedRef.substance_id }}</div>
              <div><strong>数据点:</strong> {{ selectedRef.n_points }}</div>
              <div><strong>频率范围:</strong> {{ selectedRef.freq_min.toFixed(2) }}–{{ selectedRef.freq_max.toFixed(2) }} Hz</div>
              <div><strong>来源:</strong> {{ selectedRef.source || '未指定' }}</div>
            </div>
          </div>
          <div v-else class="empty-state" style="padding: 40px;">
            <div class="icon">📐</div>
            <div>点击左侧光谱查看详情</div>
          </div>
        </div>

        <div class="card">
          <div class="card-title">物质参数配置</div>
          <table>
            <thead>
              <tr><th>物质</th><th>频率范围</th><th>C_eq</th><th>阈值</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in substances" :key="s.id">
                <td><span :style="{ color: s.color }">{{ s.name }}</span></td>
                <td>{{ s.freq_min }}–{{ s.freq_max }} Hz</td>
                <td>{{ s.c_equivalent }} m/s</td>
                <td>{{ s.threshold }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>
