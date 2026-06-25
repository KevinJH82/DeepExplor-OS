# 2️⃣ 地理编码模块技术文档

## 模块概述

**文件**：`reporter/geocoder.py`  
**行数**：~180 行  
**功能**：Nominatim 反向地理编码，获取中文行政区划

## 核心功能

### 1. 反向地理编码

```python
location_context = create_location_context(bbox, area_name, kml_description)
```

**输入**：
- `bbox`：边界框 `(min_lon, min_lat, max_lon, max_lat)`
- `area_name`：区块名称
- `kml_description`：地质背景描述

**输出**：`LocationContext` 对象

### 2. LocationContext 数据结构

```python
@dataclass
class LocationContext:
    country: str              # 国家名称
    country_code: str         # 国家代码（ISO 3166-1）
    province: str             # 省/州
    city: str                 # 城市/市
    district: str             # 区/县
    centroid_lat: float       # 中心点纬度
    centroid_lon: float       # 中心点经度
    min_lon: float            # 最小经度
    min_lat: float            # 最小纬度
    max_lon: float            # 最大经度
    max_lat: float            # 最大纬度
    area_name: str            # KML 区块名称
    kml_description: str      # KML 地质背景
```

### 3. 便捷属性

```python
# 坐标范围字符串
coords_str = location.coords_str  
# → "东经104.00~104.50°，北纬36.40~36.70°"

# 位置字符串
location_str = location.location_str  
# → "甘肃省 白银区"
```

## Nominatim API

### API 调用

```python
geo_data = reverse_geocode(lat, lon, retries=3, timeout=10)
```

**参数**：
- `lat`, `lon`：纬度和经度
- `retries`：重试次数（默认 3）
- `timeout`：请求超时秒数（默认 10）

**返回**：`{"address": {...}}`

### 地址解析

Nominatim 返回的地址字段：

| 字段 | 含义 | 备注 |
|---|---|---|
| `country` | 国家 | 中文名称 |
| `country_code` | 国家代码 | ISO 标准 |
| `state` | 省份 | 中文名称 |
| `city` | 城市 | 优先级：city > town |
| `county` | 县区 | 备用：district |
| `district` | 区 | 最后备选 |

## 错误处理

```python
try:
    location = create_location_context(bbox, area_name, description)
except GeocoderError as e:
    print(f"地理编码失败：{e}")
```

**常见错误**：
- 网络连接失败
- API 请求超时
- 坐标超出范围
- 响应 JSON 解析失败

### 速率限制处理

- **Nominatim 限制**：1 请求/秒
- **自动重试**：指数退避（2^n 秒）
- **HTTP 429 处理**：自动等待重试

## 使用示例

```python
from reporter.kml_parser import parse_kml
from reporter.geocoder import create_location_context

# 解析 KML
geometry, bbox, name, area_name, description = parse_kml("area.kml")

# 地理编码
location = create_location_context(bbox, area_name, description)

print(f"位置：{location.location_str}")
print(f"坐标范围：{location.coords_str}")
print(f"中心点：{location.centroid_lat:.4f}, {location.centroid_lon:.4f}")
```

## 依赖

- `urllib`：HTTP 请求（无需额外安装）
- `json`：JSON 解析

## 性能

- **API 响应时间**：5-10 秒
- **速率限制**：1 请求/秒
- **重试总时间**：最多 ~30 秒（3 次重试）

## 注意事项

1. **需要网络连接**：依赖 Nominatim 服务
2. **Free Tier 限制**：1 请求/秒，不支持批量
3. **语言设置**：使用 `accept-language=zh-CN` 获取中文
4. **坐标系统**：仅支持 WGS84（EPSG:4326）
5. **准确性**：农村或边界区域可能不够精确
