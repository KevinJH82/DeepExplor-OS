# 8️⃣ Web 前端 UI 模块技术文档

## 模块概述

**文件**：`web/templates/index.html`  
**行数**：~350 行  
**功能**：单页应用（SPA），处理 KML 上传、进度显示、报告下载

## 技术栈

- **HTML5**：结构
- **CSS3**：样式和动画
- **原生 JavaScript**：交互逻辑（无框架依赖）
- **Server-Sent Events (SSE)**：实时进度推送

## UI 布局

### 主要区域

```
┌─────────────────────────────────────────┐
│       地质勘探综合报告生成平台          │
│   🌍 Geo-Reporter                      │
├─────────────────────────────────────────┤
│ [上传表单区域]                          │
│ ┌─────────────────────────────────────┐ │
│ │ 📁 点击选择或拖拽 KML/KMZ 文件    │ │
│ └─────────────────────────────────────┘ │
│ [上传按钮] [重置按钮]                  │
├─────────────────────────────────────────┤
│ [进度区域]                              │
│ ┌─────────────────────────────────────┐ │
│ │ 进度日志                            │ │
│ │ 🎯 KML 文件已上传                 │ │
│ │ ⏳ 开始生成报告...                 │ │
│ │ ✅ 报告生成完成！                  │ │
│ └─────────────────────────────────────┘ │
│ [下载按钮]                              │
└─────────────────────────────────────────┘
```

## 核心功能

### 1. 文件上传

#### 点击选择

```html
<input type="file" id="kmlFile" accept=".kml,.kmz">
```

#### 拖拽上传

```javascript
fileInputLabel.addEventListener('dragover', (e) => {
    e.preventDefault();
    fileInputLabel.style.background = '#efefff';
});

fileInputLabel.addEventListener('drop', (e) => {
    e.preventDefault();
    document.getElementById('kmlFile').files = e.dataTransfer.files;
});
```

#### 验证

- 文件类型：.kml 或 .kmz
- 最大文件：100 MB

### 2. 上传并生成

```javascript
async function uploadAndGenerate() {
    const formData = new FormData();
    formData.append('file', selectedFile);
    
    // 上传 KML
    const uploadRes = await fetch('/api/upload-kml', {
        method: 'POST',
        body: formData
    });
    
    const uploadData = await uploadRes.json();
    currentTaskId = uploadData.task_id;
    
    // 开始流式报告生成
    await streamReportGeneration(currentTaskId);
}
```

### 3. 实时进度（SSE）

```javascript
async function streamReportGeneration(taskId) {
    const response = await fetch(`/api/run/${taskId}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        const text = decoder.decode(value);
        const lines = text.split('\n');
        
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = JSON.parse(line.substring(6));
                if (data.error) {
                    addLog(data.error, 'error');
                } else if (data.message) {
                    addLog(data.message, data.step === 5 ? 'success' : 'info');
                }
            }
        }
    }
}
```

### 4. 下载报告

```javascript
async function downloadReport() {
    const link = document.createElement('a');
    link.href = `/api/download/${currentTaskId}`;
    link.download = 'report.docx';
    link.click();
}
```

## 样式和 UI

### 色彩方案

```css
primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
success-color: #27ae60;
error-color: #e74c3c;
info-color: #667eea;
```

### 响应式设计

```css
/* 移动设备 */
@media (max-width: 768px) {
    .container {
        max-width: 100%;
        padding: 20px;
    }
}
```

### 动画效果

#### 加载旋转

```css
@keyframes spin {
    to { transform: rotate(360deg); }
}

.spinner {
    animation: spin 0.8s linear infinite;
}
```

#### 按钮悬停

```css
.btn-primary:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
}
```

## HTML 结构

### 主容器

```html
<div class="container">
    <div class="header">
        <h1>🌍 地质勘探综合报告生成平台</h1>
        <p>上传 KML 文件，自动生成专业地质报告</p>
    </div>
    
    <div class="error-message" id="errorMessage"></div>
    
    <!-- 上传表单 -->
    <div id="uploadForm">...</div>
    
    <!-- 进度显示 -->
    <div class="progress-section" id="progressSection">...</div>
</div>
```

### 文件上传区

```html
<div class="form-group">
    <label>选择 KML 文件：</label>
    <div class="file-input-wrapper">
        <label for="kmlFile" class="file-input-label">
            📁 点击选择或拖拽 KML/KMZ 文件
        </label>
        <input type="file" id="kmlFile" accept=".kml,.kmz">
        <span class="file-name" id="fileName"></span>
    </div>
</div>
```

### 进度日志

```html
<div class="progress-log" id="progressLog"></div>
```

日志条目 HTML：

```html
<div class="log-entry [success|error|info]">
    <span class="spinner"></span>
    消息文本
</div>
```

### 下载区域

```html
<div id="downloadSection" class="download-section">
    <p>✅ 报告生成成功！</p>
    <button class="btn btn-download" onclick="downloadReport()">
        📥 下载 Word 报告
    </button>
</div>
```

## JavaScript 变量

### 全局状态

```javascript
let currentTaskId = null;      // 当前任务 ID
let selectedFile = null;       // 选中的文件对象
```

### 用户交互

```javascript
// 文件选择
document.getElementById('kmlFile').addEventListener('change', function(e) {
    selectedFile = e.target.files[0];
    document.getElementById('fileName').textContent = `已选择：${selectedFile.name}`;
    document.getElementById('uploadBtn').disabled = false;
});
```

## 主要函数

| 函数 | 功能 |
|---|---|
| `uploadAndGenerate()` | 上传并开始生成 |
| `streamReportGeneration(taskId)` | SSE 流式接收进度 |
| `addLog(message, type)` | 添加日志条目 |
| `downloadReport()` | 下载报告 |
| `resetForm()` | 重置表单 |
| `showError(message)` | 显示错误 |
| `clearError()` | 清除错误 |

## 错误处理

### 用户输入验证

```javascript
if (!selectedFile) {
    showError('请先选择 KML 文件');
    return;
}
```

### API 错误处理

```javascript
if (!uploadRes.ok) {
    const error = await uploadRes.json();
    showError(`上传失败：${error.error}`);
    return;
}
```

### SSE 错误处理

```javascript
if (data.error) {
    addLog(`❌ 错误：${data.error}`, 'error');
    return;
}
```

## 进度日志样式

### 三种日志类型

```javascript
// 普通消息
addLog('信息文本', 'normal');

// 信息消息（带旋转动画）
addLog('正在处理...', 'info');

// 成功消息
addLog('✅ 成功！', 'success');

// 错误消息
addLog('❌ 错误！', 'error');
```

### CSS 类定义

```css
.log-entry.success {
    color: #27ae60;
    font-weight: 500;
}

.log-entry.error {
    color: #e74c3c;
    font-weight: 500;
}

.log-entry.info {
    color: #667eea;
}
```

## 使用流程

### 用户操作顺序

1. **选择文件**
   - 点击文件框或拖拽文件
   - 显示 `已选择：filename.kml`
   - "上传并生成报告"按钮启用

2. **点击上传**
   - 隐藏表单
   - 显示进度区域
   - 开始日志流

3. **监看进度**
   - 实时显示 5 个步骤的消息
   - 自动滚动日志到底部

4. **完成下载**
   - 显示"✅ 报告生成成功"
   - 点击"下载 Word 报告"
   - 文件自动下载到本地

5. **重置或再次上传**
   - 点击"重置"按钮清空状态
   - 返回初始上传界面

## 性能优化

### 前端

- 原生 JavaScript（无框架）
- CSS 最小化
- 事件委托
- 防抖处理

### 网络

- 压缩传输
- 二进制流下载
- SSE 长连接复用

## 浏览器兼容性

| 浏览器 | 兼容性 |
|---|---|
| Chrome | ✅ 完全支持 |
| Firefox | ✅ 完全支持 |
| Safari | ✅ 完全支持 |
| Edge | ✅ 完全支持 |
| IE 11 | ❌ 不支持（SSE） |

## 依赖

- 无第三方 JavaScript 库
- 仅依赖浏览器原生 API

## 注意事项

1. **跨域问题**：如果后端在不同域，需要 CORS 配置
2. **网络中断**：SSE 自动重连（需要后端支持）
3. **大文件上传**：支持最大 100 MB
4. **移动设备**：全响应式，支持触摸操作
