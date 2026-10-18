"""Google Sheets by the client's own Google authorisation: no file, no shared account.

Proved against fakes (no network, no real Google): the journey (link saved first, consent, return,
status, disconnect), the grant kept encrypted in the client's own scope of the database and nowhere
else, one client never seeing or using another's, and the credentials resolved from the grant alone.
Not proved: that Google accepts the application (it needs the Admin's Google Cloud configuration).
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent))
from test_google_oauth_flow import ADDRESS, REFRESH, FakeOAuth, state_of

from sales_app import sheets_wiring
from sales_app.api.client import COOKIE_NAME
from sales_app.api.client_sheets_oauth import create_client_sheets_oauth_router
from sales_app.config.loader import _sheets_grant_state
from sales_app.config.secrets import (
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    GMAIL_OAUTH_STATE_KEY,
    SHEETS_REAUTH_REQUIRED,
    SHEETS_REFRESH_TOKEN,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.secret_rows import SqlSecretStore
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    AesGcmCipher,
    InMemorySecretStore,
    client_scope,
    generate_master_key,
)
from sales_integrations.fakes import FakeClock
from sales_integrations.google_oauth import SHEETS_SCOPE, TokenGrant

BASE = "/client/v1/sheets-access"
NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
GRANTED = f"{SHEETS_SCOPE} openid email"


class FakeSheetsOAuth(FakeOAuth):
    scope = GRANTED

    def exchange_code(self, *, code, code_verifier):
        return TokenGrant("ya29.x", self.refresh_token_returned, 3600, self.scope)


class Auth:
    def authenticate(self, token):
        return SimpleNamespace(client_id=token) if token in ("alpha", "beta") else None


def platform(store, *, configured=True):
    if configured:
        store.set(GLOBAL_SCOPE, GMAIL_OAUTH_CLIENT_ID, "id-0123456789", by="test")
        store.set(GLOBAL_SCOPE, GMAIL_OAUTH_CLIENT_SECRET, "secret-0123456789", by="test")
        store.set(GLOBAL_SCOPE, GMAIL_OAUTH_STATE_KEY, "k" * 32, by="test")


def make(*, store=None, configured=True, saved=lambda c: True, scope=GRANTED):
    store = store if store is not None else InMemorySecretStore()
    platform(store, configured=configured)
    oauth = FakeSheetsOAuth()
    oauth.scope = scope
    service = sheets_wiring.SheetsAccessService(
        secrets=store,
        clock=FakeClock(NOW),
        public_base_url=lambda: "https://revansa.example",
        oauth_factory=lambda **_: oauth,
    )
    refreshed = []
    application = SimpleNamespace(
        sheets_access=service,
        client_auth=Auth(),
        registry=SimpleNamespace(get_config=lambda c: object()),
        runtime_manager=SimpleNamespace(refresh_client=refreshed.append),
    )
    app = FastAPI()
    app.include_router(create_client_sheets_oauth_router(application, spreadsheet_saved=saved))
    return TestClient(app, follow_redirects=False), service, oauth, store, refreshed


def login(client, who="alpha"):
    client.cookies.set(COOKIE_NAME, who)
    return client


def test_requires_a_session():
    client, *_ = make()
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/start").status_code == 401


def test_the_link_is_saved_before_the_client_can_authorise():
    client, *_ = make(saved=lambda c: False)
    login(client)
    body = client.get(BASE).json()
    assert body["state"] == "not_connected" and body["spreadsheet_saved"] is False
    assert client.post(f"{BASE}/start").status_code == 409


def test_journey_connect_status_disconnect():
    client, service, oauth, store, refreshed = make()
    login(client)
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    # Google's consent page comes back to this route, under the Sheets callback.
    assert service.connection.configured()
    r = client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert r.status_code == 302
    assert r.headers["location"] == "/client/connections/sources?google_sheets=connected"
    assert refreshed == ["alpha"]
    body = client.get(BASE).json()
    assert body["state"] == "connected" and body["address"] == ADDRESS
    assert REFRESH not in client.get(BASE).text
    assert client.post(f"{BASE}/disconnect").json()["state"] == "not_connected"
    assert oauth.revoked == [REFRESH]
    assert store.get(client_scope("alpha"), SHEETS_REFRESH_TOKEN) is None


def test_the_grant_lives_in_the_clients_scope_only():
    client, service, _oauth, store, _ = make()
    url = login(client).post(f"{BASE}/start").json()["authorize_url"]
    client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert store.get(client_scope("alpha"), SHEETS_REFRESH_TOKEN) == REFRESH
    assert store.get(GLOBAL_SCOPE, SHEETS_REFRESH_TOKEN) is None
    assert store.get(client_scope("beta"), SHEETS_REFRESH_TOKEN) is None
    assert login(client, "beta").get(BASE).json()["state"] == "not_connected"
    assert service.grants.load("beta") is None


def test_two_clients_may_use_the_same_google_account():
    client, _service, _oauth, store, _ = make()
    for who in ("alpha", "beta"):
        url = login(client, who).post(f"{BASE}/start").json()["authorize_url"]
        r = client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
        assert r.headers["location"].endswith("google_sheets=connected")
    assert store.get(client_scope("beta"), SHEETS_REFRESH_TOKEN) == REFRESH


def test_a_state_made_for_another_client_is_refused():
    client, *_ = make()
    url = login(client, "alpha").post(f"{BASE}/start").json()["authorize_url"]
    r = login(client, "beta").get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert r.headers["location"].endswith("google_sheets=invalid")


@pytest.mark.parametrize(
    ("params", "scope", "expected"),
    [
        ({"error": "access_denied"}, GRANTED, "denied"),
        ({"code": "c"}, "openid email", "scope"),
        ({}, GRANTED, "error"),
    ],
)
def test_callback_outcomes_are_redirects_only(params, scope, expected):
    client, _service, _oauth, store, _ = make(scope=scope)
    login(client)
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    r = client.get(f"{BASE}/callback", params={"state": state_of(url), **params})
    assert r.status_code == 302
    assert r.headers["location"].endswith(f"google_sheets={expected}")
    assert store.get(client_scope("alpha"), SHEETS_REFRESH_TOKEN) is None


def test_not_configured_by_the_admin_is_unavailable():
    client, *_ = make(configured=False)
    login(client)
    assert client.get(BASE).json()["state"] == "unavailable"
    assert client.post(f"{BASE}/start").status_code == 503


def test_gmail_and_sheets_do_not_share_a_grant():
    client, service, _oauth, store, _ = make()
    url = login(client).post(f"{BASE}/start").json()["authorize_url"]
    client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert store.get(client_scope("alpha"), "gmail.refresh_token") is None
    assert service.oauth is not None and sheets_wiring.CALLBACK_PATH.endswith(
        "/sheets-access/callback"
    )


def test_the_resolver_sees_a_usable_grant_or_one_to_renew():
    store = InMemorySecretStore()
    client = client_scope("alpha")
    assert _sheets_grant_state(store, client) is None
    store.set(client, SHEETS_REFRESH_TOKEN, REFRESH, by="test")
    assert _sheets_grant_state(store, client) == "usable"
    store.set(client, SHEETS_REAUTH_REQUIRED, "1", by="test")
    assert _sheets_grant_state(store, client) == "reauth"
    assert _sheets_grant_state(store, client_scope("beta")) is None
    assert _sheets_grant_state(None, client) is None


def test_the_token_is_encrypted_in_the_database_and_isolated():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    key = generate_master_key()
    store = SqlSecretStore(engine, AesGcmCipher.from_text(key))
    service = sheets_wiring.SheetsAccessService(
        secrets=store, clock=FakeClock(NOW), public_base_url=lambda: "https://revansa.example"
    )
    service.grants.save("alpha", REFRESH, ADDRESS)
    with engine.connect() as connection:
        rows = connection.execute(text("select scope, key, value from secrets")).all()
    assert rows and all(REFRESH not in row.value and ADDRESS not in row.value for row in rows)
    assert {row.scope for row in rows} == {"client:alpha"}
    assert service.grants.load("alpha").refresh_token == REFRESH
    assert service.grants.load("beta") is None
    other = SqlSecretStore(engine, AesGcmCipher.from_text(generate_master_key()))
    with pytest.raises(Exception, match="master key|could not be decrypted|does not open"):
        other.verify()


def test_the_provider_reads_with_the_clients_authorisation_not_a_file():
    from sales_app.config.secrets import SHEETS_OAUTH_GRANT, Secrets
    from sales_app.wiring.providers import google_sheets

    seen = []
    service = sheets_wiring.SheetsAccessService(
        secrets=InMemorySecretStore(),
        clock=FakeClock(NOW),
        sheets_factory=lambda source, **kw: seen.append(source) or "api",
    )
    config = SimpleNamespace(client_id="alpha", secrets=Secrets({SHEETS_OAUTH_GRANT: "1"}))
    sheets_wiring.install(service)
    try:
        assert google_sheets.api_for(config) == "api"
        assert seen == [service.token_source("alpha")]
    finally:
        sheets_wiring.install(None)
    with pytest.raises(Exception, match="not available"):
        google_sheets.api_for(config)
