"""E-mail, when is it really usable: who a message is from, and what the client is told.

- **Sender name**: the client's commercial name (`display_name`), never a global setting and never
  something a stored document says in platform mode.
- **Sender address**: the mailbox assigned to that client, never another's.
- **Client status**: "ready" only while both logins of the client's own mailbox were accepted with
  the settings as they are now; nothing technical is ever said to the client.

Servers are doubles: nothing here proves that a real provider accepts these logins.
"""

import imaplib
import smtplib
from types import SimpleNamespace
from typing import ClassVar

import pytest
from test_email_modes import (
    ADDRESS,
    MAIL,
    NOW,
    PASSWORD,
    PLATFORM,
    FakeImap,
    FakeSmtp,
    assign,
    custom_client,
    load,
    new_store,
    outbound,
    platform_client,
)

from sales_app.channel_status import READY, WAITING_PLATFORM, channel_status
from sales_app.email_platform import AUTHENTICATED, AuthLedger, MailLoginProbe
from sales_app.mailbox_access import resolve_mailbox, sender_name
from sales_app.mailbox_proof import (
    NOT_AUTHENTICATED,
    mailbox_state,
    verify_all,
    verify_client_mailbox,
)
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    CredentialService,
    InMemorySecretAudit,
    client_scope,
)
from sales_app.wiring.providers import email as email_provider

DOCUMENT = {"channel": {"provider": "email", "settings": PLATFORM}}
UNCONFIRMED = {"channel": {"provider": "email", "settings": {"mode": "platform"}}}
BETA = "beta@mail.example.org"
BETA_PASSWORD = "Beta-pass-9981"


class Servers:
    """Both servers: accept the logins they are told to, count every login attempt."""

    def __init__(self):
        self.accepted: set[tuple[str, str]] = set()
        self.attempts: list[tuple[str, str, str]] = []

    def smtp(self, settings):
        outer = self

        class Connection:
            def login(self, user, password):
                outer.attempts.append(("smtp", user, password))
                if (user, password) not in outer.accepted:
                    raise smtplib.SMTPAuthenticationError(535, b"no")

            def quit(self):
                pass

        return Connection()

    def imap(self, settings):
        outer = self

        class Connection:
            def login(self, user, password):
                outer.attempts.append(("imap", user, password))
                if (user, password) not in outer.accepted:
                    raise imaplib.IMAP4.error("no")

            def select(self, *a, **k):
                return "OK", [b"0"]

            def uid(self, *a, **k):
                return "OK", [b""]

            def logout(self):
                pass

        return Connection()

    def probe(self, smtp, imap):
        return MailLoginProbe(smtp, imap, smtp_factory=self.smtp, imap_factory=self.imap)


def application(store, configs=()):
    return SimpleNamespace(
        channel_policy=SimpleNamespace(
            effective=lambda client_id: frozenset({"whatsapp", "email"})
        ),
        credentials=CredentialService(store, InMemorySecretAudit(), now=lambda: NOW),
        mail_auth=AuthLedger(),
        registry=SimpleNamespace(configs=lambda: list(configs)),
    )


def status_of(app, cid="alpha"):
    return channel_status(app, cid, "email", DOCUMENT)


@pytest.fixture
def two_clients(tmp_path):
    store = new_store()
    assign(store, "alpha")
    assign(store, "beta", BETA, BETA_PASSWORD)
    loaded = load(
        tmp_path,
        store,
        ("alpha", {**UNCONFIRMED, "display_name": "Alpha Shop"}),
        ("beta", {**UNCONFIRMED, "display_name": "Beta & Co"}),
    )
    return store, {c.client_id: c for c in loaded.clients}


class TestTheSenderName:
    """The Sender Name is the client's own, confirmed setting; its commercial name is only the
    proposal used until one is confirmed."""

    def test_without_a_confirmed_name_the_commercial_name_is_used(self, two_clients):
        _, configs = two_clients
        for cid, address, name in (("alpha", ADDRESS, "Alpha Shop"), ("beta", BETA, "Beta & Co")):
            server = FakeSmtp()
            channel = email_provider.build_channel(
                configs[cid], smtp_factory=lambda settings, s=server: s
            )
            channel.send(outbound())
            (sent,) = server.sent
            assert sent["From"] == f"{name} <{address}>"

    def test_a_confirmed_name_is_used_and_stays_when_the_commercial_name_changes(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        settings = {"mode": "platform", "from_name": "Alpha Boutique"}
        (config,) = load(
            tmp_path,
            store,
            (
                "alpha",
                {
                    "channel": {"provider": "email", "settings": settings},
                    "display_name": "Alpha Shop",
                },
            ),
        ).clients
        assert resolve_mailbox(config).smtp.from_name == "Alpha Boutique"
        (renamed,) = load(
            tmp_path,
            store,
            (
                "alpha",
                {
                    "channel": {"provider": "email", "settings": settings},
                    "display_name": "Alpha Group",
                },
            ),
        ).clients
        assert renamed.spec.display_name == "Alpha Group"
        assert resolve_mailbox(renamed).smtp.from_name == "Alpha Boutique"

    def test_each_client_uses_its_own_confirmed_name(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        assign(store, "beta", BETA, BETA_PASSWORD)
        loaded = load(
            tmp_path,
            store,
            (
                "alpha",
                {
                    "channel": {
                        "provider": "email",
                        "settings": {"mode": "platform", "from_name": "Alpha Boutique"},
                    }
                },
            ),
            ("beta", UNCONFIRMED),
        )
        names = {c.client_id: resolve_mailbox(c).smtp.from_name for c in loaded.clients}
        assert names == {"alpha": "Alpha Boutique", "beta": "Beta"}

    def test_a_historical_mailbox_keeps_its_own_from_name(self, tmp_path):
        store = new_store()
        store.set(client_scope("alpha"), "email.password", PASSWORD, by="alpha")
        (config,) = load(tmp_path, store, ("alpha", custom_client())).clients
        assert resolve_mailbox(config).smtp.from_name is None
        named = {"channel": {"provider": "email", "settings": {**MAIL, "from_name": "Custom Name"}}}
        (config,) = load(tmp_path, store, ("alpha", named)).clients
        assert resolve_mailbox(config).smtp.from_name == "Custom Name"

    def test_there_is_no_global_sender_name_to_set(self):
        from sales_app.secret_store.catalog import CATALOG

        assert not [s.key for s in CATALOG if "from_name" in s.key or "FROM_NAME" in s.key]

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Alpha Shop", "Alpha Shop"),
            ("  Alpha \n Shop  ", "Alpha Shop"),
            ('Evil <evil@x.org>, "boss"', "Evil evil@x.org boss"),
            ("Café Müller", "Café Müller"),
            ('<>",;', None),
            ("", None),
            (None, None),
        ],
    )
    def test_the_name_is_made_safe_for_a_header(self, raw, expected):
        assert sender_name(raw) == expected

    def test_the_name_is_bounded(self):
        assert len(sender_name("x" * 300)) == 80


class TestTheSenderAddress:
    def test_each_client_sends_and_reads_only_as_its_own_mailbox(self, two_clients):
        _, configs = two_clients
        alpha, beta = resolve_mailbox(configs["alpha"]), resolve_mailbox(configs["beta"])
        assert alpha.smtp.from_address == alpha.smtp.username == alpha.imap.username == ADDRESS
        assert beta.smtp.from_address == beta.smtp.username == beta.imap.username == BETA
        assert (alpha.smtp.password, beta.smtp.password) == (PASSWORD, BETA_PASSWORD)
        assert PASSWORD not in repr(beta) and BETA_PASSWORD not in repr(alpha)

    def test_the_inbox_of_a_client_logs_in_as_its_own_mailbox(self, two_clients):
        _, configs = two_clients
        server = FakeImap()
        inbox = email_provider.build_inbox(configs["beta"], imap_factory=lambda s: server)
        inbox.fetch()
        inbox.close()
        assert server.logins == [(BETA, BETA_PASSWORD)]

    def test_an_address_cannot_be_assigned_to_two_clients(self, two_clients):
        store, _ = two_clients
        from sales_app.secret_store import DuplicateSecretError

        with pytest.raises(DuplicateSecretError):
            store.set(
                client_scope("beta"), "email.platform_mailbox", ADDRESS, by="root", unique=True
            )

    def test_without_an_assigned_mailbox_nothing_is_sent_as_anyone(self, tmp_path):
        store = new_store()
        (config,) = load(tmp_path, store, ("alpha", platform_client())).clients
        with pytest.raises(ValueError):
            resolve_mailbox(config)


class TestWhatTheClientIsTold:
    def test_stored_fields_alone_are_not_ready(self, two_clients):
        store, _ = two_clients
        app = application(store)
        status, message = status_of(app)
        assert status == WAITING_PLATFORM and "setting up" in message

    def test_ready_only_after_both_logins_were_accepted(self, two_clients):
        store, _ = two_clients
        app, servers = application(store), Servers()
        servers.accepted.add((ADDRESS, PASSWORD))
        verify_client_mailbox(app, "alpha", servers.probe)
        assert status_of(app) == (READY, None)
        assert {p for p, _, _ in servers.attempts} == {"smtp", "imap"}

    def test_a_refused_login_is_not_ready_and_says_nothing_technical(self, two_clients):
        store, _ = two_clients
        app, servers = application(store), Servers()  # accepts nothing
        verify_client_mailbox(app, "alpha", servers.probe)
        status, message = status_of(app)
        assert status == WAITING_PLATFORM
        for leaked in (ADDRESS, PASSWORD, "smtp", "imap", "login", "password"):
            assert leaked not in message

    def test_one_protocol_alone_is_not_enough(self, two_clients):
        store, _ = two_clients
        app, servers = application(store), Servers()
        servers.accepted.add((ADDRESS, PASSWORD))
        probe = servers.probe

        class SmtpOnly:
            def __init__(self, smtp, imap):
                self.inner = probe(smtp, imap)

            def check_smtp(self):
                self.inner.check_smtp()

            def check_imap(self):
                from sales_app.connection_tests import MailProbeFailure

                raise MailProbeFailure("unreachable")

        verify_client_mailbox(app, "alpha", SmtpOnly)
        assert app.mail_auth.state("client:alpha:smtp", _print(app)) == AUTHENTICATED
        assert status_of(app)[0] == WAITING_PLATFORM

    def test_changing_the_password_voids_the_proof(self, two_clients):
        store, _ = two_clients
        app, servers = application(store), Servers()
        servers.accepted.add((ADDRESS, PASSWORD))
        verify_client_mailbox(app, "alpha", servers.probe)
        assert status_of(app)[0] == READY
        store.set(client_scope("alpha"), "email.password", "new-password-123", by="root")
        assert status_of(app)[0] == WAITING_PLATFORM

    def test_removing_a_server_setting_voids_readiness(self, two_clients):
        store, _ = two_clients
        app, servers = application(store), Servers()
        servers.accepted.add((ADDRESS, PASSWORD))
        verify_client_mailbox(app, "alpha", servers.probe)
        store.delete(GLOBAL_SCOPE, "email.platform_imap_host")
        assert status_of(app)[0] == WAITING_PLATFORM

    def test_the_proof_of_one_client_is_not_the_proof_of_another(self, two_clients):
        store, _ = two_clients
        app, servers = application(store), Servers()
        servers.accepted.add((ADDRESS, PASSWORD))  # beta's login is not accepted
        verify_client_mailbox(app, "alpha", servers.probe)
        verify_client_mailbox(app, "beta", servers.probe)
        assert status_of(app, "alpha")[0] == READY
        assert status_of(app, "beta")[0] == WAITING_PLATFORM
        beta_logins = {(u, p) for _, u, p in servers.attempts if u == BETA}
        assert beta_logins == {(BETA, BETA_PASSWORD)}  # never alpha's password with beta's address

    def test_an_incomplete_mailbox_attempts_no_login(self, tmp_path):
        store = new_store()
        app, servers = application(store), Servers()
        _, results = verify_client_mailbox(app, "alpha", servers.probe)
        assert results == [] and servers.attempts == []
        assert mailbox_state(app, "alpha") == "mailbox_not_assigned"


class TestCheckingAgainstAfterARestart:
    def test_every_unproven_platform_mailbox_is_checked_once(self, two_clients):
        store, configs = two_clients
        app, servers = application(store, configs.values()), Servers()
        servers.accepted |= {(ADDRESS, PASSWORD), (BETA, BETA_PASSWORD)}
        verify_all(app, servers.probe)
        assert status_of(app, "alpha")[0] == status_of(app, "beta")[0] == READY
        before = len(servers.attempts)
        verify_all(app, servers.probe)  # already proven: no new login
        assert len(servers.attempts) == before

    def test_a_failing_mailbox_does_not_stop_the_others(self, two_clients):
        store, configs = two_clients
        app, servers = application(store, configs.values()), Servers()
        servers.accepted.add((BETA, BETA_PASSWORD))  # alpha is refused
        verify_all(app, servers.probe)
        assert mailbox_state(app, "alpha") == NOT_AUTHENTICATED
        assert mailbox_state(app, "beta") == "ready"

    def test_a_client_that_is_not_on_a_platform_mailbox_is_left_alone(self, tmp_path):
        store = new_store()
        (config,) = load(tmp_path, store, ("alpha", {})).clients
        app, servers = application(store, [config]), Servers()
        verify_all(app, servers.probe)
        assert servers.attempts == []


def _print(app):
    from sales_app.mailbox_proof import client_mailbox

    return client_mailbox(app, "alpha").print_


class TestTheRuntimeWaitsForTheSenderName:
    """A client is not built or run (so nothing is sent) until its Sender Name is confirmed."""

    CONFIRMED: ClassVar[dict] = {
        "channel": {
            "provider": "email",
            "settings": {"mode": "platform", "from_name": "Alpha Boutique"},
        }
    }

    def test_a_platform_client_without_a_confirmed_name_is_not_ready(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        (config,) = load(tmp_path, store, ("alpha", UNCONFIRMED)).clients
        assert not config.ready
        assert "Email sender name has not been confirmed yet" in config.missing_credentials

    def test_once_confirmed_it_is_ready(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        (config,) = load(tmp_path, store, ("alpha", self.CONFIRMED)).clients
        assert config.ready and config.missing_credentials == ()

    def test_a_blank_name_is_not_a_confirmation(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        blank = {
            "channel": {"provider": "email", "settings": {"mode": "platform", "from_name": "  "}}
        }
        (config,) = load(tmp_path, store, ("alpha", blank)).clients
        assert not config.ready

    def test_a_historical_mailbox_needs_no_name(self, tmp_path):
        store = new_store()
        store.set(client_scope("alpha"), "email.password", PASSWORD, by="alpha")
        (config,) = load(tmp_path, store, ("alpha", custom_client())).clients
        assert config.ready
        assert not any("sender name" in m.lower() for m in config.missing_credentials)

    def test_the_client_is_told_to_confirm_it(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        app = application(store)
        status, message = channel_status(app, "alpha", "email", UNCONFIRMED)
        assert status == "action_required" and "Confirm your sender name" in message
