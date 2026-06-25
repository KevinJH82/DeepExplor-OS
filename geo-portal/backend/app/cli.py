"""门户账号管理 CLI(替代已移除的明文 seed 账号)。

用法(在 backend 目录):
  python -m app.cli create-admin                     # 交互创建 org_admin
  python -m app.cli create-admin --username admin --display 张工 --tenant t_demo
  python -m app.cli set-password --username admin     # 重设口令并吊销其现有会话
  python -m app.cli create-user --username geo --role member
  python -m app.cli list-users

口令经 argon2id 哈希后落库;不接受明文存储。
"""
import argparse
import getpass
import sys

from . import store, auth


def _prompt_password(confirm: bool = True) -> str:
    pw = getpass.getpass("设置口令: ").strip()
    if not pw:
        sys.exit("口令不能为空")
    if confirm and getpass.getpass("再次确认: ").strip() != pw:
        sys.exit("两次输入不一致")
    return pw


def _ensure_tenant(tenant_id: str):
    t = store.get_tenant(tenant_id)
    if not t:
        sys.exit(f"租户不存在: {tenant_id}(可在 portal_db.json 的 tenants 中添加)")
    return t


def cmd_create_admin(args):
    _ensure_tenant(args.tenant)
    pw = args.password or _prompt_password()
    try:
        u = store.create_user(args.tenant, args.username, args.display,
                              "org_admin", auth.hash_password(pw))
    except ValueError as e:
        sys.exit(str(e))
    print(f"已创建租户管理员: {u['username']} (id={u['id']}, tenant={u['tenant_id']})")


def cmd_create_user(args):
    _ensure_tenant(args.tenant)
    if args.role not in auth.TENANT_ROLES:
        sys.exit(f"非法角色: {args.role}(可选 {sorted(auth.TENANT_ROLES)})")
    pw = args.password or _prompt_password()
    try:
        u = store.create_user(args.tenant, args.username, args.display,
                              args.role, auth.hash_password(pw))
    except ValueError as e:
        sys.exit(str(e))
    print(f"已创建用户: {u['username']} (role={u['tenant_role']})")


def cmd_set_password(args):
    u = store.get_user_by_username(args.username)
    if not u:
        sys.exit(f"用户不存在: {args.username}")
    pw = args.password or _prompt_password()
    store.set_password(u["id"], auth.hash_password(pw))
    store.revoke_all_user_refresh(u["id"])   # 改密即踢下线所有现有会话
    print(f"已重设口令并吊销现有会话: {u['username']}")


def cmd_list_users(args):
    users = store.list_users()
    if not users:
        print("(无用户。先 create-admin)")
        return
    for u in users:
        has = "有口令" if u.get("password_hash") else "无口令(禁用)"
        print(f"  {u['username']:<16} {u['tenant_role']:<16} {u.get('status','active'):<10} {has}  tenant={u['tenant_id']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="app.cli", description="门户账号管理")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("create-admin", help="创建租户管理员")
    pa.add_argument("--username", required=True)
    pa.add_argument("--display", default="")
    pa.add_argument("--tenant", default="t_demo")
    pa.add_argument("--password", default="", help="非交互口令(慎用,会留在 shell 历史)")
    pa.set_defaults(func=cmd_create_admin)

    pu = sub.add_parser("create-user", help="创建普通用户")
    pu.add_argument("--username", required=True)
    pu.add_argument("--display", default="")
    pu.add_argument("--tenant", default="t_demo")
    pu.add_argument("--role", default="member")
    pu.add_argument("--password", default="")
    pu.set_defaults(func=cmd_create_user)

    ps = sub.add_parser("set-password", help="重设口令")
    ps.add_argument("--username", required=True)
    ps.add_argument("--password", default="")
    ps.set_defaults(func=cmd_set_password)

    pl = sub.add_parser("list-users", help="列出用户")
    pl.set_defaults(func=cmd_list_users)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
