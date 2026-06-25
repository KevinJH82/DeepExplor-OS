"""任务执行器（P2）。

按编排单 execution_plan.phases 依次执行：阶段内并行（ThreadPoolExecutor），
顺序阶段串行。skip 的服务跳过；geo-insar 提交后不阻塞；单服务失败仅记录隔离，
不中断后续（智能降级是 P3 范畴）。支持断点续跑（已完成/已跳过的服务不重跑）。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from config.config import Config
from core.service_client import (ServiceClient, ServiceResult,
                                 ST_COMPLETED, ST_FAILED, ST_SUBMITTED, ST_SKIPPED)
from core.progress import (ProgressTracker, EXEC_RUNNING, EXEC_COMPLETED,
                           EXEC_FAILED, EXEC_PAUSED)
from core.quality_checker import QualityChecker, Q_UNKNOWN
from core.fallback_manager import FallbackManager, ACTION_ABORT
from core.adaptive_adjuster import AdaptiveAdjuster

MAX_PARALLEL = 6


class Executor:
    """驱动一份编排单执行（P2 调度 + P3 质量评估/降级/自适应）。"""

    def __init__(self, client: Optional[ServiceClient] = None):
        self.client = client or ServiceClient()
        self._pause = threading.Event()
        self._skip_services: set = set()   # 运行期手动跳过 / 降级联动跳过
        # P3 组件
        self.quality_checker = QualityChecker()
        self.fallback_manager = FallbackManager()
        self.adjuster = AdaptiveAdjuster()
        self.quality_reports: dict = {}    # service -> QualityReport
        self._bbox = None
        self._roots = {}
        self._async_pending: dict = {}     # service -> task_id（提交后异步，如 geo-insar）
        self._insar_waited = False         # 有界等待门只执行一次

    # ── 外部控制 ──────────────────────────────────────────────
    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def skip_service(self, service: str):
        self._skip_services.add(service)

    # ── 主流程 ────────────────────────────────────────────────
    def execute(self, plan: dict, tracker: ProgressTracker):
        trace_id = plan.get("trace_id")
        roi = plan.get("roi") or {}
        kml_path = roi.get("kml_path")
        mineral = plan.get("mineral", "")
        aoi_name = roi.get("aoi_name", "unnamed")

        tracker.set_overall(EXEC_RUNNING)
        tracker.log(f"开始执行编排单 {tracker.plan_id}（{aoi_name} / {mineral}）")

        if not kml_path:
            tracker.log("编排单缺少 roi.kml_path，无法执行", "ERROR")
            tracker.set_overall(EXEC_FAILED)
            tracker.finish_stream()
            return

        # P3：质量评估/自适应所需上下文
        self._bbox = roi.get("bbox")
        self._roots = Config.upstream_roots()
        aborted_reason = None

        # ── 服务自检:执行前确保编排单涉及的下游服务都已启动,未启动则拉起 ──
        # (子服务常被手动停掉/崩溃 → 编排中途"服务不可达"失败。先统一拉齐再跑。)
        plan_services = []
        for ph in (plan.get("execution_plan") or {}).get("phases", []):
            for g in ph.get("parallel_groups", []):
                sv = g.get("service")
                if sv and sv not in plan_services:
                    plan_services.append(sv)
        if plan_services:
            try:
                from core.service_launcher import ensure_services_up
                tracker.log(f"服务自检:检查 {len(plan_services)} 个下游服务是否就绪…")
                statuses = ensure_services_up(plan_services, on_log=tracker.log)
                down = [s for s, (ok, _) in statuses.items() if ok is False]
                started = [s for s, (ok, m) in statuses.items() if ok and m != "已在运行"]
                if started:
                    tracker.log(f"服务自检:已为本次编排拉起 {', '.join(started)}")
                if down:
                    tracker.log(f"服务自检:{', '.join(down)} 未能启动,相关步骤将走失败处理/降级", "WARN")
                else:
                    tracker.log("服务自检:下游服务全部就绪")
            except Exception as e:  # 自检不应阻断编排
                tracker.log(f"服务自检异常(忽略,继续执行):{e}", "WARN")

        try:
            phases = (plan.get("execution_plan") or {}).get("phases", [])
            for ph in phases:
                pno = ph.get("phase")
                self._wait_if_paused(tracker)

                # InSAR 有界等待门：进入含 geo-model3d 的阶段前，若 InSAR 已提交未就绪，
                # 等待至就绪/超时（超时按 P3 降级，model3d 不含形变层但不中止）。
                self._await_insar_if_needed(ph, tracker)

                groups = ph.get("parallel_groups", [])
                runnable = [g for g in groups if self._should_run(g, pno, tracker)]

                # 标注跳过的服务
                for g in groups:
                    if g not in runnable:
                        svc = g.get("service")
                        cur = tracker.phases.get(pno, {}).get("services", {}).get(svc, {})
                        if cur.get("status") not in ("completed", "submitted"):
                            tracker.update_service(
                                pno, svc, ST_SKIPPED,
                                detail=g.get("skip_reason") or "已有产物/降级跳过")

                if not runnable:
                    tracker.update_phase(pno, "completed")
                    continue

                tracker.update_phase(pno, "running")
                tracker.log(f"阶段 {pno}「{ph.get('name')}」启动，{len(runnable)} 个服务")

                parallel = ph.get("parallel") or self._infer_parallel(pno)
                if parallel and len(runnable) > 1:
                    results = self._run_parallel(runnable, pno, kml_path, mineral,
                                                 aoi_name, trace_id, tracker)
                else:
                    results = self._run_sequential(runnable, pno, kml_path, mineral,
                                                    aoi_name, trace_id, tracker)

                # P3：失败降级处理（可能中止）
                aborted_reason = self._handle_failures(results, tracker)
                tracker.update_phase(pno, "completed")
                if aborted_reason:
                    break

            if aborted_reason:
                tracker.set_overall(EXEC_FAILED)
                tracker.log(f"执行中止：{aborted_reason}", "ERROR")
            else:
                degraded = bool(tracker.degradations)
                tracker.set_overall(EXEC_COMPLETED)
                tracker.log("执行结束" + ("（含降级，见降级提示）" if degraded else "（全部完成）"),
                            "WARNING" if degraded else "INFO")
        except Exception as e:
            import traceback
            tracker.log(f"执行器异常：{e}", "ERROR")
            tracker.log(traceback.format_exc(), "ERROR")
            tracker.set_overall(EXEC_FAILED)
        finally:
            tracker.finish_stream()

    # ── 阶段内执行 ────────────────────────────────────────────
    def _run_parallel(self, groups, pno, kml_path, mineral, aoi_name, trace_id, tracker):
        results = []
        workers = min(MAX_PARALLEL, len(groups))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {
                ex.submit(self._run_group, g, pno, kml_path, mineral,
                          aoi_name, trace_id, tracker): g
                for g in groups
            }
            for fut in as_completed(future_map):
                g = future_map[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = ServiceResult(g.get("service"), ST_FAILED, error=str(e))
                results.append((g, res))
        return results

    def _run_sequential(self, groups, pno, kml_path, mineral, aoi_name, trace_id, tracker):
        results = []
        for g in groups:
            self._wait_if_paused(tracker)
            res = self._run_group(g, pno, kml_path, mineral, aoi_name, trace_id, tracker)
            results.append((g, res))
        return results

    def _run_group(self, group, pno, kml_path, mineral, aoi_name, trace_id, tracker) -> ServiceResult:
        service = group.get("service")
        params = self._params_for(group, aoi_name)

        # P3：下游服务的质量驱动自适应调整
        if service in ("geo-model3d", "geo-drill"):
            adj = self.adjuster.adjust(service, self.quality_reports)
            if adj.params:
                params.update(adj.params)
            if adj.reasons:
                tracker.log(f"[自适应] {service}：{adj.summary}")

        tracker.update_service(pno, service, "running", detail="启动中",
                               log=f"→ {service} 开始")

        def on_log(msg, level="INFO"):
            tracker.log(msg, level)

        res = self.client.run(service, kml_path, mineral, params, trace_id, on_log)

        status_map = {
            ST_COMPLETED: ("completed", res.detail or "完成"),
            ST_SUBMITTED: ("submitted", res.detail or "已提交（异步）"),
            ST_FAILED: ("failed", res.error or "失败"),
            ST_SKIPPED: ("skipped", res.detail or "跳过"),
        }
        ui_status, detail = status_map.get(res.status, ("failed", res.error or "未知"))
        tracker.update_service(pno, service, ui_status, detail=detail,
                               task_id=res.task_id,
                               log=f"← {service} {ui_status}：{detail}")

        # 异步提交（geo-insar）：记下 task_id，供建模前有界等待门轮询
        if res.status == ST_SUBMITTED and res.task_id:
            self._async_pending[service] = res.task_id

        # P3：完成后质量评估（读 broker 产物指标）
        if res.status == ST_COMPLETED:
            try:
                rep = self.quality_checker.check(service, self._bbox, self._roots, trace_id)
                if rep.level != Q_UNKNOWN:
                    self.quality_reports[service] = rep
                    tracker.set_quality(service, rep.to_dict())
                    tracker.log(f"[质量] {service}：{rep.level} — {rep.note}")
            except Exception as e:
                tracker.log(f"质量评估跳过（{service}）：{e}", "WARNING")
        return res

    def _await_insar_if_needed(self, phase: dict, tracker: ProgressTracker):
        """进入含 geo-model3d 的阶段前，对已提交未就绪的 InSAR 做有界等待。"""
        if self._insar_waited:
            return
        services = [g.get("service") for g in phase.get("parallel_groups", [])]
        if "geo-model3d" not in services:
            return
        task_id = self._async_pending.get("geo-insar")
        if not task_id:
            return
        self._insar_waited = True
        timeout = getattr(Config, "INSAR_WAIT_TIMEOUT", 1800)
        tracker.log(f"[InSAR 等待门] geo-model3d 前等待 InSAR 就绪（最多 {timeout}s）...")

        def on_log(msg, level="INFO"):
            tracker.log(msg, level)

        res = self.client.wait_insar(task_id, timeout, on_log)
        # 找到 InSAR 所在阶段号（阶段一），更新其 tracker 状态
        insar_phase = next((int(p) for p, ph in tracker.phases.items()
                            if "geo-insar" in (ph.get("services") or {})), 1)
        if res.status == ST_COMPLETED:
            tracker.update_service(insar_phase, "geo-insar", "completed",
                                   detail="InSAR 就绪", log="[InSAR 等待门] 已就绪，继续三维建模")
        else:
            # 超时或失败：不中止，降级——model3d 自动按可用证据建模（不含形变层）
            tracker.update_service(insar_phase, "geo-insar", "submitted",
                                   detail=res.detail or "未就绪")
            tracker.add_degradation("geo-insar", "skip",
                                    "InSAR 未在等待窗内就绪，三维建模将不含形变证据层")
            tracker.log(f"[InSAR 等待门] {res.detail}；按降级继续（model3d 不含形变层）", "WARNING")
        self._async_pending.pop("geo-insar", None)

    def _handle_failures(self, results, tracker) -> Optional[str]:
        """P3：对失败的服务应用降级策略。返回中止原因（若需 abort），否则 None。"""
        for g, res in results:
            if not res or res.status != ST_FAILED:
                continue
            decision = self.fallback_manager.handle_failure(g.get("service"), res.error or "")
            tracker.add_degradation(decision.service, decision.action, decision.impact)
            tracker.log(f"[降级] {decision.notify}", "WARNING")
            for casc in decision.cascade_skip:
                self.skip_service(casc)
                tracker.log(f"[降级] 联动跳过 {casc}（依赖 {decision.service}）", "WARNING")
            if decision.action == ACTION_ABORT:
                return decision.notify
        return None

    # ── 辅助 ──────────────────────────────────────────────────
    def _should_run(self, group: dict, pno, tracker: ProgressTracker) -> bool:
        service = group.get("service")
        if group.get("skip"):
            return False
        if service in self._skip_services:
            return False
        # 断点续跑：tracker 里已完成/已提交的不重跑
        cur = tracker.phases.get(pno, {}).get("services", {}).get(service, {})
        if cur.get("status") in ("completed", "submitted", "skipped"):
            return False
        return True

    @staticmethod
    def _infer_parallel(pno) -> bool:
        # 阶段 1（数据获取）、阶段 2（并行处理）可并行；3/4/5 顺序
        return pno in (1, 2)

    @staticmethod
    def _params_for(group: dict, aoi_name: str) -> dict:
        """从编排单 group.tasks 提取调用参数。"""
        service = group.get("service")
        tasks = group.get("tasks") or []

        if service == "geo-downloader":
            sensors = [t.get("sensor") for t in tasks if t.get("sensor")]
            params = {"sensors": sensors or ["sentinel2"]}
            return params

        # 其余服务：用首个 task 作为参数字典
        params = dict(tasks[0]) if tasks else {}
        params.pop("reason", None)
        params.setdefault("aoi_name", aoi_name)
        return params

    def _wait_if_paused(self, tracker: ProgressTracker):
        if self._pause.is_set():
            tracker.set_overall(EXEC_PAUSED)
            tracker.log("已暂停，等待恢复...")
            while self._pause.is_set():
                self._pause.wait(timeout=1.0)
            tracker.set_overall(EXEC_RUNNING)
            tracker.log("已恢复执行")
