#!/usr/bin/env python3
"""
分发探针:弄清 PRISMA 已完成订单的 Distribution(分发)请求格式,
以便在 prisma.py 里用 requests 复现端到端下载。

承接 probe_prisma_playwright.py 的结论:
  - 无 Bearer token;/api/v2/products/<id>/download 是死路;
  - 真机制 = Distribution,经 prisma-cat/service.php 提交。

本探针(登录后,全部只读探测):
  1. dump service.php?request=dynamicguis&type=Distribution / Processing(分发表单字段定义)
  2. 探测 service.php 的订单列表/详情接口,找订单的 distribution{} spec
     (尝试 request=orders / getorders / order / userorders 等)
  3. 抓取 catalog 主 JS(acs-eo-cat-module-min.js 等),本地 grep
     distribut / service.php / request= / ORDSServiceProxy / processItem
     / delivery_method / dist_spec —— 还原分发请求是怎么拼的。

用法:
  python3 scripts/probe_prisma_distribution.py
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
_SVC = "http://prisma.asi.it/prisma-cat/service.php"
_ORDER_ID = "extref_146602"
_USER = "CHN_SNN2_2629_1$"

_OUT = _ROOT / "scripts" / "_prisma_probe_out"
_OUT.mkdir(exist_ok=True)


def _creds():
    c = yaml.safe_load((_ROOT / "config" / "credentials.yaml").read_text("utf-8"))
    p = c.get("prisma") or {}
    return p["username"], p["password"]


def _fetch_json(page, url):
    """页面上下文 fetch,返回 (status, text)。"""
    return page.evaluate("""async (url) => {
        try {
            const r = await fetch(url, {credentials:'include'});
            return {status:r.status, text:(await r.text())};
        } catch(e){ return {status:-1, text:String(e)}; }
    }""", url)


def main():
    username, password = _creds()
    headless = os.environ.get("HEADLESS", "1") != "0"
    captured = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=True,
                                  user_agent="Mozilla/5.0 (compatible; geo-downloader/1.0)")
        page = ctx.new_page()
        page.on("request", lambda r: captured.append((r.method, r.url))
                if ("service.php" in r.url or "distribut" in r.url.lower()
                    or "order" in r.url.lower()) else None)

        # ── 登录 ──
        print("=== 登录 ===")
        page.goto(_PORTAL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(2500)
        if page.query_selector("input[name='username']"):
            page.fill("input[name='username']", username)
            page.fill("input[name='password']", password)
            for sel in ["button[type='submit']", "input[type='submit']"]:
                if page.query_selector(sel):
                    page.click(sel); break
            page.wait_for_load_state("networkidle", timeout=90_000)
            page.wait_for_timeout(2500)
        print("  URL:", page.url, " 登录:", "missionselect" in page.url)

        # 必须先加载 catalog 客户端初始化 PHP session
        page.goto(_CATALOG, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(5000)

        # ── 1. dynamicguis 表单定义 ──
        print("\n=== 1. Distribution / Processing 表单定义 ===")
        for typ in ("Distribution", "Processing"):
            res = _fetch_json(page, f"{_SVC}?request=dynamicguis&type={typ}")
            fn = _OUT / f"dynamicguis_{typ}.json"
            fn.write_text(res["text"], "utf-8")
            print(f"  {typ}: HTTP {res['status']}, {len(res['text'])} bytes → {fn.name}")
            print("    head:", res["text"][:300].replace("\n", " "))

        # ── 2. 订单列表 / 详情接口探测 ──
        print("\n=== 2. 订单接口探测(找 distribution spec)===")
        probes = [
            f"{_SVC}?request=orders&username={_USER}",
            f"{_SVC}?request=getorders&username={_USER}",
            f"{_SVC}?request=userorders&username={_USER}",
            f"{_SVC}?request=order&id={_ORDER_ID}",
            f"{_SVC}?request=order&externalOrderId={_ORDER_ID}",
            f"{_SVC}?request=getorder&externalOrderId={_ORDER_ID}",
            f"{_SVC}?request=orderdetail&externalOrderId={_ORDER_ID}",
        ]
        for u in probes:
            res = _fetch_json(page, u)
            body = res["text"][:200].replace("\n", " ")
            marker = "★" if (res["status"] == 200 and len(res["text"]) > 5
                             and "error" not in body.lower()[:40]) else " "
            print(f"  {marker} HTTP {res['status']:>4}  {u.split('service.php')[1][:70]}")
            print(f"        {body}")
            if marker == "★":
                (_OUT / f"order_probe_{abs(hash(u))%10000}.json").write_text(res["text"], "utf-8")

        # ── 3. catalog 主 JS 逆向 ──
        print("\n=== 3. 抓 catalog JS 并 grep 分发逻辑 ===")
        html = _fetch_json(page, _CATALOG)["text"]
        js_rel = re.findall(r'src="([^"]*\.js[^"]*)"', html)
        # 优先 module/min JS
        js_rel = sorted(set(js_rel), key=lambda s: (("cat-module" not in s and "min" not in s), s))
        keywords = ["distribut", "service.php", "request:", "request=", "ORDSServiceProxy",
                    "processItem", "distributeItem", "delivery_method", "dist_spec",
                    "externalOrderId", "submitOrder", "doOrder"]
        for rel in js_rel[:12]:
            full = rel if rel.startswith("http") else _CATALOG + rel.lstrip("./")
            res = _fetch_json(page, full)
            if res["status"] != 200 or len(res["text"]) < 100:
                continue
            txt = res["text"]
            hits = {k: txt.lower().count(k.lower()) for k in keywords}
            hits = {k: v for k, v in hits.items() if v}
            if hits:
                name = rel.split("/")[-1]
                (_OUT / f"js_{name}").write_text(txt, "utf-8")
                print(f"  {name} ({len(txt)} bytes) hits: {hits}")

        print("\n=== 抓到的 service.php/order 请求(浏览器自身发的)===")
        seen = set()
        for m, u in captured:
            key = u.split("?")[0] + "?" + (u.split("?")[1][:50] if "?" in u else "")
            if key not in seen:
                seen.add(key)
                print(f"  {m} {u[:140]}")

        print(f"\n输出目录: {_OUT}")
        browser.close()


if __name__ == "__main__":
    main()
