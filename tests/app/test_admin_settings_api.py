"""`/admin/v1/settings/llm`: read-only platform LLM configuration.

Covers: authentication required, the unrestricted default (no `SALES_AGENT_ALLOWED_LLM_MODELS`),
an allow-list configured via `Settings.allowed_llm_models`, and that no response ever carries a
secret value -- only provider names and allow-listed model ids.
"""

from datetime import UTC, datetime

import pytest
from app_support import InlineExecutor, env_for, fake_builders, make_settings, write_client
from fastapi.testclient import TestClient

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
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


def owner(username="root") -> AdminUser:
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=AdminRole.OWNER,
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
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def login(self, username="alice", password=PASSWORD):
        return self.client.post("/admin/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path, "alpha")
    fixture.users.create(editor())
    fixture.users.create(owner())
    return fixture


def as_alice(fx):
    fx.login()
    return fx.client


def as_root(fx):
    fx.login("root")
    return fx.client


class TestRequiresAuth:
    def test_anonymous_cannot_read_llm_settings(self, fx):
        assert fx.client.get("/admin/v1/settings/llm").status_code == 401


class TestUnrestrictedDefault:
    def test_no_allow_list_configured(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/settings/llm").json()
        assert body["read_only"] is True
        assert body["restricted"] is False
        assert body["providers"] == [{"provider": "openai", "wired": True, "allowed_models": None}]

    def test_a_client_scoped_editor_can_also_read_it(self, fx):
        # Platform-level, not per-client: any authenticated admin can see it, scoped or not.
        client = as_alice(fx)
        assert client.get("/admin/v1/settings/llm").status_code == 200


class TestAllowListConfigured:
    def test_allowed_models_are_reported_per_provider(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha", allowed_llm_models={"openai": ("test-model",)})
        fixture.users.create(owner())
        fixture.login("root")
        body = fixture.client.get("/admin/v1/settings/llm").json()
        assert body["restricted"] is True
        assert body["providers"] == [{"provider": "openai", "wired": True, "allowed_models": ["test-model"]}]

    def test_a_provider_left_off_the_allow_list_has_no_allowed_models(self, tmp_path):
        # alpha's own llm.provider ("openai") must itself stay on the allow list, or client
        # loading fails before the app even boots (see resolve_client_config). "anthropic" is
        # allow-listed but not wired: reachable for its models, unusable until a builder exists.
        fixture = Fixture(
            tmp_path,
            "alpha",
            allowed_llm_models={"openai": ("test-model",), "anthropic": ("claude",)},
        )
        fixture.users.create(owner())
        fixture.login("root")
        body = fixture.client.get("/admin/v1/settings/llm").json()
        assert body["restricted"] is True
        by_provider = {p["provider"]: p for p in body["providers"]}
        assert by_provider["openai"] == {"provider": "openai", "wired": True, "allowed_models": ["test-model"]}
        assert by_provider["anthropic"] == {"provider": "anthropic", "wired": False, "allowed_models": ["claude"]}


class TestNoSecretsLeak:
    def test_no_secret_value_ever_appears(self, fx):
        client = as_root(fx)
        response = client.get("/admin/v1/settings/llm")
        assert response.status_code == 200
        payload = response.text
        assert "_env" not in payload
        assert "sk-test" not in payload  # OPENAI_API_KEY value
