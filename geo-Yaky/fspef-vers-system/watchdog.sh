#!/bin/bash
# FSPEF-VERS 守护进程 — 自动重启 + 心跳检测
# 每隔 30 秒检查一次，进程挂掉自动重启

PROJECT_DIR="/Users/kevin/Desktop/Deep Search/Yakymchuk/fspef-vers-system"
PID_DIR="$PROJECT_DIR/.pids"
LOG_DIR="$PROJECT_DIR/.logs"
WATCHDOG_PID="$PID_DIR/watchdog.pid"
WATCHDOG_LOG="$LOG_DIR/watchdog.log"

mkdir -p "$PID_DIR" "$LOG_DIR"

is_alive() {
    [ -n "$1" ] && kill -0 "$1" 2>/dev/null
}

backend_alive() {
    curl -s --max-time 5 http://127.0.0.1:8000/api/health > /dev/null 2>&1
}

frontend_alive() {
    curl -s --max-time 5 http://localhost:5188/ > /dev/null 2>&1
}

watchdog_loop() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 守护进程启动 (PID: $$)" >> "$WATCHDOG_LOG"

    while true; do
        # Check backend
        if ! backend_alive; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Backend 无响应，重启中..." >> "$WATCHDOG_LOG"
            cd "$PROJECT_DIR/backend"
            source venv/bin/activate
            nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
                >> "$LOG_DIR/backend.log" 2>&1 &
            local bpid=$!
            echo $bpid > "$PID_DIR/backend.pid"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Backend 已重启 (PID: $bpid)" >> "$WATCHDOG_LOG"
            sleep 5
        fi

        # Check frontend
        if ! frontend_alive; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Frontend 无响应，重启中..." >> "$WATCHDOG_LOG"
            cd "$PROJECT_DIR/frontend"
            nohup npx vite --host 0.0.0.0 \
                >> "$LOG_DIR/frontend.log" 2>&1 &
            local fpid=$!
            echo $fpid > "$PID_DIR/frontend.pid"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Frontend 已重启 (PID: $fpid)" >> "$WATCHDOG_LOG"
            sleep 5
        fi

        sleep 30
    done
}

case "${1:-start}" in
    start)
        if [ -f "$WATCHDOG_PID" ]; then
            old_pid=$(cat "$WATCHDOG_PID")
            if is_alive "$old_pid"; then
                echo "守护进程已在运行 (PID: $old_pid)"
                exit 0
            fi
        fi
        echo "启动守护进程..."
        nohup bash "$0" _run >> "$WATCHDOG_LOG" 2>&1 &
        echo $! > "$WATCHDOG_PID"
        echo "守护进程已启动 (PID: $(cat "$WATCHDOG_PID"))"
        ;;
    stop)
        if [ -f "$WATCHDOG_PID" ]; then
            pid=$(cat "$WATCHDOG_PID")
            if is_alive "$pid"; then
                kill "$pid" 2>/dev/null
                echo "守护进程已停止 (PID: $pid)"
            fi
            rm -f "$WATCHDOG_PID"
        else
            echo "守护进程未运行"
        fi
        ;;
    _run)
        watchdog_loop
        ;;
    status)
        if [ -f "$WATCHDOG_PID" ]; then
            pid=$(cat "$WATCHDOG_PID")
            if is_alive "$pid"; then
                echo "守护进程运行中 (PID: $pid)"
            else
                echo "守护进程已停止 (PID 文件过期)"
            fi
        else
            echo "守护进程未启动"
        fi
        echo "日志: $WATCHDOG_LOG"
        tail -5 "$WATCHDOG_LOG" 2>/dev/null
        ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        ;;
esac
