# 1️⃣ KML 解析模块技术文档

## 模块概述

**文件**：`reporter/kml_parser.py`  
**行数**：~280 行  
**功能**：解析 KML/KMZ 文件，提取几何体和元数据

## 核心功能

### 1. KML 文件解析
- **支持格式**：.kml、.kmz（压缩）、.ovkml、.ovkmz
- **几何体类型**：Polygon、Point、LineString、MultiGeometry
- **命名空间兼容**：支持 KML 2.2 和 KML 2.1

### 2. 数据提取

```python
geometry, bbox, name, area_name, description = parse_kml(kml_path)
```

**返回值**：
- `geometry`：Shapely 几何体（Polygon 或 MultiPolygon）
- `bbox`：边界框 `(min_lon, min_lat, max_lon, max_lat)`
- `name`：文件名（不含扩展名）
- `area_name`：KML 中 `<Placemark><name>` 值
- `description`：KML 中 `<Placemark><description>` 值

### 3. 特殊处理

**Point 缓冲**：
- 如果 KML 只包含 Point，自动缓冲为圆形多边形
- 缓冲距离：0.1 度（约 11 km）

**MultiGeometry 合并**：
- 保留多个独立多边形的形状
- 避免 `unary_union` 产生凸包扭曲

## 关键函数

| 函数 | 功能 | 返回值 |
|---|---|---|
| `parse_kml(kml_path)` | 解析单个 KML 文件 | `(geometry, bbox, name, area_name, desc)` |
| `parse_kml_folder(folder)` | 批量解析文件夹 | `List[(geometry, bbox, name, area_name, desc)]` |
| `_parse_coordinates(text)` | 解析坐标字符串 | `[(lon, lat), ...]` |
| `_extract_polygon(element)` | 从 XML 提取 Polygon | `Shapely Polygon` |
| `_extract_placemark_metadata()` | 提取 name 和 description | `(name, description)` |

## 错误处理

```python
try:
    geometry, bbox, name, area_name, description = parse_kml(kml_path)
except KMLParseError as e:
    print(f"KML 解析失败：{e}")
```

**常见错误**：
- 文件不存在
- 格式不支持
- 文件损坏或格式错误
- 未找到几何体

## 使用示例

```python
from reporter.kml_parser import parse_kml

# 解析 KML 文件
kml_path = "study_area.kml"
geometry, bbox, name, area_name, description = parse_kml(kml_path)

print(f"区块名称：{area_name}")
print(f"边界框：{bbox}")
print(f"几何体类型：{geometry.geom_type}")
print(f"面积：{geometry.area} 平方度")
```

## 依赖

- `lxml`：XML 解析
- `shapely`：几何体处理
- `zipfile`：KMZ 解压

## 性能

- **解析时间**：~1 秒（取决于文件大小）
- **支持的最大文件**：无限制（内存允许）

## 注意事项

1. **坐标系统**：假设使用 WGS84（EPSG:4326）
2. **KML 命名空间**：自动检测 KML 2.2 或 2.1
3. **缓冲距离**：Point 默认缓冲 0.1 度，可自定义
4. **多边形有效性**：自动检查 `polygon.is_valid`
