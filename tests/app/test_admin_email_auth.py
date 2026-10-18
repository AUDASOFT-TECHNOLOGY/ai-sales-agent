"""Admin → Email: the logins. The platform's test login for each server, the mailbox assigned to a
client, and where each stands (`missing_configuration` ... `ready`).

Every server here is a double: nothing proves that a real server accepts these logins. What is
proven is that the checks log in with the stored login, only say "authenticated" when the double
accepted it, send nothing, read nothing, and never repeat a password.
"""

import imaplib
import smtplib

import pytest
from fastapi.testclient import TestClient
from test_admin_email_platform import BASE, TEST, store_profile
from test_admin_integrations_api import Fixture, put, user

from sales_app.admin_auth import AdminRole
from sales_app.api import create_app
from sales_app.email_platform import MailLoginProbe, PlatformMailProbe
from sales_app.mailbox_access import platform_access
from sales_integrations.mail import ImapMailbox, MailHostRefusedError, MailUnavailableError

SMTP_PASSWORD = "smtp-secret-Zq81xv"
IMAP_PASSWORD = "imap-secret-Kd47wp"
MAILBOX_PASSWORD = "mailbox-secret-Tr90mn"
LOGIN = {
    "email.platform_smtp_username": "smtp-login@example.org",
    "email.platform_smtp_password": SMTP_PASSWORD,
    "email.platform_imap_username": "imap-login@example.org",
    "email.platform_imap_password": IMAP_PASSWORD,
}
SECRETS = (SMTP_PASSWORD, IMAP_PASSWORD, MAILBOX_PASSWORD)


class Server:
    """Stands for a mail server: accepts the logins it is told to, records everything it sees."""

    def __init__(self):
        self.accepted: set[tuple[str, str]] = set()
        self.logins: list[tuple[str, str, str]] = []  # (protocol, username, password)
        self.sent: list[object] = []
        self.read: list[object] = []
        self.unreachable = False
        self.refused = False
        self.imap_refuses = False

    def smtp_connection(self, settings):
        self._reach()
        server = self

        class Connection:
            def login(self, username, password):
                server.logins.append(("smtp", username, password))
                if (username, password) not in server.accepted:
                    raise smtplib.SMTPAuthenticationError(535, b"bad login " + password.encode())

            def send_message(self, *a, **k):
                server.sent.append(a)

            def quit(self):
                pass

        return Connection()

    def imap_connection(self, settings):
        self._reach()
        server = self

        class Connection:
            def login(self, username, password):
                server.logins.append(("imap", username, password))
                if server.imap_refuses or (username, password) not in server.accepted:
                    raise imaplib.IMAP4.error("LOGIN failed " + password)

            def select(self, *a, **k):
                return "OK", [b"0"]

            def uid(self, *a, **k):
                server.read.append(a)
                return "OK", [b""]

            def logout(self):
                pass

        return Connection()

    def _reach(self):
        if self.refused:
            raise MailHostRefusedError("10.0.0.5")
        if self.unreachable:
            raise MailUnavailableError("down")

    def connect_probe(self, profile):
        return PlatformMailProbe(
            profile, smtp_factory=self.smtp_connection, imap_factory=self.imap_connection
        )

    def probe(self, smtp, imap):
        return MailLoginProbe(
            smtp, imap, smtp_factory=self.smtp_connection, imap_factory=self.imap_connection
        )


@pytest.fixture
def server():
    return Server()


@pytest.fixture
def fx(tmp_path, server):
    fixture = Fixture(tmp_path)
    fixture.users.create(user("root", AdminRole.OWNER))
    fixture.users.create(user("olivia", AdminRole.OPERATOR, frozenset({"alpha"})))
    fixture.users.create(user("vera", AdminRole.VIEWER, frozenset({"alpha"})))
    fixture.client = TestClient(
        create_app(
            fixture.application,
            run_scheduler=False,
            mail_login_probe=server.probe,
            platform_mail_probe=server.connect_probe,  # no test here ever reaches a real server
        ),
        base_url="https://testserver",
    )
    return fixture


def store_login(client, **over):
    for key, value in {**LOGIN, **over}.items():
        if value is not None:
            assert put(client, f"{BASE}/{key}", value).status_code == 200, key


def status(client):
    return client.get(f"{BASE}/email/status").json()


def assign(client, cid="alpha", address="box@mail.example.org", password=MAILBOX_PASSWORD):
    base = f"/admin/v1/clients/{cid}/integrations"
    assert put(client, f"{base}/email.platform_mailbox", address).status_code == 200
    if password:
        assert put(client, f"{base}/email.password", password).status_code == 200


def accept_platform(server):
    server.accepted |= {
        (LOGIN["email.platform_smtp_username"], SMTP_PASSWORD),
        (LOGIN["email.platform_imap_username"], IMAP_PASSWORD),
    }


class TestSavingAndReloading:
    def test_the_four_logins_are_listed_and_only_usernames_are_shown(self, fx):
        client = fx.login("root")
        body = {c["key"]: c for c in client.get(BASE).json()}
        for key in LOGIN:
            assert body[key]["scope"] == "global" and body[key]["configured"] is False
        assert body["email.platform_smtp_username"]["sensitive"] is False
        assert body["email.platform_smtp_password"]["sensitive"] is True

    def test_after_a_save_the_username_comes_back_and_the_password_never_does(self, fx):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        listing = client.get(BASE)
        body = {c["key"]: c for c in listing.json()}
        assert (
            body["email.platform_smtp_username"]["value"] == LOGIN["email.platform_smtp_username"]
        )
        assert body["email.platform_smtp_password"]["configured"] is True
        assert body["email.platform_smtp_password"]["value"] is None
        assert not any(secret in listing.text for secret in SECRETS)

    def test_a_save_response_does_not_echo_the_password(self, fx):
        response = put(fx.login("root"), f"{BASE}/email.platform_smtp_password", SMTP_PASSWORD)
        assert response.status_code == 200 and SMTP_PASSWORD not in response.text

    def test_a_username_with_a_space_is_refused_without_being_echoed(self, fx):
        response = put(fx.login("root"), f"{BASE}/email.platform_smtp_username", "bad name")
        assert response.status_code == 422 and "bad name" not in response.text

    @pytest.mark.parametrize("who", ["olivia", "vera"])
    def test_only_an_owner_saves_them(self, fx, who):
        url = f"{BASE}/email.platform_smtp_password"
        assert put(fx.login(who), url, SMTP_PASSWORD).status_code == 403

    def test_the_logins_are_not_a_setting_of_any_client(self, fx):
        url = "/admin/v1/clients/alpha/integrations/email.platform_smtp_password"
        assert put(fx.login("root"), url, SMTP_PASSWORD).status_code == 404

    def test_the_passwords_are_encrypted_at_rest(self, fx):
        store_login(fx.login("root"))
        on_disk = fx.everything_on_disk()
        assert on_disk and not any(secret in on_disk for secret in SECRETS)


class TestTheLoginChecks:
    @pytest.mark.parametrize("path", ["email/test-smtp", "email/test-imap"])
    def test_they_are_for_owners_only(self, fx, path):
        assert fx.client.post(f"{BASE}/{path}").status_code == 401
        assert fx.login("olivia").post(f"{BASE}/{path}").status_code == 403
        assert fx.login("vera").post(f"{BASE}/{path}").status_code == 403

    def test_without_servers_or_a_login_they_say_what_is_missing_and_connect_nowhere(
        self, fx, server
    ):
        client = fx.login("root")
        body = client.post(f"{BASE}/email/test-smtp").json()
        assert body["ok"] is False and "not complete" in body["message"]
        store_profile(client)
        body = client.post(f"{BASE}/email/test-imap").json()
        assert body["ok"] is False and "IMAP username or password is not set" in body["message"]
        assert server.logins == []

    def test_smtp_logs_in_with_the_stored_login_and_sends_nothing(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        accept_platform(server)
        body = client.post(f"{BASE}/email/test-smtp").json()
        assert body["ok"] is True and body["details"] == {"smtp": "ok"}
        assert "accepted the login" in body["message"]
        assert server.logins == [("smtp", LOGIN["email.platform_smtp_username"], SMTP_PASSWORD)]
        assert server.sent == [] and server.read == []

    def test_imap_logs_in_opens_the_folder_and_reads_nothing(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        accept_platform(server)
        body = client.post(f"{BASE}/email/test-imap").json()
        assert body["ok"] is True and body["details"] == {"imap": "ok"}
        assert server.logins == [("imap", LOGIN["email.platform_imap_username"], IMAP_PASSWORD)]
        assert server.sent == []
        assert all(call[0] != "FETCH" and call[0] != "STORE" for call in server.read)

    @pytest.mark.parametrize("protocol", ["smtp", "imap"])
    def test_a_refused_login_is_an_auth_failure_that_repeats_no_password(
        self, fx, server, protocol
    ):
        client = fx.login("root")
        store_profile(client)
        store_login(client)  # the server accepts nothing
        response = client.post(f"{BASE}/email/test-{protocol}")
        body = response.json()
        assert body["ok"] is False and body["details"] == {protocol: "auth"}
        assert "refused the login" in body["message"]
        assert not any(secret in response.text for secret in SECRETS)

    def test_an_unreachable_server_is_not_an_auth_failure(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        server.unreachable = True
        body = client.post(f"{BASE}/email/test-smtp").json()
        assert body["ok"] is False and body["details"] == {"smtp": "unreachable"}

    def test_a_host_that_is_not_public_is_refused(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        server.refused = True
        body = client.post(f"{BASE}/email/test-imap").json()
        assert body["details"] == {"imap": "refused"} and "10.0.0.5" not in str(body)

    def test_the_plain_connection_test_is_never_called_an_authentication(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        accept_platform(server)
        body = client.post(TEST).json()  # servers reached, not logged in
        assert "No login was attempted" in body["message"]
        assert status(client)["state"] == "server_not_authenticated"
        assert server.logins == []


class TestThePlatformState:
    def test_nothing_set_is_missing_configuration(self, fx):
        body = status(fx.login("root"))
        assert body["state"] == "missing_configuration"
        assert body["smtp"] == body["imap"] == "not_configured"
        assert "SMTP host" in body["missing"]

    def test_servers_without_a_login_are_still_missing_configuration(self, fx):
        client = fx.login("root")
        store_profile(client)
        assert status(client)["state"] == "missing_configuration"

    def test_a_complete_configuration_is_not_ready_until_both_logins_were_accepted(
        self, fx, server
    ):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        accept_platform(server)
        assert status(client)["state"] == "server_not_authenticated"
        client.post(f"{BASE}/email/test-smtp")
        body = status(client)
        assert body["state"] == "server_not_authenticated"
        assert (body["smtp"], body["imap"]) == ("authenticated", "not_tested")
        client.post(f"{BASE}/email/test-imap")
        assert status(client) == {
            "state": "ready",
            "smtp": "authenticated",
            "imap": "authenticated",
            "missing": [],
            "smtp_problem": None,
            "imap_problem": None,
            "new_clients_access": False,
            "clients_awaiting_mailbox": [],
        }

    def test_a_failed_login_is_not_ready(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        client.post(f"{BASE}/email/test-smtp")
        assert status(client)["smtp"] == "failed"

    def test_changing_a_password_voids_the_proof(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        accept_platform(server)
        client.post(f"{BASE}/email/test-smtp")
        client.post(f"{BASE}/email/test-imap")
        assert status(client)["state"] == "ready"
        store_login(client, **{"email.platform_smtp_password": "another-Pw-3322"})
        body = status(client)
        assert body["state"] == "server_not_authenticated" and body["smtp"] == "not_tested"

    def test_changing_a_host_voids_the_proof(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        store_login(client)
        accept_platform(server)
        client.post(f"{BASE}/email/test-imap")
        store_profile(client, **{"email.platform_imap_host": "other.example.org"})
        assert status(client)["imap"] == "not_tested"

    def test_any_admin_may_read_the_state(self, fx):
        assert fx.login("vera").get(f"{BASE}/email/status").status_code == 200
        anonymous = TestClient(fx.client.app, base_url="https://testserver")
        assert anonymous.get(f"{BASE}/email/status").status_code == 401


class TestAClientsMailbox:
    def url(self, cid, tail):
        return f"/admin/v1/clients/{cid}/integrations/email/{tail}"

    def prepare(self, fx, server):
        client = fx.login("root")
        store_profile(client)
        return client

    def test_each_state_before_ready(self, fx, server):
        client = fx.login("root")
        assert client.get(self.url("alpha", "status")).json()["state"] == "missing_configuration"
        store_profile(client)
        assert client.get(self.url("alpha", "status")).json()["state"] == "mailbox_not_assigned"
        assign(client, password=None)
        assert client.get(self.url("alpha", "status")).json()["state"] == "password_missing"
        assign(client)
        body = client.get(self.url("alpha", "status")).json()
        assert (body["state"], body["smtp"], body["imap"]) == (
            "not_authenticated",
            "not_tested",
            "not_tested",
        )

    def test_smtp_and_imap_can_differ_and_each_says_why(self, fx, server):
        client = self.prepare(fx, server)
        assign(client)
        server.accepted.add(("box@mail.example.org", MAILBOX_PASSWORD))
        server.imap_refuses = True
        client.post(self.url("alpha", "test"))
        body = client.get(self.url("alpha", "status")).json()
        assert (body["state"], body["smtp"], body["imap"]) == (
            "not_authenticated",
            "authenticated",
            "failed",
        )
        assert body["smtp_problem"] is None
        assert "refused the login" in body["imap_problem"]
        assert not any(secret in str(body) for secret in SECRETS)
        assert "box@mail.example.org" not in str(body)

    def test_the_test_logs_in_with_the_mailboxs_own_address_and_password(self, fx, server):
        client = self.prepare(fx, server)
        assign(client)
        server.accepted.add(("box@mail.example.org", MAILBOX_PASSWORD))
        response = client.post(self.url("alpha", "test"))
        body = response.json()
        assert body["ok"] is True and body["details"] == {"smtp": "ok", "imap": "ok"}
        assert server.logins == [
            ("smtp", "box@mail.example.org", MAILBOX_PASSWORD),
            ("imap", "box@mail.example.org", MAILBOX_PASSWORD),
        ]
        assert server.sent == []
        assert MAILBOX_PASSWORD not in response.text
        assert client.get(self.url("alpha", "status")).json()["state"] == "ready"

    def test_a_wrong_password_is_not_ready(self, fx, server):
        client = self.prepare(fx, server)
        assign(client)
        response = client.post(self.url("alpha", "test"))
        assert response.json()["ok"] is False and "refused the login" in response.json()["message"]
        assert MAILBOX_PASSWORD not in response.text
        body = client.get(self.url("alpha", "status")).json()
        assert body["state"] == "not_authenticated" and body["smtp"] == "failed"

    def test_an_unassigned_mailbox_is_said_and_no_server_is_touched(self, fx, server):
        client = self.prepare(fx, server)
        body = client.post(self.url("alpha", "test")).json()
        assert body["ok"] is False and "no mailbox has been assigned" in body["message"]
        assert server.logins == []

    def test_the_test_is_for_owners_and_for_the_admins_who_reach_the_client(self, fx):
        assert fx.login("olivia").post(self.url("alpha", "test")).status_code == 403
        assert fx.login("root").post(self.url("ghost", "test")).status_code == 404
        assert fx.login("olivia").get(self.url("beta", "status")).status_code == 404

    def test_two_clients_never_share_a_mailbox_or_a_proof(self, fx, server):
        client = self.prepare(fx, server)
        assign(client, "alpha", "alpha@mail.example.org", "alpha-secret-Pw11")
        assign(client, "beta", "beta@mail.example.org", "beta-secret-Pw22")
        server.accepted.add(("alpha@mail.example.org", "alpha-secret-Pw11"))
        assert client.post(self.url("alpha", "test")).json()["ok"] is True
        # beta's own login was never accepted, and alpha's proof is not beta's.
        assert client.get(self.url("alpha", "status")).json()["state"] == "ready"
        assert client.get(self.url("beta", "status")).json()["state"] == "not_authenticated"
        assert client.post(self.url("beta", "test")).json()["ok"] is False
        used = {(protocol, user_) for protocol, user_, _ in server.logins}
        assert ("smtp", "beta@mail.example.org") in used
        assert not any(
            u == "alpha@mail.example.org" and p == "beta-secret-Pw22" for _, u, p in server.logins
        )

    def test_a_mailbox_address_belongs_to_one_client_only(self, fx):
        client = fx.login("root")
        assign(client, "alpha", "same@mail.example.org")
        response = put(
            client,
            "/admin/v1/clients/beta/integrations/email.platform_mailbox",
            "SAME@mail.example.org",
        )
        assert response.status_code == 409

    def test_changing_the_password_voids_the_proof(self, fx, server):
        client = self.prepare(fx, server)
        assign(client)
        server.accepted.add(("box@mail.example.org", MAILBOX_PASSWORD))
        client.post(self.url("alpha", "test"))
        put(client, "/admin/v1/clients/alpha/integrations/email.password", "changed-Pw-5566")
        assert client.get(self.url("alpha", "status")).json()["state"] == "not_authenticated"


class TestSendingAndReceivingWithTheAssignedMailbox:
    """The engine's own adapters, built from the assigned mailbox with doubles for the servers."""

    def access(self, address="box@mail.example.org"):
        from sales_app.email_platform import PlatformMailProfile

        profile = PlatformMailProfile(
            "smtp.example.org", 587, "starttls", "imap.example.org", 993, "ssl"
        )
        return platform_access(profile, address, MAILBOX_PASSWORD, from_name="Revansa")

    def test_the_sender_is_always_the_assigned_address(self):
        access = self.access("one@mail.example.org")
        assert access.smtp.from_address == access.smtp.username == "one@mail.example.org"
        assert access.imap.username == "one@mail.example.org"
        assert access.smtp.host == "smtp.example.org" and access.imap.host == "imap.example.org"

    def test_sending_uses_the_assigned_login_and_never_prints_the_password(self):
        # The full send (headers, From identity, delivery) is covered in test_email_modes.
        access = self.access()
        assert access.smtp.password == MAILBOX_PASSWORD
        assert MAILBOX_PASSWORD not in repr(access)

    def test_receiving_reads_the_assigned_mailbox_only(self):
        seen = []

        class Connection:
            def login(self, u, p):
                seen.append((u, p))

            def select(self, *a, **k):
                return "OK", [b"0"]

            def uid(self, *a, **k):
                return "OK", [b""]

            def logout(self):
                pass

        access = self.access("two@mail.example.org")
        mailbox = ImapMailbox(access.imap, factory=lambda settings: Connection())
        assert mailbox.fetch(10) == []
        mailbox.close()
        assert seen == [("two@mail.example.org", MAILBOX_PASSWORD)]
