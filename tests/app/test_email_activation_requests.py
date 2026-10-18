# ruff: noqa: F811  (fixtures imported from test_email_channel_api are used as arguments)
"""The client asks for its e-mail to be activated; the Admin answers by hand.

Covers: what a client must have done before asking; one open request at most and asking again after
a rejection; the Admin's list and moves (start, reject with a reason, activate, deactivate); that
activation really tries both logins and stores nothing when they fail; that a request which is not
active keeps the agent from sending; that the password is never in any response; and that clients
and roles stay apart. Every mail server is a double: nothing here proves a real server accepts a
login, and no message is sent.
"""

import pytest
from test_client_agent_api import READINESS, START
from test_email_channel_api import (  # noqa: F401 - fixtures
    CHANNEL,
    MAIL,
    TestAPlatformMailboxNeedsAProvenLogin,
    admin,
    allow_email,
    alpha,
    beta,
    choose,
    wire_legacy_mailbox,
    world,
)

from sales_app.connection_tests import MailProbeFailure

REQUEST = "/client/v1/channel/email-activation"
LIST = "/admin/v1/email-activations"
PASSWORD = "Mailbox-Pw-Zq81xv!"
ADDRESS = "alpha@mail.example.org"


def url(cid, action=""):
    return f"/admin/v1/clients/{cid}/email-activation" + (f"/{action}" if action else "")


class Mail:
    """Stands for the mail servers' login: accepts or refuses, and sends and reads nothing."""

    def __init__(self):
        self.refuse: str | None = None  # None: accept; else the kind of failure
        self.tried: list[str] = []

    def __call__(self, smtp, imap):
        self.tried.append(smtp.username)
        return self

    def check_smtp(self):
        if self.refuse:
            raise MailProbeFailure(self.refuse)

    def check_imap(self):
        if self.refuse:
            raise MailProbeFailure(self.refuse)


@pytest.fixture
def mail(world):
    double = Mail()
    world.app_kwargs["mail_login_probe"] = double
    return double


def set_servers(admin):
    for key, value in TestAPlatformMailboxNeedsAProvenLogin.SERVERS.items():
        assert admin.put(f"/admin/v1/settings/integrations/{key}", json={"value": value}).is_success


@pytest.fixture
def ready(world, mail, alpha, beta, admin):
    """Email allowed, servers set, both clients on e-mail with a confirmed Sender Name."""
    allow_email(admin)
    set_servers(admin)
    assert choose(alpha).is_success and choose(beta).is_success
    return admin


def ask(client):
    return client.post(REQUEST)


def activation(client):
    return client.get(CHANNEL).json()["email"]["activation"]


def activate(admin, cid="alpha", address=ADDRESS, password=PASSWORD):
    return admin.post(url(cid, "activate"), json={"address": address, "password": password})


def in_progress(admin, alpha):
    assert ask(alpha).status_code == 201
    assert admin.post(url("alpha", "start")).status_code == 200


def stored(world, cid, key):
    from sales_app.secret_store import client_scope

    return world.application.credentials.status(client_scope(cid), key).configured


class TestAskingForActivation:
    def test_it_needs_the_admin_to_allow_email_first(self, world, alpha, admin):
        assert choose(alpha, "whatsapp", None).is_success
        assert ask(alpha).status_code == 403

    def test_it_needs_the_email_channel(self, ready, alpha):
        assert choose(alpha, "whatsapp", None).is_success
        assert ask(alpha).status_code == 409

    def test_it_needs_a_confirmed_sender_name(self, world, alpha, admin):
        allow_email(admin)
        assert choose(alpha, settings={"subject": "Hello"}).is_success
        refused = ask(alpha)
        assert refused.status_code == 409 and "sender name" in refused.json()["detail"]

    def test_a_historical_mailbox_has_nothing_to_activate(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        assert ask(alpha).status_code == 409

    def test_the_request_appears_with_a_status_and_a_date(self, ready, alpha):
        assert activation(alpha) is None
        assert ask(alpha).status_code == 201
        body = activation(alpha)
        assert body["status"] == "pending" and body["requested_at"] and body["reason"] is None

    def test_a_second_open_request_is_refused(self, ready, alpha):
        ask(alpha)
        assert ask(alpha).status_code == 409
        ready.post(url("alpha", "start"))
        assert ask(alpha).status_code == 409
        assert activation(alpha)["status"] == "in_progress"

    def test_it_needs_a_client_session(self, world):
        assert world.anonymous().post(REQUEST).status_code == 401

    def test_asking_is_not_the_admins_authorization_of_email(self, ready, alpha, admin):
        ask(alpha)
        # Email is still allowed or not by the Admin alone; the request grants nothing.
        assert admin.get("/admin/v1/clients/alpha/channels").json()["effective"].count("email") == 1
        admin.put("/admin/v1/settings/channels", json={"allowed": ["whatsapp"]})
        assert alpha.get(CHANNEL).json()["authorized"] is False
        assert activation(alpha)["status"] == "pending"


class TestWhatTheAdminSees:
    def test_the_list_shows_client_sender_name_date_and_status(self, ready, alpha):
        ask(alpha)
        rows = ready.get(LIST).json()
        assert [r["client_id"] for r in rows] == ["alpha"]
        row = rows[0]
        assert row["sender_name"] == MAIL["from_name"] and row["status"] == "pending"
        assert row["requested_at"] and row["client_name"]
        assert row["sender_name"] != ADDRESS and "@" not in row["sender_name"]

    def test_only_open_requests_are_listed_unless_all_is_asked(self, ready, alpha, beta):
        ask(alpha)
        ask(beta)
        ready.post(url("beta", "reject"), json={"reason": "Not eligible"})
        assert [r["client_id"] for r in ready.get(LIST).json()] == ["alpha"]
        everyone = ready.get(LIST, params={"status": "all"}).json()
        assert {r["client_id"] for r in everyone} == {"alpha", "beta"}

    def test_a_client_with_no_request_is_not_listed(self, ready):
        assert ready.get(LIST).json() == []
        assert ready.get(url("alpha")).json() is None

    def test_the_routes_are_for_admins_only(self, ready, alpha, world):
        ask(alpha)
        for call in (
            lambda c: c.get(LIST),
            lambda c: c.post(url("alpha", "start")),
            lambda c: c.post(url("alpha", "reject"), json={"reason": "x"}),
            lambda c: c.post(url("alpha", "activate"), json={"address": ADDRESS, "password": "p"}),
        ):
            assert call(world.anonymous()).status_code == 401
            assert call(alpha).status_code in (401, 403)

    def test_an_unknown_client_is_a_404(self, ready):
        assert ready.get(url("nobody")).status_code == 404
        assert ready.post(url("nobody", "start")).status_code == 404


class TestAnswering:
    def test_a_pending_request_goes_in_progress_once(self, ready, alpha):
        ask(alpha)
        assert ready.post(url("alpha", "start")).json()["status"] == "in_progress"
        assert activation(alpha)["status"] == "in_progress"
        assert ready.post(url("alpha", "start")).status_code == 409

    def test_a_rejection_needs_a_reason_and_the_client_reads_it(self, ready, alpha):
        ask(alpha)
        assert ready.post(url("alpha", "reject"), json={"reason": "  "}).status_code == 422
        assert ready.post(url("alpha", "reject"), json={}).status_code == 422
        assert activation(alpha)["status"] == "pending"
        done = ready.post(url("alpha", "reject"), json={"reason": "Not on your plan"})
        assert done.status_code == 200 and done.json()["reason"] == "Not on your plan"
        seen = activation(alpha)
        assert seen["status"] == "rejected" and seen["reason"] == "Not on your plan"
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "action_required"

    def test_after_a_rejection_the_client_may_ask_again(self, ready, alpha):
        ask(alpha)
        ready.post(url("alpha", "reject"), json={"reason": "Not yet"})
        assert ask(alpha).status_code == 201
        again = activation(alpha)
        assert again["status"] == "pending" and again["reason"] is None

    def test_a_rejected_request_cannot_be_activated(self, ready, alpha, world):
        ask(alpha)
        ready.post(url("alpha", "reject"), json={"reason": "No"})
        assert activate(ready).status_code == 409
        assert not stored(world, "alpha", "email.platform_mailbox")

    def test_one_clients_request_never_reaches_another(self, ready, alpha, beta):
        ask(alpha)
        assert activation(beta) is None
        assert ready.get(url("beta")).json() is None
        ready.post(url("alpha", "reject"), json={"reason": "Only for alpha"})
        assert activation(beta) is None


class TestActivating:
    def test_it_needs_the_request_to_be_in_progress(self, ready, alpha, world):
        assert activate(ready).status_code == 409  # no request at all
        ask(alpha)
        refused = activate(ready)  # still pending
        assert refused.status_code == 409 and "start" in refused.json()["detail"]
        assert not stored(world, "alpha", "email.platform_mailbox")

    def test_a_good_mailbox_is_stored_and_the_channel_becomes_ready(
        self, ready, alpha, world, mail
    ):
        in_progress(ready, alpha)
        done = activate(ready)
        assert done.status_code == 200 and done.json()["status"] == "active"
        assert done.json()["mailbox"] == "ready"
        assert mail.tried == [ADDRESS]
        assert stored(world, "alpha", "email.platform_mailbox")
        assert stored(world, "alpha", "email.password")
        assert activation(alpha)["status"] == "active"
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "ready" and body["status_message"] is None
        checks = {c["id"]: c for c in alpha.get(READINESS).json()["checks"]}
        assert checks["channel_ready"]["ok"] is True

    def test_the_password_is_in_no_response_and_the_address_is_not_the_clients(
        self, ready, alpha, beta
    ):
        in_progress(ready, alpha)
        admin_side = [
            activate(ready).text,
            ready.get(LIST, params={"status": "all"}).text,
            ready.get(url("alpha")).text,
            ready.get("/admin/v1/clients/alpha/integrations").text,
        ]
        client_side = [alpha.get(CHANNEL).text, alpha.get(READINESS).text, beta.get(CHANNEL).text]
        assert not any(PASSWORD in text for text in admin_side + client_side)
        assert not any(ADDRESS in text for text in client_side)
        assert ADDRESS not in "".join(admin_side[:3])  # the activation routes name no address

    def test_a_wrong_login_stores_nothing_and_leaves_the_request_as_it_was(
        self, ready, alpha, world, mail
    ):
        in_progress(ready, alpha)
        mail.refuse = "auth"
        refused = activate(ready)
        assert refused.status_code == 422
        problems = refused.json()["detail"]["problems"]
        assert any("refused the login" in p for p in problems)
        assert PASSWORD not in refused.text and ADDRESS not in refused.text
        assert not stored(world, "alpha", "email.platform_mailbox")
        assert not stored(world, "alpha", "email.password")
        assert activation(alpha)["status"] == "in_progress"
        assert alpha.get(CHANNEL).json()["status"] == "waiting_platform"
        mail.refuse = None  # the Admin corrects it and tries again
        assert activate(ready).json()["status"] == "active"

    def test_an_unreachable_server_is_said_too(self, ready, alpha, mail):
        in_progress(ready, alpha)
        mail.refuse = "unreachable"
        refused = activate(ready)
        assert refused.status_code == 422
        assert any("could not be reached" in p for p in refused.json()["detail"]["problems"])
        assert activation(alpha)["status"] == "in_progress"

    def test_a_bad_address_or_an_empty_password_is_refused_without_an_echo(
        self, ready, alpha, mail
    ):
        in_progress(ready, alpha)
        bad = activate(ready, address="not an address", password=PASSWORD)
        assert bad.status_code == 422 and "not an address" not in bad.text
        empty = activate(ready, password="")
        assert empty.status_code == 422
        assert mail.tried == []  # nothing was tried
        assert ready.post(url("alpha", "activate"), json={"address": ADDRESS}).status_code == 422
        extra = ready.post(
            url("alpha", "activate"), json={"address": ADDRESS, "password": PASSWORD, "x": 1}
        )
        assert extra.status_code == 422 and PASSWORD not in extra.text

    def test_the_servers_must_be_set_up(self, world, mail, alpha, admin):
        allow_email(admin)
        assert choose(alpha).is_success
        in_progress(admin, alpha)
        refused = activate(admin)
        assert refused.status_code == 409 and "servers" in refused.json()["detail"]
        assert mail.tried == []

    def test_email_must_be_allowed_to_this_client(self, ready, alpha, mail):
        in_progress(ready, alpha)
        ready.put("/admin/v1/settings/channels", json={"allowed": ["whatsapp"]})
        refused = activate(ready)
        assert refused.status_code == 409 and "not allowed" in refused.json()["detail"]
        assert mail.tried == []

    def test_an_address_held_by_another_client_is_refused(self, ready, alpha, beta, world):
        in_progress(ready, alpha)
        assert activate(ready).status_code == 200
        ask(beta)
        ready.post(url("beta", "start"))
        clash = activate(ready, "beta", address=ADDRESS.upper())
        assert clash.status_code == 409
        assert not stored(world, "beta", "email.platform_mailbox")
        assert not stored(world, "beta", "email.password")
        assert activation(beta)["status"] == "in_progress"

    def test_a_failed_activation_keeps_a_mailbox_that_was_already_there(
        self, ready, alpha, world, mail
    ):
        in_progress(ready, alpha)
        assert activate(ready).status_code == 200
        ready.post(url("alpha", "deactivate"), json={"reason": "Switching boxes"})
        assert ask(alpha).status_code == 201
        ready.post(url("alpha", "start"))
        mail.refuse = "auth"
        assert activate(ready, address="other@mail.example.org").status_code == 422
        assert not stored(world, "alpha", "email.platform_mailbox")

    def test_only_an_owner_changes_anything_and_a_scoped_admin_sees_only_its_clients(
        self, ready, alpha, beta, world
    ):
        from test_email_channel_api import PASSWORD, _admin

        from sales_app.admin_auth import AdminRole

        in_progress(ready, alpha)
        ask(beta)
        world.users.create(_admin("eve", AdminRole.EDITOR))
        world.users.create(_admin("sam", AdminRole.EDITOR, client_ids=("beta",)))
        for name in ("eve", "sam"):
            client = world.anonymous()
            login = client.post(
                "/admin/v1/auth/login", json={"username": name, "password": PASSWORD}
            )
            assert login.status_code == 200
            assert activate(client).status_code == 403
            assert client.post(url("alpha", "start")).status_code == 403
            assert client.post(url("alpha", "reject"), json={"reason": "x"}).status_code == 403
        sam = world.anonymous()
        sam.post("/admin/v1/auth/login", json={"username": "sam", "password": PASSWORD})
        assert [r["client_id"] for r in sam.get(LIST).json()] == ["beta"]
        assert sam.get(url("alpha")).status_code == 404  # not even known to exist
        assert not stored(world, "alpha", "email.platform_mailbox")


class TestSendingNeedsTheActivation:
    def prove(self, world, ready):
        """Assign a mailbox by hand and prove its login, as an Admin could before this feature."""
        base = "/admin/v1/clients/alpha/integrations"
        ready.put(f"{base}/email.platform_mailbox", json={"value": ADDRESS})
        ready.put(f"{base}/email.password", json={"value": PASSWORD})
        from sales_app.mailbox_proof import verify_client_mailbox

        verify_client_mailbox(world.application, "alpha", Mail())

    def channel_ready(self, client):
        return {c["id"]: c for c in client.get(READINESS).json()["checks"]}["channel_ready"]["ok"]

    def test_a_client_that_never_asked_keeps_working(self, ready, alpha, world):
        self.prove(world, ready)
        assert alpha.get(CHANNEL).json()["status"] == "ready"
        assert self.channel_ready(alpha) is True

    @pytest.mark.parametrize("move", [None, "start", "reject"])
    def test_a_request_that_is_not_active_stops_the_agent(self, ready, alpha, world, move):
        self.prove(world, ready)
        assert ask(alpha).status_code == 201
        if move == "start":
            ready.post(url("alpha", "start"))
        if move == "reject":
            ready.post(url("alpha", "reject"), json={"reason": "No"})
        assert alpha.get(CHANNEL).json()["status"] != "ready"
        assert self.channel_ready(alpha) is False
        assert alpha.post(START).status_code == 409

    def test_deactivating_takes_the_mailbox_off_and_the_client_may_ask_again(
        self, ready, alpha, world
    ):
        in_progress(ready, alpha)
        activate(ready)
        assert ready.post(url("alpha", "deactivate"), json={"reason": " "}).status_code == 422
        ended = ready.post(url("alpha", "deactivate"), json={"reason": "Contract ended"})
        assert ended.status_code == 200 and ended.json()["status"] == "rejected"
        assert not stored(world, "alpha", "email.platform_mailbox")
        assert not stored(world, "alpha", "email.password")
        seen = activation(alpha)
        assert seen["status"] == "rejected" and seen["reason"] == "Contract ended"
        assert self.channel_ready(alpha) is False
        assert ask(alpha).status_code == 201

    def test_only_an_active_request_can_be_deactivated(self, ready, alpha):
        ask(alpha)
        assert ready.post(url("alpha", "deactivate"), json={"reason": "x"}).status_code == 409


class TestOtherChannelsAreUntouched:
    def test_a_whatsapp_client_has_no_activation(self, ready, alpha):
        choose(alpha, "whatsapp", None)
        body = alpha.get(CHANNEL).json()
        assert body["email"] is None and body["channel"] == "whatsapp"
