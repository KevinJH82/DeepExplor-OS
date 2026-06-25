"""服务自检 + 自启 —— 执行编排单前确保下游服务已就绪,未启动则按既定命令拉起。

背景:子服务常被手动停掉/崩溃(如 geo-downloader 8080 停了 → 数据阶段"准备失败")。
执行前统一健康检查,对未就绪的服务拉起并等待健康,避免编排中途因服务不可达失败。

启动命令以各服务实际运行命令为准(cwd + 入口);python 解释器:服务自带 venv 则用其
venv(如 geo-insar/geo-downloader 依赖 asf_search/hyp3_sdk 等云 SDK,仅在 venv),否则
用系统 python3(共享 rasterio/numpy 地学栈)。可用 env GEO_<SVC>_PYTHON 覆盖。
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request

# 仓库根:.../deepexplor-services(本文件在 geo-orchestrator/core/ 下)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SYS_PY = "/usr/bin/python3"

# service 名(与 service_client 一致) → (端口, 运行目录(相对仓库根), 入口 args, 自带 venv 的根目录|None)
_SPECS = {
    "geo-downloader":  (8080, "geo-downloader",                          ["web/app.py"],                 "geo-downloader"),
    "geo-reporter":    (8081, "geo-reporter",                            ["web/app.py"],                 "geo-reporter"),
    "geo-stru":        (8082, "geo-stru",                                ["run.py"],                     "geo-stru"),
    "geo-exploration": (8083, "geo-exploration/Python_Project/web_app",  ["run.py", "--port", "8083"],   None),
    "geo-insar":       (8084, "geo-insar",                               ["web/app.py"],                 "geo-insar"),
    "data-colle":      (8085, "data-colle/prospector",                   ["web_app.py"],                 None),
    "geo-model3d":     (8086, "geo-model3d",                             ["app.py"],                     None),
    "geo-geophys":     (8087, "geo-geophys",                             ["app.py"],                     None),
    "geo-geochem":     (8088, "geo-geochem",                             ["app.py"],                     None),
    "geo-drill":       (8089, "geo-drill",                               ["app.py"],                     None),
    "geo-analyser":    (5001, "geo-analyser",                            ["app.py"],                     None),
    "geo-7slow":       (8001, "geo-7slow",                               ["start.sh"],                   None),  # shell 脚本自管 env
}


def _python_for(svc: str, venv_root: str | None) -> str:
    env = os.environ.get(f"GEO_{svc.replace('-', '_').upper()}_PYTHON")
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
    except Exception:
        return False


def _start_one(svc: str) -> tuple[bool, str]:
    spec = _SPECS.get(svc)
    if not spec:
        return False, "无启动规格"
    port, cwd_rel, entry, venv_root = spec
    cwd = os.path.join(_REPO_ROOT, cwd_rel)
    if not os.path.isdir(cwd):
        return False, f"运行目录不存在: {cwd}"
    if entry == ["start.sh"]:
        cmd = ["bash", "start.sh"]
    else:
        cmd = [_python_for(svc, venv_root)] + entry
    try:
        logf = open(os.path.join("/tmp", f"{svc}.log"), "ab")
        # start_new_session:脱离编排进程组,编排自身重启/退出不带走已拉起的服务
        subprocess.Popen(cmd, cwd=cwd, stdout=logf, stderr=logf, start_new_session=True)
    except Exception as e:  # noqa: BLE001
        return False, f"拉起失败: {e}"
    for _ in range(50):          # 最多等 ~25s 健康
        time.sleep(0.5)
        if _healthy(port):
            return True, "已启动并就绪"
    return False, "已拉起但健康检查超时(查 /tmp/%s.log)" % svc


def ensure_services_up(services, on_log=None) -> dict:
    """对给定服务列表健康检查;未就绪者拉起并等就绪。返回 {svc: (ok|None, msg)}。

    ok=True 在跑/已拉起就绪;ok=False 拉起失败;ok=None 无启动规格(跳过)。
    不抛异常 —— 单个服务拉不起来只告警,交由编排各自的失败处理/降级。
    """
    def log(msg, level="INFO"):
        if on_log:
            on_log(msg, level)

    result = {}
    seen = []
    for svc in services:
        if svc in seen:
            continue
        seen.append(svc)
        spec = _SPECS.get(svc)
        if not spec:
            log(f"服务自检:{svc} 无启动规格,跳过(仍会按需调用)", "WARN")
            result[svc] = (None, "无启动规格")
            continue
        port = spec[0]
        if _healthy(port):
            result[svc] = (True, "已在运行")
            continue
        log(f"服务自检:{svc}(:{port}) 未启动,正在拉起…", "WARN")
        ok, msg = _start_one(svc)
        result[svc] = (ok, msg)
        log(f"服务自检:{svc} → {'✓ ' if ok else '✗ '}{msg}", "INFO" if ok else "ERROR")
    return result
