"""What the Admin may assign to a client: a model the Admin has configured in Settings, and on.

The same rule on every route that assigns one (`PUT /clients/{id}/llm`, `PATCH /clients/{id}`,
creation): a provider that is wired here, then the deployment's allow-list, then the models of
Settings. Each refusal says what is wrong. A missing API key is not a refusal: the client is simply
not ready until the key is set. Loading an existing configuration is not subject to it."""

import re
from pathlib import Path

import pytest
from test_admin_client_apply import Harness

M = "/admin/v1/settings/openai/models"
SRC = Path(__file__).resolve().parents[2] / "src" / "sales_app"


def create_body(provider, model):
    return {
        "display_name": "New Co",
        "login": "New.Client",
        "temporary_password": "temp-pass-123",
        "llm_provider": provider,
        "llm_model": model,
        "status": "inactive",
        "whatsapp_number": "+216 22 768 307",
    }


def put(h, provider, model):
    return h.client.put("/admin/v1/clients/alpha/llm", json={"provider": provider, "model": model})


def patch(h, provider, model):
    return h.client.patch(
        "/admin/v1/clients/alpha", json={"llm_provider": provider, "llm_model": model}
    )


def create(h, provider, model):
    return h.client.post("/admin/v1/clients", json=create_body(provider, model))


ROUTES = pytest.mark.parametrize(
    ("route", "ok"), [(put, 200), (patch, 200), (create, 201)], ids=["put", "patch", "create"]
)
ROUTE = pytest.mark.parametrize("route", [put, patch, create], ids=["put", "patch", "create"])


def untouched(h, route):
    """Nothing was assigned, published, written or rebuilt."""
    if route is create:
        assert not (h.clients / "new-co").exists()
    else:
        assert h.application.config_store.current("alpha") is None
        assert h.running_config().llm.model == "test-model"


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.application.close()


@pytest.fixture
def bare(tmp_path):
    """Settings holds no model at all."""
    harness = Harness(tmp_path, models=())
    yield harness
    harness.application.close()


class TestAModelOfSettingsMayBeAssigned:
    @ROUTES
    def test_a_configured_active_model_is_accepted(self, h, route, ok):
        assert route(h, "openai", "model-x").status_code == ok

    def test_the_older_per_model_credentials_count_until_the_list_is_saved(self, bare):
        bare.application.credentials.save("global", "openai.model_sol", "legacy-sol", actor="t")
        assert put(bare, "openai", "legacy-sol").status_code == 200


class TestNothingConfigured:
    @ROUTE
    def test_nothing_can_be_assigned_and_the_message_says_what_to_do(self, bare, route):
        response = route(bare, "openai", "model-x")
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "no model is configured for provider 'openai' in Settings" in detail
        assert "add one" in detail
        untouched(bare, route)

    def test_an_emptied_list_is_the_same_as_no_list(self, h):
        h.application.credentials.save("global", "openai.models", "[]", actor="t")
        assert h.client.get(M).json() == {"models": []}
        response = put(h, "openai", "model-x")
        assert response.status_code == 400
        assert "no model is configured" in response.json()["detail"]


class TestAnUnknownModel:
    @ROUTE
    def test_it_is_refused_as_not_configured(self, h, route):
        response = route(h, "openai", "not-a-model")
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "llm.model 'not-a-model' is not configured in Settings for provider 'openai'"
        )
        untouched(h, route)


class TestASwitchedOffModel:
    @ROUTE
    def test_it_is_refused_as_switched_off_not_as_unknown(self, h, route):
        h.client.put(M + "/model-x", json={"name": "model-x", "id": "model-x", "active": False})
        response = route(h, "openai", "model-x")
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "switched off in Settings" in detail and "not configured" not in detail
        assert "switch it on" in detail
        untouched(h, route)

    def test_it_is_accepted_again_once_switched_on(self, h):
        off = {"name": "model-x", "id": "model-x", "active": False}
        h.client.put(M + "/model-x", json=off)
        assert put(h, "openai", "model-x").status_code == 400
        h.client.put(M + "/model-x", json={**off, "active": True})
        assert put(h, "openai", "model-x").status_code == 200


class TestTheProvider:
    @ROUTE
    def test_a_provider_nobody_integrated_is_refused(self, h, route):
        response = route(h, "nowhere", "model-x")
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'nowhere' is not wired in this deployment" in detail
        assert "known" not in detail
        untouched(h, route)

    @ROUTE
    def test_a_known_provider_without_an_adapter_says_so(self, h, route):
        response = route(h, "meta", "muse-spark")
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'meta' is not wired in this deployment" in detail
        assert "known, but no adapter can run it yet" in detail
        untouched(h, route)

    def test_a_client_that_does_not_exist_is_not_found_whatever_the_body(self, h):
        # The URL comes first: this never became a 400 about the model.
        missing = h.client.put(
            "/admin/v1/clients/nope/llm", json={"provider": "openai", "model": "not-a-model"}
        )
        assert missing.status_code == 404


class TestTheDeploymentsAllowListComesFirst:
    @ROUTE
    def test_a_model_off_the_list_is_refused_as_not_allowed(self, h, route):
        h.application.settings.allowed_llm_models = {"openai": ("test-model",)}
        response = route(h, "openai", "model-x")  # configured in Settings, but not allowed
        assert response.status_code == 400
        assert "not allowed for provider 'openai'" in response.json()["detail"]
        untouched(h, route)


class TestAMissingApiKeyIsNotARefusal:
    def test_the_client_is_assigned_the_model_and_is_not_ready_until_the_key_is_set(self, tmp_path):
        h = Harness(tmp_path, api_key=False)
        try:
            assert h.status()["readiness"] == "not_ready"
            response = put(h, "openai", "model-x")
            assert response.status_code == 200
            assert response.json()["model"] == "model-x"
            assert h.application.config_store.current("alpha").document["llm"]["model"] == "model-x"
            status = h.status()
            assert status["readiness"] == "not_ready"
            assert "OpenAI API key is not configured" in status["missing_credentials"]
            assert h.detail()["llm_model"] == "model-x"
            h.client.put(
                "/admin/v1/settings/integrations/openai.api_key", json={"value": "sk-from-admin"}
            )
            h.wait_for("applied")
            assert h.status()["readiness"] == "ready"
            assert h.running_config().llm.model == "model-x"
        finally:
            h.application.close()


class TestLoadingExistingConfigurationsIsNotStricter:
    def test_a_client_runs_on_a_model_that_could_not_be_assigned_today(self, bare):
        assert bare.status()["readiness"] == "ready"
        assert bare.running_config().llm.model == "test-model"
        assert put(bare, "openai", "test-model").status_code == 400  # assigning is strict...
        assert bare.status()["readiness"] == "ready"  # ...loading is not


class TestTheRuleNamesNoProviderOrModel:
    def test_check_model_reads_the_providers_descriptors(self):
        text = (SRC / "llm_models.py").read_text(encoding="utf-8")
        body = text.split("def check_model")[1]
        assert not re.search(r"gpt-|muse|\"openai\"|\"meta\"", body)
        assert "model_list" in body
