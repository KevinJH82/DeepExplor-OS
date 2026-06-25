"""编排单 / 执行结果注册表（P4 backbone）。

持久化每一份编排单与其执行结果到 results/_registry/，支撑 P4 三件事：
  - 4.2 版本管理：同一 ROI 的多份编排单（含 chat 改写的新版本，parent 关联）
  - 4.3 跨 ROI 知识迁移：find_similar() 按成因族/面积/气候召回历史相似 ROI
  - 4.4 持续学习闭环：record_outcome() 落执行结果，strategy_bias() 按族聚合历史命中率

存储：append-only JSONL，读时聚合（无并发写冲突即可；单进程足够）。
  results/_registry/plans.jsonl     一行一份编排单元数据
  results/_registry/outcomes.jsonl  一行一次执行结果
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import List, Optional

from config.config import Config

_LOCK = threading.Lock()


def _registry_dir() -> str:
    d = os.path.join(Config.RESULTS_FOLDER, "_registry")
    os.makedirs(d, exist_ok=True)
    return d


def _plans_path() -> str:
    return os.path.join(_registry_dir(), "plans.jsonl")


def _outcomes_path() -> str:
    return os.path.join(_registry_dir(), "outcomes.jsonl")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append(path: str, record: dict):
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_all(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with _LOCK:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def _plan_sensors(plan: dict) -> list:
    sensors = []
    for ph in (plan.get("execution_plan") or {}).get("phases", []):
        for g in ph.get("parallel_groups", []):
            if g.get("service") == "geo-downloader":
                for t in (g.get("tasks") or []):
                    if t.get("sensor"):
                        sensors.append(t["sensor"])
    return sensors


def _plan_services(plan: dict) -> list:
    """非跳过的服务列表。"""
    svcs = []
    for ph in (plan.get("execution_plan") or {}).get("phases", []):
        for g in ph.get("parallel_groups", []):
            if not g.get("skip"):
                svcs.append(g.get("service"))
    return svcs


class PlanRegistry:
    """编排单与执行结果的持久化注册表。"""

    # ── 写入 ──────────────────────────────────────────────────
    @staticmethod
    def register_plan(plan_id: str, plan: dict, source: str = "auto",
                      parent_plan_id: Optional[str] = None) -> dict:
        """注册一份编排单（生成时或 chat 确认新版时调用）。"""
        roi = plan.get("roi") or {}
        record = {
            "plan_id": plan_id,
            "aoi_name": roi.get("aoi_name", "unnamed"),
            "bbox": roi.get("bbox"),
            "area_km2": roi.get("area_km2"),
            "climate_zone": roi.get("climate_zone"),
            "vegetation_cover": roi.get("vegetation_cover"),
            "mineral": plan.get("mineral"),
            "family": plan.get("family"),
            "sensors": _plan_sensors(plan),
            "services": _plan_services(plan),
            "source": source,                 # auto | chat
            "parent_plan_id": parent_plan_id,
            "planner_mode": (plan.get("meta") or {}).get("planner_mode"),
            "created_at": _now(),
        }
        _append(_plans_path(), record)
        # 同时把完整编排单落盘，便于版本对比/复跑
        try:
            d = os.path.join(Config.RESULTS_FOLDER, record["aoi_name"],
                             "orchestration", plan_id)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plan.json"), "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return record

    @staticmethod
    def record_outcome(plan_id: str, snapshot: dict):
        """执行结束时落结果（来自 ProgressTracker.snapshot()）。"""
        phases = snapshot.get("phases") or {}
        svc_status = {}
        for ph in phases.values():
            for svc, sv in (ph.get("services") or {}).items():
                svc_status[svc] = sv.get("status")
        record = {
            "plan_id": plan_id,
            "aoi_name": snapshot.get("aoi_name"),
            "overall_status": snapshot.get("overall_status"),
            "service_status": svc_status,
            "degradations": [d.get("service") + ":" + d.get("action", "")
                             for d in (snapshot.get("degradations") or [])],
            "quality": {s: (q.get("level") if isinstance(q, dict) else q)
                        for s, q in (snapshot.get("quality") or {}).items()},
            "recorded_at": _now(),
        }
        _append(_outcomes_path(), record)
        return record

    # ── 读取：版本管理（4.2）──────────────────────────────────
    @staticmethod
    def list_plans(aoi_name: Optional[str] = None) -> List[dict]:
        plans = _read_all(_plans_path())
        if aoi_name:
            plans = [p for p in plans if p.get("aoi_name") == aoi_name]
        return plans

    @staticmethod
    def get_plan(plan_id: str) -> Optional[dict]:
        """读回完整编排单 JSON（从落盘 plan.json）。"""
        for rec in _read_all(_plans_path()):
            if rec.get("plan_id") == plan_id:
                d = os.path.join(Config.RESULTS_FOLDER, rec.get("aoi_name", ""),
                                 "orchestration", plan_id, "plan.json")
                if os.path.exists(d):
                    try:
                        with open(d, encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        return None
        return None

    @staticmethod
    def compare(plan_id_a: str, plan_id_b: str) -> dict:
        """对比两份编排单的传感器/服务/族差异。"""
        a = PlanRegistry.get_plan(plan_id_a)
        b = PlanRegistry.get_plan(plan_id_b)
        if not a or not b:
            return {"error": "找不到编排单"}
        sa, sb = set(_plan_sensors(a)), set(_plan_sensors(b))
        va, vb = set(_plan_services(a)), set(_plan_services(b))
        return {
            "a": plan_id_a, "b": plan_id_b,
            "mineral": {"a": a.get("mineral"), "b": b.get("mineral")},
            "family": {"a": a.get("family"), "b": b.get("family")},
            "sensors": {
                "only_a": sorted(sa - sb), "only_b": sorted(sb - sa),
                "common": sorted(sa & sb),
            },
            "services": {
                "only_a": sorted(va - vb), "only_b": sorted(vb - va),
                "common": sorted(va & vb),
            },
        }

    # ── 读取：跨 ROI 知识迁移（4.3）───────────────────────────
    @staticmethod
    def find_similar(roi_ctx_dict: dict, mineral: str, family: str,
                     limit: int = 3) -> List[dict]:
        """召回历史相似 ROI（排除同名 AOI）。打分：族>面积>气候/植被。"""
        aoi = roi_ctx_dict.get("aoi_name")
        area = roi_ctx_dict.get("area_km2") or 0
        climate = roi_ctx_dict.get("climate_zone")
        veg = roi_ctx_dict.get("vegetation_cover")

        scored = []
        for p in _read_all(_plans_path()):
            if p.get("aoi_name") == aoi:
                continue
            score = 0
            reasons = []
            if family and p.get("family") == family:
                score += 3
                reasons.append(f"同成因族（{family}）")
            elif mineral and p.get("mineral") == mineral:
                score += 2
                reasons.append(f"同矿种（{mineral}）")
            pa = p.get("area_km2") or 0
            if area and pa and 0.5 <= (pa / area) <= 2.0:
                score += 2
                reasons.append("面积量级相近")
            if climate and p.get("climate_zone") == climate:
                score += 1
                reasons.append(f"同气候带（{climate}）")
            if veg and p.get("vegetation_cover") == veg:
                score += 1
                reasons.append(f"同植被覆盖（{veg}）")
            if score >= 3:
                scored.append({
                    "plan_id": p.get("plan_id"),
                    "aoi_name": p.get("aoi_name"),
                    "mineral": p.get("mineral"),
                    "family": p.get("family"),
                    "sensors": p.get("sensors"),
                    "services": p.get("services"),
                    "score": score,
                    "match_reasons": reasons,
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ── 读取：持续学习闭环（4.4）──────────────────────────────
    @staticmethod
    def strategy_bias(family: str) -> List[dict]:
        """按成因族聚合历史执行结果，产出策略偏置提示（信息性 + 轻量优先级建议）。"""
        plans = {p["plan_id"]: p for p in _read_all(_plans_path())}
        outcomes = [o for o in _read_all(_outcomes_path())
                    if plans.get(o.get("plan_id"), {}).get("family") == family]
        if not outcomes:
            return []

        n = len(outcomes)
        # 统计各服务 skip/failed 比例
        svc_bad = {}      # service -> 次数（skipped 或 failed）
        svc_total = {}
        for o in outcomes:
            for svc, st in (o.get("service_status") or {}).items():
                svc_total[svc] = svc_total.get(svc, 0) + 1
                if st in ("skipped", "failed"):
                    svc_bad[svc] = svc_bad.get(svc, 0) + 1

        hints = []
        for svc, total in svc_total.items():
            bad = svc_bad.get(svc, 0)
            if total >= 2 and bad / total >= 0.6:
                hints.append({
                    "service": svc,
                    "bias": "deprioritize",
                    "note": f"历史上 {family} 该区类型 {bad}/{total} 次 {svc} 跳过/失败，建议降低其优先级或预备降级",
                })
        # prior_only 化探频发提示
        prior_cnt = sum(1 for o in outcomes
                        if (o.get("quality") or {}).get("geo-geochem") == "prior_only")
        if prior_cnt >= 2:
            hints.append({
                "service": "geo-geochem",
                "bias": "expect_prior_only",
                "note": f"历史 {prior_cnt}/{n} 次化探为 prior_only（无实测点位），建议提示用户上传化探数据",
            })
        return hints
