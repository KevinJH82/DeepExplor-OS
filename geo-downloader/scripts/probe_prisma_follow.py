#!/usr/bin/env python3
"""
跟随重定向探针:在已登录浏览器里 fetch /api/v2/products/<id>/download,
这次 redirect:'follow',看最终落点 + 是否真能拿到 .he5。
并对比:用 page.request(带全部浏览器 cookie)直接 GET,看 content-type。
若浏览器能下、requests 不能 → 差在 cookie(JSESSIONID 等),可复制 cookie 解决。

用法: python3 scripts/probe_prisma_follow.py
"""
import os, sys, json
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import yaml
from playwright.sync_api import sync_playwright

_PORTAL = "http://prisma.asi.it/missionselect/"
_CATALOG = "http://prisma.asi.it/js-cat-client-prisma-src/"
_DL = "http://prisma.asi.it/api/v2/products/422787/download"
_OUT = _ROOT / "scripts" / "_prisma_probe_out"; _OUT.mkdir(exist_ok=True)


def main():
    c = yaml.safe_load((_ROOT/"config"/"credentials.yaml").read_text("utf-8"))["prisma"]
    headless = os.environ.get("HEADLESS","1") != "0"
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=headless)
        ctx = b.new_context(accept_downloads=True,
                            user_agent="Mozilla/5.0 (compatible; geo-downloader/1.0)")
        page = ctx.new_page()
        page.goto(_PORTAL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(2500)
        if page.query_selector("input[name='username']"):
            page.fill("input[name='username']", c["username"])
            page.fill("input[name='password']", c["password"])
            for s in ["button[type='submit']","input[type='submit']"]:
                if page.query_selector(s): page.click(s); break
            page.wait_for_load_state("networkidle", timeout=90_000)
            page.wait_for_timeout(2500)
        print("登录:", "missionselect" in page.url)
        page.goto(_CATALOG, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(3000)

        # 1. fetch follow，看最终 URL / content-type / 前几个字节
        print("\n=== 1. fetch(redirect:follow)===")
        res = page.evaluate("""async (url)=>{
            try{
              const r = await fetch(url, {credentials:'include'});  // 默认 follow
              const ct = r.headers.get('content-type');
              const cl = r.headers.get('content-length');
              const buf = await r.arrayBuffer();
              const head = Array.from(new Uint8Array(buf).slice(0,8));
              let txt = '';
              if (ct && (ct.includes('html')||ct.includes('json')))
                  txt = new TextDecoder().decode(buf.slice(0,300));
              return {status:r.status, ok:r.ok, finalUrl:r.url, redirected:r.redirected,
                      contentType:ct, contentLength:cl, bytes:buf.byteLength,
                      head8:head, txt};
            }catch(e){return{error:String(e)};}
        }""", _DL)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        # .he5/HDF5 魔数 = 0x89 H D F = [137,72,68,70]
        if isinstance(res, dict) and res.get("head8", [])[:4] == [137,72,68,70]:
            print("  ✅✅ 这是 HDF5 文件!浏览器能直接下!")

        # 2. page.request(带全部浏览器 cookie 的服务端请求)
        print("\n=== 2. page.request.get(带浏览器全量 cookie)===")
        try:
            r2 = page.request.get(_DL, max_redirects=10)
            body = r2.body()
            print(json.dumps({"status": r2.status, "url": r2.url,
                              "contentType": r2.headers.get("content-type"),
                              "bytes": len(body),
                              "head8": list(body[:8])}, ensure_ascii=False, indent=2))
            if body[:4] == b'\x89HDF':
                fn = _OUT / "DOWNLOADED_TEST.he5"
                fn.write_bytes(body)
                print(f"  ✅✅ HDF5!已存 {fn} ({len(body)/1e6:.1f} MB)")
        except Exception as e:
            print("  page.request 失败:", e)

        # 3. dump 全部 cookie(供 requests 复制对照)
        print("\n=== 3. 浏览器 cookies(domain/name)===")
        for ck in ctx.cookies():
            print(f"  {ck['domain']:24} {ck['name']:16} (path={ck.get('path')})")
        b.close()


if __name__ == "__main__":
    main()
