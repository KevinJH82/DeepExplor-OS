#!/bin/bash
# 启动卫星数据下载任务（刚果金项目）
set -e
cd /opt/deepexplor-services/geo-downloader
source venv/bin/activate
# credentials.yaml 的 task: 节已经写了所有参数，无需重复传命令行
exec python main.py
