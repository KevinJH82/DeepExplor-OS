#!/usr/bin/env python3
"""geo-geophys 物探处理系统 - 启动脚本"""

import argparse
from config.config import Config
from app import app


def main():
    parser = argparse.ArgumentParser(description='物探(位场处理+ANT接入)系统')
    parser.add_argument('--host', default=Config.HOST)
    parser.add_argument('--port', type=int, default=Config.PORT)
    parser.add_argument('--debug', action='store_true', default=Config.DEBUG)
    args = parser.parse_args()

    Config.create_directories()
    print("=" * 60)
    print("物探处理系统 geo-geophys（位场处理 + ANT 速度体接入）")
    print("=" * 60)
    print(f"服务器地址: http://{args.host}:{args.port}")
    print("=" * 60)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main()
