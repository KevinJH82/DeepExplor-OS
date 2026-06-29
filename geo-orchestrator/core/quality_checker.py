"""中间产物质量评估（P3）。

服务完成后，读取该服务的 broker 产物（model_stats / structural_stats / results），
产出 QualityReport：质量等级 + 关键指标 + 标志位（供降级/自适应使用）。

指标来源（实读源码确认）：
  - geo-stru     : structural_broker → structural_stats.n_lineaments / lineament_density_mean
  - geo-geophys  : geophys_broker    → model_stats.euler.n_points / mean_confidence
  - geo-geochem  : geochem_broker    → model_stats.status（"measured" vs prior_only）
  - geo-model3d  : model3d_broker    → model_stats.uncertainty_stats.mean / n_targets
  - geo-analyser : analyser_broker   → results[].anomaly_ratio（取最大）
  - geo-exploration: exploration_broker → prospecting_targets 数量
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# 质量等级
Q_GOOD = "good"
Q_WEAK = "weak"
Q_POOR = "poor"
Q_PRIOR_ONLY = "prior_only"
Q_UNKNOWN = "unknown"


@dataclass
class QualityReport:
    service: str
    level: str = Q_UNKNOWN
    metrics: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)   # weak_structure / prior_only / no_euler / high_uncertainty
    note: str = ""

    def to_dict(self) -> dict:
        return {"service": self.service, "level": self.level,
                "metrics": self.metrics, "flags": self.flags, "note": self.note}


def _ensure_path():
    for _repo in (os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                  "/opt/deepexplor-services"):
        if _repo not in sys.path:
            sys.path.insert(0, _repo)


def _first_match(module_name, func_name, bbox, root, trace_id=None):
    """调用 commons broker 的 find_*_for_bbox，返回首个匹配（无则 None）。"""
    _ensure_path()
    try:
        mod = __import__(module_name, fromlist=[func_name])
        func = getattr(mod, func_name)
        try:
            matches = func(bbox, root, trace_id=trace_id)
        except TypeError:
            matches = func(bbox, root)
        return matches[0] if matches else None
    except Exception:
        return None


class QualityChecker:
    """逐服务质量评估。所有评估容错：读不到指标 → level=unknown，不阻断主流程。"""

    # broker 映射：service → (module, func, root_key)
    _BROKERS = {
        "geo-analyser": ("commons.analyser_broker", "find_alteration_for_bbox", "analyser"),
        "geo-stru": ("commons.structural_broker", "find_structural_for_bbox", "stru"),
        "geo-stru-insar-fusion": ("commons.insar_fusion_broker", "find_insar_fusion_for_bbox", "stru"),
        "geo-geophys": ("commons.geophys_broker", "find_geophys_for_bbox", "geophys"),
        "geo-geochem": ("commons.geochem_broker", "find_geochem_for_bbox", "geochem"),
        "geo-model3d": ("commons.model3d_broker", "find_model3d_for_bbox", "model3d"),
        "geo-exploration": ("commons.exploration_broker", "find_exploration_for_bbox", "exploration"),
    }

    def check(self, service: str, bbox, roots: dict, trace_id=None) -> QualityReport:
        spec = self._BROKERS.get(service)
        if not spec:
            return QualityReport(service, Q_UNKNOWN, note="无质量评估规则")
        module, func, root_key = spec
        entry = _first_match(module, func, bbox, roots.get(root_key, ""), trace_id)
        if entry is None:
            return QualityReport(service, Q_UNKNOWN, note="未发现产物，无法评估质量")

        handler = getattr(self, f"_check_{service.replace('-', '_')}", None)
        try:
            return handler(entry) if handler else QualityReport(service, Q_UNKNOWN)
        except Exception as e:
            return QualityReport(service, Q_UNKNOWN, note=f"评估异常：{e}")

    # ── 各服务评估规则 ────────────────────────────────────────
    def _check_geo_analyser(self, entry) -> QualityReport:
        ratios = []
        for r in (entry.get("results") or []):
            v = r.get("anomaly_ratio")
            if isinstance(v, (int, float)):
                ratios.append(float(v))
        if not ratios:
            return QualityReport("geo-analyser", Q_UNKNOWN, note="无 anomaly_ratio")
        mx = max(ratios)
        # anomaly_ratio 存的是百分比（×100）；<1% 视为异常占比过低
        level = Q_POOR if mx < 1.0 else (Q_WEAK if mx < 3.0 else Q_GOOD)
        return QualityReport("geo-analyser", level,
                             metrics={"max_anomaly_ratio_pct": round(mx, 2)},
                             flags={"poor_alteration": level == Q_POOR},
                             note=f"最大蚀变异常占比 {mx:.2f}%")

    def _check_geo_stru(self, entry) -> QualityReport:
        ss = entry.get("structural_stats") or {}
        n = ss.get("n_lineaments")
        density = ss.get("lineament_density_mean")
        if n is None:
            return QualityReport("geo-stru", Q_UNKNOWN, note="无 n_lineaments")
        weak = n < 5
        return QualityReport("geo-stru", Q_WEAK if weak else Q_GOOD,
                             metrics={"n_lineaments": n, "density_mean": density},
                             flags={"weak_structure": weak},
                             note=f"线性体 {n} 条" + ("（不足，构造证据弱）" if weak else ""))

    def _check_geo_stru_insar_fusion(self, entry) -> QualityReport:
        fs = entry.get("fusion_stats") or {}
        sq = fs.get("signal_quality")
        n_active = fs.get("n_active_consistent_lineaments")
        n_clusters = fs.get("n_subsidence_clusters")
        if sq is None and n_active is None:
            return QualityReport("geo-stru-insar-fusion", Q_UNKNOWN, note="无 fusion_stats")
        metrics = {"signal_quality": sq, "n_active_lineaments": n_active,
                   "n_subsidence_clusters": n_clusters}
        if sq == "insufficient":
            return QualityReport("geo-stru-insar-fusion", Q_POOR, metrics=metrics,
                                 flags={"insufficient_temporal": True},
                                 note="时序覆盖不足，形变信号不可靠")
        if (n_active or 0) < 1:
            return QualityReport("geo-stru-insar-fusion", Q_WEAK, metrics=metrics,
                                 flags={"no_active_lineaments": True},
                                 note="无活动一致线性体，活动构造证据弱")
        has_2d = isinstance(sq, str) and sq.endswith("2d")
        return QualityReport("geo-stru-insar-fusion", Q_GOOD, metrics=metrics,
                             flags={"has_2d_decomposition": has_2d,
                                    "subsidence_detected": (n_clusters or 0) > 0},
                             note=f"活动线性体 {n_active} 条，沉降区 {n_clusters} 个，信号质量 {sq}")

    def _check_geo_geophys(self, entry) -> QualityReport:
        euler = (entry.get("model_stats") or {}).get("euler") or {}
        n = euler.get("n_points")
        conf = euler.get("mean_confidence")
        if n is None:
            return QualityReport("geo-geophys", Q_UNKNOWN, note="无欧拉解")
        no_euler = n < 3
        return QualityReport("geo-geophys", Q_WEAK if no_euler else Q_GOOD,
                             metrics={"n_euler": n, "mean_confidence": conf},
                             flags={"no_euler": no_euler},
                             note=f"欧拉深度解 {n} 个" + ("（磁源约束不足）" if no_euler else ""))

    def _check_geo_geochem(self, entry) -> QualityReport:
        status = (entry.get("model_stats") or {}).get("status")
        prior_only = status != "measured"
        if prior_only:
            return QualityReport("geo-geochem", Q_PRIOR_ONLY,
                                 metrics={"status": status},
                                 flags={"prior_only": True},
                                 note="无实测点位，化探降级为阈值先验")
        return QualityReport("geo-geochem", Q_GOOD, metrics={"status": status},
                             note="有实测点位")

    def _check_geo_model3d(self, entry) -> QualityReport:
        ms = entry.get("model_stats") or {}
        unc = (ms.get("uncertainty_stats") or {}).get("mean")
        n_targets = ms.get("n_targets")
        flags = {}
        level = Q_GOOD
        notes = []
        if isinstance(unc, (int, float)):
            if unc > 0.7:
                level = Q_POOR
                flags["high_uncertainty"] = True
                notes.append(f"不确定性均值 {unc:.2f} 偏高（证据不足）")
            elif unc > 0.5:
                level = Q_WEAK
                notes.append(f"不确定性均值 {unc:.2f}")
        if isinstance(n_targets, int) and n_targets < 5:
            flags["few_targets"] = True
            notes.append(f"靶点仅 {n_targets} 个")
            if level == Q_GOOD:
                level = Q_WEAK
        return QualityReport("geo-model3d", level,
                             metrics={"uncertainty_mean": unc, "n_targets": n_targets},
                             flags=flags, note="；".join(notes) or "建模质量正常")

    def _check_geo_exploration(self, entry) -> QualityReport:
        targets = entry.get("prospecting_targets") or []
        n = len(targets)
        return QualityReport("geo-exploration", Q_GOOD if n else Q_WEAK,
                             metrics={"n_targets": n},
                             note=f"深部靶点 {n} 个")
