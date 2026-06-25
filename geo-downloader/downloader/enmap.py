"""
EnMAP Downloader — DLR EOWEB GeoPortal（自动化版本）

实现方案：CAS SSO 认证 + Playwright 无头浏览器操作 EOWEB
  - CAS 登录：纯 HTTP（requests.Session）或 Playwright 完成
  - 场景搜索：Playwright 登录 EOWEB → 选择 EnMAP collection → 设置 bbox/时间
             → 点击 Search → 拦截 GWT-RPC getRecords 响应提取场景 ID
  - 下单：Playwright 选择场景 → 点击购物车图标 → Cart 结算
  - 轮询：requests.Session 轮询 getOrderSummaries GWT-RPC
  - 下载：订单就绪后 download_with_resume

产品：EnMAP L0/L2A（高光谱，244波段，30m）
平台：DLR EOWEB GeoPortal（https://eoweb.dlr.de/egp/）
账号：dlr_eoweb（credentials.yaml）

依赖：
  pip install playwright
  playwright install chromium
"""

import re
import time
import json
import random
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Any
from datetime import datetime, timedelta

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from .base import BaseDownloader, download_with_resume

# ── 端点常量 ──────────────────────────────────────────────────────────────────
_CAS_LOGIN      = "https://sso.eoc.dlr.de/eoc/auth/login"
_EOWEB_SERVICE  = "https://eoweb.dlr.de/egp/login/cas"
_EOWEB_MAIN     = "https://eoweb.dlr.de/egp/main"
_EOWEB_HANDLER  = "https://eoweb.dlr.de/egp/handler/"

# GWT-RPC 服务哈希（每次 EOWEB 部署更新）
# 通过 page.content() 中 home/HASH.cache.js 文件名提取
_GWT_HASH_SEARCH = "98DD4B11FAE780F63E6BE4B9B9E78762"
_GWT_HASH_ORDER  = "DC54AE017BF9BF744DCCAEEA3B1D9E9B"
_GWT_BASE        = "https://eoweb.dlr.de/egp/home/"

# FTPS 数据连接的 recv 空闲超时(秒)。DLR 限速服务器在半死状态会"涓流"
# (每隔很久才吐几个字节),旧的 1800s 太宽——一个卡死下载能拖住 daemon 一个多小时
# 才超时。180s 内毫无数据即判该连接已死,交给上层 REST 续传重连。
_FTPS_IDLE_TIMEOUT = 180

# EnMAP collection IDs（EOWEB 内部 URN）
_ENMAP_COLLECTION_L0  = "urn:eop:DLR:EOWEB:ENMAP.HSI.L0"
_ENMAP_COLLECTION_LQ  = "urn:eop:DLR:EOWEB:ENMAP.HSI.L0-Low-Quality"


class EnMAPDownloader(BaseDownloader):
    """
    EnMAP 自动化下载器（EOWEB GeoPortal）。

    search()   → Playwright 操作 EOWEB UI，拦截 GWT-RPC getRecords 响应，
                 提取 EnMAP 场景 ID 列表
    download() → Playwright 在 UI 中选中场景 → 加购物车 → 提交订单
                 → requests 轮询 getOrderSummaries → 订单就绪后下载
    """

    PLATFORM_NAME = "enmap"
    REQUIRES_AUTH = True

    def __init__(
        self,
        credentials: Dict[str, str],
        output_dir: str = "./downloads",
        headless: bool = True,
        **kwargs,
    ):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._username = credentials.get("username", "")
        self._password = credentials.get("password", "")
        self._headless = headless
        self._logger: Optional[logging.Logger] = None
        self._log_dir: Optional[Path] = None
        self._gwt_hash_order: Optional[str] = None  # 动态从页面抓取，覆盖硬编码常量

    # ── 日志系统 ─────────────────────────────────────────────────────────────

    def _setup_logger(self, save_dir: Path):
        """初始化文件日志，写入 {save_dir}/enmap_debug.log"""
        self._log_dir = save_dir
        self._logger = logging.getLogger(f"enmap.{id(self)}")
        self._logger.setLevel(logging.DEBUG)
        # 移除旧 handler 防止重复
        self._logger.handlers.clear()
        log_file = save_dir / "enmap_debug.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self._logger.addHandler(fh)
        self._logger.info("=" * 60)
        self._logger.info("EnMAP 日志初始化  save_dir=%s", save_dir)

    def _log(self, msg: str, level: str = "info", also_print: bool = True):
        """同时写日志文件和 print 到 stdout"""
        if also_print:
            print(msg)
        if self._logger:
            clean = msg.strip()
            getattr(self._logger, level, self._logger.info)(clean)

    def _save_screenshot(self, page, name: str) -> Optional[Path]:
        """截图保存到 _log_dir/{name}.png，返回路径"""
        if not self._log_dir:
            return None
        ts = datetime.now().strftime("%H%M%S")
        path = self._log_dir / f"enmap_{name}_{ts}.png"
        try:
            page.screenshot(path=str(path))
            self._log(f"    [EnMAP] 截图已保存: {path}", also_print=False)
            return path
        except Exception as e:
            self._log(f"    [EnMAP] 截图失败({name}): {e}", level="warning", also_print=False)
            return None

    # ── SmartGWT overlay 处理 ────────────────────────────────────────────────

    @staticmethod
    def _hide_overlay(cdp):
        """隐藏 SmartGWT screenSpan overlay，防止其拦截 Playwright 的点击。"""
        cdp.send("Runtime.evaluate", {
            "expression": """(function() {
                var spans = document.querySelectorAll('div[eventproxy*=\"screenSpan\"]');
                for (var i = 0; i < spans.length; i++) spans[i].style.display = 'none';
            })()""",
            "returnByValue": True,
        })

    # ── 代理检测 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_proxy() -> Optional[Dict[str, str]]:
        """从环境变量读取代理设置，供 Playwright 使用。

        注意：
        - Playwright Chromium 不支持 SOCKS 代理（ERR_NO_SUPPORTED_PROXIES）
        - 遇到 SOCKS 代理时，回退到本地 HTTP 代理（快柠檬 10792 端口）
        - 若 HTTP 代理也不可用，返回 None（直连，仅在 DLR 可直连时有效）
        """
        import os
        import socket
        proxy_url = (
            os.environ.get("https_proxy") or
            os.environ.get("HTTPS_PROXY") or
            os.environ.get("http_proxy") or
            os.environ.get("HTTP_PROXY")
        )
        if not proxy_url:
            return None
        # Playwright Chromium 不支持 socks 代理，回退到 HTTP 代理
        if proxy_url.lower().startswith("socks"):
            # 尝试快柠檬 HTTP 代理端口 10792
            try:
                with socket.create_connection(("127.0.0.1", 10792), timeout=1):
                    return {"server": "http://127.0.0.1:10792"}
            except OSError:
                pass
            return None
        return {"server": proxy_url}

    # ── 依赖检查 ──────────────────────────────────────────────────────────────

    def _check_deps(self):
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests\n请运行: pip install requests")
        if not HAS_PLAYWRIGHT:
            raise ImportError(
                "缺少依赖: playwright\n"
                "请运行:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )
        if not self._username or not self._password:
            raise ValueError(
                "缺少 DLR 账号，请在 credentials.yaml 中配置:\n"
                "  dlr_eoweb:\n"
                "    username: your_username\n"
                "    password: your_password"
            )

    # ── CAS 认证（纯 HTTP）────────────────────────────────────────────────────

    def _cas_login_requests(self, service_url: str) -> "requests.Session":
        """
        用 requests.Session 完成 CAS 登录，返回持有 session cookie 的 Session。
        用于轮询订单和下载文件（不需要完整浏览器）。
        """
        session = requests.Session()
        session.headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        login_url = f"{_CAS_LOGIN}?service={requests.utils.quote(service_url, safe='')}"
        resp = session.get(login_url, timeout=30)
        resp.raise_for_status()

        m = re.search(r'name="execution"\s+value="([^"]+)"', resp.text)
        if not m:
            raise RuntimeError("CAS 登录页未找到 execution token")

        time.sleep(random.uniform(0.5, 1.2))
        post_resp = session.post(
            login_url,
            data={
                "username": self._username,
                "password": self._password,
                "execution": m.group(1),
                "_eventId": "submit",
                "geolocation": "",
            },
            allow_redirects=True,
            timeout=30,
        )

        if 'name="execution"' in post_resp.text:
            raise RuntimeError("CAS 登录失败，请检查 dlr_eoweb 的用户名和密码")

        # 确认已到达 EOWEB 主页（有 egp/main 或 GWT 内容），否则 session 无效
        final_url = post_resp.url
        if "egp/main" not in final_url and "eoweb.dlr.de" not in final_url:
            raise RuntimeError(f"CAS 登录后未到达 EOWEB 主页，当前 URL: {final_url}")

        # 访问 egp/main 建立 GWT session（JSESSIONID），否则 GWT-RPC 请求会被拒绝
        if "egp/main" not in final_url:
            main_resp = session.get(_EOWEB_MAIN, timeout=30)
        else:
            main_resp = post_resp

        # 从页面 HTML 动态提取 GWT order permutation hash（home/HASH.cache.js）
        # EOWEB 每次部署会更新 hash，硬编码常量过期后 GWT-RPC 会返回 403
        m_hash = re.search(r'home/([0-9A-F]{32})\.cache\.js', main_resp.text)
        if m_hash:
            self._gwt_hash_order = m_hash.group(1)
            print(f"    [EnMAP] 动态获取 GWT hash: {self._gwt_hash_order}")

        return session

    # ── Playwright 浏览器登录 ─────────────────────────────────────────────────

    def _pw_login(self, page: "Page", wait_text: Optional[str] = "EnMAP"):
        """在 Playwright page 中完成 EOWEB CAS 登录，等待主页面加载完毕。

        wait_text: 登录成功后等待出现的文字标志，默认 "EnMAP"（搜索/下单流程）。
                   传 None 则只等待 URL 跳转，不等待特定文字（Orders 页面场景）。
        """
        page.goto(
            f"{_CAS_LOGIN}?service={requests.utils.quote(_EOWEB_SERVICE, safe='')}",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        if "sso.eoc.dlr.de" in page.url:
            page.fill('input[name="username"]', self._username)
            page.fill('input[name="password"]', self._password)
            time.sleep(random.uniform(0.3, 0.7))
            page.evaluate('() => { document.querySelector("form").submit(); }')
        try:
            page.wait_for_url("**/egp/main*", timeout=60000)
        except Exception:
            pass
        if wait_text:
            # SmartGWT 应用加载需等待：先等 Loading... 消失，再等目标文字出现
            # GWT 需加载数 MB 的 .cache.js，弱网下可能需要数分钟
            try:
                page.locator("text=Loading").first.wait_for(state="hidden", timeout=240000)
            except Exception:
                pass  # 若 Loading 从未出现或消失超时，继续尝试等目标文字
            page.locator(f"text={wait_text}").first.wait_for(state="visible", timeout=120000)
        else:
            # 不等待特定文字，等待页面基本加载完毕（GWT bootstrap）
            page.wait_for_timeout(8000)

    # ── 场景搜索 ──────────────────────────────────────────────────────────────

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 100,
        max_results: int = 50,
        **kwargs,
    ) -> List[Dict]:
        """
        在 EOWEB GeoPortal 搜索 EnMAP 场景。

        策略：
          1. Playwright 登录 EOWEB
          2. 选择 EnMAP collection（Filter by Collection）
          3. 填写时间范围（Filter by Time）
          4. 填写 bbox（Bounding Box 标签页）
          5. 点击 Search，拦截 GWT-RPC getRecords 响应
          6. 解析响应中的 EnMAP 场景 ID

        云量过滤在 EOWEB 搜索层不支持，搜索后按 cloud_cover 过滤。
        """
        self._check_deps()
        min_lon, min_lat, max_lon, max_lat = bbox
        # 初始化日志（search 阶段用 output_dir 下的临时位置）
        tmp_log_dir = Path(self.output_dir) / "enmap"
        tmp_log_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logger(tmp_log_dir)
        print(f"\n[enmap] 搜索影像...")
        self._log(f"    [EnMAP] 搜索场景  {start_date} ~ {end_date}", also_print=False)
        self._log(f"    [EnMAP] 范围: [{min_lon:.4f}, {min_lat:.4f}, {max_lon:.4f}, {max_lat:.4f}]")

        search_responses: List[str] = []   # 收集 getRecords GWT 响应体

        _proxy = self._get_proxy()
        # eoweb.dlr.de 直连更稳定（GWT 静态资源大，代理可能导致 Loading 卡住）
        if _proxy:
            _proxy["bypass"] = "eoweb.dlr.de,sso.eoc.dlr.de,download.geoservice.dlr.de"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self._headless,
                proxy=_proxy,
            )
            ctx = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1920, "height": 1080},
            )
            cdp = ctx.new_cdp_session(ctx.new_page())
            page = ctx.pages[0]

            # CDP 拦截 handler/search 的请求和响应体
            pending: Dict[str, str] = {}   # reqId -> request body

            def _on_req(event):
                url = event.get("request", {}).get("url", "")
                if "/egp/handler/search" in url:
                    pending[event["requestId"]] = event.get("request", {}).get("postData", "")

            def _on_finish(event):
                rid = event["requestId"]
                if rid in pending:
                    try:
                        body = cdp.send("Network.getResponseBody", {"requestId": rid}).get("body", "")
                        if "getRecords" in pending[rid] and "dlrHma" in pending[rid]:
                            search_responses.append(body)
                    except Exception:
                        pass
                    del pending[rid]

            cdp.on("Network.requestWillBeSent", _on_req)
            cdp.on("Network.loadingFinished", _on_finish)
            cdp.send("Network.enable")

            try:
                self._pw_login(page)

                # 1. 选 EnMAP collection（_pw_login 已确认 EnMAP 文字可见）
                enmap_locator = page.locator("text=EnMAP").first
                try:
                    enmap_locator.wait_for(state="visible", timeout=10000)
                except Exception:
                    self._save_screenshot(page, "search_enmap_not_found")
                    raise
                enmap_locator.click(timeout=8000)
                page.wait_for_timeout(2000)

                # 2. 设置时间范围
                page.fill('input[name="startDateItem_dateTextField"]', start_date)
                page.keyboard.press("Tab")
                page.fill('input[name="endDateItem_dateTextField"]', end_date)
                page.keyboard.press("Tab")
                page.wait_for_timeout(500)

                # 3. 打开高级地图设置 bbox
                page.locator("text=Show Advanced Map").click(timeout=5000)
                page.wait_for_timeout(1500)

                # 切换到 Bounding Box 标签
                page.locator("text=Bounding Box").click(timeout=5000)
                page.wait_for_timeout(800)

                # 填写坐标（Upper/Lower Lat, Left/Right Lon）
                page.fill('input[name="ULAT"]', str(max_lat))
                page.fill('input[name="LLAT"]', str(min_lat))
                page.fill('input[name="LLON"]', str(min_lon))
                page.fill('input[name="RLON"]', str(max_lon))
                page.keyboard.press("Tab")
                page.wait_for_timeout(500)

                # 4. 点击 Search（第一个 Search 按钮）
                page.locator("text=Search").first.click(timeout=10000)
                page.wait_for_timeout(20000)   # 等待 GWT 请求完成

            except Exception as e:
                self._log(f"    [EnMAP] 搜索操作出错: {e}", level="error")
                self._save_screenshot(page, "search_error") if 'page' in dir() else None
            finally:
                browser.close()

        # 5. 从 GWT-RPC 响应中提取场景 ID
        self._log(f"    [EnMAP] 收到 {len(search_responses)} 个 GWT 搜索响应", also_print=False)
        for i, resp in enumerate(search_responses):
            self._log(f"    [EnMAP] GWT响应#{i} 长度={len(resp)} 预览={resp[:200]}", also_print=False)
        scenes = self._parse_gwt_search_responses(
            search_responses, start_date, end_date, cloud_cover, max_results,
            bbox=bbox,
        )

        self._log(f"    [EnMAP] 找到 {len(scenes)} 景（bbox+时间过滤后）")
        for sc in scenes[:5]:
            self._log(f"      {sc['properties'].get('datetime','?')[:10]}  "
                  f"云量={sc['properties'].get('eo:cloud_cover','?')}%  "
                  f"{sc['id'][:60]}")
        if len(scenes) > 5:
            self._log(f"      ... 共 {len(scenes)} 景")
        return scenes

    def _parse_gwt_search_responses(
        self,
        responses: List[str],
        start_date: str,
        end_date: str,
        cloud_cover: int,
        max_results: int,
        bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> List[Dict]:
        """
        从 GWT-RPC getRecords 响应中提取 EnMAP 场景列表。

        GWT-RPC 响应格式：//OK[...numbers...,["str0","str1",...],N,7]
        其中字符串数组包含所有字符串值（包括场景 ID）。
        数字部分通过负索引引用字符串数组（-1 = strings[-1] = strings[last]）。
        """
        scenes: List[Dict] = []
        seen: set = set()

        for body in responses:
            # 提取字符串数组中所有 EnMAP 场景 ID
            strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', body)
            enmap_ids = [s for s in strings if "ENMAP.HSI.L0:" in s and "dims_nz" in s]

            # 同时提取其他字段（datetime 和 cloud_cover 嵌在字符串数组里）
            # 日期格式：2024-01-15T08:23:45.678Z
            datetimes = [s for s in strings if re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', s)]
            # 云量：纯数字字符串，范围 0-100
            # 暂时无法精确关联场景和云量，使用默认值 0

            for i, scene_id in enumerate(enmap_ids):
                if scene_id in seen:
                    continue
                seen.add(scene_id)

                # 尝试关联日期（简单地按索引配对，不精确但可接受）
                dt_str = datetimes[i] if i < len(datetimes) else ""

                scenes.append({
                    "id": scene_id,
                    "properties": {
                        "datetime": dt_str,
                        "eo:cloud_cover": 0,
                        "platform": "enmap",
                        # 存储搜索参数，供 download() 在下单时复现相同的 EOWEB 搜索
                        "_search_start": start_date,
                        "_search_end": end_date,
                        "_search_bbox": list(bbox) if bbox else None,
                    },
                    "assets": {},
                })

                if len(scenes) >= max_results:
                    break

        return scenes

    # ── 下单（Playwright 操作 EOWEB UI）────────────────────────────────────────

    def _submit_order(
        self, scene_ids: List[str],
        search_start: Optional[str] = None,
        search_end: Optional[str] = None,
        search_bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> Optional[str]:
        """
        在 EOWEB 中将 scene_ids 加入购物车并提交订单。

        EOWEB 订购流程（通过截图确认）：
          1. 选 EnMAP collection + 日期范围 → Search（使用与 search() 相同的日期范围）
          2. 勾选目标场景 checkbox
          3. 点击结果工具栏第二个图标 → 跳转到 Order Options 页面
             （显示 scene_id、Order Option 下拉框、"Add to Order" 按钮）
          4. 在 Order Options 页面点击 "Add to Order"
          5. 对每个 scene 重复步骤 2-4（每次单独点击）
          6. 点击导航栏 "Cart (N)" → 提交订单
        """
        if not scene_ids:
            return None

        self._log(f"    [EnMAP] 准备下单 {len(scene_ids)} 个场景...")
        self._log(f"    [EnMAP] scene_ids: {scene_ids}", also_print=False)
        order_id: Optional[str] = None

        # 使用传入的日期范围；缺省时用宽范围
        if not search_start:
            search_start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        if not search_end:
            search_end = datetime.now().strftime("%Y-%m-%d")

        with sync_playwright() as pw:
            _proxy = self._get_proxy()
            if _proxy:
                _proxy["bypass"] = "eoweb.dlr.de,sso.eoc.dlr.de,download.geoservice.dlr.de"
            browser = pw.chromium.launch(
                headless=self._headless,
                proxy=_proxy,
            )
            ctx = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1920, "height": 1080},
            )
            cdp = ctx.new_cdp_session(ctx.new_page())
            page = ctx.pages[0]

            order_responses: Dict[str, Any] = {}

            def _on_req(event):
                url = event.get("request", {}).get("url", "")
                if "/egp/handler/order" in url:
                    order_responses[event["requestId"]] = {
                        "req": event.get("request", {}).get("postData", "")
                    }

            def _on_finish(event):
                rid = event["requestId"]
                if rid in order_responses:
                    try:
                        body = cdp.send("Network.getResponseBody", {"requestId": rid}).get("body", "")
                        order_responses[rid]["res"] = body
                    except Exception:
                        pass

            cdp.on("Network.requestWillBeSent", _on_req)
            cdp.on("Network.loadingFinished", _on_finish)
            cdp.send("Network.enable")

            try:
                self._pw_login(page)

                # 1. 选 EnMAP + 时间范围 + bbox → Search
                # _pw_login() 已等待 text=EnMAP 出现（最长60秒），此处直接点击即可
                # 若仍未出现（极端情况），再等 30 秒后截图上报
                enmap_locator2 = page.locator("text=EnMAP").first
                if not enmap_locator2.is_visible():
                    try:
                        enmap_locator2.wait_for(state="visible", timeout=60000)
                    except Exception:
                        self._save_screenshot(page, "order_enmap_not_found")
                        raise
                enmap_locator2.click(timeout=8000)
                page.wait_for_timeout(2000)
                page.fill('input[name="startDateItem_dateTextField"]', search_start)
                page.keyboard.press("Tab")
                page.fill('input[name="endDateItem_dateTextField"]', search_end)
                page.keyboard.press("Tab")
                page.wait_for_timeout(500)

                # 如果有 bbox，展开高级地图并填写
                if search_bbox:
                    min_lon, min_lat, max_lon, max_lat = search_bbox
                    try:
                        page.locator("text=Show Advanced Map").click(timeout=5000)
                        page.wait_for_timeout(1500)
                        page.locator("text=Bounding Box").click(timeout=5000)
                        page.wait_for_timeout(800)
                        page.fill('input[name="ULAT"]', str(max_lat))
                        page.fill('input[name="LLAT"]', str(min_lat))
                        page.fill('input[name="LLON"]', str(min_lon))
                        page.fill('input[name="RLON"]', str(max_lon))
                        page.keyboard.press("Tab")
                        page.wait_for_timeout(500)
                    except Exception as e:
                        self._log(f"    [EnMAP] 设置 bbox 失败（继续无 bbox 搜索）: {e}", level="warning")

                page.locator("text=Search").first.click(timeout=10000)
                page.wait_for_timeout(20000)

                # 收起高级地图面板，确保结果列表工具栏在可见区域
                try:
                    page.locator("text=Hide Advanced Map").click(timeout=5000)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

                # 2-4. 批量选中所有场景 → 一次进入 Order Options → 反复 Add to Order
                added_count = self._batch_add_to_order(page, cdp, scene_ids)
                self._save_screenshot(page, "after_batch_add")

                # 5. 检查购物车数量
                cart_text = self._get_cart_count(cdp)
                self._log(f"    [EnMAP] 购物车状态: {cart_text}")

                if "(0)" in (cart_text or ""):
                    self._log("    [EnMAP] 购物车为空，下单失败", level="error")
                    self._save_screenshot(page, "cart_empty")
                    return None

                # 6. 进入 Cart 页面提交
                # SmartGWT overlay (isc_EH_screenSpan) 可能短暂覆盖页面，等待消失
                page.wait_for_timeout(2000)
                try:
                    # 优先用 JS 直接触发 Cart tab 点击，绕过 overlay 遮挡
                    cdp.send("Runtime.evaluate", {
                        "expression": """(function() {
                            const all = document.querySelectorAll("td, div, a");
                            for (const el of all) {
                                const t = (el.innerText || "").trim();
                                if (t.startsWith("Cart (") && t.length < 20) {
                                    el.click();
                                    return "clicked: " + t;
                                }
                            }
                            return "not found";
                        })()""",
                        "returnByValue": True,
                    })
                    page.wait_for_timeout(3000)
                except Exception:
                    page.locator("text=Cart").first.click(timeout=5000)
                    page.wait_for_timeout(3000)
                order_id = self._checkout_cart(page, cdp, order_responses)

            except Exception as e:
                self._log(f"    [EnMAP] 下单操作出错: {e}", level="error")
                import traceback
                self._log(f"    [EnMAP] traceback: {traceback.format_exc()}", level="error", also_print=False)
                self._save_screenshot(page, "order_error") if 'page' in dir() else None
            finally:
                browser.close()

        return order_id

    def _add_scene_to_order(self, page: "Page", cdp, scene_id: str) -> bool:
        """
        [已废弃——由 _batch_add_to_order 取代]
        """
        raise NotImplementedError("请使用 _batch_add_to_order")

    def _batch_add_to_order(
        self, page: "Page", cdp, scene_ids: List[str],
    ) -> int:
        """
        批量下单：一次性选中所有场景 checkbox → 打开 Order 菜单 →
        回答 "Multiple Products Order" 对话框（Yes）→ 点击 "Add to Order"。

        SmartGWT 结构特点：
          - checkbox 和 scene_id 在不同 DOM 列容器中，通过 y 坐标对齐
          - SmartGWT checkbox 不响应 JS .click()，必须用 page.mouse.click()
          - 选中多个场景后进入 Order Options 会弹出 "Multiple Products Order"
            对话框，需先点 "Yes" 再点 "Add to Order"

        Returns: 成功加入购物车的场景数
        """
        if not scene_ids:
            return 0

        # 提取每个 scene_id 的唯一 key（XXXXB + 数字串）
        id_keys: List[str] = []
        for sid in scene_ids:
            m = re.search(r'(XXXXB\d{20,})', sid)
            if m:
                id_keys.append(m.group(1))
            else:
                idx = sid.find("dims_nz")
                id_keys.append(sid[idx:idx + 60] if idx >= 0 else "")

        # 1. 批量选中所有场景的 checkbox
        selected = 0
        for key in id_keys:
            if not key:
                continue
            escaped = key.replace("\\", "\\\\").replace('"', '\\"')
            locate_result = cdp.send("Runtime.evaluate", {
                "expression": f"""(function() {{
                    var key = "{escaped}";
                    var allDivs = document.querySelectorAll("div");
                    for (var i = 0; i < allDivs.length; i++) {{
                        var dt = "";
                        for (var n = allDivs[i].firstChild; n; n = n.nextSibling) {{
                            if (n.nodeType === 3) dt += n.textContent;
                        }}
                        if (dt.indexOf(key) >= 0) {{
                            var r = allDivs[i].getBoundingClientRect();
                            if (r.height > 5 && r.height < 30) {{
                                var ty = Math.round(r.y + r.height / 2);
                                var cbs = document.querySelectorAll("span.checkboxFalse");
                                var best = null, bd = 999;
                                for (var j = 0; j < cbs.length; j++) {{
                                    var cr = cbs[j].getBoundingClientRect();
                                    if (cr.width < 5) continue;
                                    var d = Math.abs(Math.round(cr.y + cr.height/2) - ty);
                                    if (d < bd) {{ bd = d; best = {{cx: Math.round(cr.x+cr.width/2), cy: Math.round(cr.y+cr.height/2)}}; }}
                                }}
                                return JSON.stringify(best || {{}});
                            }}
                        }}
                    }}
                    return JSON.stringify({{}});
                }})()""",
                "returnByValue": True,
            })
            pos = json.loads(locate_result.get("result", {}).get("value", "{}"))
            if pos.get("cx"):
                page.mouse.click(pos["cx"], pos["cy"])
                page.wait_for_timeout(600)
                selected += 1

        if selected == 0:
            self._log("    [EnMAP] 未能选中任何场景 checkbox", level="error")
            self._log(f"    [EnMAP] id_keys tried: {id_keys}", also_print=False)
            self._save_screenshot(page, "checkbox_none_selected")
            return 0

        page.wait_for_timeout(1000)
        self._log(f"    [EnMAP] 已选中 {selected} 个场景")
        self._save_screenshot(page, "after_checkbox_select")

        # 2. 点击工具栏菜单 → Order，进入 Order Options
        icon_pos = self._get_order_icon_position(cdp)
        page.mouse.click(icon_pos["x"], icon_pos["y"])
        page.wait_for_timeout(2000)

        try:
            page.locator("text=Order").first.click(timeout=5000)
            page.wait_for_timeout(6000)
        except Exception as e:
            self._log(f"    [EnMAP] 点击 Order 菜单失败: {e}", level="error")
            self._save_screenshot(page, "order_menu_fail")
            return 0

        # 3. 处理 "Multiple Products Order" 对话框（如果出现）
        #    "Would you like to set the same order options for all requested products?"
        #    → 点 "Yes"
        #    SmartGWT 按钮的 offsetParent 在弹窗中经常为 null，不能用它判断可见性。
        #    改用 getBoundingClientRect 获取坐标后由 Playwright mouse.click 点击，
        #    这是操作 SmartGWT 按钮最可靠的方式。
        #    多次重试等待对话框出现（最多 5 次，每次 1s）。
        try:
            import json as _json
            yes_val = None
            for _attempt in range(5):
                page.wait_for_timeout(1000)
                yes_pos = cdp.send("Runtime.evaluate", {
                    "expression": """(function() {
                        const all = document.querySelectorAll("td, div, button, span");
                        for (const el of all) {
                            const t = (el.innerText || el.textContent || "").trim();
                            // 匹配 "Yes" 或以 "Yes" 开头（防止 SmartGWT 混入空白字符）
                            if (t === "Yes" || t === "YES" || (t.startsWith("Yes") && t.length <= 4)) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0) {
                                    return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
                                }
                            }
                        }
                        return null;
                    })()""",
                    "returnByValue": True,
                })
                yes_val = yes_pos.get("result", {}).get("value")
                if yes_val:
                    break
            if yes_val:
                pos = _json.loads(yes_val)
                page.mouse.click(pos["x"], pos["y"])
                self._log(f"    [EnMAP] Multiple Products Order 对话框: clicked Yes at ({pos['x']:.0f},{pos['y']:.0f})")
                page.wait_for_timeout(3000)
            else:
                self._log(f"    [EnMAP] Multiple Products Order 对话框: Yes not found, 继续")
        except Exception as e:
            self._log(f"    [EnMAP] 处理 Multiple Products Order 对话框失败: {e}", level="warning")

        # 3b. 选择 Order Option（必填下拉框）
        #    SmartGWT 使用自定义 Select 组件，不是原生 <select>，需要：
        #    1) 找到 Order Option 旁边的 picker 图标元素并点击（展开下拉列表）
        #    2) 再点击第一个非空列表项
        try:
            # 点击 picker 图标：Order Option 标签行中 role="img" 或含 "Picker" class 的元素
            picker_pos = cdp.send("Runtime.evaluate", {
                "expression": """(function() {
                    // 找 "Order Option" 标签所在行，然后找其旁边的 picker 触发元素
                    const labels = document.querySelectorAll("td, div, span");
                    for (const lbl of labels) {
                        const t = (lbl.innerText || lbl.textContent || "").trim();
                        if (t === "Order Option" || t === "Order Option *" || t === "Order Option:") {
                            // 找同行或附近的 picker 图标（SmartGWT 通常是 role=img 或 class 含 Picker）
                            const row = lbl.closest("tr") || lbl.parentElement;
                            if (row) {
                                const imgs = row.querySelectorAll("[role='img'], [class*='Picker'], [class*='picker']");
                                for (const img of imgs) {
                                    const r = img.getBoundingClientRect();
                                    if (r.width > 0) return JSON.stringify({x: r.left+r.width/2, y: r.top+r.height/2, how:"picker-img"});
                                }
                                // fallback: 点击行内的 input/div 输入区域
                                const inputs = row.querySelectorAll("input, [role='combobox'], [role='listbox']");
                                for (const inp of inputs) {
                                    const r = inp.getBoundingClientRect();
                                    if (r.width > 0) return JSON.stringify({x: r.left+r.width/2, y: r.top+r.height/2, how:"input"});
                                }
                            }
                        }
                    }
                    return null;
                })()""",
                "returnByValue": True,
            })
            picker_val = picker_pos.get("result", {}).get("value")
            if picker_val:
                import json as _json
                pp = _json.loads(picker_val)
                page.mouse.click(pp["x"], pp["y"])
                self._log(f"    [EnMAP] Order Option picker 点击: ({pp['x']:.0f},{pp['y']:.0f}) [{pp.get('how','')}]")
                page.wait_for_timeout(1500)
                # 点击下拉列表中第一个非空选项
                opt_result = cdp.send("Runtime.evaluate", {
                    "expression": """(function() {
                        // SmartGWT 展开后的列表项通常在 role=option 或 class 含 listBody 的元素
                        const items = document.querySelectorAll("[role='option'], [role='listitem'], [class*='listBody'] td, [class*='listBody'] div");
                        for (const it of items) {
                            const t = (it.innerText || it.textContent || "").trim();
                            if (t && t !== "") {
                                const r = it.getBoundingClientRect();
                                if (r.width > 0) return JSON.stringify({x: r.left+r.width/2, y: r.top+r.height/2, text: t});
                            }
                        }
                        return null;
                    })()""",
                    "returnByValue": True,
                })
                opt_val_raw = opt_result.get("result", {}).get("value")
                if opt_val_raw:
                    op = _json.loads(opt_val_raw)
                    page.mouse.click(op["x"], op["y"])
                    self._log(f"    [EnMAP] Order Option 选择: {op.get('text','')}")
                    page.wait_for_timeout(1500)
                else:
                    self._log(f"    [EnMAP] Order Option 下拉列表为空，跳过")
            else:
                self._log(f"    [EnMAP] Order Option picker 未找到，跳过")
        except Exception as e:
            self._log(f"    [EnMAP] 选择 Order Option 失败: {e}", level="warning")

        # 4. 点击 "Add to Order"（用 getBoundingClientRect 定位坐标后 mouse.click，
        #    与 Yes 按钮同样的方式，避免 SmartGWT offsetParent 为 null 导致误判）
        try:
            add_pos = cdp.send("Runtime.evaluate", {
                "expression": """(function() {
                    const all = document.querySelectorAll("td, div, button, span, input");
                    for (const el of all) {
                        const t = (el.innerText || el.textContent || el.value || "").trim();
                        if (t === "Add to Order") {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
                            }
                        }
                    }
                    return null;
                })()""",
                "returnByValue": True,
            })
            add_val_raw = (add_pos.get("result", {}).get("value") or "")
            if add_val_raw:
                import json as _json
                ap = _json.loads(add_val_raw)
                page.mouse.click(ap["x"], ap["y"])
                self._log(f"    [EnMAP] 点击 Add to Order: clicked at ({ap['x']:.0f},{ap['y']:.0f})")
            else:
                self._log(f"    [EnMAP] 点击 Add to Order: Add to Order not found", level="warning")
            page.wait_for_timeout(4000)
            self._save_screenshot(page, "after_add_to_order")
        except Exception as e:
            self._log(f"    [EnMAP] Add to Order 失败: {e}", level="error")
            self._save_screenshot(page, "add_to_order_fail")
            return 0

        return selected

    def _get_order_icon_position(self, cdp) -> Dict:
        """
        获取结果工具栏中第一个图标按钮（菜单按钮，点击后展开 Details/Order/Export 菜单）的位置。

        实测（1920×1080 viewport，Hide Advanced Map 后）：
          第一个 div.normal ≈ x=647, y=385  → 打开下拉菜单（Details/Order/Export）
          点击 "Order" 菜单项 → 进入 Order Options 页面 → "Add to Order"
        """
        icon_info = cdp.send("Runtime.evaluate", {
            "expression": """(function() {
                // 找 "N of N results selected" 文字，向上找父容器，
                // 返回第一个 div.normal 子元素的中心坐标（菜单按钮）
                const allEl = document.querySelectorAll("*");
                for (const el of allEl) {
                    const t = (el.innerText || "").trim();
                    if (/\\d+ of \\d+ results selected/.test(t) &&
                        el.offsetWidth > 0 && el.offsetWidth < 300) {
                        let p = el.parentElement;
                        for (let depth = 0; depth < 15; depth++) {
                            if (!p) break;
                            const btns = Array.from(p.querySelectorAll("div.normal")).filter(d => {
                                const r = d.getBoundingClientRect();
                                return r.width > 10 && r.height > 10;
                            });
                            if (btns.length >= 1) {
                                // 第一个 div.normal = 菜单图标（含 Order 选项）
                                const r = btns[0].getBoundingClientRect();
                                return JSON.stringify({
                                    x: Math.round(r.x + r.width / 2),
                                    y: Math.round(r.y + r.height / 2)
                                });
                            }
                            p = p.parentElement;
                        }
                    }
                }
                // fallback: 已知坐标（1920×1080 viewport，Hide Advanced Map 后）
                return JSON.stringify({x: 647, y: 385});
            })()""",
            "returnByValue": True,
        })
        val = icon_info.get("result", {}).get("value")
        if val:
            try:
                return json.loads(val)
            except Exception:
                pass
        return {"x": 647, "y": 385}

    def _get_cart_count(self, cdp) -> str:
        """读取导航栏 Cart 按钮的文字（如 "Cart (3)"）。"""
        result = cdp.send("Runtime.evaluate", {
            "expression": """(function() {
                const all = document.querySelectorAll("td, div");
                for (const el of all) {
                    const t = (el.innerText || "").trim();
                    if (t.startsWith("Cart (") && t.length < 15) return t;
                }
                return "";
            })()""",
            "returnByValue": True,
        })
        return result.get("result", {}).get("value", "")

    def _checkout_cart(
        self, page: "Page", cdp, order_responses: Dict
    ) -> Optional[str]:
        """
        处理 EOWEB Cart 结算完整流程（3步骤向导）：
          1. 若出现 "Proceed to Cart / Return" 对话框，点 "Proceed to Cart"
          2. Cart 汇总页：mouse.click() 点击 "Proceed to checkout"（SmartGWT 须用鼠标坐标）
          3. Accept License Agreements 页：点击两个 SmartGWT span 复选框 + Continue
          4. Verify & Submit 页：点击 Submit
          5. Complete 页：从页面文字提取 order_id
        """
        # ── Step 1: "Proceed to Cart" 对话框 ──────────────────────────────────
        try:
            proceed_btn = page.locator("text=Proceed to Cart").first
            if proceed_btn.is_visible(timeout=4000):
                proceed_btn.click(timeout=5000)
                page.wait_for_timeout(4000)
        except Exception:
            pass

        # ── Step 2: Cart 汇总页 → "Proceed to checkout" ───────────────────────
        # 必须用 mouse.click() + 动态坐标，JS .click() 对 SmartGWT 按钮无效
        btn_pos = cdp.send("Runtime.evaluate", {
            "expression": """(function() {
                const all = document.querySelectorAll("div, button, a, td, span");
                for (const el of all) {
                    const t = (el.innerText || "").trim();
                    if (t === "Proceed to checkout" && el.offsetParent !== null) {
                        const r = el.getBoundingClientRect();
                        return JSON.stringify({
                            cx: Math.round(r.x + r.width / 2),
                            cy: Math.round(r.y + r.height / 2)
                        });
                    }
                }
                return null;
            })()""",
            "returnByValue": True,
        })
        try:
            pos = json.loads(btn_pos.get("result", {}).get("value") or "{}")
            checkout_cx = pos.get("cx", 1593)
            checkout_cy = pos.get("cy", 138)
        except Exception:
            checkout_cx, checkout_cy = 1593, 138

        page.mouse.click(checkout_cx, checkout_cy)
        page.wait_for_timeout(5000)
        self._log(f"    [EnMAP] 点击 Proceed to checkout at ({checkout_cx},{checkout_cy})")
        self._save_screenshot(page, "after_proceed_checkout")

        # ── Step 3: Accept License Agreements 页 ─────────────────────────────
        # URL 变为 #mainWindowtabAcceptLicenseAgreements
        if "AcceptLicense" in page.url or "acceptLicense" in page.url.lower():
            self._log("    [EnMAP] 接受许可协议...")
            self._save_screenshot(page, "license_page")
            # 找两个许可协议复选框（SmartGWT span，无 checkboxFalse class）
            cb_info = cdp.send("Runtime.evaluate", {
                "expression": """(function() {
                    const form = document.querySelector('.egpGwtOrderingProcessLicenseForm')
                               || document.getElementById('isc_1DN');
                    const root = form ? form : document.body;
                    const spans = root.querySelectorAll('span');
                    const results = [];
                    for (const el of spans) {
                        const r = el.getBoundingClientRect();
                        if (r.width >= 10 && r.width <= 20 && r.height >= 10 && r.height <= 20) {
                            results.push({
                                id: el.id,
                                cx: Math.round(r.x + r.width / 2),
                                cy: Math.round(r.y + r.height / 2)
                            });
                        }
                    }
                    return JSON.stringify(results);
                })()""",
                "returnByValue": True,
            })
            try:
                cbs = json.loads(cb_info.get("result", {}).get("value") or "[]")
                for cb in cbs[:2]:  # 只点前两个（D-SDA + EnMAP 许可）
                    page.mouse.click(cb["cx"], cb["cy"])
                    page.wait_for_timeout(600)
                    self._log(f"    [EnMAP] 勾选许可: id={cb['id']} ({cb['cx']},{cb['cy']})")
            except Exception as e:
                self._log(f"    [EnMAP] 许可复选框点击失败: {e}", level="warning")
                # fallback: 已知坐标（1920×1080 viewport）
                for cy_fallback in [216, 256]:
                    page.mouse.click(305, cy_fallback)
                    page.wait_for_timeout(600)

            page.wait_for_timeout(500)
            # 点击 Continue 按钮
            cont_pos = cdp.send("Runtime.evaluate", {
                "expression": """(function() {
                    const el = document.querySelector('.egpGwtOrderingProcessButtonContinue')
                              || document.getElementById('isc_1DX');
                    if (el && el.offsetParent) {
                        const r = el.getBoundingClientRect();
                        return JSON.stringify({cx: Math.round(r.x+r.width/2), cy: Math.round(r.y+r.height/2)});
                    }
                    // fallback: find by text
                    for (const e of document.querySelectorAll('div,td')) {
                        if ((e.innerText||'').trim() === 'Continue' && e.offsetParent) {
                            const r = e.getBoundingClientRect();
                            return JSON.stringify({cx: Math.round(r.x+r.width/2), cy: Math.round(r.y+r.height/2)});
                        }
                    }
                    return null;
                })()""",
                "returnByValue": True,
            })
            try:
                cpos = json.loads(cont_pos.get("result", {}).get("value") or "{}")
                page.mouse.click(cpos.get("cx", 1413), cpos.get("cy", 301))
            except Exception:
                page.mouse.click(1413, 301)
            page.wait_for_timeout(5000)
            self._log("    [EnMAP] 点击 Continue")
            self._save_screenshot(page, "after_license_continue")
        else:
            self._log(f"    [EnMAP] 未进入 License 页面，当前 URL: {page.url}", level="warning", also_print=False)
            self._save_screenshot(page, "no_license_page")

        # ── Step 4: Verify & Submit 页 ────────────────────────────────────────
        if "CheckAndSubmit" in page.url or "VerifyAndSubmit" in page.url.lower() or \
           "checkandsubmit" in page.url.lower():
            self._log("    [EnMAP] 提交订单（Verify & Submit）...")
            self._save_screenshot(page, "verify_submit_page")
            submit_pos = cdp.send("Runtime.evaluate", {
                "expression": """(function() {
                    const el = document.querySelector('.egpGwtOrderingProcessButtonSubmit')
                              || document.getElementById('isc_1FT');
                    if (el && el.offsetParent) {
                        const r = el.getBoundingClientRect();
                        return JSON.stringify({cx: Math.round(r.x+r.width/2), cy: Math.round(r.y+r.height/2)});
                    }
                    for (const e of document.querySelectorAll('div,td,button')) {
                        if ((e.innerText||'').trim() === 'Submit' && e.offsetParent) {
                            const r = e.getBoundingClientRect();
                            return JSON.stringify({cx: Math.round(r.x+r.width/2), cy: Math.round(r.y+r.height/2)});
                        }
                    }
                    return null;
                })()""",
                "returnByValue": True,
            })
            try:
                spos = json.loads(submit_pos.get("result", {}).get("value") or "{}")
                page.mouse.click(spos.get("cx", 1681), spos.get("cy", 187))
            except Exception:
                page.mouse.click(1681, 187)
            page.wait_for_timeout(8000)
            self._log("    [EnMAP] 点击 Submit")
            self._save_screenshot(page, "after_submit")
        else:
            self._log(f"    [EnMAP] 未进入 Verify&Submit 页面，当前 URL: {page.url}", level="warning", also_print=False)
            self._save_screenshot(page, "no_verify_page")

        # ── Step 5: 提取 order_id ─────────────────────────────────────────────
        order_id: Optional[str] = None

        # 优先从页面文字提取（Complete 页显示 "Order {order_id}"）
        try:
            page_text = page.inner_text("body")
            # 格式: "Order kevin_jh-cat1distributor_2026-04-09-02:02:17,823"
            m = re.search(
                r'Order\s+([A-Za-z0-9_\-:,\.]+)',
                page_text
            )
            if m:
                candidate = m.group(1).strip()
                # 过滤掉 "Order name" 后面的普通词
                if len(candidate) > 10 and any(c in candidate for c in ['-', '_', ':']):
                    order_id = candidate
                    self._log(f"    [EnMAP] 从页面提取 order_id: {order_id}")
        except Exception:
            pass

        # 备用：从 GWT 响应提取
        if not order_id:
            order_id = self._extract_order_id_from_gwt(order_responses)

        # 备用：从 GWT 响应中找 URN 格式订单号
        if not order_id:
            for rid, data in order_responses.items():
                res = data.get("res", "")
                m = re.search(r'urn:eop:DLR:EOWEB:([^",\\]+)', res)
                if m:
                    order_id = m.group(1)
                    self._log(f"    [EnMAP] 从 GWT URN 提取 order_id: {order_id}")
                    break

        if order_id:
            self._log(f"    [EnMAP] 下单成功，订单 ID: {order_id}")
            self._save_screenshot(page, "order_success")
        else:
            # 即使无法提取 ID，订单可能已提交成功
            page_text_lower = ""
            try:
                page_text_lower = page.inner_text("body").lower()
            except Exception:
                pass
            if "Finished" in page.url or "success" in page_text_lower:
                self._log("    [EnMAP] 订单已提交（无法提取 order_id，请在 EOWEB 订单页查看）", level="warning")
                self._save_screenshot(page, "order_no_id")
                order_id = "submitted"
            else:
                self._log("    [EnMAP] 未能提取 order_id", level="error")
                self._log(f"    [EnMAP] 当前 URL: {page.url}", also_print=False)
                self._save_screenshot(page, "order_failed_no_id")

        return order_id

    def _extract_order_id_from_gwt(self, order_responses: Dict) -> Optional[str]:
        """从 GWT-RPC order 响应中提取 order_id。"""
        for rid, data in order_responses.items():
            res = data.get("res", "")
            if not res:
                continue
            # EOWEB 订单 URN 格式: urn:eop:DLR:EOWEB:{username}_{date}
            m = re.search(r'urn:eop:DLR:EOWEB:([^",\\]+)', res)
            if m:
                return m.group(1)
        return None

    # ── 订单轮询 ──────────────────────────────────────────────────────────────

    def _poll_order(
        self,
        order_id: Optional[str],
        poll_interval: int = 60,
        timeout: int = 28800,
    ) -> Tuple[bool, List[str]]:
        """
        用 requests.Session 轮询 EOWEB 的 getOrderSummaries GWT-RPC，
        等待订单状态变为 COMPLETED/READY，返回下载 URL 列表。

        返回 (is_ready, urls)：
          (True,  urls) — 订单就绪，urls 可能为空（需要 Playwright 备用提取）
          (False, [])   — 轮询超时，订单仍在处理中

        _check_order_ready 返回三种情况：
          (True,  urls) — 订单就绪
          (False, [])   — 订单仍在处理中
          (None,  [])   — 网络/session 失败，需重新登录
        """
        self._log(f"    [EnMAP] 开始轮询订单（每 {poll_interval}s，最长 {timeout//60} 分钟）")
        if order_id:
            self._log(f"    [EnMAP] 订单 ID: {order_id}")

        start_ts = time.time()
        session = None
        session_failures = 0   # 连续登录失败次数
        consecutive_403 = 0    # 连续 403 次数（登录成功但请求仍 403）

        poll_count = 0
        while True:
            elapsed = int(time.time() - start_ts)
            if elapsed >= timeout:
                self._log(f"\n    [EnMAP] 订单轮询超时（{timeout//60} 分钟），请手动检查 EOWEB 订单")
                self._log(f"    [EnMAP] 请登录 https://eoweb.dlr.de/egp/main#Orders 查看状态")
                return False, []

            # session 为空时（首次或失效后）尝试重新登录
            if session is None:
                try:
                    session = self._cas_login_requests(_EOWEB_SERVICE)
                    session_failures = 0
                    consecutive_403 = 0
                except Exception as e:
                    session_failures += 1
                    wait = min(60 * session_failures, 300)   # 最长等5分钟
                    self._log(f"    [EnMAP] 轮询登录失败（第{session_failures}次），{wait}s后重试: {e}", level="warning")
                    time.sleep(wait)
                    continue

            poll_count += 1
            print(f"    [EnMAP] 轮询 #{poll_count}  已等待 {elapsed // 60} 分钟  ", end="", flush=True)
            if self._logger:
                self._logger.info(f"轮询 #{poll_count}  已等待 {elapsed // 60} 分钟")

            is_ready, urls = self._check_order_ready(session, order_id)

            if is_ready is None:
                # 网络/session 失败，丢弃当前 session，下次循环重新登录
                print()
                session_failures += 1
                consecutive_403 += 1
                self._log(f"    [EnMAP] 轮询请求失败（第{session_failures}次），将重新登录", level="warning")
                session = None
                # 连续 403 说明 GWT hash 已过期，延长等待让服务端恢复
                wait = min(30 * session_failures, 120) if consecutive_403 < 3 else min(120 * consecutive_403, 600)
                time.sleep(wait)
                continue

            if is_ready:
                print()  # 换行
                if urls:
                    self._log(f"    [EnMAP] 订单已就绪，共 {len(urls)} 个下载文件")
                else:
                    self._log(f"    [EnMAP] 订单状态已就绪，但 GWT-RPC 未返回下载链接")
                return True, urls

            # 订单仍在处理中，继续等待
            print()  # 换行
            time.sleep(poll_interval)

    def _order_summary_strings(self, session: "requests.Session") -> Optional[List[str]]:
        """发 GWT-RPC getOrderSummaries(最近 30 天),返回响应里的字符串池。
        返回 None 表示网络失败或 session 失效(无 //OK),调用方应重新登录。
        响应里订单按时间倒序排列,最新订单(刚下的那张)在最前,其状态字符串
        紧跟其 URN(只有最新订单内联状态,旧订单状态走 GWT 整数回引,字符串池里没有)。
        """
        end_dt   = datetime.now(tz=None)
        start_dt = end_dt - timedelta(days=30)
        start_str = start_dt.strftime("%Y-%m-%dT00:00:00,000")
        end_str   = end_dt.strftime("%Y-%m-%dT23:59:59,000")

        gwt_body = (
            f"7|0|9|{_GWT_BASE}|{self._gwt_hash_order or _GWT_HASH_ORDER}|"
            "de.dlr.eoweb.gwt.order.client.OrderServiceGWTWrapper|getOrderSummaries|"
            "de.dlr.schemas.eoweb.eos.StatusRequest/2242062801|"
            "de.dlr.schemas.eoweb.eos.StatusRequestType$DateRange/93747011|"
            f"{start_str}|{end_str}|brief|1|2|3|4|1|5|5|6|7|8|9|0|0|0|"
        )

        try:
            resp = session.post(
                f"{_EOWEB_HANDLER}order",
                data=gwt_body,
                headers={
                    "Content-Type": "text/x-gwt-rpc; charset=UTF-8",
                    "X-GWT-Module-Base": _GWT_BASE,
                    "X-GWT-Permutation": self._gwt_hash_order or _GWT_HASH_ORDER,
                    "Origin": "https://eoweb.dlr.de",
                    "Referer": _EOWEB_MAIN,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            self._log(f"    [EnMAP] 轮询请求异常: {e}", level="warning")
            return None

        body = resp.text
        if "//OK" not in body:
            # session 可能已失效（返回登录页等），触发重新登录
            preview = body[:200].replace("\n", " ")
            self._log(f"    [EnMAP] 轮询响应异常（无//OK），可能 session 失效，响应预览: {preview}", level="warning")
            return None

        return re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', body)

    def _newest_order_id(self, session: "requests.Session") -> Optional[str]:
        """返回最近一张订单的 order_id(URN 去掉前缀,含毫秒)。
        刚下完单调用时,最新订单就是本次提交的那张——比从下单页面文字里
        刮(常刮到订单列表里的旧行)可靠得多。失败返回 None。
        """
        strings = self._order_summary_strings(session)
        if not strings:
            return None
        for s in strings:
            if s.startswith("urn:eop:DLR:EOWEB:"):
                return s.split("urn:eop:DLR:EOWEB:", 1)[1]
        return None

    def _check_order_ready(
        self, session: "requests.Session", order_id: Optional[str]
    ) -> Tuple[Optional[bool], List[str]]:
        """
        发送 GWT-RPC getOrderSummaries 请求，检查指定订单是否就绪。
        返回 (is_ready, url_list)：
          (True,  urls) — 订单已就绪
          (False, [])   — 订单仍在处理中
          (None,  [])   — 网络/session 失败，调用方应重新登录

        EOWEB 订单状态流转（实测）：
          Submitted → In Progress → Completed/PROCESSED
        """
        strings = self._order_summary_strings(session)
        if strings is None:
            return None, []

        # 查找目标订单的 URN 和状态
        # GWT 响应格式: ...数字部分...,["str0","str1",...],N,7]
        # 订单 URN: urn:eop:DLR:EOWEB:{order_id}
        # 状态紧跟在 StatusInfoType 类型声明之后
        # 已知状态值（实测）:
        #   Submitted → IN_PROCESSING/InProduction → Completed/DELIVERED/PROCESSED
        _ready_statuses = {"completed", "ready", "available", "success", "done", "finished", "delivered", "processed"}
        _pending_statuses = {"submitted", "in progress", "in_processing", "inproduction",
                            "queued", "pending", "processing"}

        # 找到目标订单并获取其状态
        order_status = None
        order_found = False
        if order_id:
            # order_id 在 GWT 中以 URN 形式出现: urn:eop:DLR:EOWEB:{order_id}
            for i, s in enumerate(strings):
                if order_id in s:
                    order_found = True
                    # 状态通常在同组的前几个字符串（GWT结构中位置固定）
                    # 向前搜索最近的已知状态字符串
                    for j in range(max(0, i - 5), min(len(strings), i + 5)):
                        sl = strings[j].lower()
                        if sl in _ready_statuses or sl in _pending_statuses:
                            order_status = strings[j]
                            break
                    break
        else:
            order_found = True

        if not order_found:
            return False, []

        # 如果没找到订单特定状态，取全局状态（所有订单共享同一状态引用时）
        if not order_status:
            for s in strings:
                sl = s.lower()
                if sl in _ready_statuses or sl in _pending_statuses:
                    order_status = s
                    break

        # 打印当前状态
        if order_status:
            print(f"    [EnMAP] 订单状态: {order_status}", end="  ", flush=True)
            if self._logger:
                self._logger.info(f"订单状态: {order_status}")

        # 判断是否就绪
        is_ready = bool(order_status and order_status.lower() in _ready_statuses)
        if not is_ready:
            return False, []

        # 记录 GWT 响应中所有字符串（调试用）
        self._log(f"    [EnMAP] getOrderSummaries 字符串数={len(strings)}", also_print=False)
        for i, s in enumerate(strings):
            self._log(f"    [EnMAP]   summary_str[{i}]={s[:120]}", also_print=False)

        # 提取下载链接（.zip/.tiff/.nc/.h5 / FTP路径）
        # 同时提取 dims_op_oc_oc-en_ 文件名，保存到订单文件供 FTPS fallback 精确匹配
        urls: List[str] = []
        expected_filenames: List[str] = []
        for s in strings:
            if re.search(r'\.(zip|tar\.gz|tiff?|nc|h5)$', s, re.I):
                urls.append(s)
            elif s.startswith("ftp://") or s.startswith("ftps://"):
                urls.append(s)
            elif re.search(r'https?://[^\s]+\.(zip|tar\.gz|tiff?|nc|h5)', s, re.I):
                urls.append(s)
            # 提取 dims_op_oc_oc-en_ 文件名（FTPS 精确匹配用）
            m = re.search(r'(dims_op_oc_oc-en_\d+_\d+\.tar\.gz)', s)
            if m:
                expected_filenames.append(m.group(1))
        if expected_filenames:
            self._log(f"    [EnMAP] getOrderSummaries 提取到预期文件名: {expected_filenames}", also_print=False)
            # 通知调用方更新订单文件
            self._expected_filenames_cache = expected_filenames

        if not urls:
            self._log(f"    [EnMAP] getOrderSummaries 无下载链接，尝试 getOrderDetails...", also_print=False)
            # 状态就绪但无链接：尝试通过 getOrderDetails 获取下载 URL
            urls = self._get_order_download_urls(session, order_id)

        if not urls:
            # 备用：从 EOWEB 主页扫描
            urls = self._extract_download_urls_from_page(session, order_id)

        # 返回 (True, urls)：即使 urls 为空也表示订单已就绪，上层不应继续轮询
        return True, urls

    def _get_order_download_urls(
        self, session: "requests.Session", order_id: Optional[str]
    ) -> List[str]:
        """
        通过 GWT-RPC getOrderDetails 获取就绪订单的下载链接。
        getOrderSummaries 只返回状态，getOrderDetails 返回具体的下载 URL。
        """
        if not order_id:
            return []
        # 构造 order URN
        urn = f"urn:eop:DLR:EOWEB:{order_id}"
        gwt_body = (
            f"7|0|6|{_GWT_BASE}|{self._gwt_hash_order or _GWT_HASH_ORDER}|"
            "de.dlr.eoweb.gwt.order.client.OrderServiceGWTWrapper|getOrderDetails|"
            "java.lang.String/2004016611|"
            f"{urn}|1|2|3|4|1|5|6|"
        )
        try:
            resp = session.post(
                f"{_EOWEB_HANDLER}order",
                data=gwt_body,
                headers={
                    "Content-Type": "text/x-gwt-rpc; charset=UTF-8",
                    "X-GWT-Module-Base": _GWT_BASE,
                    "X-GWT-Permutation": self._gwt_hash_order or _GWT_HASH_ORDER,
                    "Origin": "https://eoweb.dlr.de",
                    "Referer": _EOWEB_MAIN,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            self._log(f"    [EnMAP] getOrderDetails 请求失败: {e}", level="warning", also_print=False)
            return []

        body = resp.text
        self._log(f"    [EnMAP] getOrderDetails 响应长度={len(body)}, 前300字符={body[:300]}", also_print=False)
        if "//OK" not in body:
            self._log(f"    [EnMAP] getOrderDetails 响应无 //OK", level="warning", also_print=False)
            return []

        strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', body)
        self._log(f"    [EnMAP] getOrderDetails 字符串数={len(strings)}", also_print=False)
        # 记录所有字符串以便调试
        for i, s in enumerate(strings):
            self._log(f"    [EnMAP]   str[{i}]={s[:120]}", also_print=False)

        urls: List[str] = []
        for s in strings:
            if re.search(r'\.(zip|tar\.gz|tiff?|nc|h5)$', s, re.I):
                urls.append(s)
            elif s.startswith("ftp://") or s.startswith("ftps://"):
                urls.append(s)
            elif re.search(r'https?://[^\s]+download', s, re.I):
                urls.append(s)
        self._log(f"    [EnMAP] getOrderDetails 提取到 {len(urls)} 个链接", also_print=False)
        return list(set(urls))

    def _extract_download_urls_from_page(
        self, session: "requests.Session", order_id: Optional[str]
    ) -> List[str]:
        """
        通过 GWT-RPC getOrderDetails 获取订单详情，提取实际下载链接。
        DLR EnMAP 数据通过 FTPS 传输，链接格式为 ftps://...
        """
        urls: List[str] = []
        if not order_id:
            return urls
        try:
            # 尝试访问 EOWEB 主页，扫描页面中的下载链接
            resp = session.get(_EOWEB_MAIN, timeout=30)
            content = resp.text
            # FTP 链接
            ftps = re.findall(r'ftps?://[^\s"\'<>]+', content, re.IGNORECASE)
            urls.extend(ftps)
            # HTTP 下载链接
            http_dl = re.findall(
                r'https?://[^\s"\'<>]+\.(?:zip|tar\.gz|tiff?|nc|h5)',
                content, re.IGNORECASE
            )
            urls.extend(http_dl)
            urls = list(set(urls))
        except Exception:
            pass
        return urls

    def _extract_download_urls_playwright(self, order_id: Optional[str]) -> List[str]:
        """
        用 Playwright 打开 EOWEB Orders 页面，点击目标订单，
        通过 CDP 拦截 getOrderDetails GWT-RPC 响应，提取下载链接。

        这是 GWT-RPC HTTP 方式失败时的备用方案——浏览器中的 GWT 请求
        携带完整 session + 正确的 GWT permutation hash，更可靠。
        """
        self._log("    [EnMAP] 尝试通过 Playwright 从 Orders 页面提取下载链接...")
        urls: List[str] = []
        detail_responses: List[str] = []

        _proxy = self._get_proxy()
        if _proxy:
            _proxy["bypass"] = "eoweb.dlr.de,sso.eoc.dlr.de,download.geoservice.dlr.de"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self._headless,
                proxy=_proxy,
            )
            ctx = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1920, "height": 1080},
            )
            cdp = ctx.new_cdp_session(ctx.new_page())
            page = ctx.pages[0]

            # CDP 拦截 order handler 的响应
            pending_reqs: Dict[str, str] = {}

            def _on_req(event):
                url = event.get("request", {}).get("url", "")
                if "/egp/handler/order" in url:
                    pending_reqs[event["requestId"]] = event.get("request", {}).get("postData", "")

            def _on_finish(event):
                rid = event["requestId"]
                if rid in pending_reqs:
                    try:
                        body = cdp.send("Network.getResponseBody", {"requestId": rid}).get("body", "")
                        detail_responses.append(body)
                    except Exception:
                        pass
                    del pending_reqs[rid]

            cdp.on("Network.requestWillBeSent", _on_req)
            cdp.on("Network.loadingFinished", _on_finish)
            cdp.send("Network.enable")

            try:
                self._pw_login(page, wait_text=None)

                # 登录后从页面提取最新 GWT hash，确保后续 GWT-RPC 不会 403
                try:
                    html = page.content()
                    m_hash = re.search(r'home/([0-9A-F]{32})\.cache\.js', html)
                    if m_hash:
                        self._gwt_hash_order = m_hash.group(1)
                        self._log(f"    [EnMAP] Playwright 更新 GWT hash: {self._gwt_hash_order}", also_print=False)
                except Exception:
                    pass

                # 导航到 Orders 页面
                page.goto(f"{_EOWEB_MAIN}#Orders", wait_until="domcontentloaded", timeout=60000)
                # 等待 Orders 页面的 GWT 内容加载（等待包含 "Orders" 或 "PROCESSED" 的元素出现）
                try:
                    page.locator("text=Orders").first.wait_for(state="visible", timeout=60000)
                except Exception:
                    pass
                page.wait_for_timeout(5000)
                self._save_screenshot(page, "orders_page")

                # 点击目标订单行（通过 order_id 文字定位）
                if order_id and order_id != "submitted":
                    # 尝试通过 order_id 部分文字找到订单行并点击
                    # order_id 格式: kevin_jh-cat1distributor_2026-04-13-08:33:45
                    # 页面中可能显示为截断形式，用用户名前缀匹配
                    order_clicked = False
                    # 方法1：直接在页面中查找包含 order_id 的元素并点击
                    escaped_oid = order_id.replace('"', '\\"')
                    js_find_order = """(function() {
                            var oid = "%s";
                            var allDivs = document.querySelectorAll("div, td, span");
                            for (var i = 0; i < allDivs.length; i++) {
                                var t = "";
                                for (var n = allDivs[i].firstChild; n; n = n.nextSibling) {
                                    if (n.nodeType === 3) t += n.textContent;
                                }
                                if (t.indexOf(oid) >= 0) {
                                    var r = allDivs[i].getBoundingClientRect();
                                    if (r.height > 5 && r.height < 40 && r.width > 50) {
                                        return JSON.stringify({
                                            cx: Math.round(r.x + r.width / 2),
                                            cy: Math.round(r.y + r.height / 2)
                                        });
                                    }
                                }
                            }
                            return null;
                        })()""" % escaped_oid
                    click_result = cdp.send("Runtime.evaluate", {
                        "expression": js_find_order,
                        "returnByValue": True,
                    })
                    val = click_result.get("result", {}).get("value")
                    if val and val != "null":
                        try:
                            pos = json.loads(val)
                            page.mouse.click(pos["cx"], pos["cy"])
                            order_clicked = True
                            self._log(f"    [EnMAP] 点击订单行 ({pos['cx']},{pos['cy']})")
                        except Exception:
                            pass

                    if not order_clicked:
                        # 方法2：按 order_id 的日期时间戳找对应行
                        # order_id 格式: kevin_jh-cat1distributor_2026-04-17-09:00:09
                        # 提取日期部分用于模糊匹配（精度到分钟）
                        ts_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{2}:\d{2})', order_id or "")
                        ts_prefix = ts_match.group(1) if ts_match else ""
                        self._log(f"    [EnMAP] 未找到订单文字，按时间戳 {ts_prefix!r} 查找订单行...")
                        find_expr = """(function() {
                            var ts = "%s";
                            var allDivs = document.querySelectorAll("div");
                            // 优先找包含时间戳的行
                            if (ts) {
                                for (var i = 0; i < allDivs.length; i++) {
                                    var t = (allDivs[i].innerText || "").trim();
                                    if (t.indexOf(ts) >= 0 && allDivs[i].offsetWidth > 0) {
                                        var r = allDivs[i].getBoundingClientRect();
                                        if (r.height > 5 && r.height < 60) {
                                            return JSON.stringify({cx: Math.round(r.x + r.width/2), cy: Math.round(r.y + r.height/2)});
                                        }
                                    }
                                }
                            }
                            // 回退：找最近（y坐标最大）的 PROCESSED 行
                            var best = null, bestY = -1;
                            for (var i = 0; i < allDivs.length; i++) {
                                var t = (allDivs[i].innerText || "").trim();
                                if ((t === "PROCESSED" || t === "Completed" || t === "DELIVERED") &&
                                    allDivs[i].offsetWidth > 0) {
                                    var r = allDivs[i].getBoundingClientRect();
                                    if (r.y > bestY) { bestY = r.y; best = r; }
                                }
                            }
                            if (best) return JSON.stringify({cx: 200, cy: Math.round(best.y + best.height/2)});
                            return null;
                        })()""" % (ts_prefix,)
                        first_row = cdp.send("Runtime.evaluate", {
                            "expression": find_expr,
                            "returnByValue": True,
                        })
                        fval = first_row.get("result", {}).get("value")
                        if fval and fval != "null":
                            try:
                                fpos = json.loads(fval)
                                page.mouse.click(fpos["cx"], fpos["cy"])
                                order_clicked = True
                                self._log(f"    [EnMAP] 点击订单行 ({fpos['cx']},{fpos['cy']})")
                            except Exception:
                                pass

                    # 等待 getOrderDetails GWT-RPC 响应
                    page.wait_for_timeout(12000)
                    self._save_screenshot(page, "order_detail_page")

                # 从拦截到的 GWT 响应中提取下载链接
                self._log(f"    [EnMAP] Playwright 拦截到 {len(detail_responses)} 个 order 响应", also_print=False)
                for resp_body in detail_responses:
                    strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', resp_body)
                    self._log(f"    [EnMAP]   响应字符串数={len(strings)}", also_print=False)
                    for s in strings:
                        self._log(f"    [EnMAP]     pw_str={s[:150]}", also_print=False)
                        if re.search(r'\.(zip|tar\.gz|tiff?|nc|h5)$', s, re.I):
                            urls.append(s)
                        elif s.startswith("ftp://") or s.startswith("ftps://"):
                            urls.append(s)
                        elif re.search(r'https?://[^\s]+download', s, re.I):
                            urls.append(s)
                        elif re.search(r'https?://[^\s]+/data/', s, re.I):
                            urls.append(s)

                # 备用：直接从页面 DOM 中扫描下载链接
                if not urls:
                    self._log("    [EnMAP] GWT 响应无链接，扫描页面 DOM...")
                    page_links = cdp.send("Runtime.evaluate", {
                        "expression": """(function() {
                            var links = [];
                            // 扫描所有 a 标签的 href
                            var anchors = document.querySelectorAll("a[href]");
                            for (var i = 0; i < anchors.length; i++) {
                                var h = anchors[i].href;
                                if (h && (h.match(/\\.(zip|tar\\.gz|tiff?|nc|h5)$/i) ||
                                          h.indexOf("download") >= 0 ||
                                          h.indexOf("/data/") >= 0 ||
                                          h.indexOf("ftp") >= 0)) {
                                    links.push(h);
                                }
                            }
                            // 扫描页面文字中的 URL（ftp/ftps/http链接）
                            var text = document.body.innerText || "";
                            var ftpMatches = text.match(/ftps?:\\/\\/[^\\s"'<>]+/gi);
                            if (ftpMatches) links = links.concat(ftpMatches);
                            var httpMatches = text.match(/https?:\\/\\/[^\\s"'<>]+\\.(zip|tar\\.gz|tiff?|nc|h5)/gi);
                            if (httpMatches) links = links.concat(httpMatches);
                            return JSON.stringify([...new Set(links)]);
                        })()""",
                        "returnByValue": True,
                    })
                    try:
                        page_urls = json.loads(page_links.get("result", {}).get("value") or "[]")
                        self._log(f"    [EnMAP] DOM 扫描到 {len(page_urls)} 个链接", also_print=False)
                        for u in page_urls:
                            self._log(f"    [EnMAP]     dom_url={u[:150]}", also_print=False)
                        urls.extend(page_urls)
                    except Exception:
                        pass

            except Exception as e:
                self._log(f"    [EnMAP] Playwright Orders 页面操作出错: {e}", level="error")
                import traceback
                self._log(f"    [EnMAP] traceback: {traceback.format_exc()}", level="error", also_print=False)
                self._save_screenshot(page, "pw_orders_error")
            finally:
                browser.close()

        urls = list(set(urls))
        if urls:
            self._log(f"    [EnMAP] Playwright 提取到 {len(urls)} 个下载链接")
        else:
            self._log("    [EnMAP] Playwright 也未能提取到下载链接", level="warning")
        return urls

    # ── 订单持久化（跨进程恢复）────────────────────────────────────────────

    @staticmethod
    def _order_file(save_dir: Path) -> Path:
        return save_dir / ".enmap_pending_order.json"

    def _save_order(self, save_dir: Path, order_id: str, scene_ids: List[str],
                    pre_existing_files: Optional[List[str]] = None):
        """将订单信息持久化到磁盘，供下次运行恢复轮询。
        pre_existing_files: 下单前 FTPS 服务器上已有的文件名列表，用于过滤历史文件。
        """
        import json as _json
        data = {
            "order_id": order_id,
            "scene_ids": scene_ids,
            "submitted_at": datetime.now().isoformat(),
            "pre_existing_files": pre_existing_files or [],
        }
        self._order_file(save_dir).write_text(_json.dumps(data, indent=2))

    def _load_order(self, save_dir: Path) -> Optional[Dict]:
        """读取未完成的订单信息。"""
        import json as _json
        f = self._order_file(save_dir)
        if not f.exists():
            return None
        try:
            return _json.loads(f.read_text())
        except Exception:
            return None

    def _clear_order(self, save_dir: Path):
        self._order_file(save_dir).unlink(missing_ok=True)

    # ── Flask daemon 接口:check_pending ─────────────────────────────────────────

    def check_pending(self, save_dir: Path) -> List[Path]:
        """
        给 web/app.py 后台 daemon 调用:检查指定目录里有没有未完成的 EnMAP 订单。
          - 没 pending → return []
          - pending 但未 ready → return []
          - pending 且 ready → 下载到 save_dir,清理订单文件,返回已下载文件列表
        失败也 return [](吞错,daemon 下个周期重试)
        取链接顺序与主流程 download() 一致:GWT-RPC → Playwright → FTPS 目录列表。
        EnMAP 实际通过 FTPS 交付,FTPS 兜底是已就绪订单能否下载的关键。
        """
        save_dir = Path(save_dir)
        pending = self._load_order(save_dir)
        if not pending:
            return []
        order_id = pending.get("order_id")
        if not order_id:
            return []
        try:
            self._check_deps()
            session = None
            # 首选来源:通知邮箱按 EOWEB Order Id 取交付 FTPS 链接。
            # 「Delivery Notice」邮件本身即「就绪」信号,且正文里 Order Id ↔ dims
            # 链接现成(pending.order_id 与邮件 'order with Id =' 同格式,直接匹配),
            # 比 GWT 轮询 + FTPS 列目录可靠。拿不到再回退原流程。一单可能多包,全取。
            urls = []
            try:
                from . import mail_links
                urls = mail_links.find_enmap_ftps_by_order_id(order_id) or []
                if urls:
                    print(f"    [EnMAP check_pending] 邮箱命中订单 {order_id} 交付链接 {len(urls)} 个")
            except Exception as _e:
                print(f"    [EnMAP check_pending] 邮件取链接失败,回退原流程: {_e}")
                urls = []

            if not urls:
                session = self._cas_login_requests(_EOWEB_SERVICE)
                is_ready, urls = self._check_order_ready(session, order_id)
                if is_ready is not True:
                    return []
            if not urls:
                try:
                    urls = self._extract_download_urls_playwright(order_id) or []
                except Exception:
                    urls = []
            if not urls:
                # 订单已 PROCESSED 但 GWT/Playwright 都拿不到链接 —— EnMAP 实际是
                # 通过 FTPS 服务器(download.dsda.dlr.de)交付的,必须列目录按本次
                # 订单文件名匹配。旧"简化版"漏了这一步,导致已就绪订单永远下不下来,
                # daemon 一直误报"仍在等数据中心备货"。逻辑与主流程 download() 一致。
                try:
                    ftps_files = self._list_ftps_files()
                except Exception as e:
                    print(f"    [EnMAP check_pending] FTPS 列目录失败: {e}")
                    ftps_files = []
                if ftps_files:
                    expected = (getattr(self, "_expected_filenames_cache", None)
                                or pending.get("expected_files"))
                    if expected:
                        matched = [f for f in ftps_files if f in expected]
                        if matched:
                            urls = self._build_ftps_urls(matched)
                    else:
                        # 回退:只取本次下单后新增的文件(排除下单前已有的历史文件)
                        pre_existing = set(pending.get("pre_existing_files", []))
                        new_files = [f for f in ftps_files if f not in pre_existing]
                        if new_files:
                            urls = self._build_ftps_urls(new_files)
            if not urls:
                return []
            from .base import download_with_resume
            downloaded = []
            for url in urls:
                filename = url.split("/")[-1].split("?")[0] or "enmap_product.zip"
                dest = save_dir / filename
                try:
                    if url.lower().startswith("ftp"):
                        # 续传 + 远端 SIZE 校验:大小不符不算成功(不再把截断档当成品)
                        if self._fetch_ftps_verified(url, dest):
                            downloaded.append(dest)
                        else:
                            print(f"    [EnMAP check_pending] {filename} 未下全/校验未过,保留 pending 下轮再续")
                    else:
                        if dest.exists() and dest.stat().st_size > 0:
                            downloaded.append(dest)
                            continue
                        download_with_resume(session, url, dest, desc=filename, timeout=600)
                        downloaded.append(dest)
                except Exception as e:
                    print(f"    [EnMAP check_pending] 下载失败 {filename}: {e}")
            # 仅当所有文件都下全且校验通过才清订单,否则保留供下个周期续传
            if downloaded and len(downloaded) == len(urls):
                self._clear_order(save_dir)
            return downloaded
        except Exception as e:
            print(f"    [EnMAP check_pending] 处理失败: {e}")
            return []

    # ── FTPS 目录列表 ─────────────────────────────────────────────────────────

    def _list_ftps_files(self) -> List[str]:
        """
        登录 DLR FTPS 服务器，列出根目录下的所有 .tar.gz 文件名。

        DLR EnMAP 下载服务器：download.dsda.dlr.de
        用户名：credentials 中的 username（如 kevin_jh-cat1distributor）
        密码：credentials 中的 password

        链接格式示例：
          ftps://kevin_jh-cat1distributor@download.dsda.dlr.de//dims_op_oc_oc-en_703425667_1.tar.gz
        """
        import ftplib
        import socket
        import ssl
        _FTPS_HOST = "download.dsda.dlr.de"
        _FTPS_PORT = 21

        def _socks5_available() -> bool:
            try:
                with socket.create_connection(("127.0.0.1", 10793), timeout=1):
                    return True
            except OSError:
                return False

        use_socks5 = _socks5_available()
        filenames: List[str] = []

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                if use_socks5:
                    try:
                        import socks

                        class _SocksFTPTLS(ftplib.FTP_TLS):
                            def ntransfercmd(self, cmd, rest=None):
                                import socket as _socket
                                import ssl as _ssl
                                size = None
                                if self.passiveserver:
                                    # PASV 返回的 IP 可能是服务器内网/127.0.0.1，强制用原始主机名
                                    _, dport = self.makepasv()
                                    dhost = _FTPS_HOST
                                    _timeout = self.timeout if self.timeout is not _socket._GLOBAL_DEFAULT_TIMEOUT else 60
                                    conn = socks.create_connection(
                                        (dhost, dport),
                                        proxy_type=socks.SOCKS5,
                                        proxy_addr="127.0.0.1",
                                        proxy_port=10793,
                                        timeout=_timeout,
                                    )
                                    try:
                                        if rest is not None:
                                            self.sendcmd("REST %s" % rest)
                                        resp = self.sendcmd(cmd)
                                        if resp[0] == '2':
                                            resp = self.getresp()
                                        if resp[0] != '1':
                                            raise ftplib.error_reply(resp)
                                    except Exception:
                                        conn.close()
                                        raise
                                    if self._prot_p:
                                        # 不传 server_hostname，避免内网 IP TLS SNI 不匹配
                                        ctx = _ssl.create_default_context()
                                        ctx.check_hostname = False
                                        ctx.verify_mode = _ssl.CERT_NONE
                                        conn = ctx.wrap_socket(conn)
                                else:
                                    conn, size = super().ntransfercmd(cmd, rest)
                                    return conn, size
                                if resp[:3] == '150':
                                    size = ftplib.parse150(resp)
                                return conn, size

                        ftp = _SocksFTPTLS()
                        raw_sock = socks.create_connection(
                            (_FTPS_HOST, _FTPS_PORT),
                            proxy_type=socks.SOCKS5,
                            proxy_addr="127.0.0.1",
                            proxy_port=10793,
                            timeout=60,
                        )
                        ftp.host = _FTPS_HOST
                        ftp.sock = raw_sock
                        ftp.af = raw_sock.family
                        ftp.file = raw_sock.makefile("r", encoding="latin-1")
                        ftp.welcome = ftp.getresp()
                        ftp.auth()
                        self._log(f"    [EnMAP] FTPS 目录列表通过 SOCKS5 连接 {_FTPS_HOST}", also_print=False)
                    except ImportError:
                        ftp = ftplib.FTP_TLS()
                        ftp.connect(_FTPS_HOST, _FTPS_PORT, timeout=60)
                else:
                    ftp = ftplib.FTP_TLS()
                    ftp.connect(_FTPS_HOST, _FTPS_PORT, timeout=60)

                ftp.login(self._username, self._password)
                ftp.prot_p()
                ftp.set_pasv(True)
                # 防止 LIST 数据传输无限阻塞
                try:
                    ftp.sock.settimeout(60)
                except Exception:
                    pass

                # 列出根目录下的文件
                lines: List[str] = []
                try:
                    ftp.retrlines("LIST", lines.append)
                except ssl.SSLEOFError:
                    pass  # 服务器关闭方式不标准，数据已收到
                try:
                    ftp.quit()
                except Exception:
                    pass

                for line in lines:
                    # LIST 输出格式: "-rw-r--r-- 1 user group size date filename"
                    parts = line.split()
                    if parts:
                        name = parts[-1]
                        if name.endswith(".tar.gz") or name.endswith(".zip"):
                            filenames.append(name)

                self._log(f"    [EnMAP] FTPS 目录列出 {len(filenames)} 个文件: {filenames}", also_print=False)
                self._log(f"    [EnMAP] FTPS 服务器上共 {len(filenames)} 个可下载文件")
                break  # 成功，退出重试循环
            except Exception as e:
                wait = 2 ** attempt * 10  # 10s, 20s, 40s
                if attempt < max_attempts - 1:
                    self._log(f"    [EnMAP] FTPS 目录列表失败（第{attempt+1}次），{wait}s后重试: {e}", level="warning")
                    time.sleep(wait)
                else:
                    self._log(f"    [EnMAP] FTPS 目录列表失败: {e}", level="warning")

        return filenames

    def _build_ftps_urls(self, filenames: List[str]) -> List[str]:
        """根据 FTPS 文件名列表拼接完整下载 URL。

        格式: ftps://{username}@download.dsda.dlr.de//{filename}
        """
        return [
            f"ftps://{self._username}@download.dsda.dlr.de//{name}"
            for name in filenames
        ]

    # ── 主下载入口 ────────────────────────────────────────────────────────────

    def _download_ftps(self, url: str, dest: Path, resume: bool = False):
        """通过 ftplib.FTP_TLS 下载 ftps:// 链接到 dest。
        若检测到 SOCKS5 代理（快柠檬 10793），通过 PySocks 建立隧道。
        控制连接和 PASV 数据连接都走 SOCKS5。

        resume=True 且 dest 已存在部分字节时,用 FTP REST 从断点续传(追加写),
        否则从头覆盖下载。单次调用;超时/断流会抛异常,交给上层重试。
        """
        import ftplib
        import socket
        import ssl
        from urllib.parse import urlparse
        p = urlparse(url)
        host = p.hostname
        port = p.port or 21
        path = p.path
        username = p.username or self._username or "anonymous"
        password = p.password or self._password or ""

        # 检测 SOCKS5 代理（快柠檬 10793）
        def _socks5_available() -> bool:
            try:
                with socket.create_connection(("127.0.0.1", 10793), timeout=1):
                    return True
            except OSError:
                return False

        use_socks5 = _socks5_available()

        if use_socks5:
            try:
                import socks  # PySocks

                # 子类化 FTP_TLS，覆盖数据连接使其也走 SOCKS5
                class _SocksFTPTLS(ftplib.FTP_TLS):
                    def ntransfercmd(self, cmd, rest=None):
                        import socket as _socket
                        size = None
                        if self.passiveserver:
                            # PASV 返回的 IP 可能是服务器内网/127.0.0.1，强制用原始主机名
                            _, dport = self.makepasv()
                            dhost = host
                            conn = socks.create_connection(
                                (dhost, dport),
                                proxy_type=socks.SOCKS5,
                                proxy_addr="127.0.0.1",
                                proxy_port=10793,
                                timeout=60,
                            )
                            # 数据连接单独设置读超时（控制连接 ftp.sock.settimeout 无效）
                            # 这是 recv 空闲超时:_FTPS_IDLE_TIMEOUT 内无任何数据即判连接死,
                            # 由上层 _fetch_ftps_verified 做 REST 续传重连(而不是干等)。
                            conn.settimeout(_FTPS_IDLE_TIMEOUT)
                            try:
                                if rest is not None:
                                    self.sendcmd("REST %s" % rest)
                                resp = self.sendcmd(cmd)
                                if resp[0] == '2':
                                    resp = self.getresp()
                                if resp[0] != '1':
                                    raise ftplib.error_reply(resp)
                            except Exception:
                                conn.close()
                                raise
                            if self._prot_p:
                                # 不传 server_hostname，避免内网 IP TLS SNI 不匹配
                                ctx = ssl.create_default_context()
                                ctx.check_hostname = False
                                ctx.verify_mode = ssl.CERT_NONE
                                conn = ctx.wrap_socket(conn)
                        else:
                            conn, size = super().ntransfercmd(cmd, rest)
                            return conn, size
                        if resp[:3] == '150':
                            size = ftplib.parse150(resp)
                        return conn, size

                ftp = _SocksFTPTLS()
                # 控制连接通过 SOCKS5
                raw_sock = socks.create_connection(
                    (host, port),
                    proxy_type=socks.SOCKS5,
                    proxy_addr="127.0.0.1",
                    proxy_port=10793,
                    timeout=60,
                )
                ftp.host = host
                ftp.sock = raw_sock
                ftp.af = raw_sock.family
                ftp.file = raw_sock.makefile("r", encoding="latin-1")
                ftp.welcome = ftp.getresp()
                ftp.auth()   # AUTH TLS，内部重建 ftp.file
                self._log(f"    [EnMAP] FTPS 通过 SOCKS5 隧道连接 {host}:{port}", also_print=False)

            except ImportError:
                self._log("    [EnMAP] PySocks 未安装，FTP 将直连（pip install PySocks）", level="warning")
                ftp = ftplib.FTP_TLS()
                ftp.connect(host, port, timeout=60)
        else:
            # 非 SOCKS5 直连路径：同样需要对数据连接设超时
            # ftplib.FTP_TLS 没有暴露数据 socket，通过子类 ntransfercmd 注入
            class _TimeoutFTPTLS(ftplib.FTP_TLS):
                def ntransfercmd(self, cmd, rest=None):
                    conn, size = super().ntransfercmd(cmd, rest)
                    conn.settimeout(_FTPS_IDLE_TIMEOUT)
                    return conn, size

            ftp = _TimeoutFTPTLS()
            ftp.connect(host, port, timeout=60)

        ftp.login(username, password)
        ftp.prot_p()   # 数据连接加密
        ftp.set_pasv(True)
        # 注：ftp.sock 是控制连接，settimeout 对数据传输无效
        # 数据连接超时已在 ntransfercmd 子类中通过 conn.settimeout(1800) 设置
        dest.parent.mkdir(parents=True, exist_ok=True)

        # 获取文件大小用于进度显示
        total_size = 0
        try:
            total_size = ftp.size(path) or 0
        except Exception:
            pass

        # 断点续传:dest 已有部分字节且小于总大小时，从断点 REST 续传
        offset = 0
        if resume and dest.exists():
            have = dest.stat().st_size
            if 0 < have and (not total_size or have < total_size):
                offset = have

        received = [offset]
        start_ts = [time.time()]
        last_log_ts = [time.time()]
        fname_display = dest.name[:50]
        _LOG_INTERVAL = 10  # 每 10 秒输出一次进度行（带 \n，确保日志框能读到）

        def _write_with_progress(chunk: bytes):
            f.write(chunk)
            received[0] += len(chunk)
            now = time.time()
            if now - last_log_ts[0] < _LOG_INTERVAL:
                return
            last_log_ts[0] = now
            elapsed = now - start_ts[0]
            speed_kb = received[0] / 1024 / max(elapsed, 0.1)
            if total_size:
                pct = received[0] / total_size * 100
                done_mb = received[0] / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                print(
                    f"      {fname_display}  "
                    f"{done_mb:.1f}/{total_mb:.1f} MB  "
                    f"{pct:.0f}%  "
                    f"{speed_kb:.0f} KB/s",
                    flush=True,
                )
            else:
                done_mb = received[0] / (1024 * 1024)
                print(
                    f"      {fname_display}  "
                    f"{done_mb:.1f} MB  "
                    f"{speed_kb:.0f} KB/s",
                    flush=True,
                )

        with open(dest, "ab" if offset else "wb") as f:
            try:
                ftp.retrbinary(f"RETR {path}", _write_with_progress,
                               rest=offset if offset else None)
            except ssl.SSLEOFError:
                pass  # 服务器关闭方式不标准，文件已写入
        try:
            ftp.quit()
        except Exception:
            pass

    def _ftps_size(self, url: str) -> Optional[int]:
        """取远端 FTPS 文件字节数,用于校验下载完整性;失败返回 None。"""
        import ftplib
        from urllib.parse import urlparse
        p = urlparse(url)
        host = p.hostname
        port = p.port or 21
        user = p.username or self._username or "anonymous"
        pwd  = p.password or self._password or ""
        ftp = ftplib.FTP_TLS()
        try:
            ftp.connect(host, port, timeout=60)
            ftp.login(user, pwd)
            ftp.prot_p()
            ftp.voidcmd("TYPE I")
            return int(ftp.size(p.path))
        except Exception:
            return None
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    def _fetch_ftps_verified(self, url: str, dest: Path, attempts: int = 6) -> bool:
        """带 REST 续传 + 退避重试 + 远端 SIZE 校验的 FTPS 下载。

        只有 dest 落地大小 == 远端 SIZE 时才返回 True;断流/超时会自动从断点续传。
        这是 daemon/主流程都该用的"下到底且校验完整"的入口,堵住两类老坑:
          - 半死连接涓流把整个下载拖死(靠 _FTPS_IDLE_TIMEOUT + 重连续传化解);
          - 截断档被当成品(靠 SIZE 校验,大小不符绝不算成功)。
        拿不到远端 SIZE 时退回旧行为(非空即视为成功),不至于卡死无 SIZE 的服务器。
        """
        expect = self._ftps_size(url)
        if expect is None and dest.exists() and dest.stat().st_size > 0:
            # 拿不到远端 SIZE 又已有非空文件:退回旧"非空即接受",避免无谓重下
            return True
        for attempt in range(1, attempts + 1):
            have = dest.stat().st_size if dest.exists() else 0
            if expect:
                if have == expect:
                    return True
                if have > expect:                 # 脏档,清掉重下
                    self._log(f"    [EnMAP] {dest.name} 落地({have})超过远端({expect}),清掉重下",
                              level="warning")
                    dest.unlink()
            try:
                self._download_ftps(url, dest, resume=True)
            except Exception as e:
                got = dest.stat().st_size if dest.exists() else 0
                wait = min(60, 10 * attempt)
                self._log(f"    [EnMAP] {dest.name} 传输中断@{got}B(第{attempt}/{attempts}次): {e}",
                          level="warning")
                if attempt < attempts:
                    time.sleep(wait)
                continue
            got = dest.stat().st_size if dest.exists() else 0
            if expect is None:
                return got > 0
            if got == expect:
                self._log(f"    [EnMAP] [完成] {dest.name} ({got}B,校验通过)")
                return True
            self._log(f"    [EnMAP] {dest.name} 落地 {got} != 远端 {expect},续传重试",
                      level="warning")
            if attempt < attempts:
                time.sleep(min(60, 10 * attempt))
        return False

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 3,
        poll_interval: int = 60,
        order_timeout: int = 28800,
        on_polling_started=None,
        defer_poll: bool = False,
        **kwargs,
    ) -> List[Path]:
        """
        EnMAP 完整下载流程：
          1. 检查是否有未完成的持久化订单 → 有则直接恢复轮询
          2. 提取 scene_id 列表（最多 max_items 个）
          3. Playwright 在 EOWEB UI 中下单
          4. 持久化订单信息到磁盘
          5. 轮询等待订单就绪（阻塞，最长 order_timeout 秒）
          6. download_with_resume 下载文件
          7. 下载成功后清除持久化订单
        """
        self._check_deps()
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logger(save_dir)

        # ── 检查未完成订单 ─────────────────────────────────────────
        # 若磁盘上有未完成订单，先校验 scene_ids 是否与本次搜索结果一致：
        # - 一致 → 恢复轮询，避免重复下单
        # - 不一致（本次搜索到不同的景）→ 清除旧订单，重新下单
        pending = self._load_order(save_dir)
        if pending:
            current_ids = set(
                item["id"] for item in (search_results or [])[:max_items]
                if item.get("id")
            )
            saved_ids = set(pending.get("scene_ids", []))
            if current_ids and current_ids != saved_ids:
                self._log(
                    f"    [EnMAP] 已有订单与本次搜索结果不一致"
                    f"（本次 {len(current_ids)} 景，已保存 {len(saved_ids)} 景），"
                    f"清除旧订单重新下单"
                )
                self._clear_order(save_dir)
                pending = None
        if pending:
            order_id = pending["order_id"]
            scene_ids = pending.get("scene_ids", [])
            submitted = pending.get("submitted_at", "?")
            self._log(f"    [EnMAP] 发现未完成订单，恢复轮询")
            self._log(f"    [EnMAP] 订单 ID: {order_id}  (提交于 {submitted})")
            self._log(f"    [EnMAP] 场景数: {len(scene_ids)}")
        else:
            if not search_results:
                self._log(f"    [EnMAP] 无搜索结果，跳过下载")
                return []

            items = search_results[:max_items]
            scene_ids = [item["id"] for item in items if item.get("id")]

            if not scene_ids:
                self._log("    [EnMAP] 无有效 scene_id，跳过下载")
                return []

            self._log(f"    [EnMAP] 将下单 {len(scene_ids)} 个场景")

            # 从 search_results 中提取搜索参数
            first_props = items[0].get("properties", {}) if items else {}
            order_start = first_props.get("_search_start")
            order_end   = first_props.get("_search_end")
            order_bbox_raw = first_props.get("_search_bbox")
            order_bbox: Optional[Tuple[float, float, float, float]] = (
                tuple(order_bbox_raw) if order_bbox_raw and len(order_bbox_raw) == 4 else None
            )

            # fallback：用各 scene datetime 推算
            if not order_start or not order_end:
                dates = [
                    item["properties"].get("datetime", "")[:10]
                    for item in items
                    if item.get("properties", {}).get("datetime", "")
                ]
                if dates:
                    order_start = order_start or min(dates)
                    order_end   = order_end   or max(dates)

            # 下单前先快照 FTPS 上已有文件，用于后续过滤历史订单文件
            self._log("    [EnMAP] 记录下单前 FTPS 已有文件...")
            pre_existing_files = self._list_ftps_files()

            # 下单
            order_id = self._submit_order(
                scene_ids,
                search_start=order_start,
                search_end=order_end,
                search_bbox=order_bbox,
            )

            if not order_id:
                self._log("    [EnMAP] 下单失败", level="error")
                return []

            # 校正 order_id:下单页面文字常刮到订单列表里的旧行(实测存成 4 天前的
            # 旧单),导致后续按 order_id 提链接/查状态都对不上。提交后立即查
            # getOrderSummaries 取最新一张订单——那就是刚下的这张——以它为准。
            try:
                _sess = self._cas_login_requests(_EOWEB_SERVICE)
                newest = self._newest_order_id(_sess)
                if newest and newest != order_id:
                    self._log(f"    [EnMAP] order_id 校正: 页面取到 {order_id} → 最新订单 {newest}")
                    order_id = newest
            except Exception as e:
                self._log(f"    [EnMAP] order_id 校正跳过(沿用页面值 {order_id}): {e}",
                          level="warning", also_print=False)

            # 持久化订单信息（含下单前已有文件快照）
            self._save_order(save_dir, order_id, scene_ids, pre_existing_files=pre_existing_files)
            self._log(f"    [EnMAP] 订单已保存，若任务中断可自动恢复轮询")
            # 通知应用内消息中心(web/app.py 子进程 stdout 协议解析)
            print(f"__NOTIFY__:info:EnMAP 订单已提交:order_id={order_id},等异步处理(默认最长 8h)")

        # ── 轮询等待 ──────────────────────────────────────────────
        # defer_poll=True 时主流程不阻塞,把轮询交给 web 后台 daemon
        if defer_poll:
            if on_polling_started is not None:
                on_polling_started()   # 也通知优先策略层:EnMAP 已下单,其他传感器可启动
            self._log("    [EnMAP] defer_poll=True,主流程不阻塞等待,Flask daemon 将自动接管轮询和后续下载")
            return []

        if on_polling_started is not None:
            on_polling_started()   # 通知调度层：EnMAP 已进入轮询，其他传感器可以启动
        order_ready, download_urls = self._poll_order(
            order_id,
            poll_interval=poll_interval,
            timeout=order_timeout,
        )

        if order_ready and not download_urls:
            # 订单已就绪但 GWT-RPC 未返回链接，用 Playwright 从 Orders 页面提取
            self._log("    [EnMAP] GWT-RPC 未获取到下载链接，尝试 Playwright 提取...")
            download_urls = self._extract_download_urls_playwright(order_id)

        if order_ready and not download_urls:
            # Playwright 也未能提取，直接登录 FTPS 服务器列出可下载文件
            self._log("    [EnMAP] Playwright 未能提取，尝试 FTPS 目录列表...")
            ftps_files = self._list_ftps_files()
            if ftps_files:
                # 优先用 getOrderSummaries 提取到的预期文件名精确匹配
                expected = getattr(self, '_expected_filenames_cache', None)
                if not expected and pending:
                    expected = pending.get("expected_files")
                if expected:
                    matched = [f for f in ftps_files if f in expected]
                    if matched:
                        self._log(f"    [EnMAP] 精确匹配本次订单文件 {len(matched)} 个")
                        download_urls = self._build_ftps_urls(matched)
                    else:
                        self._log(f"    [EnMAP] 预期文件 {expected} 尚未出现在 FTPS", level="warning")
                else:
                    # 回退：只保留本次下单后新增的文件（排除下单前已有的历史文件）
                    pre_existing = set(pending.get("pre_existing_files", []) if pending else [])
                    new_files = [f for f in ftps_files if f not in pre_existing]
                    if new_files:
                        self._log(f"    [EnMAP] 本次新增文件 {len(new_files)} 个（FTPS 共 {len(ftps_files)} 个，历史 {len(pre_existing)} 个）")
                        download_urls = self._build_ftps_urls(new_files)
                    else:
                        self._log(f"    [EnMAP] FTPS 上 {len(ftps_files)} 个文件均为历史订单，本次订单尚未生成文件", level="warning")
                self._log(f"    [EnMAP] FTPS 目录列出 {len(ftps_files)} 个文件准备下载")

        if not download_urls:
            self._log("    [EnMAP] 未获取到下载链接", level="warning")
            self._log("    [EnMAP] 订单信息已保存，下次运行将自动恢复轮询")
            self._log("    [EnMAP] 也可登录 https://eoweb.dlr.de/egp/main#Orders 手动下载")
            return []

        # ── 下载文件 ──────────────────────────────────────────────
        downloaded: List[Path] = []
        try:
            dl_session = self._cas_login_requests(_EOWEB_SERVICE)
        except Exception as e:
            self._log(f"    [EnMAP] 下载登录失败: {e}", level="error")
            return []

        for url in download_urls:
            filename = url.split("/")[-1].split("?")[0] or "enmap_product.zip"
            dest = save_dir / filename
            if url.lower().startswith("ftp"):
                # 续传 + 远端 SIZE 校验:既不会被半死连接拖死,也不会把截断档当成品
                self._log(f"    [EnMAP] 下载: {filename}")
                if self._fetch_ftps_verified(url, dest):
                    downloaded.append(dest)
                else:
                    self._log(f"    [EnMAP] [跳过] {filename} 多次续传仍未下全/校验未过", level="error")
                continue
            # 非 FTP(HTTP)链接:沿用 download_with_resume + 3 次重试
            if dest.exists() and dest.stat().st_size > 0:
                self._log(f"    [EnMAP] 已存在，跳过: {filename}")
                downloaded.append(dest)
                continue
            success = False
            for attempt in range(1, 4):
                try:
                    self._log(f"    [EnMAP] 下载: {filename}（第{attempt}次）")
                    download_with_resume(dl_session, url, dest, desc=filename, timeout=600)
                    downloaded.append(dest)
                    self._log(f"    [EnMAP] [完成] {filename}")
                    success = True
                    break
                except Exception as e:
                    self._log(f"    [EnMAP] [错误] {filename} 第{attempt}次: {e}", level="error")
                    if attempt < 3:
                        time.sleep(30)
            if not success:
                self._log(f"    [EnMAP] [跳过] {filename} 3次均失败", level="error")

        # 全部下载成功后清除持久化订单；部分失败则保留，下次运行可跳过已存在文件
        if len(downloaded) == len(download_urls):
            self._clear_order(save_dir)
            self._log(f"    [EnMAP] 已清除订单缓存")
        elif downloaded:
            self._log(f"    [EnMAP] 部分下载失败（{len(downloaded)}/{len(download_urls)}），订单缓存保留", level="warning")

        return downloaded
