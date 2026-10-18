"""`/client/v1/channel` and `/admin/v1/.../channels`: who may use which channel, and what a client says.

Covers: authentication and isolation between clients; the Admin's authority (the global list, a
client's exceptions, who may write them) and the client's choice only among what is allowed; what a
client may change (its channel and how its e-mails are worded) and what it may not (anything
technical -- servers, ports, logins, passwords, tokens, webhooks -- anything of WhatsApp or Meta, the
model, the mode); version conflicts and validation, with nothing written on a refusal and nothing
echoed back; that no route lets a client write, remove, test or connect a credential; and where the
channel stands (ready, waiting for the platform, action required) in words with nothing technical.

The mail servers are never contacted: nothing here proves a real server accepts the login.
"""

import contextlib
import json
import logging
from dataclasses import asdict
from typing import ClassVar

import pytest
from test_admin_clients_api import PASSWORD
from test_client_agent_api import AGENT, READINESS, START, World, check_ids

from sales_app.admin_auth import AdminRole
from sales_app.api.admin_clients import publish_client_settings
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    InMemorySecretAudit,
    InMemorySecretStore,
    client_scope,
)

CHANNEL = "/client/v1/channel"
GLOBAL = "/admin/v1/settings/channels"
SECRET = "S3cret-mail-pass!"
# What a client may say about its e-mails: nothing but wording.
MAIL = {"from_name": "Naga Sales", "subject": "About your enquiry"}
# What an administrator wires for a client that keeps a mailbox of its own (never typed by a client).
LEGACY_MAILBOX = {
    "from_address": "sales@example.org",
    "from_name": "Naga Sales",
    "username": "sales@example.org",
    "smtp_host": "smtp.example.org",
    "imap_host": "imap.example.org",
}


@pytest.fixture
def world(tmp_path):
    audit = InMemorySecretAudit()
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=audit)
    w.audit = audit
    yield w
    w.application.close()


@pytest.fixture
def alpha(world):
    return world.browser("alpha")


@pytest.fixture
def beta(world):
    return world.browser("beta")


@pytest.fixture
def admin(world):
    return world.admin()


def allow_email(admin, *, globally=True):
    body = {"allowed": ["whatsapp", "email"] if globally else ["whatsapp"]}
    response = admin.put(GLOBAL, json=body)
    assert response.status_code == 200, response.text
    return response


def choose(client, channel="email", settings=MAIL, expected="current"):
    if expected == "current":
        expected = client.get(CHANNEL).json()["version"]
    body = {"expected_version": expected, "channel": channel}
    if settings is not None:
        body["email"] = settings
    return client.put(CHANNEL, json=body)


def wire_legacy_mailbox(world, cid="alpha", *, password=True):
    """What the Admin does for a client with a mailbox of its own: the servers in its settings and
    the password in the credential store. The client does neither."""
    current = world.config_store.current(cid)
    publish_client_settings(
        world.application,
        cid,
        {"channel": {"provider": "email", "settings": LEGACY_MAILBOX}},
        expected_version=current.version if current else None,
        author="root",
        top_keys=(),
        sections={"channel": ("provider", "settings")},
    )
    if world.application.registry.get_config(cid) is not None:  # the running agent takes it
        with contextlib.suppress(Exception):
            world.application.runtime_manager.refresh_client(cid)
    if password:
        world.application.credentials.save(
            client_scope(cid), "email.password", SECRET, actor="root", apply=True
        )


def history(world, cid="alpha"):
    return world.config_store.history(cid)


# ---- authentication -------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "url"), [("get", CHANNEL), ("put", CHANNEL)])
def test_every_client_route_needs_a_client_session(world, method, url):
    response = getattr(world.anonymous(), method)(url, **({"json": {}} if method == "put" else {}))
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", GLOBAL),
        ("put", GLOBAL),
        ("get", "/admin/v1/clients/alpha/channels"),
        ("put", "/admin/v1/clients/alpha/channels"),
    ],
)
def test_every_admin_route_needs_an_admin_session(world, method, url):
    anonymous = world.anonymous()
    response = getattr(anonymous, method)(url, **({"json": {}} if method == "put" else {}))
    assert response.status_code == 401


def test_a_client_cannot_use_the_admin_routes(alpha):
    assert alpha.get(GLOBAL).status_code == 401
    assert alpha.put(GLOBAL, json={"allowed": ["email"]}).status_code == 401
    assert (
        alpha.put("/admin/v1/clients/alpha/channels", json={"added": ["email"]}).status_code == 401
    )


# ---- the Admin decides ----------------------------------------------------------------------


class TestTheAdminsDecision:
    def test_until_decided_only_whatsapp_is_allowed(self, admin):
        body = admin.get(GLOBAL).json()
        assert body["allowed"] == ["whatsapp"]
        assert {c["key"]: c["allowed"] for c in body["channels"]} == {
            "email": False,
            "telegram": False,
            "whatsapp": True,
        }

    def test_the_owner_sets_the_global_list(self, admin):
        allow_email(admin)
        assert admin.get(GLOBAL).json()["allowed"] == ["email", "whatsapp"]

    def test_none_is_a_decision_too(self, admin):
        response = admin.put(GLOBAL, json={"allowed": []})
        assert response.status_code == 200 and response.json()["allowed"] == []

    def test_an_unknown_channel_is_refused_and_nothing_changes(self, admin):
        assert (
            admin.put(GLOBAL, json={"allowed": ["whatsapp", "carrier_pigeon"]}).status_code == 422
        )
        assert admin.get(GLOBAL).json()["allowed"] == ["whatsapp"]

    def test_only_an_owner_may_write_it(self, world):
        world.users.create(_admin("eve", AdminRole.EDITOR))
        world.users.create(_admin("vera", AdminRole.VIEWER))
        for name, may_read in (("eve", True), ("vera", True)):
            client = world.anonymous()
            assert (
                client.post(
                    "/admin/v1/auth/login", json={"username": name, "password": PASSWORD}
                ).status_code
                == 200
            )
            assert client.get(GLOBAL).status_code == (200 if may_read else 403)
            assert client.put(GLOBAL, json={"allowed": ["email"]}).status_code == 403
            assert (
                client.put(
                    "/admin/v1/clients/alpha/channels", json={"added": ["email"]}
                ).status_code
                == 403
            )

    def test_a_client_exception_adds_or_removes_for_that_client_only(self, admin):
        response = admin.put("/admin/v1/clients/alpha/channels", json={"added": ["email"]})
        assert response.status_code == 200
        assert response.json()["effective"] == ["email", "whatsapp"]
        assert admin.get("/admin/v1/clients/beta/channels").json()["effective"] == ["whatsapp"]

    def test_a_refusal_wins_over_a_permission(self, admin):
        allow_email(admin)
        response = admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        assert response.json()["effective"] == ["whatsapp"]
        assert admin.get("/admin/v1/clients/beta/channels").json()["effective"] == [
            "email",
            "whatsapp",
        ]

    def test_added_and_removed_at_once_is_refused(self, admin):
        response = admin.put(
            "/admin/v1/clients/alpha/channels", json={"added": ["email"], "removed": ["email"]}
        )
        assert response.status_code == 422

    def test_an_empty_exception_clears_it(self, admin):
        admin.put("/admin/v1/clients/alpha/channels", json={"added": ["email"]})
        response = admin.put("/admin/v1/clients/alpha/channels", json={})
        assert response.json()["added"] == [] and response.json()["effective"] == ["whatsapp"]

    def test_a_client_that_does_not_exist_is_not_found(self, admin):
        assert admin.get("/admin/v1/clients/nobody/channels").status_code == 404
        assert admin.put("/admin/v1/clients/nobody/channels", json={}).status_code == 404

    def test_a_scoped_admin_reaches_only_their_own_clients(self, world):
        scoped = _admin("sam", AdminRole.EDITOR, client_ids=("alpha",))
        world.users.create(scoped)
        client = world.anonymous()
        client.post("/admin/v1/auth/login", json={"username": "sam", "password": PASSWORD})
        assert client.get("/admin/v1/clients/alpha/channels").status_code == 200
        assert client.get("/admin/v1/clients/beta/channels").status_code == 404

    def test_every_change_is_audited_without_a_value(self, world, admin):
        allow_email(admin)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        events = [e for e in world.audit.recent() if e.key.startswith("channels.")]
        assert {(e.scope, e.key, e.actor) for e in events} == {
            ("global", "channels.allowed", "root"),
            ("client:alpha", "channels.access", "root"),
        }
        assert all(e.reason is None for e in events)  # no free text where a value could hide


# ---- the client chooses among what is allowed -----------------------------------------------


class TestTheClientsChoice:
    def test_it_sees_only_the_channels_it_may_choose(self, world, alpha, admin):
        wire_whatsapp(world)
        body = alpha.get(CHANNEL).json()
        assert body["channel"] == "whatsapp" and [o["key"] for o in body["options"]] == ["whatsapp"]
        assert body["email"] is None and body["authorized"] is True and body["status"] == "ready"
        allow_email(admin)
        assert [o["key"] for o in alpha.get(CHANNEL).json()["options"]] == ["email", "whatsapp"]

    def test_a_channel_it_may_not_use_is_refused_and_nothing_is_written(self, world, alpha):
        before = len(history(world))
        response = choose(alpha)
        assert response.status_code == 403
        assert len(history(world)) == before

    def test_a_refusal_says_the_same_for_a_channel_that_does_not_exist(self, alpha):
        assert choose(alpha, "carrier_pigeon", settings=None).status_code == 422

    def test_an_allowed_channel_is_chosen_with_wording_only(self, world, alpha, admin):
        allow_email(admin)
        response = choose(alpha)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["channel"] == "email" and body["authorized"] is True
        sending = body["email"].pop("sending")  # the daily limit's state: said, never a setting
        assert sending["state"] in {"ok", "not_set", "reached", "unavailable"}
        assert body["email"] == {
            "mode": "platform",
            "from_name": "Naga Sales",
            "sender_name_confirmed": True,
            "activation": None,
            "subject": "About your enquiry",
        }
        document = dict(world.config_store.current("alpha").document)
        assert document["channel"]["provider"] == "email"
        # What the client chose is the platform's mailbox: no server, port or login in it.
        assert dict(document["channel"]["settings"]) == {"mode": "platform", **MAIL}

    def test_an_exception_for_one_client_does_not_open_the_channel_to_another(
        self, alpha, beta, admin
    ):
        admin.put("/admin/v1/clients/alpha/channels", json={"added": ["email"]})
        assert choose(alpha).status_code == 200
        assert choose(beta).status_code == 403

    def test_a_stale_version_is_refused(self, alpha, admin):
        allow_email(admin)
        assert choose(alpha).status_code == 200
        assert choose(alpha, settings={"subject": "Hello"}, expected=None).status_code == 409

    def test_invalid_wording_is_refused_without_repeating_a_value(self, world, alpha, admin):
        allow_email(admin)
        before = len(history(world))
        response = choose(alpha, settings={"from_name": "Sales <script>"})
        assert response.status_code == 422 and "script" not in response.text
        response = choose(alpha, settings={"subject": "two\nlines"})
        assert response.status_code == 422 and "two" not in response.text
        assert len(history(world)) == before

    def test_a_partial_change_keeps_the_rest(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        response = choose(alpha, settings={"subject": "Following up"})
        assert response.status_code == 200
        email = response.json()["email"]
        assert {k: email[k] for k in ("from_name", "subject")} == {
            "from_name": "Naga Sales",
            "subject": "Following up",
        }

    def test_the_same_choice_again_publishes_nothing(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha)
        count = len(history(world))
        assert choose(alpha).status_code == 200
        assert len(history(world)) == count

    def test_it_can_go_back_to_whatsapp(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        response = choose(alpha, "whatsapp", settings=None)
        assert response.status_code == 200
        assert response.json()["channel"] == "whatsapp" and response.json()["email"] is None

    def test_switching_pauses_a_running_agent(self, world, alpha, admin):
        allow_email(admin)
        world.application.agent_state.set("alpha", running=True, by="t", at=world.clock.now())
        choose(alpha)
        assert world.application.agent_state.get("alpha").running is False

    def test_the_other_clients_channel_is_not_touched(self, alpha, beta, admin):
        allow_email(admin)
        choose(alpha)
        assert beta.get(CHANNEL).json()["channel"] == "whatsapp"

    def test_email_settings_cannot_go_with_another_channel(self, alpha):
        assert choose(alpha, "whatsapp").status_code == 422

    def test_wording_is_applied_to_a_mailbox_the_admin_wired_without_changing_it(
        self, world, alpha, admin
    ):
        allow_email(admin)
        wire_legacy_mailbox(world)
        response = choose(alpha, settings={"subject": "A new subject"})
        assert response.status_code == 200, response.text
        assert set(response.json()["email"]) == {
            "mode",
            "from_name",
            "sender_name_confirmed",
            "subject",
            "activation",
            "sending",
        }  # wording and the mode: never a server, a login or a password
        stored = dict(world.config_store.current("alpha").document)["channel"]["settings"]
        assert stored["smtp_host"] == "smtp.example.org" and stored["subject"] == "A new subject"


# ---- nothing technical is the client's --------------------------------------------------------

TECHNICAL_WORDS = (
    "password",
    "token",
    "smtp",
    "imap",
    "webhook",
    "server",
    "port",
    "login",
    "secret",
)
TECHNICAL_KEYS = {
    "password",
    "bot_token",
    "access_token",
    "token",
    "credential",
    "secret",
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "imap_host",
    "imap_port",
    "imap_security",
    "username",
    "mailbox",
    "from_address",
    "webhook",
    "connected",
    "problem",
}
CREDENTIAL_ROUTES = [
    ("put", "/client/v1/channel/credentials"),
    ("delete", "/client/v1/channel/credentials"),
    ("post", "/client/v1/channel/test"),
    ("post", "/client/v1/channel/connect"),
    ("put", "/client/v1/source/credentials"),
    ("delete", "/client/v1/source/credentials"),
]


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _is_plain(text):
    return not any(word in text.lower() for word in TECHNICAL_WORDS)


class TestNoTechnicalConfiguration:
    @pytest.mark.parametrize(("method", "url"), CREDENTIAL_ROUTES)
    def test_there_is_no_route_to_write_remove_test_or_connect_a_credential(
        self, world, alpha, admin, method, url
    ):
        allow_email(admin)
        choose(alpha)
        body = {"json": {"password": SECRET, "bot_token": SECRET, "access_token": SECRET}}
        response = getattr(alpha, method)(url, **(body if method == "put" else {}))
        assert response.status_code in (404, 405)
        assert SECRET not in response.text
        for key in ("email.password", "telegram.bot_token", "hubspot.access_token"):
            assert world.application.credentials.reveal(client_scope("alpha"), key) is None

    @pytest.mark.parametrize(
        "name",
        [
            "smtp_host",
            "smtp_port",
            "smtp_security",
            "imap_host",
            "imap_port",
            "imap_security",
            "username",
            "mailbox",
            "from_address",
            "password",
            "bot_token",
            "access_token",
            "webhook_url",
            "mode",
        ],
    )
    def test_a_technical_email_setting_is_refused_and_not_echoed(self, world, alpha, admin, name):
        allow_email(admin)
        before = len(history(world))
        response = choose(alpha, settings=MAIL | {name: "leak-me-please-1234"})
        assert response.status_code == 422 and "leak-me-please-1234" not in response.text
        assert len(history(world)) == before

    @pytest.mark.parametrize(
        "name", ["password", "bot_token", "access_token", "webhook_url", "smtp_host"]
    )
    def test_a_technical_key_beside_the_channel_is_refused_and_not_echoed(
        self, world, alpha, admin, name
    ):
        allow_email(admin)
        version = alpha.get(CHANNEL).json()["version"]
        before = len(history(world))
        response = alpha.put(
            CHANNEL,
            json={"expected_version": version, "channel": "email", "email": MAIL}
            | {name: "leak-me-please-1234"},
        )
        assert response.status_code == 422 and "leak-me-please-1234" not in response.text
        assert len(history(world)) == before

    def test_a_response_carries_no_technical_field_or_secret(self, world, alpha, admin, caplog):
        caplog.set_level(logging.DEBUG)
        allow_email(admin)
        wire_legacy_mailbox(world)
        response = alpha.get(CHANNEL)
        assert response.status_code == 200
        body = response.json()
        assert not (set(_keys(body)) & TECHNICAL_KEYS)
        for hidden in (SECRET, "smtp.example.org", "imap.example.org", "sales@example.org"):
            assert hidden not in response.text and hidden not in caplog.text
        assert body["status"] == "ready"

    def test_an_answer_to_a_change_carries_none_either(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        response = choose(alpha, settings={"subject": "New subject"})
        assert response.status_code == 200
        assert not (set(_keys(response.json())) & TECHNICAL_KEYS)
        assert SECRET not in response.text and "smtp.example.org" not in response.text

    def test_a_refusal_never_carries_a_secret(self, world, alpha, admin):
        wire_legacy_mailbox(world)
        for response in (choose(alpha), choose(alpha, "carrier_pigeon", settings=None)):
            assert SECRET not in response.text and "smtp.example.org" not in response.text

    def test_the_audit_trail_holds_no_secret(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        choose(alpha)
        assert SECRET not in json.dumps([asdict(e) for e in world.audit.recent()], default=str)


class TestWhatsAppAndMetaStayWithTheAdmin:
    @pytest.mark.parametrize(
        "extra",
        [
            {"whatsapp": {"phone_number_id": "123456789"}},
            {"phone_number_id": "123456789"},
            {"access_token": "EAAG-secret-meta-token"},
            {"app_secret": "meta-app-secret-value"},
            {"model": "gpt"},
            {"mode": "autonomous"},
        ],
    )
    def test_such_a_body_is_refused_and_not_echoed(self, world, alpha, admin, extra):
        allow_email(admin)
        before = len(history(world))
        version = alpha.get(CHANNEL).json()["version"]
        response = alpha.put(
            CHANNEL, json={"expected_version": version, "channel": "email", "email": MAIL} | extra
        )
        assert response.status_code == 422
        assert not any(str(v) in response.text for v in _leaves(extra))
        assert len(history(world)) == before

    def test_a_response_never_shows_a_meta_credential(self, world, alpha):
        world.application.credentials.save(
            client_scope("alpha"), "whatsapp.access_token", "EAAG-never-shown", actor="root"
        )
        text = alpha.get(CHANNEL).text
        assert "EAAG-never-shown" not in text and "whatsapp.access_token" not in text
        assert "phone_number_id" not in text


# ---- where the channel stands ---------------------------------------------------------------


def wire_whatsapp(world):
    """What the Admin stores for WhatsApp: it is ready only once every Meta credential is stored."""
    credentials = world.application.credentials
    credentials.save(GLOBAL_SCOPE, "whatsapp.app_secret", "app-secret-marker", actor="test")
    credentials.save(GLOBAL_SCOPE, "whatsapp.verify_token", "verify-marker", actor="test")
    scope = client_scope("alpha")
    credentials.save(scope, "whatsapp.access_token", "token-alpha", actor="test")
    credentials.save(scope, "whatsapp.phone_number_id", "1234567890001", actor="test")


class TestWhereTheChannelStands:
    def test_whatsapp_waits_for_the_platform_until_meta_is_wired(self, alpha):
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "waiting_platform" and body["status_message"]

    def test_whatsapp_is_ready_once_every_meta_credential_is_stored(self, world, alpha):
        wire_whatsapp(world)
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "ready" and body["status_message"] is None

    def test_a_platform_mailbox_not_yet_assigned_is_waiting_for_the_platform(self, alpha, admin):
        allow_email(admin)
        body = choose(alpha).json()
        assert body["status"] == "waiting_platform"
        assert body["status_message"] and _is_plain(body["status_message"])

    def test_a_mailbox_the_admin_wired_is_ready(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        assert alpha.get(CHANNEL).json()["status"] == "ready"

    def test_a_mailbox_missing_its_password_is_waiting_for_the_platform_not_for_the_client(
        self, world, alpha, admin
    ):
        allow_email(admin)
        wire_legacy_mailbox(world, password=False)
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "waiting_platform" and _is_plain(body["status_message"])

    def test_a_channel_the_admin_took_away_asks_the_client_to_choose_another(
        self, world, alpha, admin
    ):
        allow_email(admin)
        wire_legacy_mailbox(world)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        body = alpha.get(CHANNEL).json()
        assert body["authorized"] is False and body["status"] == "action_required"
        assert "choose another" in body["status_message"].lower()


# ---- readiness ------------------------------------------------------------------------------


class TestReadiness:
    def checks(self, client):
        return {c["id"]: c for c in client.get(READINESS).json()["checks"]}

    def test_a_client_on_whatsapp_keeps_its_checks(self, alpha):
        body = alpha.get(READINESS).json()
        ids = check_ids(body, True) | check_ids(body, False)
        assert "whatsapp_number" in ids and "channel_ready" not in ids

    def test_a_client_on_email_is_not_asked_for_a_whatsapp_number(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        checks = self.checks(alpha)
        assert "whatsapp_number" not in checks
        assert checks["channel_ready"]["ok"] is False

    def test_what_the_platform_has_to_do_is_said_without_anything_technical(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        message = self.checks(alpha)["channel_ready"]["message"]
        assert _is_plain(message) and "nothing for you to do" in message.lower()
        reasons = {r["id"]: r["message"] for r in alpha.get(AGENT).json()["reasons"]}
        assert reasons["channel_ready"] == message

    def test_a_channel_chosen_without_any_wording_still_waits_for_the_platform(
        self, world, alpha, admin
    ):
        allow_email(admin)
        version = alpha.get(CHANNEL).json()["version"]
        publish_client_settings(  # what an administrator could publish: the channel, no mailbox
            world.application,
            "alpha",
            {"channel": {"provider": "email"}},
            expected_version=version,
            author="root",
            top_keys=(),
            sections={"channel": ("provider", "settings")},
        )
        check = self.checks(alpha)["channel_ready"]
        assert check["ok"] is False and _is_plain(check["message"])

    def test_the_channel_is_ready_once_the_admin_has_wired_it(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha)
        assert self.checks(alpha)["channel_ready"]["ok"] is False
        wire_legacy_mailbox(world)
        assert self.checks(alpha)["channel_ready"]["ok"] is True

    def test_a_forbidden_channel_is_not_ready_even_when_fully_wired(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        check = self.checks(alpha)["channel_ready"]
        assert check["ok"] is False and "no longer available" in check["message"]
        assert alpha.get(CHANNEL).json()["authorized"] is False

    def test_the_agent_cannot_start_on_a_forbidden_channel(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        response = alpha.post(START)
        assert response.status_code == 409
        assert "channel_ready" in {r["id"] for r in response.json()["detail"]["reasons"]}
        assert alpha.get(AGENT).json()["active"] is False


class _AcceptingLogin:
    """Both logins of a mailbox are accepted (a double: no server is contacted)."""

    def __init__(self, smtp, imap):
        pass

    def check_smtp(self):
        pass

    def check_imap(self):
        pass


class TestAPlatformMailboxNeedsAProvenLogin:
    """Stored fields are not enough: the client's status and the agent's start follow the proof."""

    SERVERS: ClassVar[dict[str, str]] = {
        "email.platform_smtp_host": "smtp.platform.example",
        "email.platform_smtp_port": "465",
        "email.platform_smtp_security": "ssl",
        "email.platform_imap_host": "imap.platform.example",
        "email.platform_imap_port": "993",
        "email.platform_imap_security": "ssl",
    }

    def wire(self, admin, cid="alpha", address="alpha@mail.example.org", password="Pw-alpha-1234"):
        for key, value in self.SERVERS.items():
            assert admin.put(
                f"/admin/v1/settings/integrations/{key}", json={"value": value}
            ).is_success
        base = f"/admin/v1/clients/{cid}/integrations"
        assert admin.put(f"{base}/email.platform_mailbox", json={"value": address}).is_success
        assert admin.put(f"{base}/email.password", json={"value": password}).is_success

    def channel_ready(self, client):
        return {c["id"]: c for c in client.get(READINESS).json()["checks"]}["channel_ready"]["ok"]

    def test_stored_but_unproven_is_not_ready_and_the_agent_does_not_start(
        self, world, alpha, admin
    ):
        allow_email(admin)
        choose(alpha)
        self.wire(admin)
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "waiting_platform" and _is_plain(body["status_message"])
        assert self.channel_ready(alpha) is False
        response = alpha.post(START)
        assert response.status_code == 409
        assert "channel_ready" in {r["id"] for r in response.json()["detail"]["reasons"]}

    def test_a_proven_login_makes_it_ready_and_a_new_password_voids_it(self, world, alpha, admin):
        from sales_app.mailbox_proof import verify_client_mailbox

        allow_email(admin)
        choose(alpha)
        self.wire(admin)
        verify_client_mailbox(world.application, "alpha", _AcceptingLogin)
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "ready" and body["status_message"] is None
        assert self.channel_ready(alpha) is True
        response = alpha.post(START)
        assert "channel_ready" not in {
            r["id"] for r in response.json().get("detail", {}).get("reasons", [])
        }
        admin.put(
            "/admin/v1/clients/alpha/integrations/email.password", json={"value": "Other-Pw-5678"}
        )
        assert alpha.get(CHANNEL).json()["status"] == "waiting_platform"
        assert self.channel_ready(alpha) is False

    def test_the_proof_of_one_client_does_not_make_another_ready(self, world, alpha, beta, admin):
        from sales_app.mailbox_proof import verify_client_mailbox

        allow_email(admin)
        choose(alpha)
        choose(beta)
        self.wire(admin)
        self.wire(admin, "beta", "beta@mail.example.org", "Pw-beta-5678")
        verify_client_mailbox(world.application, "alpha", _AcceptingLogin)
        assert alpha.get(CHANNEL).json()["status"] == "ready"
        assert beta.get(CHANNEL).json()["status"] == "waiting_platform"
        assert self.channel_ready(beta) is False

    def test_what_the_client_is_told_reveals_nothing(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha)
        self.wire(admin)
        text = alpha.get(CHANNEL).text + alpha.get(READINESS).text + alpha.get(AGENT).text
        for secret in ("alpha@mail.example.org", "Pw-alpha-1234", "smtp.platform.example"):
            assert secret not in text


class TestTheSenderName:
    """Platform mailbox: the Sender Name is the client's own, confirmed by saving it; the commercial
    name is only the proposal. A historical mailbox keeps its field as it always was."""

    def email(self, client):
        return client.get(CHANNEL).json()["email"]

    def test_the_commercial_name_is_proposed_and_not_taken_for_a_confirmation(self, alpha, admin):
        allow_email(admin)
        body = choose(alpha, settings={"subject": "About your enquiry"}).json()
        assert body["sender_name_proposal"] == "Alpha"
        assert body["email"]["mode"] == "platform"
        assert body["email"]["from_name"] is None
        assert body["email"]["sender_name_confirmed"] is False

    def test_the_proposal_is_known_before_email_is_chosen(self, alpha):
        body = alpha.get(CHANNEL).json()
        assert body["email"] is None and body["sender_name_proposal"] == "Alpha"

    def test_saving_a_name_confirms_it_and_it_is_kept_by_later_changes(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha, settings={"subject": "First"})
        saved = choose(alpha, settings={"from_name": "Alpha Boutique", "subject": "First"}).json()
        assert saved["email"]["from_name"] == "Alpha Boutique"
        assert saved["email"]["sender_name_confirmed"] is True
        assert saved["sender_name_proposal"] == "Alpha"  # the commercial name is a separate thing
        # A change of subject alone neither erases nor re-proposes the confirmed name.
        later = choose(alpha, settings={"subject": "Second"}).json()
        assert (
            later["email"]["from_name"] == "Alpha Boutique"
            and later["email"]["subject"] == "Second"
        )
        # It can be changed after activation.
        renamed = choose(alpha, settings={"from_name": "Alpha Studio"}).json()
        assert renamed["email"]["from_name"] == "Alpha Studio"

    def test_the_name_is_stored_in_the_clients_own_settings_only(self, world, alpha, beta, admin):
        allow_email(admin)
        choose(alpha, settings={"from_name": "Alpha Boutique"})
        choose(beta, settings={"subject": "Hello"})
        assert self.email(alpha)["from_name"] == "Alpha Boutique"
        assert self.email(beta)["from_name"] is None
        assert beta.get(CHANNEL).json()["sender_name_proposal"] == "Beta"

    @pytest.mark.parametrize(
        "name",
        [
            "Evil <evil@x.org>",
            "Name" + chr(13) + chr(10) + "Bcc: victim@example.com",
            'Quote "inside"',
            "A, B",
            "x" * 81,
        ],
    )
    def test_a_name_that_could_hurt_a_header_is_refused_and_not_echoed(
        self, world, alpha, admin, name
    ):
        allow_email(admin)
        choose(alpha, settings={"subject": "First"})
        before = len(history(world))
        version = alpha.get(CHANNEL).json()["version"]
        response = alpha.put(
            CHANNEL,
            json={"expected_version": version, "channel": "email", "email": {"from_name": name}},
        )
        assert response.status_code == 422
        assert "victim@example.com" not in response.text and "evil@x.org" not in response.text
        assert len(history(world)) == before

    def test_a_historical_mailbox_keeps_a_visible_working_field(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        body = alpha.get(CHANNEL).json()
        assert body["email"]["mode"] == "custom"
        assert body["email"]["sender_name_confirmed"] is False  # nothing to confirm in this mode
        changed = alpha.put(
            CHANNEL,
            json={
                "expected_version": body["version"],
                "channel": "email",
                "email": {"from_name": "Own Name"},
            },
        ).json()
        assert changed["email"]["mode"] == "custom" and changed["email"]["from_name"] == "Own Name"

    def test_the_response_adds_no_technical_field(self, world, alpha, admin):
        allow_email(admin)
        body = choose(alpha, settings={"from_name": "Alpha Boutique"}).json()
        assert set(body["email"]) == {
            "mode",
            "from_name",
            "sender_name_confirmed",
            "subject",
            "activation",
            "sending",
        }
        assert "smtp" not in json.dumps(body).lower() and "password" not in json.dumps(body).lower()

    def test_changing_the_name_leaves_the_login_proof_alone(self, world, alpha, admin):
        from sales_app.mailbox_proof import verify_client_mailbox

        wiring = TestAPlatformMailboxNeedsAProvenLogin()
        allow_email(admin)
        choose(alpha, settings={"from_name": "Alpha First", "subject": "First"})
        wiring.wire(admin)
        verify_client_mailbox(world.application, "alpha", _AcceptingLogin)
        assert alpha.get(CHANNEL).json()["status"] == "ready"
        choose(alpha, settings={"from_name": "Alpha Boutique"})
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "ready" and body["email"]["from_name"] == "Alpha Boutique"
        assert wiring.channel_ready(alpha) is True


class TestTheSenderNameIsConfirmedBeforeTheFirstSend:
    """Platform mailbox: until the client has saved (confirmed) a Sender Name, the channel is not
    ready, the agent cannot start and the client is told what to do. A historical mailbox is not
    concerned."""

    def wiring(self):
        return TestAPlatformMailboxNeedsAProvenLogin()

    def reasons(self, response):
        return {r["id"]: r["message"] for r in response.json()["detail"]["reasons"]}

    def test_no_confirmed_name_means_not_ready_and_the_client_is_told_what_to_do(
        self, world, alpha, admin
    ):
        from sales_app.mailbox_proof import verify_client_mailbox

        allow_email(admin)
        choose(alpha, settings={"subject": "First"})
        wiring = self.wiring()
        wiring.wire(admin)
        verify_client_mailbox(world.application, "alpha", _AcceptingLogin)  # logins are fine
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "action_required"
        assert "confirm your sender name" in body["status_message"].lower()
        assert _is_plain(body["status_message"])
        assert wiring.channel_ready(alpha) is False
        response = alpha.post(START)
        assert response.status_code == 409
        assert "sender name" in self.reasons(response)["channel_ready"].lower()

    def test_confirming_the_name_removes_that_obstacle_and_the_agent_can_start(
        self, world, alpha, admin
    ):
        from sales_app.mailbox_proof import verify_client_mailbox

        allow_email(admin)
        choose(alpha, settings={"subject": "First"})
        wiring = self.wiring()
        wiring.wire(admin)
        verify_client_mailbox(world.application, "alpha", _AcceptingLogin)
        choose(alpha, settings={"from_name": "Alpha Boutique"})
        assert alpha.get(CHANNEL).json()["status"] == "ready"
        assert wiring.channel_ready(alpha) is True
        response = alpha.post(START)
        assert "channel_ready" not in {
            r["id"] for r in response.json().get("detail", {}).get("reasons", [])
        }

    def test_the_name_is_the_last_step_not_a_substitute_for_the_platforms_work(
        self, world, alpha, admin
    ):
        allow_email(admin)
        choose(alpha, settings={"from_name": "Alpha Boutique"})  # confirmed, nothing else done
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "waiting_platform"

    def test_a_refused_new_name_leaves_the_old_one_active(self, world, alpha, admin):
        from sales_app.mailbox_proof import verify_client_mailbox

        allow_email(admin)
        choose(alpha, settings={"from_name": "Alpha Boutique"})
        self.wiring().wire(admin)
        verify_client_mailbox(world.application, "alpha", _AcceptingLogin)
        version = alpha.get(CHANNEL).json()["version"]
        refused = alpha.put(
            CHANNEL,
            json={
                "expected_version": version,
                "channel": "email",
                "email": {"from_name": "Bad <x>"},
            },
        )
        assert refused.status_code == 422
        body = alpha.get(CHANNEL).json()
        assert body["email"]["from_name"] == "Alpha Boutique" and body["status"] == "ready"
        assert body["version"] == version  # nothing was published

    def test_a_historical_mailbox_needs_no_confirmation(self, world, alpha, admin):
        allow_email(admin)
        wire_legacy_mailbox(world)
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "ready" and body["email"]["mode"] == "custom"

    def test_each_client_confirms_its_own_name(self, world, alpha, beta, admin):
        allow_email(admin)
        choose(alpha, settings={"from_name": "Alpha Boutique"})
        choose(beta, settings={"subject": "Hello"})
        assert beta.get(CHANNEL).json()["status"] == "action_required"
        assert alpha.get(CHANNEL).json()["status"] == "waiting_platform"


# ---- helpers --------------------------------------------------------------------------------


def _admin(username, role, client_ids=None):
    from test_admin_clients_api import NOW

    from sales_app.admin_auth import AdminUser
    from sales_app.admin_auth.passwords import hash_password

    scope = {} if client_ids is None else {"client_ids": client_ids}
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=role,
        created_at=NOW,
        **scope,
    )


def _leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    else:
        yield value
