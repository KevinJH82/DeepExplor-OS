# 下载端拼接/裁剪优化:选片整治 + 逐波段相对辐射归一化(RRN)

## Context(为什么做)

下游蚀变分析平台(`/opt/deepexplor-services/geo-analyser`)对镶嵌产品的**辐射一致性高度敏感**:
拼接缝两侧的辐射阶跃会污染全局阈值(`mean+k·std`)、抢占 PCA 主成分,在缝处和半幅范围制造**假异常**,
同时压制偏暗子景里的真实弱蚀变(漏检)。参考文档 `merry-booping-gem.md` §4 明确指出:这些"治本"措施
应由**镶嵌数据处理上游**承担——也就是本下载系统的拼接环节。

**本系统拼接/裁剪现状(已核查):**
- 拼接 `postprocess/mosaic.py:234` `rasterio_merge(datasets, res=target_res)`:**硬拼**(first-wins 覆盖)、
  逐波段、**未显式传 nodata**、默认最近邻重采样。**完全没有**直方图匹配 / RRN / 接缝羽化 / 增益偏移校正。
- 选景 `mosaic.py:70-169` `select_covering_scenes`:贪心按覆盖面积选最少景;**云量仅作平局裁决**,
  **日期/时序根本不参与选片**——同一份镶嵌可能拼进相隔数月的景,凭空制造辐射边界。
- 裁剪 `postprocess/clip.py`:正确(reproject + `rasterio.mask` + 合理 nodata),**这部分不动**。
- 已满足的部分(文档 §4.2 step1):各传感器均交付 provider 级 **L2A/Collection-2 BOA 反射率**(同算法同参数),
  逐波段保留,打包按夏/冬季分目录。**绝对大气校正这一档已具备**,缺的正是 §4.2 step2(RRN)与 §4.3(nodata/对齐)。

**用户已确认力度:选片整治 + 逐波段 RRN(中档)。** 不做接缝羽化(会混合像素值、破坏定量反射率)。
RRN 默认开关位 **off**,先用现有数据验证,确认有效再切默认。

---

## Part A — 选片整治(选片阶段,保物理量、零像素改动、低风险)

> 文档 §4.3「尽量同期/相近期镶嵌:季节性差异无法靠辐射归一消除,只能选片避开」——这是最便宜、收益最大的一招。

### A1. 给候选景补 `_acq_date`(各 sensor search,与现有 `_cloud_cover` 并列)
现状只挂了 `_footprint` / `_cloud_cover`,**没有统一的采集日期**。在已设 `_cloud_cover` 的同一处补 `_acq_date`(`YYYY-MM-DD` 字符串):
- `downloader/sentinel2.py:147` ← `it["ContentDate"]["Start"][:10]`
- `downloader/landsat.py:223` ← `it["properties"]["datetime"][:10]`
- `downloader/prisma.py:268`、`downloader/aster.py`(CMR `time_start`)、`downloader/emit.py`(UMM 时间)各自补齐。
- 取不到日期则不挂(下游降级为旧行为)。

### A2. `select_covering_scenes` 加入时序聚类 + 云量主排序(`mosaic.py:70-169`)
保持"贪心选最少景完整覆盖"的主框架,在其上叠加两条约束:
1. **云量从"平局裁决"升为"主排序键之一"**:贪心每轮的打分从纯 `gain`(新增覆盖面积)改为
   `gain` 优先、`gain` 接近时(如相差 <5%)选**低云**——避免高云大景把低云景挤掉。保留 `_get_cloud` 取值逻辑(:143-151)。
2. **时序聚类优先同期**:用 `_acq_date` 把候选按日期窗口(默认 ±30 天)聚成若干"时相簇";
   优先在**单个时相簇内**完成覆盖(该簇能覆盖 ≥99% 就只用它,并在簇内按云量贪心);
   没有任何单簇能覆盖时,才跨簇补洞,并 `print` 警示"本次镶嵌跨 N 个时相(最大间隔 X 天),可能存在辐射边界"。
   - 阈值参数化:函数签名加 `same_period_days: int = 30`(`0` 关闭聚类=旧行为)。
   - 取不到 `_acq_date` 的候选 → 退回旧的纯覆盖贪心(完全兼容)。
- ASTER 的 `[(prod_key, granules), ...]` 结构:聚类在每个 `prod_key` 组内独立进行(沿用 `base.py:560-569` 的分组调用,无需改 base)。

### A3. merge 的 nodata / 像元对齐整治(`mosaic.py:228-256`,文档 §4.3)
- **显式传 nodata 给 merge**:从第一景 `ref.nodata` 取值;为空时按 dtype 推断(浮点→`nan`,整型→`0`),
  以 `nodata=` 传入 `rasterio_merge(..., nodata=fill)`,并写进输出 `meta["nodata"]`。
  防止边缘 0 值被当作有效数据、在缝/边界制造新伪异常。
- **重采样方法显式化**:`rasterio_merge(..., resampling=Resampling.bilinear)`(光学波段;沿用 clip.py 已用的 bilinear),
  避免默认最近邻在对齐时放大缝效应。热红外/分类类波段仍走最近邻(按波段键判断,复用 clip.py 中已有的"热红外用 nearest"判据)。
- 这两项对**单景/多景都安全**,不改像素辐射值,纯粹消除边缘/重采样伪影。

---

## Part B — 逐波段相对辐射归一化(RRN,治本残差;文档 §4.2 step2)

> 目标:拼接前让各景在重叠区**同名地物反射率一致**,缝阶跃≈0。**逐波段线性、保物理量**,不做视觉匀色、不做羽化。
> 默认 **off**,用开关位/参数控制;开后只在"多景拼接"路径生效,单景不受影响。

### B1. 新增模块 `postprocess/radiometric.py`
```python
def normalize_to_reference(file_paths, *, ref_idx=None, min_overlap_px=500,
                           method="linear") -> list[Path]:
    """对一组同波段多景做相对辐射归一化(写副本后返回新路径)。
    - 选参考景:ref_idx 显式指定,否则选「云量最低 / 覆盖最大」的一景(读 _cloud_cover/footprint)。
    - 对每个非参考景 i:在 与参考景的几何重叠区 采样双方有效像元(剔除 nodata、剔除云/水等异常:
      用稳健分位 2%-98% 截断 + 可选 MAD 离群剔除),逐波段拟合 y = a·x + b
      (稳健回归:Theil-Sen 或带 IRLS 的最小二乘),把景 i 像素线性映射到参考辐射尺度。
    - 重叠像元 < min_overlap_px 的景:跳过归一化(样本不足,强行拟合更危险),print 警示。
    返回处理后的文件路径列表;绝不抛到调用方,失败则原样返回输入。"""
```
- **保物理量**:仅 `a·x+b`(per-band gain/offset),保持波段间相对关系(比值/吸收深度依赖此)。
  **禁用**直方图拉伸 / Wallis / 任何视觉匀色(文档 §4.3 红线)。
- 复用 `clip._reproject_geometry` + `rasterio.mask` 求重叠区采样;复用 `_valid_pixel_ratio`(package.py:335-355)思路判有效像元。
- 写出时保留原 dtype/nodata,压缩沿用 deflate+predictor(与 mosaic.py 一致)。

### B2. 接入 `mosaic_and_clip`(`mosaic.py:172-268`)与 S2 路径(`mosaic_sentinel2_zips:336-365`)
- 在 `rasterio_merge` 之前、`len(file_paths) > 1` 时,若开关开启:
  `file_paths = normalize_to_reference(file_paths, ...)`(同一波段组才归一,跨波段不混)。
- S2 路径已按 `band_key` 分组(:336),天然逐波段——在每个波段的多景分支(:349-356)调用即可。
- 开关:`mosaic_and_clip(..., rrn: bool = False)`;由 `base.py` 调用处(:652、:677 附近)按全局配置/CLI 透传。

### B3. 配置 + CLI 开关
- `main.py` argparse 加 `--radiometric-normalize`(store_true,默认关)与 `--same-period-days`(默认 30)。
- `config/schema.yaml` 加 `mosaic: { radiometric_normalize: false, same_period_days: 30 }`,`web/app.py` 拼 argv 处按需透传(留默认即可)。
- 文档化:RRN 仅对**定量分析波段**;`basemap_RGB`(package.py 的 p2-p98 视觉拉伸)不在此列、保持现状。

---

## 需要改动的文件
- `downloader/sentinel2.py`、`landsat.py`、`aster.py`、`emit.py`、`prisma.py` — 在设 `_cloud_cover` 处补挂 `_acq_date`(A1)。
- `postprocess/mosaic.py` — `select_covering_scenes` 加时序聚类+云量主排序(A2);`mosaic_and_clip`/`mosaic_sentinel2_zips` 加 nodata/重采样整治(A3)+ 接 RRN 开关(B2)。
- `postprocess/radiometric.py` — **新增**:`normalize_to_reference()`(B1),复用 `clip._reproject_geometry`+`rasterio.mask`。
- `downloader/base.py` — 选景调用处无需改(沿用 :556-574);拼接调用处(:652、:677)透传 `rrn` 开关。
- `main.py` — argparse 加 `--radiometric-normalize` / `--same-period-days`(B3)。
- `config/schema.yaml` / `web/app.py` — 配置项与 argv 透传(B3)。

## 验证(尽量用已有数据,不重新下载)
1. **选片时序(dry-run)**:构造或取一组带 `_acq_date`/`_footprint`/`_cloud_cover` 的候选,调用
   `select_covering_scenes(..., same_period_days=30)` → 确认优先返回**同期低云**子集;`same_period_days=0` → 复现旧结果。
2. **nodata/对齐**:对已有多景同波段(如某 ASTER/S2 波段)跑 `mosaic_and_clip` 前后对比 → 输出 `meta["nodata"]` 正确、
   边缘 0 值不再计入有效区(用 `_valid_pixel_ratio` 或目视缝/边界)。
3. **RRN 单元**:取一对**有重叠**的同波段两景,跑 `normalize_to_reference` → 重叠区同名像元逐波段残差应≈0;
   沿缝画剖面,**波段比值/吸收深度曲线在缝处连续**(文档 §4.4 验证法);对归一后镶嵌做一次 PCA,确认无"沿缝主成分"。
4. **保物理量回归**:确认归一只改 a·x+b,未破坏波段间比例——同一像元归一前后各波段比值变化在数值误差内。
5. **开关回归**:`--radiometric-normalize` 关闭时,镶嵌结果与改动前逐统计一致(确保默认行为不变)。
6. **端到端(可选)**:下一单跨景任务开启 RRN,确认交付波段在缝处无台阶、下游蚀变分析不再出现沿缝带状假异常。

## 附带说明(不在本方案范围)
- 文档 §5「系统内稳健阈值(median+k·MAD)」属于**下游 geo-analyser** 的改进,不是本下载系统的事,本方案不涉及。
- 接缝羽化/融合(文档 §4.2 step3)对定量反射率有害,**本方案不做**;若将来只为 `basemap_RGB` 出图好看,可单独按视觉产品处理。
