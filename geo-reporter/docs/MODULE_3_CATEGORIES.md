# 3️⃣ 数据类别定义模块技术文档

## 模块概述

**文件**：`reporter/categories.py`  
**行数**：~130 行  
**功能**：定义 8 类地学数据和数据结构

## 核心数据结构

### 1. DataPoint（数据点）

```python
@dataclass
class DataPoint:
    item: str      # 项目名称
    value: str     # 数值或描述
    source: str    # 数据来源
```

### 2. SearchResult（搜索结果）

```python
@dataclass
class SearchResult:
    category_id: str              # 类别 ID
    category_name: str            # 类别中文名称
    summary: str                  # 100-200 字概述
    data_points: List[DataPoint]  # 数据点列表
    key_findings: List[str]       # 3-5 个关键发现
    data_sources: List[str]       # 数据来源列表
    error: Optional[str]          # 错误信息（若有）
```

### 3. SearchCategory（搜索类别）

```python
@dataclass
class SearchCategory:
    id: str              # 类别 ID（英文标识）
    name: str            # 中文名称
    chapter_title: str   # 报告中的章节标题
    sub_topics: List[str] # 子主题列表
```

## 8 类地学数据

| # | ID | 中文名称 | 章节标题 | 子主题数 |
|---|---|---|---|---|
| 1 | `climate` | 气候资料 | 气候资料 | 6 |
| 2 | `geography` | 地理与地形地貌资料 | 地理与地形地貌资料 | 6 |
| 3 | `infrastructure` | 交通、基础设施及经济条件 | 交通、基础设施及经济条件 | 6 |
| 4 | `hydrology` | 水系与水文资料 | 水系与水文资料 | 6 |
| 5 | `geology` | 地质与矿产资料 | 地质与矿产资料 | 6 |
| 6 | `geophysics` | 地球物理与地球化学资料 | 地球物理与地球化学资料 | 6 |
| 7 | `remote_sensing` | 遥感与地形地貌资料 | 遥感与地形地貌资料 | 6 |
| 8 | `mining_rights` | 矿业权与法律政策资料 | 矿业权与法律政策资料 | 6 |

## 每类的子主题

### 1. 气候资料

1. 年均气温、月均气温变化规律
2. 年均降雨量、季节降雨分布
3. 蒸发量、相对湿度
4. 极端气温和降雨事件
5. 气象灾害类型与频率
6. 植被覆盖和地表水体

### 2. 地理与地形地貌资料

1. 主要地形地貌类型（高山、平原、盆地等）
2. 海拔范围和最高峰信息
3. 地面坡度分级
4. 地貌分区和地势走向
5. 河流与水系分布
6. 人工地形改造迹象

### 3. 交通、基础设施及经济条件

1. 公路网密度和主干道名称
2. 铁路分布和车站位置
3. 距离最近城市（100k人口以上）的距离
4. 电力供应和主变电站位置
5. 通信基础设施（光缆、基站）
6. 当地 GDP、主导产业、失业率

### 4. 水系与水文资料

1. 主要河流名称、流向、流域面积
2. 多年平均径流量和季节变化
3. 水系密度和分级
4. 洪水灾害历史和防御设施
5. 地下水位埋深和补给方式
6. 地表水体分布（湖泊、水库、沼泽）

### 5. 地质与矿产资料

1. 地层岩性和地质构造
2. 主要构造带和断层系统
3. 已知矿床名称和矿化类型
4. 矿化蚀变强度和分布规律
5. 成矿时代和成矿模式
6. 区域成矿背景和找矿前景

### 6. 地球物理与地球化学资料

1. 重力异常（布格重力）和磁力异常（总强度磁场）
2. 磁铁矿分布和磁异常梯度带
3. 地球化学元素背景值（Cu, Mo, Au, Ag 等）
4. 异常元素的空间分布和强度
5. 地震活动强度和震源机制
6. 热流异常和深部结构特征

### 7. 遥感与地形地貌资料

1. 可用遥感影像数据源（Landsat, Sentinel, GaoFen 等）
2. DEM 数据的空间分辨率和覆盖范围
3. 地表侵蚀分级和沟壑密度
4. 植被指数（NDVI）和土壤含水量
5. 土地利用/土地覆盖类型分布
6. 热红外辐射温度和热异常区

### 8. 矿业权与法律政策资料

1. 已登记探矿权和采矿权分布
2. 采矿许可证有效期和权利人
3. 环境影响评价要求和污染防治规范
4. 生态保护和自然保护区分界
5. 投资政策和税收优惠
6. 禁止开采区和限制开采区划分

## 使用函数

### 获取全部类别

```python
from reporter.categories import get_all_categories

categories = get_all_categories()  # 返回 8 个 SearchCategory 对象列表
for cat in categories:
    print(f"{cat.id}: {cat.name}")
```

### 按 ID 查询类别

```python
from reporter.categories import get_category_by_id

cat = get_category_by_id("climate")
if cat:
    print(f"类别名称：{cat.name}")
    print(f"子主题：{cat.sub_topics}")
```

## 使用示例

```python
from reporter.categories import CATEGORIES, DataPoint, SearchResult

# 构建搜索结果
result = SearchResult(
    category_id="climate",
    category_name="气候资料",
    summary="该地区属温带大陆性干旱气候...",
    data_points=[
        DataPoint(item="年均气温", value="7.0~8.5℃", source="气象部门"),
        DataPoint(item="年均降雨", value="200-280mm", source="遥感数据"),
    ],
    key_findings=["冬季寒冷干燥", "降水集中在夏季"],
    data_sources=["气象部门", "遥感卫星"]
)

print(result.summary)
```

## 扩展说明

### 添加新类别

编辑 `CATEGORIES` 列表：

```python
CATEGORIES = [
    # ... 现有 8 个 ...
    SearchCategory(
        id="new_category",
        name="新类别名称",
        chapter_title="报告中的标题",
        sub_topics=["子主题1", "子主题2", ...]
    )
]
```

### 修改子主题

直接编辑对应 `SearchCategory` 的 `sub_topics` 列表即可。

## 依赖

- `dataclasses`：Python 3.7+ 内置

## 注意事项

1. **类别顺序**：报告按列表顺序生成章节
2. **ID 唯一性**：必须保证所有 ID 唯一
3. **字数限制**：summary 应控制在 100-200 字
4. **子主题数量**：建议 6 个（对应报告布局）
