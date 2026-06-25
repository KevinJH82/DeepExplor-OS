#!/usr/bin/env python3
"""
verb 探针:service.php(acs-apache-solr_lightsg)有个 request= 白名单,
合法 verb 返回数据/特定错误,非法 verb 统一报 "Unknown request: X"。
枚举候选 verb 找出「下载/分发」那个动词。

用法: python3 scripts/probe_prisma_verbs.py
"""
import os, re, sys, json
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import yaml
from playwright.sync_api import sync_playwright

_PORTAL = "http://prisma.asi.it/missionselect/"
_CATALOG = "http://prisma.asi.it/js-cat-client-prisma-src/"
_SVC = "http://prisma.asi.it/prisma-cat/service.php"
_ORDER = "extref_146602"
_USER = "CHN_SNN2_2629_1$"
_OUT = _ROOT / "scripts" / "_prisma_probe_out"; _OUT.mkdir(exist_ok=True)

VERBS = [
    "download", "getdownload", "downloadproduct", "getproduct", "product",
    "products", "getfile", "file", "getdata", "data", "retrieve", "fetch",
    "distribute", "distribution", "deliver", "delivery", "ordersdistribution",
    "processstatus", "status", "result", "results", "getresult", "output",
    "getoutput", "deliverable", "deliverables", "package", "getpackage",
    "export", "getexport", "link", "getlink", "url", "geturl", "ftp",
]


def _f(page, url):
    return page.evaluate("""async (u)=>{try{const r=await fetch(u,{credentials:'include'});
        return {s:r.status, t:(await r.text())};}catch(e){return{s:-1,t:String(e)};}}""", url)


def main():
    c = yaml.safe_load((_ROOT/"config"/"credentials.yaml").read_text("utf-8"))["prisma"]
    headless = os.environ.get("HEADLESS","1") != "0"
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=headless)
        ctx = b.new_context(user_agent="Mozilla/5.0 (compatible; geo-downloader/1.0)")
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
        page.wait_for_timeout(4000)

        print("\n=== verb 枚举(★=可能合法,即非 'Unknown request')===")
        valid = []
        for v in VERBS:
            res = _f(page, f"{_SVC}?request={v}&externalOrderId={_ORDER}&username={_USER}")
            body = res["t"][:160].replace("\n"," ")
            unknown = "Unknown request" in res["t"]
            mark = " " if unknown else "★"
            if not unknown: valid.append(v)
            print(f"  {mark} [{res['s']}] {v:18} {body}")

        print("\n=== 合法 verb:", valid, "===")

        # 重新搜目录,找 L2D 处理产物(可能含真实下载字段)
        print("\n=== 搜 L2D 产物(看 docs 里有无 download/url 字段)===")
        q = (f"{_SVC}?request=query&core=products&rows=5"
             f"&fq=processinglevel_s:L2D&q=*:*")
        res = _f(page, q)
        (_OUT/"l2d_query.json").write_text(res["t"], "utf-8")
        try:
            docs = json.loads(res["t"]).get("response",{}).get("docs",[])
            print(f"  命中 {len(docs)} 个 L2D 产物")
            if docs:
                keys = sorted(docs[0].keys())
                print("  字段:", [k for k in keys if any(x in k.lower()
                      for x in ("download","url","link","path","file","loc","dist","ftp","id"))])
                print("  样例 doc(截断):", json.dumps(docs[0], ensure_ascii=False)[:600])
        except Exception as e:
            print("  解析失败:", e, res["t"][:200])
        b.close()


if __name__ == "__main__":
    main()
