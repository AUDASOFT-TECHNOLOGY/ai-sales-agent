"""`/client/v1/source`: where a client's prospects come from, its token, and a real connection check.

Covers: authentication and isolation between clients; what is read; what a client may change (the
source, its spreadsheet and tab) and what it may not (the model, the mode, the store, anything of
WhatsApp or Meta); version conflicts and validation, with nothing written on a refusal; the token's
whole life (stored write-only, never in any response, error or log); the real connection checks
through doubles of the two services; HubSpot being configurable and testable but never runnable;
and the readiness check that keeps an agent from starting on it.
"""

import json
import logging
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest
import yaml
from test_admin_clients_api import PASSWORD
from test_client_agent_api import (
    AGENT,
    READINESS,
    START,
    World,
    check_ids,
    set_status,
)

from sales_app.agent_state import AgentStateStoreUnavailableError
from sales_app.api import client_source
from sales_app.config.schema import ClientFile
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, client_scope

SOURCE = "/client/v1/source"
CREDENTIALS = f"{SOURCE}/credentials"
TEST = f"{SOURCE}/test"
SETTINGS = "/client/v1/settings"
TOKEN = "pat-eu1-12345678-1234-1234-1234-123456789abc"
OTHER_TOKEN = "pat-na1-abcdef01-aaaa-bbbb-cccc-1234567890ab"
TOKEN_KEY = "hubspot.access_token"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUv_-123/edit#gid=0"
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUv_-123"


class Net:
    """The two outside services: HubSpot over HTTP, Google Sheets through the platform's probe."""

    def __init__(self):
        self.hubspot_requests: list[httpx.Request] = []
        self.hubspot = lambda request: httpx.Response(200, json={"results": []})
        self.titles = ("Prospects", "Actions")
        self.sheets_error = None
        self.sheets_calls: list[str] = []
        self.probe_files: list[str] = []
        self.probe_error = None

    def http(self) -> httpx.Client:
        def respond(request: httpx.Request) -> httpx.Response:
            self.hubspot_requests.append(request)
            return self.hubspot(request)

        return httpx.Client(transport=httpx.MockTransport(respond))

    def probe(self, credentials_file: str):
        self.probe_files.append(credentials_file)
        if self.probe_error is not None:
            raise self.probe_error
        return SimpleNamespace(metadata=self._metadata)

    def _metadata(self, spreadsheet_id: str):
        self.sheets_calls.append(spreadsheet_id)
        if self.sheets_error is not None:
            raise self.sheets_error
        return SimpleNamespace(titles=self.titles)


class SheetsStatus(Exception):
    def __init__(self, status):
        super().__init__("Google said something with a path /srv/secret.json")
        self.status = status


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(client_source, "TEST_MIN_INTERVAL_SECONDS", 0.0)
    net = Net()
    audit = InMemorySecretAudit()
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=audit)
    w.net, w.audit = net, audit
    w.app_kwargs = {"connection_http": net.http, "sheets_probe": net.probe}
    yield w
    w.application.close()


@pytest.fixture
def alpha(world):
    return world.browser("alpha")


@pytest.fixture
def beta(world):
    return world.browser("beta")


def choose(client, provider, expected=None, **extra):
    return client.put(SOURCE, json={"expected_version": expected, "source": provider, **extra})


def save_token(client, token=TOKEN):
    return client.put(CREDENTIALS, json={"access_token": token})


def history(world, cid="alpha"):
    return world.config_store.history(cid)


def stored_token(world, cid="alpha"):
    return world.application.credentials.reveal(client_scope(cid), TOKEN_KEY)


def written_document(world, cid="alpha"):
    return dict(world.config_store.current(cid).document)


# ---- authentication and isolation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", SOURCE),
        ("put", SOURCE),
        ("put", CREDENTIALS),
        ("delete", CREDENTIALS),
        ("post", TEST),
    ],
)
def test_every_route_needs_a_client_session(world, method, url):
    response = getattr(world.anonymous(), method)(url, **({"json": {}} if method == "put" else {}))
    assert response.status_code == 401


def test_an_admin_session_does_not_open_the_client_routes(world):
    browser = world.anonymous()
    assert browser.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})
    assert browser.get(SOURCE).status_code == 401
    assert browser.post(TEST).status_code == 401
    assert browser.delete(CREDENTIALS).status_code == 401


def test_no_route_takes_a_client_id(world, alpha):
    paths = alpha.get("/openapi.json").json()["paths"]
    source_paths = [p for p in paths if p.startswith(SOURCE)]
    assert set(source_paths) == {SOURCE, CREDENTIALS, TEST}
    assert not any("{" in p for p in source_paths)
    for path in source_paths:
        for operation in paths[path].values():
            assert not [p for p in operation.get("parameters", []) if p["in"] == "path"]


def test_one_client_never_sees_or_changes_another(world, alpha, beta):
    assert choose(alpha, "hubspot").status_code == 200
    assert save_token(alpha).status_code == 200
    seen = beta.get(SOURCE).json()
    assert seen["source"] == "google_sheets" and seen["credential"] is None
    assert TOKEN not in beta.get(SOURCE).text
    assert stored_token(world, "beta") is None
    assert [v.version for v in history(world, "beta")] == []
    assert beta.get(SOURCE).json()["google_sheets"]["spreadsheet_id"] == "sheet-beta"
    # a client's own test only ever uses its own settings and its own token
    assert beta.post(TEST).json()["source"] == "google_sheets"
    assert world.net.sheets_calls == ["sheet-beta"] and world.net.hubspot_requests == []


# ---- reading ---------------------------------------------------------------------------------


def test_a_client_that_never_chose_uses_the_store_provider_google_sheets(world, alpha):
    body = alpha.get(SOURCE).json()
    assert body["version"] is None and body["source"] == "google_sheets"
    assert body["runnable"] is True and body["credential"] is None
    assert body["google_sheets"] == {
        "spreadsheet_id": "sheet-alpha",
        "prospects_tab": "Prospects",
        "share_with": None,
    }
    assert {o["key"]: o["runnable"] for o in body["options"]} == {
        "google_sheets": True,
        "hubspot": True,
    }
    assert body["updated_at"] is None and body["updated_by"] is None


def test_the_client_is_told_which_address_to_share_its_spreadsheet_with(
    world, alpha, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)  # the platform's file is named by a relative path in the tests
    private = {"client_email": "revansa@platform.iam.gserviceaccount.com", "private_key": "KEY"}
    (tmp_path / "creds.json").write_text(json.dumps(private), encoding="utf-8")
    body = alpha.get(SOURCE)
    assert body.json()["google_sheets"]["share_with"] == "revansa@platform.iam.gserviceaccount.com"
    assert "KEY" not in body.text  # only the address, never anything else of the file


def test_responses_are_never_cached(world, alpha):
    for response in (alpha.get(SOURCE), alpha.post(TEST), save_token_when_hubspot(alpha)):
        assert response.headers["cache-control"] == "no-store"


def save_token_when_hubspot(client):
    choose(client, "hubspot")
    return save_token(client)


# ---- choosing the source ----------------------------------------------------------------------


def test_choosing_hubspot_publishes_a_new_version_and_keeps_everything_else(world, alpha):
    before = alpha.get(SETTINGS).json()
    response = choose(alpha, "hubspot")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["version"], body["source"], body["runnable"]) == (1, "hubspot", True)
    assert body["google_sheets"] is None and body["updated_by"] == "client"
    assert body["credential"] == {
        "label": "HubSpot access token",
        "configured": False,
        "updated_at": None,
    }
    (version,) = history(world)
    assert version.author == "client:alpha" and version.note == "client source"
    document = dict(version.document)
    assert document["source"] == {"provider": "hubspot"}
    # nothing else moved: not the model, the mode, the store's tabs, the Brain's settings
    assert document["llm"]["model"] == "test-model" and document["mode"] == "approval"
    assert document["sheets"]["spreadsheet_id"] == "sheet-alpha"
    assert document["sheets"]["actions_tab"] == "Actions"
    assert alpha.get(SETTINGS).json()["settings"] == before["settings"]


def test_choosing_google_sheets_with_a_link_stores_the_id_and_the_tab(world, alpha):
    response = choose(
        alpha, "google_sheets", google_sheets={"spreadsheet": SHEET_URL, "prospects_tab": "Leads"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["google_sheets"]["spreadsheet_id"] == SHEET_ID
    document = written_document(world)
    assert document["sheets"]["spreadsheet_id"] == SHEET_ID
    assert document["sheets"]["prospects_tab"] == "Leads"
    assert document["sheets"]["actions_tab"] == "Actions"  # the store's tabs stay the Admin's
    assert "source" not in document  # the store's own provider is written as nothing at all


def test_a_bare_id_is_accepted_and_one_field_alone_keeps_the_other(world, alpha):
    choose(alpha, "google_sheets", google_sheets={"spreadsheet": SHEET_ID})
    response = choose(alpha, "google_sheets", 1, google_sheets={"prospects_tab": "Leads"})
    assert response.status_code == 200, response.text
    assert response.json()["google_sheets"]["spreadsheet_id"] == SHEET_ID
    assert response.json()["google_sheets"]["prospects_tab"] == "Leads"


def test_going_back_to_google_sheets_leaves_no_trace_of_the_other_choice(world, alpha):
    choose(alpha, "hubspot")
    response = choose(alpha, "google_sheets", 1)
    assert response.status_code == 200 and response.json()["source"] == "google_sheets"
    assert "source" not in written_document(world)


def test_choosing_what_is_already_chosen_publishes_nothing(world, alpha):
    choose(alpha, "hubspot")
    again = choose(alpha, "hubspot", 1)
    assert again.status_code == 200 and again.json()["version"] == 1
    assert len(history(world)) == 1
    same_sheet = choose(alpha, "google_sheets", 1, google_sheets={"prospects_tab": "Prospects"})
    assert same_sheet.status_code == 200 and len(history(world)) == 2  # the provider did change


@pytest.mark.parametrize("provider", ["meta", "openai", "whatsapp", "nothing", "../x", "Google"])
def test_only_a_known_source_can_be_chosen(world, alpha, provider):
    response = choose(alpha, provider)
    assert response.status_code == 422 and not history(world)


def test_spreadsheet_settings_belong_to_the_google_sheets_source(world, alpha):
    response = choose(alpha, "hubspot", google_sheets={"spreadsheet": SHEET_ID})
    assert response.status_code == 422 and not history(world)


@pytest.mark.parametrize("bad", ["not a link", "https://example.com/x", "a b", "sheet/1"])
def test_a_spreadsheet_that_is_neither_a_link_nor_an_id_is_refused_without_echo(world, alpha, bad):
    response = choose(alpha, "google_sheets", google_sheets={"spreadsheet": bad})
    assert response.status_code == 422 and not history(world)
    assert bad not in json.dumps(response.json())


@pytest.mark.parametrize("empty", [{}, {"prospects_tab": None}])
def test_an_empty_spreadsheet_change_is_refused(world, alpha, empty):
    response = choose(alpha, "google_sheets", google_sheets=empty)
    assert response.status_code == 422 and not history(world)


# ---- what a client may not change -------------------------------------------------------------

FORBIDDEN = [
    {"llm": {"model": "gpt-x"}},
    {"model": "gpt-x"},
    {"mode": "autonomous"},
    {"daily_action_limit": 1000},
    {"whatsapp": {"phone_number_id": "123456789"}},
    {"whatsapp_number": "+21620000001"},
    {"phone_number_id": "123456789"},
    {"access_token": TOKEN},
    {"app_secret": "x"},
    {"verify_token": "x"},
    {"sheets": {"actions_tab": "Mine"}},
    {"client_id": "beta"},
    {"enabled": True},
    {"store": {"provider": "hubspot"}},
]


@pytest.mark.parametrize("extra", FORBIDDEN, ids=lambda e: next(iter(e)))
def test_what_belongs_to_the_admin_or_the_platform_is_refused_not_ignored(world, alpha, extra):
    response = choose(alpha, "google_sheets", **extra)
    assert response.status_code == 422 and not history(world)
    assert TOKEN not in response.text  # not even a token in the wrong field is echoed back


@pytest.mark.parametrize(
    "sheets",
    [
        {"actions_tab": "Mine"},
        {"messages_tab": "x"},
        {"log_tab": "x"},
        {"column_mapping": {"prospects.name": "Nom"}},
        {"spreadsheet_id": "abc"},
        {"credentials_env": "OTHER"},
        {"provider": "hubspot"},
    ],
    ids=lambda s: next(iter(s)),
)
def test_the_stores_settings_are_not_the_clients(world, alpha, sheets):
    response = choose(alpha, "google_sheets", google_sheets=sheets)
    assert response.status_code == 422 and not history(world)


def test_the_settings_route_still_cannot_reach_the_source(world, alpha):
    for settings in ({"source": {"provider": "hubspot"}}, {"sheets": {"spreadsheet_id": "x"}}):
        response = alpha.put(SETTINGS, json={"expected_version": None, "settings": settings})
        assert response.status_code == 422 and not history(world)


# ---- versions and validation ------------------------------------------------------------------


def test_a_write_must_say_which_version_it_is_based_on(world, alpha):
    response = alpha.put(SOURCE, json={"source": "hubspot"})
    assert response.status_code == 422 and not history(world)


def test_a_stale_version_is_refused_and_nothing_is_written(world, alpha):
    choose(alpha, "hubspot")
    response = choose(alpha, "google_sheets", None)
    assert response.status_code == 409 and len(history(world)) == 1
    assert alpha.get(SOURCE).json()["source"] == "hubspot"


def test_two_writes_on_the_same_version_cannot_both_win(world, alpha):
    assert choose(alpha, "hubspot", None).status_code == 200
    assert choose(alpha, "google_sheets", None).status_code == 409
    assert len(history(world)) == 1


def test_what_the_admin_published_in_between_is_kept_not_overwritten(world, alpha):
    choose(alpha, "hubspot")
    current = world.config_store.current("alpha")
    document = {**dict(current.document), "llm": {"provider": "openai", "model": "admin-model"}}
    world.config_store.publish("alpha", document, author="cli", expected_parent=current.version)
    assert choose(alpha, "google_sheets", 1).status_code == 409  # stale: the Admin moved it
    assert choose(alpha, "google_sheets", 2).status_code == 200
    assert written_document(world)["llm"]["model"] == "admin-model"


def test_a_source_that_would_make_the_settings_invalid_is_never_published(world, alpha):
    # settings the Brain rejects, published behind the API's back (an operator's command)
    from test_client_agent_api import _wiring_and_base_document

    _raw, base = _wiring_and_base_document(world.application, "alpha")
    broken = {**base, "brain": {**base["brain"], "settings": {"business": {"name": "x"}}}}
    world.config_store.publish("alpha", broken, author="cli", expected_parent=None)
    response = choose(alpha, "hubspot", 1)
    assert response.status_code == 422 and len(history(world)) == 1


def test_a_failing_settings_store_answers_503(world, alpha, monkeypatch):
    from sales_app.config_store import ConfigStoreError

    def broken(client_id):
        raise ConfigStoreError("down")

    monkeypatch.setattr(world.application.config_store, "current", broken)
    assert alpha.get(SOURCE).status_code == 503
    assert choose(alpha, "hubspot").status_code == 503


# ---- the running agent ------------------------------------------------------------------------


def test_choosing_another_source_pauses_a_running_agent(world, alpha):
    assert alpha.post(START).status_code == 200
    assert choose(alpha, "hubspot").status_code == 200
    state = world.agent_state.get("alpha")
    assert state.running is False and state.changed_by == "client:alpha"
    assert alpha.get(AGENT).json()["state"] == "paused"


def test_a_change_that_keeps_the_source_leaves_the_agent_running(world, alpha):
    alpha.post(START)
    response = choose(alpha, "google_sheets", google_sheets={"prospects_tab": "Leads"})
    assert response.status_code == 200 and world.agent_state.get("alpha").running is True


def test_a_switch_that_cannot_be_paused_changes_nothing(world, alpha, monkeypatch):
    alpha.post(START)

    def down(client_id):
        raise AgentStateStoreUnavailableError("down")

    monkeypatch.setattr(world.application.agent_state, "get", down)
    response = choose(alpha, "hubspot")
    assert response.status_code == 503 and not history(world)


def test_a_running_client_that_chose_hubspot_stops_and_is_never_run_on_sheets(world, alpha):
    choose(alpha, "hubspot")
    config = world.application.registry.get_config("alpha")
    assert config.spec.source_apart_from_store is True  # the new choice is in force...
    assert config.missing_credentials  # ...and without its token the client takes no work
    assert world.application.registry.get("alpha").suspended
    save_token(alpha)  # this deployment's test builders cannot build a HubSpot source
    status = world.application.runtime_manager.status("alpha")
    assert any("cannot be run yet" in e for e in status.errors)
    assert world.application.registry.get("alpha").suspended


# ---- the token ----------------------------------------------------------------------------------


def test_a_token_needs_hubspot_to_be_the_chosen_source(world, alpha):
    response = save_token(alpha)
    assert response.status_code == 409 and stored_token(world) is None
    assert alpha.delete(CREDENTIALS).status_code == 409


def test_a_token_is_stored_in_the_clients_own_scope_and_never_returned(world, alpha):
    choose(alpha, "hubspot")
    response = save_token(alpha)
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True and body["label"] == "HubSpot access token"
    assert set(body) == {"label", "configured", "updated_at"}
    assert TOKEN not in response.text
    assert stored_token(world) == TOKEN
    entries = world.application.credentials.describe(client_scope("alpha"))
    assert {e.key for e in entries if e.configured} == {TOKEN_KEY}  # nothing else was written
    for view in (alpha.get(SOURCE), alpha.get(READINESS), alpha.get(AGENT)):
        assert TOKEN not in view.text
    assert alpha.get(SOURCE).json()["credential"]["configured"] is True


def test_the_admin_sees_that_a_token_is_set_and_never_its_value(world, alpha):
    choose(alpha, "hubspot")
    save_token(alpha)
    admin = world.admin()
    entries = admin.get("/admin/v1/clients/alpha/integrations").json()
    (entry,) = [e for e in entries if e["key"] == TOKEN_KEY]
    assert entry["configured"] is True and entry["value"] is None
    assert TOKEN not in json.dumps(entries)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "pat-eu1-short",
        "EAAB-a-whatsapp-looking-token",
        "pat-EU1-12345678-1234-1234-1234-123456789abc",
        "pat-eu1-12345678-1234-1234-1234-123456789abc\nextra",
        "x" * 5000,
    ],
)
def test_a_value_that_is_not_a_hubspot_token_is_refused_with_the_rule_not_the_value(
    world, alpha, bad
):
    choose(alpha, "hubspot")
    response = save_token(alpha, bad)
    assert response.status_code == 422 and stored_token(world) is None
    if bad.strip():
        assert bad not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"token": TOKEN},
        {"value": TOKEN},
        {"access_token": TOKEN, "extra": 1},
        {"access_token": 12345},
        {"access_token": None},
        {"access_token": {"nested": TOKEN}},
        [TOKEN],
        TOKEN,
        {},
    ],
)
def test_a_body_that_is_not_exactly_the_expected_one_is_refused_without_echo(world, alpha, body):
    choose(alpha, "hubspot")
    response = alpha.put(CREDENTIALS, json=body)
    assert response.status_code == 422 and stored_token(world) is None
    assert TOKEN not in response.text and "12345" not in response.text


def test_a_body_that_is_not_json_is_refused(world, alpha):
    choose(alpha, "hubspot")
    response = alpha.put(CREDENTIALS, content=f"access_token={TOKEN}")
    assert response.status_code == 400 and TOKEN not in response.text


def test_the_same_token_cannot_serve_two_clients_and_nobody_is_named(world, alpha, beta):
    choose(alpha, "hubspot")
    choose(beta, "hubspot")
    assert save_token(alpha).status_code == 200
    response = save_token(beta)
    assert response.status_code == 409 and stored_token(world, "beta") is None
    text = response.text.lower()
    assert "alpha" not in text and TOKEN not in response.text
    assert save_token(beta, OTHER_TOKEN).status_code == 200


def test_a_token_can_be_replaced_and_removed(world, alpha):
    choose(alpha, "hubspot")
    save_token(alpha)
    save_token(alpha, OTHER_TOKEN)
    assert stored_token(world) == OTHER_TOKEN
    removed = alpha.delete(CREDENTIALS)
    assert removed.status_code == 200 and removed.json()["configured"] is False
    assert stored_token(world) is None
    assert alpha.delete(CREDENTIALS).json()["configured"] is False  # removing again is harmless
    assert alpha.get(SOURCE).json()["credential"]["configured"] is False


def test_a_client_cannot_store_anything_but_its_source_token(world, alpha):
    choose(alpha, "hubspot")
    for url in (
        f"{CREDENTIALS}/whatsapp.access_token",
        f"{CREDENTIALS}/{TOKEN_KEY}",
        "/client/v1/integrations/whatsapp.access_token",
        "/client/v1/credentials",
    ):
        for method in ("put", "post", "delete"):
            response = alpha.request(method.upper(), url, json={"access_token": "EAAB-x"})
            assert response.status_code in (404, 405)
    assert world.application.credentials.describe(client_scope("alpha"))
    assert {
        e.key for e in world.application.credentials.describe(client_scope("alpha")) if e.configured
    } == set()


def test_the_credential_of_google_sheets_is_the_platforms_so_a_client_gives_none(world, alpha):
    assert alpha.get(SOURCE).json()["credential"] is None
    assert save_token(alpha).status_code == 409


def test_unavailable_credential_storage_is_a_fixed_503(tmp_path, monkeypatch):
    monkeypatch.setattr(client_source, "TEST_MIN_INTERVAL_SECONDS", 0.0)
    # no master key: reads find nothing, writes are refused (and the audit stays in memory)
    plain = World(tmp_path, secret_audit=InMemorySecretAudit())
    try:
        client = plain.browser("alpha")
        choose(client, "hubspot")
        response = save_token(client)
        assert response.status_code == 503 and TOKEN not in response.text
        assert "SALES_AGENT_SECRETS_KEY" not in response.text  # nothing of the deployment
    finally:
        plain.application.close()


# ---- the connection test -----------------------------------------------------------------------


def test_google_sheets_is_tested_with_the_platforms_access_and_the_clients_spreadsheet(
    world, alpha
):
    body = alpha.post(TEST).json()
    assert body["ok"] is True and body["source"] == "google_sheets"
    assert world.net.sheets_calls == ["sheet-alpha"] and world.net.hubspot_requests == []
    assert world.net.probe_files == ["creds.json"]


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (403, "not shared with the platform's address"),
        (404, "Spreadsheet not found"),
        (401, "refused the platform's access"),
        (429, "rate limit"),
        (500, "answered with an error"),
    ],
)
def test_a_refusal_by_google_is_a_failure_in_a_fixed_sentence(world, alpha, status, words):
    world.net.sheets_error = SheetsStatus(status)
    response = alpha.post(TEST)
    body = response.json()
    assert response.status_code == 200 and body["ok"] is False and words in body["message"]
    assert "/srv/secret.json" not in response.text


def test_a_google_timeout_and_an_unusable_platform_file_are_failures(world, alpha):
    world.net.sheets_error = TimeoutError("/srv/secret.json")
    timeout = alpha.post(TEST).json()
    assert timeout["ok"] is False and "did not answer in time" in timeout["message"]
    world.net.sheets_error = None
    world.net.probe_error = ValueError("cannot read /srv/secret.json")
    unusable = alpha.post(TEST).json()
    assert unusable["ok"] is False and "contact your administrator" in unusable["message"]
    assert "/srv/secret.json" not in json.dumps([timeout, unusable])


def test_a_missing_prospects_tab_is_a_failure(world, alpha):
    world.net.titles = ("Other",)
    body = alpha.post(TEST).json()
    assert body["ok"] is False and body["details"] == {"missing_tab": "Prospects"}


def test_hubspot_is_tested_with_the_clients_own_token(world, alpha):
    choose(alpha, "hubspot")
    save_token(alpha)
    response = alpha.post(TEST)
    body = response.json()
    assert body["ok"] is True and body["source"] == "hubspot"
    (request,) = world.net.hubspot_requests
    assert request.method == "GET" and request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in response.text and world.net.sheets_calls == []


def test_hubspot_without_a_token_sends_nothing(world, alpha):
    choose(alpha, "hubspot")
    body = alpha.post(TEST).json()
    assert body["ok"] is False and "not configured" in body["message"]
    assert world.net.hubspot_requests == []


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (401, "refused this access token"),
        (403, "may not read contacts"),
        (404, "answered 404"),
        (429, "rate limit"),
        (500, "answered with an error"),
    ],
)
def test_a_refusal_by_hubspot_is_a_failure_in_a_fixed_sentence(world, alpha, status, words):
    choose(alpha, "hubspot")
    save_token(alpha)
    world.net.hubspot = lambda request: httpx.Response(
        status, json={"message": f"token {TOKEN} is wrong", "category": "INVALID_AUTHENTICATION"}
    )
    response = alpha.post(TEST)
    body = response.json()
    assert response.status_code == 200 and body["ok"] is False and words in body["message"]
    assert TOKEN not in response.text


def test_hubspot_timeouts_and_unexpected_answers_are_failures(world, alpha):
    choose(alpha, "hubspot")
    save_token(alpha)

    def slow(request):
        raise httpx.ReadTimeout(f"timeout {TOKEN}")

    world.net.hubspot = slow
    timeout = alpha.post(TEST)
    assert timeout.json()["ok"] is False and "did not answer in time" in timeout.json()["message"]
    world.net.hubspot = lambda request: httpx.Response(200, text="<html>nope</html>")
    odd = alpha.post(TEST)
    assert odd.json()["ok"] is False and "not with a contact list" in odd.json()["message"]
    assert TOKEN not in timeout.text + odd.text


def test_a_success_proves_the_access_and_changes_nothing_else(world, alpha):
    choose(alpha, "hubspot")
    save_token(alpha)
    versions = len(history(world))
    assert alpha.post(TEST).json()["ok"] is True
    assert len(history(world)) == versions and world.agent_state.get("alpha") is None


def test_tests_are_spaced_so_a_client_cannot_hammer_an_outside_service(world, alpha, monkeypatch):
    monkeypatch.setattr(client_source, "TEST_MIN_INTERVAL_SECONDS", 60.0)
    assert alpha.post(TEST).status_code == 200
    assert alpha.post(TEST).status_code == 429
    assert len(world.net.sheets_calls) == 1


def test_the_token_never_reaches_the_logs(world, alpha, caplog):
    caplog.set_level(logging.DEBUG)
    choose(alpha, "hubspot")
    save_token(alpha)
    world.net.hubspot = lambda request: httpx.Response(401, json={"message": TOKEN})
    alpha.post(TEST)
    save_token(alpha, "pat-bad")
    alpha.delete(CREDENTIALS)
    assert TOKEN not in caplog.text
    assert world.audit.recent()  # the saves and the removal are recorded...
    assert TOKEN not in json.dumps([asdict(e) for e in world.audit.recent()], default=str)


# ---- readiness: HubSpot runs only if the runtime could build it ------------------------------------


def test_google_sheets_clients_stay_ready(world, alpha):
    body = alpha.get(READINESS).json()
    assert body["ready"] is True and "source_connected" in check_ids(body, True)


def test_hubspot_does_not_start_while_the_runtime_could_not_build_it(world, alpha):
    choose(alpha, "hubspot")
    save_token(alpha)
    assert alpha.post(TEST).json()["ok"] is True
    body = alpha.get(READINESS).json()
    assert body["ready"] is False and check_ids(body, False) == {"platform_connections"}
    assert TOKEN not in json.dumps(body)
    start = alpha.post(START)
    assert start.status_code == 409
    assert [r["id"] for r in start.json()["detail"]["reasons"]] == ["platform_connections"]
    agent = alpha.get(AGENT).json()
    assert agent["state"] == "not_started" and agent["active"] is False
    assert world.agent_state.get("alpha") is None


def test_going_back_to_google_sheets_makes_the_agent_startable_again(world, alpha):
    choose(alpha, "hubspot")
    assert alpha.post(START).status_code == 409
    choose(alpha, "google_sheets", 1)
    assert alpha.get(READINESS).json()["ready"] is True
    assert alpha.post(START).status_code == 200


def test_a_source_the_registry_does_not_know_never_passes(world, alpha):
    # published behind the API's back: a source name that is no provider
    from test_client_agent_api import _wiring_and_base_document

    _raw, base = _wiring_and_base_document(world.application, "alpha")
    world.config_store.publish(
        "alpha", {**base, "source": {"provider": "salesforce"}}, author="cli", expected_parent=None
    )
    body = alpha.get(READINESS).json()
    assert check_ids(body, False) == {"source_connected"}
    assert alpha.post(START).status_code == 409
    assert alpha.get(SOURCE).json()["runnable"] is False


def test_an_admin_disabling_the_client_still_works_with_a_hubspot_source(world, alpha):
    choose(alpha, "hubspot")
    set_status(world.admin(), "alpha", "inactive")
    assert world.application.agent_may_work("alpha") is False


def test_the_document_a_client_publishes_is_what_the_schema_validates(world, alpha):
    choose(alpha, "hubspot")
    raw = yaml.safe_load((world.tmp_path / "alpha" / "client.yaml").read_text("utf-8"))
    assert "source" not in raw  # the operator's wiring is never rewritten by a client
    assert ClientFile.model_validate(
        {**raw, "source": {"provider": "hubspot"}}
    ).source_provider == ("hubspot")


# ---- the last test counts only for what it tested ---------------------------------------------


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """Like `world`, but no client has tested its source yet."""
    monkeypatch.setattr(client_source, "TEST_MIN_INTERVAL_SECONDS", 0.0)
    net = Net()
    audit = InMemorySecretAudit()
    w = World(
        tmp_path,
        secret_store=InMemorySecretStore(),
        secret_audit=audit,
        source_tested=False,
    )
    w.net, w.audit = net, audit
    w.app_kwargs = {"connection_http": net.http, "sheets_probe": net.probe}
    yield w
    w.application.close()


def not_ready_because(client):
    body = client.get(READINESS).json()
    (message,) = [c["message"] for c in body["checks"] if c["id"] == "source_connected"] or [None]
    return body["ready"], check_ids(body, False), message


def test_a_source_never_tested_does_not_let_the_agent_start(fresh):
    alpha = fresh.browser("alpha")
    ready, failing, message = not_ready_because(alpha)
    assert (ready, failing) == (False, {"source_connected"})
    assert "Test the connection to Google Sheets" in message
    assert alpha.get(SOURCE).json()["last_test"] is None
    start = alpha.post(START)
    assert start.status_code == 409
    assert [r["id"] for r in start.json()["detail"]["reasons"]] == ["source_connected"]
    assert fresh.agent_state.get("alpha") is None


def test_a_successful_test_makes_the_source_connected_and_the_agent_startable(fresh):
    alpha = fresh.browser("alpha")
    tested = alpha.post(TEST).json()
    assert tested["ok"] is True and tested["checked_at"]
    assert alpha.get(READINESS).json()["ready"] is True
    last = alpha.get(SOURCE).json()["last_test"]
    assert (last["ok"], last["current"]) == (True, True) and last["checked_at"] == tested[
        "checked_at"
    ]
    assert alpha.post(START).status_code == 200


def test_a_failed_test_is_kept_and_never_counts_as_connected(fresh):
    alpha = fresh.browser("alpha")
    fresh.net.sheets_error = SheetsStatus(403)
    assert alpha.post(TEST).json()["ok"] is False
    ready, failing, message = not_ready_because(alpha)
    assert (ready, failing) == (False, {"source_connected"})
    assert "last connection test of Google Sheets failed" in message
    assert alpha.get(SOURCE).json()["last_test"]["ok"] is False
    assert alpha.post(START).status_code == 409
    fresh.net.sheets_error = None  # the client shared the spreadsheet, and tests again
    assert alpha.post(TEST).json()["ok"] is True
    assert alpha.get(READINESS).json()["ready"] is True


def test_changing_the_tab_or_the_spreadsheet_cancels_the_result(fresh):
    alpha = fresh.browser("alpha")
    alpha.post(TEST)
    assert alpha.get(READINESS).json()["ready"] is True
    choose(alpha, "google_sheets", google_sheets={"prospects_tab": "Leads"})
    ready, failing, message = not_ready_because(alpha)
    assert (ready, failing) == (False, {"source_connected"})
    assert "since it was last changed" in message
    assert alpha.get(SOURCE).json()["last_test"]["current"] is False
    assert alpha.post(START).status_code == 409
    fresh.net.titles = ("Leads",)
    assert alpha.post(TEST).json()["ok"] is True
    assert alpha.post(START).status_code == 200
    choose(alpha, "google_sheets", 1, google_sheets={"spreadsheet": SHEET_ID})
    assert alpha.get(READINESS).json()["ready"] is False  # another spreadsheet: tested for none


def test_going_back_to_what_was_tested_makes_that_result_count_again(fresh):
    alpha = fresh.browser("alpha")
    alpha.post(TEST)
    choose(alpha, "google_sheets", google_sheets={"prospects_tab": "Leads"})
    assert alpha.get(READINESS).json()["ready"] is False
    choose(alpha, "google_sheets", 1, google_sheets={"prospects_tab": "Prospects"})
    assert alpha.get(READINESS).json()["ready"] is True  # the very configuration it was made for


def test_one_clients_test_never_counts_for_another(fresh):
    alpha, beta = fresh.browser("alpha"), fresh.browser("beta")
    alpha.post(TEST)
    assert alpha.get(READINESS).json()["ready"] is True
    assert beta.get(READINESS).json()["ready"] is False
    assert beta.get(SOURCE).json()["last_test"] is None


def test_the_fingerprint_says_nothing_of_the_spreadsheet_or_the_token(fresh):
    alpha = fresh.browser("alpha")
    choose(alpha, "hubspot")
    save_token(alpha)
    alpha.post(TEST)
    kept = fresh.application.source_checks.get("alpha")
    assert len(kept.fingerprint) == 64 and set(kept.fingerprint) <= set("0123456789abcdef")
    assert TOKEN not in kept.fingerprint and "sheet-alpha" not in kept.fingerprint


def test_replacing_the_token_cancels_the_test_made_with_the_old_one(fresh):
    alpha = fresh.browser("alpha")
    choose(alpha, "hubspot")
    save_token(alpha)
    assert alpha.post(TEST).json()["ok"] is True
    assert alpha.get(SOURCE).json()["last_test"]["current"] is True
    save_token(alpha, OTHER_TOKEN)
    assert alpha.get(SOURCE).json()["last_test"]["current"] is False
    alpha.delete(CREDENTIALS)
    assert alpha.get(SOURCE).json()["last_test"]["current"] is False


def test_hubspot_with_a_current_successful_test_still_needs_a_runtime_that_built(fresh):
    alpha = fresh.browser("alpha")
    choose(alpha, "hubspot")
    save_token(alpha)
    assert alpha.post(TEST).json()["ok"] is True
    assert alpha.get(SOURCE).json()["last_test"] == {
        "ok": True,
        "checked_at": alpha.get(SOURCE).json()["last_test"]["checked_at"],
        "current": True,
    }
    ready, failing, message = not_ready_because(alpha)
    assert (ready, failing) == (False, {"platform_connections"})
    assert not message or TOKEN not in message  # what is missing is the built runtime
    assert alpha.post(START).status_code == 409 and fresh.agent_state.get("alpha") is None


def test_the_result_cannot_be_written_or_forged_by_the_client(fresh):
    alpha = fresh.browser("alpha")
    for body in (
        {"ok": True},
        {"last_test": {"ok": True, "current": True}},
        {"expected_version": None, "source": "google_sheets", "last_test": {"ok": True}},
    ):
        response = alpha.put(SOURCE, json=body)
        assert response.status_code == 422
    assert fresh.application.source_checks.get("alpha") is None
    assert alpha.get(READINESS).json()["ready"] is False


def test_a_store_that_cannot_be_read_never_counts_as_connected(fresh, monkeypatch):
    from sales_app.source_checks import SourceCheckStoreUnavailableError

    alpha = fresh.browser("alpha")
    alpha.post(TEST)

    def down(client_id):
        raise SourceCheckStoreUnavailableError("down")

    monkeypatch.setattr(fresh.application.source_checks, "get", down)
    ready, failing, message = not_ready_because(alpha)
    assert (ready, failing) == (
        False,
        {"source_connected"},
    ) and "temporarily unavailable" in message
    assert alpha.post(START).status_code == 409
    assert alpha.get(SOURCE).status_code == 503


def test_a_result_that_cannot_be_kept_is_not_pretended(fresh, monkeypatch):
    from sales_app.source_checks import SourceCheckStoreUnavailableError

    def down(client_id, **kwargs):
        raise SourceCheckStoreUnavailableError("down")

    monkeypatch.setattr(fresh.application.source_checks, "set", down)
    response = fresh.browser("alpha").post(TEST)
    assert response.status_code == 503 and "ok" not in response.json()
