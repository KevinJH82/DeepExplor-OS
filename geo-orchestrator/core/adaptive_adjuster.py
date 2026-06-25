"""基于上游质量的下游参数自适应调整（P3）。

⚠️ 现实约束（实读 geo-model3d 源码确认）：
  geo-model3d 的证据层权重按成因族在 knowledge.py 中硬编码，**API 不支持权重覆盖**；
  且 model3d 会自动检测缺失/弱证据层（缺失 → 权重重归一化；构造弱 → 自动 +0.10
  不确定性）。因此"降低 alteration 权重"这类调整无法经 API 生效，也无必要。

真正可经 API 生效的自适应（实测可调参数）：
  - geo-drill.explore_weight ：model3d 不确定性高 → 调高（多布信息孔）
  - geo-drill.top_n          ：model3d 靶点少 → 相应减少
  - geo-model3d.fusion_method/grid：可调，但无质量驱动的明确依据，默认不动

每个调整都带 reason，写入执行日志供审查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from core.quality_checker import QualityReport, Q_POOR, Q_WEAK, Q_PRIOR_ONLY


@dataclass
class Adjustment:
    service: str
    params: dict = field(default_factory=dict)   # 要覆盖/合并到下游 group.tasks[0] 的参数
    reasons: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.params:
            return "无需调整"
        kv = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{kv}（{'；'.join(self.reasons)}）"


class AdaptiveAdjuster:
    """根据已收集的质量报告，调整下游服务参数。"""

    def adjust_model3d_params(self, reports: Dict[str, QualityReport]) -> Adjustment:
        """model3d 权重不可经 API 覆盖；仅记录将自动发生的降级，不改参数。"""
        adj = Adjustment("geo-model3d")
        degraded = []
        for svc in ("geo-stru", "geo-geochem", "geo-geophys", "geo-insar", "geo-exploration"):
            rep = reports.get(svc)
            if rep and rep.level in (Q_WEAK, Q_POOR, Q_PRIOR_ONLY):
                degraded.append(f"{svc}={rep.level}")
        if degraded:
            adj.reasons.append(
                "上游证据偏弱（" + ", ".join(degraded) +
                "）；model3d 将自动按可用证据层重归一化权重并提高不确定性（API 不支持权重覆盖，无需传参）")
        return adj

    def adjust_drill_params(self, reports: Dict[str, QualityReport]) -> Adjustment:
        """根据 model3d 质量调整钻探策略（真实可调）。"""
        adj = Adjustment("geo-drill")
        m3d = reports.get("geo-model3d")
        if not m3d:
            return adj

        unc = (m3d.metrics or {}).get("uncertainty_mean")
        n_targets = (m3d.metrics or {}).get("n_targets")

        # 不确定性高 → 提高 explore_weight（多布信息孔，价值=有利度+ew×不确定性）
        if isinstance(unc, (int, float)) and unc > 0.6:
            adj.params["explore_weight"] = 0.6
            adj.reasons.append(f"model3d 不确定性均值 {unc:.2f} 偏高 → explore_weight=0.6（多布信息孔）")
        elif isinstance(unc, (int, float)) and unc > 0.45:
            adj.params["explore_weight"] = 0.45
            adj.reasons.append(f"model3d 不确定性均值 {unc:.2f} → explore_weight=0.45")

        # 靶点少 → 减少 top_n，避免在贫信息区强行布孔
        if isinstance(n_targets, int) and 0 < n_targets < 10:
            adj.params["top_n"] = max(3, n_targets)
            adj.reasons.append(f"model3d 仅 {n_targets} 个靶点 → drill top_n={adj.params['top_n']}")

        return adj

    def adjust(self, service: str, reports: Dict[str, QualityReport]) -> Adjustment:
        if service == "geo-model3d":
            return self.adjust_model3d_params(reports)
        if service == "geo-drill":
            return self.adjust_drill_params(reports)
        return Adjustment(service)
