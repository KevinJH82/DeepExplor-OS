import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue') },
  { path: '/analysis', name: 'analysis', component: () => import('../views/AnalysisView.vue') },
  { path: '/map/:jobId', name: 'map', component: () => import('../views/MapExplorerView.vue'), props: true },
  { path: '/spectrum/:jobId', name: 'spectrum', component: () => import('../views/SpectralExplorerView.vue'), props: true },
  { path: '/model3d/:jobId', name: 'model3d', component: () => import('../views/Model3DView.vue'), props: true },
  { path: '/library', name: 'library', component: () => import('../views/LibraryView.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
