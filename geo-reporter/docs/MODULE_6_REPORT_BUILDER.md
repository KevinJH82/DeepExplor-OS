# 6️⃣ 报告生成模块技术文档

## 模块概述

**文件**：`reporter/report_builder.py`  
**行数**：~350 行  
**功能**：使用 python-docx 生成专业中文 Word 报告

## 核心类：ReportBuilder

### 初始化

```python
from reporter.report_builder import ReportBuilder

builder = ReportBuilder(output_dir="./reports")
```

**参数**：
- `output_dir`：报告输出目录（默认 `./reports`）

### 主要方法

```python
report_path = builder.build_report(
    location=location_context,
    search_results=search_results_dict,
    output_name="custom_name"  # 可选
)
```

**返回**：报告文件路径

## 报告结构

### 完整报告布局

```
封面页
├─ 标题：地质勘探综合报告
├─ 研究区名称
├─ 位置和坐标
└─ 生成日期

分页符

第一章  研究区基本信息
├─ 1.1 地理位置
├─ 1.2 行政区划
└─ 1.3 地质背景

分页符

第二章  气候资料
├─ 概述段落
├─ 数据表格
├─ 关键发现列表
└─ [分页符]

第三章  地理与地形地貌资料
├─ ... (同上)
└─ [分页符]

... (第四至第九章) ...

附录  数据来源与参考文献
└─ 数据来源列表
```

## 文档样式设置

### GB/T 9704 标准

```python
# 页面设置
section.top_margin = Cm(2.54)      # 上边距
section.bottom_margin = Cm(2.54)   # 下边距
section.left_margin = Cm(3.17)     # 左边距
section.right_margin = Cm(3.17)    # 右边距
```

### 字体设置

```python
FONT_SONGTI = "宋体"      # 正文字体
FONT_HEITI = "黑体"       # 标题字体
```

### 标题级别

| 级别 | 字体 | 大小 | 对齐 | 加粗 |
|---|---|---|---|---|
| 1 | 黑体 | 16pt | 居中 | ✓ |
| 2 | 黑体 | 14pt | 左对 | ✓ |
| 3 | 宋体 | 12pt | 左对 | ✓ |

### 段落格式

```python
# 正文
font_size = 12pt
line_spacing = 1.5        # 1.5 倍行距
space_before = Pt(6)      # 段前 6pt
space_after = Pt(6)       # 段后 6pt
```

## 核心方法详解

### 1. _add_heading()

添加标题。

```python
builder._add_heading(doc, "第一章  标题文本", level=1)
```

**参数**：
- `text`：标题文本
- `level`：标题级别（1、2、3）

**功能**：
- 自动设置中文字体（东亚字体）
- 应用对应级别样式
- 设置颜色和对齐

### 2. _add_paragraph()

添加段落。

```python
builder._add_paragraph(
    doc,
    "段落文本内容",
    font_size=12,
    bold=False,
    alignment=None
)
```

**参数**：
- `text`：段落文本
- `font_size`：字体大小（默认 12pt）
- `bold`：是否加粗
- `alignment`：对齐方式

### 3. _add_table()

添加数据表格。

```python
builder._add_table(
    doc,
    headers=["项目", "数值/描述", "数据来源"],
    rows=[
        ["年均气温", "7.0~8.5℃", "气象部门"],
        ["年均降雨", "200-280mm", "国家气象数据"]
    ],
    col_widths=[3, 5, 4]  # 列宽（厘米）
)
```

**特点**：
- 表头背景灰色（RGB 128, 128, 128）
- 表头文本白色加粗
- 单线表格
- 自动设置中文字体

### 4. _add_key_findings()

添加关键发现列表。

```python
builder._add_key_findings(
    doc,
    findings=["发现1", "发现2", "发现3"]
)
```

**特点**：
- 符号列表样式（✓）
- 1.5 倍行距
- 自动中文字体设置

## 完整报告生成流程

```
[build_report() 调用]
   ↓
[创建 Document]
   ├─ 设置页边距
   └─ 设置样式
   ↓
[生成封面页]
   ├─ 标题
   ├─ 区块信息
   └─ 日期
   ↓
[添加分页符]
   ↓
[第一章：基本信息]
   ├─ 地理位置
   ├─ 行政区划
   └─ 地质背景
   ↓
[添加分页符]
   ↓
[第二至九章：数据章节]
   ├─ 循环处理 8 个 SearchResult
   ├─ 为每个类别：
   │  ├─ 添加标题
   │  ├─ 检查是否失败
   │  │  ├─ 失败 → 显示 [数据获取失败] 信息
   │  │  └─ 成功 → 添加 3 部分：
   │  │     ├─ 概述段落
   │  │     ├─ 数据表格
   │  │     └─ 关键发现列表
   │  └─ 添加分页符（除最后一章）
   ↓
[添加分页符]
   ↓
[附录：数据来源]
   ├─ 收集所有数据来源
   └─ 生成排序列表
   ↓
[保存文档]
   └─ 返回文件路径
```

## 使用示例

### 完整报告生成

```python
from reporter.report_builder import ReportBuilder
from reporter.kml_parser import parse_kml
from reporter.geocoder import create_location_context
from reporter.search_engine import SearchEngine

# 准备数据
geometry, bbox, name, area_name, description = parse_kml("area.kml")
location = create_location_context(bbox, area_name, description)
search_engine = SearchEngine("./templates")
results = search_engine.search_all_categories(location)

# 生成报告
builder = ReportBuilder("./reports")
report_path = builder.build_report(location, results)
print(f"报告已生成：{report_path}")
```

### 自定义报告名称

```python
report_path = builder.build_report(
    location,
    results,
    output_name="白银铜矿_2026-04-15"
)
# 输出：reports/白银铜矿_2026-04-15.docx
```

## 文件名规则

如不指定 `output_name`，自动生成：

```python
timestamp = datetime.now().strftime("%Y%m%d")
output_name = f"{location.area_name}_{timestamp}"
# 例：内蒙油田_20260415
```

## 中文字体处理

### 问题

Windows/Mac/Linux 默认字体可能不支持中文。

### 解决方案

**在 Word 中打开报告后**：

1. 全选（Ctrl+A）
2. 选择"宋体"或"微软雅黑"
3. 保存

**或使用系统字体**：

修改代码中的字体名称：

```python
FONT_SONGTI = "微软雅黑"    # 或其他系统中文字体
FONT_HEITI = "黑体"
```

## 性能

- **报告生成时间**：15-25 秒
- **文件大小**：30-50 KB（取决于数据量）
- **兼容性**：Word 2010+、WPS、LibreOffice

## 常见问题

### 问题：字体显示不正确

**解决**：检查系统是否安装了"宋体"和"黑体"

### 问题：表格内容超出页面

**解决**：减少数据点数量或减小字体大小

### 问题：中文字符乱码

**解决**：确保文件编码为 UTF-8

## 依赖

- `python-docx`：Word 文档操作
- `datetime`：时间戳处理

## 注意事项

1. **字体兼容性**：确保系统安装了所用字体
2. **编码**：始终使用 UTF-8 编码
3. **文件权限**：输出目录必须可写
4. **重复文件**：如果同名文件存在会被覆盖
