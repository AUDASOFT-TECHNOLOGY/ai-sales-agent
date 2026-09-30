"""`/admin/v1/agents` and `/admin/v1/agents/{agent_id}`: read-only agent configuration.

Mirrors `test_admin_clients_api.py`: today's architecture has exactly one Brain per client, so
`agent_id == client_id` and the same registry/raw-yaml read paths apply. Covers: authentication
required, per-client scoping (`AdminUser.can_reach`), an active agent (from the validated
`ClientRegistry`), a disabled agent (from raw `client.yaml`), an unknown agent, and that no
response ever carries a secret value -- only provider names and the Brain's factory/settings
version, never `*_env` values.
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
    # "naga" mirrors a real, disabled client directory: present on disk, excluded from the
    # registry, and must still be reachable as an agent too (see resolve_client_config's
    # "disabled: not validated, not built" rule). Written before the app boots, like alpha/beta.
    write_client(tmp_path, "naga", enabled=False, display_name="Naga", mode="approval", timezone="Africa/Tunis", daily_action_limit=15)
    # Present on disk but skipped entirely by discovery (leading "_", see loader.py) -- must
    # never be reachable through the API either, even by an owner guessing the name.
    write_client(tmp_path, "_template", enabled=False)
    fixture = Fixture(tmp_path, "alpha", "beta")
    fixture.users.create(editor(client_ids=frozenset({"alpha", "naga"})))
    fixture.users.create(owner())
    return fixture


def as_alice(fx):
    fx.login()
    return fx.client


def as_root(fx):
    fx.login("root")
    return fx.client


class TestListRequiresAuth:
    def test_anonymous_cannot_list_agents(self, fx):
        assert fx.client.get("/admin/v1/agents").status_code == 401

    def test_anonymous_cannot_read_an_agent(self, fx):
        assert fx.client.get("/admin/v1/agents/alpha").status_code == 401


class TestListScoping:
    def test_owner_sees_every_agent_including_disabled(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/agents").json()
        assert sorted(a["agent_id"] for a in body) == ["alpha", "beta", "naga"]

    def test_scoped_editor_sees_only_its_own_agents(self, fx):
        client = as_alice(fx)
        body = client.get("/admin/v1/agents").json()
        assert sorted(a["agent_id"] for a in body) == ["alpha", "naga"]  # not "beta"

    def test_an_editor_with_no_scope_sees_nothing(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha")
        fixture.users.create(editor(client_ids=frozenset()))
        fixture.login()
        assert fixture.client.get("/admin/v1/agents").json() == []


class TestActiveAgentSummaryAndDetail:
    def test_summary_fields(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/agents").json()
        alpha = next(a for a in body if a["agent_id"] == "alpha")
        assert alpha["client_id"] == "alpha"  # agent_id == client_id in this V1
        assert alpha["display_name"] == "Alpha"
        assert alpha["status"] == "active"
        assert alpha["mode"] == "approval"
        assert alpha["brain"] == {"factory": "app_test_brains:good_brain", "settings_version": 1}
        assert alpha["llm"] == {"provider": "openai", "model": "test-model"}

    def test_detail_has_configuration_but_no_secret_values(self, fx):
        client = as_root(fx)
        response = client.get("/admin/v1/agents/alpha")
        assert response.status_code == 200
        body = response.json()
        assert body["agent_id"] == "alpha"
        assert body["client_id"] == "alpha"
        assert body["timezone"] == "Africa/Tunis"
        assert body["channels"] == {"sheets": "google_sheets", "whatsapp": "whatsapp"}
        assert body["brain"]["factory"] == "app_test_brains:good_brain"
        assert body["llm"]["model"] == "test-model"
        # No *_env field, and no secret env-var name or value, ever appears.
        payload = response.text
        assert "_env" not in payload
        assert "sk-test" not in payload  # OPENAI_API_KEY value
        assert "token-alpha" not in payload  # ALPHA_TOKEN value
        assert "secret-alpha" not in payload  # ALPHA_APP_SECRET value
        assert "phone-alpha" not in payload  # ALPHA_PHONE_ID value (resolved phone_number_id)

    def test_a_scoped_editor_can_read_an_agent_in_its_scope(self, fx):
        client = as_alice(fx)
        assert client.get("/admin/v1/agents/alpha").status_code == 200

    def test_a_scoped_editor_gets_404_not_403_outside_its_scope(self, fx):
        client = as_alice(fx)
        assert client.get("/admin/v1/agents/beta").status_code == 404


class TestDisabledAgent:
    def test_naga_appears_in_the_list_as_disabled(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/agents").json()
        naga = next(a for a in body if a["agent_id"] == "naga")
        assert naga["status"] == "disabled"
        assert naga["display_name"] == "Naga"
        assert naga["mode"] == "approval"

    def test_naga_detail_is_readable_and_marked_disabled(self, fx):
        client = as_root(fx)
        response = client.get("/admin/v1/agents/naga")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "disabled"
        assert body["agent_id"] == "naga"
        assert body["client_id"] == "naga"


class TestUnknownAgent:
    def test_unknown_agent_id_is_404(self, fx):
        client = as_root(fx)
        assert client.get("/admin/v1/agents/does-not-exist").status_code == 404

    def test_a_directory_that_is_not_a_client_is_404(self, fx):
        # `_template` is excluded from discovery entirely (leading underscore); even an owner
        # must not be able to fetch it by guessing the name.
        client = as_root(fx)
        assert client.get("/admin/v1/agents/_template").status_code == 404
