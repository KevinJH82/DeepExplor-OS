/**
 * 流水线模块 — 提交、轮询、进度（含子步骤）
 */

const STEP_MAP = {
  '解析 ROI': 8, '定位构造单元': 16, '查询矿种知识库': 24,
  '收集地质资料': 40, '收集地球物理资料': 58,
  '收集地球化学资料': 74, '实时查询学术论文': 88, '生成报告': 95, '完成': 100,
};

const STEP_LABELS = [
  '解析 ROI', '定位构造单元', '查询矿种知识库',
  '收集地质资料', '收集地球物理资料', '收集地球化学资料',
  '实时查询学术论文', '生成报告',
];

async function submitTask() {
  if (!AppState.currentFile) return;

  const formData = new FormData();
  formData.append('file', AppState.currentFile);
  formData.append('mineral', Utils.$('#mineralSelect').value);
  formData.append('buffer', Utils.$('#bufferSlider').value);
  formData.append('auto_download', Utils.$('#autoDownload').checked);

  Utils.hide('#inputSection');
  Utils.show('#progressSection');
  Utils.hide('#resultSection');
  resetProgressBar();
  renderStepIndicators();
  Utils.$('#stepText').textContent = '上传文件中...';
  Utils.$('#progressBar').style.width = '5%';

  try {
    const resp = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error) { showError(data.error); return; }

    AppState.currentTaskId = data.task_id;
    Utils.$('#stepText').textContent = '任务已启动...';
    Utils.$('#progressBar').style.width = '8%';
    startPolling(data.task_id);
  } catch (e) {
    showError('上传失败: ' + e.message);
  }
}

function startPolling(taskId) {
  const key = taskId || AppState.currentTaskId;
  if (AppState.pollTimers[key]) clearInterval(AppState.pollTimers[key]);
  AppState.pollTimers[key] = setInterval(() => pollStatus(key), 1000);
}

async function pollStatus(taskId) {
  try {
    const resp = await fetch(`/api/status/${taskId}`);
    const data = await resp.json();
    if (data.error && data.error === '任务不存在') {
      clearInterval(AppState.pollTimers[taskId]);
      return;
    }

    const pct = STEP_MAP[data.step] || 15;
    Utils.$('#progressBar').style.width = pct + '%';
    Utils.$('#stepText').textContent = data.step;

    updateStepIndicators(data.step);

    if (data.status === 'completed') {
      clearInterval(AppState.pollTimers[taskId]);
      Utils.$('#progressBar').style.width = '100%';
      Utils.$('#progressBar').classList.replace('bg-info', 'bg-success');
      Utils.$('#stepText').textContent = '✅ 资料收集完成!';
      AppState.currentTaskId = taskId;
      showResult(data);
      // 加载完整数据用于可视化和地图
      loadFullResult(taskId);
      refreshHistory();
    } else if (data.status === 'failed') {
      clearInterval(AppState.pollTimers[taskId]);
      Utils.$('#progressBar').classList.replace('bg-info', 'bg-danger');
      Utils.$('#stepText').textContent = '❌ 失败: ' + (data.error || '未知错误');
      refreshHistory();
    }
  } catch (e) {
    console.error('poll error:', e);
  }
}

function resetProgressBar() {
  const bar = Utils.$('#progressBar');
  bar.classList.remove('bg-success', 'bg-danger');
  bar.classList.add('bg-info');
  bar.style.width = '0%';
}

function showError(msg) {
  Utils.hide('#progressSection');
  Utils.show('#resultSection');
  Utils.$('#resultSummary').innerHTML =
    `<div class="alert alert-danger">${msg}</div>`;
}

// ===== 多步骤指示器 =====

function renderStepIndicators() {
  const container = Utils.$('#stepIndicators');
  if (!container) return;
  container.innerHTML = STEP_LABELS.map((label, i) => {
    const short = label.replace('收集', '').replace('实时查询', '');
    return `<div class="step-item" data-step="${i}" title="${label}">
      <span class="step-num">${i + 1}</span>
      <span class="step-label">${short}</span>
    </div>`;
  }).join('');
}

function updateStepIndicators(currentStep) {
  const idx = STEP_LABELS.indexOf(currentStep);
  const items = Utils.$$('.step-item');
  items.forEach((el, i) => {
    el.classList.remove('active', 'done');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  });
}

// ===== 加载完整结果用于可视化 =====

async function loadFullResult(taskId) {
  try {
    const resp = await fetch(`/api/task-detail/${taskId}`);
    if (!resp.ok) return;
    const data = await resp.json();

    // 地图叠加构造单元
    if (data.bbox) {
      showTectonicOverlay(data.bbox);
    }

    // 元素背景值图表
    if (data.thresholds && data.national_ref) {
      renderElementChart(data.thresholds, data.national_ref, data.source_unit || '全国');
    }

    // 物探地图
    renderGeophysicalMaps(taskId);

    // 论文卡片
    if (data.papers) {
      renderPaperCards(data.papers);
    }

  } catch (e) {
    console.warn('加载详细结果失败:', e);
  }
}
