import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Job, JobProgress, Substance } from '../types'
import * as api from '../api'

export const useAnalysisStore = defineStore('analysis', () => {
  const substances = ref<Substance[]>([])
  const currentJob = ref<Job | null>(null)
  const progress = ref<JobProgress>({ stage: 0, stage_name: '', percent: 0, message: '' })
  const jobs = ref<Job[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function loadSubstances() {
    try {
      substances.value = await api.getSubstances()
    } catch (e: any) {
      console.error('[Store] loadSubstances failed:', e)
      error.value = '无法加载物质列表: ' + (e.message || e)
    }
  }

  async function startAnalysis(targetSubstances: string[], demoMode = true) {
    loading.value = true
    error.value = null
    try {
      console.log('[Store] Creating job for substances:', targetSubstances)
      currentJob.value = await api.createJob(targetSubstances, demoMode)
      console.log('[Store] Job created:', currentJob.value.id)
      connectProgress(currentJob.value.id)
    } catch (e: any) {
      console.error('[Store] startAnalysis failed:', e)
      error.value = '启动分析失败: ' + (e.message || e)
    } finally {
      loading.value = false
    }
  }

  function connectProgress(jobId: string) {
    const ws = api.createProgressSocket(jobId)
    ws.onopen = () => {
      console.log('[Store] WebSocket connected for job:', jobId)
    }
    ws.onerror = (e) => {
      console.warn('[Store] WebSocket error, falling back to polling:', e)
      // Fallback: poll every 3 seconds
      const interval = setInterval(async () => {
        try {
          currentJob.value = await api.getJob(jobId)
          const j = currentJob.value
          progress.value = {
            stage: j.current_stage,
            stage_name: String(j.current_stage),
            percent: j.percent,
            message: '',
          }
          if (j.status === 'completed' || j.status === 'failed') {
            clearInterval(interval)
          }
        } catch {
          clearInterval(interval)
        }
      }, 3000)
    }
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      progress.value = data
      if (data.stage_name === 'complete' || data.stage_name === 'error') {
        ws.close()
        loadJob(jobId)
      }
    }
  }

  async function loadJob(jobId: string) {
    try {
      currentJob.value = await api.getJob(jobId)
    } catch (e: any) {
      console.error('[Store] loadJob failed:', e)
    }
  }

  async function loadJobs() {
    try {
      jobs.value = await api.listJobs()
    } catch (e: any) {
      console.error('[Store] loadJobs failed:', e)
    }
  }

  return { substances, currentJob, progress, jobs, loading, error, loadSubstances, startAnalysis, connectProgress, loadJob, loadJobs }
})
