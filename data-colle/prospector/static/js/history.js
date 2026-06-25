/**
 * 历史任务管理模块
 */

async function refreshHistory() {
  try {
    const resp = await fetch('/api/tasks');
    const data = await resp.json();
    const tasks = data.tasks || [];
    AppState.tasks = {};
    tasks.forEach(t => { AppState.tasks[t.id] = t; });
    renderHistory(tasks);
  } catch (e) {
    console.error('加载历史任务失败:', e);
  }
}

function renderHistory(tasks) {
  const container = Utils.$('#historyList');
  if (!tasks.length) {
    container.innerHTML = '<div class="text-muted text-center py-3">暂无历史任务</div>';
    return;
  }

  container.innerHTML = tasks.map(t => {
    const statusClass = {
      completed: 'badge-completed',
      failed: 'badge-failed',
      running: 'badge-running',
      pending: 'badge-pending',
    }[t.status] || 'badge-pending';

    const statusText = {
      completed: '已完成',
      failed: '失败',
      running: '运行中',
      pending: '等待中',
    }[t.status] || t.status;

    const time = t.created_at ? new Date(t.created_at).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }) : '';

    const actions = t.status === 'completed'
      ? `<button class="btn btn-sm btn-outline-info py-0 px-2" onclick="viewReport('${t.id}')" title="查看报告">📄</button>
         <a class="btn btn-sm btn-outline-success py-0 px-2" href="/api/download/${t.id}" download title="下载">📥</a>`
      : '';

    return `<div class="history-item d-flex justify-content-between align-items-center" data-task-id="${t.id}">
      <div>
        <span class="badge-mineral">${t.mineral}</span>
        <span class="badge-status ${statusClass}">${statusText}</span>
        <span class="ms-2 small">${t.output_name || ''}</span>
      </div>
      <div class="d-flex align-items-center gap-2">
        <span class="text-muted small">${time}</span>
        ${actions}
        <button class="btn btn-sm btn-outline-danger py-0 px-2" onclick="deleteTask('${t.id}')" title="删除">🗑️</button>
      </div>
    </div>`;
  }).join('');
}

async function deleteTask(taskId) {
  if (!confirm('确认删除此任务及其所有数据？')) return;
  try {
    await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
    delete AppState.tasks[taskId];
    refreshHistory();
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

function toggleHistory() {
  const panel = Utils.$('#historyPanel');
  const body = Utils.$('#historyBody');
  const icon = Utils.$('#historyToggleIcon');
  if (body.classList.contains('hidden')) {
    body.classList.remove('hidden');
    icon.style.transform = 'rotate(180deg)';
  } else {
    body.classList.add('hidden');
    icon.style.transform = '';
  }
}
