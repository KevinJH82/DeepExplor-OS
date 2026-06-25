#!/bin/bash
# 七个慢变量分析系统 - 启动脚本

cd "$(dirname "$0")"

echo "=== 七个慢变量分析系统 ==="
echo ""

# 启动后端
echo "启动后端服务..."
cd backend
if [ ! -d "venv" ]; then
  echo "  首次运行，安装Python依赖..."
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
else
  source venv/bin/activate
fi

python run.py &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID (http://localhost:8001)"

# 启动前端
echo "启动前端开发服务器..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID (http://localhost:5173)"

echo ""
echo "系统已启动！请在浏览器打开 http://localhost:5173"
echo "按 Ctrl+C 停止所有服务"

# 等待任意进程退出
wait
