"""`/client/v1/channel` and `/admin/v1/.../channels`: who may use which channel, and the mailbox.

Covers: authentication and isolation between clients; the Admin's authority (the global list, a
client's exceptions, who may write them) and the client's choice only among what is allowed; what a
client may change (its channel and its mailbox's non-secret settings) and what it may not (anything
of WhatsApp or Meta, the model, the mode); version conflicts and validation, with nothing written on
a refusal and nothing echoed back; the password's whole life (stored write-only, never in any
response, error, audit event or log); the connection test through a double of the mail servers; a
mailbox that belongs to one client only; and the readiness check that keeps an agent from starting
on a channel that is not ready.

The mail servers are doubles: nothing here proves a real server accepts the login.
"""

import json
import logging
from dataclasses import asdict

import pytest
from test_admin_clients_api import PASSWORD
from test_client_agent_api import AGENT, READINESS, START, World, check_ids

from sales_app.admin_auth import AdminRole
from sales_app.api.admin_clients import publish_client_settings
from sales_app.connection_tests import MailProbeFailure
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, client_scope

CHANNEL = "/client/v1/channel"
CREDENTIALS = f"{CHANNEL}/credentials"
TEST = f"{CHANNEL}/test"
GLOBAL = "/admin/v1/settings/channels"
SECRET = "S3cret-mail-pass!"
OTHER_SECRET = "An0ther-Mail-pass?"
MAIL = {
    "from_address": "sales@example.org",
    "from_name": "Naga Sales",
    "username": "sales@example.org",
    "smtp_host": "smtp.example.org",
    "imap_host": "imap.example.org",
}


class Probe:
    """Stands for the two mail servers: records what it was asked, fails as told."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.smtp_failure: str | None = None
        self.imap_failure: str | None = None

    def __call__(self, settings, password):
        self.calls.append((settings.smtp_host, password))
        probe = self

        class _Probe:
            def check_smtp(self):
                if probe.smtp_failure:
                    raise MailProbeFailure(probe.smtp_failure)

            def check_imap(self):
                if probe.imap_failure:
                    raise MailProbeFailure(probe.imap_failure)

        return _Probe()


@pytest.fixture
def world(tmp_path, monkeypatch):
    from sales_app.api import client_channel

    monkeypatch.setattr(client_channel, "TEST_MIN_INTERVAL_SECONDS", 0.0)
    audit = InMemorySecretAudit()
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=audit)
    w.audit, w.probe = audit, Probe()
    w.app_kwargs = {"mail_probe": w.probe}
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


def save_password(client, value=SECRET):
    return client.put(CREDENTIALS, json={"password": value})


def stored_password(world, cid="alpha"):
    return world.application.credentials.reveal(client_scope(cid), "email.password")


def history(world, cid="alpha"):
    return world.config_store.history(cid)


# ---- authentication -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", CHANNEL),
        ("put", CHANNEL),
        ("put", CREDENTIALS),
        ("delete", CREDENTIALS),
        ("post", TEST),
    ],
)
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
    def test_it_sees_only_the_channels_it_may_choose(self, alpha, admin):
        body = alpha.get(CHANNEL).json()
        assert body["channel"] == "whatsapp" and [o["key"] for o in body["options"]] == ["whatsapp"]
        assert body["email"] is None and body["credential"] is None and body["authorized"] is True
        allow_email(admin)
        assert [o["key"] for o in alpha.get(CHANNEL).json()["options"]] == ["email", "whatsapp"]

    def test_a_channel_it_may_not_use_is_refused_and_nothing_is_written(self, world, alpha):
        before = len(history(world))
        response = choose(alpha)
        assert response.status_code == 403
        assert len(history(world)) == before

    def test_a_refusal_says_the_same_for_a_channel_that_does_not_exist(self, alpha):
        assert choose(alpha, "carrier_pigeon", settings=None).status_code == 422

    def test_an_allowed_channel_is_chosen_and_its_settings_kept(self, world, alpha, admin):
        allow_email(admin)
        response = choose(alpha)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["channel"] == "email" and body["authorized"] is True
        assert body["email"]["from_address"] == "sales@example.org"
        assert body["email"]["smtp_port"] == 587 and body["email"]["imap_port"] == 993
        document = dict(world.config_store.current("alpha").document)
        assert document["channel"]["provider"] == "email"
        assert document["channel"]["settings"]["smtp_host"] == "smtp.example.org"

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

    def test_an_invalid_mailbox_is_refused_without_repeating_a_value(self, world, alpha, admin):
        allow_email(admin)
        before = len(history(world))
        response = choose(alpha, settings=MAIL | {"smtp_port": 8080})
        assert response.status_code == 422 and "8080" not in response.text
        response = choose(alpha, settings=MAIL | {"from_address": "not an address"})
        assert response.status_code == 422 and "not an address" not in response.text
        assert len(history(world)) == before

    def test_a_partial_change_keeps_the_rest(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        response = choose(alpha, settings={"subject": "About your enquiry"})
        assert response.status_code == 200
        email = response.json()["email"]
        assert email["subject"] == "About your enquiry" and email["smtp_host"] == "smtp.example.org"

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

    def test_a_mailbox_setting_cannot_be_a_password_or_a_meta_field(self, alpha, admin):
        allow_email(admin)
        for name in ("password", "access_token", "phone_number_id", "app_secret"):
            response = choose(alpha, settings=MAIL | {name: "leak-me-please-1234"})
            assert response.status_code == 422 and "leak-me-please-1234" not in response.text

    def test_a_response_never_shows_a_meta_credential(self, world, alpha):
        world.application.credentials.save(
            client_scope("alpha"), "whatsapp.access_token", "EAAG-never-shown", actor="root"
        )
        text = alpha.get(CHANNEL).text
        assert "EAAG-never-shown" not in text and "whatsapp.access_token" not in text
        assert "phone_number_id" not in text

    def test_a_client_cannot_write_a_meta_credential_through_the_password_route(
        self, world, alpha, admin
    ):
        allow_email(admin)
        choose(alpha)
        response = alpha.put(CREDENTIALS, json={"access_token": "EAAG-never-stored"})
        assert response.status_code == 422 and "EAAG-never-stored" not in response.text
        assert (
            world.application.credentials.reveal(client_scope("alpha"), "whatsapp.access_token")
            is None
        )


# ---- the password ---------------------------------------------------------------------------


class TestThePassword:
    @pytest.fixture
    def emailing(self, alpha, admin):
        allow_email(admin)
        assert choose(alpha).status_code == 200
        return alpha

    def test_it_is_stored_and_never_returned(self, world, emailing, caplog):
        caplog.set_level(logging.DEBUG)
        response = save_password(emailing)
        assert response.status_code == 200
        assert response.json()["configured"] is True and "updated_at" in response.json()
        assert stored_password(world) == SECRET
        for text in (response.text, emailing.get(CHANNEL).text, caplog.text):
            assert SECRET not in text
        events = json.dumps([asdict(e) | {"at": ""} for e in world.audit.recent()])
        assert SECRET not in events

    def test_the_status_says_stored_or_not(self, emailing):
        assert emailing.get(CHANNEL).json()["credential"]["configured"] is False
        save_password(emailing)
        assert emailing.get(CHANNEL).json()["credential"]["configured"] is True

    def test_it_can_be_replaced_and_removed(self, world, emailing):
        save_password(emailing)
        save_password(emailing, OTHER_SECRET)
        assert stored_password(world) == OTHER_SECRET
        response = emailing.delete(CREDENTIALS)
        assert response.status_code == 200 and response.json()["configured"] is False
        assert stored_password(world) is None

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"password": SECRET, "extra": 1},
            {"pass": SECRET},
            {"access_token": SECRET},
            {"password": 12345},
            [SECRET],
        ],
    )
    def test_any_other_body_is_refused_without_being_echoed(self, world, emailing, body):
        response = emailing.put(CREDENTIALS, json=body)
        assert response.status_code == 422 and SECRET not in response.text
        assert stored_password(world) is None

    def test_a_body_that_is_not_json_is_refused(self, emailing):
        response = emailing.put(
            CREDENTIALS, content=SECRET, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400 and SECRET not in response.text

    def test_a_value_the_store_refuses_is_refused_without_repeating_it(self, emailing):
        response = emailing.put(CREDENTIALS, json={"password": "line one\nline two " + SECRET})
        assert response.status_code == 422 and SECRET not in response.text

    def test_each_client_has_its_own(self, world, alpha, beta, admin):
        admin.put(GLOBAL, json={"allowed": ["whatsapp", "email"]})
        choose(alpha)
        choose(
            beta,
            settings=MAIL | {"username": "other@example.org", "imap_host": "imap2.example.org"},
        )
        save_password(alpha, SECRET)
        save_password(beta, OTHER_SECRET)
        assert stored_password(world, "alpha") == SECRET
        assert stored_password(world, "beta") == OTHER_SECRET

    def test_a_client_on_whatsapp_has_no_password_to_give(self, alpha):
        assert save_password(alpha).status_code == 409
        assert alpha.delete(CREDENTIALS).status_code == 409

    def test_a_channel_the_admin_has_taken_away_takes_no_password(self, emailing, admin):
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        response = save_password(emailing)
        assert response.status_code == 403 and SECRET not in response.text


# ---- one mailbox, one client ----------------------------------------------------------------


def test_a_mailbox_another_client_uses_cannot_be_used_and_whose_it_is_is_not_said(
    alpha, beta, admin
):
    allow_email(admin)
    assert choose(alpha).status_code == 200
    response = choose(beta)
    assert response.status_code == 409
    assert "alpha" not in response.text and "sales@example.org" not in response.text
    assert beta.get(CHANNEL).json()["channel"] == "whatsapp"


def test_the_same_client_may_keep_its_own_mailbox(alpha, admin):
    allow_email(admin)
    assert choose(alpha).status_code == 200
    assert choose(alpha, settings={"subject": "Hello again"}).status_code == 200


# ---- the test -------------------------------------------------------------------------------


class TestTheConnectionTest:
    @pytest.fixture
    def ready(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha)
        save_password(alpha)
        return alpha

    def test_both_servers_answer(self, world, ready):
        response = ready.post(TEST)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True and body["details"] == {"smtp": "ok", "imap": "ok"}
        assert world.probe.calls == [("smtp.example.org", SECRET)]  # the stored one was used
        assert SECRET not in response.text

    @pytest.mark.parametrize("kind", ["auth", "unreachable", "refused"])
    def test_a_failure_is_a_fixed_sentence(self, world, ready, kind):
        world.probe.smtp_failure = kind
        body = ready.post(TEST).json()
        assert body["ok"] is False and body["details"]["smtp"] == kind
        assert body["details"]["imap"] == "ok"
        assert SECRET not in json.dumps(body) and "example.org" not in body["message"]

    def test_the_mailbox_can_fail_while_sending_works(self, world, ready):
        world.probe.imap_failure = "auth"
        body = ready.post(TEST).json()
        assert body["ok"] is False and body["details"] == {"smtp": "ok", "imap": "auth"}

    def test_nothing_is_tested_before_the_mailbox_is_set_up(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha)  # no password yet
        body = alpha.post(TEST).json()
        assert body["ok"] is False and world.probe.calls == []

    def test_a_channel_with_nothing_to_test_says_so(self, alpha):
        assert alpha.post(TEST).status_code == 409

    def test_it_is_refused_when_the_admin_forbids_the_channel(self, ready, admin):
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        assert ready.post(TEST).status_code == 403

    def test_the_test_is_rate_limited(self, world, ready, monkeypatch):
        from sales_app.api import client_channel

        monkeypatch.setattr(client_channel, "TEST_MIN_INTERVAL_SECONDS", 60.0)
        ready.post(TEST)
        assert ready.post(TEST).status_code == 429


# ---- readiness ------------------------------------------------------------------------------


class TestReadiness:
    def test_a_client_on_whatsapp_keeps_its_checks(self, alpha):
        body = alpha.get(READINESS).json()
        ids = check_ids(body, True) | check_ids(body, False)
        assert "whatsapp_number" in ids and "channel_ready" not in ids

    def test_a_client_on_email_is_asked_for_its_mailbox_not_a_whatsapp_number(
        self, world, alpha, admin
    ):
        allow_email(admin)
        choose(alpha)
        checks = {c["id"]: c for c in alpha.get(READINESS).json()["checks"]}
        assert "whatsapp_number" not in checks
        assert checks["channel_ready"]["ok"] is False

    def test_a_channel_chosen_without_its_settings_says_so(self, world, alpha, admin):
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
        checks = {c["id"]: c for c in alpha.get(READINESS).json()["checks"]}
        assert checks["channel_ready"]["ok"] is False
        assert "email settings" in checks["channel_ready"]["message"].lower()

    def test_the_password_is_the_last_thing_missing(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        checks = {c["id"]: c for c in alpha.get(READINESS).json()["checks"]}
        assert "password" in checks["channel_ready"]["message"].lower()
        save_password(alpha)
        checks = {c["id"]: c for c in alpha.get(READINESS).json()["checks"]}
        assert checks["channel_ready"]["ok"] is True

    def test_a_forbidden_channel_is_not_ready_even_when_fully_set_up(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        save_password(alpha)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        checks = {c["id"]: c for c in alpha.get(READINESS).json()["checks"]}
        assert checks["channel_ready"]["ok"] is False
        assert "not authorized" in checks["channel_ready"]["message"]
        assert alpha.get(CHANNEL).json()["authorized"] is False

    def test_the_agent_cannot_start_on_a_forbidden_channel(self, world, alpha, admin):
        allow_email(admin)
        choose(alpha)
        save_password(alpha)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
        response = alpha.post(START)
        assert response.status_code == 409
        assert "channel_ready" in {r["id"] for r in response.json()["detail"]["reasons"]}
        assert alpha.get(AGENT).json()["active"] is False

    def test_the_missing_password_is_a_reason_the_client_can_act_on(self, alpha, admin):
        allow_email(admin)
        choose(alpha)
        reasons = {r["id"]: r["message"] for r in alpha.get(AGENT).json()["reasons"]}
        assert "channel_ready" in reasons
        assert "administrator" not in reasons["channel_ready"].lower()


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
