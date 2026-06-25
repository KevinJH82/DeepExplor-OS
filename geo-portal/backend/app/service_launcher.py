"""服务自检 + 自启 —— 调用某下游服务前确保它已就绪,未启动则按既定命令拉起。

背景:子服务常被手动停掉/崩溃(如 geo-downloader 8080 停了 → 数据阶段"准备失败")。
BFF 在 `start_service` 等入口调下游前先做健康检查,未就绪则拉起并等就绪,避免"服务不可达"。

启动命令按各服务实际运行命令校准(cwd + 入口);解释器:服务自带 venv 则用其 venv
(geo-downloader/geo-insar 依赖 asf_search/hyp3_sdk 等云 SDK,仅在 venv),否则系统 python3
(共享 rasterio/numpy 地学栈)。端口取自 services 模块(尊重 SVC_*_PORT 覆盖)。
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request

from . import services

# 仓库根:.../deepexplor-services(本文件在 geo-portal/backend/app/ 下,上溯 4 级)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SYS_PY = "/usr/bin/python3"

# BFF 服务名 → (运行目录(相对仓库根), 入口 args, 自带 venv 的根目录|None)。端口取自 services。
_SPECS = {
    "downloader":  ("geo-downloader",                          ["web/app.py"],               "geo-downloader"),
    "reporter":    ("geo-reporter",                            ["web/app.py"],               "geo-reporter"),
    "stru":        ("geo-stru",                                ["run.py"],                   "geo-stru"),
    "exploration": ("geo-exploration/Python_Project/web_app",  ["run.py", "--port", "8083"], None),
    "insar":       ("geo-insar",                               ["web/app.py"],               "geo-insar"),
    "datacolle":   ("data-colle/prospector",                   ["web_app.py"],               None),
    "model3d":     ("geo-model3d",                             ["app.py"],                   None),
    "geophys":     ("geo-geophys",                             ["app.py"],                   None),
    "geochem":     ("geo-geochem",                             ["app.py"],                   None),
    "drill":       ("geo-drill",                               ["app.py"],                   None),
    "analyser":    ("geo-analyser",                            ["app.py"],                   None),
    "preprocess":  ("geo-preprocess",                          ["run.py", "--port", "5002"], None),
    "slowvars":    ("geo-7slow",                               ["start.sh"],                 None),  # shell 自管 env
    "orchestrator": ("geo-orchestrator",                       ["run.py", "--port", "8090"], None),
}


def _port(svc: str):
    try:
        return services._port(svc)
    except Exception:  # noqa: BLE001
        return None


def _python_for(svc: str, venv_root: str | None) -> str:
    env = os.environ.get(f"SVC_{svc.upper()}_PYTHON")
    if env:
        return env
    if venv_root:
        p = os.path.join(_REPO_ROOT, venv_root, "venv", "bin", "python")
        if os.path.exists(p):
            return p
    return _SYS_PY


def _healthy(port: int, timeout: float = 3.0) -> bool:
    """端口有 HTTP 响应即视为在跑(4xx/5xx 也算 —— 服务起来了只是该路径无 handler)。"""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001
        return False


def is_up(svc: str) -> bool:
    port = _port(svc)
    return bool(port) and _healthy(port)


def _start_one(svc: str):
    spec = _SPECS.get(svc)
    port = _port(svc)
    if not spec or not port:
        return False, "无启动规格"
    cwd_rel, entry, venv_root = spec
    cwd = os.path.join(_REPO_ROOT, cwd_rel)
    if not os.path.isdir(cwd):
        return False, f"运行目录不存在: {cwd}"
    cmd = ["bash", "start.sh"] if entry == ["start.sh"] else [_python_for(svc, venv_root)] + entry
    try:
        logf = open(os.path.join("/tmp", f"{svc}.log"), "ab")
        # start_new_session:脱离 BFF 进程组,BFF 重启/退出不带走已拉起的服务
        subprocess.Popen(cmd, cwd=cwd, stdout=logf, stderr=logf, start_new_session=True)
    except Exception as e:  # noqa: BLE001
        return False, f"拉起失败: {e}"
    for _ in range(50):           # 最多等 ~25s 健康
        time.sleep(0.5)
        if _healthy(port):
            return True, "已启动并就绪"
    return False, f"已拉起但健康检查超时(查 /tmp/{svc}.log)"


def ensure_up(svc: str, on_log=None) -> tuple:
    """确保单个服务就绪:在跑→直接返回;未跑→拉起并等就绪。返回 (ok|None, msg),不抛异常。

    ok=True 就绪;ok=False 拉起失败;ok=None 无启动规格(交由调用方按原逻辑处理)。
    """
    def log(msg, level="INFO"):
        if on_log:
            on_log(msg, level)

    if svc not in _SPECS:
        return None, "无启动规格"
    if is_up(svc):
        return True, "已在运行"
    log(f"服务自检:{svc}(:{_port(svc)}) 未启动,正在拉起…", "WARN")
    ok, msg = _start_one(svc)
    log(f"服务自检:{svc} → {'✓ ' if ok else '✗ '}{msg}", "INFO" if ok else "ERROR")
    return ok, msg
