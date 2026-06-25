/**
 * 结果展示模块 — 结果表格、报告查看、下载
 */

function showResult(data) {
  const r = data.result || {};
  Utils.show('#resultSection');

  let html = '<table class="table table-dark table-sm result-table">';
  const rows = [
    ['ROI 面积', `${r.area_km2?.toFixed(2) || '?'} km²`],
    ['中心坐标', `${r.center_lon?.toFixed(4)}°E, ${r.center_lat?.toFixed(4)}°N`],
    ['1:100万图幅号', r.map_sheet || 'N/A'],
    ['成矿类型', (r.metallogenic_types || []).join(' / ')],
    ['指示元素', (r.key_elements || []).join(', ')],
    ['地质链接', `${r.n_geological_links || 0} 条`],
    ['化探链接', `${r.n_geochem_links || 0} 条`],
    ['学术文献', `${r.n_cnki_links || 0} 条`],
    ['磁法数据', r.magnetic_downloaded ? '✅ 已下载' : '🔗 链接已生成'],
    ['重力数据', r.gravity_downloaded ? '✅ 已下载' : '🔗 链接已生成'],
  ];
  rows.forEach(([k, v]) => { html += `<tr><td>${k}</td><td class="fw-semibold">${v}</td></tr>`; });
  html += '</table>';
  Utils.$('#resultSummary').innerHTML = html;

  Utils.$('#downloadBtn').href = `/api/download/${AppState.currentTaskId}`;
}

async function viewReport(taskId) {
  const tid = taskId || AppState.currentTaskId;
  if (!tid) return;

  const modal = new bootstrap.Modal(Utils.$('#reportModal'));
  modal.show();
  const content = Utils.$('#reportContent');
  content.innerHTML = '<div class="text-center text-muted py-4">加载中...</div>';

  try {
    const resp = await fetch(`/api/report/${tid}`);
    const data = await resp.json();
    let md = data.content || '报告内容为空';
    md = md.replace(
      /!\[([^\]]*)\]\(((?!https?:\/\/)[^)]+)\)/g,
      (match, alt, path) => {
        const imgPath = `/api/file/${tid}/${path}`;
        console.log('[Report] Image:', alt, '→', imgPath);
        return `![${alt}](${imgPath})`;
      }
    );
    let html = marked.parse(md);
    html = html.replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" ');
    content.innerHTML = html;
    console.log('[Report] Images in DOM:', content.querySelectorAll('img').length);
    content.querySelectorAll('img').forEach((img, i) => {
      img.onerror = () => console.error('[Report] Image load failed:', img.src);
      img.onload = () => console.log('[Report] Image loaded:', img.src);
    });
  } catch (e) {
    content.innerHTML = `<div class="text-danger">加载失败: ${e.message}</div>`;
  }
}

async function downloadHTML() {
  if (!AppState.currentTaskId) return;
  try {
    const resp = await fetch(`/api/report/${AppState.currentTaskId}`);
    const data = await resp.json();
    const md = data.content || '报告内容为空';
    const imgBase = window.location.origin + `/api/file/${AppState.currentTaskId}/`;
    const mdFixed = md.replace(
      /!\[([^\]]*)\]\(((?!https?:\/\/)[^)]+)\)/g,
      (match, alt, path) => `![${alt}](${imgBase}${path})`
    );
    const bodyHTML = marked.parse(mdFixed);
    const bodyFixed = bodyHTML.replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" ');

    const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>矿产勘查资料收集报告</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; background: #fff; color: #1a1a1a; line-height: 1.6; }
  h1 { font-size: 1.5rem; border-bottom: 2px solid #2563eb; padding-bottom: .5rem; }
  h2 { font-size: 1.25rem; border-bottom: 1px solid #d1d5db; padding-bottom: .4rem; margin-top: 2rem; }
  h3 { font-size: 1.1rem; color: #2563eb; margin-top: 1.25rem; }
  table { width: 100%; border-collapse: collapse; margin: .75rem 0; font-size: .9rem; }
  th, td { border: 1px solid #d1d5db; padding: .5rem .75rem; text-align: left; }
  th { background: #f3f4f6; font-weight: 600; }
  a { color: #2563eb; }
  code { background: #f3f4f6; padding: .15rem .4rem; border-radius: 4px; font-size: .85em; }
  pre { background: #1e293b; color: #e2e8f0; padding: 1rem; border-radius: 8px; overflow-x: auto; }
  blockquote { border-left: 3px solid #2563eb; padding-left: 1rem; color: #6b7280; }
  img { max-width: 100%; }
  @media print { body { padding: 0; } }
</style>
</head>
<body>
${bodyFixed}
</body>
</html>`;

    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = '矿产勘查报告.html';
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('下载失败: ' + e.message);
  }
}
