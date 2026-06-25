# 🌍 Geo-Reporter：地质勘探综合报告生成平台

一个强大的地学数据集成与专业报告生成平台。上传 KML 文件圈定勘探区域，自动识别地理位置、并行搜索 8 类地学数据，输出专业中文 Word 报告。

## ✨ 功能特性

### 核心功能
- **KML 文件支持**：解析 .kml / .kmz 格式文件，自动提取多边形区域和元数据
- **自动地理定位**：使用 Nominatim 反向地理编码获取中文行政区划
- **8 类数据搜索**：并行搜索以下地学数据：
  1. 气候资料
  2. 地理与地形地貌资料
  3. 交通、基础设施及经济条件
  4. 水系与水文资料
  5. 地质与矿产资料
  6. 地球物理与地球化学资料
  7. 遥感与地形地貌资料
  8. 矿业权与法律政策资料

- **专业报告生成**：输出符合 GB/T 9704 标准的中文 Word 文档
- **Web UI**：用户友好的浏览器界面，支持上传、进度追踪、下载

### 技术特点
- **并行处理**：4 个工作进程同时搜索，速度快
- **Web 搜索集成**：利用 Claude 的 WebSearch 功能获取最新数据
- **流式进度推送**：Server-Sent Events (SSE) 实时进度反馈
- **高质量输出**：python-docx 生成的报告支持中文字体、表格、样式

## 🚀 快速开始

### 前置要求

- Python 3.9 或更高版本
- 所有依赖已预装（lxml, shapely, flask, python-docx, jinja2 等）

### 安装

```bash
# 进入项目目录
cd "/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-reporter"

# （可选）安装依赖（如果之前未安装）
pip3 install -r requirements.txt
```

### 启动 Web 服务器

```bash
python3 web/app.py
```

访问浏览器：**http://localhost:8081**

### 使用流程

1. **上传 KML 文件**：在页面上选择或拖拽 .kml/.kmz 文件
2. **点击生成**：系统自动开始搜索和报告生成
3. **监看进度**：实时进度日志显示各步骤完成情况
4. **下载报告**：完成后下载 Word 格式报告

## 📁 项目结构

```
geo-reporter/
├── web/                          # Web 后端和前端
│   ├── app.py                    # Flask 应用主文件
│   └── templates/
│       └── index.html            # 单页应用 UI
│
├── reporter/                     # 核心模块
│   ├── kml_parser.py            # KML 文件解析
│   ├── geocoder.py              # Nominatim 地理编码
│   ├── categories.py            # 8 类数据定义
│   ├── prompts.py               # Prompt 模板渲染
│   ├── search_engine.py         # Claude WebSearch 集成
│   └── report_builder.py        # python-docx 报告生成
│
├── templates/                    # Jinja2 prompt 模板
│   └── base_prompt.j2           # 通用 prompt 模板
│
├── uploads/                      # 上传文件存储目录
├── reports/                      # 生成报告存储目录
├── test_integration.py           # 集成测试脚本
├── requirements.txt              # Python 依赖
├── config.yaml                   # 配置文件
└── .claude/settings.local.json   # Claude Code 权限配置
```

## 🔧 API 端点

### POST `/api/upload-kml`
上传 KML 文件并验证。

**请求**：
```
Content-Type: multipart/form-data
Body: file (KML 或 KMZ 文件)
```

**响应**：
```json
{
  "task_id": "abc123",
  "status": "kml_uploaded",
  "kml_name": "area.kml",
  "area_name": "白银铜矿区勘探范围"
}
```

### GET `/api/run/<task_id>`
开始生成报告（Server-Sent Events）。

**响应**（流式）：
```
data: {"step": 1, "message": "正在确定地理位置..."}
data: {"step": 2, "message": "正在搜索数据..."}
data: {"step": 5, "message": "报告生成完成！", "report_path": "..."}
```

### GET `/api/download/<task_id>`
下载生成的 Word 报告。

**响应**：
```
Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document
Body: (Word 文档二进制)
```

### GET `/api/status/<task_id>`
获取任务状态。

**响应**：
```json
{
  "task_id": "abc123",
  "status": "completed",
  "has_report": true
}
```

### DELETE `/api/cleanup/<task_id>`
清理任务（删除临时文件）。

## 📊 报告内容示例

生成的 Word 报告结构：

```
【封面页】
  地质勘探综合报告
  研究区：白银铜矿区勘探范围
  坐标：经度 104.00~104.50°，北纬 36.40~36.70°

【目录】

第一章  研究区基本信息
  1.1 地理位置
  1.2 行政区划
  1.3 地质背景

第二章  气候资料
  [概述 150-200 字]
  [数据表格：项目 | 数值 | 来源]
  [关键发现 3-5 条]

第三章  地理与地形地貌资料
  ...

... 第四至第九章 ...

【附录】数据来源与参考文献
```

### 报告样式

- **字体**：宋体（正文）/ 黑体（标题）
- **大小**：12pt（正文）/ 16pt（一级标题）
- **行距**：1.5 倍
- **表格**：单线表头灰底
- **符合标准**：GB/T 9704 中文文档标准

## 🔍 数据搜索策略

### 搜索流程

1. **地理定位**：提取 KML 中心点坐标 → Nominatim 反向编码 → 获取省市区信息
2. **Prompt 构造**：将地理上下文注入 Jinja2 模板
3. **并行搜索**：4 个工作进程同时向 Claude 发起 8 个搜索请求
4. **结果解析**：从 Claude 响应中提取 JSON 数据（summary, data_points, key_findings）
5. **报告组装**：使用 python-docx 按章节组织数据

### 搜索提示词特点

- 中文专业术语准确
- 包含地理上下文（省市区、坐标范围）
- 包含地质背景（KML description 字段）
- 要求结构化 JSON 输出
- 失败时返回占位符数据（不中断流程）

## 🧪 测试

运行集成测试验证所有模块：

```bash
python3 test_integration.py
```

测试内容：
- ✅ KML 文件解析
- ✅ Nominatim 地理编码
- ✅ 8 类数据类别定义
- ✅ Prompt 模板渲染
- ✅ 报告生成器初始化

## ⚙️ 配置

### config.yaml

```yaml
report:
  output_dir: ./reports         # 报告输出目录
  client_name: ""               # 委托单位（可选）
  author: ""                    # 编制单位（可选）

search:
  model: haiku                  # Claude 模型
  max_workers: 4                # 并发搜索工作进程数
  timeout_seconds: 120          # 单个搜索超时时间
  retry_count: 1                # 重试次数
```

### .claude/settings.local.json

权限配置：

```json
{
  "permissions": {
    "allow": [
      "WebSearch",
      "Bash(claude -p --dangerously-skip-permissions*)",
      "Bash(python3*)"
    ]
  }
}
```

## 🚨 常见问题

### 问：生成报告需要多长时间？

**答**：通常 2-3 分钟。
- 地理定位：5-10 秒（Nominatim API）
- 数据搜索：1-2 分钟（并行 4 个任务，每个 20-30 秒）
- 报告生成：10-20 秒（python-docx）

### 问：支持哪些 KML 文件格式？

**答**：支持 .kml 和 .kmz（压缩 KML）格式。KML 文件应包含：
- `<Placemark>` 元素定义区域
- `<Polygon>` 或 `<Point>` 几何体
- （可选）`<name>` 和 `<description>` 元数据

### 问：生成失败如何排查？

**答**：
1. 检查 Web 服务器日志：`python3 web/app.py` 的终端输出
2. 检查 KML 文件格式是否正确（使用 Google Earth 验证）
3. 检查网络连接（Nominatim 和 Claude API 需要网络）
4. 查看浏览器报告页面的错误提示

### 问：如何使用自定义搜索类别或子主题？

**答**：编辑 `reporter/categories.py`，修改 `CATEGORIES` 列表和 `sub_topics` 字段。

## 📝 开发指南

### 添加新的搜索类别

1. 在 `reporter/categories.py` 中添加新的 `SearchCategory`
2. 创建对应的 Jinja2 模板文件（可选，通用模板可复用）
3. 更新报告章节编号逻辑（`report_builder.py`）

### 自定义报告样式

编辑 `reporter/report_builder.py` 中的：
- `FONT_SONGTI`, `FONT_HEITI`：字体名称
- `_add_heading()`, `_add_paragraph()`：样式定义
- `_add_table()`：表格样式

### 改进 Web UI

编辑 `web/templates/index.html`（HTML/CSS/JS）。UI 使用：
- 原生 JavaScript（无框架）
- Server-Sent Events 流式进度
- 拖拽上传支持

## 📄 许可

开源项目 - 自由使用、修改和分发。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 改进平台！

---

**版本**：v0.1.0  
**最后更新**：2026-04-15
