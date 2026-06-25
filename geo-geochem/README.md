# geo-geochem —— 化探支柱（异常提取 + 多元素组合）P1

补齐平台"化探为线索"一环：对化探多元素数据做**背景/异常分离**与**多元素组合异常**，
产物经 `commons` broker 进 geo-model3d，作压制遥感假阳性的正交地表证据。
独立 Flask 服务，端口 **8088**，零耦合订阅上游、回写 `geochem_broker`。

## P1 范围与诚实边界

- **没有等价于物探的免费全球化探格网**（NGAC 离线、GEOROC 零散）。故输入分三档、**缺失只降级不报错**：
  ① 用户上传 ICP-MS/XRF 点位 CSV / GeoJSON（真实数据主路径）；
  ② 公开化探注册表（`data/public_geochem/index.json`，预置 NGSA 等开放集，数据源无关）；
  ③ data-colle 的 `geochem_thresholds` 背景阈值先验。
- **无实测点位时退化为"背景先验"态**：只给阈值参考、**不臆造异常网格**，并在 metadata 标 `status=prior_only/empty`。
- **原生晕轴向分带 / 剥蚀程度 / 构造叠加晕**（化探独有的"深度方向"信息）属 **P2**，P1 先登记元素序列（`config.HALO_ZONATION`），不实现。

## 用法

```bash
cd geo-geochem && pip install -r requirements.txt
python3 run.py            # http://0.0.0.0:8088
```

Web：上传研究区 KML/KMZ + 选矿种（专业选项：上传化探点位 CSV，列含 `lon,lat` 与元素如 `Cu,Pb,Zn…`）
→ 点位插值/阈值先验 → C-A 异常分离 + 多元素组合 → 异常成果。

## 处理内容（P1）

- **点位插值**：IDW（cKDTree），远离采样点处置 NaN，不外推臆造。
- **背景/异常分离**：分形 **C-A（含量-面积）法**自动求异常下限（对数 C-A 曲线分段拐点），叠加阈值先验；点太少回退百分位。
- **多元素组合异常**：按矿种关键指示元素（`mineral_kb`）做主成分（numpy SVD，PC1）——亮区=多元素同步增强，最可能指示矿(化)体。
- 全部 numpy/scipy 自实现，无需 sklearn。

## 输出（`results/<AOI>/geochem/<run_id>/`）

- `grids/element_anomaly_<el>.tif`：各元素异常强度（GeoTIFF，UTM 地理参考）
- `grids/multi_element_factor.tif`：多元素组合异常
- `anomalies.geojson`：浓集中心 `{lon,lat,peak/mean_intensity,area_km2,contrast,rank}`
- `figures/*.png`：异常图 + C-A 曲线
- `metadata.json`：平台契约（`source=geo-geochem`，`model_stats` 含 `status/anomaly_stats/data_sources` 如实记录用到/缺失/降级）

下游 `commons/geochem_broker.py`：`find_geochem_for_bbox / get_product_path / load_anomaly_points`。

## 与 geo-model3d 打通（方向三回报点）

geo-model3d 的 `core/ingest._load_geochem` 已消费本服务产物：
- **多元素组合异常** → 作 2D 地表证据，按成因族 `knowledge.GEOCHEM_WEIGHT` 并入融合；
- 与 geo-analyser 蚀变做正交互证，压制遥感单证据假阳性。

## 分阶段

- **P1**（本期）：点位插值/阈值先验 + C-A 异常分离 + 多元素组合 + geochem_broker + geo-model3d 消费。
- **P2**：原生晕轴向分带（前缘晕 As-Sb-Hg / 尾缘晕 Bi-Mo-Mn）→ 剥蚀程度 + 深度延伸带；构造叠加晕预测盲矿；进 geo-model3d 深度门控（替代纯衰减核臆测）。
- **P3**：蚀变↔指示元素联合异常（接 geo-analyser）+ 喂 geo-exploration 深度互证。
- **P4**：作为方向四 RF/PU 的特征层 + 接方向五钻孔实测回灌校验分带。
