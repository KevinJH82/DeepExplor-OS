#!/bin/bash
# geo-downloader Web UI 启动 —— gunicorn + gevent(单 worker 多协程,稳健承载 SSE 长连接)。
# 端口: 环境变量 PORT(默认 8080)。线上如用 8090: PORT=8090 ./run_web.sh
# 开发临时调试可改用: python web/app.py(Werkzeug 开发服务器,不适合长连接 SSE)
set -e
cd "$(dirname "$0")"
[ -d venv ] && source venv/bin/activate
export PORT="${PORT:-8080}"
cd web
exec gunicorn -c gunicorn_conf.py wsgi:application
