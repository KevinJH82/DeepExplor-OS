# 远距离多区块 KML:防漏覆盖方案(预防 + 检测)

## Context(为什么做)

案例:任务「辽矿两测试区块」KML 含 2 个区块——"铜钼"(125.4°E)与"铁"(122.4°E),**相距约 280km**。
合并成单一 MultiPolygon 当作一个 area 下载后,实测覆盖:

| 传感器 | 铜钼(区块1) | 铁(区块2) |
|---|---|---|
| Sentinel-2 | ✅94% | ❌0%(下载中断 `.part`) |
| Landsat 8 | ✅94% | ❌范围外 |
| ASTER | ✅95% | ✅97% |
| EnMAP | ❌范围外 | ✅100% |

根因三条:① 网络断导致西侧瓦片留 `.part` 未下完(`download_errors.log` 有 Copernicus 认证 ConnectionError);
② 选景 `select_covering_scenes`(`postprocess/mosaic.py:70-169`)是**全局贪心**,远端区块易被中心景饿死;
③ **完工后没有任何逐区块覆盖核查**(报告 6 章无覆盖率),漏覆盖无人发现。

**用户已确认的方向:**
- **预防 + 检测都要。**
- **预防:把相距远的区块拆成各自独立的 area / 独立交付**(交付从一份变成 `区块1/`、`区块2/` 多份)。
- **检测:docx 报告加"逐区块覆盖核查"章节 + 任务完成时若有缺口推一条告警,两者都要。**

> 现有可复用资产:`_package_basemap` 已按多边形逐块循环出图(`postprocess/package.py:1162-1189`);
> `postprocess/clip.py` 的 `_reproject_geometry`(:41-65)+ `rasterio.mask` 按多边形裁剪;
> `_valid_pixel_ratio`(`package.py:335-355`)。检测模块直接组装这些,不重复造轮子。

---

## Part A — 预防:远区块拆成独立 area / 独立交付

### A1. 新增 `downloader/kml_parser.py::parse_kml_split_distant(kml_path, point_buffer, gap_km=50)`
返回 `List[(geometry, bbox, name)]`,行为:
1. 先调用现有 `parse_kml()` 得到合并后的 `(merged, bbox, name)`。
2. 若 `merged` 不是 MultiPolygon 或只有 1 个 part,或 `gap_km<=0` → 原样返回 `[(merged,bbox,name)]`(**完全兼容旧行为**)。
3. 否则按**单链聚类**(任意两 part 的最近邻距离 < `gap_km` 视为同簇;距离用 part 质心 haversine 公里数,边界距离亦可)对各 Polygon 聚类。
   - 单簇 → 不拆,返回合并结果(名字不变)。
   - 多簇 → 每簇产出一个 `(cluster_geom, cluster_bbox, f"{name}_区块{i}")`;簇内多 part 仍合成 MultiPolygon。
   - 名字优先用 Placemark `<name>`(若能稳定取到,案例里是"铜钼"/"铁");否则用 `区块{i}`。文件名需做文件系统安全处理。

> 聚类阈值默认 50km:案例 280km 会拆成 2 个 area;同一矿区相邻地块(<50km)仍合并为一份,避免过度碎片化。

### A2. `main.py` 接线(:708-720,批量分支与单文件分支都改)
把单次 `parse_kml(...)` + `areas.append(result)` 换成遍历 `parse_kml_split_distant(...)` 的结果:
```python
for result in parse_kml_split_distant(str(kf), args.point_buffer, args.split_distant_km):
    areas.append(result)
    area_kml_map[result[2]] = kf   # 同一 KML 的多个区块共享同一 kml_path
```
下游零改动:每个区块成为独立 area → 走现有 `_process_one`/`process_area`(:752-784)→ **各自独立的 bbox 搜索(更紧)、独立下载目录、独立交付目录 `delivery_root/{name}_区块N/`、独立报告**。
`max_items` 按区块各自计数,不再被另一区块挤占;选景全局贪心的偏置问题也随之消解(每个 area 已是紧凑单簇)。

### A3. CLI 参数
`main.py` argparse 新增 `--split-distant-km`(type=float, default=50.0;`0` 关闭拆分=旧行为)。`web/app.py` 拼 argv 处(`/api/run`)按需透传(留默认即可)。

**A 的代价(已与用户确认接受):** 一个 KML 产出多份交付目录;远区块各下一套,下载量增加。这正是为"每个区块都拍全"付的代价。
`base.py:500-513` 的分块搜索可保留不动(拆分后多数 area 已是单簇,它对簇内仍含 2 个近邻多边形的情况继续兜底)。

---

## Part B — 检测:逐区块覆盖核查 + 报告章节 + 完工告警

### B1. 新增模块 `postprocess/block_coverage.py`
```python
def compute_block_coverage(delivery_dir: Path, geometry, *, ok=0.30) -> dict:
    """逐区块×逐季节×逐传感器算多边形内有效画素率。返回结构化 dict 并写
    delivery_dir/.block_coverage.json(供 daemon 读取告警)。绝不抛到调用方。"""
```
- `parts = list(geometry.geoms) if MultiPolygon else [geometry]`(复用 `_package_basemap` 的拆法)。
- 季节目录:`data-矿权-夏季（6-8月）` / `data-矿权-冬季（11-3月）`(沿用 package.py 常量)。
- 每个 `区块i × 季节 × 传感器子目录`:取该传感器一个代表波段(目录里第一个 `.tif/.tiff`,**只读一景提速**),
  用 `_reproject_geometry`(clip.py:41-65)把多边形投到栅格 CRS,`rasterio.mask(crop=True)` 后统计
  `(data!=nodata)&(data!=0)` 占比。分级:`missing`(0 或范围外)/`partial`(0<r<ok)/`ok`(≥ok)。
- 汇总 `alerts`:形如 `"夏季 · 区块2(铁): Sentinel-2 缺失、Landsat 缺失"`。
- 返回 `{blocks:[{id,name,bounds,seasons:{...}}], alerts:[...]}` 并落盘 `.block_coverage.json`。

### B2. 报告章节(`postprocess/report.py`)
- `generate_report(...)` 新增可选参数 `geometry=None`(签名在 :183-193)。
- 在第六章之后、保存之前(约 :507 处),若 `geometry` 提供:调用 `compute_block_coverage`,新增
  **"七、逐区块覆盖核查"** ——表格(行=区块×季节,列=各传感器,单元格=`✅94%`/`⚠️12%`/`❌缺失`)+ 一段"缺口清单"。
  缺口用红色字体(报告里已有 `RGBColor` 用法)。
- `postprocess/package.py` 调用处(:2379)传入 `geometry=geometry`(该变量在 `package_delivery` 作用域内,
  正是传给 `_package_basemap` 的同一几何体)。

> 注:Part A 拆分后每份交付是单区块,本表只有一行——仍有价值(显示该区块缺哪个传感器)。
> 关闭拆分(`--split-distant-km 0`)时,多区块合并交付的表会完整列出各区块,正好覆盖"想留单份交付"的场景。

### B3. 完工告警(`web/app.py::_notify_status_change`,:353-373)
`status=="done"` 且 `task_type=="download"` 时:
- 解析本任务交付根:`base = Path(entry.delivery_dir)`;因 Part A 可能产出多份,`glob` 出
  `Path(kml_stem) + "*"` 下的所有 `.block_coverage.json`(单份/多份都覆盖),聚合 `alerts`。
- 有缺口 → 发 `_notify("warning", f"Task {tid} 完成(有覆盖缺口)", body=前几条alerts)`;无缺口 → 维持原 `success`。
- 全程 try/except 包裹(外接盘可能未挂载),失败则回退原 success,**绝不阻塞/抛出**。
- `_notify` 已自动镜像飞书(`_mirror_to_openclaw`),无需另接。

---

## 需要改动的文件
- `downloader/kml_parser.py` — 新增 `parse_kml_split_distant()`(聚类拆分),复用现有 `parse_kml()`。
- `main.py` — :708-720 改用拆分函数;argparse 加 `--split-distant-km`。
- `postprocess/block_coverage.py` — **新增**:`compute_block_coverage()`,复用 `clip._reproject_geometry` + `rasterio.mask`。
- `postprocess/report.py` — `generate_report` 加 `geometry` 参数 + 第七章渲染(:183, ~:507)。
- `postprocess/package.py` — :2379 传 `geometry=geometry`。
- `web/app.py` — `_notify_status_change`(:353-373)读 `.block_coverage.json` 出告警。

## 验证(用已有数据,无需重新下载)
1. **检测回放(关键)**:对现有「辽矿两测试区块」交付目录跑 `compute_block_coverage(delivery_dir, parse_kml(该KML))`
   → 期望复现人工结论:区块2(铁)缺 Sentinel-2/Landsat、区块1(铜钼)缺 EnMAP;并生成 `.block_coverage.json`。
2. **报告章节**:对同一交付重跑 `generate_report(..., geometry=...)` → docx 出现"七、逐区块覆盖核查"表与缺口清单。
3. **拆分逻辑(dry-run)**:`parse_kml_split_distant("辽矿…ovkml", 0.1, 50)` → 返回 2 个 area(区块1/区块2);
   对任一单区块 KML → 返回 1 个 area(行为不变)。`--split-distant-km 0` → 始终 1 个 area。
4. **告警链路**:把某 done 任务指向含 `.block_coverage.json`(带 alerts)的交付,触发 `_notify_status_change`
   → 应发 warning 而非 success;无 alerts 的交付 → 仍 success。
5. **端到端(可选,真实下一单多区块任务)**:确认产出多份交付目录、各自报告含覆盖表、有缺口时收到告警。

## 附带提醒(本次发现,非本方案范围)
交付里出现二重嵌套 `辽矿…_1779870023/辽矿…_1780279565/`,是 `--delivery-dir` 时间戳与 area_label(=KML stem)
不一致时 `package.py:2079` `delivery_root/area_label` 造成的。Part A 不改这点;若以后困扰,可单独统一二者命名。
