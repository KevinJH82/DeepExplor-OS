#!/bin/bash

# 快速启动脚本 - 舒曼波共振遥感矿产预测系统

echo "==============================================="
echo "舒曼波共振遥感矿产预测系统 - Web 版本"
echo "==============================================="
echo

# 检查 Python 版本
echo "检查 Python 环境..."
python_version=$(python3 --version 2>&1)
if [[ $? -eq 0 ]]; then
    echo "✓ Python 版本: $python_version"
else
    echo "✗ 未找到 Python 3，请先安装 Python 3"
    exit 1
fi

# 检查是否在正确的目录
if [[ ! -f "app.py" ]]; then
    echo "✗ 请在 web_app 目录下运行此脚本"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [[ ! -d "venv" ]]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
    if [[ $? -ne 0 ]]; then
        echo "✗ 创建虚拟环境失败"
        exit 1
    fi
    echo "✓ 虚拟环境创建成功"
fi

# 激活虚拟环境
echo "激活虚拟环境..."
source venv/bin/activate

# 安装依赖
echo "安装依赖包..."
pip install -r requirements.txt
if [[ $? -ne 0 ]]; then
    echo "✗ 安装依赖失败"
    exit 1
fi
echo "✓ 依赖安装完成"

# 创建必要的目录
echo "创建必要的目录..."
mkdir -p uploads results logs temp
echo "✓ 目录创建完成"

# 设置环境变量
export FLASK_APP=run.py
export FLASK_ENV=development

echo
echo "==============================================="
echo "系统启动完成！"
echo "==============================================="
echo
echo "访问地址: http://localhost:8080"
echo
echo "按 Ctrl+C 停止服务器"
echo

# 启动 Flask 应用
python run.py