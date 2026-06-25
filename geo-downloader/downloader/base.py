"""
Base Downloader
所有平台下载器的抽象基类，定义统一接口。
"""

import os
import time
import random
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any
from datetime import datetime

# 下载 stall 检测阈值（秒）：iter_content 超过此时间没有收到任何数据，
# 视为连接挂死（常见于代理半开连接），主动中断并触发重试。
_STALL_TIMEOUT = 120


def _emit_progress_event(sensor_key: Optional[str], **payload):
    """通过 stdout 打印结构化进度事件供 web/app.py:_read_stdout 解析。

    sensor_key=None 时静默(CLI 直跑场景不需要)。
    """
    if not sensor_key:
        return
    import json as _json
    payload["sensor"] = sensor_key
    try:
        print(f"__PROGRESS_EVENT__{_json.dumps(payload, ensure_ascii=False)}", flush=True)
    except Exception:
        pass


def _resolve_proxies(session_or_requests, override):
    """
    统一解析 download_with_* 的 proxies 参数。

    - override != "auto": 调用方显式指定（None 或 dict），原样返回
    - override == "auto" 且 session.trust_env=False: 视为"绕过代理"意图，
      返回空字符串字典（这是 requests 推荐的彻底禁用代理写法，
      会同时覆盖 env、session.proxies）
    - 其余情况: 返回 None，由 requests 走默认行为（读 HTTP_PROXY/HTTPS_PROXY
      环境变量，否则直连）。

    Why: 网络出口已统一切到 OpenVPN（系统级路由），requests 不再需要
    在应用层设代理；保留 trust_env=False 分支是因为 sentinel2/srtm/dem/prisma
    等国内可直连域名仍要显式屏蔽 env 中可能误设的代理变量。
    """
    if override != "auto":
        return override
    if getattr(session_or_requests, "trust_env", True) is False:
        return {"http": "", "https": ""}
    return None


def _iter_content_with_stall_guard(resp, chunk_size, stall_timeout=_STALL_TIMEOUT):
    """
    包装 resp.iter_content，增加 stall 检测。
    如果连续 stall_timeout 秒没有收到新数据，主动关闭连接并抛出 ConnectionError。
    使用后台线程执行实际的 iter_content，主线程带超时地等待每个 chunk。
    """
    import queue
    _SENTINEL = object()

    q = queue.Queue(maxsize=2)

    def _producer():
        try:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                q.put(chunk)
            q.put(_SENTINEL)
        except Exception as e:
            q.put(e)

    t = threading.Thread(target=_producer, daemon=True)
    t.start()
    try:
        while True:
            try:
                item = q.get(timeout=stall_timeout)
            except queue.Empty:
                # stall_timeout 内没有任何数据
                try:
                    resp.close()
                except Exception:
                    pass
                import requests as _req
                raise _req.ConnectionError(
                    f"下载停滞超过 {stall_timeout}s，主动断开重连"
                )
            if item is _SENTINEL:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    except GeneratorExit:
        try:
            resp.close()
        except Exception:
            pass


def download_with_resume(
    session_or_requests,
    url: str,
    dest: Path,
    desc: str = "",
    chunk_size: int = 1024 * 1024,
    timeout: int = 600,
    headers: Optional[Dict] = None,
    proxies: Optional[Dict] = "auto",
) -> Path:
    """
    带断点续传的文件下载工具函数。

    - 下载时写入 <dest>.part 临时文件
    - 若已有 .part 文件，发送 Range 请求从断点继续
    - 下载完成后将 .part 重命名为 dest
    - 若服务端不支持 Range（返回 200 而非 206），则从头重下
    - dest 已存在则直接跳过
    - 网络中断时自动重试（最多10次，指数退避，最长120s）

    Parameters
    ----------
    session_or_requests : requests.Session 或 requests 模块
    url                 : 下载地址
    dest                : 目标文件路径（不含 .part 后缀）
    desc                : tqdm 进度条描述文字
    chunk_size          : 每次读取字节数
    timeout             : 请求超时秒数
    headers             : 额外请求头（如 Authorization）
    """
    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    if dest.exists():
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    req_headers = dict(headers or {})

    max_net_retries = 10
    bar = None

    try:
        for net_attempt in range(max_net_retries):
            try:
                existing_size = part.stat().st_size if part.exists() else 0

                if existing_size > 0:
                    req_headers["Range"] = f"bytes={existing_size}-"
                elif "Range" in req_headers:
                    del req_headers["Range"]

                resp = session_or_requests.get(url, headers=req_headers, stream=True,
                                                timeout=(30, min(timeout, 120)),
                                                proxies=_resolve_proxies(session_or_requests, proxies))

                # 服务端不支持 Range（返回 200），从头重下
                if existing_size > 0 and resp.status_code == 200:
                    existing_size = 0
                    part.unlink(missing_ok=True)

                resp.raise_for_status()

                total = int(resp.headers.get("content-length", 0))
                if existing_size > 0:
                    total += existing_size  # content-length 是剩余字节数

                mode = "ab" if existing_size > 0 else "wb"

                if bar is None and tqdm is not None:
                    bar = tqdm(
                        total=total or None,
                        initial=existing_size,
                        unit="B", unit_scale=True,
                        desc=f"      {desc[:50]}", leave=False,
                    )
                elif bar is not None and total:
                    bar.total = total
                    bar.n = existing_size
                    bar.refresh()

                # tqdm 写 stderr，SSE 子进程抓不到；节流 5MB print 一行到 stdout，
                # 让前端日志面板能看见'续传中'而不是误判'从头开始'
                _LOG_STEP = 5 * 1024 * 1024
                _written_since_log = 0
                _total_written = existing_size
                with open(part, mode) as f:
                    for chunk in _iter_content_with_stall_guard(resp, chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        if bar is not None:
                            bar.update(len(chunk))
                        _total_written += len(chunk)
                        _written_since_log += len(chunk)
                        if _written_since_log >= _LOG_STEP:
                            _mb = _total_written / 1024 / 1024
                            if total > 0:
                                pct = _total_written * 100.0 / total
                                print(f"      [下载进度] {desc[:50]} {_mb:.1f}/{total/1024/1024:.1f} MB ({pct:.1f}%)", flush=True)
                            else:
                                print(f"      [下载进度] {desc[:50]} {_mb:.1f} MB", flush=True)
                            _written_since_log = 0

                # 下载完成后校验文件大小，不符则视为截断，触发重试
                if total > 0 and part.stat().st_size != total:
                    actual = part.stat().st_size
                    part.unlink(missing_ok=True)
                    raise ConnectionError(
                        f"文件大小不符：预期 {total} B，实际 {actual} B，疑似截断"
                    )

                break  # 下载成功，退出重试循环

            except Exception as e:
                # 检查是否是网络相关错误（值得重试）
                import requests as _requests
                import requests.exceptions as _req_exc
                # HTTPError(4xx/5xx) 交给上层处理（如 401 刷 token），不要当作"网络错误"重试同一个过期请求
                if isinstance(e, _requests.HTTPError):
                    raise
                _net_errors = (
                    _requests.ConnectionError,
                    _requests.Timeout,
                    _req_exc.ChunkedEncodingError,
                    ConnectionResetError,
                    TimeoutError,
                )
                is_net_err = isinstance(e, _net_errors)
                # 也捕获底层 OSError/IOError（如 Broken pipe）
                if not is_net_err and isinstance(e, OSError):
                    is_net_err = True

                if is_net_err and net_attempt < max_net_retries - 1:
                    wait = min(2 ** net_attempt + random.uniform(0, 1), 120)
                    print(f"      [重试 {net_attempt + 1}/{max_net_retries}] 网络中断，{wait:.0f}s 后重连... ({e})")
                    time.sleep(wait)
                else:
                    raise
    finally:
        if bar is not None:
            bar.close()

    part.rename(dest)
    return dest


def download_with_chunks(
    session_or_requests,
    url: str,
    dest: Path,
    desc: str = "",
    n_workers: int = 8,
    min_size: int = 1 * 1024 * 1024,   # 小于 1MB 退化为单线程
    chunk_size: int = 1024 * 1024,
    timeout: int = 600,
    headers: Optional[Dict] = None,
    proxies: Optional[Dict] = "auto",
) -> Path:
    """
    多线程分块并行下载（仅适用于支持 Range 头的 HTTP 服务器）。

    - 先用 HEAD 请求获取文件大小并确认 Range 支持
    - 文件 < min_size 或服务器不支持 Range → 退化为 download_with_resume()
    - 否则将文件切分为 n_workers 块，每块并行下载到 .part.N 临时文件
    - 全部完成后按顺序合并，删除临时块文件
    - 断点续传：.part.N 已存在且大小正确则跳过该块

    proxies: "auto" 走 requests 默认行为（OpenVPN 系统级路由处理出口）；
             传 dict（如 {"http":"","https":""}）显式禁用代理，用于国内可直连
             的域名（如 Copernicus）。
    """
    if dest.exists():
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    req_headers = dict(headers or {})
    _resolved_proxies = _resolve_proxies(session_or_requests, proxies)

    # HEAD 请求探测文件大小和 Range 支持
    try:
        head = session_or_requests.head(url, headers=req_headers, timeout=(30, 30),
                                         allow_redirects=True, proxies=_resolved_proxies)
        total_size = int(head.headers.get("content-length", 0))
        accepts_ranges = head.headers.get("accept-ranges", "").lower() == "bytes"
    except Exception:
        total_size = 0
        accepts_ranges = False

    # 不满足分块条件时退化
    if not accepts_ranges or total_size < min_size:
        return download_with_resume(
            session_or_requests, url, dest,
            desc=desc, chunk_size=chunk_size, timeout=timeout, headers=headers,
            proxies=proxies,
        )

    import threading

    block = total_size // n_workers
    ranges = []
    for i in range(n_workers):
        start = i * block
        end = (start + block - 1) if i < n_workers - 1 else (total_size - 1)
        ranges.append((i, start, end))

    errors = []
    part_files = [dest.with_suffix(dest.suffix + f".part.{i}") for i in range(n_workers)]

    def _dl_chunk(idx: int, byte_start: int, byte_end: int):
        import time, random
        part = part_files[idx]
        expected = byte_end - byte_start + 1
        if part.exists() and part.stat().st_size == expected:
            return  # 该块已完整，跳过
        chunk_headers = dict(req_headers)
        chunk_headers["Range"] = f"bytes={byte_start}-{byte_end}"
        # (connect_timeout, read_timeout) — read_timeout 控制每次 socket 读取
        # 的最长等待，防止 iter_content 在 Azure/S3 限速时永久挂住
        req_timeout = (30, min(timeout, 120))
        max_retries = 5
        for attempt in range(max_retries):
            try:
                resp = session_or_requests.get(url, headers=chunk_headers,
                                               stream=True, timeout=req_timeout,
                                               proxies=_resolved_proxies)
                resp.raise_for_status()
                with open(part, "wb") as f:
                    for data in _iter_content_with_stall_guard(resp, chunk_size):
                        if data:
                            f.write(data)
                # 校验分块大小，不匹配视为损坏，重试
                if part.stat().st_size != expected:
                    raise RuntimeError(
                        f"块大小不匹配: 期望 {expected} 字节，实际 {part.stat().st_size} 字节"
                    )
                return  # 成功
            except Exception as e:
                part.unlink(missing_ok=True)
                if attempt < max_retries - 1:
                    wait = 2 ** attempt + random.uniform(0, 1)
                    time.sleep(wait)
                else:
                    errors.append(f"块{idx}: {e}")

    threads = [
        threading.Thread(target=_dl_chunk, args=(i, s, e), daemon=True)
        for i, s, e in ranges
    ]
    print(f"      {desc[:50]} → 分 {n_workers} 块并行下载 ({total_size/1024/1024:.1f} MB)")
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if errors:
        for p in part_files:
            p.unlink(missing_ok=True)
        raise RuntimeError(f"分块下载失败: {'; '.join(errors)}")

    # 合并
    with open(dest, "wb") as out:
        for part in part_files:
            with open(part, "rb") as f:
                while True:
                    buf = f.read(65536)
                    if not buf:
                        break
                    out.write(buf)
            part.unlink()

    return dest


class BaseDownloader(ABC):
    """
    所有平台下载器的基类。

    子类必须实现：
    - search()：搜索符合条件的影像列表
    - download()：下载指定影像到本地
    """

    # 子类设置此属性标识平台名称
    PLATFORM_NAME: str = "unknown"
    # 是否需要账号认证
    REQUIRES_AUTH: bool = True

    def __init__(
        self,
        credentials: Optional[Dict[str, str]] = None,
        output_dir: str = "./downloads",
    ):
        self.credentials = credentials or {}
        self.output_dir = Path(output_dir)

    def get_save_dir(self, area_name: str) -> Path:
        """返回指定区域、指定平台的存储目录，并自动创建。"""
        d = self.output_dir / area_name / self.PLATFORM_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    @abstractmethod
    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        **kwargs,
    ) -> List[Any]:
        """
        搜索符合条件的影像。

        Parameters
        ----------
        bbox : (min_lon, min_lat, max_lon, max_lat)
        start_date : 'YYYY-MM-DD'
        end_date   : 'YYYY-MM-DD'
        cloud_cover : 最大云量百分比（仅光学影像有效）

        Returns
        -------
        list：搜索结果条目列表（各平台格式不同）
        """

    @abstractmethod
    def download(
        self,
        search_results: List[Any],
        save_dir: Path,
        max_items: int = 10,
        **kwargs,
    ) -> List[Path]:
        """
        下载搜索结果到本地。

        Parameters
        ----------
        search_results : search() 的返回值
        save_dir : 存储目录
        max_items : 最多下载景数

        Returns
        -------
        list of Path：已下载的文件路径列表
        """

    def run(
        self,
        bbox: Tuple[float, float, float, float],
        geometry,
        area_name: str,
        start_date: str,
        end_date: str,
        cloud_cover: int = 20,
        max_items: int = 10,
        clip: bool = True,
        sensor_key: Optional[str] = None,
        **kwargs,
    ) -> List[Path]:
        """
        完整流程：搜索 → 下载 → (可选)裁剪

        Parameters
        ----------
        geometry   : Shapely几何体，用于裁剪
        clip       : 是否裁剪到KML范围
        sensor_key : main.py 用的 sensor 标识(sentinel2/landsat/...);非 None
                     时会通过 __PROGRESS_EVENT__ 协议向 web 后端汇报进度
        """
        save_dir = self.get_save_dir(area_name)

        # 拼接/选片优化开关（从 kwargs 取出，避免透传到 search/download）
        same_period_days = kwargs.pop("same_period_days", 30)
        rrn = bool(kwargs.pop("radiometric_normalize", False))

        print(f"\n[{self.PLATFORM_NAME}] 搜索影像...")
        print(f"  区域: {area_name} | 范围: {bbox}")
        print(f"  时间: {start_date} ~ {end_date}")

        # ── 分块搜索:MultiPolygon 且各块隔得很远时,bbox 包络框可能比真实数据
        # 面积大几个数量级,STAC bbox 排序会返回中心区景,把所有 polygon 全错过。
        # 检测:MultiPolygon + bbox 面积 > 5 × sum(parts area) → 对每块独立 search 合并。
        # 触发实例:辽矿两测试区块 KML 包含 2 个分隔 ~270km 的矿权地。
        def _bbox_area(b):
            return max(0.0, (b[2]-b[0])) * max(0.0, (b[3]-b[1]))

        def _result_dedup_key(item):
            # 各 sensor 的 search() 返回 dict,字段名不一,试常见 id 字段;
            # 不是 dict 或都没有就 fallback repr
            if isinstance(item, dict):
                for k in ('id', 'Id', 'name', 'Name', 'title', 'product_id'):
                    v = item.get(k)
                    if v:
                        return (k, v)
            return ('repr', repr(item))

        per_part_bboxes = None
        try:
            if geometry is not None and geometry.geom_type == "MultiPolygon":
                parts = list(geometry.geoms)
                if len(parts) > 1:
                    full_area = _bbox_area(bbox)
                    parts_area = sum(_bbox_area(p.bounds) for p in parts)
                    if parts_area > 0 and full_area > parts_area * 5:
                        per_part_bboxes = [tuple(p.bounds) for p in parts]
                        print(f"  [分块搜索] MultiPolygon 各块隔得远(包络框/各块和={full_area/parts_area:.1f}x),"
                              f"按 {len(per_part_bboxes)} 块独立搜索")
        except Exception as _e:
            print(f"  [分块搜索] 检测失败,回退整块 bbox: {_e}")
            per_part_bboxes = None

        if per_part_bboxes:
            results = []
            seen = set()
            for i, sub_bbox in enumerate(per_part_bboxes, 1):
                print(f"  [分块 {i}/{len(per_part_bboxes)}] bbox={sub_bbox}")
                try:
                    sub = self.search(sub_bbox, start_date, end_date, cloud_cover, **kwargs)
                except Exception as _e:
                    print(f"    [警告] 分块 {i} 搜索失败: {_e}")
                    continue
                if not sub:
                    continue
                # ASTER tuple 结构特殊处理:按 prod_key 合并 granule 列表
                if sub and isinstance(sub[0], tuple) and len(sub[0]) == 2:
                    # results 也用 tuple 累加;同 prod_key 合并 granules 去重
                    existing = dict(results)  # prod_key -> granule list
                    for prod_key, granules in sub:
                        keep = existing.get(prod_key, [])
                        for g in granules:
                            k = _result_dedup_key(g)
                            if k not in seen:
                                seen.add(k)
                                keep.append(g)
                        existing[prod_key] = keep
                    results = list(existing.items())
                else:
                    for it in sub:
                        k = _result_dedup_key(it)
                        if k not in seen:
                            seen.add(k)
                            results.append(it)
            print(f"  [分块搜索] 合并去重后共 {len(results) if not (results and isinstance(results[0], tuple)) else sum(len(g) for _,g in results)} 景")
        else:
            results = self.search(bbox, start_date, end_date, cloud_cover, **kwargs)

        if not results:
            print(f"  [!] 未找到符合条件的影像")
            _emit_progress_event(sensor_key, phase="search", target=0)
            return []

        # ── 覆盖选景：大面积区域自动选择能完整覆盖的最少景数 ───────
        if geometry is not None and len(results) > 1:
            try:
                from postprocess.mosaic import select_covering_scenes
                # ASTER 返回 [(prod_key, granule_list), ...] 结构，需要特殊处理
                if results and isinstance(results[0], tuple) and len(results[0]) == 2:
                    # 对每个产品组独立做覆盖选景
                    new_results = []
                    for prod_key, granules in results:
                        if len(granules) > 1:
                            selected = select_covering_scenes(granules, geometry, max_scenes=max_items * 3,
                                                              same_period_days=same_period_days)
                            new_results.append((prod_key, selected))
                        else:
                            new_results.append((prod_key, granules))
                    results = new_results
                else:
                    selected = select_covering_scenes(results, geometry, max_scenes=max_items * 3,
                                                      same_period_days=same_period_days)
                    if selected:
                        max_items = max(max_items, len(selected))
                        results = selected
            except Exception as e:
                print(f"  [覆盖选景] 跳过（{e}），使用原始搜索结果")

        print(f"  找到 {len(results)} 景，开始下载（最多 {max_items} 景）...")
        # ASTER 等 results 是 [(prod_key, granules), ...] 的结构,target 不能直接用 len(results),
        # 改用 max_items 上限 + 实际可下载景数最小值;非元组结构走常规路径
        try:
            if results and isinstance(results[0], tuple) and len(results[0]) == 2:
                _total = sum(len(g) for _, g in results)
            else:
                _total = len(results)
        except Exception:
            _total = len(results)
        _emit_progress_event(sensor_key, phase="search", target=min(_total, max_items))
        downloaded = self.download(results, save_dir, max_items, **kwargs)

        # 记录本次下载结果（search 无景时不到此处，不污染统计）
        try:
            from downloader.stats import record as _stats_record
            _stats_record(self.PLATFORM_NAME, success=len(downloaded) > 0)
        except Exception:
            pass

        if clip and downloaded and geometry is not None:
            from postprocess.clip import clip_to_geometry
            from postprocess.mosaic import covers_geometry, mosaic_and_clip, mosaic_sentinel2_zips

            # ── 裁剪前验证：删除损坏文件，重下缺失 ─────────────────
            def _is_readable_tif(f):
                if f.suffix.lower() not in (".tif", ".tiff") or not f.exists():
                    return True  # 非 TIF 不检查
                try:
                    import rasterio
                    with rasterio.open(f) as src:
                        src.read(1, window=((0, min(1, src.height)), (0, min(1, src.width))))
                    return True
                except Exception:
                    return False

            bad_files = [f for f in downloaded if not _is_readable_tif(f)]
            if bad_files:
                print(f"  [修复] 检测到 {len(bad_files)} 个损坏文件，删除后重新下载...")
                for f in bad_files:
                    print(f"    删除损坏: {f.name}")
                    f.unlink(missing_ok=True)
                    downloaded.remove(f)
                # 重新下载（已存在的文件会被跳过，只补缺失的）
                redownloaded = self.download(results, save_dir, max_items, **kwargs)
                for f in redownloaded:
                    if f not in downloaded:
                        downloaded.append(f)

            print(f"  裁剪影像到KML范围...")

            # 按文件类型分组
            tif_files = [f for f in downloaded if f.suffix.lower() in (".tif", ".tiff") and f.exists()]
            zip_files = [f for f in downloaded if f.suffix.lower() == ".zip" and f.exists()]
            others    = [f for f in downloaded if f not in tif_files and f not in zip_files and f.exists()]

            result = []

            # ── GeoTIFF 多景：按波段分组后检测覆盖，按需拼接 ──────────
            if len(tif_files) > 1:
                from postprocess.mosaic import _extract_band_key
                # 按波段键分组（同一波段的多景放一组，不同波段各自独立）
                band_groups: dict = {}
                for f in tif_files:
                    bk = _extract_band_key(f)
                    band_groups.setdefault(bk, []).append(f)

                if len(band_groups) > 1:
                    # 多波段传感器（如 ASTER）：每个波段独立处理，避免混合拼接
                    print(f"  [多波段] 检测到 {len(band_groups)} 个波段组，按波段独立处理...")
                    for bk, files in sorted(band_groups.items()):
                        if len(files) > 1 and not any(covers_geometry(f, geometry) for f in files):
                            out_path = save_dir / f"{area_name}_mosaic_{bk}.tif"
                            try:
                                merged = mosaic_and_clip(files, geometry, out_path, rrn=rrn)
                                print(f"  [完成] 波段 {bk} 拼接裁剪: {merged.name}")
                                result.append(merged)
                            except Exception as e:
                                print(f"  [警告] 波段 {bk} 拼接失败，回退逐景裁剪: {e}")
                                for f in files:
                                    try:
                                        out = clip_to_geometry(f, geometry)
                                        if out.exists():
                                            result.append(out)
                                    except Exception as ce:
                                        print(f"  [警告] 裁剪失败 {f.name}: {ce}")
                                        if f.exists():
                                            result.append(f)
                        else:
                            for f in files:
                                try:
                                    out = clip_to_geometry(f, geometry)
                                    if out.exists():
                                        result.append(out)
                                except Exception as e:
                                    print(f"  [警告] 裁剪失败 {f.name}: {e}")
                                    if f.exists():
                                        result.append(f)
                elif not any(covers_geometry(f, geometry) for f in tif_files):
                    # 单波段多景：整体拼接
                    print(f"  [拼接] 检测到 {len(tif_files)} 景需拼接才能完整覆盖研究区，开始合并...")
                    out_path = save_dir / f"{area_name}_mosaic.tif"
                    try:
                        merged = mosaic_and_clip(tif_files, geometry, out_path, rrn=rrn)
                        print(f"  [完成] 拼接裁剪: {merged.name}")
                        result.append(merged)
                    except Exception as e:
                        print(f"  [警告] 拼接失败，回退逐景裁剪: {e}")
                        for f in tif_files:
                            try:
                                out = clip_to_geometry(f, geometry)
                                if out.exists():
                                    result.append(out)
                            except Exception as ce:
                                print(f"  [警告] 裁剪失败 {f.name}: {ce}")
                                if f.exists():
                                    result.append(f)
                else:
                    # 已有完整覆盖：逐景裁剪
                    for f in tif_files:
                        try:
                            out = clip_to_geometry(f, geometry)
                            if out.exists():
                                result.append(out)
                        except Exception as e:
                            print(f"  [警告] 裁剪失败 {f.name}: {e}")
                            if f.exists():
                                result.append(f)
            else:
                # 单景：直接裁剪
                for f in tif_files:
                    try:
                        out = clip_to_geometry(f, geometry)
                        if out.exists():
                            result.append(out)
                    except Exception as e:
                        print(f"  [警告] 裁剪失败 {f.name}: {e}")
                        if f.exists():
                            result.append(f)

            # ── ZIP 多景（Sentinel-2）：按波段分组拼接 ─────────────────
            if len(zip_files) > 1:
                print(f"  [拼接] 检测到 {len(zip_files)} 景 ZIP，按波段分组拼接...")
                try:
                    result.extend(mosaic_sentinel2_zips(zip_files, geometry, save_dir, rrn=rrn))
                except Exception as e:
                    print(f"  [警告] ZIP拼接失败，回退逐景裁剪: {e}")
                    for f in zip_files:
                        try:
                            out = clip_to_geometry(f, geometry)
                            if out.exists():
                                result.append(out)
                        except Exception as ce:
                            print(f"  [警告] 裁剪失败 {f.name}: {ce}")
                            if f.exists():
                                result.append(f)
            elif len(zip_files) == 1:
                try:
                    out = clip_to_geometry(zip_files[0], geometry)
                    if out.exists():
                        result.append(out)
                except Exception as e:
                    print(f"  [警告] 裁剪失败 {zip_files[0].name}: {e}")
                    if zip_files[0].exists():
                        result.append(zip_files[0])

            # ── 其他格式（.nc / .h5 等）：原有逐景处理 ────────────────
            for f in others:
                try:
                    out = clip_to_geometry(f, geometry)
                    if out.exists():
                        result.append(out)
                except Exception as e:
                    print(f"  [警告] 裁剪失败 {f.name}: {e}")
                    if f.exists():
                        result.append(f)

            return [r for r in result if r and r.exists()]

        return downloaded

    @staticmethod
    def _validate_date(date_str: str) -> str:
        """验证日期格式"""
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            raise ValueError(f"日期格式错误: '{date_str}'，请使用 YYYY-MM-DD 格式")
