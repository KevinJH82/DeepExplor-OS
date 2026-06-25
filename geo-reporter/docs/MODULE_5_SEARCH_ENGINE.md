# 5️⃣ Web 搜索引擎模块技术文档

## 模块概述

**文件**：`reporter/search_engine.py`  
**行数**：~200 行  
**功能**：封装 Claude subprocess 调用，实现串行搜索和重试机制

## 核心类：SearchEngine

### 初始化

```python
from reporter.search_engine import SearchEngine

search_engine = SearchEngine(
    templates_dir="./templates",    # Jinja2 模板目录
    model="haiku",                  # Claude 模型（默认 haiku）
    max_workers=1,                  # 工作进程数（已改为串行）
    timeout_sec=120                 # 每个请求超时时间
)
```

## 主要方法

### 1. render_prompt()

渲染搜索 prompt 模板。

```python
prompt = search_engine.render_prompt(category, location)
```

**参数**：
- `category`：SearchCategory 对象
- `location`：LocationContext 对象

**返回**：渲染后的 prompt 字符串

### 2. search_all_categories()

串行搜索所有 8 个类别。

```python
results = search_engine.search_all_categories(
    location=location_context,
    categories=None  # 不指定则搜索所有
)
```

**返回**：`Dict[str, SearchResult]`

**工作流**：
1. 为每个类别构建 prompt
2. **按顺序**逐个发送搜索请求
3. 每两个请求间等待 3 秒
4. 触发限流时自动重试（延迟：20、40、60 秒）

### 3. _run_single_search()

执行单个搜索请求（含重试）。

```python
result = search_engine._run_single_search(
    category_id="climate",
    prompt="...",
    retry_count=4
)
```

**参数**：
- `category_id`：类别 ID
- `prompt`：搜索 prompt
- `retry_count`：重试次数（默认 4）

**返回**：SearchResult 对象

## 搜索流程

### 完整搜索流程

```
[搜索开始]
   ↓
[构建 8 个 Prompt]
   ↓
[第 1 类搜索]
   ├─ 请求 Claude
   ├─ 触发限流? → 等 20s → 重试
   ├─ 触发限流? → 等 40s → 重试
   ├─ 触发限流? → 等 60s → 重试
   └─ 返回结果
   ↓
[等待 3 秒]
   ↓
[第 2 类搜索]
   ├─ ... (同上)
   └─ 返回结果
   ↓
... (重复 8 次) ...
   ↓
[所有搜索完成]
   ↓
返回 Dict[str, SearchResult]
```

### 单个请求流程

```
[调用 claude -p subprocess]
   ↓
成功? → [解析 JSON]
        → [返回 SearchResult]
   ↓
失败? → [检查错误类型]
   ├─ API 限流 → [重试逻辑]
   ├─ 超时 → [返回错误结果]
   └─ 其他 → [返回错误结果]
```

## 重试机制

### 速率限制检测

```python
if "请求过于频繁" in error_msg or "Too many requests" in error_msg:
    # 触发重试
```

### 重试延迟

| 重试次 | 等待时间 | 总累积时间 |
|---|---|---|
| 第 1 次 | 20 秒 | 20 秒 |
| 第 2 次 | 40 秒 | 60 秒 |
| 第 3 次 | 60 秒 | 120 秒 |
| 失败返回 | - | - |

### JSON 解析失败处理

```python
try:
    data = json.loads(json_str)
except json.JSONDecodeError:
    # 返回纯文本 fallback 结果
    return SearchResult(
        summary=output[:300],
        data_points=[],
        key_findings=[],
        data_sources=[]
    )
```

## 错误处理

### 异常类型

```python
class SearchEngineError(Exception):
    pass
```

### 错误返回

所有错误都返回有效的 SearchResult，包含 `error` 字段：

```python
SearchResult(
    category_id="climate",
    category_name="气候资料",
    summary="",
    error="Claude 执行失败：API Error: 400..."
)
```

## 使用示例

### 完整搜索流程

```python
from reporter.kml_parser import parse_kml
from reporter.geocoder import create_location_context
from reporter.search_engine import SearchEngine

# 1. 解析 KML
kml_path = "study_area.kml"
geometry, bbox, name, area_name, description = parse_kml(kml_path)

# 2. 地理编码
location = create_location_context(bbox, area_name, description)

# 3. 搜索数据
search_engine = SearchEngine("./templates")
results = search_engine.search_all_categories(location)

# 4. 检查结果
for cat_id, result in results.items():
    if result.error:
        print(f"❌ {result.category_name}: {result.error}")
    else:
        print(f"✅ {result.category_name}")
        print(f"   概述：{result.summary[:100]}...")
        print(f"   数据点：{len(result.data_points)} 条")
```

### 单个类别搜索

```python
from reporter.categories import get_category_by_id

category = get_category_by_id("climate")
prompt = search_engine.render_prompt(category, location)
result = search_engine._run_single_search(category.id, prompt)
print(result.summary)
```

## 性能优化

### 当前策略

| 方面 | 参数 | 说明 |
|---|---|---|
| 并发模式 | 串行（max_workers=1） | 避免 API 限流 |
| 请求间隔 | 3 秒 | 符合 Nominatim 的 1 req/sec |
| 单个超时 | 120 秒 | 完整搜索时间 |
| 总重试次 | 4 次 | 最多等 120 秒 |

### 优化建议

**如果想加快速度**：

```python
# 改为并行（需承受限流风险）
search_engine = SearchEngine(..., max_workers=4)

# 减少子主题（简化 prompt）
# 修改 categories.py 中的 sub_topics
```

**如果限流仍频繁**：

```python
# 增加请求间隔
time.sleep(5)  # 改为 5 秒

# 增加重试延迟
wait_time = 30 * (attempt + 1)  # 改为 30, 60, 90 秒
```

## 日志输出

### 信息级别

```
[INFO] 搜索进度：1/8 - climate
[INFO] 等待 3 秒再发送下一个请求...
[DEBUG] climate 触发 API 限流，等待 20 秒后重试...
```

### 调试

终端会输出详细日志，查看进度和错误：

```bash
tail -f /tmp/flask.log | grep -E "\[INFO\]|\[DEBUG\]"
```

## 依赖

- `subprocess`：执行 `claude -p` 命令
- `json`：JSON 解析
- `re`：正则表达式（提取 JSON 代码块）
- `time`：延迟控制
- `jinja2`：模板渲染
- `concurrent.futures`：（目前未用，串行模式）

## 注意事项

1. **Claude CLI 必需**：需要安装并配置 `claude` 命令
2. **网络连接**：需要访问 Claude API
3. **权限配置**：`.claude/settings.local.json` 需要配置 WebSearch 权限
4. **UTF-8 编码**：确保 prompt 和响应都是 UTF-8
5. **API 限流**：生产环境应监控限流情况
