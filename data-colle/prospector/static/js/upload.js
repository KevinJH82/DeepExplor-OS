/**
 * 文件上传模块 — 拖放、校验、预览（含地图预览）
 */

function initUpload() {
  const zone = Utils.$('#uploadZone');
  const input = Utils.$('#fileInput');

  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) processFile(e.dataTransfer.files[0]);
  });
}

function handleFile(e) {
  if (e.target.files.length) processFile(e.target.files[0]);
}

async function processFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['kml', 'ovkml', 'xlsx', 'xls'].includes(ext)) {
    alert('不支持的文件格式，请上传 .kml / .ovkml / .xlsx');
    return;
  }
  AppState.currentFile = file;
  Utils.$('#fileName').textContent = `📎 ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
  Utils.show('#fileInfo');
  Utils.$('#submitBtn').disabled = false;

  // 在地图上预览 ROI
  const bufferKm = Utils.$('#bufferSlider').value;
  previewRoi(file, bufferKm);
}

function clearFile() {
  AppState.currentFile = null;
  Utils.$('#fileInput').value = '';
  Utils.hide('#fileInfo');
  Utils.$('#submitBtn').disabled = true;
  Utils.hide('#mapSection');
}
