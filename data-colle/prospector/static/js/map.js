/**
 * 地图可视化模块 — Leaflet.js
 */

let _map = null;
let _roiLayer = null;
let _bufferLayer = null;
let _overlayLayer = null;
let _centerMarker = null;

function initMap() {
  if (_map) return;
  _map = L.map('mapContainer', {
    center: [35, 105],
    zoom: 4,
    zoomControl: true,
    attributionControl: false,
  });

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
  }).addTo(_map);

  // 缩放控件移到右上
  _map.zoomControl.setPosition('topright');
}

function showRoiOnMap(geojson, bbox, center, expandedBbox) {
  initMap();
  Utils.show('#mapSection');

  // 清除旧图层
  if (_roiLayer) _map.removeLayer(_roiLayer);
  if (_bufferLayer) _map.removeLayer(_bufferLayer);
  if (_centerMarker) _map.removeLayer(_centerMarker);

  // ROI 多边形
  _roiLayer = L.geoJSON(geojson, {
    style: {
      color: '#38bdf8',
      weight: 2,
      fillColor: '#38bdf8',
      fillOpacity: 0.15,
    },
  }).addTo(_map);

  // 缓冲区范围
  if (expandedBbox) {
    const bufCoords = [
      [expandedBbox.south, expandedBbox.west],
      [expandedBbox.north, expandedBbox.west],
      [expandedBbox.north, expandedBbox.east],
      [expandedBbox.south, expandedBbox.east],
    ];
    _bufferLayer = L.polygon(bufCoords, {
      color: '#4ade80',
      weight: 1,
      dashArray: '6 4',
      fillColor: '#4ade80',
      fillOpacity: 0.05,
    }).addTo(_map);
  }

  // 中心点
  _centerMarker = L.circleMarker([center.lat, center.lon], {
    radius: 5,
    color: '#fbbf24',
    fillColor: '#fbbf24',
    fillOpacity: 1,
    weight: 2,
  }).addTo(_map);

  _map.fitBounds(_roiLayer.getBounds().pad(0.2));
}

async function showTectonicOverlay(bbox) {
  if (!_map) return;
  if (_overlayLayer) _map.removeLayer(_overlayLayer);

  try {
    const params = new URLSearchParams(bbox).toString();
    const resp = await fetch(`/api/tectonic-overlay?${params}`);
    const data = await resp.json();

    _overlayLayer = L.geoJSON(data, {
      style(feature) {
        if (feature.properties.type === 'basin') {
          return { color: '#f97316', weight: 1, fillColor: '#f97316', fillOpacity: 0.08, dashArray: '4 4' };
        }
        return { color: '#a78bfa', weight: 1.5, fillColor: '#a78bfa', fillOpacity: 0.06 };
      },
      onEachFeature(feature, layer) {
        const p = feature.properties;
        const minerals = (p.major_minerals || []).join(', ');
        const label = p.type === 'basin'
          ? `<b>${p.name}</b><br>含油气盆地<br>面积: ${p.area_km2?.toLocaleString()} km²`
          : `<b>${p.name}</b><br>${p.name_en || ''}<br>主要矿产: ${minerals}`;
        layer.bindTooltip(label, { sticky: true, className: 'map-tooltip' });
      },
    }).addTo(_map);
  } catch (e) {
    console.error('加载构造单元覆盖层失败:', e);
  }
}

async function previewRoi(file, bufferKm) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('buffer', bufferKm);

  try {
    const resp = await fetch('/api/parse-roi', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error) {
      console.warn('ROI 预览失败:', data.error);
      return null;
    }
    showRoiOnMap(data.geometry, data.bbox, data.center, data.expanded_bbox);
    return data;
  } catch (e) {
    console.warn('ROI 预览失败:', e);
    return null;
  }
}
