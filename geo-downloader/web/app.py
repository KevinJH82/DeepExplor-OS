"""
geo-downloader Web UI — Flask 后端
提供配置读写、多任务下载管理、实时日志流(SSE)等API。

启动方式：
  cd geo-downloader
  pip install flask
  python3 web/app.py
  # → http://localhost:8080
"""

import os
import sys
import time
import uuid
import threading
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Dict, List

import yaml
from flask import Flask, Response, abort, jsonify, render_template, request

# ── 路径设置 ──────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent          # geo-downloader/
CONFIG_PATH = ROOT / "config" / "credentials.yaml"
SCHEMA_PATH = ROOT / "config" / "schema.yaml"
MAIN_PY = ROOT / "main.py"
UPLOAD_DIR = ROOT / "uploads" / "kml"        # 上传的 KML 存放目录
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = ROOT / "logs" / "tasks"            # 每任务日志落盘目录
LOG_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

# 可直连的域名不走代理（Copernicus/DLR 等欧洲服务）
_NO_PROXY_DOMAINS = ",".join([
    "dataspace.copernicus.eu",     # Sentinel-2 (catalogue/identity/zipper)
    "eoweb.dlr.de",                # EnMAP / DESIS
    "download.geoservice.dlr.de",  # DLR 下载
    "earthdata.nasa.gov",          # NASA Earthdata (CMR搜索/认证/下载)
    "usgs.gov",                    # USGS (Landsat EarthExplorer/ERS)
    "jpl.nasa.gov",                # JPL (AVIRIS-NG)
])
_existing_no_proxy = os.environ.get("no_proxy", "")
os.environ["no_proxy"] = ",".join(filter(None, [_existing_no_proxy, _NO_PROXY_DOMAINS]))

app = Flask(__name__, template_folder="templates")
# ── 内部鉴权:拒绝绕过 BFF 的直连(PORTAL_INTERNAL_KEY 配置后生效) ──
try:
    import sys as _ia_sys
    if '/opt/deepexplor-services' not in _ia_sys.path:
        _ia_sys.path.insert(0, '/opt/deepexplor-services')
    from commons.internal_auth import init_internal_auth as _init_internal_auth
    _init_internal_auth(app)
except Exception as _ia_e:
    print(f'[internal_auth] 跳过接入: {_ia_e}')
app.config["JSON_ENSURE_ASCII"] = False


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "接口不存在", "detail": str(e)}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "服务器内部错误", "detail": str(e)}), 500


# ── 多任务注册表 ─────────────────────────────────────────────

MAX_DOWNLOAD     = 3    # 最大并发下载任务
MAX_PACKAGE      = 2    # 最大并发打包任务
GC_SECONDS       = 86400  # 已结束任务保留 24 小时（短期 GC）
DAILY_GC_KEEP_DAYS = 7  # 每日 0 点 GC：已结束任务最多保留 7 天


@dataclass
class TaskEntry:
    task_id:     str
    task_type:   str                                    # 'download' | 'package'
    status:      str = "running"                        # 'running' | 'polling' | 'done' | 'error' | 'stopped'
    label:       str = ""
    kml:         str = ""
    output_dir:  str = ""
    sensors:     list = field(default_factory=list)
    max_items:   int = 5
    start_date:  str = ""
    end_date:    str = ""
    cloud:       int = 100
    delivery_dir: str = ""
    pid:         Optional[int] = None
    proc:        Optional[subprocess.Popen] = None
    returncode:  Optional[int] = None
    log_buf:     list = field(default_factory=list)     # None = 结束标记
    log_lock:    threading.Lock = field(default_factory=threading.Lock)
    log_event:   threading.Event = field(default_factory=threading.Event)
    log_file:    Optional[Any] = field(default=None, repr=False)  # 文件 sink,Flask 重启后可回查
    created_at:  float = 0.0
    finished_at: Optional[float] = None
    argv:        list = field(default_factory=list)
    pending_async: list = field(default_factory=list)  # ["prisma","enmap"] 异步 sensor 待 daemon 接管
    progress:    dict = field(default_factory=dict)    # {sensor: {phase, target, done, ...}} 由 __PROGRESS_EVENT__ 累积
    validation_status: str = ""                        # "" | ok | repairing | needs_attention | waiting_async
    validation_report: dict = field(default_factory=dict)  # DeliveryCheckReport.to_dict()(仅当前份)
    repair_attempts:   dict = field(default_factory=dict)  # {"package": n, "ftps:enmap": n} 限次计数
    last_checked_at:   Optional[float] = None

    def __post_init__(self):
        if self.task_id and self.log_file is None:
            try:
                fh = open(LOG_DIR / f"{self.task_id}.log", "a", encoding="utf-8", buffering=1)
                fh.write(f"# === session opened at {datetime.now().isoformat(timespec='seconds')} ===\n")
                self.log_file = fh
            except Exception:
                self.log_file = None


_tasks: Dict[str, TaskEntry] = {}
_tasks_lock = threading.Lock()
_config_lock = threading.Lock()    # 保护 credentials.yaml 读写

# 任务持久化文件：Flask 重启后恢复任务列表
_TASKS_PERSIST_FILE = ROOT / ".geo_tasks_persist.json"


def _int_or_default(value, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── 应用内消息系统 ───────────────────────────────────────────

@dataclass
class Notification:
    id:         str                          # uuid hex 短
    task_id:    str = ""                     # 关联 task,空字符串表示系统级消息
    level:      str = "info"                 # "info" | "success" | "warning" | "error"
    title:      str = ""
    body:       str = ""
    created_at: float = 0.0
    read:       bool = False


_NOTIFY_MAX_KEEP   = 500        # 最多保留条数,溢出按 created_at 驱逐
_NOTIFY_KEEP_DAYS  = 30         # 也清掉超过 N 天的(_daily_gc 触发)
_notifications:      Dict[str, Notification] = {}
_notifications_buf:  list = []                          # 顺序追加,供 SSE 增量读
_notifications_lock = threading.Lock()
_notifications_event = threading.Event()                # SSE 等待新通知
_NOTIFICATIONS_PERSIST_FILE = ROOT / ".geo_notifications_persist.json"


def _persist_tasks():
    """将当前任务列表写入磁盘（仅保存元数据，不含日志内容）。"""
    import json as _json
    with _tasks_lock:
        data = []
        for t in _tasks.values():
            data.append({
                "task_id":      t.task_id,
                "task_type":    t.task_type,
                "status":       t.status,
                "label":        t.label,
                "kml":          t.kml,
                "output_dir":   t.output_dir,
                "sensors":      t.sensors,
                "max_items":    t.max_items,
                "start_date":   t.start_date,
                "end_date":     t.end_date,
                "cloud":        t.cloud,
                "delivery_dir": t.delivery_dir,
                "pid":          t.pid,
                "argv":         t.argv,
                "returncode":   t.returncode,
                "created_at":   t.created_at,
                "finished_at":  t.finished_at,
                "pending_async": list(t.pending_async or []),
                "progress":     dict(t.progress or {}),
                "validation_status": t.validation_status or "",
                "validation_report": dict(t.validation_report or {}),
                "repair_attempts":   dict(t.repair_attempts or {}),
                "last_checked_at":   t.last_checked_at,
            })
    try:
        _TASKS_PERSIST_FILE.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _restore_tasks():
    """Flask 启动时读取持久化文件，恢复任务列表。
    - 进程仍存活（running）：标记为 running，启动 pid 监控线程跟踪结束
    - 进程已退出：根据退出码标记 done/error，或保持 stopped
    """
    import json as _json
    if not _TASKS_PERSIST_FILE.exists():
        return
    try:
        data = _json.loads(_TASKS_PERSIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return

    for d in data:
        task_id = d.get("task_id")
        if not task_id:
            continue
        pid = d.get("pid")
        saved_status = d.get("status", "done")

        # 判断进程是否仍存活
        alive = False
        if pid and saved_status == "running":
            try:
                os.kill(pid, 0)   # 不发信号，只探测进程是否存在
                alive = True
            except OSError:
                pass

        entry = TaskEntry(
            task_id=task_id,
            task_type=d.get("task_type", "download"),
            label=d.get("label", ""),
            kml=d.get("kml", ""),
            output_dir=d.get("output_dir", ""),
            sensors=d.get("sensors", []),
            max_items=d.get("max_items", 5),
            start_date=d.get("start_date", "") or "",
            end_date=d.get("end_date", "") or "",
            cloud=_int_or_default(d.get("cloud"), 100),
            delivery_dir=d.get("delivery_dir", "") or "",
            pid=pid,
            argv=d.get("argv", []),
            returncode=d.get("returncode"),
            created_at=d.get("created_at", time.time()),
            finished_at=d.get("finished_at"),
            pending_async=list(d.get("pending_async") or []),
            progress=dict(d.get("progress") or {}),
            validation_status=d.get("validation_status", "") or "",
            validation_report=dict(d.get("validation_report") or {}),
            repair_attempts=dict(d.get("repair_attempts") or {}),
            last_checked_at=d.get("last_checked_at"),
        )

        if alive:
            entry.status = "running"
            entry.finished_at = None
            _log_put(entry, "[恢复] Flask 重启后恢复任务，子进程仍在运行（日志不可重播）")
            _log_put(entry, f"[恢复] 进程 PID={pid}，等待其结束...")
            # 启动监控线程等待子进程结束
            threading.Thread(target=_watch_pid, args=(entry, pid), daemon=True).start()
        else:
            if saved_status == "running":
                # 进程已不存在，尝试用 returncode 判断
                rc = d.get("returncode")
                entry.status = "error" if rc else "done"
                entry.finished_at = entry.finished_at or time.time()
                _log_put(entry, f"[恢复] Flask 重启前进程已退出（returncode={rc}）")
            else:
                entry.status = saved_status
            _log_put(entry, None)  # 结束标记

        with _tasks_lock:
            _tasks[task_id] = entry


# ── Notification 持久化 / 推送 / 飞书镜像 ────────────────────

def _persist_notifications():
    import json as _json
    with _notifications_lock:
        data = [{
            "id":         n.id,
            "task_id":    n.task_id,
            "level":      n.level,
            "title":      n.title,
            "body":       n.body,
            "created_at": n.created_at,
            "read":       n.read,
        } for n in _notifications_buf]
    try:
        _NOTIFICATIONS_PERSIST_FILE.write_text(_json.dumps(data, ensure_ascii=False),
                                                encoding="utf-8")
    except Exception:
        pass


def _restore_notifications():
    import json as _json
    if not _NOTIFICATIONS_PERSIST_FILE.exists():
        return
    try:
        data = _json.loads(_NOTIFICATIONS_PERSIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    cutoff = time.time() - _NOTIFY_KEEP_DAYS * 86400
    with _notifications_lock:
        for d in data:
            if d.get("created_at", 0) < cutoff:
                continue
            n = Notification(
                id=d.get("id", uuid.uuid4().hex[:12]),
                task_id=d.get("task_id", "") or "",
                level=d.get("level", "info"),
                title=d.get("title", ""),
                body=d.get("body", "") or "",
                created_at=d.get("created_at", time.time()),
                read=bool(d.get("read", False)),
            )
            _notifications[n.id] = n
            _notifications_buf.append(n)
        _notifications_buf.sort(key=lambda x: x.created_at)
        # 超量驱逐
        while len(_notifications_buf) > _NOTIFY_MAX_KEEP:
            oldest = _notifications_buf.pop(0)
            _notifications.pop(oldest.id, None)


_OPENCLAW_LOG = ROOT / ".openclaw_mirror.log"


def _openclaw_log(line: str):
    """把 openclaw 镜像的失败记一行(侧车日志 + stderr),best-effort。
    注意:绝不在此调用 _notify,否则失败会再次触发镜像 → 死循环。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    msg = f"{stamp} {line}"
    try:
        with open(_OPENCLAW_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    try:
        print(f"[openclaw-mirror] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _run_openclaw(cmd: list, target: str):
    """后台线程里跑 openclaw,捕获输出;非 0 / 异常落日志(不阻塞主流程)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            _openclaw_log(f"rc={r.returncode} target={target} "
                          f"err={tail[0] if tail else '(no output)'}")
    except Exception as e:
        _openclaw_log(f"rc=EXC target={target} err={type(e).__name__}: {e}")


def _mirror_to_openclaw(n: Notification):
    """配置开启时把通知镜像推到 OpenClaw 配置的 channel/target。
    失败不抛(不影响通知主流程),但会落 .openclaw_mirror.log / stderr 以便排查。"""
    try:
        cfg = (_load_yaml().get("notify", {}) or {}).get("openclaw", {}) or {}
        if not cfg.get("enabled") or not cfg.get("target"):
            return
        icon = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(n.level, "ℹ️")
        text = f"{icon} {n.title}"
        if n.body:
            text += "\n" + n.body
        target = str(cfg["target"])
        cmd = ["openclaw", "message", "send",
               "--channel", cfg.get("channel", "feishu"),
               "--target",  target,
               "--message", text]
        if cfg.get("account"):
            cmd += ["--account", str(cfg["account"])]
        # 后台线程执行:不阻塞 _notify 返回,失败由 _run_openclaw 落日志
        threading.Thread(target=_run_openclaw, args=(cmd, target), daemon=True).start()
    except Exception as e:
        _openclaw_log(f"rc=PRE target=? err={type(e).__name__}: {e}")


def _notify(level: str, title: str, body: str = "", task_id: str = "") -> Notification:
    """统一通知入口:写内存 + 持久化 + 触发 SSE 推送 + 镜像到 OpenClaw 飞书。"""
    n = Notification(
        id=uuid.uuid4().hex[:12],
        task_id=task_id or "",
        level=level,
        title=title,
        body=body or "",
        created_at=time.time(),
    )
    with _notifications_lock:
        _notifications[n.id] = n
        _notifications_buf.append(n)
        while len(_notifications_buf) > _NOTIFY_MAX_KEEP:
            oldest = _notifications_buf.pop(0)
            _notifications.pop(oldest.id, None)
    _notifications_event.set()                  # 唤醒所有 SSE 连接
    _persist_notifications()
    _mirror_to_openclaw(n)
    return n


def _notify_status_change(entry: TaskEntry):
    """根据 task 当前 status 自动发一条通知。只对 download 类型生效。"""
    if entry.task_type != "download":
        return
    tid_short = entry.task_id[:6]
    label     = entry.label or tid_short
    duration  = ""
    if entry.created_at and entry.finished_at:
        m = int((entry.finished_at - entry.created_at) // 60)
        duration = f"用时 {m} 分钟" if m > 0 else f"用时 {int(entry.finished_at - entry.created_at)} 秒"
    body_parts = [label]
    if duration: body_parts.append(duration)
    body = " · ".join(body_parts)
    if entry.status == "done":
        _notify("success", f"Task {tid_short} 完成", body=body, task_id=entry.task_id)
    elif entry.status == "stopped":
        _notify("warning", f"Task {tid_short} 已停止", body=body, task_id=entry.task_id)
    elif entry.status == "error":
        rc = entry.returncode if entry.returncode is not None else "?"
        _notify("error", f"Task {tid_short} 失败",
                body=f"{body} · returncode={rc}", task_id=entry.task_id)


# ── 异步 PRISMA/EnMAP daemon ────────────────────────────────

_ASYNC_LOOP_INTERVAL = 60       # 秒,每分钟扫一次
_ASYNC_LOOP_FIRST_DELAY = 30    # 启动后等 30s 再首次扫(避免 task 刚创建立刻被查)
_ASYNC_HEARTBEAT_INTERVAL = 600 # 秒,"仍在等数据中心备货"日志的节流间隔(每 10 分钟一条)
_async_last_heartbeat: dict = {}  # (task_id, sensor) → last_log_ts
# sensor_id → (module_path, class_name, credentials_section)
_ASYNC_SENSOR_REGISTRY = {
    "prisma": ("downloader.prisma", "PRISMADownloader", "prisma"),
    "enmap":  ("downloader.enmap",  "EnMAPDownloader",  "dlr_eoweb"),
}


def _async_check_one(task: TaskEntry, sensor: str, sensor_dir: Path):
    """对一个 area_dir/<sensor> 子目录调 check_pending;到货则补包 + 通知。"""
    if sensor not in _ASYNC_SENSOR_REGISTRY:
        return
    mod_path, cls_name, cred_section = _ASYNC_SENSOR_REGISTRY[sensor]
    pending_file = sensor_dir / f".{sensor}_pending_order.json"
    if not pending_file.exists():
        # 没 pending — 把这个 sensor 从 task.pending_async 移除(可能之前已完成或用户清了)
        if sensor in (task.pending_async or []):
            task.pending_async = [s for s in task.pending_async if s != sensor]
            _persist_tasks()
        return
    try:
        import importlib as _il
        cls = getattr(_il.import_module(mod_path), cls_name)
        creds = (_load_yaml().get(cred_section) or {})
        dl = cls(credentials=creds, output_dir=task.output_dir or ".")
        result = dl.check_pending(sensor_dir)
    except Exception as e:
        _log_put(task, f"[daemon] {sensor} check_pending 异常: {e}")
        return
    # PRISMA 返回 Path 或 None;EnMAP 返回 list
    if isinstance(result, list):
        files = result
    elif result is None:
        files = []
    else:
        files = [result]
    if not files:
        # 还没 ready,节流打一条心跳日志(首次立即打,之后每 _ASYNC_HEARTBEAT_INTERVAL 一条)
        key = (task.task_id, sensor)
        now_ts = time.time()
        last = _async_last_heartbeat.get(key, 0.0)
        if now_ts - last >= _ASYNC_HEARTBEAT_INTERVAL or last == 0.0:
            _async_last_heartbeat[key] = now_ts
            _log_put(task, f"[daemon] {sensor} 仍在等数据中心备货(每 {_ASYNC_LOOP_INTERVAL}s 自动重查)")
        return

    # 到货了 — 推消息 + 增量补包
    _log_put(task, f"[daemon] {sensor} 异步数据已到货: {len(files)} 个文件")
    delivery_dir = task.delivery_dir
    kml = task.kml
    # 增量补包到原 delivery_dir(如果有的话)
    if delivery_dir and kml:
        try:
            from postprocess.package import package_delivery
            area_root = sensor_dir.parent          # output_dir/<area>
            area_label = area_root.name
            package_delivery(
                raw_area_dir=area_root,
                kml_path=Path(kml),
                delivery_root=Path(delivery_dir),
                area_label=area_label,
                incremental=True,
            )
            _log_put(task, f"[daemon] {sensor} 已增量补包到交付目录: {delivery_dir}")
            # 钩子(c):补包后对该交付跑自检;仍 TRUNCATED/GAP 则 SAFE 续传+再补
            try:
                _run_delivery_check(task, do_repair=True, with_ftps=True)
            except Exception as _e:
                _log_put(task, f"[自检] 补包后自检异常: {_e}")
        except Exception as e:
            _log_put(task, f"[daemon] {sensor} 补包失败: {e}")

    _notify("success",
            f"{sensor.upper()} 数据已到货",
            body=f"Task {task.task_id[:6]} · {len(files)} 个文件已下载并补包到 {delivery_dir or '(无交付目录)'}",
            task_id=task.task_id)

    # 从 pending_async 移除
    if sensor in (task.pending_async or []):
        task.pending_async = [s for s in task.pending_async if s != sensor]
        _persist_tasks()


def _task_area_dir(entry: "TaskEntry") -> Optional[Path]:
    """根据 task.kml 推导本任务对应的单个 area_dir(=output_dir/<kml_stem>)。
    没有 kml 或目录不在就返回 None。
    """
    if not entry.output_dir or not entry.kml:
        return None
    out = Path(entry.output_dir)
    stem = Path(entry.kml).stem
    p = out / stem
    return p if p.is_dir() else None


def _resurrect_orphan_orders(entry: "TaskEntry") -> List[str]:
    """扫本 task 自己的 area_dir,如果某个异步 sensor 盘上有 .{sensor}_pending_order.json
    但 task.pending_async 里没有,认回来。返回新增的 sensor 列表。"""
    area = _task_area_dir(entry)
    if area is None:
        return []
    added: List[str] = []
    current = set(entry.pending_async or [])
    for sensor in _ASYNC_SENSOR_REGISTRY:
        pf = area / sensor / f".{sensor}_pending_order.json"
        if pf.exists() and sensor not in current:
            current.add(sensor)
            added.append(sensor)
    if added:
        entry.pending_async = sorted(current)
        # done 状态的 task 重新进入 polling,让 daemon 跟踪
        if entry.status == "done":
            entry.status = "polling"
            entry.finished_at = None
            _notify_status_change(entry)
        _log_put(entry, f"[daemon] 复活孤儿异步订单: {','.join(added)}(原 status 已翻 polling)")
        _persist_tasks()
    return added


# ── 交付自检 + 分级修复(接 postprocess/delivery_check)──────────────────────────
_DELIVERY_SWEEP_INTERVAL = 1800       # 巡检间隔(秒)
_DELIVERY_SWEEP_FIRST_DELAY = 120
_DELIVERY_RECHECK_MIN_GAP = 3600      # 近 1h 内已 ok 的 task 巡检时跳过
_repair_locks: dict = {}
_repair_locks_guard = threading.Lock()


def _repair_lock_for(task_id: str) -> threading.Lock:
    """每 task 一把锁,防 sweep / daemon / verify 并发修复同一任务。"""
    with _repair_locks_guard:
        lk = _repair_locks.get(task_id)
        if lk is None:
            lk = threading.Lock()
            _repair_locks[task_id] = lk
        return lk


def _delivery_area_paths(entry: "TaskEntry"):
    """返回 (raw_area_dir, delivery_area_dir, area_label),解析与 _async_check_one 一致:
    delivery_root = entry.delivery_dir, area_label = kml stem, 真区域目录 = delivery_dir/<stem>。
    不满足返回 None。"""
    if not entry.kml or not entry.delivery_dir:
        return None
    area_dir = _task_area_dir(entry)        # output_dir/<kml_stem>
    if area_dir is None:
        return None
    stem = Path(entry.kml).stem
    return area_dir, Path(entry.delivery_dir) / stem, stem


def _mk_safe_package_cb(entry: "TaskEntry", raw_area_dir: Path, area_label: str):
    def _cb():
        try:
            from postprocess.package import package_delivery
            package_delivery(
                raw_area_dir=raw_area_dir, kml_path=Path(entry.kml),
                delivery_root=Path(entry.delivery_dir), area_label=area_label,
                incremental=True)
            return True
        except Exception as e:
            _log_put(entry, f"[自检] 增量补包失败: {e}")
            return False
    return _cb


def _mk_dem_fetch_cb(entry: "TaskEntry"):
    """DEM 自动补全:下载 Copernicus DEM 并补包到交付季节根(无需账号)。"""
    def _cb():
        try:
            from postprocess.dem_fetch import fetch_dem_for_area
            ok = fetch_dem_for_area(entry.kml, entry.output_dir, entry.delivery_dir)
            if not ok:
                _log_put(entry, "[自检] DEM 自动补全失败")
            return ok
        except Exception as e:
            _log_put(entry, f"[自检] DEM 自动补全异常: {e}")
            return False
    return _cb


def _mk_overview_fetch_cb(entry: "TaskEntry", delivery_area: Path):
    """投影底图自动补全:重新下载 Google 卫星底图到交付区域目录顶层。"""
    def _cb():
        try:
            from postprocess.satellite_overview import download_satellite_overview
            from downloader.kml_parser import parse_kml
            creds = _load_yaml() or {}
            g = (creds.get("google_maps") or {})
            api_key = g.get("api_key")
            if not api_key:
                _log_put(entry, "[自检] 未配置 google_maps.api_key,跳过投影底图补全")
                return False
            geom, bbox, _ = parse_kml(str(entry.kml))
            out = download_satellite_overview(
                bbox=bbox, api_key=api_key, delivery_dir=delivery_area,
                geometry=geom, maptype="satellite", proxy=g.get("proxy"))
            ok = bool(out and Path(out).exists())
            if not ok:
                _log_put(entry, "[自检] 投影底图补全失败(Google 瓦片下载未成功)")
            return ok
        except Exception as e:
            _log_put(entry, f"[自检] 投影底图补全异常: {e}")
            return False
    return _cb


def _mk_ftps_resume_cb(entry: "TaskEntry", raw_area_dir: Path):
    def _cb(sensor_id: str):
        reg = _ASYNC_SENSOR_REGISTRY.get(sensor_id)
        if not reg:
            return False
        mod_path, cls_name, cred_section = reg
        try:
            import importlib as _il
            cls = getattr(_il.import_module(mod_path), cls_name)
            creds = (_load_yaml().get(cred_section) or {})
            dl = cls(credentials=creds, output_dir=entry.output_dir or ".")
            return bool(dl.check_pending(raw_area_dir / sensor_id))
        except Exception as e:
            _log_put(entry, f"[自检] {sensor_id} 续传失败: {e}")
            return False
    return _cb


def _inc_attempt(entry: "TaskEntry", key: str):
    d = dict(entry.repair_attempts or {})
    d[key] = d.get(key, 0) + 1
    entry.repair_attempts = d


def _notify_risky(entry: "TaskEntry", action: dict):
    _notify("warning", "交付需人工确认",
            body=f"Task {entry.task_id[:6]} · {action.get('label','')} · 一键 {action.get('endpoint','')}",
            task_id=entry.task_id)


def _run_delivery_check(entry: "TaskEntry", *, do_repair: bool = True,
                        with_ftps: bool = False) -> dict:
    """对 entry 跑交付自检(+可选 SAFE 修复),更新持久字段并通知,返回 report dict。
    单写锁保护;拿不到锁说明已有人在修,直接返回上次报告。"""
    paths = _delivery_area_paths(entry)
    if paths is None:
        return {}
    raw_area_dir, delivery_area, area_label = paths
    lock = _repair_lock_for(entry.task_id)
    if not lock.acquire(blocking=False):
        return entry.validation_report or {}
    try:
        from postprocess.delivery_check import check_delivery, execute_repairs, load_rules
        report = check_delivery(
            delivery_dir=delivery_area, area_label=area_label,
            raw_area_dir=raw_area_dir, requested_sensors=entry.sensors or [],
            summary=None, progress=entry.progress or {},
            rules=load_rules(), task_id=entry.task_id)
        if do_repair:
            report = execute_repairs(
                report,
                safe_package_cb=_mk_safe_package_cb(entry, raw_area_dir, area_label),
                ftps_resume_cb=_mk_ftps_resume_cb(entry, raw_area_dir) if with_ftps else None,
                dem_fetch_cb=_mk_dem_fetch_cb(entry),
                overview_fetch_cb=_mk_overview_fetch_cb(entry, delivery_area),
                attempts_get=lambda k: (entry.repair_attempts or {}).get(k, 0),
                attempts_inc=lambda k: _inc_attempt(entry, k))
        entry.validation_status = report.overall
        entry.validation_report = report.to_dict()
        entry.last_checked_at = time.time()
        if report.overall == "ok":
            entry.repair_attempts = {}      # 交付正常 → 重置预算,允许日后回归再修
        _persist_tasks()
        for a in report.risky_repairs_offered:
            _notify_risky(entry, a)
        if report.safe_repairs_run:
            _log_put(entry, f"[自检] 已执行 SAFE 修复: {[a.get('kind') for a in report.safe_repairs_run]}")
        return report.to_dict()
    finally:
        lock.release()


def _delivery_sweep_loop():
    """定期巡检:扫 done 的下载任务,复查交付是否仍完整,SAFE 自动修。
    绝不碰 running/polling 或仍有 pending_async 的任务(归 _async_pending_loop)。"""
    time.sleep(_DELIVERY_SWEEP_FIRST_DELAY)
    while True:
        try:
            with _tasks_lock:
                targets = [t for t in _tasks.values()
                           if t.task_type == "download" and t.status == "done"
                           and not t.pending_async and t.delivery_dir]
            now = time.time()
            for entry in targets:
                if (entry.last_checked_at and entry.validation_status == "ok"
                        and now - entry.last_checked_at < _DELIVERY_RECHECK_MIN_GAP):
                    continue
                try:
                    _run_delivery_check(entry, do_repair=True, with_ftps=False)
                except Exception as e:
                    print(f"[delivery_sweep] {entry.task_id}: {e}", flush=True)
        except Exception as e:
            try:
                print(f"[delivery_sweep] error: {e}", flush=True)
            except Exception:
                pass
        time.sleep(_DELIVERY_SWEEP_INTERVAL)


def _async_pending_loop():
    """后台 daemon:周期扫所有 polling/running 状态 task 的 output_dir/<area>/<sensor>/,
    对 PRISMA / EnMAP 调 check_pending,到货则补包 + 通知;待所有 pending_async 清空时
    把 task.status 从 polling 转 done。"""
    time.sleep(_ASYNC_LOOP_FIRST_DELAY)
    while True:
        try:
            # 一开始先扫所有 done/polling task 复活孤儿订单(让本来漏掉的进入轮询)
            with _tasks_lock:
                resurrect_targets = [t for t in _tasks.values()
                                     if t.task_type == "download"
                                     and t.status in ("done", "polling", "running")]
            for entry in resurrect_targets:
                try:
                    _resurrect_orphan_orders(entry)
                except Exception as _e:
                    print(f"[async_pending_loop] resurrect {entry.task_id}: {_e}", flush=True)

            with _tasks_lock:
                candidates = [t for t in _tasks.values()
                              if t.task_type == "download"
                              and t.status in ("running", "polling")
                              and (t.pending_async or t.status == "polling")]
            for entry in candidates:
                area = _task_area_dir(entry)
                if area is None:
                    continue
                # 只扫本 task 自己的 area,不再遍历 output_dir 全部子目录
                for sensor in list(entry.pending_async or []):
                    sensor_dir = area / sensor
                    if not sensor_dir.exists():
                        continue
                    _async_check_one(entry, sensor, sensor_dir)
                # 所有 pending 都完成 → polling 转 done
                if entry.status == "polling" and not entry.pending_async:
                    entry.status = "done"
                    entry.finished_at = time.time()
                    _log_put(entry, "[daemon] 所有异步源已到货,task 完成")
                    _persist_tasks()
                    _notify_status_change(entry)
                    # 钩子(b):全部到货后跑交付自检 + SAFE 修复(异步刚落档,信号最全)
                    try:
                        _run_delivery_check(entry, do_repair=True, with_ftps=True)
                    except Exception as _e:
                        _log_put(entry, f"[自检] 完成自检异常: {_e}")
        except Exception as e:
            try:
                print(f"[async_pending_loop] error: {e}", flush=True)
            except Exception:
                pass
        time.sleep(_ASYNC_LOOP_INTERVAL)


def _watch_pid(task: TaskEntry, pid: int):
    """轮询等待外部 PID 退出，更新任务状态。用于 Flask 重启后恢复的任务。"""
    import signal
    while True:
        try:
            os.kill(pid, 0)
            time.sleep(5)
        except OSError:
            break
    # 进程已退出，尝试获取退出码（无法 waitpid 非子进程，只能标记 done）
    if task.status == "running":
        task.status = "done"
        task.finished_at = time.time()
        _log_put(task, "[恢复] 子进程已结束")
        _log_put(task, None)
        _persist_tasks()
        _notify_status_change(task)


def _log_put(task: TaskEntry, item):
    """向指定任务的日志 buffer 追加一条，并唤醒该任务的 SSE 连接。
    同时 mirror 到 LOG_DIR/<task_id>.log（None 结束标记会关闭文件）。
    若文件已关闭(restore 后 / 上一次 session 已 close)且本次是字符串,
    懒重新以 append 模式打开,保证 daemon 等后台事件也能落到文件里。"""
    with task.log_lock:
        task.log_buf.append(item)
        fh = task.log_file
        # 文件关了但收到真实字符串日志 → 懒重开
        if fh is None and task.task_id and isinstance(item, str) and not item.startswith("__PROGRESS__"):
            try:
                fh = open(LOG_DIR / f"{task.task_id}.log", "a", encoding="utf-8", buffering=1)
                task.log_file = fh
            except Exception:
                fh = None
        if fh is not None:
            try:
                if item is None:
                    fh.close()
                    task.log_file = None
                elif isinstance(item, str) and not item.startswith("__PROGRESS__"):
                    fh.write(item + "\n")
            except Exception:
                task.log_file = None
    task.log_event.set()


def _gc_tasks():
    """清理已结束且超过 GC_SECONDS 的任务。手动停止的任务不参与短期 GC，仅由每日 GC 清理。"""
    now = time.time()
    with _tasks_lock:
        to_remove = [
            tid for tid, t in _tasks.items()
            if t.status not in ("running", "stopped")
            and t.finished_at is not None
            and (now - t.finished_at) > GC_SECONDS
        ]
        for tid in to_remove:
            del _tasks[tid]


def _daily_gc():
    """后台线程：每天 0 点清理孤儿任务和超期历史任务。"""
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        time.sleep((next_midnight - now).total_seconds())

        cutoff = time.time() - DAILY_GC_KEEP_DAYS * 86400
        with _tasks_lock:
            to_remove = []
            for tid, t in _tasks.items():
                # 孤儿任务：status=running 但进程已不存在
                if t.status == "running" and t.pid:
                    try:
                        os.kill(t.pid, 0)
                    except OSError:
                        t.status = "error"
                        t.finished_at = time.time()
                # 超期历史任务
                if t.status != "running" and t.finished_at and t.finished_at < cutoff:
                    to_remove.append(tid)
            for tid in to_remove:
                del _tasks[tid]
        _persist_tasks()


def _build_label(task_cfg: dict) -> str:
    """构建任务的人类可读标签，如 'sentinel2, aster @ 东安金矿.kml'"""
    sensors = task_cfg.get("sensor", [])
    kml = task_cfg.get("kml", "")
    kml_name = Path(kml).stem if kml else ""
    sensor_part = ", ".join(sensors[:3])
    if len(sensors) > 3:
        sensor_part += f" +{len(sensors) - 3}"
    if kml_name:
        return f"{sensor_part} @ {kml_name}"
    return sensor_part or "未命名任务"


# ═══════════════════════════════════════════════════════════════
# 配置读写
# ═══════════════════════════════════════════════════════════════

def _load_yaml() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # yaml.safe_load 会把 YYYY-MM-DD 解析为 datetime.date，转回字符串
    def _normalize(obj):
        if isinstance(obj, dict):
            return {k: _normalize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_normalize(i) for i in obj]
        if isinstance(obj, date):
            return obj.strftime("%Y-%m-%d")
        return obj
    return _normalize(data)


def _save_yaml(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False, indent=2)


@app.route("/api/config", methods=["GET"])
def get_config():
    """读取 credentials.yaml → JSON"""
    with _config_lock:
        cfg = _load_yaml()
    # 确保各平台节存在（前端不用判空）
    for section in ("copernicus", "nasa_earthdata", "dlr_eoweb"):
        cfg.setdefault(section, {"username": "", "password": ""})
    cfg.setdefault("google_earth_engine", {
        "service_account_email": "",
        "service_account_key_path": "",
    })
    cfg.setdefault("task", {})
    task = cfg["task"]
    task.setdefault("kml", "")
    task.setdefault("sensor", ["dem"])
    task.setdefault("start", "2023-01-01")
    task.setdefault("end", "2024-12-31")
    task.setdefault("cloud", 20)
    task.setdefault("max_items", 5)
    task.setdefault("clip", True)
    task.setdefault("output", "./downloads")
    task.setdefault("no_derive", False)
    task.setdefault("no_package", False)
    task.setdefault("delivery_dir", "./delivery")
    task.setdefault("gee_collection_id", "")
    task.setdefault("gee_bands", [])
    task.setdefault("gee_scale", 30)
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def save_config():
    """保存 JSON → credentials.yaml（多任务模式下始终允许，任务参数已 argv 冻结）"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "无效的JSON"}), 400

    with _config_lock:
        existing = _load_yaml()
        for section in ("copernicus", "nasa_earthdata", "dlr_eoweb", "google_earth_engine"):
            if section in data:
                existing.setdefault(section, {})
                existing[section].update(data[section])
        if "task" in data:
            existing.setdefault("task", {})
            existing["task"].update(data["task"])
        _save_yaml(existing)
    return jsonify({"ok": True})


@app.route("/api/check-output-writable", methods=["POST"])
def check_output_writable():
    """提交下载任务前，预检指定输出目录是否对 daemon 可写。

    避免用户填了 macOS TCC 拦截的可移动卷路径（如 /Volumes/外置盘/...）
    后任务跑了几十秒才看到 Operation not permitted 错误。
    """
    import errno
    data = request.get_json(force=True) or {}
    paths = []
    for key in ("output", "delivery_dir"):
        p = (data.get(key) or "").strip()
        if p:
            paths.append((key, p))
    if not paths:
        return jsonify({"ok": True, "checked": []})

    results = []
    all_ok = True
    for label, path in paths:
        abs_path = Path(path).expanduser()
        if not abs_path.is_absolute():
            abs_path = (ROOT / abs_path).resolve()
        try:
            abs_path.mkdir(parents=True, exist_ok=True)
            probe = abs_path / f".regression_writable_{uuid.uuid4().hex[:8]}.tmp"
            probe.write_text("ok")
            probe.unlink()
            results.append({"field": label, "path": str(abs_path), "writable": True})
        except OSError as e:
            all_ok = False
            hint = ""
            if e.errno == errno.EPERM and "/Volumes/" in str(abs_path):
                hint = ("macOS 隐私保护拦截。请在 系统设置 → 隐私与安全性 → 完全磁盘访问 "
                        "中添加 Python.app（路径见 README），然后重启 daemon。")
            elif e.errno == errno.EACCES:
                hint = "权限不足，daemon 用户对该目录无写权限。"
            results.append({
                "field": label, "path": str(abs_path), "writable": False,
                "errno": e.errno, "error": str(e), "hint": hint,
            })
    return jsonify({"ok": all_ok, "checked": results})


@app.route("/api/upload-kml", methods=["POST"])
def upload_kml():
    """接收上传的 KML/ovKML/Excel 文件，保存到 uploads/kml/，返回服务器路径"""
    if "file" not in request.files:
        return jsonify({"error": "未收到文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "文件名为空"}), 400

    # 从原始文件名提取扩展名并验证
    orig_ext = Path(f.filename).suffix.lower()
    if orig_ext not in (".kml", ".ovkml", ".kmz", ".ovkmz", ".xlsx", ".xls"):
        return jsonify({"error": "仅支持 .kml / .ovkml / .kmz / .ovkmz / .xlsx / .xls 文件"}), 400

    # 文件名加时间戳后缀，防止同名文件覆盖导致冲突检测误判
    stem = Path(f.filename).stem.replace("/", "_").replace("\\", "_")
    suffix = Path(f.filename).suffix.lower()
    safe_name = f"{stem}_{int(time.time())}{suffix}"
    dest = UPLOAD_DIR / safe_name
    f.save(dest)
    return jsonify({"ok": True, "path": str(dest), "name": safe_name})


# ═══════════════════════════════════════════════════════════════
# 任务运行
# ═══════════════════════════════════════════════════════════════

def _build_argv(task: dict) -> list:
    """根据任务配置构建 main.py 的 argv 列表"""
    argv = [sys.executable, str(MAIN_PY)]

    kml = task.get("kml", "").strip()
    if kml:
        argv += ["--kml", kml]

    sensors = task.get("sensor", [])
    if sensors:
        argv += ["--sensor"] + sensors

    start = task.get("start", "").strip()
    end = task.get("end", "").strip()
    if start:
        argv += ["--start", start]
    if end:
        argv += ["--end", end]

    cloud = task.get("cloud")
    if cloud is not None:
        argv += ["--cloud", str(cloud)]

    max_items = task.get("max_items")
    if max_items is not None:
        argv += ["--max-items", str(max_items)]

    if not task.get("clip", True):
        argv.append("--no-clip")

    if task.get("no_derive", False):
        argv.append("--no-derive")

    output = task.get("output", "").strip()
    if output:
        argv += ["--output", output]

    if task.get("no_package", False):
        argv.append("--no-package")

    delivery_dir = task.get("delivery_dir", "").strip()
    if delivery_dir:
        argv += ["--delivery-dir", delivery_dir]

    # 始终传配置文件路径，使用 web 端保存的配置
    argv += ["--config", str(CONFIG_PATH)]

    return argv


import re as _re

def _parse_tqdm(raw: str):
    """
    解析 tqdm 进度行，返回 (filename, percent) 或 None。
    tqdm 输出格式示例：
      "      filename.tif   45%|████▌     | 4.50M/10.0M [00:02<00:02, 2.1MB/s]"
    行首有 \\r，或本身就是 \\r 后内容。
    """
    # 去掉控制字符
    s = raw.strip()
    # 检查是否含百分比数字 + 进度条符号
    m = _re.search(r'(\d{1,3})%\|', s)
    if not m:
        return None
    pct = int(m.group(1))
    # 文件名：进度条前、百分比前的文字（去除前导空格）
    fname = s[:m.start()].strip()
    return fname, pct


def _read_stdout(task: TaskEntry, proc: subprocess.Popen):
    """后台线程：读取子进程 stdout → 推入任务专属日志 buffer
    以行为单位读取（避免多字节 UTF-8 乱码），再按 \\r 拆分处理 tqdm 进度。
    """
    try:
        for raw_line in iter(proc.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="replace")
            # 按 \r 拆分：tqdm 用 \r 覆写同一行，最后一段才是最终内容
            parts = line.split('\r')
            for i, part in enumerate(parts):
                part = part.rstrip('\n')
                if not part:
                    continue
                is_last = (i == len(parts) - 1)
                # 子进程通知协议:__NOTIFY__:<level>:<title>:<body>
                # 把消息直接灌到应用内 notification 中心(同时镜像到飞书),
                # 不再以普通日志行进 task log。
                if part.startswith("__NOTIFY__:"):
                    try:
                        _, level, title, *body_parts = part.split(":", 3)
                        body = body_parts[0] if body_parts else ""
                        if level not in ("info", "success", "warning", "error"):
                            level = "info"
                        _notify(level, title.strip(), body=body.strip(),
                                task_id=task.task_id)
                    except Exception:
                        _log_put(task, part)
                    continue
                # 异步 pending sensors 标记协议:__ASYNC_PENDING__:prisma,enmap
                # 子进程退出后 task 不进 done,而是 polling,等 daemon 完成异步部分
                if part.startswith("__ASYNC_PENDING__:"):
                    try:
                        _, sensors_csv = part.split(":", 1)
                        names = [s.strip() for s in sensors_csv.split(",") if s.strip()]
                        existing = set(task.pending_async or [])
                        for n in names:
                            existing.add(n)
                        task.pending_async = sorted(existing)
                        _log_put(task, f"[异步] 待 daemon 接管: {','.join(task.pending_async)}")
                    except Exception:
                        _log_put(task, part)
                    continue
                # 整体进度事件:__PROGRESS_EVENT__{"sensor":...,"phase":...,...}
                # 累积到 task.progress,原样推 SSE(前端拦截后渲染顶部进度条,不进日志面板)
                if part.startswith("__PROGRESS_EVENT__"):
                    try:
                        import json as _json
                        evt = _json.loads(part[len("__PROGRESS_EVENT__"):])
                        sensor = evt.get("sensor")
                        if sensor:
                            cur = dict(task.progress.get(sensor) or {})
                            for k, v in evt.items():
                                if k != "sensor":
                                    cur[k] = v
                            task.progress[sensor] = cur
                    except Exception:
                        pass
                    _log_put(task, part)
                    continue
                # 交付自检报告:__DELIVERY_CHECK__{json}(main.py 同步打包后上报)
                if part.startswith("__DELIVERY_CHECK__"):
                    try:
                        import json as _json
                        rep = _json.loads(part[len("__DELIVERY_CHECK__"):])
                        task.validation_status = rep.get("overall", "") or ""
                        task.validation_report = rep
                        task.last_checked_at = time.time()
                        # 合并子进程 sidecar 计数器(取 max,跨子进程/daemon 不清零)
                        try:
                            sc = Path(task.output_dir or ".") / Path(task.kml).stem / ".delivery_repair_state.json"
                            if sc.exists():
                                cnt = (_json.loads(sc.read_text()) or {}).get("attempts", {})
                                merged = dict(task.repair_attempts or {})
                                for k, v in cnt.items():
                                    merged[k] = max(merged.get(k, 0), int(v))
                                task.repair_attempts = merged
                        except Exception:
                            pass
                        for a in rep.get("risky_repairs_offered", []):
                            _notify_risky(task, a)
                        _persist_tasks()
                    except Exception:
                        pass
                    continue
                parsed = _parse_tqdm(part)
                if parsed:
                    fname, pct = parsed
                    _log_put(task, f"__PROGRESS__{fname}|{pct}")
                elif is_last:
                    # 只把最后一段（真正的文本行）放入 buffer
                    _log_put(task, part)
            # 空行（只有 \n）
            if line == '\n':
                _log_put(task, "")
    finally:
        proc.stdout.close()
        proc.wait()  # 确保退出码已就绪
        task.returncode = proc.returncode
        # 只有 _read_stdout 正常结束时才更新状态（stop 已经设过 'stopped'）
        if task.status == "running":
            if proc.returncode:
                task.status = "error"
            elif task.pending_async:
                # 主子进程同步部分完成,但还有 PRISMA/EnMAP 等异步源未到货
                task.status = "polling"
                _log_put(task, f"[异步] 主流程结束,task 进入 polling,等 daemon 接管 {','.join(task.pending_async)}")
            else:
                task.status = "done"
        task.finished_at = time.time()
        _log_put(task, None)  # 结束标记
        _persist_tasks()
        _notify_status_change(task)


def _detect_proxy(task: TaskEntry, env: dict):
    """子进程网络出口配置：网络层走 OpenVPN，应用层无需注入 http_proxy。

    Why: 这台机器的出口已统一切到 OpenVPN（系统级 VPN，路由层透明转发），
    之前那套"扫 10792/10793/7890/1087/8080 端口、把找到的当成代理"的逻辑
    已被弃用 —— 它把 8080 上的 Flask web 服务自身误判成 HTTP 代理，导致
    CONNECT 请求被回 404。如果未来某天要改回应用层代理，让用户显式设
    HTTPS_PROXY 环境变量即可，本函数只负责 no_proxy 直连白名单。
    """
    _no_proxy = ",".join([
        "dataspace.copernicus.eu",     # Sentinel-2 (catalogue/identity/zipper)
        "earthdata.nasa.gov",          # NASA Earthdata (CMR搜索/认证/下载)
        "usgs.gov",                    # USGS (Landsat EarthExplorer/ERS)
        "jpl.nasa.gov",                # JPL (AVIRIS-NG)
    ])
    env["no_proxy"] = _no_proxy

    for _var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        _v = os.environ.get(_var)
        if _v:
            _log_put(task, f"[网络] 继承环境变量 {_var}={_v}")
            break
    else:
        _log_put(task, "[网络] 出口走系统路由（OpenVPN/直连），未注入 http_proxy")
    _log_put(task, f"[网络] no_proxy={_no_proxy}")
    _log_put(task, "")


@app.route("/api/run", methods=["POST"])
def run_task():
    """启动下载任务，返回 task_id"""
    _gc_tasks()

    # 并发检查
    with _tasks_lock:
        running_downloads = [t for t in _tasks.values()
                             if t.task_type == "download" and t.status == "running"]
        if len(running_downloads) >= MAX_DOWNLOAD:
            return jsonify({"error": f"已达到最大并发下载数 ({MAX_DOWNLOAD})"}), 429

    data = request.get_json(force=True) or {}
    task_cfg = data.get("task", {})

    # 冲突检测：同 kml + output_dir
    kml = task_cfg.get("kml", "").strip()
    output = task_cfg.get("output", "./downloads").strip()
    with _tasks_lock:
        for t in _tasks.values():
            if t.status == "running" and t.kml == kml and t.output_dir == output:
                return jsonify({
                    "error": "相同区域+输出目录的任务正在运行",
                    "conflict_task_id": t.task_id,
                }), 409

    # 保存配置
    with _config_lock:
        existing = _load_yaml()
        for section in ("copernicus", "nasa_earthdata", "dlr_eoweb", "google_earth_engine"):
            if section in data:
                existing.setdefault(section, {})
                existing[section].update(data[section])
        if "task" in data:
            existing.setdefault("task", {})
            existing["task"].update(data["task"])
        _save_yaml(existing)

    task_cfg = dict(existing.get("task", {}))
    argv = _build_argv(task_cfg)
    task_id = uuid.uuid4().hex[:8]

    entry = TaskEntry(
        task_id=task_id,
        task_type="download",
        label=_build_label(task_cfg),
        kml=kml,
        output_dir=output,
        sensors=task_cfg.get("sensor", []),
        max_items=task_cfg.get("max_items", 5),
        start_date=task_cfg.get("start", "") or "",
        end_date=task_cfg.get("end", "") or "",
        cloud=_int_or_default(task_cfg.get("cloud"), 100),
        delivery_dir=task_cfg.get("delivery_dir", "") or "",
        created_at=time.time(),
        argv=argv,
    )

    # 启动日志
    _log_put(entry, f"$ {' '.join(argv)}")
    _log_put(entry, "")

    # 环境变量 + 代理检测
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONWARNINGS"] = "ignore::FutureWarning"
    _detect_proxy(entry, env)

    # 启动子进程
    entry.proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,           # 无缓冲，二进制模式，保留原始 \r
        cwd=str(ROOT),
        env=env,
    )
    entry.pid = entry.proc.pid
    threading.Thread(target=_read_stdout, args=(entry, entry.proc), daemon=True).start()

    with _tasks_lock:
        _tasks[task_id] = entry

    _persist_tasks()
    _notify("info", f"Task {task_id[:6]} 已启动", body=entry.label, task_id=task_id)
    return jsonify({"ok": True, "task_id": task_id, "pid": entry.pid, "argv": argv})


@app.route("/api/stop", methods=["POST"])
def stop_task():
    """终止指定任务"""
    data = request.get_json(force=True) or {}
    task_id = data.get("task_id", "").strip()

    if not task_id:
        return jsonify({"ok": False, "error": "缺少 task_id 参数"}), 400

    with _tasks_lock:
        entry = _tasks.get(task_id)

    if not entry or entry.status != "running":
        return jsonify({"ok": False, "error": "任务不存在或已结束"})

    # 下载任务：终止子进程
    if entry.proc is not None and entry.proc.poll() is None:
        entry.proc.terminate()
        try:
            entry.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            entry.proc.kill()

    entry.status = "stopped"
    entry.finished_at = time.time()
    entry.returncode = entry.proc.returncode if entry.proc else None
    _log_put(entry, "")
    _log_put(entry, "[已停止] 用户终止了任务")
    _log_put(entry, None)
    _persist_tasks()
    _notify_status_change(entry)
    return jsonify({"ok": True})


@app.route("/api/status", methods=["GET"])
def get_status():
    """兼容旧接口：返回聚合状态 + 任务列表摘要"""
    _gc_tasks()
    with _tasks_lock:
        task_list = []
        any_running = False
        for t in _tasks.values():
            is_running = t.status == "running"
            if is_running:
                any_running = True
            task_list.append({
                "task_id":       t.task_id,
                "task_type":     t.task_type,
                "status":        t.status,
                "label":         t.label,
                "sensors":       t.sensors,
                "max_items":     t.max_items,
                "pid":           t.pid,
                "returncode":    t.returncode,
                "created_at":    t.created_at,
                "finished_at":   t.finished_at,
                "progress":      dict(t.progress or {}),
                "pending_async": list(t.pending_async or []),
            })
    task_list.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({
        "running": any_running,
        "tasks":   task_list,
    })


@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    """返回所有任务列表"""
    _gc_tasks()
    with _tasks_lock:
        result = []
        for t in _tasks.values():
            result.append({
                "task_id":      t.task_id,
                "task_type":    t.task_type,
                "status":       t.status,
                "label":        t.label,
                "kml":          t.kml,
                "output_dir":   t.output_dir,
                "sensors":      t.sensors,
                "max_items":    t.max_items,
                "start_date":   t.start_date,
                "end_date":     t.end_date,
                "cloud":        t.cloud,
                "delivery_dir": t.delivery_dir,
                "pid":          t.pid,
                "returncode":   t.returncode,
                "created_at":   t.created_at,
                "finished_at":  t.finished_at,
                "pending_async": list(t.pending_async or []),
            })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    resp = jsonify({"tasks": result})
    resp.headers["Cache-Control"] = "no-cache, no-store"
    return resp


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    """删除已结束的任务记录"""
    with _tasks_lock:
        entry = _tasks.get(task_id)
        if not entry:
            return jsonify({"error": "任务不存在"}), 404
        if entry.status == "running":
            return jsonify({"error": "任务仍在运行，请先停止"}), 409
        del _tasks[task_id]
    # 持久化删除,否则 Flask 重启后会从 .geo_tasks_persist.json 复活
    _persist_tasks()
    return jsonify({"ok": True})


def _async_source_info(entry, sensor, now_ts):
    """读取 entry 某个异步 sensor 的 pending-order 标记文件,返回结构化状态。
    供 /api/async-tasks 与 /api/tasks/<id>/diagnostic 复用。"""
    import json as _json
    from datetime import datetime as _dt
    area = _task_area_dir(entry)
    order_id = submitted_at = submitted_ts = None
    item_count = 0
    marker_exists = False
    if area is not None:
        pf = area / sensor / f".{sensor}_pending_order.json"
        if pf.exists():
            marker_exists = True
            try:
                d = _json.loads(pf.read_text(encoding="utf-8"))
                order_id = d.get("order_id")
                submitted_at = d.get("submitted_at")
                item_count = len(d.get("product_ids") or d.get("scene_ids") or [])
                if submitted_at:
                    try:
                        submitted_ts = _dt.fromisoformat(submitted_at).timestamp()
                    except Exception:
                        submitted_ts = None
            except Exception:
                pass
    base_ts = submitted_ts if submitted_ts is not None else (entry.created_at or now_ts)
    phase = (entry.progress or {}).get(sensor, {}).get("phase")
    return {
        "sensor":        sensor,
        "order_id":      order_id,
        "submitted_at":  submitted_at,
        "submitted_ts":  submitted_ts,
        "elapsed_sec":   max(0, int(now_ts - base_ts)),
        "item_count":    item_count,
        "phase":         phase,
        "marker_exists": marker_exists,
    }


@app.route("/api/async-tasks", methods=["GET"])
def get_async_tasks():
    """列出处于 polling(等异步)状态的下载任务,每个异步源给出
    order_id / 提交时间 / 已等时长 / 进度。"""
    _gc_tasks()
    now_ts = time.time()
    with _tasks_lock:
        polling = [t for t in _tasks.values()
                   if t.task_type == "download" and t.status == "polling"]
    result = []
    for t in polling:
        sources = [_async_source_info(t, s, now_ts) for s in list(t.pending_async or [])]
        result.append({
            "task_id":       t.task_id,
            "label":         t.label,
            "status":        t.status,
            "created_at":    t.created_at,
            "finished_at":   t.finished_at,
            "pending_async": list(t.pending_async or []),
            "sources":       sources,
        })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    resp = jsonify({"tasks": result})
    resp.headers["Cache-Control"] = "no-cache, no-store"
    return resp


@app.route("/api/async-tasks/<task_id>/stop", methods=["POST"])
def stop_async_task(task_id):
    """手动停止一个 polling 异步任务:删除 pending-order 标记文件(防 daemon 复活)、
    清空 pending_async、标记 stopped 留在历史里。已下载的数据全部保留。"""
    with _tasks_lock:
        entry = _tasks.get(task_id)
    if not entry:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    if entry.status != "polling":
        return jsonify({"ok": False, "error": "该任务不在异步等待中"})

    area = _task_area_dir(entry)
    removed = []
    for sensor in list(entry.pending_async or []):
        # 关键:删掉 pending 标记,否则 _resurrect_orphan_orders 下一轮会把它复活回 polling
        if area is not None:
            pf = area / sensor / f".{sensor}_pending_order.json"
            try:
                pf.unlink(missing_ok=True)
                removed.append(sensor)
            except Exception as e:
                _log_put(entry, f"[用户] 删除 {sensor} pending 标记失败: {e}")
        _async_last_heartbeat.pop((entry.task_id, sensor), None)

    entry.pending_async = []
    entry.status = "stopped"
    entry.finished_at = time.time()
    _log_put(entry, f"[用户] 手动停止异步等待,已删除 pending 标记: {','.join(removed) or '(无)'}")
    _log_put(entry, None)
    _persist_tasks()
    _notify_status_change(entry)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/tasks/<task_id>/diagnostic", methods=["GET"])
def task_diagnostic(task_id):
    """返回某任务的完整诊断包:配置 + 异步订单状态 + 完整进度 + 最近 N 行日志。
    供前端「复制诊断信息」按钮拼成可粘贴的 Markdown。"""
    from collections import deque as _deque
    try:
        n = int(request.args.get("n", 120))
    except Exception:
        n = 120
    n = max(1, min(n, 1000))
    with _tasks_lock:
        entry = _tasks.get(task_id)
    if not entry:
        return jsonify({"error": "任务不存在"}), 404

    now_ts = time.time()
    async_sources = [_async_source_info(entry, s, now_ts)
                     for s in list(entry.pending_async or [])]

    # 读日志尾部(避免整文件载入)
    log_tail = []
    log_path = LOG_DIR / f"{task_id}.log"
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                log_tail = [ln.rstrip("\n") for ln in _deque(fh, maxlen=n)]
        except Exception:
            log_tail = []

    return jsonify({
        "task_id":       entry.task_id,
        "label":         entry.label,
        "status":        entry.status,
        "task_type":     entry.task_type,
        "sensors":       list(entry.sensors or []),
        "start_date":    entry.start_date,
        "end_date":      entry.end_date,
        "cloud":         entry.cloud,
        "max_items":     entry.max_items,
        "output_dir":    entry.output_dir,
        "delivery_dir":  entry.delivery_dir,
        "kml":           entry.kml,
        "created_at":    entry.created_at,
        "finished_at":   entry.finished_at,
        "returncode":    entry.returncode,
        "pid":           entry.pid,
        "pending_async": list(entry.pending_async or []),
        "progress":      dict(entry.progress or {}),
        "async_sources": async_sources,
        "log_tail":      log_tail,
        "validation_status": entry.validation_status or "",
        "validation_report": dict(entry.validation_report or {}),
        "last_checked_at":   entry.last_checked_at,
    })


@app.route("/api/tasks/<task_id>/verify", methods=["POST"])
def verify_delivery(task_id):
    """手动触发交付自检 + SAFE 修复,返回报告。running 任务拒绝(409)。"""
    with _tasks_lock:
        entry = _tasks.get(task_id)
    if not entry:
        return jsonify({"error": "任务不存在"}), 404
    if entry.status == "running":
        return jsonify({"error": "任务仍在运行,稍后再校验"}), 409
    do_repair = request.args.get("repair", "1") != "0"
    report = _run_delivery_check(entry, do_repair=do_repair, with_ftps=True)
    return jsonify({"ok": True, "report": report})


@app.route("/api/tasks/<task_id>/repair", methods=["POST"])
def repair_delivery(task_id):
    """一键执行 RISKY 修复。body: {action_kind, sensor_id?, season_key?}。
    校验该 action 确在上次报告的 risky_repairs_offered 中,且未超限次。"""
    with _tasks_lock:
        entry = _tasks.get(task_id)
    if not entry:
        return jsonify({"error": "任务不存在"}), 404
    body = request.get_json(silent=True) or {}
    kind = body.get("action_kind", "")
    offered = (entry.validation_report or {}).get("risky_repairs_offered", [])
    if not any(a.get("kind") == kind for a in offered):
        return jsonify({"error": f"该修复动作不在最近报告里: {kind}"}), 400
    key = f"risky:{kind}"
    if (entry.repair_attempts or {}).get(key, 0) >= 3:
        entry.validation_status = "needs_attention"
        _persist_tasks()
        return jsonify({"error": "该修复已达上限,需人工介入"}), 429
    _inc_attempt(entry, key)
    _persist_tasks()
    if kind == "restart_task":
        # 复用整任务重下逻辑
        return restart_task(task_id)
    if kind == "daemon_restart":
        return jsonify({"error": "daemon 重启需在服务器手动执行(避免误杀在下任务)"}), 501
    return jsonify({"error": f"未知修复动作: {kind}"}), 400


@app.route("/api/tasks/<task_id>/restart", methods=["POST"])
def restart_task(task_id):
    """重新启动一个已停止/失败/完成的任务（复用原 argv）"""
    _gc_tasks()

    with _tasks_lock:
        old = _tasks.get(task_id)
    if not old:
        return jsonify({"error": "任务不存在"}), 404
    if old.status == "running":
        return jsonify({"error": "任务仍在运行"}), 409

    # 并发检查
    with _tasks_lock:
        running = [t for t in _tasks.values()
                   if t.task_type == old.task_type and t.status == "running"]
        limit = MAX_DOWNLOAD if old.task_type == "download" else MAX_PACKAGE
        if len(running) >= limit:
            return jsonify({"error": f"已达到最大并发数 ({limit})"}), 429

    # 冲突检测
    if old.task_type == "download":
        with _tasks_lock:
            for t in _tasks.values():
                if t.status == "running" and t.kml == old.kml and t.output_dir == old.output_dir:
                    return jsonify({"error": "相同区域的任务正在运行",
                                    "conflict_task_id": t.task_id}), 409

    new_id = uuid.uuid4().hex[:8]
    entry = TaskEntry(
        task_id=new_id,
        task_type=old.task_type,
        label=old.label,
        kml=old.kml,
        output_dir=old.output_dir,
        sensors=list(old.sensors),
        max_items=old.max_items,
        created_at=time.time(),
        argv=list(old.argv),
    )

    _log_put(entry, f"$ {' '.join(entry.argv)}")
    _log_put(entry, "")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONWARNINGS"] = "ignore::FutureWarning"
    _detect_proxy(entry, env)

    entry.proc = subprocess.Popen(
        entry.argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        cwd=str(ROOT),
        env=env,
    )
    entry.pid = entry.proc.pid
    threading.Thread(target=_read_stdout, args=(entry, entry.proc), daemon=True).start()

    with _tasks_lock:
        _tasks[new_id] = entry

    return jsonify({"ok": True, "task_id": new_id, "pid": entry.pid})


# ═══════════════════════════════════════════════════════════════
# 传感器预评估（只搜索，不下载）
# ═══════════════════════════════════════════════════════════════

# 交付架构必需的传感器组合（满足其一即可）
_DELIVERY_REQUIRED = {
    "dem_or_srtm": {"dem", "srtm"},
    "optical":     {"sentinel2", "landsat"},
    "thermal":     {"aster", "ecostress"},
}

def _preview_one(sensor: str, kml_path: str, start: str, end: str, creds: dict) -> dict:
    """对单个传感器做搜索预检，返回 {count, ok, error}"""
    try:
        import importlib
        from main import SENSOR_MAP
        from downloader.credentials import get_platform_creds, CredentialsError

        module_path, class_name, cred_key = SENSOR_MAP[sensor]
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)

        platform_creds = {}
        if cred_key:
            try:
                platform_creds = get_platform_creds(creds, cred_key)
            except CredentialsError:
                return {"count": 0, "ok": False, "error": "缺少账号"}

        _platform_kwargs = {
            "spot67":   {"platform": "spot67"},
            "pleiades": {"platform": "pleiades"},
            "wv2":      {"platform": "wv2"},
            "wv3":      {"platform": "wv3"},
            "gee_custom": {
                "collection_id": creds.get("task", {}).get("gee_collection_id", ""),
                "bands": creds.get("task", {}).get("gee_bands", []),
                "scale_meters": creds.get("task", {}).get("gee_scale", 30),
            },
        }
        extra_kwargs = _platform_kwargs.get(sensor, {})
        dl = cls(credentials=platform_creds, output_dir="/tmp", **extra_kwargs)

        from downloader.kml_parser import parse_kml
        geometry, bbox, _ = parse_kml(kml_path)

        # DEM/SRTM 无需时间参数
        if sensor in ("dem", "srtm"):
            results = dl.search(bbox=bbox, start_date=start or "2000-01-01",
                                end_date=end or "2024-12-31", count=3)
        else:
            results = dl.search(bbox=bbox, start_date=start, end_date=end, count=3)

        count = len(results) if results else 0
        return {"count": count, "ok": count > 0}
    except Exception as e:
        return {"count": 0, "ok": False, "error": str(e)[:80]}


@app.route("/api/preview", methods=["POST"])
def preview_sensors():
    """并发预检各传感器数据可用性"""
    import concurrent.futures

    data = request.get_json(force=True) or {}
    kml_path = data.get("kml_path", "").strip()
    start    = data.get("start", "")
    end      = data.get("end", "")
    sensors  = data.get("sensors", [])

    if not kml_path or not sensors:
        return jsonify({"error": "缺少 kml_path 或 sensors"}), 400

    creds = _load_yaml()

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(_preview_one, s, kml_path, start, end, creds): s
            for s in sensors
        }
        for fut in concurrent.futures.as_completed(future_map, timeout=30):
            s = future_map[fut]
            try:
                results[s] = fut.result()
            except Exception as e:
                results[s] = {"count": 0, "ok": False, "error": str(e)[:80]}

    # 计算交付架构满足度
    selected = set(sensors)
    missing_groups = []
    for group_name, group_sensors in _DELIVERY_REQUIRED.items():
        has_data = any(
            results.get(s, {}).get("ok", False)
            for s in group_sensors if s in selected
        )
        if not has_data:
            labels = {"dem_or_srtm": "高程（DEM/SRTM）",
                      "optical":     "光学（Sentinel-2/Landsat）",
                      "thermal":     "热红外（ASTER/ECOSTRESS）"}
            missing_groups.append(labels.get(group_name, group_name))

    results["__delivery__"] = {
        "ready":   len(missing_groups) == 0,
        "missing": missing_groups,
    }
    return jsonify(results)


# ═══════════════════════════════════════════════════════════════
# 应用内消息 REST + SSE
# ═══════════════════════════════════════════════════════════════

def _notification_to_dict(n: Notification) -> dict:
    return {
        "id":         n.id,
        "task_id":    n.task_id,
        "level":      n.level,
        "title":      n.title,
        "body":       n.body,
        "created_at": n.created_at,
        "read":       n.read,
    }


@app.route("/api/notifications", methods=["GET"])
def list_notifications():
    """列出消息(DESC by created_at)。query: unread_only=true/false, since=epoch, limit=N"""
    unread_only = request.args.get("unread_only", "").lower() in ("1", "true", "yes")
    try:
        since = float(request.args.get("since", "") or 0)
    except ValueError:
        since = 0
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
    except ValueError:
        limit = 50
    with _notifications_lock:
        items = list(_notifications_buf)
    items = [n for n in items
             if (not unread_only or not n.read)
             and (since <= 0 or n.created_at > since)]
    items.sort(key=lambda x: x.created_at, reverse=True)
    return jsonify({"notifications": [_notification_to_dict(n) for n in items[:limit]]})


@app.route("/api/notifications/unread_count", methods=["GET"])
def notifications_unread_count():
    with _notifications_lock:
        n = sum(1 for x in _notifications_buf if not x.read)
    return jsonify({"count": n})


@app.route("/api/notifications/<nid>/read", methods=["POST"])
def notifications_mark_read(nid):
    with _notifications_lock:
        n = _notifications.get(nid)
        if not n:
            return jsonify({"ok": False, "error": "通知不存在"}), 404
        n.read = True
    _persist_notifications()
    return jsonify({"ok": True})


@app.route("/api/notifications/read_all", methods=["POST"])
def notifications_mark_all_read():
    with _notifications_lock:
        for n in _notifications_buf:
            n.read = True
    _persist_notifications()
    return jsonify({"ok": True})


@app.route("/api/notifications/<nid>", methods=["DELETE"])
def notifications_delete(nid):
    with _notifications_lock:
        if nid not in _notifications:
            return jsonify({"ok": False, "error": "通知不存在"}), 404
        del _notifications[nid]
        _notifications_buf[:] = [n for n in _notifications_buf if n.id != nid]
    _persist_notifications()
    return jsonify({"ok": True})


@app.route("/api/notifications/clear_read", methods=["POST"])
def notifications_clear_read():
    with _notifications_lock:
        kept = [n for n in _notifications_buf if not n.read]
        removed_ids = [n.id for n in _notifications_buf if n.read]
        _notifications_buf[:] = kept
        for rid in removed_ids:
            _notifications.pop(rid, None)
    _persist_notifications()
    return jsonify({"ok": True, "removed": len(removed_ids)})


@app.route("/api/notifications/stream")
def notifications_stream():
    """SSE 实时通知流(全局共享,所有打开的页面都能收到)。"""
    def event_gen():
        # 进入连接后从当前 buf 末尾开始,只推后续新增
        with _notifications_lock:
            pos = len(_notifications_buf)
        yield "data: \n\n"
        idle_ticks = 0
        while True:
            with _notifications_lock:
                snapshot = _notifications_buf[pos:]
                pos = len(_notifications_buf)
            if snapshot:
                idle_ticks = 0
                for n in snapshot:
                    import json as _json
                    payload = _json.dumps(_notification_to_dict(n), ensure_ascii=False)
                    yield f"event: notification\ndata: {payload}\n\n"
            else:
                _notifications_event.wait(timeout=2)
                _notifications_event.clear()
                idle_ticks += 1
                if idle_ticks >= 15:    # 约 30s
                    idle_ticks = 0
                    yield ": keepalive\n\n"

    return Response(
        event_gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ═══════════════════════════════════════════════════════════════
# SSE 流 & 页面
# ═══════════════════════════════════════════════════════════════

@app.route("/api/stream")
def stream():
    """SSE 实时日志流（按 task_id 路由到对应任务的 log_buf）"""
    task_id = request.args.get("task_id", "").strip()
    if not task_id:
        return jsonify({"error": "缺少 task_id 参数"}), 400

    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404

    def event_gen():
        yield "data: \n\n"
        pos = 0  # 当前连接已读到的 buffer 位置
        while True:
            with task.log_lock:
                snapshot = task.log_buf[pos:]
            if snapshot:
                for item in snapshot:
                    pos += 1
                    if item is None:
                        yield "data: __END__\n\n"
                        return
                    if item.startswith("__PROGRESS__"):
                        yield f"data: {item}\n\n"
                    else:
                        safe = item.replace("\n", " ")
                        yield f"data: {safe}\n\n"
            else:
                # 短轮询，不 clear()，多 SSE 连接安全
                task.log_event.wait(timeout=2)
                # 每 30 秒发一次 keepalive（约 15 次空循环一次）
                yield ": keepalive\n\n"

    return Response(
        event_gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _read_version() -> str:
    """从 pyproject.toml 读取项目版本号。"""
    try:
        toml_path = ROOT / "pyproject.toml"
        for line in toml_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("version") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


@app.route("/api/version")
def api_version():
    return jsonify({"version": _read_version()})


@app.route("/api/stats")
def api_stats():
    """返回各传感器的历史下载成功率统计。"""
    from downloader.stats import get_stats
    return jsonify(get_stats())


@app.route("/")
def index():
    from main import SENSOR_NETWORK
    return render_template("index.html", sensor_network=SENSOR_NETWORK)


@app.route("/architecture")
def architecture():
    return render_template("architecture.html")


@app.route("/delivery")
def delivery():
    return render_template("delivery.html")


@app.route("/architecture.html")
def architecture_html():
    return render_template("architecture.html")


@app.route("/delivery.html")
def delivery_html():
    return render_template("delivery.html")


# ═══════════════════════════════════════════════════════════════
# 架构配置读写
# ═══════════════════════════════════════════════════════════════

_DEFAULT_SCHEMA = {
    "seasons": {
        "summer": [6, 7, 8],
        "winter": [11, 12, 1, 2, 3],
        "season_summer_label": "data-矿权-夏季（6-8月）",
        "season_winter_label": "data-矿权-冬季（11-3月）",
    },
    "sensors": [],
}


def _load_schema() -> dict:
    if not SCHEMA_PATH.exists():
        return _DEFAULT_SCHEMA.copy()
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _save_schema(data: dict):
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEMA_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False, indent=2)


@app.route("/api/schema", methods=["GET"])
def get_schema():
    """读取 schema.yaml → JSON"""
    return jsonify(_load_schema())


@app.route("/api/schema", methods=["POST"])
def save_schema():
    """保存 JSON → schema.yaml"""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "无效的JSON"}), 400
        _save_schema(data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# 整理交付文件
# ═══════════════════════════════════════════════════════════════

@app.route("/api/downloads-dirs", methods=["GET"])
def list_downloads_dirs():
    """列出 downloads/ 下的区域子目录"""
    output_dir = request.args.get("output", "./downloads").strip()
    base = (ROOT / output_dir) if not Path(output_dir).is_absolute() else Path(output_dir)
    dirs = []
    if base.exists():
        dirs = [d.name for d in sorted(base.iterdir()) if d.is_dir()]
    return jsonify({"dirs": dirs, "base": str(base)})


# ═══════════════════════════════════════════════════════════════
# 文件夹选择器 (Finder 风格)
# ═══════════════════════════════════════════════════════════════

def _allowed_roots() -> List[Path]:
    """允许浏览的根目录列表 —— 浏览限定在这几个之内,避免误选系统目录"""
    roots: List[Path] = []
    # 1. 项目内 downloads/
    downloads = (ROOT / "downloads").resolve()
    roots.append(downloads)
    # 2. 用户 Downloads
    home_dl = (Path.home() / "Downloads").resolve()
    if home_dl.exists():
        roots.append(home_dl)
    # 3. 已挂载的外接卷 /Volumes/* (macOS)
    # 注意:/Volumes/Macintosh HD 是主盘的软链接,resolve 后是 /,会把整个文件系统纳入 root —
    # 这等于失去"限定"的意义,所以排除掉 (主盘内容用 home_dl 就能覆盖)。
    volumes_root = Path("/Volumes")
    if volumes_root.exists():
        try:
            for vol in sorted(volumes_root.iterdir()):
                if not vol.is_dir() or vol.name.startswith("."):
                    continue
                try:
                    resolved = vol.resolve()
                except OSError:
                    continue
                if resolved == Path("/"):
                    continue  # 主盘软链接,跳过
                # 保留 /Volumes/<name> 而不是 resolved 路径 (让 label 仍然显示卷名)
                roots.append(vol)
        except OSError:
            pass
    return roots


def _root_label(p: Path) -> str:
    """给一个根目录起个用户能看懂的 label"""
    downloads = (ROOT / "downloads").resolve()
    home_dl = (Path.home() / "Downloads").resolve()
    if p == downloads:
        return "下载目录"
    if p == home_dl:
        return "用户 Downloads"
    if str(p).startswith("/Volumes/"):
        return p.name
    return p.name


def _root_kind(p: Path) -> str:
    downloads = (ROOT / "downloads").resolve()
    home_dl = (Path.home() / "Downloads").resolve()
    if p == downloads:
        return "downloads"
    if p == home_dl:
        return "home"
    if str(p).startswith("/Volumes/"):
        return "volume"
    return "other"


def _resolve_within_roots(raw: str):
    """把用户输入的路径解析为允许根之内的绝对路径,返回 (target, root)。
    越界则 abort(403)。raw 可为相对(./downloads)或绝对路径。"""
    if not raw:
        abort(400, "path required")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT / candidate)
    target = candidate.resolve(strict=False)
    for root in _allowed_roots():
        # /Volumes/X 这种根不 resolve (因为 resolve 可能把卷展开到 / 的 mount point);
        # 但 target 是 resolve 过的,这里用 root_resolved 对齐 target 的形态做 relative_to。
        try:
            root_resolved = root.resolve()
        except OSError:
            root_resolved = root
        try:
            target.relative_to(root_resolved)
            return target, root
        except ValueError:
            continue
    abort(403, "path outside allowed roots")


@app.route("/api/folder-roots", methods=["GET"])
def list_folder_roots():
    """返回允许浏览的根列表 (前端 panel 顶部的 chips)"""
    roots = [
        {"label": _root_label(p), "path": str(p), "kind": _root_kind(p)}
        for p in _allowed_roots()
    ]
    return jsonify({"roots": roots})


@app.route("/api/browse-folder", methods=["GET"])
def browse_folder():
    """列出某路径下的子目录 + 返回父目录(仍在 root 之内时)"""
    raw = request.args.get("path", "").strip()
    target, root = _resolve_within_roots(raw)
    dirs = []
    # 目录不存在或不是目录:返回空列表(前端 panel 仍可显示面包屑 + 根 chips,体验更好)
    if target.exists() and target.is_dir():
        try:
            for d in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if d.is_dir() and not d.name.startswith("."):
                    dirs.append({"name": d.name, "path": str(d)})
        except OSError as e:
            abort(403, f"cannot list directory: {e}")
    # 父目录:仅在还在 root 内才返回(root 自己的 parent 应该是 null)
    parent = None
    if target != root:
        try:
            target.parent.relative_to(root)
            parent = str(target.parent)
        except ValueError:
            parent = str(root)  # 越过 root 就回退到 root
    return jsonify({
        "path": str(target),
        "parent": parent,
        "in_root": str(root),
        "dirs": dirs,
    })


def _run_package_thread(task: TaskEntry, raw_area_dir: str, kml_path: str,
                         delivery_root: str, area_label: str, schema: dict):
    """在后台线程中执行整理，将 print 输出推入任务专属日志 buffer"""
    import io, contextlib

    class _QueueWriter(io.TextIOBase):
        def write(self, s):
            if s and s.strip():
                _log_put(task, s.rstrip())
            return len(s)

    try:
        from postprocess.package import package_delivery_from_schema
        writer = _QueueWriter()
        with contextlib.redirect_stdout(writer):
            package_delivery_from_schema(
                raw_area_dir=Path(raw_area_dir),
                kml_path=Path(kml_path),
                delivery_root=Path(delivery_root),
                area_label=area_label,
                schema=schema,
            )
        task.status = "done"
    except Exception as e:
        _log_put(task, f"[错误] 整理失败: {e}")
        task.status = "error"
    finally:
        task.finished_at = time.time()
        _log_put(task, None)


@app.route("/api/package", methods=["POST"])
def run_package():
    """触发整理交付文件任务，返回 task_id"""
    _gc_tasks()

    # 并发检查
    with _tasks_lock:
        running_packages = [t for t in _tasks.values()
                            if t.task_type == "package" and t.status == "running"]
        if len(running_packages) >= MAX_PACKAGE:
            return jsonify({"error": f"已达到最大并发整理数 ({MAX_PACKAGE})"}), 429

    data = request.get_json(force=True) or {}
    area_label   = data.get("area", "").strip()
    kml_path     = data.get("kml_path", "").strip()
    delivery_dir = data.get("delivery_dir", "./delivery").strip()
    output_dir   = data.get("output_dir", "./downloads").strip()

    if not area_label:
        return jsonify({"error": "缺少 area 参数"}), 400
    if not kml_path:
        return jsonify({"error": "缺少 kml_path 参数"}), 400

    base = (ROOT / output_dir) if not Path(output_dir).is_absolute() else Path(output_dir)
    raw_area_dir = str(base / area_label)
    delivery_root = str((ROOT / delivery_dir) if not Path(delivery_dir).is_absolute() else Path(delivery_dir))

    schema = _load_schema()
    task_id = uuid.uuid4().hex[:8]

    entry = TaskEntry(
        task_id=task_id,
        task_type="package",
        label=f"整理: {area_label}",
        kml=kml_path,
        output_dir=output_dir,
        created_at=time.time(),
    )

    _log_put(entry, f"[整理] 区域: {area_label}  KML: {kml_path}")
    _log_put(entry, f"[整理] 原始目录: {raw_area_dir}")
    _log_put(entry, f"[整理] 交付目录: {delivery_root}")
    _log_put(entry, "")

    t = threading.Thread(
        target=_run_package_thread,
        args=(entry, raw_area_dir, kml_path, delivery_root, area_label, schema),
        daemon=True,
    )
    t.start()

    with _tasks_lock:
        _tasks[task_id] = entry

    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/package/status", methods=["GET"])
def package_status():
    """兼容旧接口：返回是否有打包任务在运行"""
    with _tasks_lock:
        running = any(t.task_type == "package" and t.status == "running"
                      for t in _tasks.values())
    return jsonify({"running": running})


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

_bootstrapped = False
_bootstrap_lock = threading.Lock()


def bootstrap():
    """启动初始化:恢复任务/通知列表 + 拉起后台守护线程。幂等。
    dev(__main__)与 gunicorn(wsgi.py)共用 —— gunicorn 不走 __main__,必须显式调用本函数,
    否则任务恢复与 _daily_gc/_async_pending/_delivery_sweep 后台循环不会运行。"""
    global _bootstrapped
    with _bootstrap_lock:
        if _bootstrapped:
            return
        _bootstrapped = True
    _restore_tasks()           # 恢复上次重启前的任务列表
    _restore_notifications()
    threading.Thread(target=_daily_gc, daemon=True, name="daily-gc").start()
    threading.Thread(target=_async_pending_loop, daemon=True, name="async-pending").start()
    threading.Thread(target=_delivery_sweep_loop, daemon=True, name="delivery-sweep").start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print("=" * 50)
    print("  geo-downloader Web UI (开发服务器)")
    print("=" * 50)
    print(f"  项目根目录  : {ROOT}")
    print(f"  配置文件    : {CONFIG_PATH}")
    print(f"  访问地址    : http://localhost:{port}")
    print("  注: 生产请用 gunicorn+gevent 启动(./run_web.sh),开发服务器不适合长连接 SSE")
    print("=" * 50)
    bootstrap()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
