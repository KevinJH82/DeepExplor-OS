# geo-downloader

面向地质勘探的多平台遥感数据批量下载工具，支持 30 个卫星传感器，提供命令行和 Web UI 两种使用方式。

---

## 支持的传感器（30 个）

| 类型 | 传感器 | 分辨率 | 账号要求 |
|------|--------|--------|---------|
| **光学多光谱** | Sentinel-2 L2A | 10m | Copernicus |
| | Landsat 8/9 L2 | 30m | 无需 ★ |
| | Landsat 7 ETM+ | 15-30m | 无需 ★ |
| | Landsat TIRS | 30m | 无需 ★ |
| | MODIS | 250-500m | NASA Earthdata |
| **SAR 雷达** | Sentinel-1 GRD | 10m C波段 | NASA Earthdata |
| | ALOS PALSAR | 25m L波段 | NASA Earthdata |
| | ALOS-2 PALSAR-2 | 3-100m L波段 | NASA Earthdata |
| | OPERA RTC-S1 | 30m | NASA Earthdata |
| | NISAR | 3-25m L波段 | NASA Earthdata |
| **高光谱** | EMIT | 60m 285波段 | NASA Earthdata |
| | Hyperion EO-1 | 30m 242波段 | NASA Earthdata |
| | AVIRIS-NG | ~5m 432波段 | NASA Earthdata |
| | EnMAP L2A | 30m 244波段 | DLR EOWEB |
| | PRISMA L2D | 30m 239波段 | ASI |
| | DESIS L2A | 30m 235波段 | NASA Earthdata |
| | ZY-1 02D AHSI | 30m 166波段 | CRESDA（手动） |
| **高程 DEM** | Copernicus DEM GLO-30 | 30m | 无需 ★ |
| | SRTM | 30m | 无需 ★ |
| **热红外** | ASTER L2 | 15-90m | NASA Earthdata |
| | ECOSTRESS | 70m | NASA Earthdata |
| **商业影像** | SPOT 6/7 | 1.5m | OneAtlas（付费） |
| | Pleiades 1A/1B | 0.5m | OneAtlas（付费） |
| | WorldView-2 | 0.46m | Maxar（付费） |
| | WorldView-3 | 0.31m | Maxar（付费） |
| | PlanetScope | 3-5m | Planet API Key |
| **GEE 数据源** | GEE Sentinel-2 L2A | 10m via GEE | GEE 服务账号 |
| | GEE Landsat 8/9 L2 | 30m via GEE | GEE 服务账号 |
| | GEE MODIS | 500m via GEE | GEE 服务账号 |
| | GEE 自定义集合 | 用户指定 | GEE 服务账号 |

★ 无需账号，可直接使用

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

可选依赖（按需安装）：

```bash
pip install playwright earthengine-api python-docx
playwright install chromium  # EnMAP 自动下载需要
```

### 2. 配置凭证

```bash
cp credentials.yaml.example config/credentials.yaml
# 编辑 config/credentials.yaml，填入各平台账号
```

### 3. 命令行运行

```bash
# 使用配置文件默认参数
python3 main.py

# 指定参数
python3 main.py --kml area.kml --sensor sentinel2 landsat \
    --start 2024-01-01 --end 2024-06-30 --cloud 20
```

### 4. Web UI

```bash
python3 web/app.py
# 访问 http://localhost:8080
```

---

## 凭证配置说明

| 平台 | 注册地址 | credentials.yaml 字段 |
|------|---------|----------------------|
| NASA Earthdata | https://urs.earthdata.nasa.gov/register | `nasa_earthdata.username/password` |
| Copernicus | https://dataspace.copernicus.eu | `copernicus.username/password` |
| USGS EarthExplorer | https://ers.cr.usgs.gov/register | `usgs.username/password` |
| DLR EOWEB | https://eoweb.dlr.de | `dlr_eoweb.username/password` |
| Planet | https://www.planet.com | `planet.api_key` |
| OneAtlas (SPOT/Pleiades) | https://oneatlas.airbus.com | `oneatlas.api_key` |
| GEE 服务账号 | https://console.cloud.google.com | `google_earth_engine.service_account_email/key_path` |

---

## 项目结构

```
geo-downloader/
├── main.py                 # 命令行主入口
├── requirements.txt        # 依赖清单
├── credentials.yaml.example # 凭证配置模板
├── config/
│   ├── credentials.yaml   # 凭证配置（本地，不纳入版本控制）
│   └── schema.yaml        # 交付架构定义
├── downloader/             # 各传感器下载器（30个）
├── postprocess/            # 后处理模块（裁剪/镶嵌/衍生产品/打包）
├── web/                    # Flask Web UI
│   ├── app.py
│   └── templates/index.html
├── models/                 # AI 模型（不纳入版本控制）
├── docs/                   # 使用文档
└── key/                    # GEE 密钥（不纳入版本控制）
```

---

## 模型文件

超分辨率增强功能依赖 `RealESRGAN_x4plus.pth`（64MB），需手动下载后放入 `models/` 目录：

```bash
# 下载地址
https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
```

---

## 代理设置

在中国大陆使用时，工具会自动探测本地代理（快柠檬 SOCKS5 端口 10793 / HTTP 端口 10792），无需额外配置。
