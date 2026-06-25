"""执行进度跟踪（P2）。

ProgressTracker 持有一次编排执行的实时状态，同时服务两条通道：
  - snapshot()    → 给 GET /api/execute/<id>/status 轮询
  - sse_events()  → 给 GET /api/execute/<id>/stream（SSE 实时日志，带心跳）
并把状态持久化到 results/<aoi>/orchestration/<plan_id>/state.json 以支持断点续跑。
"""

from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime
from typing import Optional

from config.config import Config

# 执行整体状态
EXEC_PENDING = "pending"
EXEC_RUNNING = "running"
EXEC_COMPLETED = "completed"
EXEC_FAILED = "failed"
EXEC_PAUSED = "paused"

_SENTINEL = object()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ProgressTracker:
    """线程安全的执行态快照 + SSE 事件队列 + 持久化。"""

    def __init__(self, plan_id: str, plan: dict):
        self.plan_id = plan_id
        self.plan = plan
        self.aoi_name = (plan.get("roi") or {}).get("aoi_name", "unnamed")
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue()
        self.overall_status = EXEC_PENDING
        self.started_at = _now()
        self.logs: list = []          # 全局日志（最多 500 条）
        self.phases: dict = {}        # {phase_no: {name, status, services: {svc: {...}}}}
        self.quality: dict = {}       # service -> QualityReport.to_dict()（P3）
        self.degradations: list = []  # [{service, action, impact}]（P3）

        # 从编排单初始化骨架
        for ph in (plan.get("execution_plan") or {}).get("phases", []):
            pno = ph.get("phase")
            svc_map = {}
            for g in ph.get("parallel_groups", []):
                svc = g.get("service")
                svc_map[svc] = {
                    "status": "skipped" if g.get("skip") else "pending",
                    "detail": g.get("skip_reason", "") if g.get("skip") else "",
                    "task_id": None,
                    "required": g.get("required", True),
                    "started": None,
                    "ended": None,
                }
            self.phases[pno] = {
                "name": ph.get("name", f"阶段{pno}"),
                "status": "pending",
                "services": svc_map,
            }

    # ── 状态更新 ──────────────────────────────────────────────
    def set_overall(self, status: str):
        with self._lock:
            self.overall_status = status
        self._emit({"type": "overall", "status": status})
        self.persist()

    def update_phase(self, phase_no, status: str):
        with self._lock:
            if phase_no in self.phases:
                self.phases[phase_no]["status"] = status
        self._emit({"type": "phase", "phase": phase_no, "status": status})
        self.persist()

    def update_service(self, phase_no, service: str, status: str,
                       detail: str = "", task_id: Optional[str] = None,
                       log: Optional[str] = None):
        with self._lock:
            ph = self.phases.get(phase_no)
            if ph and service in ph["services"]:
                sv = ph["services"][service]
                sv["status"] = status
                if detail:
                    sv["detail"] = detail
                if task_id:
                    sv["task_id"] = task_id
                if status == "running" and not sv["started"]:
                    sv["started"] = _now()
                if status in ("completed", "failed", "submitted", "skipped"):
                    sv["ended"] = _now()
        self._emit({"type": "service", "phase": phase_no, "service": service,
                    "status": status, "detail": detail})
        if log:
            self.log(log)
        self.persist()

    def set_quality(self, service: str, report: dict):
        with self._lock:
            self.quality[service] = report
        self._emit({"type": "quality", "service": service, "report": report})
        self.persist()

    def add_degradation(self, service: str, action: str, impact: str):
        item = {"service": service, "action": action, "impact": impact}
        with self._lock:
            self.degradations.append(item)
        self._emit({"type": "degradation", **item})
        self.persist()

    def log(self, message: str, level: str = "INFO"):
        line = f"[{_now()}] [{level}] {message}"
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]
        self._emit({"type": "log", "line": line})

    # ── 轮询快照 ──────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "plan_id": self.plan_id,
                "aoi_name": self.aoi_name,
                "overall_status": self.overall_status,
                "started_at": self.started_at,
                "phases": json.loads(json.dumps(self.phases, ensure_ascii=False)),
                "quality": dict(self.quality),
                "degradations": list(self.degradations),
                "logs": self.logs[-80:],
            }

    # ── SSE 事件流 ────────────────────────────────────────────
    def _emit(self, event: dict):
        try:
            self._queue.put_nowait(event)
        except Exception:
            pass

    def finish_stream(self):
        """通知 SSE 生成器收尾。"""
        self._queue.put(_SENTINEL)

    def sse_events(self, keepalive: float = 8.0):
        """SSE 生成器：产出 `data: {json}\\n\\n`，空闲时发心跳防超时。

        参照 geo-reporter 的 pump_keepalive 模式。
        """
        # 先把当前快照推一份，便于前端中途接入
        yield f"data: {json.dumps({'type': 'snapshot', 'snapshot': self.snapshot()}, ensure_ascii=False)}\n\n"
        while True:
            try:
                ev = self._queue.get(timeout=keepalive)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if ev is _SENTINEL:
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    # ── 持久化 / 恢复 ─────────────────────────────────────────
    def _state_dir(self) -> str:
        return os.path.join(Config.RESULTS_FOLDER, self.aoi_name,
                            "orchestration", self.plan_id)

    def _state_path(self) -> str:
        return os.path.join(self._state_dir(), "state.json")

    def persist(self):
        try:
            os.makedirs(self._state_dir(), exist_ok=True)
            with self._lock:
                state = {
                    "plan_id": self.plan_id,
                    "aoi_name": self.aoi_name,
                    "overall_status": self.overall_status,
                    "started_at": self.started_at,
                    "phases": self.phases,
                    "quality": self.quality,
                    "degradations": self.degradations,
                    "plan": self.plan,
                }
            tmp = self._state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_path())
        except Exception:
            pass  # 持久化失败不影响主流程

    @classmethod
    def load(cls, plan_id: str, aoi_name: str) -> Optional["ProgressTracker"]:
        """从 state.json 恢复（重启续跑用）。找不到返回 None。"""
        path = os.path.join(Config.RESULTS_FOLDER, aoi_name,
                            "orchestration", plan_id, "state.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return None
        tracker = cls(plan_id, state.get("plan", {}))
        tracker.overall_status = state.get("overall_status", EXEC_PENDING)
        tracker.started_at = state.get("started_at", _now())
        tracker.quality = state.get("quality", {}) or {}
        tracker.degradations = state.get("degradations", []) or []
        # 恢复各阶段/服务状态
        for pno, ph in (state.get("phases") or {}).items():
            try:
                key = int(pno)
            except (ValueError, TypeError):
                key = pno
            if key in tracker.phases:
                tracker.phases[key]["status"] = ph.get("status", "pending")
                for svc, sv in (ph.get("services") or {}).items():
                    if svc in tracker.phases[key]["services"]:
                        tracker.phases[key]["services"][svc].update(sv)
        return tracker
