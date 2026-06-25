#!/usr/bin/env python3
"""
舒曼波共振遥感矿产预测系统 - 启动脚本
"""

import os
import sys
import argparse
from config.config import Config
from app import app

def create_directories():
    """创建必要的目录"""
    Config.create_directories()

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='舒曼波共振遥感矿产预测系统')
    parser.add_argument('--host', default=Config.HOST, help='服务器地址')
    parser.add_argument('--port', type=int, default=Config.PORT, help='服务器端口')
    parser.add_argument('--debug', action='store_true', default=Config.DEBUG, help='调试模式')
    parser.add_argument('--config', help='配置文件路径')
    return parser.parse_args()

def main():
    """主函数"""
    args = parse_args()

    # 创建必要的目录
    create_directories()

    # 打印启动信息
    print("="*60)
    print("舒曼波共振遥感矿产预测系统")
    print("="*60)
    print(f"服务器地址: http://{args.host}:{args.port}")
    print(f"调试模式: {'开启' if args.debug else '关闭'}")
    print("="*60)

    # 启动 Flask 应用
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True
    )

if __name__ == '__main__':
    main()