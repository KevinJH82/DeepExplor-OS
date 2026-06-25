<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as api from '../api'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

const props = defineProps<{ jobId: string }>()
const router = useRouter()
const container = ref<HTMLDivElement>()
const loading = ref(true)
const error = ref<string | null>(null)
const modelInfo = ref<any>(null)

let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: OrbitControls | null = null
let animId = 0

const substanceColors: Record<string, number> = {
  gold: 0xFFD700, silver: 0xC0C0C0, copper: 0xB87333, lead_zinc: 0x7B8B6F,
  iron: 0xA0522D, uranium: 0x00FF7F, ree: 0x9B59B6, lithium: 0xE74C3C,
  tungsten: 0x708090, tin: 0xD4AF37,
  oil: 0x8B4513, gas: 0xFF6600, hydrogen: 0x00AAFF, coal: 0x2C3E50,
  fluorite: 0x00CED1, water: 0x0066FF, geothermal: 0xFF4500,
}
const substanceNames: Record<string, string> = {
  gold: '金矿', silver: '银矿', copper: '铜矿', lead_zinc: '铅锌矿',
  iron: '铁矿', uranium: '铀矿', ree: '稀土矿', lithium: '锂矿',
  tungsten: '钨矿', tin: '锡矿',
  oil: '石油', gas: '天然气', hydrogen: '氢气', coal: '煤矿',
  fluorite: '萤石', water: '地下水', geothermal: '地热',
}

function initThree(models: any[]) {
  const el = container.value
  if (!el) {
    console.error('[Model3D] container ref is null')
    return
  }

  const w = el.clientWidth || el.parentElement?.clientWidth || 800
  const h = el.clientHeight || 600
  console.log(`[Model3D] initThree: ${w}x${h}, models: ${models.length}`)

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x0a0e17)

  camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 10000)

  renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setSize(w, h)
  renderer.setPixelRatio(window.devicePixelRatio)
  el.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true

  // Lighting
  scene.add(new THREE.AmbientLight(0x606080, 2.0))
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.5)
  dirLight.position.set(20, 40, 20)
  scene.add(dirLight)
  const dirLight2 = new THREE.DirectionalLight(0x8888ff, 0.5)
  dirLight2.position.set(-20, 10, -20)
  scene.add(dirLight2)

  // Grid
  scene.add(new THREE.GridHelper(100, 50, 0x1e293b, 0x111827))
  scene.add(new THREE.AxesHelper(10))

  // Collect valid models and compute global bounding box for centering
  const validModels: { substance_id: string; verts: number[][]; faces: number[][] }[] = []
  let gMin = [Infinity, Infinity, Infinity]
  let gMax = [-Infinity, -Infinity, -Infinity]

  for (const model of models) {
    const verts = model.vertices || []
    const faces = model.faces || []
    if (verts.length < 3 || faces.length < 1) {
      console.warn(`[Model3D] skipping ${model.substance_id}: verts=${verts.length}, faces=${faces.length}`)
      continue
    }
    validModels.push({ substance_id: model.substance_id, verts, faces })
    for (const v of verts) {
      for (let i = 0; i < 3; i++) {
        gMin[i] = Math.min(gMin[i], v[i])
        gMax[i] = Math.max(gMax[i], v[i])
      }
    }
  }

  // Center the model at origin; backend already normalizes to ~[0,30] range
  const center = [(gMin[0] + gMax[0]) / 2, (gMin[1] + gMax[1]) / 2, (gMin[2] + gMax[2]) / 2]

  console.log(`[Model3D] bounds: min=${gMin}, max=${gMax}, center=${center}`)

  let hasContent = false
  for (const model of validModels) {
    const color = substanceColors[model.substance_id] || 0x3b82f6

    // Remap: backend x->Three.js x, backend y->Three.js z, backend z(depth)->Three.js y(up)
    const normVerts = model.verts.map(v => [
      v[0] - center[0],
      v[2] - center[2],   // z (depth) -> Y up
      v[1] - center[1],   // y (lat) -> Z
    ])

    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(normVerts.flat()), 3))
    geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(model.faces.flat()), 1))
    geometry.computeVertexNormals()

    const material = new THREE.MeshPhongMaterial({
      color,
      transparent: true,
      opacity: 0.75,
      shininess: 80,
      side: THREE.DoubleSide,
    })

    const mesh = new THREE.Mesh(geometry, material)
    scene.add(mesh)

    // Wireframe
    scene.add(new THREE.LineSegments(
      new THREE.WireframeGeometry(geometry),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.2 })
    ))

    hasContent = true
    console.log(`[Model3D] added ${model.substance_id}: ${model.verts.length} verts, ${model.faces.length} faces`)
  }

  // Position camera to see the normalized models
  if (hasContent) {
    camera.position.set(25, 20, 25)
  } else {
    // Demo sphere fallback
    const geo = new THREE.SphereGeometry(5, 32, 32)
    const mat = new THREE.MeshPhongMaterial({ color: 0x3b82f6, transparent: true, opacity: 0.6 })
    scene.add(new THREE.Mesh(geo, mat))
    camera.position.set(15, 12, 15)
  }
  camera.lookAt(0, 0, 0)

  // Ground plane
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(100, 100),
    new THREE.MeshPhongMaterial({ color: 0x111827, transparent: true, opacity: 0.5 })
  )
  plane.rotation.x = -Math.PI / 2
  plane.position.y = -0.1
  scene.add(plane)

  function animate() {
    animId = requestAnimationFrame(animate)
    controls?.update()
    if (renderer && scene && camera) {
      renderer.render(scene, camera)
    }
  }
  animate()
}

onMounted(async () => {
  try {
    console.log('[Model3D] loading model for job:', props.jobId)
    const data = await api.getModel3D(props.jobId)
    modelInfo.value = data
    console.log('[Model3D] data loaded:', data.models?.length, 'models')
  } catch (e: any) {
    error.value = '加载3D模型数据失败: ' + (e.message || e)
    console.error('[Model3D] load failed:', e)
  }

  loading.value = false

  // Wait for DOM to render the container div
  await nextTick()

  if (modelInfo.value?.models?.length) {
    initThree(modelInfo.value.models)
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animId)
  if (renderer) {
    renderer.dispose()
    renderer = null
  }
  if (scene) {
    scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry?.dispose()
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose())
        } else {
          obj.material?.dispose()
        }
      }
    })
    scene = null
  }
})
</script>

<template>
  <div>
    <router-link to="/" class="back-link">&larr; 返回</router-link>
    <div class="page-header">
      <h2>3D 地质模型</h2>
      <p>任务 {{ jobId.slice(0, 8) }}... — Three.js 等值面渲染</p>
    </div>

    <!-- Error -->
    <div v-if="error" class="card" style="border-color: var(--danger);">
      <div style="color: var(--danger); font-size: 13px;">{{ error }}</div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="card" style="text-align: center; padding: 60px; color: var(--text-dim);">
      <div style="font-size: 32px; margin-bottom: 12px;">&#128302;</div>
      <div>加载3D模型数据...</div>
    </div>

    <!-- 3D canvas -->
    <template v-else>
      <div class="card">
        <div ref="container" class="model-container" style="width: 100%; height: 600px;"></div>
      </div>

      <!-- Controls hint -->
      <div class="card" style="font-size: 12px; color: var(--text-dim);">
        <div style="display: flex; gap: 20px; flex-wrap: wrap;">
          <span>&#128260; 左键拖拽旋转</span>
          <span>&#128170; 滚轮缩放</span>
          <span>&#9995; 右键拖拽平移</span>
        </div>
      </div>

      <!-- Model stats -->
      <div class="grid grid-3" v-if="modelInfo?.models?.length">
        <div class="stat-card" v-for="m in modelInfo.models" :key="m.substance_id">
          <div class="stat-label">{{ substanceNames[m.substance_id] || m.substance_id }}</div>
          <div class="stat-value" :style="{ color: '#' + (substanceColors[m.substance_id] || 0x3b82f6).toString(16).padStart(6, '0') }">
            {{ m.vertices?.length || 0 }}
          </div>
          <div style="font-size: 11px; color: var(--text-dim);">顶点数</div>
          <div style="font-size: 12px; margin-top: 4px; color: var(--text-dim);">
            面片: {{ m.faces?.length || 0 }}
          </div>
        </div>
      </div>

      <!-- No models -->
      <div class="card" v-if="!modelInfo?.models?.length && !error" style="text-align: center; padding: 40px; color: var(--text-dim);">
        <div style="font-size: 32px; margin-bottom: 12px;">&#128302;</div>
        <div>该任务未生成3D模型数据（可能是因为异常区域过小）</div>
      </div>
    </template>
  </div>
</template>
