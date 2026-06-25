# 4️⃣ Prompt 模板模块技术文档

## 模块概述

**文件**：`templates/base_prompt.j2`  
**行数**：~50 行  
**功能**：Jinja2 模板，用于构造 Claude 搜索 prompt

## 模板结构

### 模板变量

```jinja2
{{ category_id }}          # 类别 ID（如 "climate"）
{{ category_name }}        # 类别中文名称（如 "气候资料"）
{{ location_str }}         # 位置字符串（如 "甘肃省 白银区"）
{{ country }}              # 国家名称（如 "中国"）
{{ coords_str }}           # 坐标范围（如 "东经104~104.5°，北纬36.4~36.7°"）
{{ area_name }}            # 区块名称（如 "白银铜矿区勘探范围"）
{{ kml_description }}      # 地质背景描述
{{ sub_topics }}           # 子主题列表（通过 enumerate 循环）
```

### 循环处理

```jinja2
{% for i, topic in enumerate(sub_topics, 1) %}
{{ i }}. {{ topic }}
{% endfor %}
```

## 完整模板示例

```jinja2
你是一位资深地质勘探报告专家，精通地球物理、地球化学、矿床学等领域。

请基于以下研究区信息，搜索并整理该区域的{{ category_name }}资料：

【研究区信息】
位置：{{ location_str }}（{{ country }}）
坐标范围：{{ coords_str }}
区块名称：{{ area_name }}
地质背景：{{ kml_description }}

【搜索内容】
请详细搜索并整理以下具体内容：
{% for i, topic in enumerate(sub_topics, 1) %}
{{ i }}. {{ topic }}
{% endfor %}

【输出要求】
请用中文，以以下 JSON 格式返回结果。只返回 JSON，不要其他文本。

```json
{
  "category": "{{ category_id }}",
  "summary": "100-200 字的该类别资料综合描述，准确专业。",
  "data_points": [
    {
      "item": "数据项名称",
      "value": "数值或描述",
      "source": "来源"
    }
  ],
  "key_findings": [
    "关键发现1",
    "关键发现2",
    "关键发现3"
  ],
  "data_sources": [
    "数据来源机构或资料"
  ]
}
```

如果没有找到某个子主题的具体信息，请在 summary 中说明，并在 data_points 中留空或注明"暂无"。
```

## 输出 JSON 格式

### 示例输出

```json
{
  "category": "climate",
  "summary": "白银区位于甘肃省中部高原边缘，属典型的温带大陆性干旱气候。区域气候特征为：冬季寒冷干燥，夏季温暖，年温差大；降水集中在6-9月，全年降水稀少；蒸发强烈，相对湿度低；易发生干旱、冰雹等气象灾害。该气候条件对矿床风化、水文地球化学过程和勘探工作具有重要影响。",
  "data_points": [
    {
      "item": "年均气温",
      "value": "7.0~8.5℃",
      "source": "气象部门"
    },
    {
      "item": "年均降雨量",
      "value": "200-280mm",
      "source": "国家气象数据"
    }
  ],
  "key_findings": [
    "属温带大陆性干旱气候",
    "降水集中在 6-9 月，冬春季干旱少雨",
    "易发生干旱和冰雹灾害"
  ],
  "data_sources": [
    "国家气象部门",
    "甘肃省气象部门",
    "卫星遥感数据"
  ]
}
```

## 模板设计原则

### 1. 上下文注入

- **地理位置**：帮助 Claude 提供特定区域的信息
- **坐标范围**：提高搜索准确性
- **地质背景**：为搜索提供学科背景

### 2. 任务清晰

- 明确指定搜索范围（8 个子主题）
- 要求结构化 JSON 输出
- 明确数据格式要求

### 3. 语言设置

- 使用中文指示语，便于 Claude 理解
- 使用地质专业术语
- 指定专家角色

### 4. 输出约束

- 固定 JSON 格式
- 限制字数（summary 100-200 字）
- 列表元素数量（3-5 条关键发现）

## 渲染过程

### 在代码中使用

```python
from jinja2 import Environment, FileSystemLoader

# 初始化 Jinja2 环境
env = Environment(loader=FileSystemLoader("templates"))
template = env.get_template("base_prompt.j2")

# 准备变量
variables = {
    "category_id": "climate",
    "category_name": "气候资料",
    "location_str": "甘肃省 白银区",
    "country": "中国",
    "coords_str": "东经104.00~104.50°，北纬36.40~36.70°",
    "area_name": "白银铜矿区勘探范围",
    "kml_description": "北祁连加里东期岛弧带...",
    "sub_topics": ["年均气温...", "年均降雨..."],
    "enumerate": enumerate  # Jinja2 内置函数
}

# 渲染
prompt = template.render(**variables)
print(prompt)
```

## 定制模板

### 修改提示词

编辑 `templates/base_prompt.j2` 中的提示词部分：

```jinja2
你是一位[自定义角色描述]...
```

### 修改 JSON 格式

如需改变输出格式，编辑 JSON schema 部分：

```jinja2
```json
{
  "category": "{{ category_id }}",
  "your_field": "自定义字段"
}
```
```

### 修改搜索要求

修改【搜索内容】或【输出要求】部分即可。

## 最佳实践

1. **保持一致**：所有类别使用同一模板
2. **明确限制**：指定 summary 字数、findings 数量
3. **格式严格**：要求明确的 JSON 格式，便于解析
4. **错误处理**：告诉 Claude 如何处理缺失数据

## 故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| JSON 解析失败 | Claude 输出格式不对 | 检查 prompt 中 JSON 示例是否清晰 |
| 字段缺失 | Claude 没有返回某些字段 | 在 prompt 中明确要求所有字段 |
| 中文编码错误 | 模板文件编码问题 | 确保使用 UTF-8 编码 |

## 依赖

- `jinja2`：模板引擎（已内置）

## 注意事项

1. **UTF-8 编码**：确保文件使用 UTF-8 编码
2. **变量完整性**：所有模板变量必须传入
3. **特殊字符**：JSON 中的特殊字符需要转义
