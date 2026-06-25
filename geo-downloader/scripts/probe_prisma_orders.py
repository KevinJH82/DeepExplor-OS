#!/usr/bin/env python3
"""
Orders 面板探针:catalog 右下角有 "EO Orders status" / "EO Product basket" 按钮。
自动点开 "EO Orders status",截图,dump 面板里的订单行 + 下载/动作元素,
找到 extref_146602 后尝试触发下载并全量记网络(找出能工作的真实下载请求)。

用法: HEADLESS=0 python3 scripts/probe_prisma_orders.py
"""
import os, sys, json
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import yaml
from playwright.sync_api import sync_playwright

_PORTAL = "http://prisma.asi.it/missionselect/"
_CATALOG = "http://prisma.asi.it/js-cat-client-prisma-src/"
_OUT = _ROOT / "scripts" / "_prisma_probe_out"; _OUT.mkdir(exist_ok=True)
_ORDER = "extref_146602"


def main():
    c = yaml.safe_load((_ROOT/"config"/"credentials.yaml").read_text("utf-8"))["prisma"]
    netlog = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=False, slow_mo=250)
        ctx = b.new_context(accept_downloads=True,
                            viewport={"width": 1600, "height": 1000},
                            user_agent="Mozilla/5.0 (compatible; geo-downloader/1.0)")
        page = ctx.new_page()

        def on_resp(resp):
            try:
                u = resp.url
                if "geoserver" in u or "wmts" in u or u.endswith((".png",".js",".css",".gif")):
                    return
                netlog.append({"status": resp.status, "method": resp.request.method,
                               "url": u, "ct": resp.headers.get("content-type",""),
                               "cd": resp.headers.get("content-disposition","")})
            except Exception: pass
        page.on("response", on_resp)

        # 捕获新标签/弹出页(EO Orders status 很可能开新页)
        popups = []
        def attach(p, tag):
            p.on("response", on_resp)
            p.on("download", lambda d: on_dl(d))
        def on_page(p):
            popups.append(p)
            print(f"  ✦✦ 新页面打开: {p.url}")
            attach(p, "popup")
        ctx.on("page", on_page)

        dl_info = {}
        def on_dl(d):
            print(f"  ⬇⬇ DOWNLOAD: {d.suggested_filename}  url={d.url}")
            dl_info["url"] = d.url; dl_info["name"] = d.suggested_filename
            try:
                p = _OUT / ("DL_" + d.suggested_filename)
                d.save_as(str(p)); dl_info["saved"] = str(p)
                print(f"     已存 {p} ({p.stat().st_size/1e6:.1f} MB)")
            except Exception as e:
                print("     保存失败:", e)
        page.on("download", on_dl)

        # 登录 + 加载
        page.goto(_PORTAL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(2500)
        if page.query_selector("input[name='username']"):
            page.fill("input[name='username']", c["username"])
            page.fill("input[name='password']", c["password"])
            for s in ["button[type='submit']","input[type='submit']"]:
                if page.query_selector(s): page.click(s); break
            page.wait_for_load_state("networkidle", timeout=90_000)
            page.wait_for_timeout(2500)
        page.goto(_CATALOG, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(8000)

        # 点 "EO Orders status"(按钮文字嵌在 label>span,且偶尔渲染慢)
        print("=== 点开 EO Orders status ===")
        clicked = False
        # 先等底部按钮出现
        for _ in range(15):
            if page.query_selector("text=Orders status"):
                break
            page.wait_for_timeout(2000)
        for sel in ["span:has-text('EO Orders status')",
                    "label:has-text('Orders status')",
                    ".ui-button:has-text('Orders status')",
                    "text=EO Orders status"]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                try:
                    el.click(force=True); clicked = True; print(f"  点了 {sel}"); break
                except Exception as ex:
                    print(f"  {sel} 点击异常 {ex}")
        if not clicked:
            # 坐标兜底(probe_ui 实测稳定在 ~1578,963)
            print("  选择器失败,坐标兜底点 (1578,963)")
            page.mouse.click(1578, 963); clicked = True
        page.wait_for_timeout(5000)
        page.screenshot(path=str(_OUT / "ord_01_panel.png"))
        print("  截图: ord_01_panel.png  (clicked=%s)" % clicked)

        # 若开了新页面,聚焦它、截图、dump、把订单/下载元素抓出来
        op = popups[-1] if popups else None
        if op:
            try:
                op.bring_to_front(); op.wait_for_load_state("networkidle", timeout=60_000)
            except Exception: pass
            op.wait_for_timeout(4000)
            print(f"\n=== 订单页 URL: {op.url} ===")
            op.screenshot(path=str(_OUT / "ord_02_popup.png"))
            opinfo = op.evaluate("""() => {
                const out=[];
                for (const el of document.querySelectorAll('a,button,td,span,div,img,[onclick],[role=button]')) {
                    const t=(el.innerText||el.textContent||'').trim();
                    const title=el.getAttribute('title')||'';
                    const href=el.getAttribute('href')||'';
                    const blob=(t+' '+title+' '+href+' '+(el.className||'')).toLowerCase();
                    if ((/extref|completed|download|scarica|deliver|ftp|\.he5|order/i).test(blob)
                        && (t.length<120)) {
                        const r=el.getBoundingClientRect();
                        if(r.width>0&&r.height>0)
                          out.push({tag:el.tagName,text:t.slice(0,80),title,href:href.slice(0,120),
                                    cls:(el.className||'').toString().slice(0,40),
                                    x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});
                    }
                }
                const seen=new Set(),u=[];for(const e of out){const k=e.tag+e.text+e.href;if(!seen.has(k)){seen.add(k);u.push(e);}}
                return u.slice(0,40);
            }""")
            print("=== 订单页里的订单/下载元素 ===")
            for e in opinfo:
                print(f"  <{e['tag']}> '{e['text'][:55]}' href={e['href'][:50]} title='{e['title']}' @({e['x']},{e['y']})")
            (_OUT/"ord_popup_elements.json").write_text(json.dumps(opinfo,ensure_ascii=False,indent=2),"utf-8")
            page = op  # 后续观察窗口聚焦到订单页

        # dump 面板里所有出现 extref / COMPLETED / download / 订单号 的元素
        print("\n=== 面板内容(找订单行 + 动作)===")
        info = page.evaluate("""(order) => {
            const rows = [];
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText||'').trim();
                if (!t || t.length>120) continue;
                const low = t.toLowerCase();
                if (low.includes('extref') || low.includes('completed') ||
                    low.includes(order.toLowerCase()) || low.includes('download') ||
                    low.includes('scarica')) {
                    const r = el.getBoundingClientRect();
                    if (r.width>0&&r.height>0)
                        rows.push({tag:el.tagName, text:t.slice(0,100),
                                   cls:(el.className||'').toString().slice(0,40),
                                   title:el.getAttribute('title')||'',
                                   x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
                }
            }
            const seen=new Set(), u=[];
            for(const e of rows){const k=e.tag+e.text; if(!seen.has(k)){seen.add(k);u.push(e);}}
            return u.slice(0,30);
        }""", _ORDER)
        for e in info:
            print(f"  <{e['tag']}> '{e['text'][:60]}' title='{e['title']}' cls={e['cls'][:25]} @({e['x']},{e['y']})")
        (_OUT/"ord_elements.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),"utf-8")

        # 留窗口 150s:请在订单面板里点 extref_146602 的下载,我记网络
        print("\n>>> 窗口保留 150s。请在 Orders 面板里找到 extref_146602 并点下载/动作图标。")
        print(">>> 我在全量记网络;一旦触发下载会自动保存。")
        for _ in range(15):
            page.wait_for_timeout(10_000)
            page.screenshot(path=str(_OUT / "ord_live.png"))

        (_OUT/"ord_netlog.json").write_text(json.dumps(netlog, ensure_ascii=False, indent=2),"utf-8")
        print(f"\n网络日志 {len(netlog)} 条(已滤掉地图/静态)→ ord_netlog.json")
        for n in netlog:
            print(" ", json.dumps(n, ensure_ascii=False)[:170])
        if dl_info: print("\n下载信息:", json.dumps(dl_info, ensure_ascii=False))
        b.close()


if __name__ == "__main__":
    main()
