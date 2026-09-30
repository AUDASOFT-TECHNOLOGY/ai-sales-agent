"""`PUT /admin/v1/clients/{client_id}/llm`: admin-editable LLM provider and model.

Covers: authentication required, role permissions (viewer cannot write), updating provider and
model, persistence through `ConfigStore` (survives a rebuild of the application), an unwired
provider, a model the deployment's `LLMRegistry` does not allow, that no response ever carries a
secret value, and that every other client setting is preserved.
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
from sales_app.wiring import IntegrationBuilders
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"


def builders_with_anthropic(clock):
    """The usual fake builders, plus a second wired LLM provider to exercise a real switch."""
    builders, made = fake_builders(clock)
    builders = IntegrationBuilders(
        repositories=builders.repositories,
        llms={**builders.llms, "anthropic": builders.llms["openai"]},
        channels=builders.channels,
    )
    return builders, made


def _settings(tmp_path, **kw):
    # `Settings.config_dir` otherwise defaults to the relative "config" (see settings.py), which
    # would make `store.publish()` write outside the test's `tmp_path`. `clients_dir` here *is*
    # `tmp_path` (see `write_client`), so the store must live under a name starting with "_": the
    # loader skips those when discovering clients (see loader.py), the same trick "_template" uses
    # in test_admin_clients_api.py.
    kw.setdefault("config_dir", tmp_path / "_config")
    return make_settings(tmp_path, **kw)


def build(tmp_path, *client_ids, users=None, sessions=None, clock=None, with_anthropic=False, **settings_kw):
    clock = clock or FakeClock(NOW)
    for cid in client_ids:
        write_client(tmp_path, cid)
    builders, made = (builders_with_anthropic(clock) if with_anthropic else fake_builders(clock))
    application = build_application(
        _settings(tmp_path, **settings_kw),
        env=env_for(*client_ids),
        builders=builders,
        clock=clock,
        executor=InlineExecutor(),
        admin_user_store=users if users is not None else InMemoryAdminUserStore(),
        admin_session_store=sessions if sessions is not None else InMemoryAdminSessionStore(),
    )
    return application, made


def user(username, role, client_ids=frozenset({"alpha"})) -> AdminUser:
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=role,
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
    def __init__(self, tmp_path, *client_ids, users=None, clock=None, with_anthropic=False, **settings_kw):
        self.tmp_path = tmp_path
        self.users = users if users is not None else InMemoryAdminUserStore()
        self.sessions = InMemoryAdminSessionStore()
        self.clock = clock or FakeClock(NOW)
        self.settings_kw = settings_kw
        self.with_anthropic = with_anthropic
        self.application, self.made = build(
            tmp_path,
            *client_ids,
            users=self.users,
            sessions=self.sessions,
            clock=self.clock,
            with_anthropic=with_anthropic,
            **settings_kw,
        )
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def login(self, username="alice", password=PASSWORD):
        return self.client.post("/admin/v1/auth/login", json={"username": username, "password": password})

    def rebuild(self):
        """A fresh `Application` over the same clients/config dirs: what a restart would see."""
        builders, _made = (
            builders_with_anthropic(self.clock) if self.with_anthropic else fake_builders(self.clock)
        )
        return build_application(
            _settings(self.tmp_path, **self.settings_kw),
            env=env_for("alpha"),
            builders=builders,
            clock=self.clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=self.sessions,
        )


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path, "alpha")
    fixture.users.create(user("alice", AdminRole.EDITOR, client_ids=frozenset({"alpha"})))
    fixture.users.create(user("vera", AdminRole.VIEWER, client_ids=frozenset({"alpha"})))
    fixture.users.create(owner())
    return fixture


def as_alice(fx):
    fx.login()
    return fx.client


def as_root(fx):
    fx.login("root")
    return fx.client


def as_vera(fx):
    fx.login("vera")
    return fx.client


class TestRequiresAuth:
    def test_anonymous_cannot_update_llm(self, fx):
        response = fx.client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "test-model"})
        assert response.status_code == 401


class TestPermissions:
    def test_a_viewer_cannot_update_llm(self, fx):
        client = as_vera(fx)
        response = client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "test-model"})
        assert response.status_code == 403

    def test_an_editor_can_update_llm(self, fx):
        client = as_alice(fx)
        response = client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "new-model"})
        assert response.status_code == 200

    def test_a_scoped_editor_gets_404_outside_its_scope(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha", "beta")
        fixture.users.create(user("alice", AdminRole.EDITOR, client_ids=frozenset({"alpha"})))
        fixture.login()
        response = fixture.client.put(
            "/admin/v1/clients/beta/llm", json={"provider": "openai", "model": "new-model"}
        )
        assert response.status_code == 404


class TestUpdateProviderAndModel:
    def test_updating_the_model_only(self, fx):
        client = as_root(fx)
        response = client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
        assert response.status_code == 200
        assert response.json() == {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"}

    def test_updating_the_provider_too(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha", with_anthropic=True)
        fixture.users.create(owner())
        fixture.login("root")
        response = fixture.client.put(
            "/admin/v1/clients/alpha/llm", json={"provider": "anthropic", "model": "claude"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "anthropic" and body["model"] == "claude"
        # api_key_env is operator wiring: untouched by a provider switch from the document.
        assert body["api_key_env"] == "OPENAI_API_KEY"


class TestPersistsThroughConfigStore:
    def test_the_new_version_is_stored(self, fx):
        client = as_root(fx)
        client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
        current = fx.application.config_store.current("alpha")
        assert current.version == 1
        assert current.document["llm"] == {"provider": "openai", "model": "gpt-5"}

    def test_a_second_update_publishes_a_new_version_on_top_of_the_first(self, fx):
        client = as_root(fx)
        client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
        client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-6"})
        history = fx.application.config_store.history("alpha")
        assert [v.version for v in history] == [1, 2]
        assert history[-1].document["llm"] == {"provider": "openai", "model": "gpt-6"}

    def test_takes_effect_on_the_next_application_build_not_immediately(self, fx):
        # No hot reload exists yet (see docs/client-configuration-design.md section 6): the
        # already-running registry keeps serving the old runtime until the process rebuilds.
        client = as_root(fx)
        client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
        assert client.get("/admin/v1/clients/alpha").json()["llm"]["model"] == "test-model"
        rebuilt = fx.rebuild()
        assert rebuilt.registry.get("alpha").config.spec.llm.model == "gpt-5"


class TestProviderNotWired:
    def test_a_provider_that_is_not_wired_is_refused(self, fx):
        client = as_root(fx)
        response = client.put(
            "/admin/v1/clients/alpha/llm", json={"provider": "anthropic", "model": "claude"}
        )
        assert response.status_code == 400
        assert "not wired" in response.json()["detail"]
        # Nothing was published: the store is untouched.
        assert fx.application.config_store.current("alpha") is None


class TestModelNotAllowed:
    def test_a_model_off_the_deployment_allow_list_is_refused(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha", allowed_llm_models={"openai": ("test-model",)})
        fixture.users.create(owner())
        fixture.login("root")
        response = fixture.client.put(
            "/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "not-allowed"}
        )
        assert response.status_code == 400
        assert "not allowed" in response.json()["detail"]
        assert fixture.application.config_store.current("alpha") is None


class TestNoSecretsLeak:
    def test_no_secret_value_ever_appears(self, fx):
        client = as_root(fx)
        response = client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
        assert response.status_code == 200
        body = response.json()
        # The env var NAME is shown, like the read-only detail endpoint does -- never its value.
        assert body["api_key_env"] == "OPENAI_API_KEY"
        assert "sk-test" not in response.text  # OPENAI_API_KEY's value


class TestPreservesOtherSettings:
    def test_other_settings_are_unchanged_after_an_llm_update(self, fx):
        client = as_root(fx)
        before = client.get("/admin/v1/clients/alpha").json()
        client.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
        # What the next rebuild will see (see TestPersistsThroughConfigStore): read it straight
        # from the store, since the already-running registry does not hot-reload.
        document = fx.application.config_store.current("alpha").document
        assert document["display_name"] == before["display_name"]
        assert document["mode"] == before["mode"]
        assert document["timezone"] == before["timezone"]
        assert document["daily_action_limit"] == before["daily_action_limit"]
        assert document["sheets"]["spreadsheet_id"] == before["sheets"]["spreadsheet_id"]
        assert document["schedule"]["daily_at"] == before["schedule"]["daily_at"]
        assert document["llm"]["model"] == "gpt-5"  # only this changed
