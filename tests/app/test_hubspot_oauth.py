"""HubSpot by the client's own HubSpot authorisation (OAuth), against doubles.

Proved with `httpx.MockTransport` and fakes (no network, no real HubSpot): the HTTP exchange of the
OAuth client (consent URL, code exchange, scope check, refresh, revocation, typed errors), the journey
through the Client API, the grant kept in the client's own scope and nowhere else, one client never
seeing or using another's, the credentials resolved from the grant, and a HubSpot client whose access
token is renewed at each request.
Not proved: that HubSpot accepts the application (it needs the Admin's HubSpot developer account).
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_google_oauth_flow import REFRESH, FakeOAuth, state_of

from sales_app import hubspot_wiring
from sales_app.api.client import COOKIE_NAME
from sales_app.api.client_hubspot_oauth import create_client_hubspot_oauth_router
from sales_app.config.loader import _grant_state
from sales_app.config.secrets import (
    HUBSPOT_OAUTH_CLIENT_ID,
    HUBSPOT_OAUTH_CLIENT_SECRET,
    HUBSPOT_OAUTH_STATE_KEY,
    HUBSPOT_REAUTH_REQUIRED,
    HUBSPOT_REFRESH_TOKEN,
    SOURCE_GRANTS,
)
from sales_app.hubspot_oauth import CONTACTS_READ, SCHEMA_READ, HubSpotOAuthClient
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore, client_scope
from sales_integrations.fakes import FakeClock
from sales_integrations.google_oauth import (
    GoogleAccessDeniedError,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
    TokenGrant,
)
from sales_integrations.hubspot import HubSpotAuthError, HubSpotClient

BASE = "/client/v1/hubspot-access"
NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
ACCESS = "CJ-access-SECRET"
GRANTED = f"{CONTACTS_READ} crm.objects.contacts.write {SCHEMA_READ} oauth"
ACCOUNT = "ada@naga.example (portal 4242)"


# ---- the HTTP client --------------------------------------------------------------------------


def oauth_client(handler, **kwargs):
    return HubSpotOAuthClient(
        client_id="app-id-0123",
        client_secret="app-secret-0123",
        redirect_uri="https://revansa.example/client/v1/hubspot-access/callback",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def hubspot_server(*, scopes=None, token_status=200, token_body=None, info_status=200):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/oauth/v1/token":
            body = token_body or {
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "expires_in": 1800,
            }
            return httpx.Response(token_status, json=body)
        if path.startswith("/oauth/v1/access-tokens/"):
            return httpx.Response(
                info_status,
                json={
                    "hub_id": 4242,
                    "user": "Ada@Naga.example",
                    "scopes": GRANTED.split() if scopes is None else scopes,
                },
            )
        if request.method == "DELETE" and path.startswith("/oauth/v1/refresh-tokens/"):
            return httpx.Response(204)
        return httpx.Response(404)

    return handler, calls


def test_the_consent_url_names_the_app_the_scopes_and_the_state_but_no_secret():
    url = oauth_client(lambda r: httpx.Response(500)).authorization_url(
        state="STATE", code_challenge="cc"
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "app.hubspot.com" and parts.path == "/oauth/authorize"
    assert query["client_id"] == ["app-id-0123"] and query["state"] == ["STATE"]
    assert set(query["scope"][0].split()) == {CONTACTS_READ, "crm.objects.contacts.write", SCHEMA_READ}
    assert "app-secret-0123" not in url


def test_the_code_exchange_reads_the_granted_scopes_and_the_account():
    handler, calls = hubspot_server()
    client = oauth_client(handler)
    grant = client.exchange_code(code="the-code", code_verifier="unused")
    assert grant.refresh_token == REFRESH and grant.access_token == ACCESS
    assert set(grant.scope.split()) == set(GRANTED.split())
    form = parse_qs(calls[0].content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
    assert form["client_secret"] == ["app-secret-0123"]
    assert client.account_address(ACCESS) == ACCOUNT


def test_refresh_gives_a_new_access_token():
    handler, calls = hubspot_server(
        token_body={"access_token": "fresh", "refresh_token": REFRESH, "expires_in": 1800}
    )
    grant = oauth_client(handler).refresh(REFRESH)
    assert grant.access_token == "fresh" and grant.expires_in == 1800
    assert parse_qs(calls[0].content.decode())["grant_type"] == ["refresh_token"]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"status": "BAD_REFRESH_TOKEN", "message": "x"}, GoogleReauthRequiredError),
        (400, {"error": "invalid_grant"}, GoogleReauthRequiredError),
        (401, {"message": "no"}, GoogleAccessDeniedError),
        (400, {"message": "other"}, GoogleProtocolError),
        (429, {}, GoogleTransientError),
        (503, {}, GoogleTransientError),
    ],
)
def test_failures_are_typed_and_never_carry_what_hubspot_said(status, body, error):
    handler, _ = hubspot_server(token_status=status, token_body=body)
    with pytest.raises(error) as raised:
        oauth_client(handler).refresh(REFRESH)
    assert REFRESH not in str(raised.value) and "message" not in str(raised.value)


def test_a_network_failure_is_transient():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(GoogleTransientError):
        oauth_client(handler).refresh(REFRESH)


def test_revocation_deletes_the_refresh_token_and_forgives_an_unknown_one():
    handler, calls = hubspot_server()
    oauth_client(handler).revoke(REFRESH)
    assert calls[-1].method == "DELETE" and calls[-1].url.path.endswith(REFRESH)
    oauth_client(lambda r: httpx.Response(404)).revoke(REFRESH)


def test_the_reprs_show_nothing():
    assert "secret" not in repr(oauth_client(lambda r: httpx.Response(500))).lower()


# ---- the HubSpot client renews its token --------------------------------------------------------


def test_the_hubspot_client_asks_for_a_fresh_token_at_each_request():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"results": []})

    tokens = iter(["one", "two"])
    client = HubSpotClient(lambda: next(tokens), http=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    client.verify()
    client.verify()
    assert seen == ["Bearer one", "Bearer two"]


def test_a_token_source_that_fails_is_an_auth_error_not_a_crash():
    client = HubSpotClient(lambda: "", http=lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
    with pytest.raises(HubSpotAuthError):
        client.verify()


def test_a_fixed_token_still_works_and_an_empty_one_is_refused():
    with pytest.raises(ValueError):
        HubSpotClient("  ")
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"results": []})

    HubSpotClient(" pat-x ", http=lambda: httpx.Client(transport=httpx.MockTransport(handler))).verify()
    assert seen == ["Bearer pat-x"]


# ---- the Client API -----------------------------------------------------------------------------


class FakeHubSpotOAuth(FakeOAuth):
    scope = GRANTED

    def exchange_code(self, *, code, code_verifier):
        return TokenGrant(ACCESS, self.refresh_token_returned, 1800, self.scope)

    def account_address(self, access_token):
        return ACCOUNT


class Auth:
    def authenticate(self, token):
        return SimpleNamespace(client_id=token) if token in ("alpha", "beta") else None


def platform(store, *, configured=True):
    if configured:
        store.set(GLOBAL_SCOPE, HUBSPOT_OAUTH_CLIENT_ID, "app-id-0123", by="test")
        store.set(GLOBAL_SCOPE, HUBSPOT_OAUTH_CLIENT_SECRET, "app-secret-0123", by="test")
        store.set(GLOBAL_SCOPE, HUBSPOT_OAUTH_STATE_KEY, "k" * 32, by="test")


def make(*, store=None, configured=True, scope=GRANTED):
    store = store if store is not None else InMemorySecretStore()
    platform(store, configured=configured)
    oauth = FakeHubSpotOAuth()
    oauth.scope = scope
    service = hubspot_wiring.HubSpotAccessService(
        secrets=store,
        clock=FakeClock(NOW),
        public_base_url=lambda: "https://revansa.example",
        oauth_factory=lambda **_: oauth,
    )
    refreshed = []
    application = SimpleNamespace(
        hubspot_access=service,
        client_auth=Auth(),
        registry=SimpleNamespace(get_config=lambda c: object()),
        runtime_manager=SimpleNamespace(refresh_client=refreshed.append),
    )
    app = FastAPI()
    app.include_router(create_client_hubspot_oauth_router(application))
    return TestClient(app, follow_redirects=False), service, oauth, store, refreshed


def login(client, who="alpha"):
    client.cookies.set(COOKIE_NAME, who)
    return client


def test_requires_a_session():
    client, *_ = make()
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/start").status_code == 401


def test_journey_connect_status_disconnect():
    client, _service, oauth, store, refreshed = make()
    login(client)
    assert client.get(BASE).json()["state"] == "not_connected"
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    r = client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert r.status_code == 302
    assert r.headers["location"] == "/client/connections/sources?hubspot=connected"
    assert refreshed == ["alpha"]
    body = client.get(BASE).json()
    assert body["state"] == "connected" and body["account"] == ACCOUNT
    assert REFRESH not in client.get(BASE).text
    assert client.post(f"{BASE}/disconnect").json()["state"] == "not_connected"
    assert oauth.revoked == [REFRESH]
    assert store.get(client_scope("alpha"), HUBSPOT_REFRESH_TOKEN) is None


def test_the_grant_lives_in_the_clients_scope_only():
    client, service, _oauth, store, _ = make()
    url = login(client).post(f"{BASE}/start").json()["authorize_url"]
    client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert store.get(client_scope("alpha"), HUBSPOT_REFRESH_TOKEN) == REFRESH
    assert store.get(GLOBAL_SCOPE, HUBSPOT_REFRESH_TOKEN) is None
    assert store.get(client_scope("beta"), HUBSPOT_REFRESH_TOKEN) is None
    assert login(client, "beta").get(BASE).json()["state"] == "not_connected"
    assert service.grants.load("beta") is None
    # ... and never in the Google keys.
    assert store.get(client_scope("alpha"), "sheets.refresh_token") is None
    assert store.get(client_scope("alpha"), "gmail.refresh_token") is None


def test_a_state_made_for_another_client_is_refused():
    client, *_ = make()
    url = login(client, "alpha").post(f"{BASE}/start").json()["authorize_url"]
    r = login(client, "beta").get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert r.headers["location"].endswith("hubspot=invalid")


@pytest.mark.parametrize(
    ("params", "scope", "expected"),
    [
        ({"error": "access_denied"}, GRANTED, "denied"),
        ({"code": "c"}, "oauth", "scope"),  # the person withheld the contacts permission
        ({}, GRANTED, "error"),
    ],
)
def test_callback_outcomes_are_redirects_only(params, scope, expected):
    client, _service, _oauth, store, _ = make(scope=scope)
    login(client)
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    r = client.get(f"{BASE}/callback", params={"state": state_of(url), **params})
    assert r.status_code == 302
    assert r.headers["location"].endswith(f"hubspot={expected}")
    assert store.get(client_scope("alpha"), HUBSPOT_REFRESH_TOKEN) is None


def test_not_configured_by_the_admin_is_unavailable():
    client, service, *_ = make(configured=False)
    login(client)
    assert client.get(BASE).json()["state"] == "unavailable"
    assert client.post(f"{BASE}/start").status_code == 503
    assert "HubSpot app client id" in service.missing()


def test_the_resolver_sees_a_usable_grant_or_one_to_renew():
    store = InMemorySecretStore()
    client = client_scope("alpha")
    refresh_key, reauth_key, flag = SOURCE_GRANTS["hubspot"]
    assert _grant_state(store, client, refresh_key, reauth_key) is None
    store.set(client, HUBSPOT_REFRESH_TOKEN, REFRESH, by="test")
    assert _grant_state(store, client, refresh_key, reauth_key) == "usable"
    store.set(client, HUBSPOT_REAUTH_REQUIRED, "1", by="test")
    assert _grant_state(store, client, refresh_key, reauth_key) == "reauth"
    assert _grant_state(store, client_scope("beta"), refresh_key, reauth_key) is None
    assert flag == "hubspot.oauth_grant"


def test_the_token_source_renews_per_client_and_marks_a_revoked_grant():
    _client, service, oauth, store, _ = make()
    service.grants.save("alpha", REFRESH, ACCOUNT)
    first = service.token_source("alpha").access_token()
    assert first == service.token_source("alpha").access_token()  # cached, not renewed twice
    assert oauth.refresh_calls == 1
    with pytest.raises(GoogleReauthRequiredError):
        service.token_source("beta").access_token()  # beta never connected
    oauth.refresh_error = GoogleReauthRequiredError("gone")
    service.grants.save("alpha", "other-refresh", ACCOUNT)
    with pytest.raises(GoogleReauthRequiredError):
        service.token_source("alpha").access_token()
    assert store.get(client_scope("alpha"), HUBSPOT_REAUTH_REQUIRED) == "1"
