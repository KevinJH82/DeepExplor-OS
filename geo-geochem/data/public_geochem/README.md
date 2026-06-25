# 公开化探数据集目录（PUBLIC_GEOCHEM_ROOT）

geo-geochem 的「公开化探数据 broker」（`commons/geochem_public_broker.py`）在此目录发现
**已落地的公开地球化学点位数据集**。当任务的研究区(AOI/bbox)与某数据集相交时，自动取其点位，
走与「用户上传 CSV」**完全相同**的插值 / C-A 异常分离 / 多元素组合流程。

> broker **不联网取数**。它只发现你预置到本目录的文件。这与 geo-geophys 让 data-colle 预置
> EMAG2/ICGEM 全球网格、ingest 只做「发现」是同一套路。

## 优先级

`gather_geochem` 的数据来源优先级：
1. 用户上传点位 CSV（最高）
2. **本目录的公开数据集（AOI 命中）** ← 本 broker
3. data-colle 背景阈值先验（`prior_only`）
4. 无成果（`empty`）

## 目录布局

```
<PUBLIC_GEOCHEM_ROOT>/
├── index.json          # 数据集注册表
├── <dataset>.csv       # 点位文件（CSV/TXT）
└── <dataset>.geojson   # 或 GeoJSON 点要素
```

默认 `PUBLIC_GEOCHEM_ROOT = geo-geochem/data/public_geochem`，可用环境变量覆盖。
`PUBLIC_GEOCHEM_ENABLED=0` 可整体关闭 broker。

## 注册表 index.json 格式

```jsonc
{
  "datasets": [
    {
      "name": "唯一名",
      "source": "数据来源说明",
      "license": "许可",
      "bbox": [west, south, east, north],   // WGS84 经纬度，必填，用于 AOI 相交命中
      "crs": "EPSG:4326",
      "path": "相对本目录或绝对路径的数据文件",
      "scale_note": "尺度说明（会进专业指标表）",
      "columns": {                            // 可选：显式列映射；省略则自动识别
        "lon": "经度列名", "lat": "纬度列名",
        "elements": {"Cu": "Cu_ppm", "Pb": "Pb_ppm"}
      }
    }
  ]
}
```

- **CSV**：含经纬度列与元素含量列。`columns` 省略时按 `core/ingest.py:_detect_columns`
  自动识别（经纬度支持 lon/longitude/x/经度/lng、lat/latitude/y/纬度；元素列名以元素符号开头，
  容许 `_ppm`/`_pct` 等后缀）。
- **GeoJSON**：Point 要素，坐标作 lon/lat，properties 作元素列。

## 可入库的公开数据（海外，开放可下载）

> 这些是国家/区域尺度（水系沉积物/土壤，常 1 点/数 km² 起），适合做**区域异常底图**，
> 非矿体尺度详查。下载后裁剪到研究区、整理成上表列格式即可。

- **USGS** National Geochemical Database (NGDB)：Mineral Resources Online Spatial Data 门户。
- **Geoscience Australia** National Geochemical Survey of Australia (NGSA)：68 元素 / 1315 点。
- **USGS + GA + GSC** 全球关键矿产数据门户：>1 万样（美/澳/加）。
- **欧洲** FOREGS 地球化学图集 / GEMAS。

## 中国数据（重要）

中国区域化探全国扫面（**RGNR**，1 点/km²、39 元素）与 China Geochemical Baselines（**CGB**）
是世界级数据，但**点位级数据不公开**：由各省地调院持有、经受控的「Digital Geochemical Earth」
平台管理，**没有开放下载/API，broker 无法自动获取**。

→ 处理方式：你**合法获取** RGNR/省级/CGB 点位数据后，按上述注册表格式放入本目录即自动生效。
broker 不承诺、也不会去公网「变」出这些数据。在拿到数据前，中国 AOI 会降级到背景阈值先验。

## 当前内置

- `ngsa_demo_goldfields.csv`：**演示数据**（仿 NGSA 区域布局，非真实采样），覆盖
  bbox `[121.0, -29.0, 121.5, -28.5]`，仅用于联调验证自动接入链路。正式使用请替换/删除。
