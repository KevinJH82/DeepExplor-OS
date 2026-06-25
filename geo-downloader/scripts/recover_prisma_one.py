#!/usr/bin/env python3
"""
一次性恢复脚本:用稳定的交互式登录路径,把某个 polling 任务里挂起的 PRISMA
订单下载下来并增量补包到交付目录(绕过被限流的 daemon)。

用法:
  python3 scripts/recover_prisma_one.py <task_id>
  python3 scripts/recover_prisma_one.py ea6f9b88
"""
import sys
import json
import socket
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PERSIST = _ROOT / ".geo_tasks_persist.json"
_CONFIG = _ROOT / "config" / "credentials.yaml"


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法: python3 scripts/recover_prisma_one.py <task_id>")
    task_id = sys.argv[1]
    socket.setdefaulttimeout(120)

    tasks = json.loads(_PERSIST.read_text(encoding="utf-8"))
    task = next((t for t in tasks if t.get("task_id") == task_id), None)
    if not task:
        raise SystemExit(f"找不到任务 {task_id}")

    output_dir = Path(task["output_dir"])
    kml = Path(task["kml"])
    delivery_dir = Path(task["delivery_dir"])
    area_root = output_dir / kml.stem
    prisma_dir = area_root / "prisma"
    area_label = area_root.name

    print(f"任务      : {task_id}  ({task.get('label','')[:40]})")
    print(f"area_root : {area_root}")
    print(f"prisma_dir: {prisma_dir}")
    print(f"delivery  : {delivery_dir}\n")

    if not (prisma_dir / ".prisma_pending_order.json").exists():
        print("没有 pending PRISMA 订单,无需处理。")
        return

    import yaml
    creds = (yaml.safe_load(_CONFIG.read_text(encoding="utf-8")).get("prisma") or {})
    from downloader.prisma import PRISMADownloader

    dl = PRISMADownloader(credentials=creds, output_dir=str(output_dir))
    print(">>> 调 check_pending 下载 PRISMA .he5 …")
    result = dl.check_pending(prisma_dir)
    if not result:
        print("!! check_pending 未返回文件(可能仍未就绪/失败/网络问题),终止。")
        return
    print(f"<<< 下载完成: {result}  ({result.stat().st_size/1e6:.1f} MB)\n")

    print(">>> 增量补包到交付目录 …")
    from postprocess.package import package_delivery
    pkg = package_delivery(
        raw_area_dir=area_root,
        kml_path=kml,
        delivery_root=delivery_dir,
        area_label=area_label,
        incremental=True,
    )
    print(f"<<< 补包完成: {pkg}")
    print("\n完成。pending 标记已清,daemon 下一轮会把 prisma 移出 pending_async、任务转 done。")


if __name__ == "__main__":
    main()
