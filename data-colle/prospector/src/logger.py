"""Prospector 统一日志系统"""

import logging
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """获取模块级别的 logger"""
    return logging.getLogger(f"prospector.{name}")


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """
    配置根 logger，同时输出到控制台和文件。

    应在 web_app.py 和 prospector.py 启动时调用一次。
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("prospector")
    root.setLevel(level)

    # 避免重复添加 handler（debug 模式下 Flask 会多次初始化）
    if root.handlers:
        return

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件
    fh = logging.FileHandler(log_dir / "prospector.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
