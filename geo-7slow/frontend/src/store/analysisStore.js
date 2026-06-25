import { create } from 'zustand'

const useStore = create((set, get) => ({
  // 上传状态
  uploadId: null,
  fileMap: {},
  fileMetas: [],
  matchResult: null,
  geologicContext: null,
  selectedMineral: null,
  selectedDepositType: null,
  setUploadId: (id) => set({ uploadId: id }),
  setFileMap: (map) => set({ fileMap: map }),
  setFileMetas: (metas) => set({ fileMetas: metas }),
  setMatchResult: (result) => set({ matchResult: result }),
  setGeologicContext: (context) => set({ geologicContext: context }),
  setSelectedMineral: (mineral) => set({ selectedMineral: mineral }),
  setSelectedDepositType: (depositType) => set({ selectedDepositType: depositType }),
  clearMatchResult: () => set({ matchResult: null }),

  // 分析任务
  taskId: null,
  taskStatus: 'idle', // idle | queued | running | completed | failed
  taskProgress: 0,
  taskStep: '',
  taskError: null,
  setTask: (taskId) => set({ taskId }),
  updateTask: (data) => set({
    taskStatus: data.status || get().taskStatus,
    taskProgress: data.progress ?? get().taskProgress,
    taskStep: data.current_step || get().taskStep,
    taskError: data.error || null,
  }),
  resetTask: () => set({
    taskId: null, taskStatus: 'idle', taskProgress: 0,
    taskStep: '', taskError: null, results: null, matchResult: null,
    geologicContext: null, selectedMineral: null, selectedDepositType: null,
  }),

  // 分析结果
  results: null,
  setResults: (results) => set({ results }),

  // 图层控制
  activeLayers: {},
  layerOpacity: {},
  layerColormap: {},
  toggleLayer: (name) => set((s) => {
    const active = { ...s.activeLayers }
    active[name] = !active[name]
    return { activeLayers: active }
  }),
  setLayerOpacity: (name, val) => set((s) => ({
    layerOpacity: { ...s.layerOpacity, [name]: val },
  })),
}))

export default useStore
