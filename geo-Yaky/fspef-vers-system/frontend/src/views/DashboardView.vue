<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAnalysisStore } from '../stores/analysis'

const router = useRouter()
const store = useAnalysisStore()
const recentJobs = ref<any[]>([])

function toBJT(isoStr: string): string {
  const d = new Date(isoStr)
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

onMounted(async () => {
  await store.loadSubstances()
  try {
    recentJobs.value = await import('../api').then(m => m.listJobs())
  } catch {}
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>FSPEF-VERS 频率共振分析系统</h2>
      <p>基于 Yakymchuk 频率共振直接勘探技术 — 从"找构造"到"直接找物质"</p>
    </div>

    <div class="grid grid-4" style="margin-bottom: 24px;">
      <div class="stat-card" v-for="s in store.substances" :key="s.id">
        <div class="stat-label">{{ s.name }}</div>
        <div class="stat-value" :style="{ color: s.color }">
          {{ s.freq_min }}–{{ s.freq_max }} Hz
        </div>
        <div style="font-size: 11px; color: var(--text-dim); margin-top: 4px;">
          C = {{ s.c_equivalent }} m/s
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">快速开始</div>
      <p style="font-size: 13px; color: var(--text-dim); margin-bottom: 16px;">
        选择目标物质并运行演示分析，体验完整的 8 步频率共振处理流程。
      </p>
      <button class="btn btn-primary" @click="router.push('/analysis')">
        新建分析任务
      </button>
    </div>

    <div class="card" v-if="recentJobs.length > 0">
      <div class="card-title">最近分析</div>
      <table>
        <thead>
          <tr><th>任务ID</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="job in recentJobs.slice(0, 10)" :key="job.id">
            <td style="font-family: monospace; font-size: 12px;">{{ job.id.slice(0, 8) }}...</td>
            <td><span class="badge" :class="'badge-' + job.status">{{ job.status }}</span></td>
            <td>
              <div class="progress-bar" style="width: 100px;">
                <div class="progress-bar-fill" :style="{ width: job.percent + '%' }"></div>
              </div>
            </td>
            <td style="color: var(--text-dim); font-size: 12px;">{{ toBJT(job.created_at) }}</td>
            <td>
              <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;"
                @click="router.push('/map/' + job.id)" v-if="job.status === 'completed'">
                查看结果
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-title">系统架构</div>
      <div style="font-size: 13px; color: var(--text-dim); line-height: 2;">
        <div v-for="(label, i) in ['预处理 → 频域变换(FFT/CWT)', '特征提取 → 光谱匹配', '物质识别 → 深度换算(H=C/2f)', '异常分割 → 3D建模(Kriging)']" :key="i">
          <strong style="color: var(--accent);">Stage {{ i*2+1 }}-{{ i*2+2 }}:</strong> {{ label }}
        </div>
      </div>
    </div>
  </div>
</template>
