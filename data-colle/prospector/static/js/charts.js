/**
 * 数据可视化模块 — Chart.js 元素背景值图表 + 物探地图 + 论文卡片 + 影像网格
 */

function renderElementChart(thresholds, nationalRef, sourceUnit) {
  const container = Utils.$('#elementChartContainer');
  if (!container || !thresholds || Object.keys(thresholds).length === 0) return;
  Utils.show('#elementChartSection');

  const labels = Object.keys(thresholds);
  const bgValues = labels.map(e => thresholds[e].background);
  const weakValues = labels.map(e => thresholds[e].weak_anomaly);
  const strongValues = labels.map(e => thresholds[e].strong_anomaly);
  const natValues = labels.map(e => nationalRef[e] || 0);

  const ctx = container.getContext('2d');
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: `${sourceUnit} 背景值`,
          data: bgValues,
          backgroundColor: 'rgba(56, 189, 248, 0.7)',
          borderColor: '#38bdf8',
          borderWidth: 1,
        },
        {
          label: '全国背景值',
          data: natValues,
          backgroundColor: 'rgba(148, 163, 184, 0.5)',
          borderColor: '#94a3b8',
          borderWidth: 1,
        },
        {
          label: '弱异常 (1.5x)',
          data: weakValues,
          type: 'line',
          borderColor: '#fbbf24',
          borderWidth: 1.5,
          pointRadius: 2,
          fill: false,
        },
        {
          label: '强异常 (3x)',
          data: strongValues,
          type: 'line',
          borderColor: '#f87171',
          borderWidth: 1.5,
          pointRadius: 2,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#e2e8f0', font: { size: 11 } } },
        title: {
          display: true,
          text: `元素地球化学背景值 (${sourceUnit})`,
          color: '#e2e8f0',
          font: { size: 14 },
        },
      },
      scales: {
        x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
        y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' }, beginAtZero: true },
      },
    },
  });
}

function renderGeophysicalMaps(taskId) {
  const container = Utils.$('#geophysMapsContainer');
  if (!container) return;

  // 尝试加载磁异常图和重力图
  const imgUrls = [
    { src: `/api/file/${taskId}/02_地球物理资料/magnetic/emag2_upcont_map.png`, label: 'EMAG2 磁异常分布 (nT)', id: 'magMap' },
  ];

  let html = '';
  imgUrls.forEach(item => {
    html += `
    <div class="geophys-map-card mb-3">
      <h6 class="text-muted small mb-2">${item.label}</h6>
      <img id="${item.id}" src="${item.src}" alt="${item.label}"
           style="max-width:100%; border-radius:8px; border:1px solid var(--border); display:none;"
           onload="this.style.display='block'; this.parentElement.querySelector('.loading').style.display='none';"
           onerror="this.parentElement.style.display='none';">
      <div class="loading text-muted small">加载中...</div>
    </div>`;
  });
  container.innerHTML = html;
}

function renderPaperCards(papers) {
  const container = Utils.$('#paperCardsContainer');
  if (!container || !papers || papers.length === 0) return;
  Utils.show('#paperCardsSection');

  container.innerHTML = papers.slice(0, 10).map((p, i) => {
    const authors = (p.authors || []).slice(0, 3).join(', ');
    const cited = p.citation_count || p.cited_by || 0;
    const year = p.year || '?';
    const title = p.title || '无标题';
    const abstract = p.abstract ? p.abstract.slice(0, 200) + '...' : '';
    const url = p.url || '#';

    return `
    <div class="paper-card mb-3 p-3" style="background: var(--card); border: 1px solid var(--border); border-radius: 8px;">
      <div class="d-flex justify-content-between align-items-start">
        <div class="flex-grow-1">
          <a href="${url}" target="_blank" class="text-decoration-none" style="color: var(--text); font-weight: 600;">${title}</a>
        </div>
        <div class="ms-2 d-flex gap-1 flex-shrink-0">
          <span class="badge bg-secondary">${year}</span>
          ${cited > 0 ? `<span class="badge" style="background: rgba(251,191,36,.2); color: #fbbf24;"> cited: ${cited}</span>` : ''}
        </div>
      </div>
      <div class="text-muted small mt-1">${authors}${p.authors && p.authors.length > 3 ? ' et al.' : ''}</div>
      ${abstract ? `<div class="small mt-2" style="color: var(--muted);">${abstract}</div>` : ''}
    </div>`;
  }).join('');
}

