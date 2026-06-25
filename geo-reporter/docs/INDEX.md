# 📚 Geo-Reporter 8 个模块完整技术文档索引

## 📋 文档清单

### 1️⃣ KML 解析模块
**文件**：`docs/MODULE_1_KML_PARSER.md`  
**核心功能**：
- 解析 .kml 和 .kmz 格式文件
- 提取多边形几何体、中心点、边界框
- 提取区块名称和地质背景描述
- 支持 Polygon、Point、LineString、MultiGeometry

**关键函数**：
- `parse_kml(kml_path)` — 解析单个 KML 文件
- `_extract_polygon(element)` — 从 XML 提取 Polygon
- `_extract_placemark_metadata()` — 提取 name 和 description

**依赖**：lxml, shapely  
**性能**：~1 秒

---

### 2️⃣ 地理编码模块
**文件**：`docs/MODULE_2_GEOCODER.md`  
**核心功能**：
- Nominatim 反向地理编码
- 获取中文行政区划（国家、省、市、区）
- 创建 LocationContext 对象
- 格式化坐标范围和位置字符串

**关键函数**：
- `reverse_geocode(lat, lon)` — Nominatim API 调用
- `create_location_context(bbox, area_name, desc)` — 创建位置上下文

**数据结构**：
- `LocationContext` — 包含所有地理信息

**依赖**：urllib, json  
**性能**：5-10 秒

---

### 3️⃣ 数据类别定义模块
**文件**：`docs/MODULE_3_CATEGORIES.md`  
**核心功能**：
- 定义 8 类地学数据类别
- 每类包含 6 个精选子主题
- 定义数据结构（DataPoint, SearchResult, SearchCategory）

**8 个类别**：
1. 气候资料
2. 地理与地形地貌资料
3. 交通、基础设施及经济条件
4. 水系与水文资料
5. 地质与矿产资料
6. 地球物理与地球化学资料
7. 遥感与地形地貌资料
8. 矿业权与法律政策资料

**数据结构**：
- `DataPoint` — 单个数据点
- `SearchResult` — 搜索结果
- `SearchCategory` — 类别定义

**关键函数**：
- `get_all_categories()` — 获取所有类别
- `get_category_by_id(id)` — 按 ID 查询类别

**依赖**：dataclasses（内置）

---

### 4️⃣ Prompt 模板模块
**文件**：`docs/MODULE_4_PROMPT_TEMPLATE.md`  
**核心功能**：
- Jinja2 模板用于构造 Claude 搜索 prompt
- 注入地理上下文（省市区、坐标、区块名称）
- 定义 JSON 输出格式要求

**模板变量**：
- `{{ category_id }}` — 类别 ID
- `{{ category_name }}` — 类别名称
- `{{ location_str }}` — 位置字符串
- `{{ coords_str }}` — 坐标范围
- `{{ area_name }}` — 区块名称
- `{{ kml_description }}` — 地质背景

**JSON 输出格式**：
```json
{
  "category": "...",
  "summary": "...",
  "data_points": [...],
  "key_findings": [...],
  "data_sources": [...]
}
```

**依赖**：jinja2

---

### 5️⃣ Web 搜索引擎模块
**文件**：`docs/MODULE_5_SEARCH_ENGINE.md`  
**核心功能**：
- 封装 claude -p subprocess 调用
- **串行搜索** 8 类数据（避免 API 限流）
- 4 次重试机制（延迟：20、40、60 秒）
- JSON 解析和错误处理
- 请求间隔控制（每 3 秒一个请求）

**核心类**：
- `SearchEngine` — 搜索引擎

**主要方法**：
- `render_prompt(category, location)` — 渲染 prompt
- `search_all_categories(location, categories)` — 串行搜索 8 类
- `_run_single_search(category_id, prompt, retry_count)` — 单个搜索（含重试）

**搜索策略**：
- **串行执行**：一次只发 1 个请求
- **请求间隔**：3 秒
- **重试延迟**：20, 40, 60 秒
- **总耗时**：~5 分钟

**依赖**：subprocess, json, re, time, jinja2  
**性能**：~5 分钟（8 类串行）

---

### 6️⃣ 报告生成模块
**文件**：`docs/MODULE_6_REPORT_BUILDER.md`  
**核心功能**：
- 使用 python-docx 生成 Word 文档
- 符合 GB/T 9704 中文文档标准
- 8 个完整章节 + 附录
- 自动设置中文字体（宋体、黑体）
- 生成封面、标题、数据表格、关键发现列表

**核心类**：
- `ReportBuilder` — 报告生成器

**主要方法**：
- `build_report(location, search_results, output_name)` — 生成完整报告
- `_add_heading(doc, text, level)` — 添加标题
- `_add_paragraph(doc, text, font_size, bold)` — 添加段落
- `_add_table(doc, headers, rows, col_widths)` — 添加表格
- `_add_key_findings(doc, findings)` — 添加关键发现

**文档结构**：
- 封面页
- 第一章：研究区基本信息
- 第二至九章：8 类数据
- 附录：数据来源

**样式标准**（GB/T 9704）：
- 页边距：上下 2.54cm，左右 3.17cm
- 字体：宋体（正文）/ 黑体（标题）
- 行距：1.5 倍
- 表格：单线表头灰底

**依赖**：python-docx, datetime  
**性能**：15-25 秒

---

### 7️⃣ Flask Web 后端模块
**文件**：`docs/MODULE_7_FLASK_BACKEND.md`  
**核心功能**：
- Flask Web 服务器
- KML 文件上传接口
- 报告生成流程
- Server-Sent Events (SSE) 流式进度推送
- 报告下载接口
- 任务状态管理

**API 端点**：

| 方法 | 路由 | 功能 |
|---|---|---|
| GET | `/` | 主页（HTML UI） |
| POST | `/api/upload-kml` | KML 上传 |
| GET | `/api/run/<task_id>` | 开始生成（SSE） |
| GET | `/api/download/<task_id>` | 下载报告 |
| GET | `/api/status/<task_id>` | 获取状态 |
| DELETE | `/api/cleanup/<task_id>` | 清理任务 |

**核心函数**：
- `upload_kml()` — KML 上传处理
- `run_report_generation()` — 报告生成流程（SSE）
- `download_report()` — 报告下载
- `get_task_status()` — 获取状态
- `cleanup_task()` — 清理任务

**目录结构**：
- `uploads/` — 上传文件存储
- `reports/` — 生成报告存储
- `templates/` — Jinja2 模板

**依赖**：flask, pathlib, json, uuid, datetime  
**配置**：
- 端口：8081
- 上传限制：100 MB
- 调试：DEBUG=True

---

### 8️⃣ Web 前端 UI 模块
**文件**：`docs/MODULE_8_WEB_UI.md`  
**核心功能**：
- 单页应用（SPA）
- KML 文件选择和拖拽上传
- 实时进度日志显示
- 错误提示和警告信息
- 报告下载按钮
- 响应式设计（支持移动端）

**技术**：
- HTML5
- CSS3（响应式 + 动画）
- 原生 JavaScript（无框架）
- Server-Sent Events (SSE)

**主要区域**：
1. **文件上传区**
   - 拖拽上传支持
   - 文件验证（.kml, .kmz）
   - 最大 100 MB

2. **进度日志区**
   - 实时日志流（SSE）
   - 5 个步骤显示
   - 自动滚动

3. **下载区**
   - 生成完成提示
   - 一键下载按钮

**主要函数**：
- `uploadAndGenerate()` — 上传并开始生成
- `streamReportGeneration(taskId)` — SSE 流式接收进度
- `addLog(message, type)` — 添加日志条目
- `downloadReport()` — 下载报告
- `resetForm()` — 重置表单

**样式**：
- 渐变背景：#667eea → #764ba2
- 成功色：#27ae60
- 错误色：#e74c3c
- 动画：旋转加载、按钮悬停

**浏览器兼容**：
- Chrome ✅
- Firefox ✅
- Safari ✅
- Edge ✅
- IE 11 ❌

**依赖**：无第三方库

---

## 🔗 模块依赖关系

```
用户浏览器
    ↓
[8️⃣ Web 前端] ←→ [7️⃣ Flask 后端]
                    ↓
                KML 上传 ↓
                [1️⃣ KML 解析]
                    ↓
                地理定位 ↓
                [2️⃣ 地理编码]
                    ↓
                准备搜索 ↓
                [3️⃣ 类别定义]
                    ↓
                构造 Prompt ↓
                [4️⃣ Prompt 模板]
                    ↓
                执行搜索 ↓
                [5️⃣ 搜索引擎]
                    ↓
                组装报告 ↓
                [6️⃣ 报告生成]
                    ↓
                下载报告 ↓
                用户本地
```

---

## 📊 模块统计

| 模块 | 文件 | 行数 | 语言 |
|---|---|---|---|
| 1️⃣ KML 解析 | kml_parser.py | 280 | Python |
| 2️⃣ 地理编码 | geocoder.py | 180 | Python |
| 3️⃣ 类别定义 | categories.py | 130 | Python |
| 4️⃣ Prompt 模板 | base_prompt.j2 | 50 | Jinja2 |
| 5️⃣ 搜索引擎 | search_engine.py | 200 | Python |
| 6️⃣ 报告生成 | report_builder.py | 350 | Python |
| 7️⃣ Flask 后端 | web/app.py | 250 | Python |
| 8️⃣ Web 前端 | web/templates/index.html | 350 | HTML/CSS/JS |
| **总计** | **8 个** | **~1,790 行** | **多语言** |

---

## ⚡ 性能指标

| 步骤 | 时间 | 备注 |
|---|---|---|
| 地理定位 | 5-10 秒 | Nominatim API |
| 数据搜索 | 4-6 分钟 | 8 类串行搜索 |
| 报告生成 | 15-25 秒 | python-docx |
| **总耗时** | **5-7 分钟** | **端到端** |

---

## 📖 如何使用这些文档

### 快速查找

- **想了解 KML 如何解析**？→ 读 MODULE_1_KML_PARSER.md
- **想了解地理编码**？→ 读 MODULE_2_GEOCODER.md
- **想修改搜索类别**？→ 读 MODULE_3_CATEGORIES.md
- **想调整搜索 prompt**？→ 读 MODULE_4_PROMPT_TEMPLATE.md
- **想优化搜索性能**？→ 读 MODULE_5_SEARCH_ENGINE.md
- **想修改报告格式**？→ 读 MODULE_6_REPORT_BUILDER.md
- **想添加新 API**？→ 读 MODULE_7_FLASK_BACKEND.md
- **想改进 UI**？→ 读 MODULE_8_WEB_UI.md

### 开发流程

1. **新增功能** → 从对应模块文档找到相关函数
2. **调试问题** → 查看"常见问题"和"故障排查"章节
3. **扩展功能** → 参考"使用示例"和"关键函数"
4. **性能优化** → 查看"性能指标"和"优化建议"

---

## 🎯 下一步

### 快速开始

```bash
cd "/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-reporter"
python3 web/app.py
# 访问 http://localhost:8081
```

### 常见任务

| 任务 | 文档 | 位置 |
|---|---|---|
| 修改 8 个搜索类别 | MODULE_3 | categories.py:40-140 |
| 调整搜索 prompt | MODULE_4 | templates/base_prompt.j2 |
| 改进报告样式 | MODULE_6 | report_builder.py:50-100 |
| 添加新 API 端点 | MODULE_7 | web/app.py:100-200 |
| 改进 UI 设计 | MODULE_8 | web/templates/index.html |

---

**文档最后更新**：2026-04-15  
**版本**：v0.1.0  
**维护者**：Claude
