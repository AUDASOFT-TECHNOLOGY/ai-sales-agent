# ruff: noqa: F811  (fixtures imported from other test modules are used as arguments)
"""The independent mailbox as the client sees it: statuses, the login only if the Admin communicates
it, the password only on explicit request (limited, audited without the value), never another
client's, never a server. Mail servers are doubles; nothing here proves a real login."""

import pytest
from test_email_activation_requests import (  # noqa: F401 - fixtures and helpers
    ADDRESS,
    PASSWORD,
    activate,
    ask,
    beta,
    in_progress,
    mail,
    ready,
    url,
)
from test_email_channel_api import admin, alpha, world  # noqa: F401

from sales_app.config.secrets import EMAIL_PASSWORD
from sales_app.secret_store import client_scope

BOX = "/client/v1/channel/mailbox"
REVEAL = BOX + "/reveal-password"


def share(admin, cid="alpha", shared=True):
    return admin.put(url(cid, "credentials-sharing"), json={"shared": shared})


def test_statuses_follow_the_request(ready, alpha, admin):
    assert alpha.get(BOX).json()["status"] == "not_requested"
    assert ask(alpha).status_code == 201
    assert alpha.get(BOX).json()["status"] == "pending"
    assert admin.post(url("alpha", "start")).status_code == 200
    assert alpha.get(BOX).json()["status"] == "in_progress"
    assert activate(admin).status_code == 200
    body = alpha.get(BOX).json()
    assert body["status"] == "active" and body["address"] == ADDRESS


def test_a_rejection_is_told_with_its_reason(ready, alpha, admin):
    assert ask(alpha).status_code == 201
    assert admin.post(url("alpha", "reject"), json={"reason": "wrong name"}).status_code == 200
    body = alpha.get(BOX).json()
    assert body["status"] == "rejected" and body["reason"] == "wrong name"


def test_an_active_mailbox_that_lost_its_password_is_an_error(ready, alpha, admin, world):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    world.application.credentials.remove(client_scope("alpha"), EMAIL_PASSWORD, actor="test")
    assert alpha.get(BOX).json()["status"] == "error"


def test_the_login_is_kept_back_until_the_admin_communicates_it(ready, alpha, admin):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    body = alpha.get(BOX)
    assert body.headers["cache-control"].startswith("no-store")
    data = body.json()
    assert data["login"] is None and data["credentials_shared"] is False
    assert data["password_revealable"] is False
    refused = alpha.post(REVEAL, json={"confirm": True})
    assert refused.status_code == 403 and PASSWORD not in refused.text


def test_the_password_is_shown_on_explicit_request_only_and_never_in_get(ready, alpha, admin, world):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    assert share(admin).json() == {"client_id": "alpha", "shared": True}
    data = alpha.get(BOX).json()
    assert data["login"] == ADDRESS and data["password_revealable"] is True
    assert PASSWORD not in alpha.get(BOX).text
    assert alpha.post(REVEAL, json={"confirm": False}).status_code == 422
    shown = alpha.post(REVEAL, json={"confirm": True})
    assert shown.status_code == 200 and shown.json()["password"] == PASSWORD
    assert shown.headers["cache-control"].startswith("no-store")
    # recorded without the value
    events = [e for e in world.application.credentials._audit.recent(50) if e.action == "reveal"]
    assert events and all(PASSWORD not in repr(e) for e in events)


def test_reveals_are_limited(ready, alpha, admin):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    share(admin)
    codes = [alpha.post(REVEAL, json={"confirm": True}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_ending_the_mailbox_withdraws_the_sharing(ready, alpha, admin):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    share(admin)
    assert admin.post(url("alpha", "deactivate"), json={"reason": "ended"}).status_code == 200
    assert alpha.post(REVEAL, json={"confirm": True}).status_code == 403
    assert alpha.get(BOX).json()["login"] is None


def test_a_client_never_reaches_another_clients_mailbox(ready, alpha, beta, admin, world):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    share(admin)
    other = beta.get(BOX).json()
    assert other["status"] == "not_requested" and other["address"] is None
    assert beta.post(REVEAL, json={"confirm": True}).status_code == 403
    assert PASSWORD not in beta.get(BOX).text


def test_sharing_needs_an_active_mailbox_and_the_owner(ready, alpha, admin):
    assert share(admin).status_code == 409  # nothing requested
    in_progress(admin, alpha)
    assert share(admin).status_code == 409  # not active yet


def test_no_server_detail_is_ever_exposed(ready, alpha, admin):
    in_progress(admin, alpha)
    assert activate(admin).status_code == 200
    share(admin)
    text = alpha.get(BOX).text.lower()
    for word in ("smtp", "imap", "port", "host"):
        assert word not in text


@pytest.mark.parametrize("path", [BOX])
def test_it_needs_a_client_session(world, path):
    from fastapi.testclient import TestClient

    from sales_app.api.app import create_app

    anonymous = TestClient(create_app(world.application))
    assert anonymous.get(path).status_code == 401
