# Changelog

所有重要变更均记录于此，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [1.0.0] - 2026-04-11

### Added

**传感器支持（30 个）**
- 光学多光谱：Sentinel-2 L2A、Landsat 8/9 L2、Landsat 7 ETM+、Landsat TIRS、MODIS
- SAR 雷达：Sentinel-1 GRD、ALOS PALSAR、ALOS-2 PALSAR-2、OPERA RTC-S1、NISAR
- 高光谱：EMIT（285波段）、Hyperion EO-1（242波段）、AVIRIS-NG（432波段）、EnMAP L2A（244波段）、PRISMA L2D（239波段）、DESIS L2A（235波段）、ZY-1 02D AHSI（166波段）
- 高程 DEM：Copernicus DEM GLO-30、SRTM
- 热红外：ASTER L2、ECOSTRESS
- 商业影像：SPOT 6/7、Pleiades 1A/1B、WorldView-2/3、PlanetScope
- GEE 数据源：GEE Sentinel-2/Landsat/MODIS/自定义集合

**下载基础设施**
- 多线程分块并行下载（默认 8 线程）+ 断点续传
- 动态代理自动探测（快柠檬 SOCKS5 10793 / HTTP 10792，实时切换）
- 下载停滞检测（120s 无数据自动重连）
- `base.py` 中 `get_proxies()` 实时探测，支持任务运行中切换代理

**后处理管道**
- 裁剪到 KML 范围（`postprocess/clip.py`）
- 多景镶嵌拼接（`postprocess/mosaic.py`）
- 衍生产品计算：地表温度、温度梯度、温度异常梯度、OTCI（`postprocess/derive.py`）
- 标准交付目录打包（`postprocess/package.py`，按传感器+季节组织）
- 报告生成（`postprocess/report.py`，Word + Markdown）

**Web UI（Flask）**
- 拖拽上传 KML 文件
- 传感器分组 chip 选择（高程/多光谱/高光谱/高分辨率/热红外/SAR/激光测高/GEE）
- 交付模式 / 自定义模式切换
- 实时日志 SSE 流 + 整体进度条 + 文件级进度条
- 数据可用性预评估
- GEE 自定义集合下拉选择（自动带出波段列表）
- 架构设置页（传感器交付文件夹结构配置）

**认证支持**
- Google Earth Engine 服务账号认证（JSON Key）
- EnMAP DLR CAS SSO 自动登录（Playwright 无头浏览器）
- NASA Earthdata、Copernicus、USGS ERS、OneAtlas、Maxar 等多平台凭证统一管理
