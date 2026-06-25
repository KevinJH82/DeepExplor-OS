#!/usr/bin/env python3
"""
一次性探针:用 Playwright 真实浏览器登录 PRISMA 门户,弄清「下载已完成订单」
到底走哪种机制 —— 决定后续是 (A) 直接浏览器下载 / 抓 Bearer token 注入,
还是 (B) 提交 distribution 分发请求 + FTPS 收件。

它做四件事(只读探测,不动交付目录):
  1. 用 WSO2 OAuth2 流程在真浏览器里登录(凭据取自 config/credentials.yaml)。
  2. 全程监听网络:打印任何带 Authorization 头的请求,以及命中
     /api/v2/products 或 prisma-orders-status 的请求(含请求头)。
  3. 登录后 dump cookies / localStorage / sessionStorage,找 access_token / Bearer。
  4. 在页面上下文里直接 fetch 那个挂起订单的下载 URL,看返回 200+二进制
     还是 302 跳 carbon 登录页 —— 这一步最关键。

用法:
  HEADLESS=0 python3 scripts/probe_prisma_playwright.py   # 想看浏览器就 HEADLESS=0
  python3 scripts/probe_prisma_playwright.py
"""
import os
import re
import sys
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml
from playwright.sync_api import sync_playwright

_PORTAL = "http://prisma.asi.it/missionselect/"
_CATALOG = "http://prisma.asi.it/js-cat-client-prisma-src/"
# 实测挂起订单(ea6f9b88 / 云顶4口井):
_PROBE_DOWNLOAD = "http://prisma.asi.it/api/v2/products/422787/download"
_ORDERS_STATUS = "http://prisma.asi.it/prisma-orders-status/"

_SHOT_DIR = _ROOT / "scripts" / "_prisma_probe_out"
_SHOT_DIR.mkdir(exist_ok=True)


def _creds():
    c = yaml.safe_load((_ROOT / "config" / "credentials.yaml").read_text("utf-8"))
    p = c.get("prisma") or {}
    return p["username"], p["password"]


def main():
    username, password = _creds()
    headless = os.environ.get("HEADLESS", "1") != "0"
    interesting = []  # 捕获的关键请求

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=True,
                                   user_agent="Mozilla/5.0 (compatible; geo-downloader/1.0)")
        page = ctx.new_page()

        def on_request(req):
            try:
                hdrs = req.headers
                auth = hdrs.get("authorization")
                url = req.url
                hot = ("/api/v2/products" in url or "prisma-orders-status" in url
                       or "/prisma-cat" in url or auth)
                if hot:
                    interesting.append({
                        "method": req.method, "url": url,
                        "authorization": auth,
                        "has_auth": bool(auth),
                    })
                    tag = "🔑AUTH" if auth else "  "
                    print(f"  [REQ {tag}] {req.method} {url[:120]}")
                    if auth:
                        print(f"           Authorization: {auth[:80]}...")
            except Exception:
                pass

        page.on("request", on_request)

        # ── 1. 登录 ─────────────────────────────────────────────
        print("\n=== STEP 1: 打开门户(将跳 WSO2 登录)===")
        page.goto(_PORTAL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(3000)
        print("  当前 URL:", page.url)

        # WSO2 登录表单:尝试多组选择器
        if "commonauth" in page.url or "authenticationendpoint" in page.url \
                or page.query_selector("input[name='username']"):
            print("  → 命中 WSO2 登录页,填表单…")
            for sel in ["input[name='username']", "#usernameUserInput", "#username"]:
                el = page.query_selector(sel)
                if el:
                    el.fill(username); break
            for sel in ["input[name='password']", "#password"]:
                el = page.query_selector(sel)
                if el:
                    el.fill(password); break
            page.screenshot(path=str(_SHOT_DIR / "01_login_filled.png"))
            for sel in ["button[type='submit']", "input[type='submit']",
                        "#loginForm button", "button:has-text('LOGIN')",
                        "button:has-text('Sign')"]:
                el = page.query_selector(sel)
                if el:
                    el.click(); break
            page.wait_for_load_state("networkidle", timeout=90_000)
            page.wait_for_timeout(3000)
        print("  登录后 URL:", page.url)
        page.screenshot(path=str(_SHOT_DIR / "02_after_login.png"))
        logged_in = "missionselect" in page.url or "prisma" in page.url
        print("  登录成功?", logged_in)

        # ── 2. 加载 catalog 客户端,让它发 API 调用 ────────────────
        print("\n=== STEP 2: 加载 catalog 客户端(观察它如何授权 API)===")
        page.goto(_CATALOG, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(6000)
        page.screenshot(path=str(_SHOT_DIR / "03_catalog.png"))

        # ── 3. dump storage / cookies 找 token ────────────────────
        print("\n=== STEP 3: 找 token(cookies / localStorage / sessionStorage)===")
        cookies = ctx.cookies()
        print("  cookies:", [c["name"] for c in cookies])
        storage = page.evaluate("""() => {
            const dump = (s) => { const o={}; for (let i=0;i<s.length;i++){const k=s.key(i); o[k]=s.getItem(k);} return o; };
            return { local: dump(localStorage), session: dump(sessionStorage) };
        }""")
        token_found = None
        for scope, kv in storage.items():
            for k, v in kv.items():
                sv = str(v)
                if re.search(r"token|bearer|authoriz|jwt|access", k, re.I) or \
                   re.match(r"ey[A-Za-z0-9_-]{10,}\.", sv):
                    print(f"  [{scope}] {k} = {sv[:90]}")
                    if not token_found and len(sv) > 20:
                        token_found = sv
        if not token_found:
            print("  (storage 里没找到明显 token)")

        # ── 4. 关键:在页面上下文 fetch 下载 URL ──────────────────
        print("\n=== STEP 4: 页面内 fetch 下载 URL(决定性测试)===")
        print("  URL:", _PROBE_DOWNLOAD)
        result = page.evaluate("""async (url) => {
            try {
                const r = await fetch(url, {credentials:'include', redirect:'manual'});
                const ct = r.headers.get('content-type');
                const cl = r.headers.get('content-length');
                let bodyHead = '';
                if (ct && ct.includes('json')) bodyHead = (await r.text()).slice(0,300);
                else if (ct && ct.includes('html')) bodyHead = (await r.text()).slice(0,200);
                return {status:r.status, type:r.type, ok:r.ok, contentType:ct,
                        contentLength:cl, redirected:r.redirected, url:r.url, bodyHead};
            } catch(e) { return {error:String(e)}; }
        }""", _PROBE_DOWNLOAD)
        print("  fetch 结果:", json.dumps(result, ensure_ascii=False, indent=2))

        # 同时查订单状态接口(确认状态查询在浏览器里也通)
        print("\n=== STEP 4b: 页面内查订单状态 ===")
        status_res = page.evaluate("""async (base) => {
            try {
                const u = base + '?userId=CHN_SNN2_2629_1$&externalOrderId=extref_146602';
                const r = await fetch(u, {credentials:'include'});
                const t = await r.text();
                return {status:r.status, body:t.slice(0,500)};
            } catch(e){ return {error:String(e)}; }
        }""", _ORDERS_STATUS)
        print("  订单状态:", json.dumps(status_res, ensure_ascii=False, indent=2))

        print("\n=== 汇总 ===")
        print("  登录:", "OK" if logged_in else "失败")
        print("  捕获到带 Authorization 的请求:",
              sum(1 for r in interesting if r["has_auth"]))
        print("  storage token:", "找到" if token_found else "未找到")
        (_SHOT_DIR / "captured_requests.json").write_text(
            json.dumps(interesting, ensure_ascii=False, indent=2), "utf-8")
        print(f"  截图 + 抓包已存到 {_SHOT_DIR}")

        browser.close()


if __name__ == "__main__":
    main()
