"""一次性 ETL:把 geo-portal 的 _data/portal_db.json 灌入 Postgres。

用法(backend 目录):
  python -m scripts.migrate_json_to_pg --dry-run     # 只统计,不写库
  python -m scripts.migrate_json_to_pg               # 实灌(需先配 DATABASE_URL)

幂等:用 merge,重复跑不会重复插入。无 password_hash 的老账号迁移后 status=disabled,
须经 `python -m app.cli set-password` 重设方可登录。
"""
import argparse
import json
import sys
from pathlib import Path

from app import db
from app.runstages import normalize_stages

_JSON_PATH = Path(__file__).resolve().parent.parent / "_data" / "portal_db.json"


def _load_json():
    if not _JSON_PATH.exists():
        sys.exit(f"找不到 JSON 库: {_JSON_PATH}")
    return json.loads(_JSON_PATH.read_text(encoding="utf-8"))


def migrate(dry: bool):
    data = _load_json()
    tenants = data.get("tenants", {})
    users = data.get("users", {})
    projects = data.get("projects", {})
    members = data.get("members", [])
    runs = data.get("runs", {})
    refresh = data.get("refresh_tokens", {})

    disabled = [u["username"] for u in users.values() if not u.get("password_hash")]
    print(f"JSON: tenants={len(tenants)} users={len(users)}(无口令将禁用 {len(disabled)}) "
          f"projects={len(projects)} members={len(members)} runs={len(runs)} "
          f"refresh={len(refresh)}")
    if disabled:
        print("  将置 disabled(需重设口令):", ", ".join(disabled))
    if dry:
        print("[dry-run] 未写库。")
        return

    db.create_all()
    with db.Session() as s:
        for t in tenants.values():
            s.merge(db.Tenant(id=t["id"], name=t.get("name", ""),
                              quota_gb=t.get("quota_gb", 0),
                              status=t.get("status", "active"), created_at=t["created_at"]))
        for u in users.values():
            has_pw = bool(u.get("password_hash"))
            s.merge(db.User(
                id=u["id"], tenant_id=u["tenant_id"], email=u.get("email"),
                username=u["username"], display=u.get("display", ""),
                tenant_role=u["tenant_role"], password_hash=u.get("password_hash"),
                status=u.get("status", "active") if has_pw else "disabled",
                created_at=u["created_at"], last_login_at=u.get("last_login_at")))
        for p in projects.values():
            s.merge(db.Project(
                id=p["id"], tenant_id=p["tenant_id"], name=p["name"],
                mineral=p.get("mineral", ""), mineral_label=p.get("mineral_label", ""),
                aoi_bbox=p.get("aoi_bbox"), thumb=p.get("thumb", "cu"),
                creator_id=p["creator_id"], current_run=p.get("current_run"),
                created_at=p["created_at"]))
        for m in members:
            s.merge(db.Member(user_id=m["user_id"], project_id=m["project_id"],
                              role=m["role"], expires_at=m.get("expires_at")))
        for r in runs.values():
            pid = r["project_id"]
            tid = projects.get(pid, {}).get("tenant_id")
            stages = r.get("stages") or normalize_stages(r.get("plan") or {})
            s.merge(db.Run(
                trace_id=r["trace_id"], project_id=pid, tenant_id=tid,
                plan=r.get("plan"), stages=stages, evidence_plan=r.get("evidence_plan"),
                version=r.get("version", 1), created_at=r["created_at"]))
        for jti, rec in refresh.items():
            s.merge(db.RefreshToken(jti=jti, user_id=rec["user_id"],
                                    expires_at=int(rec.get("expires_at", 0)),
                                    revoked=bool(rec.get("revoked")),
                                    created_at=rec.get("created_at", "")))
        s.commit()
    print("✅ 迁移完成。验证: psql -d deepexplor -c 'select count(*) from runs;'")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    migrate(a.dry_run)
