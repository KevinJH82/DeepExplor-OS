"""各兄弟服务的 HTTP 客户端封装（P2 执行器用）。

各服务 API 是异构的（启动序列、字段名、状态枚举都不同），因此这里按服务分派到
不同适配器，对外统一暴露 `ServiceClient.run(service, kml_path, mineral, params,
trace_id, on_log) -> ServiceResult`。

数据在服务间通过共享文件系统 + trace_id broker 衔接，本客户端只负责：
  上传 KML → 启动作业 → 轮询至完成（geo-insar 提交后不阻塞）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

import requests

from config.config import Config


# ── 归一化状态 ────────────────────────────────────────────────
ST_COMPLETED = "completed"
ST_FAILED = "failed"
ST_RUNNING = "running"
ST_SUBMITTED = "submitted"   # geo-insar 云端异步，提交后不阻塞
ST_SKIPPED = "skipped"

# 各服务原始状态 → 归一化
_DONE_WORDS = {"done", "completed", "complete", "success", "finished"}
_FAIL_WORDS = {"error", "failed", "failure", "aborted"}


def _normalize_status(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if s in _DONE_WORDS:
        return ST_COMPLETED
    if s in _FAIL_WORDS:
        return ST_FAILED
    return ST_RUNNING


@dataclass
class ServiceResult:
    """单个服务执行的归一化结果。"""
    service: str
    status: str                       # completed | failed | submitted | skipped
    task_id: Optional[str] = None
    detail: str = ""
    error: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (ST_COMPLETED, ST_SUBMITTED, ST_SKIPPED)

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "status": self.status,
            "task_id": self.task_id,
            "detail": self.detail,
            "error": self.error,
        }


# ── service 名 → base URL ─────────────────────────────────────
def _base_url(service: str) -> str:
    mapping = {
        "geo-downloader": Config.GEO_DOWNLOADER_URL,
        "geo-analyser": Config.GEO_ANALYSER_URL,
        "geo-stru": Config.GEO_STRU_URL,
        "geo-exploration": Config.GEO_EXPLORATION_URL,
        "geo-insar": Config.GEO_INSAR_URL,
        "data-colle": Config.DATA_COLLE_URL,
        "geo-model3d": Config.GEO_MODEL3D_URL,
        "geo-geophys": Config.GEO_GEOPHYS_URL,
        "geo-geochem": Config.GEO_GEOCHEM_URL,
        "geo-drill": Config.GEO_DRILL_URL,
        "geo-reporter": Config.GEO_REPORTER_URL,
        "geo-7slow": Config.GEO_7SLOW_URL,
    }
    url = mapping.get(service)
    if not url:
        raise ValueError(f"未知服务：{service}")
    return url.rstrip("/")


def _default_date_range(years: int = 2) -> tuple:
    """下载/InSAR 缺省时间范围：近 N 年。"""
    end = datetime.now()
    start = end - timedelta(days=365 * years)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


class ServiceClient:
    """统一驱动 11 个兄弟服务。线程安全（无共享可变状态，每次调用独立）。"""

    def __init__(self, poll_interval: float = 5.0, poll_timeout: float = 3600.0,
                 sync_timeout: float = 1800.0):
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.sync_timeout = sync_timeout

    # ── 对外统一入口 ──────────────────────────────────────────
    def run(self, service: str, kml_path: str, mineral: str, params: dict,
            trace_id: Optional[str] = None,
            on_log: Optional[Callable] = None) -> ServiceResult:
        params = params or {}

        def log(msg, level="INFO"):
            if on_log:
                on_log(f"[{service}] {msg}", level)

        handlers = {
            "geo-downloader": self._run_downloader,
            "data-colle": self._run_datacolle,
            "geo-analyser": self._run_synchronous,
            "geo-exploration": self._run_synchronous,
            "geo-stru": self._run_stru,
            "geo-geophys": self._run_start_status,
            "geo-geochem": self._run_start_status,
            "geo-drill": self._run_start_status,
            "geo-model3d": self._run_start_status,
            "geo-7slow": self._run_start_status,
            "geo-insar": self._run_insar_submit,
            "geo-reporter": self._run_reporter,
        }
        handler = handlers.get(service)
        if not handler:
            return ServiceResult(service, ST_FAILED, error=f"无适配器：{service}")

        try:
            return handler(service, kml_path, mineral, params, trace_id, log)
        except requests.exceptions.RequestException as e:
            log(f"网络错误：{e}", "ERROR")
            return ServiceResult(service, ST_FAILED, error=f"网络错误：{e}")
        except Exception as e:
            log(f"执行异常：{e}", "ERROR")
            return ServiceResult(service, ST_FAILED, error=str(e))

    # ── 通用轮询 ──────────────────────────────────────────────
    def _poll(self, service: str, status_url: str, extract: Callable,
              log: Callable) -> ServiceResult:
        """轮询 status_url 直到归一化状态为 completed/failed 或超时。

        extract(resp_json) -> (raw_status, detail_str)。
        """
        deadline = time.time() + self.poll_timeout
        last_detail = ""
        while time.time() < deadline:
            try:
                r = requests.get(status_url, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log(f"轮询出错（重试）：{e}", "WARNING")
                time.sleep(self.poll_interval)
                continue

            raw_status, detail = extract(data)
            norm = _normalize_status(raw_status)
            if detail and detail != last_detail:
                log(detail)
                last_detail = detail
            if norm == ST_COMPLETED:
                return ServiceResult(service, ST_COMPLETED, detail=detail or "完成")
            if norm == ST_FAILED:
                return ServiceResult(service, ST_FAILED, detail=detail,
                                     error=detail or "服务报告失败")
            time.sleep(self.poll_interval)

        return ServiceResult(service, ST_FAILED, error=f"轮询超时（>{self.poll_timeout:.0f}s）")

    # ── geo-downloader：upload-kml → run → poll /api/status ───
    def _run_downloader(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        # 1. 上传 KML（拿到服务端路径）
        remote_kml = self._upload(f"{base}/api/upload-kml", kml_path, log,
                                  path_key="path")
        # 2. 组装传感器列表 + 时间范围
        sensors = params.get("sensors")
        if not sensors:
            # 从编排单 tasks 推断：params 里可能有单个 sensor
            sensors = [params["sensor"]] if params.get("sensor") else ["sentinel2"]
        start, end = params.get("start"), params.get("end")
        if not (start and end):
            start, end = _default_date_range()
        task_body = {
            "kml": remote_kml,
            "sensor": sensors,
            "start": start,
            "end": end,
        }
        if params.get("cloud") is not None:
            task_body["cloud"] = params["cloud"]
        log(f"启动下载：{sensors}  {start}~{end}")
        r = requests.post(f"{base}/api/run", json={"task": task_body}, timeout=60)
        r.raise_for_status()
        task_id = (r.json() or {}).get("task_id")
        if not task_id:
            return ServiceResult(service, ST_FAILED, error="downloader 未返回 task_id")

        # 3. 轮询 /api/status（返回所有任务列表，按 task_id 匹配）
        def extract(data):
            for t in (data.get("tasks") or []):
                if t.get("task_id") == task_id:
                    return t.get("status"), f"下载状态：{t.get('status')}"
            return "running", ""

        res = self._poll(service, f"{base}/api/status", extract, log)
        res.task_id = task_id
        return res

    # ── data-colle：upload(file+mineral) → poll /api/status/<id> ─
    def _run_datacolle(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        with open(kml_path, "rb") as fh:
            files = {"file": (os.path.basename(kml_path), fh)}
            data = {"mineral": mineral, "auto_download": "true"}
            if params.get("buffer") is not None:
                data["buffer"] = str(params["buffer"])
            if trace_id:
                data["trace_id"] = trace_id
            log("上传 ROI + 启动在线查取...")
            r = requests.post(f"{base}/api/upload", files=files, data=data, timeout=60)
        r.raise_for_status()
        task_id = (r.json() or {}).get("task_id")
        if not task_id:
            return ServiceResult(service, ST_FAILED, error="data-colle 未返回 task_id")

        def extract(d):
            return d.get("status"), f"查取：{d.get('step', d.get('status'))}"

        res = self._poll(service, f"{base}/api/status/{task_id}", extract, log)
        res.task_id = task_id
        return res

    # ── geo-analyser / geo-exploration：同步阻塞 ──────────────
    def _run_synchronous(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        # 1. upload_roi → project_name
        proj = self._upload(f"{base}/api/upload_roi", kml_path, log,
                            path_key="project_name")
        body = {
            "project_name": proj,
            "deposit_type": mineral,
            "methods": params.get("methods", ["ratio", "pca"]),
        }
        if params.get("selected_minerals"):
            body["selected_minerals"] = params["selected_minerals"]
        if trace_id:
            body["trace_id"] = trace_id
        log(f"同步分析（project={proj}）...这可能需要数分钟")
        r = requests.post(f"{base}/api/analyze_batch", json=body, timeout=self.sync_timeout)
        r.raise_for_status()
        log("同步分析完成")
        return ServiceResult(service, ST_COMPLETED, detail="同步完成")

    # ── geo-stru：upload_area → start → poll /api/status/<id> ──
    def _run_stru(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        # 1. upload_area → file_path + resolved.project_name
        with open(kml_path, "rb") as fh:
            files = {"file": (os.path.basename(kml_path), fh)}
            log("上传研究区...")
            r = requests.post(f"{base}/api/upload_area", files=files, timeout=60)
        r.raise_for_status()
        up = r.json() or {}
        file_path = up.get("file_path")
        project_name = (up.get("resolved") or {}).get("project_name") or up.get("project_name")
        # 2. start
        body = {
            "file_path": file_path,
            "project_name": project_name,
            "use_landsat": params.get("use_landsat", True),
            "mineral_hint": mineral,
        }
        if trace_id:
            body["trace_id"] = trace_id
        log(f"启动构造解译（project={project_name}）...")
        r = requests.post(f"{base}/api/start", json=body, timeout=60)
        r.raise_for_status()
        task_id = (r.json() or {}).get("task_id")
        if not task_id:
            return ServiceResult(service, ST_FAILED, error="geo-stru 未返回 task_id")

        res = self._poll(service, f"{base}/api/status/{task_id}",
                         self._task_extract, log)
        res.task_id = task_id
        return res

    # ── geophys/geochem/drill/model3d：start(multipart) → status ─
    def _run_start_status(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        with open(kml_path, "rb") as fh:
            files = {"file": (os.path.basename(kml_path), fh)}
            data = {"mineral": mineral}
            aoi = params.get("aoi_name")
            if aoi:
                data["aoi_name"] = aoi
            if trace_id:
                data["trace_id"] = trace_id
            # 透传编排单里的标量参数（top_n / min_sep_m / res_m / ... ）
            for k, v in params.items():
                if k in ("aoi_name",) or v is None:
                    continue
                if isinstance(v, (str, int, float, bool)):
                    data[k] = str(v)
            log(f"启动 {service}（参数：{ {k: data[k] for k in data if k != 'trace_id'} }）...")
            r = requests.post(f"{base}/api/start", files=files, data=data, timeout=60)
        r.raise_for_status()
        body = r.json() or {}
        task_id = body.get("task_id")
        if not task_id:
            return ServiceResult(service, ST_FAILED,
                                 error=f"{service} 未返回 task_id：{body}")

        res = self._poll(service, f"{base}/api/status/{task_id}",
                         self._task_extract, log)
        res.task_id = task_id
        return res

    # ── geo-insar：inspect → run，提交后不阻塞 ────────────────
    def _run_insar_submit(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        # 1. inspect → kml_path（服务端）+ backend_hint
        with open(kml_path, "rb") as fh:
            files = {"file": (os.path.basename(kml_path), fh)}
            log("InSAR AOI 检查...")
            r = requests.post(f"{base}/api/aoi/inspect", files=files, timeout=60)
        r.raise_for_status()
        ins = r.json() or {}
        remote_kml = ins.get("kml_path")
        backend = params.get("backend") or ins.get("backend_hint") or "INSAR_ISCE_BURST"
        start, end = params.get("start"), params.get("end")
        if not (start and end):
            start, end = _default_date_range()
        body = {
            "kml_path": remote_kml,
            "start": start,
            "end": end,
            "backend": backend,
            "polarization": params.get("polarization", "VV"),
            "pair": params.get("pair", Config.INSAR_PAIR_STRATEGY),
            "max_temporal_baseline": params.get(
                "max_temporal_baseline", Config.INSAR_MAX_TEMPORAL_BASELINE_DAYS),
            "max_perp_baseline": params.get(
                "max_perp_baseline", Config.INSAR_MAX_PERP_BASELINE_M),
        }
        log(f"提交 InSAR 云端处理（backend={backend}, pair={body['pair']}）—— 提交后不阻塞")
        r = requests.post(f"{base}/api/run", json=body, timeout=60)
        r.raise_for_status()
        task_id = (r.json() or {}).get("task_id")
        # 云端处理耗时数小时，按方案提交后即返回，产物到位由 model3d broker 自取
        return ServiceResult(service, ST_SUBMITTED, task_id=task_id,
                             detail="已提交云端处理（异步，不阻塞后续阶段）")

    # ── geo-insar：建模前的有界等待（轮询 HyP3 任务至就绪/超时）──
    def wait_insar(self, task_id, timeout: float, on_log=None) -> ServiceResult:
        """轮询 geo-insar 任务直到就绪/失败/超时。

        超时返回 status=submitted（云端可能仍在跑，不算失败，交由 model3d 降级处理）。
        """
        base = _base_url("geo-insar")
        url = f"{base}/api/tasks/{task_id}"

        def log(msg, level="INFO"):
            if on_log:
                on_log(f"[geo-insar] {msg}", level)

        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                data = r.json() or {}
            except Exception as e:
                log(f"等待轮询出错（重试）：{e}", "WARNING")
                time.sleep(self.poll_interval)
                continue

            # 任务态：优先看顶层 status；否则看 jobs 全部就绪
            raw = data.get("status") or (data.get("task") or {}).get("status")
            jobs = data.get("jobs") or (data.get("task") or {}).get("jobs") or []
            if not raw and jobs:
                js = [(_normalize_status(j.get("status"))) for j in jobs]
                if js and all(s == ST_COMPLETED for s in js):
                    raw = "done"
                elif any(s == ST_FAILED for s in js):
                    raw = "error"
            norm = _normalize_status(raw)
            detail = f"InSAR 状态：{raw or 'running'}（jobs={len(jobs)}）"
            if detail != last:
                log(detail)
                last = detail
            if norm == ST_COMPLETED:
                return ServiceResult("geo-insar", ST_COMPLETED, task_id=task_id, detail="InSAR 就绪")
            if norm == ST_FAILED:
                return ServiceResult("geo-insar", ST_FAILED, task_id=task_id,
                                     detail=detail, error="InSAR 处理失败")
            time.sleep(self.poll_interval)

        return ServiceResult("geo-insar", ST_SUBMITTED, task_id=task_id,
                             detail=f"等待超时（>{timeout:.0f}s），云端可能仍在处理")

    # ── geo-reporter：upload-kml → SSE run → 完成 ──────────────
    def _run_reporter(self, service, kml_path, mineral, params, trace_id, log):
        base = _base_url(service)
        # 1. upload-kml → task_id
        with open(kml_path, "rb") as fh:
            files = {"file": (os.path.basename(kml_path), fh)}
            log("上传 KML 到报告服务...")
            r = requests.post(f"{base}/api/upload-kml", files=files, timeout=60)
        r.raise_for_status()
        task_id = (r.json() or {}).get("task_id")
        if not task_id:
            return ServiceResult(service, ST_FAILED, error="reporter 未返回 task_id")

        # 2. 触发 SSE 生成（GET /api/run/<id>?mineral=），消费流到结束
        log("生成 GB/T 9704 报告（SSE 流）...")
        try:
            with requests.get(f"{base}/api/run/{task_id}", params={"mineral": mineral},
                              stream=True, timeout=self.sync_timeout) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(raw[5:].strip())
                    except Exception:
                        continue
                    if ev.get("message"):
                        log(f"报告：{ev['message']}")
                    if ev.get("error"):
                        return ServiceResult(service, ST_FAILED, task_id=task_id,
                                             error=ev["error"])
        except Exception as e:
            log(f"SSE 流中断，回退轮询状态：{e}", "WARNING")

        # 3. 确认最终状态
        try:
            r = requests.get(f"{base}/api/status/{task_id}", timeout=30)
            data = r.json() or {}
            if _normalize_status(data.get("status")) == ST_COMPLETED or data.get("has_report"):
                return ServiceResult(service, ST_COMPLETED, task_id=task_id, detail="报告已生成")
        except Exception:
            pass
        return ServiceResult(service, ST_COMPLETED, task_id=task_id,
                             detail="报告流程结束")

    # ── helpers ──────────────────────────────────────────────
    @staticmethod
    def _task_extract(data):
        """适配 {success, task:{status, progress, ...}} 形态。"""
        task = data.get("task") or {}
        prog = task.get("progress")
        detail = f"进度 {prog}%" if prog is not None else ""
        return task.get("status"), detail

    def _upload(self, url, kml_path, log, path_key):
        with open(kml_path, "rb") as fh:
            files = {"file": (os.path.basename(kml_path), fh)}
            r = requests.post(url, files=files, timeout=60)
        r.raise_for_status()
        body = r.json() or {}
        val = body.get(path_key)
        if not val:
            raise RuntimeError(f"上传未返回 {path_key}：{body}")
        return val
