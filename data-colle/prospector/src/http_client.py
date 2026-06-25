"""Prospector HTTP 客户端 — 统一请求、重试、流式下载"""

import time
from pathlib import Path
from typing import Optional, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .logger import get_logger
from config import (
    DEFAULT_TIMEOUT,
    LONG_TIMEOUT,
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_STATUS_CODES,
    DOWNLOAD_CHUNK_SIZE,
)

logger = get_logger("http")


def _build_session() -> requests.Session:
    """创建带自动重试的 requests Session"""
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=list(RETRY_STATUS_CODES),
        allowed_methods=["GET", "POST", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_session = _build_session()


def get(url: str, *, params=None, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> requests.Response:
    """带重试的 GET 请求"""
    logger.debug("GET %s", url)
    resp = _session.get(url, params=params, timeout=timeout, **kwargs)
    logger.debug("GET %s → %d (%.1fs)", url, resp.status_code, resp.elapsed.total_seconds())
    return resp


def post(url: str, *, json=None, data=None, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> requests.Response:
    """带重试的 POST 请求"""
    logger.debug("POST %s", url)
    resp = _session.post(url, json=json, data=data, timeout=timeout, **kwargs)
    logger.debug("POST %s → %d (%.1fs)", url, resp.status_code, resp.elapsed.total_seconds())
    return resp


def download_file(
    url: str,
    output_path: Path,
    *,
    chunk_size: int = DOWNLOAD_CHUNK_SIZE,
    timeout: int = LONG_TIMEOUT,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    流式下载文件，支持进度回调。

    Args:
        url: 下载地址
        output_path: 保存路径
        chunk_size: 分块大小
        timeout: 超时（秒）
        progress_callback: (downloaded_bytes, total_bytes) 回调
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 获取文件大小
    total_size = 0
    try:
        with _session.head(url, allow_redirects=True, timeout=timeout) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
    except Exception:
        pass

    # 仅记录路径末段、去掉 query（避免 API_Key 等敏感参数进日志）
    _name = url.split("?")[0].rstrip("/").split("/")[-1] or "file"
    logger.info("开始下载: %s (%s)",
                _name,
                f"{total_size / 1024 / 1024:.0f} MB" if total_size else "未知大小")

    response = _session.get(url, stream=True, timeout=timeout)
    response.raise_for_status()

    downloaded = 0
    last_report = 0
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=chunk_size):
            f.write(chunk)
            downloaded += len(chunk)

            # 日志：每 50MB 报告一次
            if total_size > 0:
                pct = int(downloaded / total_size * 100)
                if pct >= last_report + 10:
                    last_report = pct
                    logger.info("下载进度: %d%% (%d/%d MB)",
                                pct,
                                downloaded // 1024 // 1024,
                                total_size // 1024 // 1024)

            if progress_callback:
                progress_callback(downloaded, total_size)

    logger.info("下载完成: %s (%d MB)", output_path.name, downloaded // 1024 // 1024)
