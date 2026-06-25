"""
PRISMA Hyperspectral Downloader — ASI (Agenzia Spaziale Italiana)
通过 ASI PRISMA 数据门户 (https://prisma.asi.it) 搜索和下载。

注册（免费）：
  https://prisma.asi.it/missionselect/

产品：PRISMA L2D（地表反射率，几何和大气校正）
波段：239波段，400-2500nm（VNIR+SWIR），分辨率30m
格式：HDF5 (.he5)
"""

import re
import time
import json
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urljoin

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .base import BaseDownloader, download_with_chunks as download_with_resume

# ASI PRISMA 门户端点
_PRISMA_BASE     = "http://prisma.asi.it"
_PRISMA_PORTAL   = f"{_PRISMA_BASE}/missionselect/"
_PRISMA_DOWNLOAD = f"{_PRISMA_BASE}/api/v2/products"

# 订单状态服务:真实 URL 在 EO 模块 config 的 orders_status_url 里(=/prisma-orders-status/),
# 旧代码用正则从 minified JS 提取 orders_status_url 总是失败(它只是 b.orders_status_url 引用,
# 真值在 data/config/acs_eo_cat_module.json)。这里直接读模块 config,取不到再回退默认。
_PRISMA_EO_MODULE_CFG = f"{_PRISMA_BASE}/js-cat-client-prisma-src/data/config/acs_eo_cat_module.json"
_PRISMA_ORDERS_STATUS = f"{_PRISMA_BASE}/prisma-orders-status/"   # 兜底默认

# 异步订单超过此时长仍未就绪/未失败 → daemon 放弃(避免像 extref_146602 那样无限挂)
_PRISMA_ASYNC_GIVEUP_SECONDS = 3 * 86400
_PRISMA_READY_STATUSES  = {"completed", "ready", "available", "done",
                           "delivered", "processed", "success", "distributed"}
_PRISMA_FAILED_STATUSES = {"failed", "rejected", "cancelled", "canceled",
                           "error", "aborted", "invalid", "refused", "expired"}


class PRISMADownloader(BaseDownloader):

    PLATFORM_NAME = "prisma"
    REQUIRES_AUTH = True

    def __init__(self, credentials: Dict[str, str], output_dir: str = "./downloads", **kwargs):
        super().__init__(credentials=credentials, output_dir=output_dir)
        self._session: Optional[Any] = None

    def _check_deps(self):
        if not HAS_REQUESTS:
            raise ImportError("缺少依赖: requests\n请运行: pip install requests")

    def _authenticate(self) -> "requests.Session":
        """
        通过 WSO2 OAuth2/OIDC 流程登录 ASI PRISMA 门户，返回带认证 cookie 的 Session。
        流程：
          1. GET http://prisma.asi.it/missionselect/ — 获取 PHPSESSID（第一跳返回循环重定向）
          2. 再次 GET /missionselect/（带 PHPSESSID）→ 302 到 OAuth2 授权 URL
          3. GET OAuth2 授权 URL → 302 到 WSO2 登录页，提取 sessionDataKey
          4. POST WSO2 commonauth 提交用户名密码
          5. 跟随 OAuth2 授权码回调，最终落地 /missionselect/
        """
        if self._session is not None:
            return self._session

        import requests as req
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = req.Session()
        session.trust_env = False  # 全程直连，避免认证 session 与 catalog session IP 不一致
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; geo-downloader/1.0)",
        })
        # 自动重试（直连偶尔会被服务器断开）
        _retry = Retry(total=3, backoff_factor=1,
                       status_forcelist=[500, 502, 503, 504],
                       allowed_methods=["GET", "POST"])
        session.mount("http://",  HTTPAdapter(max_retries=_retry))
        session.mount("https://", HTTPAdapter(max_retries=_retry))

        # Step 1: 第一次访问，只为获取 PHPSESSID，不跟随重定向
        session.get(_PRISMA_PORTAL, timeout=30, allow_redirects=False)

        # Step 2: 带 PHPSESSID 再次访问，不继续跟随，拿 OAuth2 授权 URL
        r2 = session.get(_PRISMA_PORTAL, timeout=30, allow_redirects=False)
        oauth2_url = r2.headers.get("Location", "")
        if not oauth2_url or "oauth2/authorize" not in oauth2_url:
            raise RuntimeError(
                f"PRISMA 未返回 OAuth2 授权 URL，实际 Location: {oauth2_url}\n"
                "门户认证流程可能已变更。"
            )

        # Step 3: 访问 OAuth2 授权 URL（HTTPS 直连），提取 sessionDataKey
        r3 = session.get(oauth2_url, timeout=60, allow_redirects=True)
        r3.raise_for_status()

        session_data_key = None
        parsed = urlparse(r3.url)
        qs = parse_qs(parsed.query)
        if "sessionDataKey" in qs:
            session_data_key = qs["sessionDataKey"][0]
        if not session_data_key:
            m = re.search(r'name=["\']sessionDataKey["\'][^>]+value=["\']([^"\']+)', r3.text)
            if not m:
                m = re.search(r'value=["\']([^"\']+)["\'][^>]+name=["\']sessionDataKey', r3.text)
            if m:
                session_data_key = m.group(1)

        if not session_data_key:
            raise RuntimeError(
                "PRISMA 登录失败：无法获取 sessionDataKey，门户登录流程可能已变更。\n"
                f"当前登录页 URL: {r3.url}"
            )

        # Step 4: POST 到 WSO2 commonauth，不自动跟随（HTTPS→HTTP 直连无此问题）
        auth_url = urljoin(r3.url, "../commonauth")
        payload = {
            "username":       self.credentials["username"],
            "password":       self.credentials["password"],
            "sessionDataKey": session_data_key,
        }
        r4 = session.post(auth_url, data=payload, timeout=60,
                          headers={"Referer": r3.url}, allow_redirects=False)

        # 手动跟随重定向（直连情况下 HTTPS→HTTP 可正常跳转）
        _hop = r4
        for _ in range(10):
            loc = _hop.headers.get("Location", "")
            if not loc:
                break
            if loc.startswith("/"):
                _parsed = urlparse(_hop.url)
                loc = f"{_parsed.scheme}://{_parsed.netloc}{loc}"
            _hop = session.get(loc, timeout=60, allow_redirects=False,
                        headers={"Referer": _hop.url})
            if _hop.status_code == 200:
                break
        r4 = _hop
        if r4.status_code not in (200, 302) and r4.status_code >= 400:
            r4.raise_for_status()

        # Step 5: 验证是否成功落地门户
        if "missionselect" not in r4.url and "missionselect" not in r4.text.lower():
            raise RuntimeError(
                "PRISMA 登录失败，请检查账号密码。\n"
                "注册地址: https://prisma.asi.it/missionselect/"
            )

        self._session = session
        return session

    def _solr_search(self, session, bbox, start_date, end_date, cloud_cover, max_results=50):
        """
        通过 Solr query API 搜索 PRISMA 产品（L0 原始数据）。
        认证：先访问 catalog HTML 客户端初始化 PHP session，再调用 service.php。
        全程直连（trust_env=False），避免代理导致 catalog session IP 不一致。
        """
        min_lon, min_lat, max_lon, max_lat = bbox

        _CAT_CLIENT = f"{_PRISMA_BASE}/js-cat-client-prisma-src/"
        _SVC = f"{_PRISMA_BASE}/prisma-cat/service.php"

        # 访问 catalog HTML 客户端，初始化 PHP session（必须步骤，否则 service.php 返回 401）
        try:
            session.get(_CAT_CLIENT, timeout=20, allow_redirects=True)
        except Exception:
            pass

        # 时间格式：YYYY-MM-DD → YYYY-MM-DDT00:00:00Z
        def _dt(d):
            return d if "T" in d else d + "T00:00:00Z"

        bbox_wkt = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        # Solr range 语法（来自 API 文档）：
        #   field=val]  表示 field <= val
        #   field=[val  表示 field >= val
        params = {
            "request": "query",
            "core":    "products",
            "rows":    str(max_results),
            "validitystart_dt": f"[{_dt(start_date)}",  # >= start_date
            "fq": f'geom_srpt:"Intersects({bbox_wkt})"',
        }
        # 按云量过滤（cloud_pctg_f 是百分比 0-100）
        if cloud_cover < 100:
            params["cloud_pctg_f"] = f"{cloud_cover}]"  # <= cloud_cover

        r = session.get(_SVC, params=params, timeout=60)
        if r.status_code == 401:
            raise RuntimeError(
                "PRISMA catalog 搜索 401 未授权。catalog session 初始化失败，"
                f"请确认能访问 {_CAT_CLIENT}"
            )
        r.raise_for_status()
        return r.json()

    def _parse_solr_docs(self, data):
        """解析 Solr query 响应，提取产品元数据。"""
        # 响应结构: {"response": {"numFound": N, "docs": [...]}}
        resp = data.get("response", {})
        docs = resp.get("docs", [])
        results = []
        for doc in docs:
            rid = doc.get("id", "")
            id_inv = doc.get("id_inv_i")   # 整数 ID，供 api/v2 接口使用
            name = doc.get("filename_s", rid)
            date = doc.get("validitystart_dt", "")[:10]  # 只取日期部分
            cloud = doc.get("cloud_pctg_f", 0)
            dl_url = f"{_PRISMA_DOWNLOAD}/{id_inv or rid}/download"
            results.append({
                "id":           rid,
                "id_inv_i":     id_inv,     # 整数 ID
                "name":         name,
                "date":         date,
                "cloud_cover":  round(float(cloud), 1) if cloud else 0,
                "download_url": dl_url,
                "_raw":         doc,
            })
        return results

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 80,
        **kwargs,
    ) -> List[Dict]:
        """
        搜索 PRISMA L2D 产品（通过 Solr catalog 接口）。
        返回产品元数据列表，每项含 id / name / date / cloud_cover / download_url。
        """
        self._check_deps()
        session = self._authenticate()

        try:
            data = self._solr_search(
                session, bbox, start_date, end_date, cloud_cover,
                max_results=kwargs.get("count", 50),
            )
            results = self._parse_solr_docs(data)
        except Exception as e:
            print(f"    [警告] PRISMA 搜索失败: {e}")
            return []

        # 附加 _footprint 供覆盖选景使用
        try:
            from shapely import wkt as _wkt
            for r in results:
                raw = r.get("_raw", {})
                geom_wkt = raw.get("geom_srpt", "")
                if geom_wkt:
                    try:
                        r["_footprint"] = _wkt.loads(geom_wkt)
                        r["_cloud_cover"] = r.get("cloud_cover", 100)
                    except Exception:
                        pass
                # 采集日期（供时序选景使用，取不到则不挂）
                _d = (r.get("date") or "")[:10]
                if _d:
                    r["_acq_date"] = _d
        except ImportError:
            pass

        print(f"    找到 {len(results)} 景 PRISMA L2D 高光谱")
        for r in results[:3]:
            print(f"      {r['name']}  日期:{r['date']}  云量:{r['cloud_cover']}%")
        if len(results) > 3:
            print(f"      ... 共 {len(results)} 景")

        return results

    # ── 下单 ──────────────────────────────────────────────────────────────────

    def _submit_order(self, session, id_inv) -> Optional[str]:
        """
        通过 service.php?request=process 提交 L2D 处理请求。
        id_inv: (id_inv_i, raw_doc) 元组
        """
        _CAT_CLIENT = f"{_PRISMA_BASE}/js-cat-client-prisma-src/"
        _SVC = f"{_PRISMA_BASE}/prisma-cat/service.php"

        try:
            session.get(_CAT_CLIENT, timeout=20, allow_redirects=True)
        except Exception:
            pass

        try:
            import requests as _req
            from requests.adapters import HTTPAdapter
            probe = _req.Session()
            probe.cookies.update(session.cookies)
            probe.headers.update(session.headers)
            probe.trust_env = False
            probe.mount("http://",  HTTPAdapter(max_retries=0))
            probe.mount("https://", HTTPAdapter(max_retries=0))

            id_inv_val, raw_doc = id_inv
            filename = raw_doc.get("filename_s", "")
            doc_id = raw_doc.get("id", "")
            start_time = raw_doc.get("validitystart_dt", "")
            stop_time = raw_doc.get("validitystop_dt", "")

            payload = {
                "INPUT_NAME": filename,
                "id": doc_id,
                "processorname": "L2D",
                "start_time": start_time,
                "stop_time": stop_time,
                "POnOff": "PanOn",
                "VOnOff": "VnirOn",
                "SOnOff": "SwirOn",
                "L2_HGRP": 1,
                "UseGCP": "GCPNo",
                "SelOrBin": "BSel",
                "VnirBandSelect": "4-66",
                "SwirBandSelect": "1-170",
                "Binning": 1,
            }

            r = probe.post(
                _SVC,
                params={"request": "process"},
                json=payload,
                timeout=60,
                allow_redirects=False,
            )

            if r.status_code >= 400:
                # 解析错误信息
                try:
                    err = r.json().get("message", "")
                except Exception:
                    err = r.text[:200]
                if "quota" in err.lower():
                    print(f"    [PRISMA] 配额已用完: {err}")
                else:
                    print(f"    [PRISMA] process 失败 (HTTP {r.status_code}): {err}")
                return None

            try:
                data = r.json()
            except Exception:
                data = {}

            order_id = str(data.get("orderId") or data.get("order_id") or "")
            if not order_id and r.status_code == 200:
                order_id = f"inv_{id_inv_val}"

            if data.get("status") == "success":
                print(f"    [PRISMA] process 成功: orderId={order_id}")
            return order_id or None

        except Exception as e:
            print(f"    [PRISMA] order 请求失败: {e}")
            return None

    # ── 轮询 ──────────────────────────────────────────────────────────────────

    def _get_orders_status_url(self, session) -> str:
        """发现订单状态服务 URL。优先从 EO 模块 config 读 orders_status_url
        (真值 = /prisma-orders-status/),取不到则回退默认。结果缓存。"""
        cached = getattr(self, "_orders_status_url", None)
        if cached:
            return cached
        url = None
        try:
            r = session.get(_PRISMA_EO_MODULE_CFG, timeout=20)
            if r.status_code == 200:
                raw = (r.json() or {}).get("orders_status_url")
                if raw:
                    url = raw if raw.startswith("http") else \
                        f"{_PRISMA_BASE}{raw if raw.startswith('/') else '/' + raw}"
        except Exception:
            pass
        url = url or _PRISMA_ORDERS_STATUS
        self._orders_status_url = url
        print(f"    [PRISMA] orders_status_url = {url}")
        return url

    def _catalog_user_id(self, session) -> str:
        """订单查询用的 userId:门户里 acs.cat.username 才是真正的 userId
        (多数部署与登录用户名一致);提取失败则回退登录用户名。结果缓存。"""
        cached = getattr(self, "_cat_user_id", None)
        if cached:
            return cached
        uid = None
        try:
            html = session.get(f"{_PRISMA_BASE}/js-cat-client-prisma-src/", timeout=20).text
            m = re.search(r'acs\.cat\.username\s*=\s*"([^"]+)"', html)
            if m:
                uid = m.group(1)
        except Exception:
            pass
        uid = uid or self.credentials.get("username", "")
        self._cat_user_id = uid
        return uid

    def _query_order(self, session, order_id: str) -> Optional[Dict]:
        """查询单个订单的状态记录,返回订单 dict(含 status 等)或 None。"""
        try:
            import requests as _req
            from requests.adapters import HTTPAdapter
            probe = _req.Session()
            probe.cookies.update(session.cookies)
            probe.headers.update(session.headers)
            probe.trust_env = False
            probe.mount("http://",  HTTPAdapter(max_retries=0))
            probe.mount("https://", HTTPAdapter(max_retries=0))

            ords_url = self._get_orders_status_url(session)
            uid = self._catalog_user_id(session)
            r = probe.get(ords_url, params={
                "userId": uid,
                "externalOrderId": order_id,
            }, timeout=30)
            if r.status_code != 200:
                print(f"    [PRISMA] 订单查询失败: HTTP {r.status_code}")
                return None
            try:
                data = r.json()
            except Exception:
                return None
            if isinstance(data, list):
                for item in data:
                    if str(item.get("externalOrderId")) == str(order_id):
                        return item
                return data[0] if data else None
            return data if isinstance(data, dict) else None
        except Exception as e:
            print(f"    [PRISMA] 订单查询异常: {e}")
            return None

    def _order_age_seconds(self, pending: Dict) -> Optional[int]:
        """根据 pending 文件里的 submitted_at 计算订单已等待秒数。"""
        sa = pending.get("submitted_at")
        if not sa:
            return None
        try:
            return int(time.time() - datetime.fromisoformat(sa).timestamp())
        except Exception:
            return None

    def _check_order_ready(
        self, session, order_id: str
    ) -> Tuple[Optional[bool], Optional[str]]:
        """查询订单状态,返回 (is_ready, download_url)。
        is_ready=None 表示查询本身失败(调用方可重试/重认证)。"""
        order = self._query_order(session, order_id)
        if order is None:
            return None, None
        status = str(order.get("status", order.get("orderStatus", ""))).lower()
        print(f"订单状态: {status}", end="  ", flush=True)
        if status in _PRISMA_READY_STATUSES:
            dl_url = (order.get("download_url") or order.get("url") or
                      order.get("link") or order.get("downloadUrl"))
            return True, dl_url
        return False, None

    def _poll_order(
        self,
        session,
        order_id: str,
        product_id: str,
        poll_interval: int = 120,
        timeout: int = 28800,
    ) -> Tuple[bool, Optional[str]]:
        """
        轮询订单状态，等待 L2D 处理完成。
        返回 (is_ready, download_url)。
        """
        print(f"    [PRISMA] 开始轮询订单（每 {poll_interval}s，最长 {timeout // 60} 分钟）")
        print(f"    [PRISMA] 订单 ID: {order_id}")

        start_ts = time.time()
        poll_count = 0
        while True:
            elapsed = int(time.time() - start_ts)
            if elapsed >= timeout:
                print(f"\n    [PRISMA] 轮询超时（{timeout // 60} 分钟），请手动检查 PRISMA 门户")
                return False, None

            poll_count += 1
            print(f"    [PRISMA] 轮询 #{poll_count}  已等待 {elapsed // 60} 分钟  ", end="", flush=True)

            is_ready, dl_url = self._check_order_ready(session, order_id)
            print()  # 换行

            if is_ready is None:
                # session 可能失效，重新认证
                try:
                    self._session = None
                    session = self._authenticate()
                except Exception:
                    pass
                time.sleep(30)
                continue

            if is_ready:
                print(f"    [PRISMA] 订单已就绪")
                if dl_url:
                    return True, dl_url
                # 就绪但无链接：尝试通过 api/v2 构造下载链接
                dl_url = f"{_PRISMA_DOWNLOAD}/{product_id}/download"
                return True, dl_url

            time.sleep(poll_interval)

    # ── 订单持久化 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _order_file(save_dir: Path) -> Path:
        return save_dir / ".prisma_pending_order.json"

    def _save_order(
        self,
        save_dir: Path,
        order_id: str,
        product_ids: List[str],
        filename: str = "",
    ):
        # filename 持久化原因:重启后 search 顺序不稳，search_results[0].name
        # 可能漂移；订单已绑死那个产品，dest 必须跟订单走才能续传到原 .part
        data = {
            "order_id":    order_id,
            "product_ids": product_ids,
            "filename":    filename or "",
            "dl_url":      "",
            "submitted_at": datetime.now().isoformat(),
        }
        self._order_file(save_dir).write_text(json.dumps(data, indent=2))

    def _update_order_url(self, save_dir: Path, dl_url: str):
        data = self._load_order(save_dir) or {}
        data["dl_url"] = dl_url or ""
        self._order_file(save_dir).write_text(json.dumps(data, indent=2))

    def _load_order(self, save_dir: Path) -> Optional[Dict]:
        f = self._order_file(save_dir)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except Exception:
            return None

    def _clear_order(self, save_dir: Path):
        self._order_file(save_dir).unlink(missing_ok=True)

    # ── Flask daemon 接口:check_pending ─────────────────────────────────────────

    def check_pending(self, save_dir: Path) -> Optional[Path]:
        """
        给 web/app.py 后台 daemon 调用:检查指定目录里有没有未完成的 PRISMA 订单。
          - 没 pending → return None
          - pending 但未 ready → return None(daemon 下次再来)
          - pending 且 ready → 下载 .he5,清理订单文件,返回 .he5 路径
        失败也 return None(吞错,daemon 下个周期重试)
        """
        pending = self._load_order(Path(save_dir))
        if not pending:
            return None
        order_id = pending.get("order_id")
        if not order_id:
            return None
        try:
            self._check_deps()
            session = self._authenticate()
            order = self._query_order(session, order_id)

            # ⚠ 查询失败(order is None):无法判定状态 —— 绝不放弃,下轮重试。
            # (否则限流/瞬时 500 导致查询失败时,超龄订单会被误删、数据被白白丢弃)
            if order is None:
                return None

            status = str(order.get("status", "")).lower()

            # ① 终态失败 → 放弃:删订单文件,daemon 下轮自动收尾,不再无限等
            if status in _PRISMA_FAILED_STATUSES:
                print(f"    [PRISMA check_pending] 订单 {order_id} 状态={status},终态失败 → 放弃并清理")
                self._clear_order(Path(save_dir))
                return None

            # ② 拿到了确切的"非就绪"状态(仍在处理),且超过放弃阈值才放弃;否则下轮再来。
            #    只有 order 非 None(状态确实查到了)才会走到这,瞬时查询失败永远不会触发放弃。
            if status not in _PRISMA_READY_STATUSES:
                age = self._order_age_seconds(pending)
                if age is not None and age >= _PRISMA_ASYNC_GIVEUP_SECONDS:
                    print(f"    [PRISMA check_pending] 订单 {order_id} 已等 {age // 86400} 天仍未就绪"
                          f"(status={status or '未知'})→ 超时放弃并清理")
                    self._clear_order(Path(save_dir))
                return None

            # ③ 就绪 → 从通知邮箱取 L2D zip 直链。
            #    PRISMA 完成后把带令牌的下载链接邮件投递(令牌自带授权,匿名可下);
            #    /api/v2/products/<id>/download 端点实测连完全登录的浏览器也 404→carbon,
            #    已废弃。链接按 L0 文件名时间戳 <start>_<stop> 匹配回本订单。
            zip_url = self._emailed_zip_url(pending)
            if not zip_url:
                print(f"    [PRISMA check_pending] 订单 {order_id} 已 COMPLETED,"
                      f"但通知邮箱暂未找到下载链接(邮件未到/转发未生效)→ 下轮重试")
                return None
            try:
                self._update_order_url(Path(save_dir), zip_url)
            except Exception:
                pass
            he5 = self._download_and_extract_he5(zip_url, Path(save_dir))
            if he5 is None:
                return None
            self._clear_order(Path(save_dir))
            return he5
        except Exception as e:
            print(f"    [PRISMA check_pending] 处理失败: {e}")
            return None

    # ── 邮件取链接 + 下载解压 ───────────────────────────────────────────────────

    def _emailed_zip_url(self, pending: Dict) -> Optional[str]:
        """据 pending 订单的 L0 文件名时间戳,从通知邮箱找 L2D zip 直链;失败/未配置返回 None。"""
        try:
            from . import mail_links
        except Exception as e:
            print(f"    [PRISMA] 邮件模块不可用: {e}")
            return None
        name = pending.get("filename") or ""
        ts = mail_links.timestamps_from_filename(name)
        if not ts:
            print(f"    [PRISMA] 无法从文件名 '{name}' 提取时间戳,无法匹配邮件链接")
            return None
        url = mail_links.find_prisma_zip(ts)
        if url:
            print(f"    [PRISMA] 邮箱命中下载链接(ts={ts}): {url}")
        return url

    def _download_and_extract_he5(self, zip_url: str, save_dir: Path) -> Optional[Path]:
        """匿名下载 L2D zip(令牌自带授权),解出内部最大的 .he5 返回其路径;完成后删 zip 省空间。"""
        import zipfile, shutil
        import requests as _req
        from urllib.parse import urlparse
        from .base import download_with_chunks as _dl
        save_dir = Path(save_dir)
        # 已解出过则直接复用
        done = sorted(save_dir.glob("PRS_L2D*.he5"), key=lambda p: p.stat().st_size, reverse=True)
        if done and done[0].stat().st_size > 0:
            print(f"    [PRISMA] 已存在解出的 .he5,跳过: {done[0].name}")
            return done[0]
        zip_name = Path(urlparse(zip_url).path).name or "prisma_l2d.zip"
        zip_dest = save_dir / zip_name
        # 令牌授权,无需登录会话;trust_env=False 直连避免代理
        anon = _req.Session(); anon.trust_env = False
        anon.headers.update({"User-Agent": "Mozilla/5.0 (compatible; geo-downloader/1.0)"})
        try:
            _dl(anon, zip_url, zip_dest, desc=zip_name, timeout=600)
        except Exception as e:
            print(f"    [PRISMA] zip 下载失败: {e}")
            return None
        try:
            with zipfile.ZipFile(zip_dest) as z:
                he5 = [n for n in z.namelist() if n.lower().endswith(".he5")]
                if not he5:
                    print(f"    [PRISMA] zip 内未找到 .he5: {z.namelist()[:8]}")
                    return None
                he5.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
                target = he5[0]
                dest = save_dir / Path(target).name
                if not (dest.exists() and dest.stat().st_size > 0):
                    with z.open(target) as src, open(dest, "wb") as f:
                        shutil.copyfileobj(src, f, length=8 * 1024 * 1024)
                print(f"    [PRISMA] 解出 {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
                return dest
        except Exception as e:
            print(f"    [PRISMA] 解压失败: {e}")
            return None
        finally:
            try:
                zip_dest.unlink(missing_ok=True)
            except Exception:
                pass

    # ── 主下载入口 ─────────────────────────────────────────────────────────────

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 3,
        poll_interval: int = 120,
        order_timeout: int = 28800,
        on_polling_started=None,
        defer_poll: bool = False,
        **kwargs,
    ) -> List[Path]:
        """
        PRISMA 完整下载流程：
          1. 检查未完成的持久化订单 → 一致则恢复轮询
          2. 对每景产品提交 L2D process 请求
          3. 持久化订单信息
          4. 轮询等待订单就绪
          5. 下载 .he5 文件
        """
        self._check_deps()
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        session = self._authenticate()

        # ── 检查未完成订单 ─────────────────────────────────────────
        pending = self._load_order(save_dir)
        if pending:
            # 只比对"实际下单的那一景"是否仍在本次 search_results 里。
            # Solr 不保证返回顺序，参数完全一致时也可能因后台新 indexed 产品
            # 让 current_ids 集合多一两个；用严格 == 会误清订单导致重新等 8h。
            saved_ids = pending.get("product_ids") or []
            actually_ordered = str(saved_ids[0]) if saved_ids else ""
            current_ids = {
                str(it.get("id_inv_i") or it.get("id", ""))
                for it in (search_results or [])[:max_items]
            }
            if actually_ordered and current_ids and actually_ordered not in current_ids:
                print(
                    f"    [PRISMA] 已下单产品({actually_ordered})不在本次搜索结果中"
                    f"（本次 {len(current_ids)} 景），清除旧订单重新下单"
                )
                self._clear_order(save_dir)
                pending = None

        if pending:
            order_id   = pending["order_id"]
            product_ids = pending.get("product_ids", [])
            print(f"    [PRISMA] 发现未完成订单，恢复轮询")
            print(f"    [PRISMA] 订单 ID: {order_id}  (提交于 {pending.get('submitted_at', '?')})")
        else:
            if not search_results:
                print("    [PRISMA] 无搜索结果，跳过下载")
                return []

            items = search_results[:max_items]

            if not items:
                print("    [PRISMA] 无有效产品，跳过下载")
                return []

            print(f"    [PRISMA] 提交 {len(items)} 景 L2D process 请求...")

            # 传 (id_inv_i, raw_doc) 给 _submit_order
            first = items[0]
            order_id = self._submit_order(
                session,
                (first.get("id_inv_i"), first.get("_raw", {}))
            )
            if not order_id:
                print(
                    "    [PRISMA] 下单失败。ASI PRISMA L2D 需在 Catalog 手动下单：\n"
                    "      http://prisma.asi.it/missionselect/ → Catalog → process"
                )
                return []

            product_ids = [str(it.get("id_inv_i") or it.get("id", "")) for it in items]
            # 与订单绑死的最终 .he5 文件名，避免重启后 search 顺序变 dest 漂移
            ordered_name = first.get("name", product_ids[0] if product_ids else "prisma_product")
            if not ordered_name.endswith(".he5"):
                ordered_name = ordered_name + ".he5"
            self._save_order(save_dir, order_id, product_ids, filename=ordered_name)
            print(f"    [PRISMA] 下单成功，订单 ID: {order_id}")
            # 通知应用内消息中心(由 web/app.py 子进程 stdout 协议解析)
            print(f"__NOTIFY__:info:PRISMA 订单已提交:order_id={order_id},等异步处理(默认最长 8h)")

        # ── 轮询等待 ──────────────────────────────────────────────
        # defer_poll=True 时主流程不阻塞,把轮询交给 web 后台 daemon
        if defer_poll:
            if on_polling_started is not None:
                on_polling_started()
            print("    [PRISMA] defer_poll=True,主流程不阻塞等待,Flask daemon 将自动接管轮询和后续下载")
            return []

        if on_polling_started is not None:
            on_polling_started()

        order_ready, dl_url = self._poll_order(
            session, order_id, product_ids[0] if product_ids else "",
            poll_interval=poll_interval,
            timeout=order_timeout,
        )

        if not order_ready:
            print("    [PRISMA] 订单信息已保存，下次运行将自动恢复轮询")
            return []

        # ── 订单就绪 → 从通知邮箱取 L2D zip 直链,下载并解出 .he5 ──────────
        # （api/v2 已废弃;_poll_order 返回的 dl_url 不再使用,链接只走邮件投递)
        pending2 = self._load_order(save_dir) or {}
        if not pending2.get("filename"):
            nm = (search_results or [{}])[0].get("name", "")
            if nm:
                pending2 = {**pending2, "filename": nm if nm.endswith(".he5") else nm + ".he5"}
        zip_url = self._emailed_zip_url(pending2)
        if not zip_url:
            print("    [PRISMA] 订单就绪,但通知邮箱暂未找到下载链接(邮件未到/转发未生效)")
            print("           订单已持久化,下次运行或 daemon 会自动重试取链接")
            return []
        try:
            self._update_order_url(save_dir, zip_url)
        except Exception:
            pass
        he5 = self._download_and_extract_he5(zip_url, save_dir)
        if not he5:
            return []
        self._clear_order(save_dir)
        print(f"    [PRISMA] [完成] {he5.name}")
        return [he5]
