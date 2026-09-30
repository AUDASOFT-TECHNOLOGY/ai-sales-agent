"""`/admin/v1`: login, logout, `me`, cookie behaviour, and the route-protection building blocks
(`require_role`, `ensure_client_access`) that the not-yet-built resource routers will depend on."""

from datetime import UTC, datetime, timedelta

import pytest
from app_support import InlineExecutor, env_for, fake_builders, make_settings, write_client
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
from sales_app.api.admin import COOKIE_NAME, ensure_client_access, require_role
from sales_app.bootstrap import build_application
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"


def build(tmp_path, *client_ids, users=None, sessions=None, clock=None, **settings_kw):
    clock = clock or FakeClock(NOW)
    for cid in client_ids:
        write_client(tmp_path, cid)
    builders, made = fake_builders(clock)
    application = build_application(
        make_settings(tmp_path, **settings_kw),
        env=env_for(*client_ids),
        builders=builders,
        clock=clock,
        executor=InlineExecutor(),
        admin_user_store=users if users is not None else InMemoryAdminUserStore(),
        admin_session_store=sessions if sessions is not None else InMemoryAdminSessionStore(),
    )
    return application, made


def editor(username="alice", client_ids=frozenset({"alpha"})) -> AdminUser:
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=AdminRole.EDITOR,
        client_ids=client_ids,
        created_at=NOW,
    )


class Fixture:
    def __init__(self, tmp_path, *client_ids, users=None, clock=None, **settings_kw):
        self.users = users if users is not None else InMemoryAdminUserStore()
        self.sessions = InMemoryAdminSessionStore()
        self.clock = clock or FakeClock(NOW)
        self.application, self.made = build(
            tmp_path,
            *client_ids,
            users=self.users,
            sessions=self.sessions,
            clock=self.clock,
            **settings_kw,
        )
        # https base_url: a Secure cookie (the real default) is otherwise never re-sent by the
        # test client's cookie jar, since http://testserver is not a secure context.
        self.client = TestClient(create_app(self.application, run_scheduler=False), base_url="https://testserver")

    def login(self, username="alice", password=PASSWORD):
        return self.client.post("/admin/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path, "alpha")
    fixture.users.create(editor())
    return fixture


class TestLogin:
    def test_correct_credentials_set_an_httponly_secure_samesite_lax_cookie(self, fx):
        response = fx.login()
        assert response.status_code == 200
        header = response.headers.get("set-cookie", "").lower()
        assert COOKIE_NAME.lower() in header
        assert "httponly" in header
        assert "secure" in header
        assert "samesite=lax" in header
        assert "path=/admin/v1" in header

    def test_the_response_body_carries_role_and_scope_but_never_the_raw_token(self, fx):
        body = fx.login().json()
        assert body == {"username": "alice", "role": "editor", "client_ids": ["alpha"]}

    def test_wrong_password_is_401(self, fx):
        assert fx.login(password="wrong").status_code == 401

    def test_unknown_username_is_401(self, fx):
        assert fx.login(username="ghost").status_code == 401

    def test_repeated_failures_eventually_become_429(self, fx):
        for _ in range(5):
            assert fx.login(password="wrong").status_code == 401
        assert fx.login(password="wrong").status_code == 429
        assert fx.login().status_code == 429  # even the right password, while locked out

    def test_admin_cookie_secure_can_be_disabled_for_local_http_development(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha", admin_cookie_secure=False)
        fixture.users.create(editor())
        header = fixture.login().headers.get("set-cookie", "").lower()
        assert "secure" not in header


class TestMe:
    def test_without_a_session_is_401(self, fx):
        assert fx.client.get("/admin/v1/auth/me").status_code == 401

    def test_with_a_session_returns_the_account(self, fx):
        fx.login()
        response = fx.client.get("/admin/v1/auth/me")
        assert response.status_code == 200
        assert response.json() == {"username": "alice", "role": "editor", "client_ids": ["alpha"]}

    def test_a_garbage_cookie_is_401(self, fx):
        fx.client.cookies.set(COOKIE_NAME, "not-a-real-token")
        assert fx.client.get("/admin/v1/auth/me").status_code == 401

    def test_an_expired_session_is_401(self, fx):
        fx.login()
        fx.clock.advance(timedelta(hours=25))  # default ttl is 24h
        assert fx.client.get("/admin/v1/auth/me").status_code == 401


class TestLogout:
    def test_logout_clears_the_cookie_and_ends_the_session(self, fx):
        fx.login()
        assert fx.client.post("/admin/v1/auth/logout").status_code == 200
        assert fx.client.get("/admin/v1/auth/me").status_code == 401

    def test_logout_without_a_session_is_401_not_a_crash(self, fx):
        assert fx.client.post("/admin/v1/auth/logout").status_code == 401


# ---- building blocks for the not-yet-built resource routers ---------------------------------


def _owner() -> AdminUser:
    return AdminUser(
        id="id-owner",
        username="root",
        password_hash="x",
        role=AdminRole.OWNER,
        created_at=NOW,
    )


class TestRequireRole:
    def test_an_allowed_role_passes_through(self):
        dependency = require_role(lambda: None, AdminRole.OWNER, AdminRole.OPERATOR)
        admin = _owner()
        assert dependency(admin=admin) is admin

    def test_a_disallowed_role_is_403(self):
        dependency = require_role(lambda: None, AdminRole.OWNER)
        with pytest.raises(HTTPException) as exc:
            dependency(admin=editor())
        assert exc.value.status_code == 403


class TestEnsureClientAccess:
    def test_owner_reaches_any_client(self):
        ensure_client_access(_owner(), "anything")  # must not raise

    def test_editor_reaches_only_its_own_scope(self):
        ensure_client_access(editor(client_ids=frozenset({"alpha"})), "alpha")  # must not raise

    def test_a_client_outside_the_scope_is_404_not_403(self):
        with pytest.raises(HTTPException) as exc:
            ensure_client_access(editor(client_ids=frozenset({"alpha"})), "beta")
        assert exc.value.status_code == 404
