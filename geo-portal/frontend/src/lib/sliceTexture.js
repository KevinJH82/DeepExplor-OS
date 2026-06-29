import { fromArrayBuffer } from 'geotiff'
import client from '../api/client'

// 有利度 0..1 → jet 色带 [r,g,b]
function jet(t) {
  t = Math.max(0, Math.min(1, t))
  const r = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 3)))
  const g = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 2)))
  const b = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 1)))
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)]
}

// 把 GeoTIFF arraybuffer 渲染为上色透明热力图 dataURL(低值/NaN 透明)
async function renderTiff(arrayBuffer) {
  const tiff = await fromArrayBuffer(arrayBuffer)
  const image = await tiff.getImage()
  const w = image.getWidth(), h = image.getHeight()
  const [data] = await image.readRasters()
  let mn = Infinity, mx = -Infinity
  for (let i = 0; i < data.length; i++) {
    const v = data[i]
    if (Number.isFinite(v)) { if (v < mn) mn = v; if (v > mx) mx = v }
  }
  const rng = (mx - mn) || 1
  const canvas = document.createElement('canvas')
  canvas.width = w; canvas.height = h
  const ctx = canvas.getContext('2d')
  const img = ctx.createImageData(w, h)
  for (let i = 0; i < data.length; i++) {
    const v = data[i], o = i * 4
    if (!Number.isFinite(v)) { img.data[o + 3] = 0; continue }
    const t = (v - mn) / rng
    if (t < 0.08) { img.data[o + 3] = 0; continue }   // 极低有利度透明,避免糊成一片
    const [r, g, b] = jet(t)
    img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b
    img.data[o + 3] = Math.round(60 + t * 195)
  }
  ctx.putImageData(img, 0, 0)
  return canvas.toDataURL()
}

// 深度切片等:只要 dataURL
export async function loadSliceTexture(url) {
  const resp = await client.get(url, { responseType: 'arraybuffer' })
  return renderTiff(resp.data)
}

// 证据栅格:同时读取 BFF 的 scope/bounds 头(裁空回退区域级时需按真实范围摆放 + 标注)
export async function loadEvidenceRaster(url) {
  const resp = await client.get(url, { responseType: 'arraybuffer' })
  const dataURL = await renderTiff(resp.data)
  const h = resp.headers || {}
  const scope = h['x-raster-scope'] || h['X-Raster-Scope'] || 'aoi'
  const note = h['x-raster-note'] || h['X-Raster-Note'] || ''
  const raw = h['x-raster-bounds'] || h['X-Raster-Bounds'] || ''
  const bounds = raw ? raw.split(',').map(Number).filter((n) => Number.isFinite(n)) : null
  return { dataURL, scope, note, bounds: bounds && bounds.length === 4 ? bounds : null }
}
