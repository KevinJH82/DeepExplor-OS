#!/usr/bin/env python3
"""高分一号 WFV 数据检索 — GS Cloud 自动查询"""

import requests, json, sys, os

COOKIE = os.environ.get("GS_CLOUD_COOKIE", "")

# 你所有的 ROI（来源：之前转换的 OVKML 文件）
ROIS = {
    "云顶（矿权+外围+4口井）":     (114.185, 42.354, 114.310, 42.420),
    "云顶（矿权+外围+4口井）(1)":  (114.185, 42.354, 114.310, 42.420),
    "甘肃庆阳华池县油气6个钻井":   (107.696, 36.416, 107.780, 36.473),
    "庆阳矿权":                    (107.6,   36.3,   107.9,   36.6),
    "辽矿两测试区块":              (121.3,   41.5,   121.8,   42.0),
    "布基纳法索6小区块":           (-2.0,    11.0,   0.0,     13.0),
}


def main():
    if not COOKIE:
        print("请设置 GS_CLOUD_COOKIE 环境变量：")
        print('  export GS_CLOUD_COOKIE="user_id=...; gsc_csrftoken=..."')
        print("  python3 gf1_search.py")
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Cookie": COOKIE,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.gscloud.cn/sources/accessdata/428?pid=471",
    })

    # 逐页拉取，客户端过滤
    import time as _time
    all_scenes = []
    seen_ids = set()
    page, page_size = 1, 500
    total_known = 0

    # 跳页策略：中国区数据在靠后的页，直接跳
    pages_to_check = [1, 2, 3, 10, 20, 40, 60, 80, 100, 150, 200, 250, 300, 350]

    print(f"正在从 GS Cloud 拉取 GF-1 WFV 数据 (共 {183273:,} 条，跳跃采样)...")
    for page in pages_to_check:
        tbl = json.dumps({"page": page, "pageSize": page_size})
        try:
            r = session.post(
                "https://www.gscloud.cn/wsd/gscloud_wsd/dataset/query_data",
                data={"tableInfo": tbl, "pid": "428"},
                timeout=60,
            )
            result = r.json()
        except Exception as e:
            print(f"  第 {page} 页: 失败 ({e})，跳过")
            continue

        data = result.get("data", [])
        total_known = result.get("total", total_known)
        new = 0
        for d in data:
            did = d.get("dataid", "")
            if did not in seen_ids:
                seen_ids.add(did)
                all_scenes.append(d)
                new += 1

        print(f"  第 {page} 页: {len(data)} 条 → 去重 {new} 条 (累计 {len(all_scenes):,} / {total_known:,})")
        _time.sleep(0.3)

    print(f"\n共获取 {len(all_scenes):,} 条 GF-1 WFV 记录")

    # 按 ROI 过滤
    print("\n" + "=" * 60)
    print("各 ROI 覆盖情况")
    print("=" * 60)

    for name, (w, s, e, n) in ROIS.items():
        # GF-1 WFV 幅宽 ~200km ≈ 2°，场景中心到边缘 ~1°
        # 用 ±3° 宽松筛选，避免漏掉（后面可根据 lat/lon 精细判断）
        buffer = 3.0  # 度
        matches = [
            d for d in all_scenes
            if d.get("ct_long") is not None
            and (w - buffer) <= d["ct_long"] <= (e + buffer)
            and (s - buffer) <= d.get("ct_lat", -999) <= (n + buffer)
        ]

        # 按云量排序
        matches.sort(key=lambda x: x.get("cloudcover", 100) or 100)

        print(f"\n📍 {name} ({w:.2f}E, {s:.2f}N → {e:.2f}E, {n:.2f}N)")
        print(f"   覆盖影像: {len(matches)} 景")

        if matches:
            print(f"   {'影像ID':<20} {'日期':<12} {'云量':>6} {'中心坐标':>20}  {'状态'}")
            print(f"   {'-'*70}")
            for d in matches[:20]:  # 最多显示 20 景
                exists = "✅可下载" if d.get("dataexists") else "⚠️需申请"
                print(
                    f"   {d.get('dataid','?'):<20} "
                    f"{str(d.get('datadate',''))[:10]:<12} "
                    f"{str(d.get('cloudcover','?')) + '%':>6} "
                    f"({str(d.get('ct_long','?')):>8}, {str(d.get('ct_lat','?')):<8}) "
                    f"{exists}"
                )
        else:
            print("   ⚠️ 未找到覆盖影像，可能需要扩大搜索范围")


if __name__ == "__main__":
    main()
