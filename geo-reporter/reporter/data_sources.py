"""
Direct Data Sources — 直连全球权威 API
为特定类别提供零成本、高可信度的结构化原始数据，降级到 Tavily 搜索。

支持的类别和数据源：
  climate       → Open-Meteo（历史气候，无需 API Key）
  geography     → OpenTopoData SRTM30（高程，无需 API Key）
  geology       → Macrostrat（地层数据）+ USGS Earthquake（地震）
  hydrology     → HydroSHEDS / OpenStreetMap Overpass（河流）
"""

import json
import urllib.request
import urllib.parse
from contextvars import ContextVar
from typing import List, Optional, Tuple

# 当前报告的租户上下文(P2 隔离):build_report 入口 set,broker 发现按此过滤他租户产物。
# 报告在 worker 线程内同步构建,ContextVar 线程内可见;并发报告各线程独立,天然无串扰。
_TENANT: ContextVar = ContextVar("reporter_tenant", default=None)


def set_tenant(tenant_id):
    _TENANT.set(tenant_id or None)


def current_tenant():
    return _TENANT.get()


def _get(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "geo-reporter/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Climate — Open-Meteo 历史气候 API（免费，无需 Key）
# ---------------------------------------------------------------------------

def fetch_climate(lat: float, lon: float) -> List[str]:
    """
    从 Open-Meteo 获取研究区年均气温、降雨量、蒸发量等气候数据。
    返回可直接传入提取 prompt 的文本列表。
    """
    try:
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "start_date": "2014-01-01",
            "end_date": "2023-12-31",
            "daily": ",".join([
                "temperature_2m_max", "temperature_2m_min",
                "precipitation_sum", "et0_fao_evapotranspiration",
                "windspeed_10m_max"
            ]),
            "timezone": "auto",
            "models": "ERA5"
        })
        url = f"https://archive.open-meteo.com/v1/archive?{params}"
        data = _get(url)

        daily = data.get("daily", {})
        temps_max = [t for t in daily.get("temperature_2m_max", []) if t is not None]
        temps_min = [t for t in daily.get("temperature_2m_min", []) if t is not None]
        precip    = [p for p in daily.get("precipitation_sum", []) if p is not None]
        et0       = [e for e in daily.get("et0_fao_evapotranspiration", []) if e is not None]
        wind      = [w for w in daily.get("windspeed_10m_max", []) if w is not None]

        if not temps_max:
            return []

        ann_temp_max = round(sum(temps_max) / len(temps_max), 1)
        ann_temp_min = round(sum(temps_min) / len(temps_min), 1)
        ann_precip   = round(sum(precip) / 10, 1)   # 10年总量 → 年均
        ann_et0      = round(sum(et0) / 10, 1)
        ann_wind     = round(sum(wind) / len(wind), 1)
        temp_max_val = round(max(temps_max), 1)
        temp_min_val = round(min(temps_min), 1)

        text = (
            f"[Open-Meteo ERA5 历史气候数据，2014-2023年，坐标({lat},{lon})]\n"
            f"年均最高气温：{ann_temp_max} °C\n"
            f"年均最低气温：{ann_temp_min} °C\n"
            f"历史极端最高气温：{temp_max_val} °C\n"
            f"历史极端最低气温：{temp_min_val} °C\n"
            f"年均降雨量：{ann_precip} mm\n"
            f"年均蒸散量（FAO ET0）：{ann_et0} mm\n"
            f"年均最大风速：{ann_wind} km/h\n"
            f"数据来源：Open-Meteo.com / ERA5 再分析数据集（ECMWF）"
        )
        return [text]
    except Exception as e:
        print(f"[DataSource] Open-Meteo 失败：{e}")
        return []


# ---------------------------------------------------------------------------
# Geography — OpenTopoData SRTM30 高程
# ---------------------------------------------------------------------------

def fetch_geography(lat: float, lon: float, min_lon: float, min_lat: float,
                    max_lon: float, max_lat: float) -> List[str]:
    """
    从 OpenTopoData 获取研究区多点高程，计算海拔范围。
    """
    try:
        # 9点采样（3×3网格）
        lats = [min_lat, (min_lat+max_lat)/2, max_lat]
        lons = [min_lon, (min_lon+max_lon)/2, max_lon]
        locations = "|".join(f"{la},{lo}" for la in lats for lo in lons)
        url = f"https://api.opentopodata.org/v1/srtm30m?locations={locations}"
        data = _get(url)

        elevations = [r["elevation"] for r in data.get("results", []) if r.get("elevation") is not None]
        if not elevations:
            return []

        elev_min = round(min(elevations))
        elev_max = round(max(elevations))
        elev_mean = round(sum(elevations) / len(elevations))
        area_km2 = round(
            abs(max_lon - min_lon) * 111 * abs(max_lat - min_lat) * 111 * 0.9, 1
        )

        text = (
            f"[OpenTopoData SRTM30m 高程数据，坐标范围({min_lat}-{max_lat}N, {min_lon}-{max_lon}E)]\n"
            f"海拔最低点：{elev_min} m\n"
            f"海拔最高点：{elev_max} m\n"
            f"区域平均海拔：{elev_mean} m\n"
            f"高差：{elev_max - elev_min} m\n"
            f"估算区块面积：{area_km2} km²\n"
            f"数据来源：OpenTopoData.org / NASA SRTM 30m DEM"
        )
        return [text]
    except Exception as e:
        print(f"[DataSource] OpenTopoData 失败：{e}")
        return []


# ---------------------------------------------------------------------------
# Geology — Macrostrat 地层 + USGS 地震
# ---------------------------------------------------------------------------

def fetch_geology(lat: float, lon: float) -> List[str]:
    """
    从 Macrostrat 获取地层单元，从 USGS 获取地震记录。
    """
    texts = []

    # Macrostrat 地层
    try:
        url = f"https://macrostrat.org/api/v2/units?lat={lat}&lng={lon}&response=long&format=json"
        data = _get(url)
        units = data.get("success", {}).get("data", [])[:5]
        if units:
            lines = [f"[Macrostrat 地层数据，坐标({lat},{lon})]"]
            for u in units:
                age = f"{u.get('b_age', '?')}–{u.get('t_age', '?')} Ma"
                lines.append(
                    f"地层单元：{u.get('unit_name','未知')}，时代：{age}，"
                    f"岩性：{u.get('lith','未知')}，厚度：{u.get('thick','?')} m"
                )
            lines.append("数据来源：Macrostrat.org 全球地层数据库")
            texts.append("\n".join(lines))
    except Exception as e:
        print(f"[DataSource] Macrostrat 失败：{e}")

    # USGS 地震（近50年 M≥3.0，半径200km）
    try:
        params = urllib.parse.urlencode({
            "format": "geojson",
            "latitude": lat, "longitude": lon,
            "maxradiuskm": 200,
            "minmagnitude": 3.0,
            "starttime": "1975-01-01",
            "endtime":   "2025-01-01",
            "orderby": "magnitude",
            "limit": 10
        })
        url = f"https://earthquake.usgs.gov/fdsnws/event/1/query?{params}"
        data = _get(url)
        features = data.get("features", [])
        if features:
            max_mag = features[0]["properties"]["mag"]
            count = len(features)
            lines = [
                f"[USGS 地震数据，坐标({lat},{lon})，半径200km，1975–2025年]",
                f"M≥3.0 地震记录：{count} 次（查询上限10条）",
                f"最大震级：M{max_mag}",
            ]
            for f in features[:3]:
                p = f["properties"]
                lines.append(f"  - M{p['mag']}，{p.get('place','未知')}，时间：{p.get('time','')}")
            lines.append("数据来源：USGS Earthquake Hazards Program")
            texts.append("\n".join(lines))
    except Exception as e:
        print(f"[DataSource] USGS 地震 失败：{e}")

    return texts


# ---------------------------------------------------------------------------
# Hydrology — OSM Overpass 河流查询
# ---------------------------------------------------------------------------

def fetch_hydrology(min_lon: float, min_lat: float,
                    max_lon: float, max_lat: float) -> List[str]:
    """
    通过 OSM Overpass API 查询研究区内的河流/水体要素。
    """
    try:
        bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
        query = f"""
[out:json][timeout:25];
(
  way["waterway"~"river|stream|canal"]({bbox});
  relation["waterway"="river"]({bbox});
  way["natural"="water"]({bbox});
);
out body;
"""
        encoded = urllib.parse.urlencode({"data": query})
        url = f"https://overpass-api.de/api/interpreter?{encoded}"
        data = _get(url, timeout=30)

        elements = data.get("elements", [])
        rivers = [e for e in elements if e.get("tags", {}).get("waterway") in ("river", "stream", "canal")]
        waters = [e for e in elements if e.get("tags", {}).get("natural") == "water"]

        if not elements:
            return []

        names = list({e["tags"].get("name", e["tags"].get("name:zh", "")) for e in rivers if e.get("tags")})
        names = [n for n in names if n]

        text = (
            f"[OpenStreetMap Overpass 水系数据，范围({min_lat}-{max_lat}N, {min_lon}-{max_lon}E)]\n"
            f"区内河流/水道要素数量：{len(rivers)} 条\n"
            f"水体面要素数量：{len(waters)} 个\n"
            f"已命名河流：{', '.join(names[:10]) if names else '未检测到命名河流'}\n"
            f"数据来源：OpenStreetMap / Overpass API"
        )
        return [text]
    except Exception as e:
        print(f"[DataSource] OSM Overpass 失败：{e}")
        return []


# ---------------------------------------------------------------------------
# 子系统 broker 接入（commons/*_broker）——把兄弟子系统标准输出注入对应章节
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    """仓库根目录（geo-reporter 的上一级），基于本文件位置推导，兼容不同部署前缀。"""
    from pathlib import Path
    return str(Path(__file__).resolve().parents[2])


def _import_commons():
    """把仓库根加入 sys.path 以导入 commons.* broker（兼容 /opt/Project 与 /opt 两种部署路径）。"""
    import sys
    for _repo in (_repo_root(), "/opt/deepexplor-services"):
        if _repo not in sys.path:
            sys.path.insert(0, _repo)


def _bbox(min_lon, min_lat, max_lon, max_lat):
    return (min_lon, min_lat, max_lon, max_lat)


# 各兄弟子系统标准输出目录（基于真实仓库根推导）。
# broker 内部默认写死 /opt/deepexplor-services/...，在本机不存在；所有 broker 调用必须显式传入下列路径。
_ROOT = _repo_root()
GEO_STRU_OUTPUTS        = _ROOT + "/geo-stru/results"
DATACOLLE_OUTPUTS       = _ROOT + "/data-colle/prospector/output"
GEO_ANALYSER_OUTPUTS    = _ROOT + "/geo-analyser/results"
GEO_EXPLORATION_OUTPUTS = _ROOT + "/geo-exploration/Python_Project/web_app/uploads"
GEO_INSAR_DOWNLOADS     = _ROOT + "/geo-insar/downloads"


# geo-stru 高清地质构造解译图：(metadata.products 键, 图注)
_GEO_STRU_MAPS = [
    ("map_hillshade_png", "山体阴影遥感地质构造解译图"),
    ("map_terrain_png", "地形渲染遥感地质构造解译图"),
    ("rose_diagram", "构造线方向玫瑰图"),
]


def _geo_stru_figures(bbox) -> List[dict]:
    """取 geo-stru 对本研究区的高清地质构造解译图（本地实证，优先于公开地质图）；无则返回空。"""
    _import_commons()
    try:
        from commons.structural_broker import find_structural_for_bbox, get_product_path
    except Exception as e:
        print(f"[Figures] geo-stru import 失败：{e}")
        return []
    try:
        matches = find_structural_for_bbox(bbox, GEO_STRU_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Figures] geo-stru 查询失败：{e}")
        return []
    if not matches:
        return []
    entry = matches[0]
    aoi = entry.get("aoi_name", "")
    figs: List[dict] = []
    for key, caption in _GEO_STRU_MAPS:
        p = get_product_path(entry, key)
        if p:
            figs.append({"path": p,
                         "caption": f"{caption}（{aoi}，geo-stru 构造解译）",
                         "source": "geo-stru"})
    return figs


GEO_MODEL3D_OUTPUTS = _ROOT + "/geo-model3d/results"

# geo-model3d 三维立体成矿预测图：(metadata.products 键, 图注)
_GEO_MODEL3D_FIGS = [
    ("depth_profile_png", "三维成矿有利度/不确定性—深度剖面"),
]


def _geo_model3d_figures(bbox) -> List[dict]:
    """取 geo-model3d 对本研究区的三维立体成矿预测图件（深度切片/剖面）；无则返回空。"""
    _import_commons()
    try:
        from commons.model3d_broker import find_model3d_for_bbox, get_product_path
    except Exception as e:
        print(f"[Figures] geo-model3d import 失败：{e}")
        return []
    try:
        matches = find_model3d_for_bbox(bbox, GEO_MODEL3D_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Figures] geo-model3d 查询失败：{e}")
        return []
    if not matches:
        return []
    entry = matches[0]
    aoi = entry.get("aoi_name", "")
    ms = entry.get("model_stats", {})
    fam = ms.get("family", "")
    band = ms.get("depth_km_band", "")
    figs: List[dict] = []
    for key, caption in _GEO_MODEL3D_FIGS:
        p = get_product_path(entry, key)
        if p:
            figs.append({"path": p,
                         "caption": f"{caption}（{aoi}，成因族 {fam}，成矿深度带 {band}km，geo-model3d 立体预测）",
                         "source": "geo-model3d"})
    # 深度切片 PNG（slice_pngs 是相对路径列表）
    for rel in (entry.get("products", {}) or {}).get("slice_pngs", []) or []:
        import os as _os
        p = _os.path.join(entry["model3d_dir"], rel)
        if _os.path.exists(p):
            figs.append({"path": p, "caption": f"三维成矿有利度深度切片（{aoi}，geo-model3d）",
                         "source": "geo-model3d"})
    return figs


_PRED_METHOD_CN = {"knowledge": "知识加权融合（无标签）", "woe": "证据权法 WofE（数据驱动）",
                   "rf": "随机森林（数据驱动）", "pu": "PU-learning 正样本-未标注半监督（数据驱动）",
                   "domain_adapt": "领域自适应跨区迁移"}


def geo_model3d_modeling_summary(bbox) -> List[str]:
    """成矿预测建模小节（方向四）：从 geo-model3d metadata 提炼预测方法/标签/特征重要性/验证。

    返回 markdown 文本块列表（可直接拼入报告）；无成果或无建模信息→空。
    """
    _import_commons()
    try:
        from commons.model3d_broker import find_model3d_for_bbox
        matches = find_model3d_for_bbox(bbox, GEO_MODEL3D_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Modeling] geo-model3d 读取失败：{e}")
        return []
    if not matches:
        return []
    ms = matches[0].get("model_stats", {}) or {}
    aoi = matches[0].get("aoi_name", "")
    method = ms.get("prediction_method", "knowledge")
    ls = ms.get("label_status", {}) or {}
    fi = ms.get("feature_importance") or {}
    val = ms.get("validation", {}) or {}
    tr = ms.get("transfer") or {}

    lines = [f"### 成矿预测建模方法（AOI: {aoi}，geo-model3d）",
             f"- **预测方法**：{_PRED_METHOD_CN.get(method, method)}",
             f"- **已知矿点标签**：正样本 {ls.get('n_positive', 0)} 个"
             f"（落入网格 {ls.get('n_in_grid', 0)}，真负样本/钻孔 {ls.get('n_barren', 0)}），"
             f"{'达到' if ls.get('sufficient') else '未达'}建模阈值({ls.get('label_min', '?')})"]

    # 特征重要性（RF/PU）或证据权（WofE）
    if fi.get("method") in ("random_forest", "pu_bagging") and fi.get("importances"):
        imp = "；".join(f"{k} {v}" for k, v in list(fi["importances"].items())[:6])
        extra = (f"，OOB={fi.get('oob_score')}" if fi.get("oob_score") is not None else
                 f"，平均不确定性={fi.get('mean_uncertainty')}" if fi.get("mean_uncertainty") is not None else "")
        lines.append(f"- **可解释控矿因素（特征重要性）**：{imp}{extra}")
    elif fi.get("method") == "woe" and fi.get("weights"):
        ws = "；".join(f"{k} 对比度{v.get('contrast')}" for k, v in list(fi["weights"].items())[:6])
        lines.append(f"- **证据权对比度（WofE）**：{ws}")

    # 验证
    loo = val.get("loo_hit_rate", {}) or {}
    if loo.get("status") == "ok":
        lines.append(f"- **已知矿点捕获率验证**：前10%面积捕获 {loo.get('capture_top10pct')}、"
                     f"前20% {loo.get('capture_top20pct')}，提升度 lift₁₀={loo.get('lift_top10')}")
    kc = val.get("knowledge_consistency", {}) or {}
    if kc.get("tectonic_setting"):
        lines.append(f"- **知识校验（构造背景自洽性）**：{kc.get('note', '')}")

    # 跨区迁移
    if tr:
        lines.append(f"- **跨区迁移**：源区 {tr.get('source_aoi')}（族 {tr.get('source_family')}），"
                     f"迁移置信度 {tr.get('transfer_confidence')}，特征漂移 {tr.get('feature_shift')}")

    warns = ms.get("warnings", []) or []
    if warns:
        lines.append("- **诚实性提示**：" + "；".join(warns[:3]))

    return ["\n".join(lines)]


GEO_GEOPHYS_OUTPUTS = _ROOT + "/geo-geophys/results"

# geo-geophys 位场处理图件：(metadata.products 键, 图注前缀)
_GEO_GEOPHYS_MAP_KEYS = ["map_magnetic_rtp", "map_analytic_signal", "map_tilt"]


def _geo_geophys_figures(bbox) -> List[dict]:
    """取 geo-geophys 对本研究区的位场处理图件（化极/解析信号/倾斜角 + 磁源深度）；无则返回空。"""
    _import_commons()
    try:
        from commons.geophys_broker import find_geophys_for_bbox, get_product_path
    except Exception as e:
        print(f"[Figures] geo-geophys import 失败：{e}")
        return []
    try:
        matches = find_geophys_for_bbox(bbox, GEO_GEOPHYS_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Figures] geo-geophys 查询失败：{e}")
        return []
    if not matches:
        return []
    entry = matches[0]
    aoi = entry.get("aoi_name", "")
    ms = entry.get("model_stats", {})
    eu = (ms.get("euler") or {})
    depth_txt = (f"，磁源深度中位 {eu['depth_median_m']/1000:.1f}km" if eu.get("depth_median_m") else "")
    figs: List[dict] = []
    # 图件在 products.figures（相对路径列表）
    import os as _os
    for rel in (entry.get("products", {}) or {}).get("figures", []) or []:
        p = _os.path.join(entry["geophys_dir"], rel)
        if _os.path.exists(p):
            figs.append({"path": p,
                         "caption": f"区域物探解释（{aoi}{depth_txt}，geo-geophys 位场处理，区域尺度）",
                         "source": "geo-geophys"})
    return figs


GEO_GEOCHEM_OUTPUTS = _ROOT + "/geo-geochem/results"
# 已知矿点标签（geo-deposits 写入 geo-model3d/results/<AOI>/deposits）与钻探布孔/反馈
GEO_DEPOSITS_OUTPUTS = _ROOT + "/geo-model3d/results"
GEO_DRILL_OUTPUTS = _ROOT + "/geo-drill/results"
# 公开地球化学数据注册表根（index.json 所在目录）
GEO_GEOCHEM_PUBLIC_ROOT = _ROOT + "/geo-geochem/data/public_geochem"
# geo-7slow 七慢变量综合证据（COG 栅格 + 靶区 GeoJSON）
GEO_SLOWVARS_OUTPUTS = _ROOT + "/geo-7slow/backend/data/results"

# geo-7slow 8 慢变量权重键 → 中文（驱动 b 项 / 阻力 a 项）
_SLOWVARS_WEIGHT_CN = {
    "stress": "构造应力(驱动)", "fault": "断裂活动(驱动)", "redox": "氧化还原(驱动)",
    "fluid": "流体超压(驱动)", "chem": "化学势(驱动)", "temp_drive": "温度(驱动)",
    "cap_rock": "盖层封闭(阻力)", "temp_resist": "温度(阻力)",
}
# 7 个慢变量驱动层产物键 → 中文
_SLOWVARS_DRIVER_CN = {
    "stress_gradient": "构造应力梯度", "redox_gradient": "氧化还原梯度",
    "fluid_overpressure": "流体超压", "fault_activity": "断裂活动性",
    "cap_rock_pressure": "盖层封闭", "temp_gradient": "温度梯度",
    "chem_potential": "化学势",
}


def fetch_slowvars_text(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geo-7slow 标准输出读本研究区慢变量综合证据：矿床类型来源/置信度、权重预设、
    7 个慢变量驱动、尖点突变判别式 Δ、靶区面积与逐靶主控因子。无产物 → 空（章节静默降级）。"""
    _import_commons()
    try:
        from commons.slowvars_broker import find_slowvars_for_bbox, load_target_zones
    except Exception as e:
        print(f"[Slowvars] geo-7slow import 失败：{e}")
        return []
    try:
        matches = find_slowvars_for_bbox((min_lon, min_lat, max_lon, max_lat), GEO_SLOWVARS_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Slowvars] geo-7slow 查询失败：{e}")
        return []
    if not matches:
        return []

    entry = matches[0]
    ms = entry.get("model_stats", {}) or {}
    aoi = entry.get("aoi_name", "")
    gctx = entry.get("geologic_context") or {}

    lines = [f"【慢变量综合证据 — geo-7slow 子系统本地实证，AOI: {aoi}，run: {entry.get('run_id')}】"]

    # 矿床类型上下文：来源 + 置信度（geo-stru 推理或用户选择）
    deposit_type = ms.get("deposit_type") or gctx.get("deposit_type")
    family = ms.get("family") or gctx.get("family")
    mineral = ms.get("mineral") or gctx.get("mineral_hint")
    src = gctx.get("source") or gctx.get("geologic_context_source")
    conf = gctx.get("deposit_type_confidence") or gctx.get("geo_struct_confidence")
    if deposit_type or family or mineral:
        seg = "- 成矿类型上下文："
        if deposit_type:
            seg += f"矿床类型={deposit_type}"
        if family:
            seg += f"（成因族 {family}）"
        if mineral:
            seg += f"，矿种={mineral}"
        if src:
            seg += f"，来源={src}"
        if conf is not None:
            try:
                seg += f"，置信度={float(conf):.2f}"
            except Exception:
                pass
        lines.append(seg)

    # 权重预设（驱动 b / 阻力 a 分组）
    weights = ms.get("weights") or {}
    if weights:
        def _fmt(d):
            return "、".join(f"{_SLOWVARS_WEIGHT_CN.get(k, k)} {float(v):.2f}"
                            for k, v in sorted(d.items(), key=lambda x: -float(x[1])))
        drive = {k: v for k, v in weights.items() if k not in ("cap_rock", "temp_resist")}
        resist = {k: v for k, v in weights.items() if k in ("cap_rock", "temp_resist")}
        if drive:
            lines.append(f"- 驱动力(b)权重预设：{_fmt(drive)}")
        if resist:
            lines.append(f"- 阻力(a)权重预设：{_fmt(resist)}")

    # 7 个慢变量驱动层（产物）
    products = entry.get("products") or {}
    present = [_SLOWVARS_DRIVER_CN[k] for k in _SLOWVARS_DRIVER_CN if products.get(k)]
    if present:
        lines.append("- 七个慢变量驱动层（COG 栅格）：" + "、".join(present))

    # 尖点突变判别式 Δ
    delta_th = ms.get("delta_threshold")
    delta_pc = ms.get("delta_percentile")
    if delta_th is not None or delta_pc is not None:
        seg = "- 尖点突变判别式 Δ："
        if delta_pc is not None:
            seg += f"自适应分位 P{float(delta_pc):.0f}"
        if delta_th is not None:
            seg += f"（阈值 {float(delta_th):.2f}，Δ<阈值=双稳成矿有利区）"
        lines.append(seg)

    # 靶区面积与数量
    ta = ms.get("target_area_km2")
    tc = ms.get("target_count")
    if ta is not None and tc is not None:
        lines.append(f"- 圈定靶区：{int(tc)} 个，合计 {float(ta):.2f} km²"
                     + ("（本区慢变量场偏平缓，未圈出显著靶区）" if not tc else ""))

    # 逐靶主控慢变量（最多列前 5 个）
    try:
        zones = load_target_zones(entry) or []
    except Exception:
        zones = []
    for z in zones[:5]:
        p = z.get("properties") or {}
        drv = _SLOWVARS_DRIVER_CN.get(p.get("dominant_driver"), p.get("dominant_driver") or "—")
        seg = f"  · 靶区#{p.get('rank')}：面积 {float(p.get('area_km2', 0) or 0):.2f} km²，主控={drv}"
        if p.get("mean_delta") is not None:
            seg += f"，平均 Δ={float(p['mean_delta']):.3f}"
        lines.append(seg)

    lines.append("- 说明：慢变量综合为多源遥感/地形证据的尖点突变（cusp catastrophe）融合，靶区为"
                 "本服务内相对有利区，需结合蚀变/化探/已知矿点验证；本服务 TIR 为定标辐亮度，温度项仅相对意义。")
    return ["\n".join(lines)]


def _geo_slowvars_figures(bbox) -> List[dict]:
    """geo-7slow 慢变量图件：仅嵌入位图（png/jpg）。当前产物为 COG/GeoJSON、暂无报告级位图→空；
    待 geo-7slow 产出 PNG 预览（delta/target_zones/dominant_driver）即自动生效。"""
    _import_commons()
    try:
        from commons.slowvars_broker import find_slowvars_for_bbox, get_product_path
    except Exception as e:
        print(f"[Figures] geo-7slow import 失败：{e}")
        return []
    try:
        matches = find_slowvars_for_bbox(tuple(bbox), GEO_SLOWVARS_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Figures] geo-7slow 查询失败：{e}")
        return []
    if not matches:
        return []
    entry = matches[0]
    aoi = entry.get("aoi_name", "")
    figs: List[dict] = []
    for key, cap in [("delta_discriminant_png", "尖点突变判别式 Δ"),
                     ("target_zones_png", "慢变量靶区圈定"),
                     ("dominant_driver_png", "主控慢变量分布")]:
        p = get_product_path(entry, key)
        if p and p.lower().endswith((".png", ".jpg", ".jpeg")):
            figs.append({"path": p, "caption": f"{cap}（{aoi}，geo-7slow）", "source": "geo-7slow"})
    return figs


def _geo_geochem_figures(bbox) -> List[dict]:
    """取 geo-geochem 对本研究区的化探异常图件（元素异常/组合异常/C-A 曲线）；无则返回空。"""
    _import_commons()
    try:
        from commons.geochem_broker import find_geochem_for_bbox
    except Exception as e:
        print(f"[Figures] geo-geochem import 失败：{e}")
        return []
    try:
        matches = find_geochem_for_bbox(bbox, GEO_GEOCHEM_OUTPUTS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Figures] geo-geochem 查询失败：{e}")
        return []
    if not matches:
        return []
    entry = matches[0]
    aoi = entry.get("aoi_name", "")
    mineral = (entry.get("model_stats") or {}).get("mineral_type", "")
    figs: List[dict] = []
    # 图件在 products.figures（相对 geochem_dir 的路径列表）
    import os as _os
    for rel in (entry.get("products", {}) or {}).get("figures", []) or []:
        p = _os.path.join(entry["geochem_dir"], rel)
        if _os.path.exists(p):
            figs.append({"path": p,
                         "caption": f"地球化学异常（{aoi}，目标矿种 {mineral}，geo-geochem C-A 分形分离/多元素组合）",
                         "source": "geo-geochem"})
    return figs


def fetch_geochem_summary_text(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geo-geochem 标准输出读取本研究区化探异常统计与浓集中心，供 LLM 提取（本地实证，优先采信）。"""
    _import_commons()
    try:
        from commons.geochem_broker import find_geochem_for_bbox, load_anomaly_points
    except Exception:
        return []
    try:
        matches = find_geochem_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_GEOCHEM_OUTPUTS, tenant_id=current_tenant())
    except Exception:
        return []
    if not matches:
        return []
    entry = matches[0]
    ms = entry.get("model_stats") or {}
    lines = [f"【本地地球化学异常 - geo-geochem 子系统标准输出，AOI: {entry.get('aoi_name')}，"
             f"目标矿种: {ms.get('mineral_type', '?')}】(优先于 Web 搜索，可直接引用)"]
    ast = ms.get("anomaly_stats") or {}
    if ast:
        els = ast.get("elements_processed") or []
        comb = ast.get("combination") or {}
        lines.append(f"- 基于 {ast.get('n_points', '?')} 个化探采样点，提取 {len(els)} 种元素异常"
                     f"（{'、'.join(els)}），组合方法 {comb.get('method', '-')}。")
        thr = ast.get("thresholds") or {}
        for el, v in list(thr.items())[:8]:
            lines.append(f"  · {el} 异常下限 {v.get('threshold')}（{v.get('method', '-')}）")
    elif ms.get("prior_only"):
        po = ms["prior_only"]
        lines.append(f"- 本区无实测化探点位，仅有区域背景阈值先验（{po.get('n_threshold_elements', 0)} 种元素），未提取异常。")
        # 元素异常分级阈值表（background/weak/moderate/strong），供报告引用具体下限
        thr = po.get("thresholds") or {}
        if thr:
            lines.append("- 区域背景与异常分级阈值（背景/弱/中/强异常）：")
            for el, v in list(thr.items())[:10]:
                lines.append(f"  · {el}：背景 {v.get('background', '-')}，弱 {v.get('weak_anomaly', '-')}，"
                             f"中 {v.get('moderate_anomaly', '-')}，强 {v.get('strong_anomaly', '-')}")
    pts = load_anomaly_points(entry)
    if pts:
        lines.append(f"- 识别浓集中心 {len(pts)} 个（按强度排序，前若干）：")
        for a in pts[:8]:
            lon, lat = a.get("lon"), a.get("lat")
            loc = f"({lon:.4f}, {lat:.4f})" if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) else "(-,-)"
            lines.append(f"  · #{a.get('rank')}: 中心 {loc}，面积 {a.get('area_km2', '-')} km²，衬度 {a.get('contrast', '-')}")
    for w in (ms.get("warnings") or []):
        lines.append(f"- 注意：{w}")
    return ["\n".join(lines)]


def fetch_datacolle_section(section_id: str, min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """
    从 data-colle 标准输出读取与本研究区相交、最新一次成果的指定章节文本
    （section_id ∈ geology / geophysics / geochemistry）。
    """
    _import_commons()
    try:
        from commons.datacolle_broker import find_datacolle_for_bbox
    except Exception:
        return []
    matches = find_datacolle_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), DATACOLLE_OUTPUTS, tenant_id=current_tenant())
    if not matches:
        return []
    entry = matches[0]
    text = (entry.get("sections") or {}).get(section_id, "")
    if not text:
        return []
    header = (f"【本地资料 - data-colle 子系统标准输出，AOI: {entry.get('aoi_name')}，"
              f"目标矿种: {entry.get('mineral','?')}】(优先于 Web 搜索，可直接引用)\n")
    return [header + text]


def fetch_alteration_local(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geo-analyser 读取与本研究区相交的蚀变分析结果，转中文叙述文本。"""
    _import_commons()
    try:
        from commons.analyser_broker import find_alteration_for_bbox
    except Exception:
        return []
    texts: List[str] = []
    for entry in find_alteration_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_ANALYSER_OUTPUTS, tenant_id=current_tenant()):
        lines = [f"【本地蚀变分析 - AOI: {entry.get('aoi_name')}，成矿类型: {entry.get('deposit_type','')}】"
                 f"(来源: geo-analyser 标准输出，优先于 Web)"]
        for r in entry.get("results", []):
            lines.append(
                f"- 蚀变矿物「{r.get('mineral','')}」({r.get('anomaly_type','')})："
                f"{r.get('sensor','')}/{r.get('method','')} 法，异常占比 {r.get('anomaly_ratio','?')}%，"
                f"阈值 {r.get('threshold','?')}"
            )
        st = entry.get("structural", {})
        if st and st.get("association_by_mineral"):
            for m, a in st["association_by_mineral"].items():
                lines.append(
                    f"- 构造-蚀变关联「{m}」：异常像元 {a.get('n_anomaly','?')}，"
                    f"中位距断裂 {a.get('median_dist_m','?'):.0f} m，"
                    f"{a.get('buffer_m','?'):.0f} m 缓冲内占比 {a.get('frac_within_buffer',0)*100:.1f}%"
                    if isinstance(a.get('median_dist_m'), (int, float)) else
                    f"- 构造-蚀变关联「{m}」：异常像元 {a.get('n_anomaly','?')}"
                )
        lines.append("- 说明：蚀变异常为多源遥感影像自动提取的找矿决策支持产物，需野外查证。")
        texts.append("\n".join(lines))
    return texts


def fetch_deep_detection_local(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geo-exploration 读取与本研究区相交的矿产深部探测结果，转中文叙述文本。"""
    _import_commons()
    try:
        from commons.exploration_broker import find_exploration_for_bbox
    except Exception:
        return []
    texts: List[str] = []
    for entry in find_exploration_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_EXPLORATION_OUTPUTS, tenant_id=current_tenant()):
        st = entry.get("statistics", {})
        targets = entry.get("prospecting_targets", [])
        lines = [
            f"【本地深部探测 - AOI: {entry.get('aoi_name')}，目标矿种: {entry.get('mineral_type','')}】"
            f"(来源: geo-exploration 标准输出，优先于 Web)",
            f"- 深部成矿潜力指数：最大 {st.get('max_value','?')}，均值 {st.get('mean_value','?')}，"
            f"标准差 {st.get('std_value','?')}",
            f"- 阈值以上异常像元数：{st.get('area_threshold','?')}",
            f"- 识别靶区数（top）：{len(targets)} 个",
        ]
        for t in targets[:5]:
            lines.append(f"  · 靶区#{t.get('rank')}：经度 {t.get('longitude'):.4f}，"
                         f"纬度 {t.get('latitude'):.4f}，潜力值 {t.get('value'):.3f}")
        lines.append("- 说明：深部探测基于舒曼波共振遥感反演，靶区为决策支持，需工程验证。")
        texts.append("\n".join(lines))
    return texts


def fetch_geophys_text(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geo-geophys 标准输出读取本研究区位场处理解释与欧拉磁源深度（本地实证，优先采信）。"""
    _import_commons()
    try:
        from commons.geophys_broker import find_geophys_for_bbox, load_euler_sources
    except Exception:
        return []
    try:
        matches = find_geophys_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_GEOPHYS_OUTPUTS, tenant_id=current_tenant())
    except Exception:
        return []
    if not matches:
        return []
    entry = matches[0]
    ms = entry.get("model_stats") or {}
    eu = ms.get("euler") or {}
    lines = [f"【本地地球物理 - geo-geophys 子系统标准输出，AOI: {entry.get('aoi_name')}，"
             f"目标矿种: {ms.get('mineral_type', '?')}】(优先于 Web 搜索，可直接引用)"]
    # plain_summary 已是面向报告的中文解释，直接引用
    for s in (ms.get("plain_summary") or [])[:6]:
        lines.append(f"- {s}")
    if eu:
        dm = eu.get("depth_median_m")
        dtxt = f"，深度中位 {dm/1000:.1f} km" if isinstance(dm, (int, float)) else ""
        lines.append(f"- 欧拉反演磁源：{eu.get('n_points', '?')} 个（结构指数 SI={eu.get('si', '-')}，"
                     f"窗口 {eu.get('window', '-')}{dtxt}）。")
    # 欧拉磁源点位（前若干，按埋深排序）
    try:
        pts = load_euler_sources(entry)
    except Exception:
        pts = []
    if pts:
        pts_sorted = sorted([p for p in pts if isinstance(p.get("depth_m"), (int, float))],
                            key=lambda p: p["depth_m"])
        if pts_sorted:
            lines.append(f"- 代表性磁源点位（共 {len(pts)} 个，列举浅部前若干）：")
            for p in pts_sorted[:6]:
                lon, lat = p.get("lon"), p.get("lat")
                loc = f"({lon:.4f}, {lat:.4f})" if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) else "(-,-)"
                lines.append(f"  · {loc}，埋深 {p['depth_m']/1000:.2f} km，置信度 {p.get('confidence', '-')}")
    for w in (ms.get("warnings") or [])[:3]:
        lines.append(f"- 注意：{w}")
    return ["\n".join(lines)]


def fetch_known_deposits_text(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 deposits（geo-deposits 写入 geo-model3d/results）读取本研究区已知矿点标签（本地实证）。"""
    _import_commons()
    try:
        from commons.deposits_broker import find_deposits_for_bbox, get_points
    except Exception:
        return []
    try:
        matches = find_deposits_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_DEPOSITS_OUTPUTS, tenant_id=current_tenant())
    except Exception:
        return []
    if not matches:
        return []
    entry = matches[0]
    pts = get_points(entry)
    if not pts:
        return []
    lines = [f"【本地已知矿点 - geo-deposits 子系统标准输出，AOI: {entry.get('aoi_name')}】"
             f"(真实矿点标签，优先于 Web 搜索)",
             f"- 区内及周边已登记已知矿点 {len(pts)} 处（按矿种/类型汇总）："]
    for p in pts[:12]:
        lon, lat = p.get("lon"), p.get("lat")
        loc = f"({lon:.4f}, {lat:.4f})" if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) else "(-,-)"
        name = p.get("name") or "(未命名)"
        lines.append(f"  · {name}：矿种 {p.get('commodity', '-')}，类型 {p.get('deposit_type', '-')}，"
                     f"位置 {loc}，来源 {p.get('source', '-')}")
    return ["\n".join(lines)]


def fetch_drill_text(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geo-drill 标准输出读取本研究区 AI 布孔与钻探反馈（本地实证）；无产出返回空。"""
    _import_commons()
    try:
        from commons.drill_broker import find_drill_for_bbox, get_holes, get_feedback
    except Exception:
        return []
    try:
        matches = find_drill_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_DRILL_OUTPUTS, tenant_id=current_tenant())
    except Exception:
        return []
    if not matches:
        return []
    entry = matches[0]
    holes = get_holes(entry)
    feedback = get_feedback(entry)
    ms = entry.get("model_stats") or {}
    if not holes and not feedback:
        return []
    lines = [f"【本地钻探验证 - geo-drill 子系统标准输出，AOI: {entry.get('aoi_name')}，"
             f"目标矿种: {ms.get('mineral_type', '?')}】(决策支持，需工程实施验证)"]
    for s in (ms.get("plain_summary") or [])[:3]:
        lines.append(f"- {s}")
    if holes:
        lines.append(f"- AI 推荐钻孔 {len(holes)} 个（按优先级，列举前若干）：")
        for h in holes[:6]:
            lon, lat = h.get("lon"), h.get("lat")
            loc = f"({lon:.4f}, {lat:.4f})" if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) else "(-,-)"
            lines.append(f"  · {h.get('hole_id', '#'+str(h.get('rank', '?')))}：{loc}，"
                         f"目标深度 {h.get('target_depth_m', '-')} m，评分 {h.get('score', '-')}，"
                         f"优先级 {h.get('priority', '-')}")
    if feedback:
        ore = sum(1 for f in feedback if f.get("outcome") == "ore")
        lines.append(f"- 钻孔反馈 {len(feedback)} 个：见矿 {ore}，无矿 {len(feedback)-ore}：")
        for f in feedback[:6]:
            lon, lat = f.get("lon"), f.get("lat")
            loc = f"({lon:.4f}, {lat:.4f})" if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) else "(-,-)"
            lines.append(f"  · {f.get('hole_id', '-')}：{loc}，结果 {f.get('outcome', '-')}，"
                         f"元素 {f.get('element', '-')}，最高品位 {f.get('max_grade', '-')}（边界 {f.get('cutoff', '-')}）")
    return ["\n".join(lines)]


def fetch_geochem_public_text(min_lon, min_lat, max_lon, max_lat) -> List[str]:
    """从 geochem_public 注册表读取与本研究区相交的公开化探数据集统计摘要（本地实证，补充）。"""
    _import_commons()
    try:
        from commons.geochem_public_broker import find_public_geochem_for_bbox, load_public_geochem_df
    except Exception:
        return []
    try:
        matches = find_public_geochem_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), GEO_GEOCHEM_PUBLIC_ROOT, tenant_id=current_tenant())
    except Exception:
        return []
    if not matches:
        return []
    texts: List[str] = []
    for entry in matches[:2]:
        df, cols = load_public_geochem_df(entry)
        if df is None or df.empty:
            continue
        lines = [f"【公开地球化学数据 - {entry.get('name', '?')}（{entry.get('source', 'public')}）】"
                 f"(来源: geochem_public 注册表，{len(df)} 个采样点)"]
        elems = ((cols or {}).get("elements")) or {}
        for el, colname in list(elems.items())[:8]:
            if colname in df.columns:
                try:
                    s = df[colname].dropna()
                    if len(s):
                        lines.append(f"- {el}（{colname}）：均值 {s.mean():.3g}，中位 {s.median():.3g}，"
                                     f"P95 {s.quantile(0.95):.3g}，max {s.max():.3g}")
                except Exception:
                    pass
        if len(lines) > 1:
            texts.append("\n".join(lines))
    return texts


def collect_subsystem_figures(category_id: str, min_lon, min_lat, max_lon, max_lat) -> List[dict]:
    """
    [向后兼容薄封装] 按声明式消费契约（consumption.CHAPTER_CONTRACT）确定性收集某章节
    对应的子系统图件（不经 LLM，避免幻觉）。返回 [{path, caption, source}, ...]。

    图件优先级与 fallback（如 geology 优先 geo-stru/geo-model3d、无则回退 Macrostrat；
    geophysics 优先 geo-geophys、无则回退 data-colle）统一在 consumption 契约表里声明。
    """
    from .consumption import consume_chapter
    # lat/lon 对图件 provider 不影响，传中心点占位
    lat = (min_lat + max_lat) / 2.0
    lon = (min_lon + max_lon) / 2.0
    return consume_chapter(category_id, lat, lon, min_lon, min_lat, max_lon, max_lat)["figures"]


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

SUPPORTED = {"climate", "geography", "geology", "geophysics", "geochemistry",
             "hydrology", "insar_deformation", "remote_sensing", "slow_variables"}


# ---------------------------------------------------------------------------
# InSAR 形变监测 — 从 geo-insar 标准输出读本地干涉对统计
# ---------------------------------------------------------------------------

_GEO_INSAR_DOWNLOADS = GEO_INSAR_DOWNLOADS


def _insar_pair_stats_lines(aoi_dir) -> Tuple[List[str], Optional[list]]:
    """从一个 AOI 目录的 stack_index.json / 逐对 metadata 汇总干涉对堆栈统计。

    返回 (文本行列表, aoi_bbox)；无干涉对→([], None)。
    """
    from pathlib import Path
    aoi_dir = Path(aoi_dir)
    idx_path = aoi_dir / "stack_index.json"
    summary = None
    if idx_path.exists():
        try:
            with open(idx_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
        except Exception:
            summary = None
    pair_metas: List[dict] = []
    if summary is not None:
        pair_metas = summary.get("pairs", []) or []
    else:
        for sensor_dir in aoi_dir.iterdir():
            if not sensor_dir.is_dir():
                continue
            for pair_dir in sensor_dir.iterdir():
                meta_p = pair_dir / "metadata.json"
                if not meta_p.exists():
                    continue
                try:
                    with open(meta_p, "r", encoding="utf-8") as f:
                        pair_metas.append(json.load(f))
                except Exception:
                    pass
    if not pair_metas:
        return [], None

    bbox = next((m.get("aoi_bbox") for m in pair_metas if m.get("aoi_bbox")), None)
    n = len(pair_metas)
    dates = sorted({d for m in pair_metas
                    for d in (m.get("master_date", ""), m.get("slave_date", "")) if d})
    baselines = [m.get("temporal_baseline_days") for m in pair_metas
                 if m.get("temporal_baseline_days") is not None]
    perp = [m.get("perp_baseline_m") for m in pair_metas if m.get("perp_baseline_m") is not None]
    coh = [m.get("stats", {}).get("coherence_mean") for m in pair_metas]
    coh = [v for v in coh if v is not None]
    disp_min = [m.get("stats", {}).get("los_displacement_min_mm") for m in pair_metas]
    disp_min = [v for v in disp_min if v is not None]
    disp_max = [m.get("stats", {}).get("los_displacement_max_mm") for m in pair_metas]
    disp_max = [v for v in disp_max if v is not None]
    sources = sorted({m.get("source", "") for m in pair_metas if m.get("source")})
    pols = sorted({m.get("polarization", "") for m in pair_metas if m.get("polarization")})
    orbits = sorted({m.get("orbit_direction", "") for m in pair_metas if m.get("orbit_direction")})

    lines = [f"- 干涉对数量: {n} 对",
             f"- 时间跨度: {dates[0] if dates else '?'} 至 {dates[-1] if dates else '?'}"]
    if baselines:
        lines.append(f"- 平均时间基线: {sum(baselines)/len(baselines):.1f} 天 "
                     f"(范围 {min(baselines)}-{max(baselines)} 天)")
    if perp:
        lines.append(f"- 垂直基线范围: {min(perp):.0f}–{max(perp):.0f} m")
    if coh:
        lines.append(f"- 整体相干性均值: {sum(coh)/len(coh):.3f} "
                     f"(中位数 {sorted(coh)[len(coh)//2]:.3f})")
    if disp_min and disp_max:
        lines.append(f"- 逐对 LOS 形变范围: {min(disp_min):.2f} 至 {max(disp_max):.2f} mm")
    if sources:
        lines.append(f"- 数据来源: {', '.join(sources)}")
    if pols or orbits:
        lines.append(f"- 极化: {', '.join(pols) or '-'}; 轨道方向: {', '.join(orbits) or '-'}")
    lines.append("- 数据契约: commons/insar_schema.json")
    return lines, bbox


def _insar_2d_decomp_lines(aoi_dir) -> List[str]:
    """读取 decomposition_2d.json（升降轨 2D 分解），返回垂直/东西向形变速率文本行；无则空。"""
    from pathlib import Path
    p = Path(aoi_dir) / "decomposition_2d.json"
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    st = d.get("stats") or {}
    vmean = st.get("vertical_mean_mm_yr")
    if not isinstance(vmean, (int, float)):
        return []
    vmin, vmax = st.get("vertical_min_mm_yr"), st.get("vertical_max_mm_yr")
    lines = [f"- 升降轨 2D 分解（{d.get('method', 'vertical+EW')}）："]
    rng = (f"，区间 {vmin:.2f}–{vmax:.2f}"
           if isinstance(vmin, (int, float)) and isinstance(vmax, (int, float)) else "")
    lines.append(f"  · 垂直形变速率: 均值 {vmean:.2f}{rng} mm/yr（正=抬升，负=沉降）")
    return lines


def fetch_insar_local(
    lat: float, lon: float,
    min_lon: float, min_lat: float,
    max_lon: float, max_lat: float,
) -> List[str]:
    """
    扫描 geo-insar 标准输出目录,把本研究区范围内的 InSAR 堆栈统计转成文本注入到
    Tavily/Claude 的 raw_data 中。优先级高于 Tavily(本地权威数据)。

    匹配规则:对每个 AOI 目录,读 metadata.json 的 aoi_bbox 判断与本研究区是否重叠。

    优先走 commons.insar_broker（AOI 级形变证据契约 stats）；broker 无结果时回退到
    旧的逐对 metadata 目录遍历，保持兼容。

    Returns
    -------
    List[str]: 每个匹配 AOI 一段叙述文本(中文,带具体数值和单位)
    """
    from pathlib import Path

    def _intersects(b):
        if not b or len(b) < 4:
            return False
        return not (b[2] < min_lon or b[0] > max_lon or b[3] < min_lat or b[1] > max_lat)

    # 主路径：insar_broker 标准 AOI 级形变证据契约 + 同一 AOI 目录的 2D 分解与逐对堆栈细节（合并）
    _import_commons()
    try:
        from commons.insar_broker import find_insar_for_bbox
        broker_matches = find_insar_for_bbox(_bbox(min_lon, min_lat, max_lon, max_lat), _GEO_INSAR_DOWNLOADS, tenant_id=current_tenant())
    except Exception:
        broker_matches = []

    texts: List[str] = []
    covered = set()
    for entry in broker_matches:
        aoi_dir = entry.get("insar_dir")
        if aoi_dir:
            covered.add(str(Path(aoi_dir).resolve()))
        st = entry.get("stats") or {}
        lines = [f"【本地 InSAR 形变监测 - AOI: {entry.get('aoi_name')}】"
                 f"(来源: geo-insar 标准输出契约，本地实证，优先于 Web)"]
        if isinstance(st.get("deformation_rate_abs_mean_mm_yr"), (int, float)):
            lines.append(
                f"- LOS 形变速率(绝对值): 均值 {st['deformation_rate_abs_mean_mm_yr']:.2f} mm/yr，"
                f"P95 {st.get('deformation_rate_abs_p95_mm_yr', 0):.2f}，"
                f"峰值 {st.get('deformation_rate_abs_max_mm_yr', 0):.2f} mm/yr")
            cov = st.get("coverage_ratio")
            cov_txt = f"{cov*100:.1f}%" if isinstance(cov, (int, float)) else "?"
            lines.append(
                f"- 有效覆盖率: {cov_txt}，burst 数: {st.get('n_bursts', '?')}，"
                f"证据源: {st.get('evidence_source', '-')}")
        if aoi_dir and Path(aoi_dir).is_dir():
            lines += _insar_2d_decomp_lines(aoi_dir)           # 升降轨 2D 分解（垂直/EW）
            pair_lines, _ = _insar_pair_stats_lines(aoi_dir)   # 干涉对数/时间跨度/相干性/基线/极化轨道
            lines += pair_lines
        texts.append("\n".join(lines))

    # 回退：无 insar_metadata.json 契约、但有逐对堆栈的 AOI 目录（broker 未覆盖）
    root = Path(_GEO_INSAR_DOWNLOADS)
    if root.exists():
        for aoi_dir in root.iterdir():
            if not aoi_dir.is_dir() or str(aoi_dir.resolve()) in covered:
                continue
            pair_lines, bbox = _insar_pair_stats_lines(aoi_dir)
            if not pair_lines or (bbox and not _intersects(bbox)):
                continue
            head = [f"【本地 InSAR 堆栈 - AOI: {aoi_dir.name}】(来源: geo-insar 标准输出，本地实证)"]
            texts.append("\n".join(head + _insar_2d_decomp_lines(aoi_dir) + pair_lines))

    return texts


def _geo_insar_figures(bbox) -> List[dict]:
    """取 geo-insar 对本研究区的 SBAS 时序反演图、速度图与升降轨 2D 速度对比图。

    这些 PNG 未被 insar_metadata.json 的 products 索引（只索引了 .tif 证据），故确定性扫目录收集；
    仅取 sbas/<burst>/ 与 AOI 级 2D 对比图，不收 sentinel1_insar 逐对图（数量过多）。
    """
    _import_commons()
    try:
        from commons.insar_broker import find_insar_for_bbox
    except Exception as e:
        print(f"[Figures] geo-insar import 失败：{e}")
        return []
    try:
        matches = find_insar_for_bbox(bbox, _GEO_INSAR_DOWNLOADS, tenant_id=current_tenant())
    except Exception as e:
        print(f"[Figures] geo-insar 查询失败：{e}")
        return []
    import os
    figs: List[dict] = []
    for entry in matches:
        d = entry.get("insar_dir")
        if not d or not os.path.isdir(d):
            continue
        aoi = entry.get("aoi_name", "")
        # burst → 升/降轨 映射（来自 2D 分解的 ascending/descending.velocity 路径）
        orbit = {}
        dp = os.path.join(d, "decomposition_2d.json")
        if os.path.exists(dp):
            try:
                with open(dp, "r", encoding="utf-8") as f:
                    dd = json.load(f)
                for key, cn in (("ascending", "升轨"), ("descending", "降轨")):
                    v = (dd.get(key) or {}).get("velocity", "") or ""
                    parts = v.split("/")
                    if len(parts) >= 2:
                        orbit[parts[1]] = cn
            except Exception:
                pass
        # AOI 级升降轨 2D 速度对比图
        p = os.path.join(d, "velocity_comparison_2d.png")
        if os.path.exists(p):
            figs.append({"path": p,
                         "caption": f"升降轨 LOS 2D 分解速度对比图（{aoi}，geo-insar InSAR 形变监测）",
                         "source": "geo-insar"})
        # SBAS 逐 burst：年均速率图 + 特征点位时序反演图
        sbas_dir = os.path.join(d, "sbas")
        if os.path.isdir(sbas_dir):
            for burst in sorted(os.listdir(sbas_dir)):
                bd = os.path.join(sbas_dir, burst)
                if not os.path.isdir(bd):
                    continue
                tag = orbit.get(burst, burst)
                vm = os.path.join(bd, "velocity_map.png")
                if os.path.exists(vm):
                    figs.append({"path": vm,
                                 "caption": f"SBAS 时序反演—年均形变速率图（{tag} {burst}，{aoi}，geo-insar）",
                                 "source": "geo-insar"})
                ts = os.path.join(bd, "timeseries_points.png")
                if os.path.exists(ts):
                    figs.append({"path": ts,
                                 "caption": f"SBAS 时序反演—特征点位形变时序（{tag} {burst}，{aoi}，geo-insar）",
                                 "source": "geo-insar"})
    return figs


def fetch_direct(category_id: str, lat: float, lon: float,
                 min_lon: float, min_lat: float,
                 max_lon: float, max_lat: float) -> List[str]:
    """
    [向后兼容薄封装] 按声明式消费契约（consumption.CHAPTER_CONTRACT）取某章节文本，
    丢弃来源层级仅返回文本列表。需要层级标注的调用方请直接用 consumption.consume_chapter。
    不支持/无数据的类别返回空列表（降级到 Tavily）。
    """
    from .consumption import consume_chapter
    res = consume_chapter(category_id, lat, lon, min_lon, min_lat, max_lon, max_lat)
    return [t for t, _level in res["texts"]]


def _strike_group(deg: float) -> str:
    """把走向角(0–180°,正北为0顺时针)归入四组方位名。"""
    d = deg % 180.0
    if d < 22.5 or d >= 157.5:
        return "近SN(近南北)"
    if d < 67.5:
        return "NE(北东)"
    if d < 112.5:
        return "近EW(近东西)"
    return "NW(北西)"


def _structural_metallogenic_interp(st: dict) -> str:
    """
    基于 geo-stru 已落盘的构造统计,给出**与矿种无关**的通用构造控矿初步研判。
    只依据真实数值(走向分组/断裂密度/线性体规模)做"源-运-储"框架下的导/容矿
    通道判读,不引入任何具体矿种假设、不臆造未提取的构造。结论属决策支持,需野外核实。
    """
    lines: List[str] = []
    strikes = st.get("dominant_strikes_deg") or []
    groups: List[str] = []
    for d in strikes:
        try:
            g = _strike_group(float(d))
        except (TypeError, ValueError):
            continue
        if g not in groups:
            groups.append(g)

    if groups:
        if len(groups) >= 2:
            lines.append(
                f"- 构造格架:发育 {len(groups)} 组优势方向({'、'.join(groups)}),"
                f"以首组「{groups[0]}」为区域主干断裂格架,余组为横切/次级构造。"
                f"不同方向断裂的**交汇结点**(尤其主干×横切近共轭部位)是应力-流体易聚集、"
                f"成矿最有利的构造部位,应作为找矿优先靶区。"
            )
        else:
            lines.append(
                f"- 构造格架:优势方向集中于「{groups[0]}」单组,构成区域主干断裂格架;"
                f"沿该组主断裂的拐折、分支与膨大部位为有利容矿构造部位。"
            )

    dens = st.get("lineament_density_mean")
    if isinstance(dens, (int, float)):
        lines.append(
            f"- 断裂网络与导/容矿:平均断裂密度 {dens:.4f}。在「源-运-储」成矿模型中,"
            f"断裂网络承担「运」的角色——既是深部成矿流体的**导矿通道**,其交汇/密集带与"
            f"次级裂隙又构成**容矿空间**;断裂密度热点带因而是构造控矿先验高权重区。"
        )

    n = st.get("n_lineaments")
    tot = st.get("total_lineament_length_km")
    if isinstance(n, int) and n > 0 and isinstance(tot, (int, float)):
        lines.append(
            f"- 控矿研判要点:本区共提取 {n} 条线性体、总长 {tot:.1f} km;"
            f"建议将「近断裂带 + 蚀变/化探异常套合」「断裂交汇结点」「密度热点」三类位置"
            f"叠合圈定靶区(可进一步叠加 geo-analyser 蚀变、geo-exploration 深部探测收敛)。"
        )

    if not lines:
        return ""
    return "构造控矿初步研判(通用框架,与具体矿种无关):\n" + "\n".join(lines) + "\n"


def fetch_structural_local(
    min_lon: float, min_lat: float,
    max_lon: float, max_lat: float,
) -> List[str]:
    """
    扫描 geo-stru 标准构造解译输出,把与本研究区相交的构造统计转成中文文本,
    注入"地质与矿产"章节,作为本 AOI 影像实测的硬本地实证(优先于 Web 搜索)。
    复用 commons/structural_broker(bbox 相交发现 + metadata.json 读取)。

    除几何/统计事实外,还附带一段**通用构造控矿初步研判**(走向分组、共轭交汇靶区、
    密度热点=导/容矿通道、源-运-储框架),给下游 LLM 合成「成矿-构造关系」提供本地实证抓手,
    避免因缺实证而在「不得凭空捏造」约束下略过该论述。
    """
    _import_commons()
    try:
        from commons.structural_broker import find_structural_for_bbox
    except Exception:
        return []

    texts: List[str] = []
    for entry in find_structural_for_bbox((min_lon, min_lat, max_lon, max_lat), GEO_STRU_OUTPUTS, tenant_id=current_tenant()):
        st = entry.get("structural_stats", {})
        n = st.get("n_lineaments")
        text = f"【本地构造解译 - AOI: {entry.get('aoi_name')}】(来源: geo-stru 标准输出)\n"
        if n is not None:
            text += f"- 自动提取线性体(断裂/线性构造): {n} 条\n"
        if st.get("total_lineament_length_km") is not None:
            text += f"- 线性体总长度: {st['total_lineament_length_km']:.1f} km\n"
        if st.get("dominant_strikes_deg"):
            strikes = "、".join(f"{d:.0f}°" for d in st["dominant_strikes_deg"])
            text += f"- 主构造方向(走向): {strikes}\n"
        if st.get("lineament_density_mean") is not None:
            text += f"- 平均断裂密度: {st['lineament_density_mean']:.4f}\n"
        if st.get("elevation_range_m"):
            er = st["elevation_range_m"]
            text += f"- 高程范围: {er[0]:.0f}–{er[1]:.0f} m\n"
        interp = _structural_metallogenic_interp(st)
        if interp:
            text += interp
        text += "- 数据契约: commons/structural_schema.json v1\n"
        text += "- 说明: 构造解译为遥感地形自动提取的决策支持产物,断裂位置/方向及控矿研判需野外核实。\n"
        texts.append(text)
    return texts
