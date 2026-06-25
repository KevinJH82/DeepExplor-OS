"""自动补全 DEM —— 交付自检 SAFE 修复回调用。

封装"下载 Copernicus DEM GLO-30 + 裁剪 + 打包到交付季节根 DEM.tif"为一次
`main.py --sensor dem` 子进程调用。DEM 无需账号,幂等(已存在则跳过下载/补包)。

刻意走子进程而非进程内调用:
  - 复用既有、已测的下载+打包流水线(含裁剪/重采样/_package_dem);
  - 与 main.py 收尾自检、web daemon 巡检两处共用同一份逻辑;
  - 强制用 venv 解释器,避免 daemon 跑在系统 python(缺 pyproj)时
    裁剪/重采样静默退化。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = _ROOT / "venv" / "bin" / "python3"


def _python() -> str:
    return str(_VENV_PY) if _VENV_PY.exists() else sys.executable


def fetch_dem_for_area(
    kml_path,
    output_root,
    delivery_root,
    config_path: Optional[str] = None,
    timeout: int = 1800,
) -> bool:
    """对一个区域下载并打包 DEM.tif 到交付季节根。成功返回 True。

    kml_path      : 区域 KML(.kml/.ovkml)
    output_root   : 原始数据根(= 任务 --output;真实写入 output_root/<area>/dem/)
    delivery_root : 交付根(= 任务 --delivery-dir)
    config_path   : 可选 credentials.yaml;DEM 无需认证,缺省也可
    """
    main_py = _ROOT / "main.py"
    if not main_py.exists():
        return False
    argv = [
        _python(), str(main_py),
        "--kml", str(kml_path),
        "--sensor", "dem",
        "--output", str(output_root),
        "--delivery-dir", str(delivery_root),
    ]
    if config_path:
        argv += ["--config", str(config_path)]
    # 标记为 dem 补全子进程,令其收尾自检不再递归触发 dem_fetch
    env = os.environ.copy()
    env["GEO_DEM_FETCH_CHILD"] = "1"
    try:
        r = subprocess.run(
            argv, cwd=str(_ROOT), timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        )
        return r.returncode == 0
    except Exception:
        return False
