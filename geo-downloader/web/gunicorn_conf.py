"""
gunicorn 配置 —— geo-downloader Web UI (SSE 稳健化)。

要点:
  - workers = 1: 任务状态(_tasks/_notifications/log_buf)存在进程内存,多 worker 会各持一份
    导致任务列表/SSE/状态不一致。**必须单 worker**;并发由 gevent 协程在该 worker 内承载。
  - worker_class = gevent: 协程模型可同时持有大量长连接(SSE),不再像 Werkzeug dev server
    那样每连接占一个线程、并发下抖动/重置。
  - preload_app = False: 让 bootstrap() 的后台守护线程与下载子进程在 worker 进程内启动
    (preload 会在 master 起线程再 fork,线程不随 fork 存活)。
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 1
worker_class = "gevent"
worker_connections = 1000        # 单 worker 可并发的协程/连接上限(SSE 长连接够用)
timeout = 120                    # gevent 下长 SSE 不触发(worker 心跳独立于请求时长),留余量
graceful_timeout = 30
keepalive = 75                   # 长于浏览器默认,利于 SSE 复用连接
preload_app = False
loglevel = "info"
proc_name = "geo-downloader-web"
