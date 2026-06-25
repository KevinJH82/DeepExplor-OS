#!/usr/bin/env bash
# 启动 geo-portal BFF(默认 8100)
cd "$(dirname "$0")"
export PORTAL_PORT="${PORTAL_PORT:-8100}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORTAL_PORT" --reload
