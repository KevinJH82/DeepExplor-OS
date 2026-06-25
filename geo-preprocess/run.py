"""geo-preprocess 启动脚本 — 支持 --host/--port/--debug(参照 geo-stru 范式)"""
import argparse

from app import app
from config.config import HOST, PORT, DEBUG


def main():
    ap = argparse.ArgumentParser(description="geo-preprocess 遥感数据预处理服务")
    ap.add_argument("--host", default=HOST, help="监听地址(默认 0.0.0.0)")
    ap.add_argument("--port", type=int, default=PORT, help="端口(默认 5002)")
    ap.add_argument("--debug", action="store_true", default=DEBUG, help="调试模式")
    args = ap.parse_args()
    print(f"  geo-preprocess 数据预处理服务")
    print(f"  Web 界面: http://127.0.0.1:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
