# 快速启动指南

## 3 步启动 Geo-Reporter

### 步骤 1：打开终端进入项目目录

```bash
cd "/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-reporter"
```

### 步骤 2：启动 Web 服务器

```bash
python3 web/app.py
```

你会看到类似输出：
```
 * Running on http://127.0.0.1:8081
 * Press CTRL+C to quit
```

### 步骤 3：打开浏览器访问

在浏览器中访问：**http://localhost:8081**

---

## 使用说明

### 上传并生成报告

1. **选择 KML 文件**
   - 点击上传框或拖拽文件
   - 支持 .kml 和 .kmz 格式

2. **点击"上传并生成报告"**
   - 系统自动开始处理
   - 显示实时进度日志

3. **等待报告生成完成**
   - 过程包括：地理定位 → 数据搜索 → 报告生成
   - 通常需要 2-3 分钟

4. **下载 Word 报告**
   - 点击"下载 Word 报告"按钮
   - 文件为 .docx 格式，可在 Word/WPS 中编辑

---

## 测试用例

### 使用自带测试 KML 文件

项目已包含一个测试 KML 文件，可在浏览器上传：

**路径**：
```
/Users/mac/Desktop/Kevin's/Claude Code/Web Search/geo-downloader/test_baiyin.kml
```

**内容**：甘肃省白银市白银铜矿区勘探范围

### 运行集成测试

验证所有模块功能：

```bash
python3 test_integration.py
```

预期输出：
```
✅ KML 解析成功
✅ 地理编码成功
✅ 已定义 8 个搜索类别
✅ Prompt 渲染成功
✅ 报告生成器初始化成功
✅ 所有测试通过！
```

---

## 常见问题

### Q: 我没看到 Web 服务器启动？

**A**: 检查以下几点：
- 确认 Python 版本 >= 3.9：`python3 --version`
- 检查依赖是否安装：`pip3 list | grep -E "flask|lxml|shapely|python-docx"`
- 查看错误信息并确认没有端口冲突（8080）

### Q: KML 上传后没有反应？

**A**: 
- 检查 KML 文件是否有效（用 Google Earth 打开验证）
- 确保文件大小 < 100MB
- 检查浏览器控制台 (F12) 是否有错误

### Q: 搜索过程中失败了？

**A**:
- 检查网络连接（需访问 Nominatim 和 Claude API）
- 查看终端输出中是否有错误信息
- 如果某个类别搜索失败，报告会显示"[数据获取失败]"但不会中断整体流程

### Q: 如何修改报告输出目录？

**A**: 编辑 `config.yaml`：
```yaml
report:
  output_dir: /custom/path  # 改为你的目录
```

### Q: 如何添加更多搜索类别？

**A**: 编辑 `reporter/categories.py`，在 `CATEGORIES` 列表中添加新的 `SearchCategory` 对象。

---

## 项目结构概览

```
geo-reporter/
├── web/app.py                    # 🌐 Web 服务器（这是主入口）
├── reporter/                     # 🔧 核心功能模块
│   ├── kml_parser.py            # KML 解析
│   ├── geocoder.py              # 地理编码
│   ├── search_engine.py         # Web 搜索
│   ├── report_builder.py        # 报告生成
│   └── categories.py            # 数据定义
├── templates/                    # 📝 Prompt 模板
├── uploads/                      # 📥 上传文件
├── reports/                      # 📤 生成报告
├── web/templates/index.html     # 🎨 前端 UI
├── test_integration.py          # 🧪 集成测试
├── README.md                    # 📖 完整文档
└── config.yaml                  # ⚙️ 配置文件
```

---

## 核心工作流

```
1. 用户上传 KML 文件 (浏览器)
        ↓
2. KML 解析 (reporter/kml_parser.py)
   → 提取多边形、名称、描述
        ↓
3. 地理定位 (reporter/geocoder.py)
   → Nominatim API → 获取省市区信息
        ↓
4. 并行搜索 × 8 (reporter/search_engine.py)
   → claude -p subprocess × 8 类数据
        ↓
5. 报告生成 (reporter/report_builder.py)
   → python-docx → Word 文档
        ↓
6. 用户下载报告 (浏览器)
```

---

## 进阶用法

### 通过 CLI 生成报告（需补充 main.py）

```bash
python3 main.py --kml area.kml --output ./reports --name "自定义报告名"
```

### 修改并发搜索数量

编辑 `web/app.py`：
```python
search_engine = SearchEngine(str(TEMPLATES_DIR), max_workers=8)  # 改为 8
```

### 修改 Claude 模型

编辑 `reporter/search_engine.py`：
```python
search_engine = SearchEngine(str(TEMPLATES_DIR), model="sonnet")  # 改为 sonnet
```

---

## 反馈和支持

如有问题或建议，请查看完整文档：`README.md`

祝你使用愉快！🚀
