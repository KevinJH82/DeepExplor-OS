"""
geo-downloader Web UI 的 WSGI 入口 —— 供 gunicorn + gevent 使用。

为什么需要它(而非直接 gunicorn 指向 app:app):
  1) gevent 必须在导入任何使用 threading/socket/subprocess 的模块**之前** monkey-patch,
     否则 app.py 里的 threading.Event/Lock、subprocess、socket 不会变成协程友好,SSE 长连接
     仍会相互阻塞。故本文件第一件事就是 monkey.patch_all()。
  2) app.py 的任务恢复与后台守护线程原本只在 __main__ 里启动;gunicorn 走 import 不走 __main__,
     必须显式调用 app.bootstrap()。

部署: 单 worker(任务状态在进程内存,见 gunicorn_conf.py) + gevent 多协程承载并发 SSE。
"""

from gevent import monkey
monkey.patch_all()  # 必须最先执行,早于下面对 app(及其 threading/subprocess)的导入

import os
import sys
from pathlib import Path

# 让 'app' 可作为顶层模块导入(本文件与 app.py 同在 web/ 目录)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as _app  # noqa: E402  (必须在 monkey.patch_all 之后)

_app.bootstrap()           # 恢复任务/通知 + 启动后台线程(gunicorn 不走 __main__)
application = _app.app      # gunicorn 入口: wsgi:application
