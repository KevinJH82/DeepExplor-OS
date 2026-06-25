"""
下载统计模块
记录每个传感器的下载成功/失败次数，用于任务启动时按成功率排序。

持久化文件：config/download_stats.json
数据格式：
  {
    "sentinel2": {"attempts": 10, "success": 9, "rate": 0.9},
    ...
  }

- attempts：搜到景且进入下载的次数（search 返回空不计）
- success：下载后返回 ≥1 个文件的次数
- rate：success / attempts（新传感器默认 0.5，排在中间位置）
"""

import json
import threading
from pathlib import Path

_STATS_PATH = Path(__file__).parent.parent / "config" / "download_stats.json"
_lock = threading.Lock()
_DEFAULT_RATE = 0.5  # 无历史数据时的默认成功率


def load_stats() -> dict:
    """读取统计文件，失败时返回空字典。"""
    if _STATS_PATH.exists():
        try:
            return json.loads(_STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_stats(stats: dict):
    """写入统计文件。"""
    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATS_PATH.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def record(sensor: str, success: bool):
    """
    记录一次传感器下载结果（线程安全）。

    Parameters
    ----------
    sensor  : 传感器键名（与 SENSOR_MAP / PLATFORM_NAME 一致）
    success : True=下载到 ≥1 个文件；False=下载失败或 0 文件
    """
    with _lock:
        stats = load_stats()
        s = stats.setdefault(
            sensor, {"attempts": 0, "success": 0, "rate": _DEFAULT_RATE}
        )
        s["attempts"] += 1
        if success:
            s["success"] += 1
        s["rate"] = round(s["success"] / s["attempts"], 4)
        save_stats(stats)


def sort_by_rate(sensors: list) -> list:
    """
    按历史成功率降序排列传感器列表（成功率高的先执行）。
    无历史数据的传感器默认成功率 0.5，排在已知传感器中间。
    成功率相同时保持原始顺序（stable sort）。
    """
    stats = load_stats()
    return sorted(
        sensors,
        key=lambda s: stats.get(s, {}).get("rate", _DEFAULT_RATE),
        reverse=True,
    )


def get_stats() -> dict:
    """返回所有传感器的统计数据（供 API 使用）。"""
    return load_stats()
