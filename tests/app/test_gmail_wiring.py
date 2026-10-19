"""The Gmail mailbox mode wired into the real `Application`, against doubles (no network, no real
Google, no real e-mail).

REAL: the Application over a migrated SQLite database, the Admin and Client HTTP routes, the secret
store, the Gmail service (`GmailMailService`), the OAuth state/PKCE logic, the XOAUTH2 SMTP/IMAP
adapters, the shared daily send cap (`_metered`), the observe-mode reader with its persistent
position, the scheduler, the Engine and the Sheets source.

SIMULATED: Google (token, refresh, revoke and userinfo endpoints: `FakeGoogle`), the SMTP and IMAP
servers (doubles at the socket's edge) and the model. Nothing here proves that Google accepts the
application or that Gmail accepts the login.
"""

import logging
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import commercial_support
from app_support import InlineExecutor, env_for, fake_builders
from commercial_support import Journey
from fastapi import HTTPException
from mail_server_double import ImapServerDouble, raw_mail
from sheets_support import TUNIS
from test_mail_channel import FakeSmtp

from sales_app.api.admin_clients import publish_client_settings
from sales_app.bootstrap import build_application
from sales_app.config.email_settings import parse_email_settings
from sales_app.gmail_wiring import (
    GmailMailService,
    SecretStoreGrantStore,
    redirect_uri_for,
)
from sales_app.persistence.llm_usage_rows import SqlLLMUsageStore
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore, client_scope
from sales_app.secret_store.cipher import generate_master_key
from sales_app.secret_store.factory import build_secret_store
from sales_app.sourcing.consent.files import open_file_registry
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_app.wiring import default_builders
from sales_app.wiring.providers import google_sheets
from sales_app.wiring.writeback import WritebackSupport, default_policies, sql_ledgers
from sales_core.contracts import ProspectId
from sales_integrations.google_oauth import (
    AccessGrant,
    GoogleReauthRequiredError,
    TokenGrant,
)

BASE = "/client/v1/channel/google"
CHANNEL = "/client/v1/channel"
ADMIN_INT = "/admin/v1/settings/integrations"
MAIL_SCOPE = "https://mail.google.com/ openid email"
CLIENT_ID = "123456789-abc.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-SECRET-client-secret"
STATE_KEY = "state-signing-key-0123456789"
ALPHA_ADDRESS = "alpha.sales@gmail.com"
BETA_ADDRESS = "beta.sales@gmail.com"
SECRETS_SEEN = ("1//SECRET-refresh", "ya29.", CLIENT_SECRET, STATE_KEY)


class FakeGoogle:
    """The Google OAuth endpoints, as the `GoogleOAuthPort`."""

    def __init__(self):
        self.address = ALPHA_ADDRESS
        self.scope = MAIL_SCOPE
        self.issued = 0
        self.refreshes = 0
        self.expires_in = 3600
        self.refresh_error = None
        self.revoked = []

    def authorization_url(self, *, state, code_challenge, login_hint=None):
        return f"https://accounts.example/auth?state={state}&cc={code_challenge}"

    def exchange_code(self, *, code, code_verifier):
        self.issued += 1
        return TokenGrant(
            f"ya29.first-{self.issued}", f"1//SECRET-refresh-{self.issued}", 3600, self.scope
        )

    def refresh(self, refresh_token):
        self.refreshes += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        return AccessGrant(f"ya29.access-{self.refreshes}", self.expires_in, self.scope)

    def revoke(self, token):
        self.revoked.append(token)

    def account_address(self, access_token):
        return self.address


class GmailSmtp(FakeSmtp):
    def __init__(self, world, address):
        super().__init__()
        self.world, self.address = world, address

    def auth(self, mechanism, authobject, **kw):
        self.world.auths.setdefault(self.address, []).append((mechanism, authobject()))

    def send_message(self, message):
        self.world.mails.setdefault(self.address, []).append(message)


class GmailImap(ImapServerDouble):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def authenticate(self, mechanism, authobject):
        self.tokens.append((mechanism, authobject(b"")))
        return "OK", [b"authenticated"]


def state_of(url):
    return url.split("state=")[1].split("&")[0]


class GmailJourney(Journey):
    """alpha and beta behind one application, e-mail through the Gmail service."""

    def __init__(self, tmp_path):
        self.google = FakeGoogle()
        self.mails = {}
        self.auths = {}
        self.inboxes = {}
        self.factory_calls = []
        super().__init__(tmp_path)

    def settings(self):
        return super().settings().model_copy(update={"public_base_url": "https://revansa.example"})

    def _oauth(self, **kw):
        self.factory_calls.append(kw)
        return self.google

    def _smtp(self, settings):
        assert settings.auth == "xoauth2" and settings.host == "smtp.gmail.com"
        return GmailSmtp(self, settings.username)

    def _imap(self, settings):
        assert settings.auth == "xoauth2" and settings.host == "imap.gmail.com"
        return self.inboxes.setdefault(settings.username, GmailImap())

    def _builders(self, settings):
        engine = self.engine
        base = default_builders(
            settings,
            operational_stores=lambda cid: SqlOperationalStore(SqlOperationalRows(engine, cid)),
            consent=lambda cid: open_file_registry(
                settings.secrets_dir, self.secrets, self.audit, cid
            ),
            writeback=WritebackSupport(
                enabled=True, open_ledger=sql_ledgers(engine), policies=default_policies()
            ),
            llm_usage=lambda cid: SqlLLMUsageStore(engine, cid),
            gmail=self.gmail,
        )
        fakes, self.made = fake_builders(self.clock)

        def build_source(config, instance):
            return google_sheets.build_prospect_source(
                self.sheets[(config.client_id, instance.id)],
                client_id=config.client_id,
                source_id=instance.id,
                settings=instance.settings,
                timezone=TUNIS,
            )

        return replace(
            base,
            llms=fakes.llms,
            channels={"email": base.channels["email"]},
            source_instances={"google_sheets": build_source},
        )

    def start(self):
        settings = self.settings()
        self.gmail = GmailMailService(
            secrets=self.secrets,
            clock=self.clock,
            public_base_url=lambda: settings.public_base_url,
            oauth_factory=self._oauth,
            smtp_factory=self._smtp,
            imap_factory=self._imap,
        )
        self.application = build_application(
            settings,
            env=env_for(*self.clients),
            builders=self._builders(settings),
            clock=self.clock,
            executor=InlineExecutor(),
            secret_store=self.secrets,
            secret_audit=self.audit,
            refresh_executor=InlineExecutor(),
            gmail_service=self.gmail,
        )

    # ---- helpers --------------------------------------------------------------------------

    def configure_google(self, root=None):
        root = root or self.admin()
        for key, value in (
            ("gmail.oauth_client_id", CLIENT_ID),
            ("gmail.oauth_client_secret", CLIENT_SECRET),
            ("gmail.oauth_state_key", STATE_KEY),
        ):
            assert root.put(f"{ADMIN_INT}/{key}", json={"value": value}).status_code == 200
        return root

    def set_cap(self, cid, cap, root=None):
        response = (root or self.admin()).put(
            f"/admin/v1/clients/{cid}/send-cap", json={"daily_cap": cap}
        )
        assert response.status_code == 200, response.text

    def connect(self, cid, address, browser=None):
        """The whole consent, as the browser does it. Returns where Google sent the person back."""
        browser = browser or self.browser(cid)
        self.google.address = address
        started = browser.post(f"{BASE}/start")
        assert started.status_code == 200, started.text
        url = started.json()["authorize_url"]
        back = browser.get(
            f"{BASE}/callback",
            params={"state": state_of(url), "code": "4/SECRET-code"},
            follow_redirects=False,
        )
        assert back.status_code == 302
        return back.headers["location"]

    def sent_to(self, address):
        return self.mails.get(address, [])

    def approve_all(self, browser):
        ids = [p["id"] for p in browser.get("/client/v1/approvals").json()["proposals"]]
        for action_id in ids:
            assert browser.post(f"/client/v1/approvals/{action_id}/approve").status_code == 200
        return ids

    def approve_when_proposed(self, browser, tries=12):
        """Let the agent plan (time passes in ten-minute ticks) and approve what it proposes."""
        for _ in range(tries):
            if self.approve_all(browser):
                return True
            self.tick()
        return False

    def conversation(self, cid, prospect):
        return self.store(cid).get_conversation(ProspectId(prospect)).messages

    def counter(self, cid):
        from datetime import date

        return SqlSendCounter(self.engine).used(cid, "email", date(2026, 1, 15))


@pytest.fixture
def world(tmp_path, monkeypatch):
    # Both clients' e-mail is the client's own Gmail (no server, no login, no password).
    monkeypatch.setattr(commercial_support, "mailbox_settings", lambda cid: {"mode": "gmail"})
    journey = GmailJourney(tmp_path)
    for cid in ("alpha", "beta"):  # the historical test password has no place in Gmail mode
        journey.secrets.delete(client_scope(cid), "email.password")
    journey.application.runtime_manager.refresh_client("alpha")
    journey.application.runtime_manager.refresh_client("beta")
    yield journey
    journey.close()


def ready_alpha(j, cap=5):
    j.configure_google()
    j.set_cap("alpha", cap)
    assert j.connect("alpha", ALPHA_ADDRESS).endswith("google=connected")
    browser = j.browser("alpha")
    started = browser.post("/client/v1/agent/start")
    assert started.status_code == 200, started.text
    return browser


# ---- the platform's side -------------------------------------------------------------------------


def test_admin_status_is_honest_configured_is_not_verified(world):
    root = world.admin()
    before = root.get(f"{ADMIN_INT}/gmail/status").json()
    assert before["configured"] is False and before["verified"] is False
    assert set(before["missing"]) == {
        "Google OAuth client id",
        "Google OAuth client secret",
        "Google OAuth state signing key",
    }
    world.configure_google(root)
    after = root.get(f"{ADMIN_INT}/gmail/status").json()
    assert after["configured"] is True and after["missing"] == []
    assert after["verified"] is False  # nothing was asked of Google
    assert after["redirect_uri"] == "https://revansa.example/client/v1/channel/google/callback"
    # the same Google client also serves "Connect Google Sheets": its callback must be shown too
    assert after["sheets_redirect_uri"] == (
        "https://revansa.example/client/v1/sheets-access/callback"
    )
    assert "Stored is not verified" in after["note"]
    listing = root.get(ADMIN_INT).text
    for secret in (CLIENT_ID, CLIENT_SECRET, STATE_KEY):
        assert secret not in listing and secret not in str(after)
    assert not world.factory_calls  # configured never called Google


def test_the_state_key_must_be_long_enough_and_the_client_cannot_write_a_grant(world):
    root = world.admin()
    short = root.put(f"{ADMIN_INT}/gmail.oauth_state_key", json={"value": "too-short"})
    assert short.status_code == 422 and "too-short" not in short.text
    for key in ("gmail.refresh_token", "gmail.account_address", "gmail.reauth_required"):
        assert (
            root.put(
                f"/admin/v1/clients/alpha/integrations/{key}", json={"value": "x" * 20}
            ).status_code
            == 404
        )


def test_without_the_platforms_google_application_the_client_waits(world):
    browser = world.browser("alpha")
    assert browser.get(BASE).json()["state"] == "unavailable"
    assert browser.post(f"{BASE}/start").status_code == 503
    channel = browser.get(CHANNEL).json()
    assert channel["status"] == "waiting_platform"
    assert (
        browser.get(
            f"{BASE}/callback", params={"state": "x", "code": "c"}, follow_redirects=False
        ).status_code
        == 302
    )


def test_redirect_uri_is_exact_and_https_only():
    assert (
        redirect_uri_for("https://r.example")
        == "https://r.example/client/v1/channel/google/callback"
    )
    assert (
        redirect_uri_for("https://r.example/app/")
        == "https://r.example/app/client/v1/channel/google/callback"
    )
    assert (
        redirect_uri_for("http://localhost:8000")
        == "http://localhost:8000/client/v1/channel/google/callback"
    )
    for bad in (
        None,
        "",
        "http://r.example",
        "ftp://r.example",
        "https://u:p@r.example",
        "https://r.example?x=1",
    ):
        assert redirect_uri_for(bad) is None


# ---- the journey ---------------------------------------------------------------------------------


def test_a_gmail_client_goes_from_consent_to_a_reply_in_the_engine(world, caplog):
    caplog.set_level(logging.DEBUG)
    j = world
    j.configure_google()
    browser = j.browser("alpha")

    # not connected: the client has something to do, and there is no password to type anywhere
    assert browser.get(BASE).json()["state"] == "not_connected"
    assert browser.get(CHANNEL).json()["status"] == "action_required"
    j.set_cap("alpha", 5)
    location = j.connect("alpha", ALPHA_ADDRESS, browser)
    assert location == "/client/channel?google=connected"  # nothing in the URL
    assert (
        j.factory_calls[-1]["redirect_uri"]
        == "https://revansa.example/client/v1/channel/google/callback"
    )

    status = browser.get(BASE).json()
    assert (status["state"], status["address"], status["send_ready"]) == (
        "connected",
        ALPHA_ADDRESS,
        True,
    )
    channel = browser.get(CHANNEL).json()
    assert channel["status"] == "ready" and channel["email"]["mode"] == "gmail"
    assert j.application.registry.get("alpha") is not None  # the connection made the agent runnable
    assert browser.post("/client/v1/agent/start").status_code == 200

    j.tick()  # the first read sets the position at the end of the mailbox: no history is touched
    j.add_prospect("alpha", "sheet_a", "p1", "Ada", email="ada@example.com")
    assert j.approve_when_proposed(browser)
    j.tick()
    (mail,) = j.sent_to(ALPHA_ADDRESS)
    assert mail["To"] == "ada@example.com" and ALPHA_ADDRESS in mail["From"]
    mechanism, initial = j.auths[ALPHA_ADDRESS][0]
    assert (
        mechanism == "XOAUTH2"
        and f"user={ALPHA_ADDRESS}" in initial
        and "Bearer ya29.access-" in initial
    )
    assert j.counter("alpha") == 1  # counted once: the wiring counts, the adapter does not

    # the prospect answers: the message reaches the Engine through the persistent position, once
    inbox = j.inboxes[ALPHA_ADDRESS]
    inbox.deliver(raw_mail("ada@example.com", "Yes, tell me more.", message_id="ada-1@mail.x"))
    inbox.deliver(raw_mail("friend@private.example", "my private plans", message_id="fr-1@mail.x"))
    j.tick()
    texts = [(m.direction.value, m.text) for m in j.conversation("alpha", "sheet_a:p1")]
    assert texts[-1] == ("inbound", "Yes, tell me more.")
    assert inbox.commands("STORE") == []  # a person's mailbox is never written to
    assert not any(inbox.seen_flags().values())
    assert 2 not in inbox.bodies_served  # a stranger's message body never left the server
    count = len(j.conversation("alpha", "sheet_a:p1"))
    for _ in range(3):
        j.tick()
    assert len(j.conversation("alpha", "sheet_a:p1")) == count  # a new poll duplicates nothing

    # an access token that expired is renewed, the answer leaves, and is counted once more
    j.clock.advance(timedelta(hours=2))
    assert j.approve_when_proposed(browser)
    j.tick()
    assert len(j.sent_to(ALPHA_ADDRESS)) == 2 and j.google.refreshes >= 2
    assert j.auths[ALPHA_ADDRESS][0][1] != j.auths[ALPHA_ADDRESS][-1][1]
    assert j.counter("alpha") == 2

    # a restart forgets the process, not the grant: still connected, nothing read or sent twice
    j.restart()
    browser = j.browser("alpha")
    again = browser.get(BASE).json()
    assert (again["state"], again["address"]) == ("connected", ALPHA_ADDRESS)
    for _ in range(3):
        j.tick()
    assert len(j.sent_to(ALPHA_ADDRESS)) == 2
    assert len(j.conversation("alpha", "sheet_a:p1")) >= count

    # disconnecting: revoked at Google, forgotten here, and nothing is read or sent any more
    opened = len(inbox.tokens)
    done = browser.post(f"{BASE}/disconnect").json()
    assert done["state"] == "not_connected" and done["send_ready"] is False
    assert j.google.revoked and j.google.revoked[-1].startswith("1//SECRET-refresh")
    assert j.application.registry.missing("alpha") == (
        "Email Gmail is not connected yet",
    )  # not run
    for key in ("gmail.refresh_token", "gmail.account_address", "gmail.reauth_required"):
        assert j.secrets.get(client_scope("alpha"), key) is None
    inbox.deliver(raw_mail("ada@example.com", "Hello again?", message_id="ada-2@mail.x"))
    for _ in range(3):
        j.tick()
    assert len(inbox.tokens) == opened and len(j.sent_to(ALPHA_ADDRESS)) == 2
    assert browser.get(CHANNEL).json()["status"] == "action_required"

    # no secret anywhere it should not be
    assert not any(s in caplog.text for s in SECRETS_SEEN)


def test_nothing_is_sent_without_a_cap_and_resumes_when_it_is_set(world):
    j = world
    browser = ready_alpha(j)
    j.tick()
    j.add_prospect("alpha", "sheet_a", "p1", "Ada", email="ada@example.com")
    assert j.approve_when_proposed(browser)
    SqlSendCapStore(j.engine).remove("alpha")  # no cap, no default, no implicit unlimited
    j.tick()
    assert j.sent_to(ALPHA_ADDRESS) == [] and j.auths.get(ALPHA_ADDRESS) is None
    assert browser.get(BASE).json()["send_ready"] is False
    j.set_cap("alpha", 3)
    j.tick()
    assert len(j.sent_to(ALPHA_ADDRESS)) == 1 and j.counter("alpha") == 1


def test_a_cap_reached_stops_sending_for_the_day(world):
    j = world
    browser = ready_alpha(j, cap=1)
    j.tick()
    j.add_prospect("alpha", "sheet_a", "p1", "Ada", email="ada@example.com")
    j.add_prospect("alpha", "sheet_a", "p3", "Cy", email="cy@example.com")
    assert j.approve_when_proposed(browser)
    j.tick()
    j.tick()
    assert len(j.sent_to(ALPHA_ADDRESS)) == 1 and j.counter("alpha") == 1


def test_a_revoked_authorisation_stops_everything_until_the_client_reconnects(world):
    j = world
    browser = ready_alpha(j)
    j.tick()
    j.add_prospect("alpha", "sheet_a", "p1", "Ada", email="ada@example.com")
    assert j.approve_when_proposed(browser)
    j.google.refresh_error = GoogleReauthRequiredError("invalid_grant")  # revoked at Google's side
    j.clock.advance(timedelta(hours=2))  # the cached access token has expired: Google is asked
    j.tick()
    assert j.sent_to(ALPHA_ADDRESS) == []
    status = browser.get(BASE).json()
    assert status["state"] == "reauth_required" and status["send_ready"] is False
    assert "Reconnect" in status["message"]
    channel = browser.get(CHANNEL).json()
    assert channel["status"] == "action_required"
    assert j.secrets.get(client_scope("alpha"), "gmail.reauth_required") == "1"  # persistent
    j.restart()
    assert j.browser("alpha").get(BASE).json()["state"] == "reauth_required"
    # reconnecting replaces the grant, clears the mark, and sending resumes
    j.google.refresh_error = None
    assert j.connect("alpha", ALPHA_ADDRESS).endswith("google=connected")
    assert j.secrets.get(client_scope("alpha"), "gmail.reauth_required") is None
    j.tick()
    j.tick()
    assert len(j.sent_to(ALPHA_ADDRESS)) == 1


def test_a_consent_without_the_mail_permission_keeps_nothing(world):
    j = world
    j.configure_google()
    j.google.scope = "openid email"
    location = j.connect("alpha", ALPHA_ADDRESS)
    assert location.endswith("google=scope")
    assert j.browser("alpha").get(BASE).json()["state"] == "not_connected"
    assert j.secrets.get(client_scope("alpha"), "gmail.refresh_token") is None
    assert j.google.revoked  # what Google had just issued is given back


# ---- isolation and exclusivity -------------------------------------------------------------------


def test_two_clients_never_see_or_share_a_grant(world):
    j = world
    j.configure_google()
    assert j.connect("alpha", ALPHA_ADDRESS).endswith("connected")
    alpha_token = j.secrets.get(client_scope("alpha"), "gmail.refresh_token")
    beta = j.browser("beta")
    assert beta.get(BASE).json()["state"] == "not_connected"
    assert beta.get(BASE).json()["address"] is None
    assert j.secrets.get(client_scope("beta"), "gmail.refresh_token") is None
    # the same Gmail cannot be connected to a second account; what Google issued is revoked
    assert j.connect("beta", ALPHA_ADDRESS, beta).endswith("google=in_use")
    assert beta.get(BASE).json()["state"] == "not_connected"
    assert j.google.revoked and alpha_token not in j.google.revoked
    assert j.connect("beta", BETA_ADDRESS, beta).endswith("connected")
    assert beta.get(BASE).json()["address"] == BETA_ADDRESS
    assert j.browser("alpha").get(BASE).json()["address"] == ALPHA_ADDRESS
    assert j.secrets.get(client_scope("alpha"), "gmail.refresh_token") == alpha_token


def test_a_state_made_for_another_client_is_refused(world):
    j = world
    j.configure_google()
    url = j.browser("alpha").post(f"{BASE}/start").json()["authorize_url"]
    back = j.browser("beta").get(
        f"{BASE}/callback", params={"state": state_of(url), "code": "c"}, follow_redirects=False
    )
    assert back.headers["location"].endswith("google=invalid")
    assert j.browser("beta").get(BASE).json()["state"] == "not_connected"


def test_gmail_and_a_revansa_mailbox_exclude_each_other_on_the_server(world):
    j = world
    j.configure_google()
    root, alpha = j.admin(), j.browser("alpha")

    # a mailbox Revansa provides is assigned: Gmail cannot start, whatever the screen says
    j.secrets.set(
        client_scope("beta"), "email.platform_mailbox", "beta@mail.example.org", by="test"
    )
    beta = j.browser("beta")
    assert beta.post(f"{BASE}/start").status_code == 409
    version = beta.get(CHANNEL).json()["version"]
    # going dedicated first (no grant yet) is allowed; going back to Gmail with the mailbox assigned is not
    assert (
        beta.put(
            CHANNEL,
            json={"expected_version": version, "channel": "email", "email": {"use_gmail": False}},
        ).status_code
        == 200
    )
    version = beta.get(CHANNEL).json()["version"]
    refused = beta.put(
        CHANNEL,
        json={"expected_version": version, "channel": "email", "email": {"use_gmail": True}},
    )
    assert refused.status_code == 409 and "cannot also use Gmail" in refused.text

    # a connected Gmail: the dedicated mode, directly through the Admin settings route or the Client
    # route, and a dedicated mailbox credential, are all refused
    assert j.connect("alpha", ALPHA_ADDRESS, alpha).endswith("connected")
    version = alpha.get(CHANNEL).json()["version"]
    back = alpha.put(
        CHANNEL,
        json={"expected_version": version, "channel": "email", "email": {"use_gmail": False}},
    )
    assert back.status_code == 409 and "disconnect" in back.text.lower()
    current = j.application.config_store.current("alpha")
    with pytest.raises(HTTPException) as caught:
        publish_client_settings(
            j.application,
            "alpha",
            {"channel": {"provider": "email", "settings": {"mode": "platform", "from_name": "A"}}},
            expected_version=current.version if current else None,
            author="root",
            top_keys=(),
            sections={"channel": ("provider", "settings")},
        )
    assert caught.value.status_code == 409
    for key in ("email.platform_mailbox", "email.password"):
        value = "alpha@mail.example.org" if key.endswith("mailbox") else "a-password-0123"
        response = root.put(f"/admin/v1/clients/alpha/integrations/{key}", json={"value": value})
        assert response.status_code == 409, key
    assert alpha.get(BASE).json()["state"] == "connected"  # nothing above disturbed the grant


def test_a_client_cannot_name_a_server_a_login_or_a_password_for_gmail(world):
    j = world
    alpha = j.browser("alpha")
    version = alpha.get(CHANNEL).json()["version"]
    for field in ("smtp_host", "username", "password", "access_token", "mode", "refresh_token"):
        response = alpha.put(
            CHANNEL, json={"expected_version": version, "channel": "email", "email": {field: "x"}}
        )
        assert response.status_code == 422, field
    assert alpha.post(f"{BASE}/start", json={"password": "hunter2"}).status_code in (200, 503)
    for model in (
        {"mode": "gmail", "smtp_host": "smtp.example.org"},
        {"mode": "gmail", "password": "x"},
    ):
        with pytest.raises(ValueError):
            parse_email_settings(model)
    assert parse_email_settings({"mode": "gmail", "from_name": "Acme"}).subject


# ---- the grant store -----------------------------------------------------------------------------


def test_the_grant_is_encrypted_at_rest_and_survives_a_new_store(tmp_path):
    key = generate_master_key()
    first = SecretStoreGrantStore(build_secret_store(tmp_path, key))
    first.save("alpha", "1//SECRET-refresh-xyz", ALPHA_ADDRESS)
    on_disk = (tmp_path / "secrets.json").read_text(encoding="utf-8")
    assert "1//SECRET-refresh-xyz" not in on_disk and ALPHA_ADDRESS not in on_disk
    second = SecretStoreGrantStore(build_secret_store(tmp_path, key))
    held = second.load("alpha")
    assert held.refresh_token == "1//SECRET-refresh-xyz" and held.address == ALPHA_ADDRESS
    assert "1//SECRET" not in repr(held) and second.load("beta") is None
    second.mark_reauth_required("alpha")
    assert SecretStoreGrantStore(build_secret_store(tmp_path, key)).load("alpha").reauth_required
    second.save("alpha", "1//SECRET-refresh-new", ALPHA_ADDRESS)  # reconnecting clears the mark
    assert not second.load("alpha").reauth_required
    second.delete("alpha")
    assert second.load("alpha") is None


def test_the_service_is_closed_when_not_configured():
    service = GmailMailService(secrets=InMemorySecretStore(), clock=None)
    assert service.configured() is False and "public base URL (https)" in service.missing()
    store = InMemorySecretStore()
    for key, value in (
        ("gmail.oauth_client_id", CLIENT_ID),
        ("gmail.oauth_client_secret", CLIENT_SECRET),
        ("gmail.oauth_state_key", STATE_KEY),
    ):
        store.set(GLOBAL_SCOPE, key, value, by="test")
    assert GmailMailService(secrets=store, clock=None).configured() is False  # no public URL
    assert GmailMailService(
        secrets=store, clock=None, public_base_url=lambda: "https://r.example"
    ).configured()
