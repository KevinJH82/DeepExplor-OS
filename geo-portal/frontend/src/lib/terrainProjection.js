// 统一投影：经纬度 + 深度(m) → Three.js 世界坐标，供地形 mesh / 靶点 / 钻孔 / 切片共用。
// 世界系：X=东(+)/西(-)，Z=南(+)/北(-)，Y=高程/深度(上为正)。ROI 水平边长归一化到 S。
import * as THREE from 'three'

export const WORLD_SIZE = 200   // ROI 水平边长(世界单位)

// 由 bbox(+可选 terrain 高程网格)构造投影器。terrain = {size, min_m, max_m, heights:[size*size]}
export function makeProjector(bbox, terrain, opts = {}) {
  const S = opts.size || WORLD_SIZE
  const [minLon, minLat, maxLon, maxLat] = bbox
  const vExag = opts.vExag ?? 3            // 竖向夸张(地形起伏)
  // 水平 1 米 → 多少世界单位(按 ROI 实际经纬跨度估算地面尺度)
  const midLat = (minLat + maxLat) / 2
  const geoWMeters = Math.max((maxLon - minLon) * 111320 * Math.cos(midLat * Math.PI / 180), 1)
  const metersToWorld = S / geoWMeters

  const N = terrain?.size || 0
  const heights = terrain?.heights || null
  const minM = terrain?.min_m ?? 0
  const hasTerrain = !!(N && heights && heights.length === N * N)

  // 双线性采样高程(米)。fx:0..1 西→东, fy:0..1 南→北
  function heightMetersAt(fx, fy) {
    if (!hasTerrain) return minM
    const cx = clamp01(fx) * (N - 1)          // 列：西→东
    const ry = (1 - clamp01(fy)) * (N - 1)    // 行：北(0)→南(N-1)
    const c0 = Math.floor(cx), r0 = Math.floor(ry)
    const c1 = Math.min(c0 + 1, N - 1), r1 = Math.min(r0 + 1, N - 1)
    const tx = cx - c0, ty = ry - r0
    const h00 = heights[r0 * N + c0], h10 = heights[r0 * N + c1]
    const h01 = heights[r1 * N + c0], h11 = heights[r1 * N + c1]
    const a = h00 + (h10 - h00) * tx
    const b = h01 + (h11 - h01) * tx
    return a + (b - a) * ty
  }

  // 高程(米) → 世界 Y(以网格最低高程为基准面 0)
  function elevToWorldY(hMeters) {
    return (hMeters - minM) * metersToWorld * vExag
  }
  // 地下深度(米，正值向下) → 世界 Y 落差
  function depthToWorldY(depthM) {
    return (depthM || 0) * metersToWorld
  }

  function lonLatToFxFy(lon, lat) {
    return [(lon - minLon) / ((maxLon - minLon) || 1),
            (lat - minLat) / ((maxLat - minLat) || 1)]
  }
  function fxFyToWorldXZ(fx, fy) {
    return [(fx - 0.5) * S, (0.5 - fy) * S]   // 北=-Z
  }

  // 地表点(高程上)
  function surfaceWorld(lon, lat) {
    const [fx, fy] = lonLatToFxFy(lon, lat)
    const [x, z] = fxFyToWorldXZ(fx, fy)
    return new THREE.Vector3(x, elevToWorldY(heightMetersAt(fx, fy)), z)
  }
  // 任意经纬+深度 → 世界点(地表下方对应深度)
  function toWorld(lon, lat, depthM) {
    const [fx, fy] = lonLatToFxFy(lon, lat)
    const [x, z] = fxFyToWorldXZ(fx, fy)
    const y = elevToWorldY(heightMetersAt(fx, fy)) - depthToWorldY(depthM)
    return new THREE.Vector3(x, y, z)
  }

  return {
    S, N, hasTerrain, metersToWorld, vExag,
    heightMetersAt, elevToWorldY, depthToWorldY,
    lonLatToFxFy, fxFyToWorldXZ, surfaceWorld, toWorld,
  }
}

function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v }
