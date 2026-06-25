# geo-model3d —— 三维地质建模与立体成矿预测（P1 / MVP）

把平台从"面"（2D）升到"体"（3D）：将各上游服务的 2D 面状证据（蚀变/构造）统一装进一个带
CRS 的体元(voxel)网格，按矿床成因族的**知识深度带**做立体成矿有利度预测并输出**不确定性体**。
独立 Flask 服务，端口 **8085**，通过 `commons/*_broker` 零耦合订阅上游产物。

## P1 范围与诚实边界

- **深度维是知识驱动的"软推断"**，非物探/钻孔反演（平台当前无真实地下约束）：
  深度由矿床成因族的典型成矿深度带（如斑岩 1–4km）决定，**无地下约束区不确定性显著偏高**。
- **算法 = 知识驱动加权证据融合（零标签）**：避免"用预测靶点当标签"的循环论证。
  地表证据决定 xy 模式，知识深度带决定 z 分布：`score3d = F_xy × DepthGate(+ 断裂向深尾部)`。
- 标签驱动方法（WofE/信息量/RF/PU）接口保留、**默认关闭**，待真实已知矿点到位（方向四）再启用。
- 真实深度约束待方向二（物探反演体）/方向五（钻孔）灌入 —— geo-model3d 是它们的同化容器。

## 用法

```bash
cd geo-model3d
pip install -r requirements.txt
python3 run.py            # http://0.0.0.0:8085
```

Web：上传研究区 KML/KMZ + 选矿种 → 自动汇聚上游 → 三维有利度体 / 深度切片 / 靶点 / 不确定性。

## 输入（经 broker，缺失只降级不报错）

| 上游 | 取用 | 角色 |
|---|---|---|
| geo-analyser | `composites.score_tif` / `results[].index_tif` + `deposit_type` | 蚀变综合证据 + 定族 |
| geo-stru | `distance_to_lineament`/`lineament_density`（回退 `curvature`） | 断裂邻近证据 |
| geo-exploration | `*_Result.mat` 的 `depth_map` / 预测靶点（均可选） | 深度提示 / 一致性校验 |

## 输出（`results/<AOI>/model3d/<run_id>/`）

- `volume/prospectivity_volume.nc`：有利度 + 不确定性体（NetCDF，dims z,y,x）
- `depth_slices/depth_slice_-XXXXm.tif`：各深度层 GeoTIFF（带 UTM 地理参考）
- `targets_3d.json`：三维靶点 `{rank,lon,lat,depth_m,score,uncertainty}`
- `figures/*.png`：深度切片 + 深度剖面预览
- `metadata.json`：平台 broker 契约（`source=geo-model3d`，含 `model_stats.data_sources` 如实记录用到/缺失来源）

下游 `commons/model3d_broker.py`：`find_model3d_for_bbox` / `scan_model3d_outputs` / `get_product_path`。
geo-reporter 已加消费分支 `_geo_model3d_figures`（报告"三维成矿预测"章节为 P2）。

## 模块

```
core/grid.py        VoxelGrid: bbox→UTM, 体元网格, GeoTIFF 重投影
core/ingest.py      gather_evidence: 经 broker 取齐 2D 证据 + 矿种一致性判断 + 降级
core/knowledge.py   MINERAL_WEIGHTS(18 成因族权重+成矿深度带) + resolve_family
core/evidence.py    2D 证据层 + 深度门控/断裂尾部剖面
core/scorers.py     knowledge_weighted_fusion(P1主力) + woe/info(默认关闭)
core/uncertainty.py 覆盖度/深度/离散度 → 不确定性体（诚实性：深部偏高）
core/validate.py    相对验证（一致性 + 权重敏感性；不报命中率/C-A）
core/model3d_engine.py  编排全流程
outputs/writers.py  NetCDF/GeoTIFF切片/targets_3d/metadata
outputs/render.py   切片/剖面 PNG
```

## 验证

```bash
python3 tests/verify_p1.py     # 端到端：辽宁本溪市铜钼矿（蚀变+构造齐、深度缺 → 验证降级）
```

## ⚠ 待地质专家复核

`core/knowledge.py` 的各成因族权重、`depth_km` 深度带、矿种默认族、低/不适用清单均为**初版**，
应交付前由地质专家校准（见 `plan: validated-gliding-sloth.md`）。
