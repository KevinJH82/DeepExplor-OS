# geo-geophys —— 物探支柱（位场处理 + ANT 速度体接入）P1

补齐平台"物探为手段"一环：用现有全球磁/重网格做**区域位场处理**，并把**磁源深度 + ANT 速度体**
接进 geo-model3d，把三维模型的深度从"纯知识软推断"升级为"知识 + 物探实测约束"。
独立 Flask 服务，端口 **8087**，经 `commons` broker 零耦合订阅上游、回写 `geophys_broker`。

## P1 范围与诚实边界

- **输入是全球区域网格**（EMAG2 磁 ~4km 且已上延4km、ICGEM 重力 ~14km）→ 所有产物为
  **区域尺度**：磁源深度是 km 级区域估计，**非矿体尺度**。矿体尺度需地面/航空实测（P3）。
- **ANT/被动源地震是硬件依赖**（野外节点 + 卫星 + 层析），桌面无法生成 → P1 做**接入适配器**：
  拿到外部 3D Vs 体（NetCDF/CSV）即重投影进体元网格，按"低 Vs→有利"出有利度体；无数据则标 absent。
- 处理**源栅格完整区域范围**（prospector 已带 ~40km 缓冲），而非裁到小 AOI——区域处理需区域数据。

## 用法

```bash
cd geo-geophys && pip install -r requirements.txt
python3 run.py            # http://0.0.0.0:8087
```
Web：上传研究区 KML/KMZ + 选矿种（+ IGRF 日期 / 欧拉构造指数 SI / 可选 ANT 速度体 .nc/.csv）
→ 取 prospector 磁/重网格 → 位场处理 + 磁源深度 +（可选）速度体接入。

## 处理内容（P1）

- **位场（磁为主）**：RTP 化极（IGRF 倾角/偏角，`ppigrf`）、垂向导数、总水平导数、**解析信号(AS)**、
  **倾斜角(Tilt)**、向上延拓、**欧拉反褶积**求磁源三维深度点（区域尺度，第一份真实测量深度证据）。
- **重力**：倾斜角等（极粗，仅区域趋势）。
- **ANT/地震 3D Vs 体接入**：重投影到统一体元网格 + 低 Vs→有利度体（NetCDF）。
- 全部用 numpy FFT/最小二乘自实现（标准公式），仅依赖轻量 `ppigrf`。

## 输出（`results/<AOI>/geophys/<run_id>/`）

- `grids/*.tif`：化极/解析信号/倾斜角/导数/向上延拓（GeoTIFF，UTM 地理参考）
- `euler_sources.geojson`：磁源深度点 `{lon,lat,depth_m,si}`
- `volume/velocity_volume.nc`：ANT Vs 体 + 有利度体（如接入）
- `figures/*.png` + `metadata.json`（`source=geo-geophys` 平台契约）

下游 `commons/geophys_broker.py`：`find_geophys_for_bbox / get_product_path / load_euler_sources`。

## 与 geo-model3d 打通（方向二回报点）

geo-model3d 已扩展 `core/geophys.py` 消费本服务产物：
- **磁源深度点** → 在对应平面位置用实测深度**局部锐化/替换知识深度门控**（实测处不确定性下降）；
- **ANT 速度有利度体** → 平台**第一份真三维证据**，直接进融合；
- **磁解析信号(AS)** → 附加 2D 构造证据；强磁矿族（IOCG/矽卡岩磁铁矿/岩浆硫化物/金伯利岩/碳酸岩）
  按 `knowledge.MAGNETIC_WEIGHT` 调高磁权重。
- 验证（阜新铁矿）：接入物探后整体不确定性较纯知识版**下降 ~15%**。

## 验证

```bash
python3 tests/verify_p1.py      # 阜新铁矿：位场/欧拉/速度接入/broker
```

## 分阶段

- **P1**（本期）：区域位场处理 + ANT 接入槽 + geophys_broker + geo-model3d 消费 + reporter 物探图件。
- **P2**：3D 磁化率/密度反演（SimPEG/等效源，区域平滑体）→ 直接作 geo-model3d 体元属性。
- **P3**：自有航空/地面实测（磁/重/IP/EM）接入 + 矿体尺度反演；自建 ANT 流程（obspy 噪声互相关→层析）。

## ⚠ 待物探专家确认

RTP 的 IGRF 日期；欧拉默认构造指数 SI（铁/磁铁矿建议 0~1）；是否启用 WGM2012；
ANT 速度体到货后的实际格式（NetCDF 维度名/CSV 列名）；低 Vs→有利度映射阈值。
