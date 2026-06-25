export interface Substance {
  id: string
  name: string
  description: string | null
  freq_min: number
  freq_max: number
  c_equivalent: number
  threshold: number
  color: string
}

export interface Job {
  id: string
  upload_id: string | null
  status: string
  current_stage: number
  percent: number
  target_substances: string
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface Anomaly {
  id: string
  substance_id: string
  center_lat: number
  center_lon: number
  depth_min: number
  depth_max: number | null
  depth_mean: number | null
  confidence: number
  area_m2: number | null
  volume_m3: number | null
  geometry_json: string
}

export interface HeatmapPoint {
  lat: number
  lon: number
  substance_id: string
  confidence: number
  scores: Record<string, number>
}

export interface Model3D {
  substance_id: string
  vertices: number[][]
  faces: number[][]
  normals: number[][]
  bounds: Record<string, number>
  volume_m3: number | null
}

export interface SpectralRef {
  id: string
  substance_id: string
  name: string
  description: string | null
  source: string | null
  freq_data: number[]
  amp_data: number[]
  n_points: number
  freq_min: number
  freq_max: number
}

export interface JobProgress {
  stage: number
  stage_name: string
  percent: number
  message: string
}

export const STAGE_LABELS: Record<number, string> = {
  1: '预处理（去噪/校正）',
  2: '频域变换（FFT/CWT）',
  3: '特征提取（峰值/Q因子）',
  4: '光谱匹配（参考库对比）',
  5: '物质识别（分类/置信度）',
  6: '深度换算（H=C/2f）',
  7: '异常分割（连通域分析）',
  8: '3D建模（Kriging/等值面）',
}
