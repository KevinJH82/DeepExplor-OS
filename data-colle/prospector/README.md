# 🏔️ Prospector — 找矿前期资料自动收集系统

> 输入 ROI（.kml / .ovkml / .xlsx）+ 矿种 → 自动输出全套地质、地球物理、地球化学资料包

## 快速开始

### 安装

```bash
pip install pandas openpyxl shapely lxml numpy requests jinja2 pyproj
# 可选（自动下载遥感影像列表）:
pip install pystac-client
# 可选（自动下载 + 裁剪磁法/重力数据）:
pip install rasterio xarray rioxarray
```

### 使用

```bash
# 基本用法（仅生成检索链接，不下载大数据）
python3 prospector.py --roi <ROI文件> --mineral <矿种>

# 自动下载可获取的数据（EMAG2 磁法 + WGM2012 重力）
python3 prospector.py --roi <ROI文件> --mineral <矿种> --download

# 交互模式
python3 prospector.py --interactive

# 自定义 buffer 和输出目录
python3 prospector.py --roi area.kml --mineral 金 --buffer 30 --output ./my_output

# 列出所有支持的矿种
python3 prospector.py --list-minerals
```

### 示例

```bash
python3 prospector.py --roi tests/test_roi.kml --mineral 铜
python3 prospector.py --roi tests/test_roi.xlsx --mineral 金
python3 prospector.py --roi target.kml --mineral 锂 --download
```

### 支持的 ROI 格式

- `.kml` / `.ovkml` — 含 Polygon 要素的 KML 文件
- `.xlsx` — 包含经纬度列的 Excel 文件（自动检测列名：经度/纬度/lon/lat等）

### 支持的矿种

铜、金、锂、铅锌、钨锡、稀土、铁

## 输出结构

```
output/{ROI文件名}_{矿种}_{时间戳}/
├── 00_项目摘要.md          # 完整结构化的 Markdown 报告
├── summary.json            # JSON 格式摘要
├── 02_地球物理资料/
│   ├── magnetic/           # EMAG2 磁异常（--download 时）
│   └── gravity/            # WGM2012 布格重力（--download 时）
└── 03_地球化学资料/
    └── georoc/             # GEOROC 火成岩地球化学查询结果
```

## 数据来源

| 数据 | 来源 | 门槛 |
|------|------|:--:|
| 全球磁异常 (EMAG2 v3) | NOAA NCEI | 零门槛 |
| 全球布格重力 (WGM2012) | BGI | 零门槛 |
| 重力场在线计算 | ICGEM / GFZ Potsdam | 零门槛 |
| 中国区域地质图/化探图 | NGAC 全国地质资料馆 | 检索链接 |
| 学术文献 | CNKI / Google Scholar | 检索链接 |
| Sentinel-2 影像 | Copernicus STAC API | 免费注册 |
| 火成岩地球化学 | GEOROC | 免费 |
| 全国化探元素背景值 | 史长义等 (2016) | 内置 |

## 项目结构

```
prospector/
├── prospector.py            # CLI 入口
├── requirements.txt         # Python 依赖
├── src/
│   ├── roi_parser.py        # ROI 解析引擎 (KML/XLSX)
│   ├── mineral_kb.py        # 矿种知识库
│   ├── geo_fetcher.py       # 地质资料获取器
│   ├── geophy_fetcher.py    # 地球物理资料获取器
│   ├── geochem_fetcher.py   # 地球化学资料获取器
│   ├── rs_fetcher.py        # 遥感资料获取器
│   └── report_generator.py  # 报告生成器
├── tests/                   # 测试数据
│   ├── test_roi.kml
│   └── test_roi.xlsx
└── output/                  # 输出目录
```
