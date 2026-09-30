"""`sales-agent admin ...`: bootstrapping and managing Admin Revansa accounts without a UI."""

import argparse
from datetime import UTC, datetime, timedelta

from sales_app.admin_auth import (
    AdminRole,
    AdminSession,
    FileAdminSessionStore,
    FileAdminUserStore,
)
from sales_app.admin_auth.models import hash_token, new_token
from sales_app.admin_auth.passwords import verify_password
from sales_app.admin_cli import EXIT_INVALID, EXIT_OK, add_admin_parser, run_admin
from sales_app.settings import Settings

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
GOOD_PASSWORD = "correct-horse-battery-staple"


def settings(tmp_path, clients=()) -> Settings:
    clients_dir = tmp_path / "clients"
    for cid in clients:
        (clients_dir / cid).mkdir(parents=True)
        (clients_dir / cid / "client.yaml").write_text("client_id: " + cid, encoding="utf-8")
    return Settings(clients_dir=clients_dir, admin_dir=tmp_path / "admin")


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    add_admin_parser(commands)
    return parser.parse_args(["admin", *argv])


def fake_getpass(monkeypatch, responses: list[str]) -> None:
    it = iter(responses)
    monkeypatch.setattr("sales_app.admin_cli.getpass.getpass", lambda prompt="": next(it))


class TestCreate:
    def test_creates_an_account_with_a_hashed_password(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        code = run_admin(
            s, parse("create", "--username", "Alice", "--role", "editor", "--client", "alpha")
        )
        assert code == EXIT_OK
        user = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        assert user is not None
        assert user.password_hash != GOOD_PASSWORD
        assert verify_password(GOOD_PASSWORD, user.password_hash)
        assert user.role is AdminRole.EDITOR and user.client_ids == frozenset({"alpha"})

    def test_owner_needs_no_client(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path)
        code = run_admin(s, parse("create", "--username", "root", "--role", "owner"))
        assert code == EXIT_OK
        user = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("root")
        assert user.role is AdminRole.OWNER and user.can_reach("anything")

    def test_a_non_owner_without_a_client_is_rejected(self, tmp_path, monkeypatch, capsys):
        fake_getpass(monkeypatch, [])  # never reached: rejected before the password prompt
        s = settings(tmp_path)
        code = run_admin(s, parse("create", "--username", "alice", "--role", "editor"))
        assert code == EXIT_INVALID
        assert "at least one --client" in capsys.readouterr().err
        assert FileAdminUserStore(s.admin_dir / "users.json").list() == ()

    def test_duplicate_username_is_rejected(self, tmp_path, monkeypatch, capsys):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        run_admin(s, parse("create", "--username", "alice", "--role", "editor", "--client", "alpha"))
        code = run_admin(
            s, parse("create", "--username", "Alice", "--role", "viewer", "--client", "alpha")
        )
        assert code == EXIT_INVALID
        assert "already exists" in capsys.readouterr().err

    def test_scoping_to_an_unknown_client_warns_but_does_not_fail(self, tmp_path, monkeypatch, capsys):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path)  # no clients on disk at all
        code = run_admin(
            s, parse("create", "--username", "alice", "--role", "editor", "--client", "ghost")
        )
        assert code == EXIT_OK
        assert "warning" in capsys.readouterr().err
        user = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        assert user.client_ids == frozenset({"ghost"})

    def test_short_passwords_are_rejected_and_reprompted(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, ["short", GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        code = run_admin(
            s, parse("create", "--username", "alice", "--role", "editor", "--client", "alpha")
        )
        assert code == EXIT_OK

    def test_mismatched_confirmation_is_rejected_and_reprompted(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, "different", GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        code = run_admin(
            s, parse("create", "--username", "alice", "--role", "editor", "--client", "alpha")
        )
        assert code == EXIT_OK


class TestList:
    def test_lists_every_account_with_role_status_and_scope(self, tmp_path, monkeypatch, capsys):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD, GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        run_admin(s, parse("create", "--username", "root", "--role", "owner"))
        run_admin(
            s, parse("create", "--username", "alice", "--role", "editor", "--client", "alpha")
        )
        capsys.readouterr()
        run_admin(s, parse("list"))
        out = capsys.readouterr().out
        assert "root  owner  active  clients: *" in out
        assert "alice  editor  active  clients: alpha" in out

    def test_an_empty_store_says_so(self, tmp_path, capsys):
        run_admin(settings(tmp_path), parse("list"))
        assert "no admin accounts" in capsys.readouterr().out


class TestDeactivate:
    def test_deactivates_and_ends_every_session(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        run_admin(
            s, parse("create", "--username", "alice", "--role", "editor", "--client", "alpha")
        )
        user = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        session_store = FileAdminSessionStore(s.admin_dir / "sessions")
        token = new_token()
        session_store.create(
            AdminSession(
                token_hash=hash_token(token),
                admin_id=user.id,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        code = run_admin(s, parse("deactivate", "--username", "alice"))
        assert code == EXIT_OK
        assert not FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice").active
        assert session_store.get(hash_token(token)) is None

    def test_an_unknown_username_is_rejected(self, tmp_path, capsys):
        code = run_admin(settings(tmp_path), parse("deactivate", "--username", "ghost"))
        assert code == EXIT_INVALID
        assert "no such account" in capsys.readouterr().err


class TestSetRole:
    def test_changes_role_and_scope_and_ends_sessions(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha", "beta"])
        run_admin(
            s, parse("create", "--username", "alice", "--role", "viewer", "--client", "alpha")
        )
        user = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        session_store = FileAdminSessionStore(s.admin_dir / "sessions")
        token = new_token()
        session_store.create(
            AdminSession(
                token_hash=hash_token(token), admin_id=user.id, created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        code = run_admin(
            s,
            parse(
                "set-role", "--username", "alice", "--role", "editor",
                "--client", "alpha", "--client", "beta",
            ),
        )
        assert code == EXIT_OK
        updated = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        assert updated.role is AdminRole.EDITOR
        assert updated.client_ids == frozenset({"alpha", "beta"})
        assert session_store.get(hash_token(token)) is None


class TestResetPassword:
    def test_replaces_the_hash_and_ends_sessions(self, tmp_path, monkeypatch):
        fake_getpass(monkeypatch, [GOOD_PASSWORD, GOOD_PASSWORD])
        s = settings(tmp_path, clients=["alpha"])
        run_admin(
            s, parse("create", "--username", "alice", "--role", "editor", "--client", "alpha")
        )
        old_user = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        session_store = FileAdminSessionStore(s.admin_dir / "sessions")
        token = new_token()
        session_store.create(
            AdminSession(
                token_hash=hash_token(token), admin_id=old_user.id, created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        new_password = "another-strong-password"
        fake_getpass(monkeypatch, [new_password, new_password])
        code = run_admin(s, parse("reset-password", "--username", "alice"))
        assert code == EXIT_OK
        updated = FileAdminUserStore(s.admin_dir / "users.json").get_by_username("alice")
        assert not verify_password(GOOD_PASSWORD, updated.password_hash)
        assert verify_password(new_password, updated.password_hash)
        assert session_store.get(hash_token(token)) is None
