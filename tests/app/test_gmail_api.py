"""`/client/v1/channel/google/*` against fakes: the client types nothing, no secret in any answer or
URL, redirects only, refusals, disconnect, and send_ready only with the cap configured."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_google_oauth_flow import REFRESH, build, state_of

from sales_app.api.client import COOKIE_NAME
from sales_app.api.client_gmail import GmailContext, create_client_gmail_router
from sales_integrations.google_oauth import TokenGrant

BASE = "/client/v1/channel/google"


class Auth:
    def authenticate(self, token):
        return SimpleNamespace(client_id=token) if token in ("alpha", "beta") else None


def make(
    *, caps=None, refusal=None, configured=True, scope="https://mail.google.com/ openid email"
):
    service, oauth, grants, *_ = build()
    oauth.exchange_code = lambda *, code, code_verifier: TokenGrant("ya29.x", REFRESH, 3600, scope)
    changed = []
    ctx = GmailContext(
        google_configured=lambda: configured,
        start_refusal=lambda c: refusal,
        send_cap_configured=lambda c: (caps or {}).get(c, False),
        changed=changed.append,
    )
    app = FastAPI()
    app.include_router(create_client_gmail_router(Auth(), service, ctx))
    client = TestClient(app, follow_redirects=False)
    return client, service, oauth, changed


def login(client, who="alpha"):
    client.cookies.set(COOKIE_NAME, who)
    return client


def test_requires_a_session():
    client, *_ = make()
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/start").status_code == 401
    assert client.post(f"{BASE}/disconnect").status_code == 401


def test_journey_connect_status_disconnect():
    client, service, oauth, changed = make(caps={"alpha": True})
    login(client)
    assert client.get(BASE).json()["state"] == "not_connected"
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    assert "password" not in url
    r = client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    assert r.status_code == 302 and r.headers["location"] == "/client/channel?google=connected"
    assert "c=" not in r.headers["location"] and "gmail" not in r.headers["location"]
    body = client.get(BASE).json()
    assert body["state"] == "connected" and body["send_ready"] is True and changed == ["alpha"]
    assert REFRESH not in client.get(BASE).text
    done = client.post(f"{BASE}/disconnect").json()
    assert done["state"] == "not_connected" and done["send_ready"] is False
    assert oauth.revoked == [REFRESH]


def test_connected_without_cap_is_not_send_ready():
    client, *_ = make(caps={})
    login(client)
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    body = client.get(BASE).json()
    assert body["state"] == "connected" and body["send_ready"] is False
    assert body["send_cap_configured"] is False


def test_other_client_sees_nothing():
    client, *_ = make(caps={"alpha": True, "beta": True})
    login(client, "alpha")
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    client.get(f"{BASE}/callback", params={"state": state_of(url), "code": "c"})
    login(client, "beta")
    assert client.get(BASE).json()["state"] == "not_connected"
    # alpha's state replayed by beta is refused
    url2 = login(client, "alpha").post(f"{BASE}/start").json()["authorize_url"]
    login(client, "beta")
    r = client.get(f"{BASE}/callback", params={"state": state_of(url2), "code": "c"})
    assert r.headers["location"] == "/client/channel?google=invalid"


@pytest.mark.parametrize(
    ("params", "scope", "expected"),
    [
        ({"error": "access_denied"}, None, "denied"),
        ({"code": "c"}, "openid email", "scope"),
        ({}, None, "error"),
        ({"state": "forged", "code": "c"}, None, "invalid"),
    ],
)
def test_callback_outcomes_are_redirects_only(params, scope, expected):
    client, *_ = make(**({"scope": scope} if scope else {}))
    login(client)
    url = client.post(f"{BASE}/start").json()["authorize_url"]
    full = {"state": state_of(url), **params}
    r = client.get(f"{BASE}/callback", params=full)
    assert r.status_code == 302
    assert r.headers["location"] == f"/client/channel?google={expected}"
    assert client.get(BASE).json()["state"] == "not_connected"


def test_callback_without_session_redirects():
    client, *_ = make()
    r = client.get(f"{BASE}/callback", params={"state": "x", "code": "c"})
    assert r.status_code == 302 and r.headers["location"].endswith("google=invalid")


def test_start_refusals_and_unavailable():
    client, *_ = make(refusal=(409, "a Revansa mailbox is already assigned to your account"))
    login(client)
    assert client.post(f"{BASE}/start").status_code == 409
    client, *_ = make(configured=False)
    login(client)
    assert client.post(f"{BASE}/start").status_code == 503
    assert client.get(BASE).json()["state"] == "unavailable"


def test_start_takes_no_input():
    client, *_ = make()
    login(client)
    r = client.post(f"{BASE}/start", json={"password": "hunter2", "token": "t"})
    assert r.status_code == 200 and "hunter2" not in r.text
