"""`sales-agent admin ...`: manage Admin Revansa accounts. No client data, no Core access -- only
who may log into `/admin/v1` and for which clients.

    create          --username U --role R [--client CID ...]   create an account (prompts for a password)
    list                                                        list every account
    deactivate      --username U                                deactivate an account, end its sessions
    set-role        --username U --role R [--client CID ...]    change role and client scope, end its sessions
    reset-password  --username U                                prompt for a new password, end its sessions

`--role owner` needs no `--client` (an owner reaches every client). Every other role needs at
least one. Exit codes: 0 ok, 1 store problem, 2 invalid input.
"""

import argparse
import getpass
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from sales_app.admin_auth import (
    AdminRole,
    AdminStoreError,
    AdminUser,
    FileAdminSessionStore,
    FileAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.settings import Settings

EXIT_OK, EXIT_STORE, EXIT_INVALID = 0, 1, 2
_MIN_PASSWORD_LENGTH = 12


def add_admin_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    admin = commands.add_parser("admin", help="manage Admin Revansa accounts")
    actions = admin.add_subparsers(dest="admin_command", required=True)
    roles = [r.value for r in AdminRole]

    def with_role_and_scope(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--role", required=True, choices=roles)
        parser.add_argument(
            "--client",
            dest="client_ids",
            action="append",
            default=[],
            help="repeatable; required unless --role owner",
        )

    create = actions.add_parser("create", help="create an account (prompts for a password)")
    create.add_argument("--username", required=True)
    with_role_and_scope(create)

    actions.add_parser("list", help="list every account")

    deactivate = actions.add_parser("deactivate", help="deactivate an account, end its sessions")
    deactivate.add_argument("--username", required=True)

    set_role = actions.add_parser("set-role", help="change role and client scope, end its sessions")
    set_role.add_argument("--username", required=True)
    with_role_and_scope(set_role)

    reset = actions.add_parser(
        "reset-password", help="prompt for a new password, end its sessions"
    )
    reset.add_argument("--username", required=True)


def run_admin(settings: Settings, args: argparse.Namespace) -> int:
    users = FileAdminUserStore(settings.admin_dir / "users.json")
    sessions = FileAdminSessionStore(settings.admin_dir / "sessions")
    try:
        return _HANDLERS[args.admin_command](settings, users, sessions, args)
    except AdminStoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_STORE


# ---- commands -----------------------------------------------------------------------------


def _create(settings, users, sessions, args) -> int:
    username = _normalize(args.username)
    if users.get_by_username(username) is not None:
        print(f"error: username {username!r} already exists", file=sys.stderr)
        return EXIT_INVALID
    role = AdminRole(args.role)
    client_ids = _checked_client_ids(settings, args.client_ids)
    if role is not AdminRole.OWNER and not client_ids:
        print("error: a non-owner account needs at least one --client", file=sys.stderr)
        return EXIT_INVALID
    password = _prompt_new_password()
    user = AdminUser(
        id=uuid.uuid4().hex,
        username=username,
        password_hash=hash_password(password),
        role=role,
        client_ids=frozenset(client_ids),
        active=True,
        created_at=datetime.now(UTC),
        created_by=_default_author(),
    )
    users.create(user)
    print(f"created {username} ({role.value})")
    return EXIT_OK


def _list(settings, users, sessions, args) -> int:
    accounts = users.list()
    if not accounts:
        print("no admin accounts")
    for user in accounts:
        status = "active" if user.active else "deactivated"
        scope = "*" if user.role is AdminRole.OWNER else ",".join(sorted(user.client_ids)) or "-"
        print(f"{user.username}  {user.role.value}  {status}  clients: {scope}")
    return EXIT_OK


def _deactivate(settings, users, sessions, args) -> int:
    username = _normalize(args.username)
    user = users.get_by_username(username)
    if user is None:
        print(f"error: no such account {username!r}", file=sys.stderr)
        return EXIT_INVALID
    users.update(replace(user, active=False))
    sessions.delete_all_for(user.id)
    print(f"deactivated {username}")
    return EXIT_OK


def _set_role(settings, users, sessions, args) -> int:
    username = _normalize(args.username)
    user = users.get_by_username(username)
    if user is None:
        print(f"error: no such account {username!r}", file=sys.stderr)
        return EXIT_INVALID
    role = AdminRole(args.role)
    client_ids = _checked_client_ids(settings, args.client_ids)
    if role is not AdminRole.OWNER and not client_ids:
        print("error: a non-owner account needs at least one --client", file=sys.stderr)
        return EXIT_INVALID
    users.update(replace(user, role=role, client_ids=frozenset(client_ids)))
    sessions.delete_all_for(user.id)  # a role or scope change takes effect immediately
    print(f"{username} is now {role.value}")
    return EXIT_OK


def _reset_password(settings, users, sessions, args) -> int:
    username = _normalize(args.username)
    user = users.get_by_username(username)
    if user is None:
        print(f"error: no such account {username!r}", file=sys.stderr)
        return EXIT_INVALID
    password = _prompt_new_password()
    users.update(replace(user, password_hash=hash_password(password)))
    sessions.delete_all_for(user.id)
    print(f"password reset for {username}")
    return EXIT_OK


_HANDLERS = {
    "create": _create,
    "list": _list,
    "deactivate": _deactivate,
    "set-role": _set_role,
    "reset-password": _reset_password,
}


# ---- helpers --------------------------------------------------------------------------------


def _normalize(username: str) -> str:
    return username.strip().lower()


def _checked_client_ids(settings: Settings, client_ids: list[str]) -> list[str]:
    for cid in client_ids:
        if not (settings.clients_dir / cid / "client.yaml").exists():
            print(f"warning: no clients/{cid}/client.yaml (scoping to it anyway)", file=sys.stderr)
    return client_ids


def _prompt_new_password() -> str:
    while True:
        password = getpass.getpass("New password: ")
        if len(password) < _MIN_PASSWORD_LENGTH:
            print(f"password must be at least {_MIN_PASSWORD_LENGTH} characters", file=sys.stderr)
            continue
        if password != getpass.getpass("Confirm password: "):
            print("passwords did not match", file=sys.stderr)
            continue
        return password


def _default_author() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:  # noqa: BLE001 - no user name available (some containers)
        return "cli"
