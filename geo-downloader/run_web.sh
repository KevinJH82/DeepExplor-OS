#!/bin/bash
# geo-downloader Web UI 启动 —— gunicorn + gevent(单 worker 多协程,稳健承载 SSE 长连接)。
# 端口: 环境变量 PORT(默认 8080)。线上 192.168.112.57 用 8086: PORT=8086 ./run_web.sh
# 解释器: 有 venv 用 venv,否则用系统 python3(如该机无 venv、依赖装在系统 python)。
#         用 `python -m gunicorn` 而非裸 gunicorn,避免 PATH 上找不到 gunicorn 脚本。
# 开发临时调试可改用: python3 web/app.py(Werkzeug 开发服务器,不适合长连接 SSE)
set -e
cd "$(dirname "$0")"
if [ -x venv/bin/python ]; then
  PY="$(pwd)/venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
fi
export PORT="${PORT:-8080}"
cd web
exec "$PY" -m gunicorn -c gunicorn_conf.py wsgi:application
