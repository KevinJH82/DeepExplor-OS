#!/usr/bin/env python3
"""
headed UI 探路:在真浏览器里登录 PRISMA catalog,记录全量网络流量,
并 dump 界面上 order/cart/download/basket 相关的可点元素 + 截图,
用于搞清「真人取已完成订单」走什么请求。

第一阶段只「看」不乱点(EXPLORE),把 UI map 交出来再决定下一步。
设 CLICK_ORDERS=1 时会尝试点命中 orders/cart 的元素。

用法:
  HEADLESS=0 python3 scripts/probe_prisma_ui.py
  HEADLESS=0 CLICK_ORDERS=1 python3 scripts/probe_prisma_ui.py
"""
import os, sys, json, time
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import yaml
from playwright.sync_api import sync_playwright

_PORTAL = "http://prisma.asi.it/missionselect/"
_CATALOG = "http://prisma.asi.it/js-cat-client-prisma-src/"
_OUT = _ROOT / "scripts" / "_prisma_probe_out"; _OUT.mkdir(exist_ok=True)
_KW = ("order", "cart", "basket", "download", "deliver", "distribut",
       "ordini", "carrello", "scarica", "richiest")  # 含意大利语


def main():
    c = yaml.safe_load((_ROOT/"config"/"credentials.yaml").read_text("utf-8"))["prisma"]
    headless = os.environ.get("HEADLESS", "1") != "0"
    do_click = os.environ.get("CLICK_ORDERS", "0") == "1"
    netlog = []

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=headless, slow_mo=300 if not headless else 0)
        ctx = b.new_context(accept_downloads=True,
                            viewport={"width": 1600, "height": 1000},
                            user_agent="Mozilla/5.0 (compatible; geo-downloader/1.0)")
        page = ctx.new_page()

        def on_resp(resp):
            try:
                u = resp.url
                ct = resp.headers.get("content-type", "")
                # 记录所有非静态资源 + 任何二进制/附件响应
                if any(x in u for x in ("service.php", "/api/", "orders", "download",
                       "distribut", ".he5", "octet", "/prisma-")) or \
                   "octet-stream" in ct or "attachment" in resp.headers.get("content-disposition", ""):
                    netlog.append({"status": resp.status, "method": resp.request.method,
                                   "url": u, "ct": ct,
                                   "cd": resp.headers.get("content-disposition", "")})
            except Exception:
                pass
        page.on("response", on_resp)
        page.on("download", lambda d: (
            print(f"  ⬇⬇ DOWNLOAD 触发: {d.suggested_filename} url={d.url}"),
            netlog.append({"DOWNLOAD": d.url, "filename": d.suggested_filename})))

        # 登录
        print("=== 登录 ===")
        page.goto(_PORTAL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(2500)
        if page.query_selector("input[name='username']"):
            page.fill("input[name='username']", c["username"])
            page.fill("input[name='password']", c["password"])
            for s in ["button[type='submit']", "input[type='submit']"]:
                if page.query_selector(s): page.click(s); break
            page.wait_for_load_state("networkidle", timeout=90_000)
            page.wait_for_timeout(2500)
        print("  登录:", "missionselect" in page.url)

        # 加载 catalog 客户端
        print("\n=== 加载 catalog 客户端 ===")
        page.goto(_CATALOG, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(6000)
        page.screenshot(path=str(_OUT / "ui_01_catalog.png"))

        # 等 "Loading modules" 弹窗消失,并点掉任何遮挡对话框
        print("  等模块加载 + 关弹窗 …")
        for i in range(20):  # 最多 ~40s
            page.wait_for_timeout(2000)
            # 点掉可见的对话框按钮(OK/Next/Close/Continue)
            for txt in ["OK", "Ok", "Next", "Continue", "Close", "Avanti", "Chiudi", "Accept"]:
                try:
                    btn = page.query_selector(f"button:has-text('{txt}'), .x-btn:has-text('{txt}')")
                    if btn and btn.is_visible():
                        btn.click(); print(f"    点掉对话框按钮: {txt}"); page.wait_for_timeout(1500)
                except Exception:
                    pass
            # 加载弹窗还在?
            loading = page.query_selector("text=/Loading modules/i")
            if not (loading and loading.is_visible()):
                print(f"    模块已加载(第{i+1}轮)"); break
        page.wait_for_timeout(3000)
        page.screenshot(path=str(_OUT / "ui_02_loaded.png"))
        print("  截图: ui_02_loaded.png")

        # dump 所有带文字的可点元素
        print("\n=== UI 可点元素(命中 order/cart/download 关键词)===")
        elems = page.evaluate("""(kw) => {
            const out = [];
            const all = document.querySelectorAll(
              'button,a,[role=button],.x-btn,.x-menu-item,[onclick],span,div,td,li');
            for (const el of all) {
                const t = (el.innerText||el.textContent||'').trim();
                const title = el.getAttribute('title')||'';
                const cls = el.className && el.className.toString ? el.className.toString() : '';
                const blob = (t+' '+title+' '+cls+' '+(el.id||'')).toLowerCase();
                if (t.length>0 && t.length<40 && kw.some(k=>blob.includes(k))) {
                    const r = el.getBoundingClientRect();
                    if (r.width>0 && r.height>0)
                        out.push({tag:el.tagName, text:t.slice(0,40), title, id:el.id||'',
                                  cls:cls.slice(0,60), x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
                }
            }
            // 去重
            const seen=new Set(), uniq=[];
            for(const e of out){const k=e.tag+e.text+e.id; if(!seen.has(k)){seen.add(k);uniq.push(e);}}
            return uniq.slice(0,40);
        }""", list(_KW))
        for e in elems:
            print(f"  <{e['tag']}> '{e['text']}' id={e['id']} cls={e['cls'][:30]} @({e['x']},{e['y']})")
        (_OUT / "ui_elements.json").write_text(json.dumps(elems, ensure_ascii=False, indent=2), "utf-8")

        # 也 dump 顶层工具栏/菜单按钮(不限关键词),帮我看整体布局
        toolbar = page.evaluate("""() => {
            const out=[];
            for (const el of document.querySelectorAll('.x-btn,.x-tool,[role=button],button')) {
                const t=(el.innerText||el.getAttribute('title')||'').trim();
                const r=el.getBoundingClientRect();
                if (r.width>0&&r.height>0&&r.y<120) out.push({text:t.slice(0,30),title:el.getAttribute('title')||'',x:Math.round(r.x),y:Math.round(r.y)});
            }
            return out.slice(0,40);
        }""")
        print("\n=== 顶部工具栏元素 ===")
        for t in toolbar:
            print(f"  '{t['text']}' title='{t['title']}' @({t['x']},{t['y']})")

        if do_click and elems:
            print("\n=== CLICK_ORDERS=1:尝试点 orders/cart 元素 ===")
            for e in elems:
                if any(k in (e['text']+e['cls']+e['id']).lower() for k in ("order","cart","basket","ordini","carrello")):
                    print(f"  点 <{e['tag']}> '{e['text']}' @({e['x']},{e['y']})")
                    try:
                        page.mouse.click(e['x'], e['y']); page.wait_for_timeout(4000)
                        page.screenshot(path=str(_OUT / f"ui_click_{e['text'][:10]}.png"))
                    except Exception as ex:
                        print("    点击失败:", ex)
                    break

        # 留窗口给人看(headed 时):你可手动点 Orders → 下载,我全程记网络
        if not headless:
            print("\n窗口保留 120s 供观察 —— 请手动点开 Orders 并下载 extref_146602,我在记网络…")
            for _ in range(12):
                page.wait_for_timeout(10_000)
                page.screenshot(path=str(_OUT / "ui_live.png"))

        (_OUT / "ui_netlog.json").write_text(json.dumps(netlog, ensure_ascii=False, indent=2), "utf-8")
        print(f"\n网络日志 {len(netlog)} 条 → ui_netlog.json")
        for n in netlog[-25:]:
            print(" ", json.dumps(n, ensure_ascii=False)[:160])
        b.close()


if __name__ == "__main__":
    main()
