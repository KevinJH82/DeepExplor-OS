"""回填历史产物的 tenant_id(P2 租户隔离收口)。

背景:接线之前产出的产物 manifest 不带 tenant_id,按"向后兼容"对所有租户可见,
导致存量数据未隔离。这些产物均产于单租户时代(仅 t_demo),本就都属 t_demo。

归属策略(每个未打标产物):
  1) 有 trace_id 且能在 PG runs 表查到 tenant → 用之(精确,兼顾未来多租户历史);
  2) 否则 → 默认租户(--default-tenant,默认 t_demo)。--no-default 时跳过(留未打标)。
已带 tenant_id 的跳过(幂等)。只新增一个键,不改其它字段。

用法(在 geo-portal/backend 目录,需 DATABASE_URL):
  python -m scripts.backfill_tenant --dry-run            # 只统计,不写盘
  python -m scripts.backfill_tenant                       # 实写
  python -m scripts.backfill_tenant --default-tenant t_demo
  python -m scripts.backfill_tenant --no-default          # 无法精确归属的不动
"""
import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app import db

# repo 根: geo-portal/backend/scripts/ → 上溯 3 层
REPO = Path(__file__).resolve().parents[3]

# 各服务产物根(与 broker 的 DEFAULT_*_OUTPUTS 对应)→ 扫描的 manifest 文件名
SOURCES = [
    ("analyser",    REPO / "geo-analyser" / "results"),
    ("stru",        REPO / "geo-stru" / "results"),
    ("geophys",     REPO / "geo-geophys" / "results"),
    ("geochem",     REPO / "geo-geochem" / "results"),
    ("model3d",     REPO / "geo-model3d" / "results"),
    ("drill",       REPO / "geo-drill" / "results"),
    ("exploration", REPO / "geo-exploration" / "Python_Project" / "web_app" / "uploads"),
    ("insar",       REPO / "geo-insar" / "downloads"),
    ("datacolle",   REPO / "data-colle" / "prospector" / "output"),
]
_MANIFEST_NAMES = ("manifest.json", "metadata.json", "task_meta.json")


def _trace_tenant_map() -> dict:
    """trace_id → tenant_id(来自 PG runs 表,去规范化的权威映射)。"""
    m = {}
    with db.Session() as s:
        for r in s.scalars(select(db.Run)):
            if r.tenant_id:
                m[r.trace_id] = r.tenant_id
    return m


def _iter_manifests(root: Path):
    if not root.exists():
        return
    for name in _MANIFEST_NAMES:
        yield from root.rglob(name)


def _resolve_tenant(meta: dict, tmap: dict, default: str) -> str:
    """按策略推断该产物的 tenant_id;无法精确且无默认时返回 None。"""
    tid = meta.get("trace_id")
    if tid and tid in tmap:
        return tmap[tid]
    for lk in (meta.get("linked_trace_ids") or []):
        if lk in tmap:
            return tmap[lk]
    return default  # 可能为 None(--no-default)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scripts.backfill_tenant")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    ap.add_argument("--default-tenant", default="t_demo", help="无法精确归属时的默认租户")
    ap.add_argument("--no-default", action="store_true", help="无法精确归属的跳过(不用默认)")
    a = ap.parse_args(argv)
    default = None if a.no_default else a.default_tenant

    tmap = _trace_tenant_map()
    print(f"runs 表 trace→tenant 映射: {len(tmap)} 条")
    print(f"默认租户: {default!r}{'(--no-default:精确归属外的跳过)' if default is None else ''}")
    print(f"模式: {'DRY-RUN(不写盘)' if a.dry_run else '实写'}\n")

    stats = {"total": 0, "already": 0, "by_trace": 0, "by_default": 0, "skipped": 0}
    per_tenant = {}
    by_src = {}
    for src, root in SOURCES:
        s_total = s_changed = 0
        for f in _iter_manifests(root):
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            stats["total"] += 1
            s_total += 1
            if meta.get("tenant_id"):
                stats["already"] += 1
                continue
            precise = (meta.get("trace_id") in tmap) or any(
                lk in tmap for lk in (meta.get("linked_trace_ids") or []))
            tenant = _resolve_tenant(meta, tmap, default)
            if not tenant:
                stats["skipped"] += 1
                continue
            stats["by_trace" if precise else "by_default"] += 1
            per_tenant[tenant] = per_tenant.get(tenant, 0) + 1
            s_changed += 1
            if not a.dry_run:
                meta["tenant_id"] = tenant
                tmp = f.with_suffix(f.suffix + ".tmp")
                tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(f)
        by_src[src] = (s_total, s_changed)

    print("按服务[扫描/将回填]:")
    for src, (t, c) in by_src.items():
        print(f"  {src:12} {t:4} / {c}")
    print(f"\n汇总: 扫描 {stats['total']} | 已打标跳过 {stats['already']} | "
          f"按trace精确 {stats['by_trace']} | 按默认 {stats['by_default']} | 无法归属跳过 {stats['skipped']}")
    print(f"回填后各租户新增: {per_tenant}")
    if a.dry_run:
        print("\n[dry-run] 未写盘。确认无误后去掉 --dry-run 实跑。")
    else:
        print("\n✅ 回填完成。")


if __name__ == "__main__":
    main()
