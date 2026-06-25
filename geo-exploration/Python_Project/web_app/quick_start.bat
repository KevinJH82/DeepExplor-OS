@echo off
REM 快速启动脚本 - 舒曼波共振遥感矿产预测系统

echo ===============================================
echo 舒曼波共振遥感矿产预测系统 - Web 版本
echo ===============================================
echo

REM 检查 Python 版本
echo 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ✗ 未找到 Python，请先安装 Python
    pause
    exit /b 1
)
echo ✓ Python 已安装

REM 检查是否在正确的目录
if not exist "app.py" (
    echo ✗ 请在 web_app 目录下运行此脚本
    pause
    exit /b 1
)

REM 创建虚拟环境（如果不存在）
if not exist "venv" (
    echo 创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ✗ 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo ✓ 虚拟环境创建成功
)

REM 激活虚拟环境
echo 激活虚拟环境...
call venv\Scripts\activate.bat

REM 安装依赖
echo 安装依赖包...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ✗ 安装依赖失败
    pause
    exit /b 1
)
echo ✓ 依赖安装完成

REM 创建必要的目录
echo 创建必要的目录...
if not exist "uploads" mkdir uploads
if not exist "results" mkdir results
if not exist "logs" mkdir logs
if not exist "temp" mkdir temp
echo ✓ 目录创建完成

echo.
echo ===============================================
echo 系统启动完成！
echo ===============================================
echo.
echo 访问地址: http://localhost:8080
echo.
echo 按Ctrl+C停止服务器
echo.

REM 启动 Flask 应用
python run.py

pause