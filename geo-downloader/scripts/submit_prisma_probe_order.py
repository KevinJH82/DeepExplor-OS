#!/usr/bin/env python3
"""
一次性诊断下单:对指定 KML 区域搜一景 PRISMA L0,提交一个 L2D 处理订单,
把搜索结果 + 提交请求/响应原样打出来,记录新 order_id 和提交时刻。
用于「提交新订单 + 门户同步盯状态」联调。

⚠ 会消耗一次配额、触发真实 L2D 处理。用法:
  python3 scripts/submit_prisma_probe_order.py [kml_path]
默认用「云顶4口井」那片。
"""
import sys, json, time
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import yaml
from downloader.kml_parser import parse_kml
from downloader.prisma import PRISMADownloader

_DEFAULT_KML = ("/opt/deepexplor-services/geo-downloader/uploads/kml/"
                "云顶4口井油气测试区块6.82km2_1779869303.ovkml")


def main():
    kml = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_KML
    creds = yaml.safe_load((_ROOT/"config"/"credentials.yaml").read_text("utf-8"))["prisma"]

    geometry, bbox, name = parse_kml(kml)
    print(f"区域: {name}\nbbox: {bbox}\n")

    dl = PRISMADownloader(credentials=creds, output_dir="/tmp/prisma_probe")
    s = dl._authenticate()
    print("登录 OK\n")

    print(">>> 搜 PRISMA L0(放宽时间/云量,确保有结果)…")
    raw = dl._solr_search(s, bbox, "2018-01-01", "2026-05-31", 100, max_results=10)
    results = dl._parse_solr_docs(raw)
    print(f"    命中 {len(results)} 景")
    for i, r in enumerate(results[:5]):
        print(f"      [{i}] {r['name']}  date={r['date']} cloud={r['cloud_cover']} id_inv={r['id_inv_i']}")
    if not results:
        print("!! 该区域无 L0 可下单,换个区域或放宽条件。")
        return

    pick = results[0]
    print(f"\n>>> 选第 0 景下单: {pick['name']} (id_inv={pick['id_inv_i']})")

    # 直接复刻 _submit_order 的 payload,但把请求/响应打全
    import requests as _req
    from requests.adapters import HTTPAdapter
    _SVC = "http://prisma.asi.it/prisma-cat/service.php"
    probe = _req.Session()
    probe.cookies.update(s.cookies); probe.headers.update(s.headers)
    probe.trust_env = False
    probe.mount("http://", HTTPAdapter(max_retries=0))
    raw_doc = pick["_raw"]
    payload = {
        "INPUT_NAME": raw_doc.get("filename_s",""), "id": raw_doc.get("id",""),
        "processorname": "L2D",
        "start_time": raw_doc.get("validitystart_dt",""),
        "stop_time": raw_doc.get("validitystop_dt",""),
        "POnOff": "PanOn", "VOnOff": "VnirOn", "SOnOff": "SwirOn",
        "L2_HGRP": 1, "UseGCP": "GCPNo", "SelOrBin": "BSel",
        "VnirBandSelect": "4-66", "SwirBandSelect": "1-170", "Binning": 1,
    }
    print("\n=== 提交请求 ===")
    print("POST", _SVC, "?request=process")
    print("payload:", json.dumps(payload, ensure_ascii=False))
    submitted_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    r = probe.post(_SVC, params={"request":"process"}, json=payload, timeout=60, allow_redirects=False)
    print("\n=== 提交响应 ===")
    print("HTTP", r.status_code)
    print("body:", r.text[:800])

    oid = ""
    try:
        oid = str((r.json() or {}).get("orderId") or (r.json() or {}).get("order_id") or "")
    except Exception:
        pass
    print(f"\n>>> 新订单 order_id = {oid or '(响应未含 orderId,见上)'}")
    print(f">>> 提交时刻 = {submitted_at}")
    print("\n现在去门户 EO Orders status → Refresh List 看这个新单是否出现、初始状态。")
    print("约 50 分钟后它应转 COMPLETED;届时我们盯它有没有下载/分发入口。")
    # 记到文件,后续盯状态用
    (_ROOT/"scripts"/"_prisma_probe_out"/"new_order.json").write_text(
        json.dumps({"order_id":oid,"submitted_at":submitted_at,"name":pick["name"],
                    "id_inv":pick["id_inv_i"],"http":r.status_code,"resp":r.text[:1000]},
                   ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
