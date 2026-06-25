#!/usr/bin/env python3
"""
PRISMA 订单状态实测脚本(独立诊断用)。

用途:绕过 daemon,直接拿凭据查 ASI 订单的真实状态,判断一个挂起的 PRISMA
异步任务到底是「还在生产」「已完成」还是「失败/被拒」。

背景:daemon 的 check_pending 吞掉所有异常返回 None,真实状态只打到 launchd
stdout 日志;此脚本把状态直接打到终端。

用法:
  python3 scripts/check_prisma_order.py extref_146602            # 查单个订单
  python3 scripts/check_prisma_order.py extref_146602 extref_146715
  python3 scripts/check_prisma_order.py --all                    # 列出该用户全部订单及状态
  python3 scripts/check_prisma_order.py --config /path/to/credentials.yaml extref_146602
"""
import sys
import argparse
import socket
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_prisma_creds(config_path: Path) -> dict:
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    creds = data.get("prisma") or {}
    if not creds.get("username") or not creds.get("password"):
        raise SystemExit(f"凭据文件 {config_path} 里缺少 prisma.username / prisma.password")
    return creds


def main():
    ap = argparse.ArgumentParser(description="实测 PRISMA 订单状态")
    ap.add_argument("orders", nargs="*", help="一个或多个 externalOrderId(如 extref_146602)")
    ap.add_argument("--all", action="store_true", help="列出该用户全部订单及状态")
    ap.add_argument("--config", default=str(_ROOT / "config" / "credentials.yaml"),
                    help="credentials.yaml 路径")
    ap.add_argument("--timeout", type=int, default=90, help="网络超时(秒)")
    args = ap.parse_args()

    if not args.orders and not args.all:
        ap.error("请至少给一个 externalOrderId,或用 --all 列出全部")

    socket.setdefaulttimeout(args.timeout)
    creds = _load_prisma_creds(Path(args.config))

    from downloader.prisma import (
        PRISMADownloader, _PRISMA_READY_STATUSES, _PRISMA_FAILED_STATUSES,
    )
    dl = PRISMADownloader(credentials=creds, output_dir="/tmp")

    print("正在登录 ASI PRISMA 门户 …")
    session = dl._authenticate()
    ords_url = dl._get_orders_status_url(session)
    uid = dl._catalog_user_id(session)
    print(f"  orders_status_url = {ords_url}")
    print(f"  catalog userId    = {uid}\n")

    def _verdict(status: str) -> str:
        s = (status or "").lower()
        if s in _PRISMA_READY_STATUSES:
            return "✅ 已就绪(可下载)"
        if s in _PRISMA_FAILED_STATUSES:
            return "❌ 终态失败(应放弃)"
        if not s:
            return "⚠ 查询不到状态"
        return "⏳ 仍在处理中"

    if args.all:
        import requests as rq
        probe = rq.Session()
        probe.cookies.update(session.cookies)
        probe.headers.update(session.headers)
        probe.trust_env = False
        r = probe.get(ords_url, params={"userId": uid}, timeout=args.timeout)
        data = r.json() if r.status_code == 200 else []
        items = data if isinstance(data, list) else (data.get("orders") or [data])
        print(f"该用户共 {len(items)} 个订单:\n")
        for it in items:
            if not isinstance(it, dict):
                continue
            oid = it.get("externalOrderId") or it.get("orderId") or "?"
            st = it.get("status") or it.get("orderStatus") or ""
            print(f"  {str(oid):20}  {str(st):12}  {_verdict(st)}")
        return

    for oid in args.orders:
        order = dl._query_order(session, oid)
        print(f"━━ {oid} ━━")
        if order is None:
            print("  查询失败:订单不存在或接口无响应\n")
            continue
        st = order.get("status") or order.get("orderStatus") or ""
        print(f"  状态        : {st}   {_verdict(st)}")
        print(f"  创建时间    : {order.get('creationDate', '—')}")
        print(f"  生产开始    : {order.get('productionStartTime', '—')}")
        print(f"  生产结束    : {order.get('productionStopTime', '—')}")
        print(f"  处理器      : {order.get('processorName', '—')}")
        print()


if __name__ == "__main__":
    main()
