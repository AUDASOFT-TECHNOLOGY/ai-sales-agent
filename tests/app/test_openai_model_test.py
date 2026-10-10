"""`POST /admin/v1/settings/openai/models/{model_id}/test`: a real request to one model.

The connection test only lists what the account can see. This one asks the model, by its exact Model
ID, for a few tokens, so it needs an explicit confirmation, generates at most 16 tokens and says
whether the model is really usable. It may be run on a model that is switched off. How it ended is
kept with the model, so the Admin can tell a model never tested from one that failed.

The remote service is replaced at the HTTP transport only: no automated test reaches OpenAI."""

# ruff: noqa: F811  (the `fx` fixture is imported from test_admin_settings_integrations)

import json
from pathlib import Path

import httpx
import pytest
from test_admin_integrations_api import SECRET, put
from test_admin_settings_integrations import Remote, fx, with_remote  # noqa: F401

from sales_app.connection_tests import INFERENCE_MAX_TOKENS, check_openai_inference
from sales_app.llm_models import ModelTest, record_test, settings_catalog, stored_models
from sales_app.secret_store import GLOBAL_SCOPE

M = "/admin/v1/settings/openai/models"
G = "/admin/v1/settings/integrations"
MODEL = "ft:sol.id-v1"  # a Model ID with every character an id may have besides letters
ECHO = "the-service-echoed-your-key-here"
TYPE = "invalid_request_error"  # what an error with no `code` reports: its `type`
SRC = Path(__file__).resolve().parents[2] / "src"


def completion(**extra):
    body = {
        "id": "chatcmpl-1",
        "model": "sol-served-2026",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 9},
    }
    return httpx.Response(200, json={**body, **extra})


def failure(status, code=None):
    error = {"message": ECHO, "type": "invalid_request_error"}
    if code is not None:
        error["code"] = code
    return httpx.Response(status, json={"error": error})


def configure(fx, handler, *, key=True, base_url=True):
    """An owner, the OpenAI account stored, one model in Settings, and a fake remote service."""
    remote = Remote(handler)
    with_remote(fx, remote)
    client = fx.login("root")
    stored = {"openai.organization": "org-abc", "openai.project": "proj_123"}
    if key:
        stored["openai.api_key"] = SECRET
    if base_url:
        stored["openai.base_url"] = "https://llm.example.test/v1"
    for name, value in stored.items():
        assert put(client, f"{G}/{name}", value).status_code == 200
    added = client.post(M, json={"name": "Sol", "id": MODEL, "active": True})
    assert added.status_code == 200
    return client, remote


def run(client, model_id=MODEL, confirm=True):
    return client.post(f"{M}/{model_id}/test" + ("?confirm=true" if confirm else ""))


def catalog(client):
    providers = {p["provider"]: p for p in client.get("/admin/v1/settings/llm").json()["catalog"]}
    return {m["key"]: m for m in providers["openai"]["models"]}


def never_called(request):
    raise AssertionError("no request may be sent")


class TestItNeedsAnExplicitConfirmation:
    @pytest.mark.parametrize("confirm", ["", "?confirm=false"])
    def test_without_confirm_nothing_is_sent(self, fx, confirm):
        client, remote = configure(fx, never_called)
        response = client.post(f"{M}/{MODEL}/test{confirm}")
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "real request" in detail and "billed" in detail and "confirm=true" in detail
        assert "16 tokens" in detail
        assert remote.requests == []

    def test_an_unknown_model_is_not_found_and_nothing_is_sent(self, fx):
        client, remote = configure(fx, never_called)
        assert run(client, "not-in-settings").status_code == 404
        assert run(client, "not-in-settings", confirm=False).status_code == 404
        assert remote.requests == []

    def test_only_an_owner_may_run_it(self, fx):
        client, remote = configure(fx, never_called)
        client.post("/admin/v1/auth/logout")
        assert run(client).status_code == 401
        editor = fx.login("eve")
        assert run(editor).status_code == 403
        assert remote.requests == []


class TestTheRequestThatIsSent:
    def test_it_is_one_small_request_for_the_exact_model_id(self, fx):
        client, remote = configure(fx, lambda request: completion())
        assert run(client).status_code == 200
        (request,) = remote.requests
        assert request.method == "POST"
        assert str(request.url) == "https://llm.example.test/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["openai-organization"] == "org-abc"
        assert request.headers["openai-project"] == "proj_123"
        body = json.loads(request.content)
        assert body["model"] == MODEL  # exactly the configured Model ID, not a catalog key
        assert set(body) == {"model", "messages", "max_completion_tokens"}
        assert body["max_completion_tokens"] == INFERENCE_MAX_TOKENS == 16
        assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"

    def test_it_uses_the_default_endpoint_when_none_is_configured(self, fx):
        client, remote = configure(fx, lambda request: completion(), base_url=False)
        run(client)
        assert str(remote.requests[0].url) == "https://api.openai.com/v1/chat/completions"

    def test_the_cap_is_the_parameter_the_adapter_uses_at_runtime(self):
        adapter = (SRC / "sales_integrations" / "openai" / "provider.py").read_text("utf-8")
        assert '"max_completion_tokens"' in adapter

    def test_a_model_that_works_is_reported_usable(self, fx):
        client, _ = configure(fx, lambda request: completion())
        body = run(client).json()
        assert body["ok"] is True and body["outcome"] == "usable"
        assert body["model_id"] == MODEL
        assert body["request_sent"] is True and body["max_tokens"] == 16
        assert body["tested_at"] and body["recorded"] is True
        assert body["details"] == {"tokens_used": "9", "served_model": "sol-served-2026"}
        assert SECRET not in json.dumps(body)

    def test_a_switched_off_model_can_be_tested_and_stays_off(self, fx):
        client, remote = configure(fx, lambda request: completion())
        off = {"name": "Sol", "id": MODEL, "active": False}
        assert client.put(f"{M}/{MODEL}", json=off).status_code == 200
        assert run(client).json()["ok"] is True
        assert len(remote.requests) == 1
        assert client.get(M).json()["models"] == [off]


OUTCOMES = [
    ("invalid_key_401", lambda r: failure(401, "invalid_api_key"), "invalid_api_key", "invalid_api_key"),
    ("forbidden_403", lambda r: failure(403), "invalid_api_key", TYPE),
    ("not_found_404", lambda r: failure(404, "model_not_found"), "model_not_found", "model_not_found"),
    ("not_found_400", lambda r: failure(400, "model_not_found"), "model_not_found", "model_not_found"),
    ("bad_parameter_400", lambda r: failure(400, "unsupported_parameter"), "invalid_parameters", "unsupported_parameter"),
    ("unprocessable_422", lambda r: failure(422), "invalid_parameters", TYPE),
    ("no_quota_429", lambda r: failure(429, "insufficient_quota"), "quota_exceeded", "insufficient_quota"),
    ("rate_limit_429", lambda r: failure(429, "rate_limit_exceeded"), "quota_exceeded", "rate_limit_exceeded"),
    ("server_500", lambda r: failure(500), "service_error", TYPE),
    ("gateway_502", lambda r: failure(502), "service_error", TYPE),
    ("not_a_completion", lambda r: httpx.Response(200, json={"nope": 1}), "unexpected_response", None),
    ("no_choice", lambda r: httpx.Response(200, json={"choices": []}), "unexpected_response", None),
    ("not_json", lambda r: httpx.Response(200, text="<html>"), "unexpected_response", None),
]  # fmt: skip


def raises(error):
    def handler(request):
        raise error

    return handler


class TestEachOutcomeIsToldApart:
    @pytest.mark.parametrize(
        ("handler", "outcome", "code"),
        [pytest.param(h, o, c, id=name) for name, h, o, c in OUTCOMES],
    )
    def test_the_service_answer(self, fx, handler, outcome, code):
        client, _ = configure(fx, handler)
        response = run(client)
        assert response.status_code == 200  # the test ran: its outcome is in the body
        body = response.json()
        assert body["ok"] is False and body["outcome"] == outcome
        assert body["request_sent"] is True and body["recorded"] is True
        assert body.get("details", {}).get("code") == code
        text = response.text
        assert SECRET not in text and ECHO not in text  # nothing the service said is repeated

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [
            (httpx.ReadTimeout("slow"), "timeout"),
            (httpx.ConnectTimeout("slow"), "timeout"),
            (httpx.ConnectError("down"), "unreachable"),
        ],
        ids=["read-timeout", "connect-timeout", "connection-error"],
    )
    def test_the_service_does_not_answer(self, fx, error, outcome):
        client, _ = configure(fx, raises(error))
        body = run(client).json()
        assert body["ok"] is False and body["outcome"] == outcome
        assert SECRET not in json.dumps(body)

    def test_the_messages_differ_where_the_remedy_differs(self, fx):
        client, remote = configure(fx, lambda r: failure(429, "insufficient_quota"))
        no_quota = run(client).json()["message"]
        remote._handler = lambda r: failure(429, "rate_limit_exceeded")
        rate = run(client).json()["message"]
        assert "no quota" in no_quota and "rate limit" in rate

    def test_a_missing_key_sends_nothing_and_records_nothing(self, fx):
        client, remote = configure(fx, never_called, key=False)
        body = run(client).json()
        assert body["ok"] is False and body["outcome"] == "no_api_key"
        assert body["request_sent"] is False and body["tested_at"] is None
        assert body["recorded"] is False
        assert remote.requests == []
        assert catalog(client)["sol"]["last_test"] is None


class TestARecordedTestKeepsTheStableKey:
    def test_a_list_without_keys_and_a_name_that_belongs_to_another_provider(self, fx):
        # A list saved before keys existed holds a model named like a model of another provider.
        # Its key is the first free one; recording a test must not hand it the other one's key.
        client, _ = configure(fx, lambda request: completion())
        legacy = [{"name": "Muse Spark", "id": MODEL, "active": True}]
        fx.application.credentials.save(
            GLOBAL_SCOPE, "openai.models", json.dumps(legacy), actor="t"
        )
        assert set(catalog(client)) >= {"muse-spark-2"}
        before = catalog(client)["muse-spark-2"]
        assert run(client).status_code == 200
        (entry,) = json.loads(fx.application.credentials.reveal(GLOBAL_SCOPE, "openai.models"))
        assert entry["key"] == "muse-spark-2"  # stored as read: it never was `muse-spark`
        assert catalog(client)["muse-spark-2"]["last_test"]["outcome"] == "usable"
        assert before["last_test"] is None
        meta = {p["provider"]: p for p in client.get("/admin/v1/settings/llm").json()["catalog"]}
        assert [m["key"] for m in meta["meta"]["models"]] == ["muse-spark"]  # Meta keeps its own

    def test_the_key_stays_the_same_after_a_second_test_and_a_rename(self, fx):
        client, _ = configure(fx, lambda request: completion())
        fx.application.credentials.save(
            GLOBAL_SCOPE,
            "openai.models",
            json.dumps([{"name": "Muse Spark", "id": MODEL, "active": True}]),
            actor="t",
        )
        run(client)
        run(client)
        client.put(f"{M}/{MODEL}", json={"name": "Renamed", "id": MODEL, "active": True})
        (entry,) = json.loads(fx.application.credentials.reveal(GLOBAL_SCOPE, "openai.models"))
        assert entry["key"] == "muse-spark-2" and entry["last_test"]["outcome"] == "usable"

    def test_recording_without_reserved_keys_is_what_used_to_drift(self, fx):
        # The function itself: given the other providers' keys, it never takes one.
        client, _ = configure(fx, lambda request: completion())
        fx.application.credentials.save(
            GLOBAL_SCOPE,
            "openai.models",
            json.dumps([{"name": "Muse Spark", "id": MODEL, "active": True}]),
            actor="t",
        )
        assert record_test(
            fx.application.credentials,
            MODEL,
            ModelTest("usable", "2026-10-10T10:00:00+00:00"),
            actor="t",
            reserved={"muse-spark"},
        )
        (entry,) = json.loads(fx.application.credentials.reveal(GLOBAL_SCOPE, "openai.models"))
        assert entry["key"] == "muse-spark-2"
        assert client is not None


class TestNeverTested:
    def test_a_model_is_never_tested_until_it_is(self, fx):
        client, _ = configure(fx, lambda request: completion())
        entry = catalog(client)["sol"]
        assert entry["configured"] is True and entry["last_test"] is None
        tested = run(client).json()
        assert catalog(client)["sol"]["last_test"] == {
            "outcome": "usable",
            "at": tested["tested_at"],
        }

    def test_a_later_test_replaces_the_record(self, fx):
        client, remote = configure(fx, lambda request: completion())
        run(client)
        remote._handler = lambda r: failure(404, "model_not_found")
        run(client)
        assert catalog(client)["sol"]["last_test"]["outcome"] == "model_not_found"

    def test_the_record_follows_the_model_when_only_its_name_or_state_changes(self, fx):
        client, _ = configure(fx, lambda request: completion())
        run(client)
        client.put(f"{M}/{MODEL}", json={"name": "Renamed", "id": MODEL, "active": False})
        assert catalog(client)["sol"]["last_test"]["outcome"] == "usable"

    def test_a_new_model_id_is_a_model_never_tested(self, fx):
        client, _ = configure(fx, lambda request: completion())
        run(client)
        client.put(f"{M}/{MODEL}", json={"name": "Sol", "id": "another-id", "active": True})
        assert catalog(client)["sol"]["last_test"] is None

    def test_the_record_is_stored_with_the_model_and_holds_no_secret(self, fx):
        client, _ = configure(fx, lambda r: failure(401, "invalid_api_key"))
        run(client)
        raw = fx.application.credentials.reveal(GLOBAL_SCOPE, "openai.models")
        (entry,) = json.loads(raw)
        assert set(entry["last_test"]) == {"outcome", "at"}
        assert entry["last_test"]["outcome"] == "invalid_api_key"
        assert SECRET not in raw and ECHO not in raw
        again = settings_catalog(fx.application.provider_registry, fx.application.credentials)
        assert again.get("sol").upstream_id == MODEL  # the model itself is untouched

    def test_the_models_list_keeps_its_shape(self, fx):
        client, _ = configure(fx, lambda request: completion())
        run(client)
        assert client.get(M).json() == {"models": [{"name": "Sol", "id": MODEL, "active": True}]}

    def test_a_model_deleted_while_the_test_runs_is_not_brought_back(self, fx):
        def deletes_then_answers(request):
            fx.application.credentials.save(GLOBAL_SCOPE, "openai.models", "[]", actor="t")
            return completion()

        client, _ = configure(fx, deletes_then_answers)
        body = run(client).json()
        assert body["ok"] is True and body["recorded"] is False
        assert stored_models(fx.application.credentials) == []

    def test_keeping_a_record_does_not_rebuild_the_running_clients(self, fx):
        client, _ = configure(fx, lambda request: completion())
        changes = []
        fx.application.credentials.set_on_change(lambda scope, key: changes.append(key))
        run(client)
        assert changes == []
        client.put(f"{M}/{MODEL}", json={"name": "Sol", "id": MODEL, "active": False})
        assert changes == ["openai.models"]  # an edit of the list still does


class TestTheConnectionTestIsUnchanged:
    def test_it_only_lists_models_and_never_asks_one_for_tokens(self, fx):
        def lists(request):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})

        client, remote = configure(fx, lists)
        body = client.post(f"{G}/openai/test").json()
        assert body["ok"] is True and body["details"] == {"Sol": "available"}
        assert [(r.method, r.url.path) for r in remote.requests] == [("GET", "/v1/models")]


class TestTheCheckItself:
    def test_without_a_key_the_service_is_not_even_contacted(self):
        def factory():
            raise AssertionError("no client may be opened")

        result = check_openai_inference(
            api_key=None,
            organization=None,
            project=None,
            base_url=None,
            model_id="m",
            http=factory,
        )
        assert result.outcome.value == "no_api_key" and not result.sent_a_request
