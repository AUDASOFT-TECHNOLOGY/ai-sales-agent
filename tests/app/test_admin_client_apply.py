"""A change published for a client (its model, its name, its quota) is in force at once.

The Admin routes publish the new settings, then have the running client take them through the same
refresh a changed credential triggers. What the Admin shows is what runs: the running client's own
configuration, or, for a client with no runtime, the settings published for it. The refresh runs on
the application's real background worker, as in production."""

import threading
import time
from datetime import UTC, datetime

import pytest
from app_support import declare_models, env_for, fake_builders, make_settings, write_client
from fastapi.testclient import TestClient

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import admin_clients, create_app
from sales_app.api.admin_clients import rename_client
from sales_app.bootstrap import build_application
from sales_app.client_profiles import InMemoryClientProfileStore
from sales_app.errors import RuntimeBuildError
from sales_app.secret_store import generate_master_key
from sales_app.wiring import IntegrationBuilders
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"
LLM = "/admin/v1/clients/alpha/llm"
MODELS = (
    "test-model",
    "model-x",
    "model-y",
    "model-p",
    "model-d",
    "model-ok",
    "model-put",
    "model-at-creation",
    "slow",
    "boom",
)


class Harness:
    """An application over temporary directories, with a model builder the test can steer."""

    def __init__(self, tmp_path, *, disabled=(), models=None, api_key=True, llm_factory=None):
        """`models`: the Model IDs Settings holds (default `MODELS`; empty: no list at all).
        `api_key` False: the environment has no OpenAI API key and none is stored.
        `llm_factory`: builds the model client of a runtime (default: a fake that never calls out)."""
        self.clients = tmp_path / "clients"
        self.clients.mkdir()
        write_client(self.clients, "alpha")
        for client_id in disabled:
            write_client(self.clients, client_id, enabled=False)
        self.built: list[str] = []  # the model each runtime was built with, in order
        self.gate: dict[str, threading.Event] = {}  # model -> what its build waits for
        clock = FakeClock(NOW)
        builders, _made = fake_builders(clock)
        make_fake_llm = builders.llms["openai"]

        def llm(config):
            model = config.spec.llm.model
            self.built.append(model)
            if model in self.gate:
                self.gate[model].wait(10)
            if model == "boom":
                raise RuntimeBuildError("the model client could not be built")
            return (llm_factory or make_fake_llm)(config)

        environment = env_for("alpha")
        if not api_key:
            environment.pop("OPENAI_API_KEY")
        users = InMemoryAdminUserStore()
        users.create(
            AdminUser(
                id="id-root",
                username="root",
                password_hash=hash_password(PASSWORD),
                role=AdminRole.OWNER,
                created_at=NOW,
            )
        )
        self.application = build_application(
            make_settings(
                self.clients,
                config_dir=tmp_path / "config",
                admin_dir=tmp_path / "admin",
                profiles_dir=tmp_path / "profiles",
                secrets_dir=tmp_path / "secrets",
                secrets_key=generate_master_key(),
            ),
            env=environment,
            builders=IntegrationBuilders(
                repositories=builders.repositories,
                llms={"openai": llm},
                channels=builders.channels,
            ),
            clock=clock,
            admin_user_store=users,
            admin_session_store=InMemoryAdminSessionStore(),
            client_profile_store=InMemoryClientProfileStore(),
        )
        # What an assignment is checked against: the Model IDs these tests give to clients.
        if models is None:
            models = MODELS
        if models:
            declare_models(self.application.credentials, *models)
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )
        login = self.client.post(
            "/admin/v1/auth/login", json={"username": "root", "password": PASSWORD}
        )
        assert login.status_code == 200

    def assign(self, model, client_id="alpha"):
        return self.client.put(
            f"/admin/v1/clients/{client_id}/llm", json={"provider": "openai", "model": model}
        )

    def detail(self, client_id="alpha"):
        return self.client.get(f"/admin/v1/clients/{client_id}").json()

    def listed(self, client_id="alpha"):
        rows = self.client.get("/admin/v1/clients").json()
        return next(row for row in rows if row["client_id"] == client_id)

    def status(self, client_id="alpha"):
        return self.client.get(f"/admin/v1/clients/{client_id}/runtime-status").json()

    def running_config(self, client_id="alpha"):
        return self.application.registry.get_config(client_id).spec

    def wait_for(self, state, client_id="alpha", seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.status(client_id)["apply_state"] == state:
                return
            time.sleep(0.02)
        raise AssertionError(f"still {self.status(client_id)['apply_state']!r}, not {state!r}")


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.application.close()


class TestTheModelIsInForceAtOnce:
    def test_the_running_client_and_everything_shown_use_the_new_model(self, h):
        engine_before = h.application.registry.get("alpha").engine
        response = h.assign("model-x")
        assert response.status_code == 200
        assert response.json()["model"] == "model-x"
        assert h.running_config().llm.model == "model-x"
        assert h.detail()["llm"]["model"] == h.detail()["llm_model"] == "model-x"
        assert h.listed()["llm_model"] == "model-x"
        assert h.application.registry.get("alpha").engine is not engine_before
        assert h.built[-1] == "model-x"  # the model client was built with exactly this model
        assert h.status()["apply_state"] == "applied"

    def test_replacing_x_by_y_is_just_as_immediate(self, h):
        h.assign("model-x")
        h.assign("model-y")
        assert h.running_config().llm.model == "model-y"
        assert h.detail()["llm_model"] == "model-y"
        assert h.built[-2:] == ["model-x", "model-y"]

    def test_patching_the_client_applies_the_model_too(self, h):
        response = h.client.patch(
            "/admin/v1/clients/alpha", json={"llm_provider": "openai", "llm_model": "model-p"}
        )
        assert response.status_code == 200
        assert response.json()["llm"]["model"] == "model-p"
        assert h.running_config().llm.model == "model-p"
        assert h.status()["apply_state"] == "applied"

    def test_the_name_and_the_quota_apply_at_once(self, h):
        response = h.client.patch(
            "/admin/v1/clients/alpha", json={"display_name": "Renamed", "daily_action_limit": 7}
        )
        assert response.status_code == 200
        spec = h.running_config()
        assert (spec.display_name, spec.daily_action_limit) == ("Renamed", 7)
        assert h.listed()["display_name"] == "Renamed"

    def test_a_rename_by_the_client_is_published_but_does_not_rebuild_the_agent(self, h):
        engine = h.application.registry.get("alpha").engine
        before = len(h.built)
        rename_client(h.application, "alpha", "Own Name", author="client:alpha")
        document = h.application.config_store.current("alpha").document
        assert document["display_name"] == "Own Name"  # the new name is published
        assert h.application.registry.get("alpha").engine is engine  # nothing was rebuilt
        assert len(h.built) == before
        assert h.running_config().display_name == "Alpha"  # the agent still runs on the old name

    def test_the_agent_takes_the_clients_new_name_at_its_next_refresh(self, h):
        rename_client(h.application, "alpha", "Own Name", author="client:alpha")
        assert h.assign("model-ok").status_code == 200  # any refresh of that client picks it up
        assert h.running_config().display_name == "Own Name"

    def test_a_rejected_assignment_changes_nothing(self, h):
        h.application.settings.allowed_llm_models = {"openai": ("test-model",)}
        before = len(h.built)
        assert h.assign("not-allowed").status_code == 400
        assert h.running_config().llm.model == "test-model"
        assert len(h.built) == before  # nothing was rebuilt


class TestARefreshThatDoesNotEndInTime:
    def test_the_request_still_succeeds_and_the_change_lands_later(self, h, monkeypatch):
        monkeypatch.setattr(admin_clients, "REFRESH_WAIT_SECONDS", 0.05)
        h.gate["slow"] = threading.Event()
        response = h.assign("slow")
        assert response.status_code == 200  # published; the refresh is still going
        assert h.application.config_store.current("alpha").document["llm"]["model"] == "slow"
        assert h.running_config().llm.model == "test-model"  # what runs is still the old one
        assert h.detail()["llm_model"] == "test-model"  # and that is what is shown
        assert h.status()["apply_state"] == "applying"
        h.gate["slow"].set()
        h.wait_for("applied")
        assert h.running_config().llm.model == "slow"
        assert h.detail()["llm_model"] == "slow"

    def test_a_failed_refresh_is_reported_not_raised(self, h):
        response = h.assign("boom")
        assert response.status_code == 200  # the change is published; applying it failed
        status = h.status()
        assert status["apply_state"] == "failed"
        assert status["apply_errors"]  # the reason is given
        assert h.running_config().llm.model == "test-model"  # the previous client keeps running
        assert h.detail()["llm_model"] == "test-model"  # the Admin shows what really runs

    def test_the_next_good_assignment_recovers(self, h):
        h.assign("boom")
        h.assign("model-ok")
        assert h.status()["apply_state"] == "applied"
        assert h.running_config().llm.model == "model-ok"


class TestAClientWithNoRuntime:
    @pytest.fixture
    def d(self, tmp_path):
        harness = Harness(tmp_path, disabled=("dormant",))
        yield harness
        harness.application.close()

    def test_a_disabled_client_shows_the_model_published_for_it(self, d):
        response = d.client.patch(
            "/admin/v1/clients/dormant", json={"llm_provider": "openai", "llm_model": "model-d"}
        )
        assert response.status_code == 200
        assert response.json()["llm_model"] == "model-d"
        assert d.detail("dormant")["llm"]["model"] == "model-d"
        assert d.listed("dormant")["llm_model"] == "model-d"
        assert d.listed("dormant")["status"] == "disabled"
        assert d.application.registry.get("dormant") is None  # nothing was started for it
        assert d.application.registry.get_config("dormant") is None

    def test_a_disabled_client_shows_its_published_name_and_quota(self, d):
        d.client.patch(
            "/admin/v1/clients/dormant", json={"display_name": "Sleeper", "daily_action_limit": 3}
        )
        detail = d.detail("dormant")
        assert (detail["display_name"], detail["daily_action_limit"]) == ("Sleeper", 3)

    def test_without_a_published_version_the_client_yaml_is_shown_as_before(self, d):
        assert d.application.config_store.current("dormant") is None
        assert d.detail("dormant")["llm"]["model"] == "test-model"

    def test_a_created_client_keeps_its_model_in_client_yaml(self, d):
        body = {
            "display_name": "Acme Corp",
            "login": "Acme.Admin",
            "temporary_password": "temp-pass-123",
            "llm_provider": "openai",
            "llm_model": "model-at-creation",
            "status": "inactive",
            "whatsapp_number": "+216 22 768 307",
        }
        assert d.client.post("/admin/v1/clients", json=body).status_code == 201
        assert d.detail("acme-corp")["llm"]["model"] == "model-at-creation"
        assert d.application.registry.get_config("acme-corp") is None  # no runtime at creation
        # The route for a client that is not configured yet is unchanged: it is not found there,
        # and its model is edited with PATCH, which writes the skeleton `client.yaml`.
        assert d.assign("model-put", "acme-corp").status_code == 404
        patched = d.client.patch(
            "/admin/v1/clients/acme-corp", json={"llm_provider": "openai", "llm_model": "model-p"}
        )
        assert patched.status_code == 200
        assert d.detail("acme-corp")["llm"]["model"] == "model-p"
        assert d.application.config_store.current("acme-corp") is None
        assert d.application.registry.get_config("acme-corp") is None
