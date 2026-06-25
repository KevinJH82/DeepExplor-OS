#!/bin/bash

# 简化版启动脚本 - 舒曼波共振遥感矿产预测系统 Web 版本

echo "==============================================="
echo "舒曼波共振遥感矿产预测系统 - Web 版本（简化）"
echo "==============================================="
echo

# 进入脚本所在目录
cd "$(dirname "$0")"

# 检查 Python
echo "检查 Python 环境..."
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
elif command -v python &> /dev/null; then
    PYTHON_CMD=python
else
    echo "✗ 未找到 Python，请先安装 Python 3"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
echo "✓ Python 版本: $PYTHON_VERSION"

# 检查 pip
if ! command -v pip3 &> /dev/null && ! command -v pip &> /dev/null; then
    echo "✗ 未找到 pip，请先安装 pip"
    exit 1
fi

# 检查 pip 命令
if command -v pip3 &> /dev/null; then
    PIP_CMD=pip3
else
    PIP_CMD=pip
fi

# 创建虚拟环境
if [[ ! -d "venv" ]]; then
    echo "创建虚拟环境..."
    $PYTHON_CMD -m venv venv
    if [[ $? -ne 0 ]]; then
        echo "✗ 创建虚拟环境失败"
        exit 1
    fi
    echo "✓ 虚拟环境创建成功"
fi

# 激活虚拟环境
echo "激活虚拟环境..."
source venv/bin/activate

# 升级 pip
echo "升级 pip..."
$PIP_CMD install --upgrade pip -q

# 安装基础依赖
echo "安装基础依赖..."
$PIP_CMD install -r requirements_simple.txt -q
if [[ $? -ne 0 ]]; then
    echo "⚠  部分依赖安装失败，尝试继续..."
fi

echo "✓ 依赖安装完成"

# 创建必要的目录
echo "创建必要的目录..."
mkdir -p uploads results logs temp
echo "✓ 目录创建完成"

# 临时创建一个简化的 mineral_engine（如果不存在）
if [[ ! -f "core/mineral_engine.py" ]]; then
    echo "创建模拟引擎..."
    mkdir -p core
    cat > core/mineral_engine.py << 'EOFPY'
"""简化的矿物引擎（用于演示）"""
class MineralEngine:
    def __init__(self):
        pass
    
    def run_analysis(self, config):
        logs = ["开始分析...", "处理中...", "分析完成"]
        results = {
            "result_path": config['out_dir'],
            "files": ["result.png", "result.kmz"]
        }
        return logs, results
EOFPY
    echo "✓ 模拟引擎创建成功"
fi

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
