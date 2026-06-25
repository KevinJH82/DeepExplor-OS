#!/usr/bin/env python3
"""
后台盯单:轮询某 PRISMA 订单状态,转 COMPLETED(或终态失败)即退出。
用法: python3 scripts/watch_prisma_order.py extref_147050
"""
import sys, time
sys.path.insert(0, "/opt/deepexplor-services/geo-downloader")
import yaml
from downloader.prisma import PRISMADownloader

_READY = {"completed","ready","available","done","delivered","processed","success","distributed"}
_FAIL = {"failed","rejected","cancelled","canceled","error","aborted","invalid","refused","expired"}


def now():
    return time.strftime("%H:%M:%S")


def main():
    order = sys.argv[1] if len(sys.argv) > 1 else "extref_147050"
    creds = yaml.safe_load(open("/opt/deepexplor-services/geo-downloader/config/credentials.yaml"))["prisma"]
    dl = PRISMADownloader(credentials=creds, output_dir="/tmp")
    s = dl._authenticate()
    uid = dl._catalog_user_id(s)
    url = dl._get_orders_status_url(s)
    print(f"[{now()}] 开始盯 {order}(每180s一次)")
    last = None
    for i in range(30):  # 最多 ~90 分钟
        try:
            r = s.get(url, params={"userId": uid, "externalOrderId": order}, timeout=40)
            data = r.json()
            item = next((x for x in data if str(x.get("externalOrderId")) == order), None)
            st = str(item.get("status","?")) if item else "(查无此单)"
        except Exception as e:
            # 会话可能过期,重新登录
            print(f"[{now()}] 查询异常({e}),重登…")
            try:
                dl._session = None; s = dl._authenticate(); uid = dl._catalog_user_id(s)
            except Exception as e2:
                print(f"[{now()}] 重登失败: {e2}")
            time.sleep(180); continue
        if st != last:
            print(f"[{now()}] 状态: {st}"); last = st
        if st.lower() in _READY:
            print(f"[{now()}] ✅ COMPLETED → 退出,唤醒主流程")
            return 0
        if st.lower() in _FAIL:
            print(f"[{now()}] ❌ 终态失败({st})→ 退出")
            return 0
        time.sleep(180)
    print(f"[{now()}] 超过盯单上限仍未完成,退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
