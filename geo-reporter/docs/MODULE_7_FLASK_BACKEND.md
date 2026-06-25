# 7️⃣ Flask Web 后端模块技术文档

## 模块概述

**文件**：`web/app.py`  
**行数**：~250 行  
**功能**：Flask 后端服务，处理 KML 上传、报告生成、文件下载

## 应用配置

### Flask 应用初始化

```python
app = Flask(__name__, template_folder=str(BASE_DIR / "web" / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB 限制
```

### 目录配置

```python
BASE_DIR = Path(__file__).parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"       # 上传文件存储
REPORTS_DIR = BASE_DIR / "reports"       # 生成报告存储
TEMPLATES_DIR = BASE_DIR / "templates"   # Jinja2 模板目录
```

## API 端点

### 1. GET `/`

主页路由，返回 HTML UI。

```
GET http://localhost:8081/
返回：index.html 页面
```

### 2. POST `/api/upload-kml`

上传并验证 KML 文件。

**请求**：
```
POST /api/upload-kml
Content-Type: multipart/form-data
Body: { file: [KML 文件] }
```

**响应**（成功）：
```json
{
  "task_id": "a1b2c3d4",
  "status": "kml_uploaded",
  "kml_name": "area.kml",
  "area_name": "白银铜矿区勘探范围"
}
```

**响应**（失败）：
```json
{
  "error": "KML parse error: ..."
}
```

**工作流**：
1. 生成唯一 task_id
2. 保存上传文件
3. 解析 KML 文件
4. 存储任务状态
5. 返回 task_id

### 3. GET `/api/run/<task_id>`

开始生成报告（Server-Sent Events）。

**请求**：
```
GET /api/run/a1b2c3d4
```

**响应**（流式）：
```
data: {"step": 1, "message": "正在确定地理位置..."}
data: {"step": 2, "message": "正在搜索数据..."}
data: {"step": 3, "message": "数据搜索完成：8/8 个类别成功"}
data: {"step": 4, "message": "正在生成报告..."}
data: {"step": 5, "message": "报告生成完成！", "report_path": "..."}
```

**特点**：
- Server-Sent Events (SSE)
- 前端实时接收进度
- 自动重连机制

### 4. GET `/api/download/<task_id>`

下载生成的报告。

**请求**：
```
GET /api/download/a1b2c3d4
```

**响应**：
```
Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document
Body: [Word 文档二进制]
```

**文件名**：报告原始名称

### 5. GET `/api/status/<task_id>`

获取任务状态。

**请求**：
```
GET /api/status/a1b2c3d4
```

**响应**：
```json
{
  "task_id": "a1b2c3d4",
  "status": "completed",
  "created_at": "2026-04-15T14:30:00",
  "has_report": true
}
```

**status 值**：
- `kml_uploaded`：KML 已上传
- `processing`：正在处理
- `completed`：已完成
- `failed`：处理失败

### 6. DELETE `/api/cleanup/<task_id>`

清理任务（删除临时文件）。

**请求**：
```
DELETE /api/cleanup/a1b2c3d4
```

**响应**：
```json
{
  "message": "Task cleaned up"
}
```

**功能**：
- 删除上传的 KML 文件
- 删除生成的报告文件
- 移除任务状态记录

## 任务管理

### 任务状态字典

```python
tasks = {
    "task_id_1": {
        "status": "kml_uploaded",
        "kml_path": "/path/to/file.kml",
        "kml_name": "file.kml",
        "geometry": <Shapely 对象>,
        "bbox": (min_lon, min_lat, max_lon, max_lat),
        "name": "file",
        "area_name": "区块名",
        "description": "地质背景",
        "created_at": "2026-04-15T14:30:00"
    },
    "task_id_2": { ... }
}
```

## 报告生成流程（SSE）

### Step 1: 地理定位（5-10 秒）

```python
location = create_location_context(bbox, area_name, description)
yield f"data: {json.dumps({'step': 1, 'message': '正在确定地理位置...'})}\n\n"
```

### Step 2: 数据搜索（5-6 分钟）

```python
search_results = search_engine.search_all_categories(location)
# 统计成功数
success = sum(1 for r in search_results.values() if not r.error)
yield f"data: {json.dumps({'step': 3, 'message': f'数据搜索完成：{success}/{total}...'})}\n\n"
```

### Step 3: 报告生成（15-25 秒）

```python
report_path = report_builder.build_report(location, search_results)
yield f"data: {json.dumps({'step': 5, 'message': '报告生成完成！', 'report_path': report_path})}\n\n"
```

### 错误处理

```python
if error_occurs:
    yield f"data: {json.dumps({'error': 'Error message'})}\n\n"
    return
```

## 路由映射

| 方法 | 路由 | 功能 |
|---|---|---|
| GET | `/` | 主页 |
| POST | `/api/upload-kml` | KML 上传 |
| GET | `/api/run/<task_id>` | 开始报告生成（SSE） |
| GET | `/api/download/<task_id>` | 下载报告 |
| GET | `/api/status/<task_id>` | 获取状态 |
| DELETE | `/api/cleanup/<task_id>` | 清理任务 |

## 文件处理

### 上传文件处理

```python
# 检查文件
if "file" not in request.files:
    return jsonify({"error": "No file provided"}), 400

file = request.files["file"]
if not file.filename.lower().endswith((".kml", ".kmz")):
    return jsonify({"error": "Only .kml/.kmz supported"}), 400

# 保存文件
upload_path = UPLOADS_DIR / f"{task_id}_{file.filename}"
file.save(str(upload_path))
```

### 下载文件处理

```python
return send_file(
    report_path,
    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    as_attachment=True,
    download_name=Path(report_path).name
)
```

## 错误处理

### 异常捕获

```python
try:
    # ... 处理逻辑 ...
except KMLParseError as e:
    return jsonify({"error": f"KML parse error: {str(e)}"}), 400
except GeocoderError as e:
    return jsonify({"error": f"Geocoding error: {str(e)}"}), 400
except SearchEngineError as e:
    return jsonify({"error": f"Search error: {str(e)}"}), 400
except Exception as e:
    return jsonify({"error": f"Unexpected error: {str(e)}"}), 500
```

## 使用示例

### Python 客户端

```python
import requests
import time

# 上传 KML
with open("area.kml", "rb") as f:
    response = requests.post(
        "http://localhost:8081/api/upload-kml",
        files={"file": f}
    )
    data = response.json()
    task_id = data["task_id"]

# 开始生成
response = requests.get(
    f"http://localhost:8081/api/run/{task_id}",
    stream=True
)

for line in response.iter_lines():
    if line:
        print(line.decode())

# 下载报告
response = requests.get(f"http://localhost:8081/api/download/{task_id}")
with open("report.docx", "wb") as f:
    f.write(response.content)

# 清理
requests.delete(f"http://localhost:8081/api/cleanup/{task_id}")
```

## 启动服务器

```bash
cd geo-reporter
python3 web/app.py

# 访问
# http://localhost:8081
```

**生产部署**：

```bash
# 使用 WSGI 服务器（如 Gunicorn）
gunicorn -w 4 -b 0.0.0.0:8081 web.app:app
```

## 依赖

- `flask`：Web 框架
- `pathlib`：文件路径处理
- `json`：JSON 序列化
- `uuid`：唯一 ID 生成
- `datetime`：时间戳

## 配置

### 上传限制

```python
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 改为其他大小
```

### 调试模式

```python
app.run(debug=True)   # 开启调试
app.run(debug=False)  # 生产环境关闭
```

## 注意事项

1. **临时文件**：上传和报告文件会保留，定期清理
2. **并发限制**：Flask 开发服务器单线程，生产需要 WSGI
3. **CORS**：如需跨域请求，需要配置 CORS
4. **日志**：所有操作都会打印到 stdout
