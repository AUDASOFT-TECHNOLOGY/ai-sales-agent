"""Admin → Settings → Email: the servers of the mailboxes Revansa provides (step 5c).

Covers: the seven global settings (listed, validated, shown because none is secret, global scope
only, written by an owner only); and the connection check (it needs a complete profile, opens an
encrypted connection to each server and closes it, and **never logs in and never sends or reads a
message**; a host that is not public is refused before any connection is made).

The mail servers are doubles: nothing here proves that a real server (OVH or another) can be
reached. That is a separate manual check.
"""

import pytest
from test_admin_integrations_api import Fixture, put, user

from sales_app.admin_auth import AdminRole
from sales_app.api import create_app
from sales_app.email_platform import PlatformMailProbe, read_profile
from sales_integrations.mail import MailHostRefusedError, MailUnavailableError

BASE = "/admin/v1/settings/integrations"
TEST = f"{BASE}/email/test"

PROFILE = {
    "email.platform_domain": "mail.example.org",
    "email.platform_smtp_host": "smtp.example.org",
    "email.platform_smtp_port": "587",
    "email.platform_smtp_security": "starttls",
    "email.platform_imap_host": "imap.example.org",
    "email.platform_imap_port": "993",
    "email.platform_imap_security": "ssl",
}


class FakeConnection:
    """What `open_smtp` / `open_imap` return. Logging in or sending is a failure of the test."""

    def __init__(self, log):
        self._log = log

    def login(self, *_a, **_k):
        raise AssertionError("the check must never log in")

    def send_message(self, *_a, **_k):
        raise AssertionError("the check must never send a message")

    def uid(self, *_a, **_k):
        raise AssertionError("the check must never read a mailbox")

    def select(self, *_a, **_k):
        raise AssertionError("the check must never open a folder")

    def quit(self):
        self._log.append("quit")

    def logout(self):
        self._log.append("logout")


class Servers:
    """Stands for the two servers: records what was opened and closed, fails as told."""

    def __init__(self):
        self.opened: list[tuple[str, str, int, str]] = []
        self.closed: list[str] = []
        self.smtp_error: Exception | None = None
        self.imap_error: Exception | None = None
        self.built = 0

    def smtp(self, settings):
        self.opened.append(("smtp", settings.host, settings.port, settings.security))
        if self.smtp_error:
            raise self.smtp_error
        return FakeConnection(self.closed)

    def imap(self, settings):
        self.opened.append(("imap", settings.host, settings.port, settings.security))
        if self.imap_error:
            raise self.imap_error
        return FakeConnection(self.closed)

    def probe(self, profile):
        self.built += 1
        return PlatformMailProbe(profile, smtp_factory=self.smtp, imap_factory=self.imap)


@pytest.fixture
def servers():
    return Servers()


@pytest.fixture
def fx(tmp_path, servers):
    fixture = Fixture(tmp_path)
    fixture.users.create(user("root", AdminRole.OWNER))
    fixture.users.create(user("olivia", AdminRole.OPERATOR, frozenset({"alpha"})))
    fixture.users.create(user("vera", AdminRole.VIEWER, frozenset({"alpha"})))
    # The same application, with the mail servers replaced by doubles.
    from fastapi.testclient import TestClient

    fixture.client = TestClient(
        create_app(fixture.application, run_scheduler=False, platform_mail_probe=servers.probe),
        base_url="https://testserver",
    )
    return fixture


def store_profile(client, **over):
    for key, value in {**PROFILE, **over}.items():
        if value is not None:
            assert put(client, f"{BASE}/{key}", value).status_code == 200, key


class TestTheSettings:
    def test_the_seven_settings_are_listed_global_unset_and_shown(self, fx):
        body = {c["key"]: c for c in fx.login("root").get(BASE).json()}
        for key in PROFILE:
            entry = body[key]
            assert entry["scope"] == "global" and entry["configured"] is False
            assert entry["sensitive"] is False and entry["value"] is None

    def test_an_owner_saves_them_and_they_are_shown_because_none_is_secret(self, fx):
        client = fx.login("root")
        store_profile(client)
        body = {c["key"]: c for c in client.get(BASE).json()}
        for key, value in PROFILE.items():
            assert body[key]["configured"] is True and body[key]["value"] == value, key

    @pytest.mark.parametrize("port", ["25", "465", "587", "2525"])
    def test_every_smtp_port_a_mail_server_uses_is_accepted(self, fx, port):
        assert put(fx.login("root"), f"{BASE}/email.platform_smtp_port", port).status_code == 200

    @pytest.mark.parametrize("port", ["143", "993"])
    def test_every_imap_port_a_mail_server_uses_is_accepted(self, fx, port):
        assert put(fx.login("root"), f"{BASE}/email.platform_imap_port", port).status_code == 200

    @pytest.mark.parametrize(
        "key,value,rule",
        [
            ("email.platform_smtp_port", "80", "25, 465, 587 or 2525"),
            ("email.platform_smtp_port", "993", "25, 465, 587 or 2525"),
            ("email.platform_smtp_port", "58 7", "25, 465, 587 or 2525"),
            ("email.platform_imap_port", "587", "143 or 993"),
            ("email.platform_imap_port", "144", "143 or 993"),
            ("email.platform_smtp_security", "plain", "starttls or ssl"),
            ("email.platform_imap_security", "none", "starttls or ssl"),
            ("email.platform_smtp_host", "bad host", "host name"),
            ("email.platform_smtp_host", "-x.example.org", "host name"),
            ("email.platform_smtp_host", "smtp.example.org/path", "host name"),
            ("email.platform_smtp_host", "ab", "host name"),
            ("email.platform_imap_host", "user@imap.example.org", "host name"),
            ("email.platform_domain", "nodot", "domain name"),
            ("email.platform_domain", "a b.example.org", "domain name"),
        ],
    )
    def test_a_bad_value_is_a_422_that_names_the_rule_and_is_not_echoed(self, fx, key, value, rule):
        response = put(fx.login("root"), f"{BASE}/{key}", value)
        assert response.status_code == 422
        assert rule in response.json()["detail"]
        assert value not in response.text
        assert fx.application.credentials.status("global", key).configured is False

    def test_hosts_may_be_typed_in_capitals(self, fx):
        assert (
            put(
                fx.login("root"), f"{BASE}/email.platform_smtp_host", "SMTP.Example.org"
            ).status_code
            == 200
        )

    @pytest.mark.parametrize("key", sorted(PROFILE))
    def test_none_of_them_exists_for_a_client(self, fx, key):
        client = fx.login("root")
        response = put(client, f"/admin/v1/clients/alpha/integrations/{key}", "smtp.example.org")
        assert response.status_code == 404
        listed = client.get("/admin/v1/clients/alpha/integrations").json()
        assert key not in {c["key"] for c in listed}

    @pytest.mark.parametrize("who", ["olivia", "vera"])
    def test_only_an_owner_writes_or_removes_them(self, fx, who):
        client = fx.login(who)
        url = f"{BASE}/email.platform_smtp_host"
        assert put(client, url, "smtp.example.org").status_code == 403
        assert client.delete(f"{url}?confirm=true").status_code == 403
        assert client.get(BASE).status_code == 200  # reading is open to any admin who can

    def test_removal_needs_confirmation_and_unsets_the_value(self, fx):
        client = fx.login("root")
        store_profile(client)
        url = f"{BASE}/email.platform_smtp_host"
        assert client.delete(url).status_code == 400
        assert client.delete(f"{url}?confirm=true").json()["configured"] is False

    def test_a_save_leaves_the_clients_mailbox_credential_alone(self, fx):
        client = fx.login("root")
        store_profile(client)
        listed = client.get("/admin/v1/clients/alpha/integrations").json()
        password = next(c for c in listed if c["key"] == "email.password")
        assert password["configured"] is False


class TestReadingTheProfile:
    def get_from(self, values):
        return lambda key: values.get(key)

    def test_a_complete_profile_is_read_with_numbers_and_lower_case_hosts(self):
        profile, missing = read_profile(
            self.get_from({**PROFILE, "email.platform_smtp_host": "SMTP.Example.org"})
        )
        assert missing == []
        assert (profile.smtp_host, profile.smtp_port, profile.smtp_security) == (
            "smtp.example.org",
            587,
            "starttls",
        )
        assert (profile.imap_host, profile.imap_port, profile.imap_security) == (
            "imap.example.org",
            993,
            "ssl",
        )

    def test_the_domain_is_not_needed_to_reach_the_servers(self):
        profile, missing = read_profile(self.get_from({**PROFILE, "email.platform_domain": None}))
        assert profile is not None and missing == []

    def test_what_is_missing_is_named_by_label(self):
        _, missing = read_profile(self.get_from({}))
        assert missing == [
            "SMTP host",
            "SMTP port",
            "SMTP security",
            "IMAP host",
            "IMAP port",
            "IMAP security",
        ]

    def test_a_stored_value_that_cannot_be_used_is_reported_not_guessed(self):
        profile, missing = read_profile(
            self.get_from(
                {**PROFILE, "email.platform_smtp_port": "80", "email.platform_imap_security": "x"}
            )
        )
        assert profile is None and missing == ["SMTP port", "IMAP security"]


class TestTheConnectionCheck:
    def test_it_is_for_owners_only(self, fx):
        assert fx.client.post(TEST).status_code == 401
        assert fx.login("olivia").post(TEST).status_code == 403
        assert fx.login("vera").post(TEST).status_code == 403

    def test_an_incomplete_profile_is_said_so_and_no_server_is_touched(self, fx, servers):
        client = fx.login("root")
        store_profile(client, **{"email.platform_imap_host": None})
        body = client.post(TEST).json()
        assert body["ok"] is False
        assert "not complete" in body["message"]
        assert body["details"] == {"missing": "IMAP host"}
        assert servers.built == 0 and servers.opened == []

    def test_a_working_profile_opens_and_closes_each_server_and_does_nothing_else(
        self, fx, servers
    ):
        client = fx.login("root")
        store_profile(client)
        response = client.post(TEST)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["details"] == {"smtp": "ok", "imap": "ok"}
        assert "No login was attempted and no message was sent" in body["message"]
        assert servers.opened == [
            ("smtp", "smtp.example.org", 587, "starttls"),
            ("imap", "imap.example.org", 993, "ssl"),
        ]
        # Closed, and never logged in to (the doubles fail the test if asked to log in or send).
        assert servers.closed == ["quit", "logout"]

    def test_the_answer_is_not_cacheable(self, fx):
        client = fx.login("root")
        store_profile(client)
        assert client.post(TEST).headers["cache-control"] == "no-store"

    def test_an_unreachable_server_is_a_fixed_sentence_and_the_other_is_still_checked(
        self, fx, servers
    ):
        client = fx.login("root")
        store_profile(client)
        servers.smtp_error = MailUnavailableError("smtp.example.org said: 421 secret-ish")
        body = client.post(TEST).json()
        assert body["ok"] is False
        assert body["details"] == {"smtp": "unreachable", "imap": "ok"}
        assert "SMTP: the server could not be reached" in body["message"]
        assert "secret-ish" not in str(body) and "421" not in str(body)
        assert servers.closed == ["logout"]  # nothing was open on the SMTP side

    def test_a_host_that_is_not_public_is_refused_in_a_fixed_sentence(self, fx, servers):
        client = fx.login("root")
        store_profile(client)
        servers.imap_error = MailHostRefusedError("10.0.0.5 is private")
        body = client.post(TEST).json()
        assert body["ok"] is False
        assert body["details"] == {"smtp": "ok", "imap": "refused"}
        assert "not a public mail server" in body["message"]
        assert "10.0.0.5" not in str(body)

    def test_both_failing_are_both_reported(self, fx, servers):
        client = fx.login("root")
        store_profile(client)
        servers.smtp_error = MailUnavailableError("x")
        servers.imap_error = MailUnavailableError("y")
        body = client.post(TEST).json()
        assert body["ok"] is False and body["details"] == {
            "smtp": "unreachable",
            "imap": "unreachable",
        }


class TestThePrivateAddressGuardStillApplies:
    """Through the real connection functions, with no double: a loopback address is refused by the
    host check before any connection is attempted (an address literal needs no name lookup)."""

    def test_a_loopback_host_is_refused_without_connecting(self, tmp_path):
        from fastapi.testclient import TestClient

        fixture = Fixture(tmp_path)
        fixture.users.create(user("root", AdminRole.OWNER))
        client = TestClient(
            create_app(fixture.application, run_scheduler=False), base_url="https://testserver"
        )
        client.post(
            "/admin/v1/auth/login",
            json={"username": "root", "password": "correct-horse-battery-staple"},
        )
        store_profile(
            client,
            **{"email.platform_smtp_host": "127.0.0.1", "email.platform_imap_host": "127.0.0.1"},
        )
        body = client.post(TEST).json()
        assert body["ok"] is False
        assert body["details"] == {"smtp": "refused", "imap": "refused"}
