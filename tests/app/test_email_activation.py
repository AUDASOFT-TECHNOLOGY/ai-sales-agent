# ruff: noqa: F811  (fixtures imported from test_admin_email_auth are used as arguments)
"""Admin → Email: switching the service on for the clients created from now on.

The Admin configures the servers once and, when both logins are proven, turns Email on for new
clients. Each client created afterwards is given Email access as its own exception; the global
channel list and the clients that already exist are never changed. No mailbox is created anywhere:
a client that may use Email and has none is listed as awaiting an assignment.

Servers are doubles (see test_admin_email_auth).
"""

from app_support import declare_models
from test_admin_clients_api import new_client
from test_admin_email_auth import (  # noqa: F401 - fixtures
    BASE,
    MAILBOX_PASSWORD,
    accept_platform,
    assign,
    fx,
    server,
    status,
    store_login,
)
from test_admin_email_platform import store_profile

NEW = f"{BASE}/email/new-clients"
CHANNELS = "/admin/v1/settings/channels"


def make_ready(client, server):
    store_profile(client)
    store_login(client)
    accept_platform(server)
    assert client.post(f"{BASE}/email/test-smtp").json()["ok"] is True
    assert client.post(f"{BASE}/email/test-imap").json()["ok"] is True
    assert status(client)["state"] == "ready"


def effective(client, cid):
    return client.get(f"/admin/v1/clients/{cid}/channels").json()["effective"]


def create(fx, client, name="Acme Corp", login="acme.admin", number="+216 22 768 307"):
    declare_models(fx.application.credentials, "gpt-4o")
    response = client.post(
        "/admin/v1/clients", json=new_client(display_name=name, login=login, whatsapp_number=number)
    )
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


class TestSwitchingItOn:
    def test_it_is_off_until_the_admin_turns_it_on(self, fx):
        body = status(fx.login("root"))
        assert body["new_clients_access"] is False and body["clients_awaiting_mailbox"] == []

    def test_it_cannot_be_turned_on_while_the_servers_are_not_proven(self, fx, server):
        client = fx.login("root")
        refused = client.put(NEW, json={"enabled": True})
        assert refused.status_code == 409 and "not ready" in refused.json()["detail"]
        store_profile(client)
        store_login(client)  # configured, not yet logged in
        assert client.put(NEW, json={"enabled": True}).status_code == 409
        assert status(client)["new_clients_access"] is False

    def test_a_proven_service_can_be_turned_on_and_off(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        on = client.put(NEW, json={"enabled": True})
        assert on.status_code == 200 and on.json()["new_clients_access"] is True
        assert status(client)["new_clients_access"] is True
        off = client.put(NEW, json={"enabled": False})
        assert off.json()["new_clients_access"] is False

    def test_only_an_owner_changes_it(self, fx, server):
        assert fx.login("olivia").put(NEW, json={"enabled": False}).status_code == 403
        assert fx.client.put(NEW, json={"enabled": False}).status_code in (401, 403)

    def test_a_body_with_anything_else_is_refused(self, fx):
        assert fx.login("root").put(NEW, json={"enabled": True, "x": 1}).status_code == 422

    def test_turning_it_on_changes_neither_the_global_list_nor_existing_clients(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        before = client.get(CHANNELS).json()["allowed"]
        client.put(NEW, json={"enabled": True})
        assert client.get(CHANNELS).json()["allowed"] == before
        assert "email" not in before
        for cid in ("alpha", "beta"):
            assert "email" not in effective(client, cid)


class TestNewClients:
    def test_a_client_created_after_it_is_on_may_use_email(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        client.put(NEW, json={"enabled": True})
        cid = create(fx, client)
        assert "email" in effective(client, cid)
        access = client.get(f"/admin/v1/clients/{cid}/channels").json()
        assert access["added"] == ["email"] and "email" not in access["global_allowed"]

    def test_a_client_created_before_it_is_on_is_not_changed_afterwards(self, fx, server):
        client = fx.login("root")
        early = create(fx, client, "Early Co", "early.admin", "+216 22 111 111")
        make_ready(client, server)
        client.put(NEW, json={"enabled": True})
        assert "email" not in effective(client, early)
        late = create(fx, client, "Late Co", "late.admin", "+216 22 222 222")
        assert "email" in effective(client, late) and "email" not in effective(client, early)

    def test_a_client_created_after_it_is_off_again_gets_nothing(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        client.put(NEW, json={"enabled": True})
        first = create(fx, client, "First Co", "first.admin", "+216 22 333 333")
        client.put(NEW, json={"enabled": False})
        second = create(fx, client, "Second Co", "second.admin", "+216 22 444 444")
        assert "email" in effective(client, first)  # nothing is taken away
        assert "email" not in effective(client, second)

    def test_when_everyone_already_may_use_email_no_exception_is_stored(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        client.put(CHANNELS, json={"allowed": ["whatsapp", "email"]})
        client.put(NEW, json={"enabled": True})
        cid = create(fx, client)
        assert client.get(f"/admin/v1/clients/{cid}/channels").json()["added"] == []

    def test_whatsapp_and_the_other_channels_are_untouched(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        client.put(NEW, json={"enabled": True})
        cid = create(fx, client)
        assert effective(client, cid) == ["email", "whatsapp"]
        assert "telegram" not in effective(client, cid)


class TestAwaitingAMailbox:
    def test_a_new_client_with_email_and_no_mailbox_is_listed_until_one_is_assigned(
        self, fx, server
    ):
        client = fx.login("root")
        make_ready(client, server)
        client.put(NEW, json={"enabled": True})
        cid = create(fx, client)
        assert status(client)["clients_awaiting_mailbox"] == [cid]
        assign(client, cid, "acme@mail.example.org", MAILBOX_PASSWORD)
        assert status(client)["clients_awaiting_mailbox"] == []

    def test_clients_without_email_access_are_not_listed(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        assert status(client)["clients_awaiting_mailbox"] == []  # alpha and beta have no Email

    def test_nothing_in_the_list_is_an_address_or_a_secret(self, fx, server):
        client = fx.login("root")
        make_ready(client, server)
        client.put(NEW, json={"enabled": True})
        create(fx, client)
        text = str(status(client))
        assert "@" not in text and MAILBOX_PASSWORD not in text
