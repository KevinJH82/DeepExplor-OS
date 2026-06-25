import api from './client'
import type { Job, JobProgress, Substance, SpectralRef } from '../types'

export async function getSubstances(): Promise<Substance[]> {
  const { data } = await api.get('/library/substances')
  return data
}

export async function getReferences(substance?: string): Promise<SpectralRef[]> {
  const { data } = await api.get('/library/references', { params: { substance } })
  return data
}

export async function createJob(targetSubstances: string[], demoMode: boolean = true): Promise<Job> {
  const { data } = await api.post('/jobs/', {
    target_substances: targetSubstances,
    demo_mode: demoMode,
    parameters: {},
  })
  return data
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await api.get(`/jobs/${jobId}`)
  return data
}

export async function listJobs(): Promise<Job[]> {
  const { data } = await api.get('/jobs/')
  return data
}

export async function getResultSummary(jobId: string) {
  const { data } = await api.get(`/results/${jobId}/summary`)
  return data
}

export async function getHeatmap(jobId: string, substance?: string) {
  const { data } = await api.get(`/results/${jobId}/heatmap`, { params: { substance } })
  return data
}

export async function getSpectrum(jobId: string) {
  const { data } = await api.get(`/results/${jobId}/spectrum`)
  return data
}

export async function getModel3D(jobId: string, substance?: string) {
  const { data } = await api.get(`/results/${jobId}/model3d`, { params: { substance } })
  return data
}

export async function getJobSubstances(jobId: string) {
  const { data } = await api.get(`/results/${jobId}/substances`)
  return data
}

export function createProgressSocket(jobId: string): WebSocket {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(`${proto}//${window.location.host}/ws/jobs/ws/${jobId}`)
}
