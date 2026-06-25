"""矿种 → 传感器 → 处理方法映射引擎。

消费 mineral_kb.py + knowledge.py + schema.yaml 的知识，
输入矿种 + ROI 上下文 → 输出推荐传感器组合 + 处理方法 + 理由。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.roi_analyzer import ROIContext


def _ensure_commons():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    for p in (repo, "/opt/deepexplor-services"):
        if p not in sys.path:
            sys.path.insert(0, p)


@dataclass
class SensorRecommendation:
    sensor: str           # sentinel2 / landsat / aster / sentinel1 / dem
    seasons: List[str]    # ["summer"] / ["summer", "winter"]
    required: bool        # 必选 or 可选
    reason: str           # 为什么推荐这个传感器
    target_services: List[str]  # 产出将被谁消费


@dataclass
class ServiceRecommendation:
    service: str
    required: bool
    reason: str
    params: dict = field(default_factory=dict)
    skip_reason: str = ""  # 若跳过，说明原因


@dataclass
class MineralRecommendation:
    mineral: str
    family: str                  # 成因族（如 porphyry）
    family_weights: dict         # 证据层权重
    depth_km_band: List[float]   # 成矿深度带
    sensors: List[SensorRecommendation] = field(default_factory=list)
    services: List[ServiceRecommendation] = field(default_factory=list)
    key_elements: List[str] = field(default_factory=list)   # 化探指示元素
    geophysical_methods: List[str] = field(default_factory=list)
    rationale: dict = field(default_factory=dict)  # 决策理由汇总


class MineralEngine:
    """根据矿种和 ROI 上下文推荐传感器组合和处理方法。"""

    # ── 传感器 → 可支持的蚀变矿物业 ──
    SENSOR_CAPABILITY = {
        "sentinel2": {
            "resolution": "10-20m",
            "type": "多光谱",
            "strengths": "铁染/泥化/绢英岩化/青磐岩化（波段比值+PCA）",
            "seasons": ["summer", "winter"],
            "target_services": ["geo-analyser"],
        },
        "landsat": {
            "resolution": "30m",
            "type": "多光谱+热红外",
            "strengths": "蚀变提取（备选）+ 构造解译辅助（Landsat+DEM → geo-stru）",
            "seasons": ["summer", "winter"],
            "target_services": ["geo-analyser", "geo-stru"],
        },
        "aster": {
            "resolution": "15-90m",
            "type": "VNIR+SWIR+TIR",
            "strengths": "高级泥化/绢英岩化/SiO₂ 热异常（SWIR 波深法）",
            "seasons": ["summer", "winter"],
            "target_services": ["geo-analyser"],
        },
        "sentinel1": {
            "resolution": "10m",
            "type": "C波段雷达",
            "strengths": "InSAR 形变监测（活动构造/沉降）；雷达不受云/植被影响",
            "seasons": ["summer", "winter"],
            "target_services": ["geo-insar"],
        },
        "dem": {
            "resolution": "30m",
            "type": "高程",
            "strengths": "DEM → 构造解译基础（山体阴影/坡度/线性体提取）",
            "seasons": [],
            "target_services": ["geo-stru"],
        },
    }

    # ── 矿种 → 推荐传感器优先级（编排侧策略，非地质知识库）──
    # primary/optional: 传感器选择策略（KB 暂无"矿种→传感器"映射，保留为编排策略）。
    # geophys/geochem: ⚠️ 回退用——正常路径已改为从 mineral_kb 的
    #   all_geophysical_methods / all_key_elements 派生（见 _build_service_recommendations），
    #   仅当 mineral_kb 导入失败时才用这两列兜底。
    # insar: 仍为编排策略（任何 KB 均无 InSAR 适用性，待 Tier 2 用族形变权重派生）。
    MINERAL_SENSOR_PRIORITY = {
        "铜":   {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "铜钼": {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "铜金": {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "金":   {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "银":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": True, "insar": "conditional"},
        "钼":   {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "铅锌": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": True, "insar": "conditional"},
        "钨锡": {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "钨":   {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "锡":   {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": "conditional"},
        "铁":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
        "锂":   {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": False, "geochem": True, "insar": False},
        "稀土": {"primary": ["sentinel2", "landsat", "dem"], "optional": ["aster"], "geophys": True, "geochem": True, "insar": False},
        "镍":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
        "铬":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
        "铀":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": True, "insar": False},
        "锰":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": True, "insar": False},
        "钒钛": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
        "石油": {"primary": ["sentinel2", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": True},
        "天然气": {"primary": ["sentinel2", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": True},
        "金刚石": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
        "铜钴": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": True, "insar": False},
        "铜镍": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
        "锑":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": True, "insar": False},
        "铝土": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": True, "insar": False},
        "磷":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": True, "insar": False},
        "石墨": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": False, "insar": False},
        "萤石": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": True, "insar": False},
        "煤":   {"primary": ["sentinel2", "dem"], "optional": [], "geophys": False, "geochem": False, "insar": False},
        "铌钽": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": False, "geochem": True, "insar": False},
        "钴":   {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": True, "insar": False},
        "铂族": {"primary": ["sentinel2", "landsat", "dem"], "optional": [], "geophys": True, "geochem": False, "insar": False},
    }

    @staticmethod
    def recommend(mineral: str, roi_ctx: ROIContext) -> MineralRecommendation:
        _ensure_commons()

        # 1. 解析成因族
        family = MineralEngine._resolve_family(mineral)
        family_spec = MineralEngine._get_family_spec(family)

        # 2. 获取矿种知识
        mineral_kb_info = MineralEngine._get_mineral_kb(mineral)

        # 3. 确定传感器组合
        priority = MineralEngine.MINERAL_SENSOR_PRIORITY.get(
            mineral, MineralEngine.MINERAL_SENSOR_PRIORITY.get("铜"))  # 默认铜的配置
        sensors = MineralEngine._build_sensor_recommendations(priority, mineral, roi_ctx, family_spec)

        # 4. 确定服务推荐
        services = MineralEngine._build_service_recommendations(
            priority, mineral, roi_ctx, family_spec, mineral_kb_info)

        # 5. 汇总理由
        rationale = MineralEngine._build_rationale(
            mineral, family, family_spec, roi_ctx, sensors, services)

        return MineralRecommendation(
            mineral=mineral,
            family=family,
            family_weights=family_spec.get('w', {}),
            depth_km_band=family_spec.get('depth_km', [0.5, 2.0]),
            sensors=sensors,
            services=services,
            key_elements=mineral_kb_info.get('all_key_elements', []),
            geophysical_methods=mineral_kb_info.get('all_geophysical_methods', []),
            rationale=rationale,
        )

    @staticmethod
    def _resolve_family(mineral: str) -> str:
        try:
            from commons.knowledge_resolver import resolve_family
            return resolve_family(mineral)
        except Exception:
            pass
        # fallback: 直接从 geo-model3d/core/knowledge.py 导入
        try:
            _ensure_commons()
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'geo-model3d'))
            from core.knowledge import resolve_family
            return resolve_family(mineral)
        except Exception:
            # 最简 fallback
            simple_map = {"铜": "porphyry", "金": "epithermal", "锂": "greisen_pegmatite",
                          "铁": "skarn", "铅锌": "skarn", "钨锡": "greisen_pegmatite"}
            return simple_map.get(mineral, "porphyry")

    @staticmethod
    def _get_family_spec(family: str) -> dict:
        try:
            _ensure_commons()
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'geo-model3d'))
            from core.knowledge import FAMILY_WEIGHTS
            return FAMILY_WEIGHTS.get(family, FAMILY_WEIGHTS.get("porphyry"))
        except Exception:
            return {"w": {"alteration": 0.4, "structure": 0.3, "deformation": 0.1, "depth_consistency": 0.2},
                    "depth_km": [0.5, 2.0], "applicability": "high"}

    @staticmethod
    def _get_mineral_kb(mineral: str) -> dict:
        try:
            _ensure_commons()
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'data-colle', 'prospector', 'src'))
            from mineral_kb import get_mineral_info
            return get_mineral_info(mineral)
        except Exception:
            return {"all_key_elements": [], "all_geophysical_methods": []}

    @staticmethod
    def _build_sensor_recommendations(priority, mineral, roi_ctx, family_spec) -> List[SensorRecommendation]:
        sensors = []
        weights = family_spec.get('w', {})

        # 主要传感器
        for s in priority.get("primary", []):
            cap = MineralEngine.SENSOR_CAPABILITY.get(s, {})
            seasons = list(cap.get("seasons", ["summer"]))

            # ROI 特征调整
            if s == "sentinel1":
                # 雷达在植被高/云覆盖高时更有价值
                pass
            elif s in ("sentinel2", "landsat", "aster"):
                # 光学在植被高时效果受限
                if roi_ctx.vegetation_cover == "高":
                    seasons = ["winter"]  # 冬季落叶后效果更好

            required = True
            reason = f"{cap.get('strengths', '')}（{mineral} → {family_spec.get('note', '')}）"

            # ASTER 额外判断
            if s == "aster":
                alteration_weight = weights.get("alteration", 0.0)
                if alteration_weight < 0.35:
                    required = False
                    reason += "；蚀变权重不高，ASTER 为增强项"

            sensors.append(SensorRecommendation(
                sensor=s, seasons=seasons, required=required,
                reason=reason, target_services=cap.get("target_services", []),
            ))

        # 可选传感器
        for s in priority.get("optional", []):
            cap = MineralEngine.SENSOR_CAPABILITY.get(s, {})
            sensors.append(SensorRecommendation(
                sensor=s, seasons=list(cap.get("seasons", ["summer"])),
                required=False, reason=f"可选增强：{cap.get('strengths', '')}",
                target_services=cap.get("target_services", []),
            ))

        # 注：不再把 sentinel1 作为 geo-downloader 的下载任务。
        # geo-insar 是独立子系统，经 ASF/HyP3 自取 SLC 并云端处理（属阶段一数据获取），
        # downloader 的 S1 GRD 对 InSAR 无用；是否做 InSAR 由 geo-insar 的服务推荐决定
        # （见 _build_service_recommendations）。
        return sensors

    @staticmethod
    def _build_service_recommendations(priority, mineral, roi_ctx, family_spec, mineral_kb_info) -> List[ServiceRecommendation]:
        services = []
        weights = family_spec.get('w', {})

        # ── 物探/化探门控：优先从权威知识库 mineral_kb 派生（单一真相）──
        # mineral_kb 列出该矿种的物探方法/指示元素：非空即需要对应处理。
        # 仅当 mineral_kb 不可用（导入失败，返回空壳）时，回退到 MINERAL_SENSOR_PRIORITY 手写表。
        geo_methods = mineral_kb_info.get("all_geophysical_methods") or []
        key_elements = mineral_kb_info.get("all_key_elements") or []
        kb_available = bool(mineral_kb_info.get("metallogenic_types")
                            or mineral_kb_info.get("mineral"))
        if kb_available:
            needs_geophys = bool(geo_methods)
            needs_geochem = bool(key_elements)
        else:
            needs_geophys = bool(priority.get("geophys"))
            needs_geochem = bool(priority.get("geochem"))

        # geo-analyser
        services.append(ServiceRecommendation(
            service="geo-analyser", required=True,
            reason=f"蚀变提取（权重 {weights.get('alteration', 0):.2f}）—— {family_spec.get('note', '')}",
            params={"sensor": "Sentinel2_L2"},
        ))

        # geo-stru
        services.append(ServiceRecommendation(
            service="geo-stru", required=True,
            reason=f"构造解译（权重 {weights.get('structure', 0):.2f}）—— 断裂控矿基础",
            params={"use_landsat": True},
        ))

        # data-colle
        services.append(ServiceRecommendation(
            service="data-colle", required=True,
            reason="在线查取地质/物探/化探资料 + geochem_thresholds + EMAG2/WGM2012",
            params={"sections": ["geology", "geophysics", "geochemistry"]},
        ))

        # geo-geophys（门控由 mineral_kb 派生；reason 引用 KB 实际方法）
        if needs_geophys:
            kb_methods_note = ("、".join(geo_methods[:4]) if geo_methods
                               else family_spec.get('note', ''))
            services.append(ServiceRecommendation(
                service="geo-geophys", required=True,
                reason=f"位场处理（磁/重异常）—— 该矿种适用物探方法：{kb_methods_note}",
                # 注：geo-geophys 实际处理流程固定（rtp/解析信号/倾斜角/欧拉），此处为编排侧标注
                params={"methods": ["rtp", "analytic_signal", "tilt_derivative", "euler"]},
            ))

        # geo-geochem（门控由 mineral_kb.all_key_elements 派生）
        if needs_geochem:
            services.append(ServiceRecommendation(
                service="geo-geochem", required=False,
                reason=f"化探异常（指示元素：{', '.join(key_elements[:6])}）—— 化探为线索",
                params={"elements": key_elements[:8]},
            ))

        # geo-insar
        insar_flag = priority.get("insar", False)
        services.append(ServiceRecommendation(
            service="geo-insar",
            required=insar_flag is True,
            reason="InSAR 形变监测" if insar_flag else "非必选（非活动构造区）",
            skip_reason="" if insar_flag else "该矿种/区域 InSAR 诊断性不高",
        ))

        # geo-exploration
        services.append(ServiceRecommendation(
            service="geo-exploration", required=False,
            reason="舒曼共振深部探测（非主流方法，提供补充靶点）",
        ))

        # geo-model3d
        services.append(ServiceRecommendation(
            service="geo-model3d", required=True,
            reason="三维建模（融合所有证据）—— 系统核心产出",
        ))

        # geo-drill
        services.append(ServiceRecommendation(
            service="geo-drill", required=False,
            reason="AI 布孔（需 model3d 产物）—— 可选",
        ))

        # geo-reporter
        services.append(ServiceRecommendation(
            service="geo-reporter", required=True,
            reason="综合报告（汇总所有子系统产物）",
        ))

        return services

    @staticmethod
    def _build_rationale(mineral, family, family_spec, roi_ctx, sensors, services):
        weights = family_spec.get('w', {})
        return {
            "family_determination": f"{mineral} → {family}（{family_spec.get('note', '')}）",
            "weight_summary": f"蚀变 {weights.get('alteration', 0):.2f} / 构造 {weights.get('structure', 0):.2f}"
                             f" / 形变 {weights.get('deformation', 0):.2f} / 深度一致性 {weights.get('depth_consistency', 0):.2f}",
            "roi_adjustment": f"植被 {roi_ctx.vegetation_cover} / 云覆盖 {roi_ctx.cloud_coverage}"
                             f" / 气候 {roi_ctx.climate_zone} / 构造 {roi_ctx.tectonic_setting}",
            "sensor_count": f"{len([s for s in sensors if s.required])} 必选 + {len([s for s in sensors if not s.required])} 可选",
            "depth_band": f"{family_spec.get('depth_km', [])} km",
        }

    @staticmethod
    def to_dict(rec: MineralRecommendation) -> dict:
        return {
            'mineral': rec.mineral,
            'family': rec.family,
            'family_weights': rec.family_weights,
            'depth_km_band': rec.depth_km_band,
            'sensors': [
                {'sensor': s.sensor, 'seasons': s.seasons, 'required': s.required,
                 'reason': s.reason, 'target_services': s.target_services}
                for s in rec.sensors
            ],
            'services': [
                {'service': s.service, 'required': s.required, 'reason': s.reason,
                 'params': s.params, 'skip_reason': s.skip_reason}
                for s in rec.services
            ],
            'key_elements': rec.key_elements,
            'geophysical_methods': rec.geophysical_methods,
            'rationale': rec.rationale,
        }
