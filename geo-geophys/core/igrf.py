"""IGRF 地磁场参数 —— 按 AOI 形心 + 日期取磁倾角(inclination)/偏角(declination)，供 RTP 化极。

用 ppigrf（轻量、内置系数）；不可用或失败时回退到一个粗略的偶极近似（仅保证可跑）。
"""

from __future__ import annotations

import datetime
from typing import Tuple

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


def _parse_date(date_str: str) -> datetime.datetime:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except Exception:
            continue
    return datetime.datetime(2020, 1, 1)


def _scalar(x) -> float:
    return float(np.asarray(x).ravel()[0])


def igrf_inc_dec(lon: float, lat: float, date_str: str = "2020-01-01",
                 alt_km: float = 0.0) -> Tuple[float, float, str]:
    """返回 (inclination_deg, declination_deg, source)。inc 向下为正。"""
    dt = _parse_date(date_str)
    try:
        import ppigrf
        Be, Bn, Bu = ppigrf.igrf(lon, lat, alt_km, dt)   # 东/北/上 分量(nT)
        Be, Bn, Bu = _scalar(Be), _scalar(Bn), _scalar(Bu)
        H = float(np.hypot(Be, Bn))
        inc = float(np.degrees(np.arctan2(-Bu, H)))      # 下为正 → -Bu
        dec = float(np.degrees(np.arctan2(Be, Bn)))
        return inc, dec, "ppigrf"
    except Exception as e:
        logger.info(f"ppigrf 不可用，回退偶极近似: {e}")
        # 偶极近似：tan(inc) = 2 tan(磁纬度)；偏角取 0
        inc = float(np.degrees(np.arctan2(2.0 * np.tan(np.radians(lat)), 1.0)))
        return inc, 0.0, "dipole_approx"
