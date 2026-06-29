"""系统全景知识 — 12 个服务的完整 I/O 契约、依赖 DAG、执行阶段。

供 LLM Agent（Planner）查阅，使其"对每个环节非常清晰"。
"""

from __future__ import annotations

# ── 服务 I/O 契约 ──────────────────────────────────────────────

SYSTEM_IO_CONTRACT = {
    "geo-downloader": {
        "port": 8080,
        "description": "卫星图像自动下载工具（30+ 传感器）",
        "input": {
            "kml": "研究区 KML/KMZ（必填）",
            "sensors": "传感器列表（从 schema.yaml 选择，如 sentinel2/landsat/aster/sentinel1）",
            "seasons": "季节（summer/winter，多选）",
            "date_range": "时间范围（可选，默认最近 2 年）",
        },
        "output": "downloads/<AOI>/<sensor>/<season>/ 各波段 GeoTIFF（按 schema.yaml 组织）",
        "output_detail": "夏季/冬季各一组波段文件（如 B01.tiff~B12.tiff for Sentinel-2）",
        "consumers": ["geo-analyser", "geo-stru", "geo-insar"],
        "api": "POST /api/scan（预览可用数据）→ POST /api/download（提交下载）→ GET /api/status",
        "available_sensors": [
            {"id": "sentinel2", "label": "Sentinel-2 L2", "bands": "B01-B12（多光谱，10-60m）"},
            {"id": "landsat", "label": "Landsat 8/9 L2", "bands": "B1-B7,B10-B11（多光谱+热红外，30m）"},
            {"id": "landsat7", "label": "Landsat 7 ETM+", "bands": "B1-B8（多光谱，30m）"},
            {"id": "aster", "label": "ASTER L2", "bands": "B1-B14（VNIR+SWIR+TIR，15-90m）"},
            {"id": "sentinel1", "label": "Sentinel-1 GRD", "bands": "VV/VH（C波段雷达，10m）"},
            {"id": "ecostress", "label": "ECOSTRESS 地表温度", "bands": "地表温度.tif"},
            {"id": "dem", "label": "SRTM DEM", "bands": "DEM.tif（30m 高程）"},
        ],
        "key_constraint": "下载是全管线的前置步骤，下游 analyser/stru/insar 均依赖其产物",
    },
    "geo-analyser": {
        "port": 8081,
        "description": "蚀变成图：波段比值 / Crosta PCA / 高光谱波深 + LLM 矿床类型推断",
        "input": {
            "sensor_data": "geo-downloader 下载的传感器 GeoTIFF 目录",
            "sensor": "传感器名（Landsat8/9, Sentinel2, ASTER）",
            "mineral": "蚀变矿物类型（如 iron_oxide, argillic, phyllic, propylitic）",
            "project_name": "交付项目名（用于定位数据）",
        },
        "output": [
            "composite_score.tif（蚀变综合得分）",
            "anomaly_*.tif（各矿物异常图）",
            "alteration_deposit_db.json（蚀变-矿床关联）",
            "figures/*.png（蚀变异常图）",
        ],
        "broker": "analyser_broker",
        "consumers": ["geo-model3d", "geo-reporter"],
        "api": "POST /api/browse → POST /api/scan → POST /api/process → POST /api/analyze → GET /api/status",
        "sensor_mineral_map": {
            "Landsat8/9": ["iron_oxide", "argillic", "phyllic", "propylitic", "silica", "carbonate"],
            "Sentinel2": ["iron_oxide", "argillic", "phyllic", "propylitic", "silica"],
            "ASTER": ["argillic", "phyllic", "propylitic", "silica", "advanced_argillic", "thermal_anomaly"],
        },
        "key_constraint": "需要先有 geo-downloader 产物；不同传感器支持的蚀变矿物不同",
    },
    "geo-stru": {
        "port": 8082,
        "description": "DEM 线性构造/断裂密度解译",
        "input": {
            "dem_path": "DEM.tif（从 geo-downloader 交付库获取）",
            "landsat_dir": "Landsat 目录（可选，用于增强解译）",
            "polygon_coords": "ROI 多边形坐标",
            "project_name": "交付项目名",
        },
        "output": [
            "distance_to_lineament.tif（到断裂距离）",
            "lineament_density.tif（断裂密度）",
            "lineaments.geojson（线性体 strike/length）",
            "map_hillshade_png / map_terrain_png（构造解译图）",
            "rose_diagram（构造线方向玫瑰图）",
        ],
        "broker": "structural_broker",
        "consumers": ["geo-analyser", "geo-model3d", "geo-exploration", "geo-drill", "geo-reporter"],
        "api": "POST /api/upload_area → POST /api/start → GET /api/status",
        "key_constraint": "需要 DEM（从 geo-downloader 交付库中获取）；构造是所有热液矿床的控矿基础",
    },
    "geo-stru-insar-fusion": {
        "port": 8082,
        "description": "geo-stru 的 InSAR 形变 × 构造融合子模块（实测活动断裂 + 采空沉降识别）",
        "input": {
            "insar_dir": "geo-insar SBAS/MintPy 形变栅格目录",
            "structural_dir": "geo-stru structural 产物（可选，用于断裂活动性打标）",
            "aoi_name": "研究区名",
        },
        "output": [
            "velocity_gradient.tif（形变梯度 → 实测活动断裂）",
            "lineaments_activity.geojson（活动/非活动断裂分类）",
            "subsidence_clusters.geojson / goaf_polygons.geojson（沉降聚集/采空区）",
            "deformation_attribution（成因归类：断裂/采矿/自然沉陷，条件产物）",
            "vertical_velocity_mm_yr.tif（竖直速率，若有 2D 分解）",
            "overlay_png / rose_deformation_png / timeseries_png（融合图件）",
        ],
        "broker": "insar_fusion_broker",
        "consumers": ["geo-exploration", "geo-model3d", "geo-reporter", "geo-portal"],
        "api": "POST /api/insar_fusion → GET /api/status",
        "key_constraint": "内置于 geo-stru（同端口）；需 InSAR 源（geo-insar 或自供）；可选融合构造打标活动性",
    },
    "geo-exploration": {
        "port": 8083,
        "description": "舒曼共振多探测器融合深部预测（top-20 靶点）",
        "input": {
            "kml": "研究区 KML",
            "mineral": "目标矿种",
            "data_dir": "多传感器融合数据目录",
        },
        "output": [
            "prospecting_targets（深部靶点列表：rank/lon/lat/value）",
            "Au_deep / depth_map（经验深度）",
            "<矿种>_Result.mat（融合体）",
            "figures/*.png（探测结果图）",
        ],
        "broker": "exploration_broker",
        "consumers": ["geo-model3d", "geo-drill", "geo-reporter"],
        "api": "POST /api/start → GET /api/status",
        "key_constraint": "深度为经验公式（Yakymchuk 共振），非反演；需 geo-downloader 多传感器数据",
    },
    "geo-insar": {
        "port": 8084,
        "description": "Sentinel-1 InSAR 形变时序分析",
        "input": {
            "kml_path": "研究区 KML",
            "start/end": "S1 时间范围",
            "note": "经 ASF/HyP3 自取 SAR，不依赖 geo-downloader；属阶段一数据获取",
        },
        "output": [
            "los_velocity.tif / los_displacement.tif（LOS 形变速率/位移）",
            "干涉图/相干图",
            "stack_index.json（堆栈统计）",
        ],
        "broker": "insar_utils / insar_broker",
        "consumers": ["geo-model3d", "geo-reporter"],
        "api": "POST /api/aoi/inspect → POST /api/run（提交 HyP3）→ GET /api/tasks/<id>",
        "key_constraint": "经 ASF/HyP3 自取 SAR（独立子系统，不依赖 geo-downloader）；冬季相干性更高；"
                          "云端处理慢，编排上属阶段一提交、建模前有界等待；非所有 ROI 都需要 InSAR",
    },
    "data-colle": {
        "port": 8085,
        "description": "前期地质/物探/化探资料查取（在线 API）",
        "input": {"kml": "研究区 KML", "mineral": "目标矿种"},
        "output": [
            "sections/{geology,geophysics,geochemistry}（文本资料）",
            "geochem_thresholds（39 元素背景值+异常下限）",
            "EMAG2/WGM2012 裁剪图（全球磁/重场）",
            "mineral_kb（矿种→成矿类型→指示元素→物探方法知识）",
        ],
        "broker": "datacolle_broker",
        "consumers": ["geo-geophys", "geo-geochem", "geo-model3d", "geo-reporter"],
        "api": "POST /api/start → GET /api/status",
        "key_constraint": "在线查取，不依赖其他服务；geochem_thresholds 是 geo-geochem 的关键先验输入",
    },
    "geo-model3d": {
        "port": 8086,
        "description": "三维地质建模与立体成矿预测（证据权/模糊/信息量/贝叶斯）",
        "input": {
            "kml": "研究区 KML",
            "mineral": "目标矿种",
            "upstream_brokers": "所有 broker 产物（蚀变/构造/物探/化探/形变/深度）",
        },
        "output": [
            "prospectivity_volume.nc（三维有利度体 + 不确定性体）",
            "targets_3d.json（三维靶点）",
            "depth_slice_*.tif（深度切片）",
            "depth_profile_png（深度剖面）",
            "slice_pngs（切片 PNG 列表）",
        ],
        "broker": "model3d_broker",
        "consumers": ["geo-drill", "geo-reporter"],
        "api": "POST /api/start → GET /api/status → GET /api/result",
        "key_constraint": "消费所有 broker 产物；缺失证据层自动降级（不报错）；是 geo-drill 的前置",
    },
    "geo-geophys": {
        "port": 8087,
        "description": "位场处理（重磁）：化极/导数/延拓/欧拉反褶积",
        "input": {
            "kml": "研究区 KML",
            "datacolle": "EMAG2/WGM2012 全球位场网格（经 data-colle 获取）",
        },
        "output": [
            "map_magnetic_rtp / map_analytic_signal / map_tilt（磁处理图）",
            "euler（磁源深度点）",
            "figures/*.png",
        ],
        "broker": "geophys_broker",
        "consumers": ["geo-model3d", "geo-reporter"],
        "api": "POST /api/start → GET /api/status",
        "key_constraint": "消费 data-colle 获取的 EMAG2/WGM2012；位场是深部找矿核心证据",
    },
    "geo-geochem": {
        "port": 8088,
        "description": "化探异常提取（C-A 分形 + 多元素组合）",
        "input": {
            "upload_csv": "ICP-MS/XRF 点位 CSV（可选，用户上传）",
            "datacolle_thresholds": "prospector 背景值先验（降级模式）",
            "mineral": "目标矿种（用于元素筛选）",
        },
        "output": [
            "grids/element_anomaly_*.tif（各元素异常图）",
            "grids/multi_element_factor.tif（组合异常）",
            "anomalies.geojson（异常多边形+浓集中心）",
            "figures/*.png",
        ],
        "broker": "geochem_broker",
        "consumers": ["geo-model3d", "geo-reporter"],
        "api": "POST /api/start → GET /api/status",
        "key_constraint": "无真实点位时退化为阈值先验（status:prior_only）；是全平台唯一携带深度方向信息的实测证据",
    },
    "geo-7slow": {
        "port": 8001,
        "description": "七慢变量成矿机制综合判别（Δ判别式 + 机制靶区）",
        "input": {
            "kml": "研究区 KML/OVKML",
            "mineral": "目标矿种",
            "delivery_project": "交付库项目（用于抓取 DEM/S2/ASTER）",
            "upstream_brokers": "蚀变/构造/InSAR/物探/化探产物（P2增强消费）",
        },
        "output": [
            "stress/redox/fluid/fault/chem/cap_rock/temp 七慢变量 GeoTIFF",
            "driving_force_b.tif / resistance_a.tif",
            "delta_discriminant.tif（越低越有利）",
            "target_zones.tif / target_zones.geojson（机制靶区）",
            "dominant_driver.tif（主控慢变量）",
        ],
        "broker": "slowvars_broker",
        "consumers": ["geo-model3d", "geo-drill", "geo-reporter"],
        "api": "POST /api/start → GET /api/status/<id> → GET /api/result/<id>/<file>",
        "key_constraint": "依赖交付库原始波段；作为机制综合证据层应在二维证据后、三维建模前运行；缺失时 model3d 自动降级",
    },
    "geo-drill": {
        "port": 8089,
        "description": "AI 辅助布孔 + 见矿判定 + 闭环回灌",
        "input": {
            "kml": "研究区 KML",
            "mineral": "目标矿种",
            "model3d_products": "prospectivity_volume + targets_3d（经 model3d_broker）",
            "collar/intervals_csv": "钻孔编录（可选，用户上传）",
            "cutoff": "截止品位（可选）",
        },
        "output": [
            "planned_holes.geojson（AI 布孔）",
            "drill_feedback.geojson（见矿/无矿反馈，可回灌 model3d）",
            "holes_db.json（钻孔数据库）",
            "figures/*.png（布孔效果图）",
        ],
        "broker": "drill_broker",
        "consumers": ["geo-model3d（闭环）", "geo-reporter"],
        "api": "POST /api/start → GET /api/status → POST /api/chain（回灌）",
        "key_constraint": "必须先有 geo-model3d 产物才能布孔；闭环回灌是系统螺旋上升的关键",
    },
    "geo-reporter": {
        "port": 8081,
        "description": "多源汇总 → GB/T 9704 Word 报告 + LLM 综合",
        "input": {
            "kml": "研究区 KML",
            "mineral": "目标矿种",
            "all_brokers": "所有 broker 产物",
        },
        "output": [
            "Word 报告 (.docx)",
            "综合研判（A/B/C/D 等级 + 靶区）",
        ],
        "broker": "（纯消费者，不产出 broker 产物）",
        "consumers": ["用户"],
        "api": "POST /api/start → GET /api/status",
        "key_constraint": "消费所有 broker；LLM 综合研判使用 claude -p；是管线的终点",
    },
}

# ── 服务依赖 DAG ───────────────────────────────────────────────

SERVICE_DEPENDENCY_GRAPH = {
    "geo-downloader": [],                           # 无依赖
    "data-colle": [],                               # 在线查取
    "geo-analyser": ["geo-downloader"],
    "geo-stru": ["geo-downloader"],
    "geo-insar": [],                                # 独立子系统：经 ASF/HyP3 自取 SAR，不消费 downloader 产物
    "geo-geophys": ["data-colle"],
    "geo-geochem": ["data-colle"],                  # 上传点位可不依赖
    "geo-exploration": ["geo-downloader"],
    "geo-7slow": ["geo-analyser", "geo-stru", "geo-insar", "geo-geophys", "geo-geochem"],
    "geo-model3d": ["geo-analyser", "geo-stru", "data-colle", "geo-geophys",
                    "geo-geochem", "geo-insar", "geo-exploration", "geo-7slow"],
    "geo-drill": ["geo-model3d"],
    "geo-reporter": ["geo-model3d", "geo-drill"],
}

# ── 执行阶段 ───────────────────────────────────────────────────

EXECUTION_PHASES = [
    {
        "phase": 1,
        "name": "数据获取",
        "services": ["geo-downloader", "data-colle", "geo-insar"],
        "parallel": True,
        "description": "统一获取原始数据：光学/DEM 下载 + 在线查取地质/物探/化探资料 "
                       "+ SAR（geo-insar 经 ASF/HyP3 自取，提交后异步处理）",
    },
    {
        "phase": 2,
        "name": "并行处理",
        "services": ["geo-analyser", "geo-stru", "geo-geophys",
                      "geo-geochem", "geo-exploration"],
        "parallel": True,
        "description": "蚀变成图/构造解译/位场处理/化探异常/深部探测",
    },
    {
        "phase": 3,
        "name": "七慢变量机制综合",
        "services": ["geo-7slow"],
        "parallel": False,
        "optional": True,
        "description": "把二维证据转译为七慢变量，输出Δ判别式、主控慢变量和机制靶区",
    },
    {
        "phase": 4,
        "name": "三维建模",
        "services": ["geo-model3d"],
        "parallel": False,
        "description": "融合所有证据，产出三维有利度体 + 不确定性体",
    },
    {
        "phase": 5,
        "name": "钻探布孔",
        "services": ["geo-drill"],
        "parallel": False,
        "optional": True,
        "description": "AI 辅助布孔 + 见矿判定 + 闭环回灌",
    },
    {
        "phase": 6,
        "name": "综合报告",
        "services": ["geo-reporter"],
        "parallel": False,
        "description": "汇总所有子系统产物，生成 GB/T 9704 标准报告",
    },
]

# ── 用于 LLM prompt 的精简文本 ──────────────────────────────────


def system_knowledge_for_prompt() -> str:
    """生成供 LLM system prompt 使用的系统知识精简文本。"""
    lines = ["## 系统全景（12 个服务的输入/输出/依赖关系）\n"]
    for svc, info in SYSTEM_IO_CONTRACT.items():
        lines.append(f"### {svc}（端口 {info['port']}）")
        lines.append(f"描述: {info['description']}")
        lines.append(f"输入: {json.dumps(info['input'], ensure_ascii=False)}")
        lines.append(f"产出: {info['output'] if isinstance(info['output'], str) else ', '.join(info['output'])}")
        lines.append(f"被消费方: {', '.join(info['consumers'])}")
        deps = SERVICE_DEPENDENCY_GRAPH.get(svc, [])
        if deps:
            lines.append(f"依赖: {', '.join(deps)}")
        lines.append(f"API: {info['api']}")
        lines.append(f"关键约束: {info['key_constraint']}")
        lines.append("")

    lines.append("## 执行阶段（阶段内可并行，阶段间需顺序）\n")
    for ph in EXECUTION_PHASES:
        opt = "（可选）" if ph.get("optional") else ""
        lines.append(f"阶段 {ph['phase']}: {ph['name']}{opt} — {', '.join(ph['services'])} — {ph['description']}")

    return "\n".join(lines)


import json  # noqa: E402 (already imported at top in dataclass, but needed here for json.dumps)
