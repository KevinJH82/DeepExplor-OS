"""geo-preprocess 集中配置(端口与上传上限,可经环境变量覆盖)"""
import os

HOST = os.environ.get("GEO_PREPROCESS_HOST", "0.0.0.0")
PORT = int(os.environ.get("GEO_PREPROCESS_PORT", "5002"))   # 与 geo-analyser(5001)/geo-stru(8082) 错开
DEBUG = os.environ.get("GEO_PREPROCESS_DEBUG", "1") == "1"
MAX_CONTENT_LENGTH = 500 * 1024 * 1024   # 500MB
