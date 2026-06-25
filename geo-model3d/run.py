#!/usr/bin/env python3
"""geo-model3d 三维地质建模与立体成矿预测系统 - 启动脚本"""

import argparse
from config.config import Config
from app import app


def main():
    parser = argparse.ArgumentParser(description='三维地质建模与立体成矿预测系统')
    parser.add_argument('--host', default=Config.HOST, help='服务器地址')
    parser.add_argument('--port', type=int, default=Config.PORT, help='服务器端口')
    parser.add_argument('--debug', action='store_true', default=Config.DEBUG, help='调试模式')
    args = parser.parse_args()

    Config.create_directories()

    print("=" * 60)
    print("三维地质建模与立体成矿预测系统 (geo-model3d)")
    print("=" * 60)
    print(f"服务器地址: http://{args.host}:{args.port}")
    print("=" * 60)

    # use_reloader=False：禁用文件热重载。否则 debug 重载器会监视经 sys.path 引入的
    # 兄弟仓库(geo-insar/geo-analyser…)，这些上游服务一写文件就会重启本服务、清空内存任务表、
    # 杀掉正在跑的建模任务（表现为进度回退到 0%、结果消失）。生产/长跑务必关闭。
    app.run(host=args.host, port=args.port, debug=args.debug,
            use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
