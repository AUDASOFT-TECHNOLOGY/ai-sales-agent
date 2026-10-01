"""Admin Settings: real connection tests, the models offered to clients, and the OpenAI account
settings reaching the provider. The remote services are replaced at the HTTP transport only, so the
requests built here are exactly what would go to OpenAI and Meta."""

import httpx
import pytest
from fastapi.testclient import TestClient
from test_admin_integrations_api import PASSWORD, SECRET, TOKEN, Fixture, put, user

from sales_app.admin_auth import AdminRole
from sales_app.api import create_app
from sales_app.config.secrets import Secrets
from sales_app.connection_tests import check_openai, check_whatsapp_client
from sales_app.secret_store import GLOBAL_SCOPE
from sales_app.wiring.integrations import default_builders

G = "/admin/v1/settings/integrations"
A = "/admin/v1/clients/alpha/integrations"


class Remote:
    """Records the requests and answers from a script."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self) -> httpx.Client:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)

        return httpx.Client(transport=httpx.MockTransport(handle))


def openai_ok(request):
    return httpx.Response(200, json={"data": [{"id": "gpt-sol-x"}, {"id": "gpt-lune-y"}]})


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path)
    fixture.users.create(user("root", AdminRole.OWNER))
    fixture.users.create(user("eve", AdminRole.EDITOR, frozenset({"alpha"})))
    return fixture


def with_remote(fx, remote):
    fx.client = TestClient(
        create_app(fx.application, run_scheduler=False, connection_http=remote),
        base_url="https://testserver",
    )
    return fx


def configure_openai(fx):
    client = fx.login("root")
    for key, value in {
        "openai.api_key": SECRET,
        "openai.organization": "org-abc",
        "openai.project": "proj_123",
        "openai.base_url": "https://llm.example.test/v1",
        "openai.model_sol": "gpt-sol-x",
        "openai.model_lune": "gpt-lune-y",
    }.items():
        assert put(client, f"{G}/{key}", value).status_code == 200
    return client


class TestOpenAITest:
    def test_calls_the_configured_endpoint_with_the_stored_credentials(self, fx):
        remote = Remote(openai_ok)
        with_remote(fx, remote)
        client = configure_openai(fx)
        body = client.post(f"{G}/openai/test").json()
        assert body["ok"] is True
        assert body["details"] == {"GPT Sol": "available", "GPT Lune": "available"}
        (request,) = remote.requests
        assert str(request.url) == "https://llm.example.test/v1/models"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["openai-organization"] == "org-abc"
        assert request.headers["openai-project"] == "proj_123"
        assert SECRET not in client.post(f"{G}/openai/test").text

    def test_reports_a_refused_key_without_echoing_it(self, fx):
        remote = Remote(
            lambda r: httpx.Response(
                401, json={"error": {"message": f"Incorrect API key {SECRET}", "code": "bad_key"}}
            )
        )
        with_remote(fx, remote)
        client = configure_openai(fx)
        response = client.post(f"{G}/openai/test")
        assert response.json()["ok"] is False
        assert "401" in response.json()["message"]
        assert SECRET not in response.text

    def test_reports_a_configured_model_the_account_cannot_use(self, fx):
        remote = Remote(lambda r: httpx.Response(200, json={"data": [{"id": "gpt-sol-x"}]}))
        with_remote(fx, remote)
        body = configure_openai(fx).post(f"{G}/openai/test").json()
        assert body["ok"] is False
        assert body["details"]["GPT Lune"] == "not available to this account"

    def test_without_a_key_nothing_is_called(self, fx):
        remote = Remote(openai_ok)
        with_remote(fx, remote)
        body = fx.login("root").post(f"{G}/openai/test").json()
        assert body["ok"] is False
        assert remote.requests == []

    def test_an_unreachable_service_is_a_failure_not_a_success(self):
        def boom():
            def handle(request):
                raise httpx.ConnectError("no route")

            return httpx.Client(transport=httpx.MockTransport(handle))

        result = check_openai(
            api_key="k", organization=None, project=None, base_url=None, models={}, http=boom
        )
        assert result.ok is False

    def test_only_owners_may_run_it(self, fx):
        with_remote(fx, Remote(openai_ok))
        assert fx.login("eve").post(f"{G}/openai/test").status_code == 403
        assert fx.client.post(f"{G}/openai/test").status_code == 403


class TestWhatsAppTest:
    def test_asks_meta_for_the_clients_phone_number(self, fx):
        remote = Remote(
            lambda r: httpx.Response(
                200, json={"display_phone_number": "+1 555 0100", "verified_name": "Acme"}
            )
        )
        with_remote(fx, remote)
        client = fx.login("root")
        assert put(client, f"{G}/whatsapp.api_version", "v22.0").status_code == 200
        assert put(client, f"{A}/whatsapp.access_token", TOKEN).status_code == 200
        assert put(client, f"{A}/whatsapp.phone_number_id", "1234567890").status_code == 200
        response = client.post(f"{A}/whatsapp/test")
        assert response.json()["ok"] is True
        assert response.json()["details"]["verified_name"] == "Acme"
        (request,) = remote.requests
        assert request.url.path == "/v22.0/1234567890"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in response.text

    def test_a_rejected_token_is_a_failure(self):
        result = check_whatsapp_client(
            access_token=TOKEN,
            phone_number_id="1234567",
            api_version="v21.0",
            http=Remote(lambda r: httpx.Response(400, json={"error": {"code": 190}})),
        )
        assert result.ok is False
        assert "190" in result.message
        assert TOKEN not in result.message

    def test_missing_credentials_are_reported_without_calling_meta(self, fx):
        remote = Remote(openai_ok)
        with_remote(fx, remote)
        client = fx.login("root")
        assert put(client, f"{A}/whatsapp.access_token", TOKEN).status_code == 200
        assert client.post(f"{A}/whatsapp/test").json()["ok"] is False
        assert remote.requests == []

    def test_unknown_client_and_other_roles(self, fx):
        with_remote(fx, Remote(openai_ok))
        assert (
            fx.login("root").post("/admin/v1/clients/nobody/integrations/whatsapp/test").status_code
            == 404
        )
        assert fx.login("eve").post(f"{A}/whatsapp/test").status_code == 403


class TestModelsOfferedToClients:
    def test_nothing_configured_keeps_the_previous_behaviour(self, fx):
        providers = fx.login("root").get("/admin/v1/settings/llm").json()["providers"]
        assert {p["provider"]: p["allowed_models"] for p in providers} == {"openai": None}

    def test_models_come_from_settings(self, fx):
        client = fx.login("root")
        put(client, f"{G}/openai.model_sol", "sol-1")
        put(client, f"{G}/openai.model_lune", "lune-1")
        (openai,) = client.get("/admin/v1/settings/llm").json()["providers"]
        assert openai["allowed_models"] == ["lune-1", "sol-1"]

    def test_a_client_can_only_be_given_a_configured_model(self, fx):
        client = fx.login("root")
        put(client, f"{G}/openai.model_sol", "sol-1")
        rejected = client.put(
            "/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "other"}
        )
        assert rejected.status_code == 400
        assert "Settings" in rejected.json()["detail"]
        accepted = client.put(
            "/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "sol-1"}
        )
        assert accepted.status_code == 200

    def test_a_malformed_model_id_is_refused(self, fx):
        client = fx.login("root")
        assert put(client, f"{G}/openai.model_sol", "has space").status_code == 422
        assert put(client, f"{G}/openai.base_url", "http://insecure.example").status_code == 422


M = "/admin/v1/settings/openai/models"


def add_model(client, name, model_id, active=True):
    return client.post(M, json={"name": name, "id": model_id, "active": active})


class TestOpenAIModelList:
    def test_no_model_is_invented(self, fx):
        client = fx.login("root")
        assert client.get(M).json() == {"models": []}
        (openai,) = client.get("/admin/v1/settings/llm").json()["providers"]
        assert openai["allowed_models"] is None and openai["models"] == []

    def test_models_can_be_added_changed_toggled_and_deleted(self, fx):
        client = fx.login("root")
        assert add_model(client, "Luna", "model-a").json()["models"] == [
            {"name": "Luna", "id": "model-a", "active": True}
        ]
        add_model(client, "Sol", "model-b", active=False)
        renamed = client.put(
            M + "/model-a", json={"name": "Luna 2", "id": "model-c", "active": True}
        )
        assert renamed.json()["models"][0] == {"name": "Luna 2", "id": "model-c", "active": True}
        toggled = client.put(M + "/model-b", json={"name": "Sol", "id": "model-b", "active": True})
        assert toggled.json()["models"][1]["active"] is True
        assert client.delete(M + "/model-c").json()["models"] == [
            {"name": "Sol", "id": "model-b", "active": True}
        ]
        assert client.get(M).json() == {
            "models": [{"name": "Sol", "id": "model-b", "active": True}]
        }

    def test_bad_input_is_refused(self, fx):
        client = fx.login("root")
        add_model(client, "Luna", "model-a")
        assert add_model(client, "Dup", "model-a").status_code == 409
        assert add_model(client, "Bad", "has space").status_code == 422
        assert add_model(client, "  ", "model-z").status_code == 422
        assert client.put(M + "/nope", json={"name": "x", "id": "y"}).status_code == 404
        assert client.delete(M + "/nope").status_code == 404
        add_model(client, "Sol", "model-b")
        clash = client.put(M + "/model-b", json={"name": "Sol", "id": "model-a"})
        assert clash.status_code == 409

    def test_only_an_owner_changes_the_list(self, fx):
        add_model(fx.login("root"), "Luna", "model-a")
        editor = fx.login("eve")
        assert editor.get(M).status_code == 200
        assert add_model(editor, "X", "model-x").status_code == 403
        assert editor.put(M + "/model-a", json={"name": "x", "id": "model-a"}).status_code == 403
        assert editor.delete(M + "/model-a").status_code == 403

    def test_client_details_receives_only_active_configured_models(self, fx):
        client = fx.login("root")
        add_model(client, "Luna", "model-a")
        add_model(client, "Sol", "model-b", active=False)
        (openai,) = client.get("/admin/v1/settings/llm").json()["providers"]
        assert openai["allowed_models"] == ["model-a"]
        assert openai["models"] == [{"id": "model-a", "name": "Luna"}]
        url = "/admin/v1/clients/alpha/llm"
        assert client.put(url, json={"provider": "openai", "model": "model-b"}).status_code == 400
        assert client.put(url, json={"provider": "openai", "model": "model-a"}).status_code == 200

    def test_deactivating_every_model_offers_none(self, fx):
        client = fx.login("root")
        add_model(client, "Luna", "model-a", active=False)
        (openai,) = client.get("/admin/v1/settings/llm").json()["providers"]
        assert openai["allowed_models"] == []

    def test_legacy_ids_are_kept_and_shown_as_models(self, fx):
        client = fx.login("root")
        put(client, f"{G}/openai.model_sol", "sol-1")
        assert client.get(M).json() == {
            "models": [{"name": "GPT Sol", "id": "sol-1", "active": True}]
        }
        add_model(client, "New", "model-n")
        ids = [m["id"] for m in client.get(M).json()["models"]]
        assert ids == ["sol-1", "model-n"]

    def test_the_api_key_is_not_returned_and_the_test_uses_the_active_models(self, fx):
        remote = Remote(lambda r: httpx.Response(200, json={"data": [{"id": "model-a"}]}))
        with_remote(fx, remote)
        client = fx.login("root")
        put(client, f"{G}/openai.api_key", SECRET)
        add_model(client, "Luna", "model-a")
        add_model(client, "Gone", "model-missing", active=False)
        assert SECRET not in client.get(G).text
        status = {c["key"]: c for c in client.get(G).json()}["openai.api_key"]
        assert status["configured"] is True and status["value"] is None
        body = client.post(f"{G}/openai/test").json()
        assert body["ok"] is True and body["details"] == {"Luna": "available"}
        assert len(remote.requests) == 1


class TestModelListWithoutCredentialStorage:
    def test_the_list_loads_and_a_change_says_how_to_enable_storage(self, tmp_path):
        fixture = Fixture(tmp_path, key=None)
        fixture.users.create(user("root", AdminRole.OWNER))
        client = fixture.login("root")
        assert client.get(M).json() == {"models": []}
        refused = add_model(client, "Luna", "model-a")
        assert refused.status_code == 503
        assert "SALES_AGENT_SECRETS_KEY" in refused.json()["detail"]


class TestOpenAIAccountReachesTheProvider:
    def test_organization_project_and_base_url_are_passed_to_the_sdk(self, tmp_path, monkeypatch):
        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

        class Spec:
            class llm:
                model = "m"

        config = type(
            "C",
            (),
            {
                "spec": Spec,
                "secrets": Secrets(
                    {
                        "openai.api_key": "k",
                        "openai.organization": "org-1",
                        "openai.project": "proj-1",
                        "openai.base_url": "https://llm.example.test/v1",
                    }
                ),
            },
        )()
        default_builders(make_settings_stub(tmp_path)).llms["openai"](config)
        assert captured["organization"] == "org-1"
        assert captured["project"] == "proj-1"
        assert captured["base_url"] == "https://llm.example.test/v1"

    def test_the_global_store_values_are_resolved_for_a_client(self, fx):
        configure_openai(fx)
        fx.application.runtime_manager._refresh(["alpha"])
        secrets = fx.application.registry.get_config("alpha").secrets
        assert secrets.get_optional("openai.base_url") == "https://llm.example.test/v1"
        assert secrets.get_optional("openai.organization") == "org-abc"
        assert fx.application.credentials.reveal(GLOBAL_SCOPE, "openai.project") == "proj_123"
        assert PASSWORD not in fx.everything_on_disk()


def make_settings_stub(tmp_path):
    from app_support import make_settings

    return make_settings(tmp_path / "clients")
