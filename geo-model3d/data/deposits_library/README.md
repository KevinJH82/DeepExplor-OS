# 本地已知矿点库（data/deposits_library）

放置**真实已知矿点**（任意区域，含中国），供数据驱动成矿预测（方向四 PU/RF/WofE）做正样本。
MRDS 仅覆盖美国；中国等区域用此库补充，**无需每次手动上传**。

## 工作机制
- 建模时按 AOI 的 bbox **自动相交裁剪** + 按矿种粗过滤读取本目录所有 `.geojson` / `.csv`。
- 当落入 AOI 的矿点数 ≥ `LABEL_MIN`(默认 8) 时，自动切换到数据驱动 PU/RF（否则诚实回退知识融合）。
- 源优先级：用户上传 > 本地矿点库 > MRDS。
- 目录可用 env `MODEL3D_LOCAL_DEPOSITS` 覆盖。

## 格式
**CSV**（表头大小写不敏感，支持中英列名）：
```
lon,lat,commodity,name
120.4475,37.1854,金,某金矿点
```
- 必需列：`lon`/`经度`/`longitude`/`x`、`lat`/`纬度`/`latitude`/`y`
- 可选列：`commodity`/`矿种`、`name`/`名称`

**GeoJSON**：`FeatureCollection`，每个 `Feature` 为 `Point`，`properties` 可含 `commodity`/`name`/`deposit_type`。

## 重要约束
- **只放真实、已确证的矿点**——绝不要放预测靶点（用预测当标签=循环论证→假准）。
- 矿种用中文名（金/铜/铅锌…）即可，会按内置映射与 AOI 矿种匹配；留空则不否决。
- 一个区域一个文件便于维护，例如 `shandong_zhaoyuan_gold.csv`。
