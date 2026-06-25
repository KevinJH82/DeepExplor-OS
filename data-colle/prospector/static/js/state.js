/**
 * Prospector 全局状态管理
 */
const AppState = {
  currentTaskId: null,
  currentFile: null,
  pollTimers: {},
  tasks: {},
};

/**
 * 工具函数
 */
const Utils = {
  $(sel) { return document.querySelector(sel); },
  $$(sel) { return document.querySelectorAll(sel); },
  show(sel) { this.$(sel)?.classList.remove('hidden'); },
  hide(sel) { this.$(sel)?.classList.add('hidden'); },
};

/**
 * 缓存文件管理
 */
async function loadCacheStatus() {
  try {
    const resp = await fetch('/api/cache-files');
    const data = await resp.json();
    const el = Utils.$('#cacheStatus');
    if (!el) return;
    if (data.files.length === 0) {
      el.innerHTML = '<span class="text-warning">⚠️ 无缓存文件 — EMAG2 磁异常数据需要手动上传</span>';
    } else {
      el.innerHTML = data.files.map(f =>
        `<div>✅ <strong>${f.name}</strong> (${f.size_mb} MB)</div>`
      ).join('');
    }
  } catch (e) {
    const el = Utils.$('#cacheStatus');
    if (el) el.innerHTML = '<span class="text-danger">加载失败</span>';
  }
}

async function uploadCacheFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const resultEl = Utils.$('#cacheUploadResult');
  resultEl.innerHTML = '<span class="text-info">上传中...</span>';

  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch('/api/cache-upload', { method: 'POST', body: form });
    const data = await resp.json();
    if (resp.ok) {
      resultEl.innerHTML = `<span class="text-success">✅ ${data.message}</span>`;
      loadCacheStatus();
    } else {
      resultEl.innerHTML = `<span class="text-danger">❌ ${data.error}</span>`;
    }
  } catch (e) {
    resultEl.innerHTML = `<span class="text-danger">❌ 上传失败: ${e.message}</span>`;
  }
  event.target.value = '';
}

// 页面加载时检查缓存状态
document.addEventListener('DOMContentLoaded', loadCacheStatus);
