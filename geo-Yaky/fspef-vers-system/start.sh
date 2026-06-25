#!/bin/bash
# FSPEF-VERS 频率共振直接勘探分析系统 — 启动/停止脚本
# Usage: ./start.sh [start|stop|restart|status]

export TZ="Asia/Shanghai"

PROJECT_DIR="/Users/kevin/Desktop/Deep Search/Yakymchuk/fspef-vers-system"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
PID_DIR="$PROJECT_DIR/.pids"
LOG_DIR="$PROJECT_DIR/.logs"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

mkdir -p "$PID_DIR" "$LOG_DIR"

is_alive() {
    local pid=$1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start_backend() {
    if [ -f "$BACKEND_PID_FILE" ]; then
        local old_pid=$(cat "$BACKEND_PID_FILE")
        if is_alive "$old_pid"; then
            echo "[Backend] 已在运行 (PID: $old_pid)"
            return 0
        fi
    fi
    echo "[Backend] 启动中..."
    nohup bash -c '
        export TZ="Asia/Shanghai"
        cd "'"$BACKEND_DIR"'"
        source "'"$VENV_DIR"'/bin/activate"
        python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
    ' > "$BACKEND_LOG" 2>&1 &
    local pid=$!
    echo $pid > "$BACKEND_PID_FILE"
    echo "[Backend] 已启动 (PID: $pid, 日志: $BACKEND_LOG)"
}

start_frontend() {
    if [ -f "$FRONTEND_PID_FILE" ]; then
        local old_pid=$(cat "$FRONTEND_PID_FILE")
        if is_alive "$old_pid"; then
            echo "[Frontend] 已在运行 (PID: $old_pid)"
            return 0
        fi
    fi
    echo "[Frontend] 启动中..."
    cd "$FRONTEND_DIR"
    nohup npx vite --host 0.0.0.0 \
        > "$FRONTEND_LOG" 2>&1 &
    local pid=$!
    echo $pid > "$FRONTEND_PID_FILE"
    echo "[Frontend] 已启动 (PID: $pid, 日志: $FRONTEND_LOG)"
}

stop_backend() {
    if [ -f "$BACKEND_PID_FILE" ]; then
        local pid=$(cat "$BACKEND_PID_FILE")
        if is_alive "$pid"; then
            kill "$pid" 2>/dev/null
            sleep 2
            if is_alive "$pid"; then
                kill -9 "$pid" 2>/dev/null
            fi
            echo "[Backend] 已停止 (PID: $pid)"
        else
            echo "[Backend] 进程已不存在"
        fi
        rm -f "$BACKEND_PID_FILE"
    else
        # Fallback: find by port
        local pids=$(lsof -ti:8000 2>/dev/null)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null
            echo "[Backend] 通过端口 8000 清理"
        else
            echo "[Backend] 未运行"
        fi
    fi
}

stop_frontend() {
    if [ -f "$FRONTEND_PID_FILE" ]; then
        local pid=$(cat "$FRONTEND_PID_FILE")
        if is_alive "$pid"; then
            kill "$pid" 2>/dev/null
            sleep 2
            if is_alive "$pid"; then
                kill -9 "$pid" 2>/dev/null
            fi
            echo "[Frontend] 已停止 (PID: $pid)"
        else
            echo "[Frontend] 进程已不存在"
        fi
        rm -f "$FRONTEND_PID_FILE"
    else
        local pids=$(lsof -ti:5188 2>/dev/null)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null
            echo "[Frontend] 通过端口 5188 清理"
        else
            echo "[Frontend] 未运行"
        fi
    fi
}

show_status() {
    echo "========================================="
    echo " FSPEF-VERS 频率共振分析系统 状态"
    echo "========================================="

    # Backend
    if [ -f "$BACKEND_PID_FILE" ]; then
        local bpid=$(cat "$BACKEND_PID_FILE")
        if is_alive "$bpid"; then
            echo "[Backend]  ✓ 运行中 (PID: $bpid)"
            local health=$(curl -s --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null)
            if [ -n "$health" ]; then
                echo "           Health: $health"
            fi
        else
            echo "[Backend]  ✗ 已停止 (PID 文件过期)"
        fi
    else
        echo "[Backend]  ✗ 未启动"
    fi

    # Frontend
    if [ -f "$FRONTEND_PID_FILE" ]; then
        local fpid=$(cat "$FRONTEND_PID_FILE")
        if is_alive "$fpid"; then
            echo "[Frontend] ✓ 运行中 (PID: $fpid)"
            echo "           URL: http://localhost:5188"
        else
            echo "[Frontend] ✗ 已停止 (PID 文件过期)"
        fi
    else
        echo "[Frontend] ✗ 未启动"
    fi

    echo "========================================="
    echo " 前端: http://localhost:5188"
    echo " 后端: http://127.0.0.1:8000"
    echo " API文档: http://127.0.0.1:8000/docs"
    echo "========================================="
}

case "${1:-start}" in
    start)
        start_backend
        sleep 3
        start_frontend
        sleep 3
        show_status
        ;;
    stop)
        stop_frontend
        stop_backend
        ;;
    restart)
        stop_frontend
        stop_backend
        sleep 2
        start_backend
        sleep 3
        start_frontend
        sleep 3
        show_status
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
