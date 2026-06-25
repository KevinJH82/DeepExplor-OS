<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'

const METAL_IDS = ['gold', 'silver', 'copper', 'lead_zinc', 'iron', 'uranium']
const CRITICAL_IDS = ['ree', 'lithium', 'tungsten', 'tin']
const ENERGY_IDS = ['oil', 'gas', 'hydrogen', 'coal', 'fluorite', 'water', 'geothermal']
import { useRouter } from 'vue-router'
import { useAnalysisStore } from '../stores/analysis'
import { STAGE_LABELS } from '../types'
import api from '../api/client'

const router = useRouter()
const store = useAnalysisStore()

// Mode: 'demo' | 'real'
const mode = ref<'demo' | 'real'>('demo')
const selectedSubstances = ref<string[]>(['oil', 'gas'])
const stageList = Array.from({ length: 8 }, (_, i) => i + 1)

// Real mode: upload state
const uploadFile = ref<File | null>(null)
const uploadId = ref<string | null>(null)
const uploadType = ref<string>('csv')
const uploadError = ref<string | null>(null)
const uploadProgress = ref('')
const uploadPreview = ref<string>('')

onMounted(() => { store.loadSubstances() })

const metalSubstances = computed(() => store.substances.filter((s: any) => METAL_IDS.includes(s.id)))
const criticalSubstances = computed(() => store.substances.filter((s: any) => CRITICAL_IDS.includes(s.id)))
const energySubstances = computed(() => store.substances.filter((s: any) => ENERGY_IDS.includes(s.id)))

function toggleSubstance(id: string) {
  const idx = selectedSubstances.value.indexOf(id)
  if (idx >= 0) selectedSubstances.value.splice(idx, 1)
  else selectedSubstances.value.push(id)
}

const canStart = computed(() => {
  if (selectedSubstances.value.length === 0 || store.loading) return false
  if (mode.value === 'real' && !uploadId.value) return false
  return true
})

async function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploadFile.value = file
  uploadError.value = null
  uploadPreview.value = ''

  // Detect type
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  if (['tif', 'tiff'].includes(ext)) uploadType.value = 'geotiff'
  else if (ext === 'csv') uploadType.value = 'csv'
  else {
    uploadError.value = '仅支持 CSV 和 GeoTIFF (.tif) 格式'
    return
  }

  // Upload to backend
  const formData = new FormData()
  formData.append('file', file)
  formData.append('survey_type', mode.value === 'demo' ? 'demo' : 'real')

  uploadProgress.value = '上传中...'
  try {
    const { data } = await api.post('/uploads/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    uploadId.value = data.id
    uploadProgress.value = `已上传: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`

    // Preview CSV content
    if (ext === 'csv') {
      const text = await file.text()
      const lines = text.trim().split('\n').slice(0, 6)
      uploadPreview.value = lines.join('\n')
    }
  } catch (e: any) {
    uploadError.value = '上传失败: ' + (e.response?.data?.detail || e.message)
    uploadProgress.value = ''
  }
}

async function startAnalysis() {
  store.error = null
  store.loading = true
  try {
    const payload: any = {
      target_substances: selectedSubstances.value,
      demo_mode: mode.value === 'demo',
      parameters: {},
    }
    if (mode.value === 'real' && uploadId.value) {
      payload.upload_id = uploadId.value
    }
    const { data } = await api.post('/jobs/', payload)
    store.currentJob = data
    store.connectProgress(data.id)
    startPolling()
  } catch (e: any) {
    store.error = '启动失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    store.loading = false
  }
}

// Polling fallback
let pollTimer: ReturnType<typeof setInterval> | null = null
function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    if (store.currentJob && store.currentJob.status === 'running') {
      await store.loadJob(store.currentJob.id)
    }
    if (store.currentJob && ['completed', 'failed'].includes(store.currentJob.status)) {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    }
  }, 3000)
}
watch(() => store.currentJob, (job) => {
  if (job?.status === 'completed' && pollTimer) { clearInterval(pollTimer); pollTimer = null }
})

function stageStatus(s: number): string {
  if (!store.currentJob) return 'pending'
  if (store.currentJob.status === 'completed') return 'completed'
  if (store.currentJob.current_stage > s) return 'completed'
  if (store.currentJob.current_stage === s) return 'active'
  return 'pending'
}

function downloadTemplate(type: string) {
  const templates: Record<string, string> = {
    spectral: 'latitude,longitude,freq_0.5Hz,freq_1.0Hz,freq_1.5Hz,freq_2.0Hz,freq_2.5Hz,freq_3.0Hz\n55.0123,73.0456,0.023,0.145,0.067,0.034,0.012,0.008\n55.0124,73.0457,0.019,0.152,0.071,0.038,0.015,0.009',
    vers: 'depth_m,freq_0.5Hz,freq_1.0Hz,freq_1.5Hz,freq_2.0Hz,freq_2.5Hz,freq_3.0Hz\n0,0.012,0.089,0.045,0.023,0.011,0.006\n1,0.015,0.102,0.051,0.027,0.013,0.007\n2,0.018,0.134,0.067,0.034,0.016,0.009',
    reference: 'frequency,amplitude\n0.5,0.012\n1.0,0.045\n1.5,0.089\n2.0,0.134\n2.5,0.098\n3.0,0.056',
  }
  const blob = new Blob([templates[type] || templates.spectral], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `template_${type}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>新建分析任务</h2>
      <p>配置目标物质并启动频率共振分析流水线</p>
    </div>

    <!-- Error -->
    <div v-if="store.error" class="card" style="border-color: var(--danger); margin-bottom: 16px;">
      <div style="color: var(--danger); font-size: 13px;">{{ store.error }}</div>
    </div>

    <div class="grid grid-2">
      <!-- Left column: configuration -->
      <div>
        <!-- Mode switcher -->
        <div class="card">
          <div class="card-title">分析模式</div>
          <div style="display: flex; gap: 0; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;">
            <button @click="mode = 'demo'"
              :style="{
                flex: 1, padding: '10px', border: 'none', cursor: 'pointer',
                fontSize: '13px', fontWeight: 600,
                background: mode === 'demo' ? 'var(--accent)' : 'var(--bg-card)',
                color: mode === 'demo' ? '#fff' : 'var(--text-dim)',
              }">
              演示模式
            </button>
            <button @click="mode = 'real'"
              :style="{
                flex: 1, padding: '10px', border: 'none', cursor: 'pointer',
                fontSize: '13px', fontWeight: 600,
                background: mode === 'real' ? 'var(--accent)' : 'var(--bg-card)',
                color: mode === 'real' ? '#fff' : 'var(--text-dim)',
              }">
              真实数据
            </button>
          </div>
          <p style="font-size: 12px; color: var(--text-dim); margin-top: 8px;">
            <template v-if="mode === 'demo'">系统自动生成合成探测数据，用于体验完整分析流程</template>
            <template v-else>上传真实的 FSPEF/VERS 测量数据或卫星影像进行分析</template>
          </p>
        </div>

        <!-- Demo mode info -->
        <div class="card" v-if="mode === 'demo'">
          <div class="card-title">演示参数</div>
          <div style="font-size: 13px; color: var(--text-dim); line-height: 2;">
            <div><strong>网格:</strong> 50 x 50 测点</div>
            <div><strong>采样率:</strong> 50 Hz</div>
            <div><strong>频段:</strong> 0.1 – 25 Hz</div>
            <div><strong>深度公式:</strong> H = C / (2f)</div>
            <div><strong>匹配方法:</strong> 余弦相似度</div>
          </div>
        </div>

        <!-- Real mode: upload -->
        <div class="card" v-if="mode === 'real'">
          <div class="card-title">数据上传</div>

          <div style="border: 2px dashed var(--border); border-radius: 12px; padding: 32px; text-align: center; transition: all 0.15s; cursor: pointer;"
            @dragover.prevent @drop.prevent="($event: any) => { const f = $event.dataTransfer?.files[0]; if (f) { ($refs.fileInput as any).files = $event.dataTransfer?.files; handleFileUpload({ target: { files: [f] } } as any) } }">

            <div style="font-size: 28px; margin-bottom: 8px;">&#128196;</div>
            <div style="font-size: 14px; font-weight: 600; margin-bottom: 4px;">拖拽文件到此处或点击选择</div>
            <div style="font-size: 12px; color: var(--text-dim);">
              支持 CSV（频谱/VERS）和 GeoTIFF（.tif），最大 100MB
            </div>
            <input ref="fileInput" type="file" accept=".csv,.tif,.tiff"
              @change="handleFileUpload($event)"
              style="display: none;">
            <button class="btn btn-secondary" style="margin-top: 12px; font-size: 12px;"
              @click="($refs.fileInput as any)?.click()">
              选择文件
            </button>
          </div>

          <!-- Upload status -->
          <div v-if="uploadProgress" style="margin-top: 12px; font-size: 13px; color: var(--success);">
            &#10003; {{ uploadProgress }}
          </div>
          <div v-if="uploadError" style="margin-top: 12px; font-size: 13px; color: var(--danger);">
            {{ uploadError }}
          </div>

          <!-- CSV preview -->
          <div v-if="uploadPreview" style="margin-top: 12px;">
            <div style="font-size: 11px; color: var(--text-dim); margin-bottom: 4px;">数据预览（前5行）:</div>
            <pre style="font-size: 11px; background: var(--bg); padding: 8px; border-radius: 6px; overflow-x: auto; color: var(--text-dim); max-height: 120px;">{{ uploadPreview }}</pre>
          </div>

          <!-- Data format help -->
          <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border);">
            <div style="font-size: 12px; font-weight: 600; margin-bottom: 8px;">数据格式说明</div>
            <div style="font-size: 11px; color: var(--text-dim); line-height: 1.8;">
              <div><strong>地面频谱 CSV:</strong> 每行一个测点，列 = latitude, longitude, freq_0.5Hz, freq_1.0Hz, ...</div>
              <div><strong>VERS 深度 CSV:</strong> 每行一个深度层，列 = depth_m, freq_0.5Hz, freq_1.0Hz, ...</div>
              <div><strong>卫星影像:</strong> GeoTIFF (.tif)，含地理坐标</div>
            </div>
            <div style="display: flex; gap: 8px; margin-top: 8px;">
              <button class="btn btn-secondary" style="font-size: 11px; padding: 4px 10px;" @click="downloadTemplate('spectral')">下载频谱模板</button>
              <button class="btn btn-secondary" style="font-size: 11px; padding: 4px 10px;" @click="downloadTemplate('vers')">下载VERS模板</button>
              <button class="btn btn-secondary" style="font-size: 11px; padding: 4px 10px;" @click="downloadTemplate('reference')">下载参考光谱模板</button>
            </div>
          </div>
        </div>

        <!-- Substance selection (shared) -->
        <div class="card">
          <div class="card-title">选择目标矿种</div>
          <!-- Metals -->
          <div style="font-size: 11px; color: var(--text-dim); margin-bottom: 4px; font-weight: 600;">金属矿产</div>
          <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px;">
            <div v-for="s in metalSubstances" :key="s.id"
              class="substance-chip"
              :class="{ active: selectedSubstances.includes(s.id) }"
              :style="{ color: s.color }"
              @click="toggleSubstance(s.id)">
              <span class="substance-dot" :style="{ background: s.color }"></span>
              {{ s.name }}
            </div>
          </div>
          <!-- Critical Minerals -->
          <div style="font-size: 11px; color: var(--text-dim); margin-bottom: 4px; font-weight: 600;">关键矿产</div>
          <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px;">
            <div v-for="s in criticalSubstances" :key="s.id"
              class="substance-chip"
              :class="{ active: selectedSubstances.includes(s.id) }"
              :style="{ color: s.color }"
              @click="toggleSubstance(s.id)">
              <span class="substance-dot" :style="{ background: s.color }"></span>
              {{ s.name }}
            </div>
          </div>
          <!-- Energy & Water -->
          <div style="font-size: 11px; color: var(--text-dim); margin-bottom: 4px; font-weight: 600;">能源 / 水 / 其他</div>
          <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px;">
            <div v-for="s in energySubstances" :key="s.id"
              class="substance-chip"
              :class="{ active: selectedSubstances.includes(s.id) }"
              :style="{ color: s.color }"
              @click="toggleSubstance(s.id)">
              <span class="substance-dot" :style="{ background: s.color }"></span>
              {{ s.name }}
            </div>
          </div>
          <p style="font-size: 12px; color: var(--text-dim);">已选 {{ selectedSubstances.length }} 种</p>
        </div>

        <!-- Start button -->
        <button class="btn btn-primary" :disabled="!canStart" @click="startAnalysis"
          style="width: 100%; padding: 12px; font-size: 14px;">
          <template v-if="store.loading">正在创建任务...</template>
          <template v-else-if="store.currentJob?.status === 'running'">分析进行中...</template>
          <template v-else>{{ mode === 'demo' ? '启动演示分析' : '启动真实数据分析' }}</template>
        </button>

        <div v-if="store.currentJob?.status === 'completed'" style="margin-top: 12px;">
          <button class="btn btn-success" @click="router.push('/map/' + store.currentJob.id)"
            style="width: 100%; padding: 12px;">
            查看分析结果 &#8594;
          </button>
        </div>
      </div>

      <!-- Right column: pipeline progress -->
      <div>
        <div class="card">
          <div class="card-title">
            8 步处理流水线
            <span v-if="store.currentJob" class="badge"
              :class="'badge-' + store.currentJob.status" style="margin-left: 8px;">
              {{ store.currentJob.status === 'queued' ? '排队中' : store.currentJob.status === 'running' ? '运行中' : store.currentJob.status === 'completed' ? '已完成' : store.currentJob.status === 'failed' ? '失败' : store.currentJob.status }}
            </span>
          </div>

          <div class="pipeline-stages">
            <div v-for="s in stageList" :key="s" class="stage-item" :class="stageStatus(s)">
              <span class="stage-icon">
                <template v-if="stageStatus(s) === 'completed'">&#10003;</template>
                <template v-else-if="stageStatus(s) === 'active'">&#10227;</template>
                <template v-else>&#9675;</template>
              </span>
              <span>Stage {{ s }}: {{ STAGE_LABELS[s] }}</span>
            </div>
          </div>

          <div v-if="store.currentJob && store.currentJob.percent > 0" style="margin-top: 16px;">
            <div style="display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px;">
              <span>Stage {{ store.currentJob.current_stage }} / 8</span>
              <span>{{ Math.round(store.currentJob.percent) }}%</span>
            </div>
            <div class="progress-bar">
              <div class="progress-bar-fill" :style="{ width: store.currentJob.percent + '%' }"></div>
            </div>
          </div>
        </div>

        <div class="card" v-if="store.currentJob?.status === 'failed'">
          <div class="card-title" style="color: var(--danger);">错误信息</div>
          <pre style="font-size: 12px; color: var(--danger); white-space: pre-wrap;">{{ store.currentJob.error_message }}</pre>
        </div>

        <!-- Theory reference -->
        <div class="card">
          <div class="card-title">核心公式</div>
          <div style="text-align: center; padding: 16px 0;">
            <div style="font-size: 24px; font-weight: 700; color: var(--accent);">H = C / (2f)</div>
          </div>
          <div style="font-size: 11px; color: var(--text-dim); line-height: 1.8;">
            <div><strong>H</strong> = 目标体埋深 (m)</div>
            <div><strong>C</strong> = 等效波速 (m/s)，因物质和地质条件而异</div>
            <div><strong>f</strong> = 共振频率 (Hz)，由 VERS 接收分析获得</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
