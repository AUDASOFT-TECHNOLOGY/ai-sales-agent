"""`/client/v1/channel` for Telegram, and the Admin's connection of the bot.

Covers: the channel is the Admin's to allow and offered only when allowed; what the client may say
(the sentences its bot uses) and what it may not (a token, a webhook, anything technical); that no
Client route writes, removes, tests or connects a bot; that the Admin stores the bot's token and
connects it -- generating the webhook secret, telling Telegram where to deliver, and storing the
secret only once Telegram agreed; that another token drops the connection it belonged to; where the
channel stands for the client (waiting for the platform, then ready) in words with nothing technical;
the readiness check that keeps the agent from starting on a bot that is not connected; and a client
sees only its own bot.

Telegram is a double (`BotDouble`): nothing here proves a real bot accepts anything.
"""

import json
import logging
from dataclasses import asdict

import pytest
from test_admin_clients_api import PASSWORD
from test_client_agent_api import AGENT, READINESS, START, World

from sales_app.connection_tests import BotProbeFailure
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, client_scope

CHANNEL = "/client/v1/channel"
INVITATIONS = f"{CHANNEL}/invitations"
GLOBAL = "/admin/v1/settings/channels"
TOKEN = "123456789:AAH-secret-token-value-of-the-bot_xx"
OTHER_TOKEN = "987654321:AAH-another-token-value-of-the-bot_yy"
URL = "https://revansa.example/webhooks/telegram"
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
TECHNICAL_KEYS = {"bot_token", "token", "credential", "secret", "webhook", "connected", "problem"}


def admin_token(cid="alpha"):
    return f"/admin/v1/clients/{cid}/integrations/telegram.bot_token"


def admin_connect(cid="alpha"):
    return f"/admin/v1/clients/{cid}/integrations/telegram/connect"


class BotDouble:
    """Stands for Telegram: records what it was asked, fails as told."""

    def __init__(self):
        self.tokens: list[str] = []
        self.registered: list[tuple[str, str]] = []  # (url, secret) as Telegram was told
        self.bot_failure: str | None = None
        self.register_failure: str | None = None
        self.deleted = 0
        self.delete_failure: str | None = None
        self.state: str | None = None  # what the webhook looks like, when not simply "as told"
        self.username = "naga_bot"

    def __call__(self, token):
        self.tokens.append(token)
        double = self

        class _Probe:
            def check_bot(self):
                if double.bot_failure:
                    raise BotProbeFailure(double.bot_failure)
                return double.username

            def register_webhook(self, url, secret):
                if double.register_failure:
                    raise BotProbeFailure(double.register_failure)
                double.registered.append((url, secret))

            def delete_webhook(self):
                if double.delete_failure:
                    raise BotProbeFailure(double.delete_failure)
                double.deleted += 1
                double.registered.clear()

            def webhook_state(self, expected_url):
                if double.state:
                    return double.state
                return (
                    "ok"
                    if double.registered and double.registered[-1][0] == expected_url
                    else "not_set"
                )

        return _Probe()


@pytest.fixture
def world(tmp_path):
    audit = InMemorySecretAudit()
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=audit)
    w.audit, w.bot = audit, BotDouble()
    w.app_kwargs = {"bot_probe": w.bot}
    w.application.settings.public_base_url = "https://revansa.example"
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


def allow_telegram(admin, *, globally=True):
    body = {"allowed": ["whatsapp", "telegram"] if globally else ["whatsapp"]}
    response = admin.put(GLOBAL, json=body)
    assert response.status_code == 200, response.text


def choose(client, channel="telegram", telegram=None, expected="current"):
    if expected == "current":
        expected = client.get(CHANNEL).json()["version"]
    body = {"expected_version": expected, "channel": channel}
    if telegram is not None:
        body["telegram"] = telegram
    return client.put(CHANNEL, json=body)


def give_token(admin, value=TOKEN, cid="alpha"):
    """What the Admin does: the bot's token is a credential of the client's scope that only the
    platform writes."""
    return admin.put(admin_token(cid), json={"value": value})


def stored(world, key, cid="alpha"):
    return world.application.credentials.reveal(client_scope(cid), key)


def ready(client):
    checks = {c["id"]: c for c in client.get(READINESS).json()["checks"]}
    return checks["channel_ready"]


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


@pytest.fixture
def on_telegram(world, alpha, admin):
    allow_telegram(admin)
    assert choose(alpha).status_code == 200
    return alpha


# ---- authentication and the Admin's authority -------------------------------------------------


@pytest.mark.parametrize("method", ["get", "put"])
def test_the_client_routes_need_a_client_session(world, method):
    response = getattr(world.anonymous(), method)(
        INVITATIONS if method == "get" else CHANNEL, **({"json": {}} if method == "put" else {})
    )
    assert response.status_code == 401


def test_a_client_cannot_use_the_admin_routes_that_store_or_connect_a_bot(alpha):
    assert alpha.put(admin_token(), json={"value": TOKEN}).status_code == 401
    assert alpha.post(admin_connect()).status_code == 401


def test_only_an_owner_may_connect_a_bot(world):
    from test_email_channel_api import _admin

    from sales_app.admin_auth import AdminRole

    world.users.create(_admin("eve", AdminRole.EDITOR))
    editor = world.anonymous()
    editor.post("/admin/v1/auth/login", json={"username": "eve", "password": PASSWORD})
    assert editor.post(admin_connect()).status_code == 403
    assert editor.put(admin_token(), json={"value": TOKEN}).status_code == 403


class TestTheAdminAllowsTheChannel:
    def test_it_is_not_offered_until_the_admin_allows_it(self, alpha, admin):
        assert "telegram" not in {o["key"] for o in alpha.get(CHANNEL).json()["options"]}
        allow_telegram(admin)
        options = {o["key"]: o["label"] for o in alpha.get(CHANNEL).json()["options"]}
        assert options["telegram"] == "Telegram"

    def test_a_client_cannot_choose_what_it_was_not_given(self, alpha, world):
        response = choose(alpha)
        assert response.status_code == 403
        assert alpha.get(CHANNEL).json()["channel"] == "whatsapp"

    def test_it_can_be_given_to_one_client_only(self, alpha, beta, admin):
        admin.put("/admin/v1/clients/alpha/channels", json={"added": ["telegram"]})
        assert choose(alpha).status_code == 200
        assert choose(beta).status_code == 403

    def test_taking_it_away_makes_the_client_not_ready_and_refuses_its_routes(
        self, on_telegram, alpha, admin
    ):
        give_token(admin)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["telegram"]})
        body = alpha.get(CHANNEL).json()
        assert body["authorized"] is False and body["status"] == "action_required"
        assert "no longer available" in ready(alpha)["message"]
        assert alpha.get(INVITATIONS).status_code == 403


# ---- choosing it --------------------------------------------------------------------------------


class TestChoosing:
    def test_a_fresh_choice_shows_the_defaults_and_waits_for_the_platform(self, on_telegram):
        body = on_telegram.get(CHANNEL).json()
        assert body["channel"] == "telegram" and body["authorized"] is True
        assert "WhatsApp" not in body["telegram"]["contact_prompt"]
        assert body["status"] == "waiting_platform"
        assert _is_plain(body["status_message"])
        assert body["email"] is None
        assert not (set(_keys(body)) & TECHNICAL_KEYS)

    def test_the_sentences_the_bot_uses_can_be_the_clients_own(self, on_telegram):
        words = {
            "contact_prompt": "Bonjour ! Partagez votre numéro avec le bouton.",
            "contact_button": "Partager mon numéro",
            "not_found_text": "Merci, nous revenons vers vous.",
        }
        body = choose(on_telegram, telegram=words).json()
        assert {k: body["telegram"][k] for k in words} == words

    def test_a_change_keeps_what_was_not_mentioned(self, on_telegram):
        choose(on_telegram, telegram={"contact_button": "Share"})
        body = choose(on_telegram, telegram={"not_found_text": "Thanks."}).json()
        assert body["telegram"]["contact_button"] == "Share"
        assert body["telegram"]["not_found_text"] == "Thanks."

    @pytest.mark.parametrize(
        "bad",
        [
            {"contact_prompt": ""},
            {"contact_prompt": "x" * 501},
            {"contact_button": "y" * 41},
            {"not_found_text": "line\x00break"},
        ],
    )
    def test_what_is_not_valid_is_refused_with_nothing_written(self, on_telegram, world, bad):
        before = world.config_store.history("alpha")
        response = choose(on_telegram, telegram=bad)
        assert response.status_code == 422
        assert world.config_store.history("alpha") == before

    def test_the_error_names_the_field_and_never_repeats_the_value(self, on_telegram):
        response = choose(on_telegram, telegram={"not_found_text": "secret\x00words"})
        assert "secret" not in response.text and "not_found_text" in response.text

    @pytest.mark.parametrize("name", ["bot_token", "webhook_url", "webhook_secret", "token"])
    def test_a_technical_setting_cannot_be_smuggled_in_with_the_sentences(
        self, on_telegram, world, name
    ):
        before = world.config_store.history("alpha")
        response = choose(on_telegram, telegram={name: TOKEN})
        assert response.status_code == 422 and TOKEN not in response.text
        assert world.config_store.history("alpha") == before

    def test_a_token_beside_the_channel_is_refused_and_not_echoed(self, on_telegram):
        version = on_telegram.get(CHANNEL).json()["version"]
        response = on_telegram.put(
            CHANNEL, json={"expected_version": version, "channel": "telegram", "bot_token": TOKEN}
        )
        assert response.status_code == 422 and TOKEN not in response.text

    def test_telegram_settings_belong_to_telegram(self, alpha, admin):
        allow_telegram(admin)
        response = choose(alpha, channel="whatsapp", telegram={"contact_button": "x"})
        assert response.status_code == 422

    def test_a_stale_version_is_a_conflict(self, on_telegram):
        assert choose(on_telegram, expected=999).status_code == 409

    def test_switching_pauses_a_running_agent(self, world, alpha, admin):
        world.agent_state.set("alpha", running=True, by="t", at=world.clock.now())
        allow_telegram(admin)
        choose(alpha)
        assert alpha.get(AGENT).json()["active"] is False

    def test_a_client_never_sees_another_clients_bot(self, world, alpha, beta, admin):
        allow_telegram(admin)
        choose(alpha)
        choose(beta)
        give_token(admin)
        assert beta.get(CHANNEL).json()["status"] == "waiting_platform"
        assert stored(world, "telegram.bot_token", "beta") is None


# ---- nothing technical is the client's ---------------------------------------------------------


class TestNoTechnicalConfiguration:
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("put", f"{CHANNEL}/credentials"),
            ("delete", f"{CHANNEL}/credentials"),
            ("post", f"{CHANNEL}/connect"),
            ("post", f"{CHANNEL}/test"),
        ],
    )
    def test_a_client_has_no_route_to_store_connect_or_test_a_bot(
        self, world, on_telegram, method, url
    ):
        body = {"json": {"bot_token": TOKEN}} if method == "put" else {}
        response = getattr(on_telegram, method)(url, **body)
        assert response.status_code in (404, 405) and TOKEN not in response.text
        assert stored(world, "telegram.bot_token") is None
        assert world.bot.registered == [] and world.bot.tokens == []

    def test_a_response_carries_no_token_secret_or_technical_field(
        self, world, on_telegram, admin, caplog
    ):
        caplog.set_level(logging.DEBUG)
        give_token(admin)
        admin.post(admin_connect())
        response = on_telegram.get(CHANNEL)
        secret = stored(world, "telegram.webhook_secret")
        assert TOKEN not in response.text and secret not in response.text
        assert TOKEN not in caplog.text and secret not in caplog.text
        assert not (set(_keys(response.json())) & TECHNICAL_KEYS)

    def test_the_invitations_carry_no_token_or_secret(self, world, on_telegram, admin):
        give_token(admin)
        admin.post(admin_connect())
        response = on_telegram.get(INVITATIONS)
        secret = stored(world, "telegram.webhook_secret")
        assert TOKEN not in response.text and secret not in response.text


# ---- the Admin stores and connects the bot -----------------------------------------------------


class TestTheAdminStoresTheToken:
    def test_it_is_stored_and_never_returned(self, world, on_telegram, admin):
        response = give_token(admin)
        assert response.status_code == 200 and TOKEN not in response.text
        assert TOKEN not in on_telegram.get(CHANNEL).text
        assert stored(world, "telegram.bot_token") == TOKEN

    def test_it_is_in_no_audit_event_and_no_log(self, world, on_telegram, admin, caplog):
        with caplog.at_level(logging.DEBUG):
            give_token(admin)
            admin.post(admin_connect())
        events = json.dumps([asdict(e) | {"at": ""} for e in world.audit.recent()], default=str)
        assert TOKEN not in events and "telegram.bot_token" in events
        assert TOKEN not in caplog.text

    def test_a_token_that_is_not_one_is_refused_without_being_repeated(self, on_telegram, admin):
        response = give_token(admin, "not-a-token")
        assert response.status_code == 422 and "not-a-token" not in response.text


class TestConnecting:
    def test_connecting_tells_telegram_where_to_deliver_with_a_secret_only_we_hold(
        self, world, on_telegram, admin
    ):
        give_token(admin)
        response = admin.post(admin_connect())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True and body["details"] == {"bot": "ok", "webhook": "ok"}
        ((url, secret),) = world.bot.registered
        assert url == URL and len(secret) >= 32
        assert stored(world, "telegram.webhook_secret") == secret
        assert secret not in response.text and TOKEN not in response.text
        assert world.bot.tokens[-1] == TOKEN

    def test_afterwards_the_client_is_ready(self, world, on_telegram, admin):
        give_token(admin)
        assert ready(on_telegram)["ok"] is False
        admin.post(admin_connect())
        body = on_telegram.get(CHANNEL).json()
        assert body["status"] == "ready" and body["status_message"] is None
        assert ready(on_telegram)["ok"] is True

    def test_connecting_again_keeps_the_secret_and_so_the_links_already_sent(
        self, world, on_telegram, admin
    ):
        give_token(admin)
        admin.post(admin_connect())
        admin.post(admin_connect())
        assert len({secret for _, secret in world.bot.registered}) == 1
        assert len(world.bot.registered) == 2

    def test_the_secret_is_different_for_each_client(self, world, on_telegram, beta, admin):
        allow_telegram(admin)
        choose(beta)
        give_token(admin)
        give_token(admin, OTHER_TOKEN, "beta")
        admin.post(admin_connect())
        admin.post(admin_connect("beta"))
        assert stored(world, "telegram.webhook_secret", "alpha") != stored(
            world, "telegram.webhook_secret", "beta"
        )

    def test_there_is_nothing_to_connect_without_a_token(self, on_telegram, world, admin):
        response = admin.post(admin_connect())
        assert response.status_code == 409 and world.bot.registered == []

    def test_a_deployment_without_a_public_address_cannot_connect_a_bot(
        self, world, on_telegram, admin
    ):
        world.application.settings.public_base_url = None
        give_token(admin)
        response = admin.post(admin_connect())
        assert response.status_code == 409 and "public https address" in response.json()["detail"]
        assert world.bot.registered == []

    @pytest.mark.parametrize("address", ["http://revansa.example", "revansa.example", "https://"])
    def test_telegram_only_delivers_to_https(self, world, on_telegram, admin, address):
        world.application.settings.public_base_url = address
        give_token(admin)
        assert admin.post(admin_connect()).status_code == 409

    def test_a_public_address_with_a_path_is_kept(self, world, on_telegram, admin):
        world.application.settings.public_base_url = "https://example.org/revansa/"
        give_token(admin)
        admin.post(admin_connect())
        assert world.bot.registered[0][0] == "https://example.org/revansa/webhooks/telegram"

    @pytest.mark.parametrize(
        ("kind", "words"),
        [
            ("auth", "refused this bot token"),
            ("unreachable", "could not be reached"),
            ("rate_limited", "try again later"),
            ("service_error", "had a problem"),
        ],
    )
    def test_a_bot_telegram_will_not_talk_about_is_told_in_a_fixed_sentence(
        self, world, on_telegram, admin, kind, words
    ):
        give_token(admin)
        world.bot.bot_failure = kind
        response = admin.post(admin_connect())
        assert response.status_code == 200 and response.json()["ok"] is False
        assert words in response.json()["message"] and TOKEN not in response.text
        assert stored(world, "telegram.webhook_secret") is None
        assert on_telegram.get(CHANNEL).json()["status"] == "waiting_platform"

    def test_an_address_telegram_refuses_leaves_the_bot_not_connected(
        self, world, on_telegram, admin
    ):
        give_token(admin)
        world.bot.register_failure = "rejected"
        response = admin.post(admin_connect())
        assert response.json()["ok"] is False and "address" in response.json()["message"]
        assert stored(world, "telegram.webhook_secret") is None

    def test_a_client_that_does_not_exist_is_not_found(self, admin):
        assert admin.post(admin_connect("nobody")).status_code == 404


def admin_test(cid="alpha"):
    return f"/admin/v1/clients/{cid}/integrations/telegram/test"


def admin_disconnect(cid="alpha"):
    return f"/admin/v1/clients/{cid}/integrations/telegram/disconnect"


class TestTestingAndDisconnecting:
    def test_the_test_asks_telegram_for_real_and_says_so(self, world, on_telegram, admin):
        give_token(admin)
        admin.post(admin_connect())
        response = admin.post(admin_test())
        body = response.json()
        assert response.status_code == 200 and body["ok"] is True
        assert body["details"]["check"] == "real_telegram_api"
        assert TOKEN not in response.text and len(world.bot.registered) == 1  # changed nothing

    def test_the_test_of_a_bot_not_connected_yet_says_so(self, world, on_telegram, admin):
        give_token(admin)
        body = admin.post(admin_test()).json()
        assert body["ok"] is False and body["details"]["webhook"] == "not_set"

    def test_the_test_without_a_token_is_a_failure_not_a_simulation(self, on_telegram, admin):
        body = admin.post(admin_test()).json()
        assert body["ok"] is False and "token is not set" in body["message"]

    def test_the_test_reports_a_refused_token_in_a_fixed_sentence(self, world, on_telegram, admin):
        give_token(admin)
        world.bot.bot_failure = "auth"
        response = admin.post(admin_test())
        assert "refused this bot token" in response.json()["message"] and TOKEN not in response.text

    def test_disconnecting_stops_telegram_and_puts_the_client_back_to_waiting(
        self, world, on_telegram, admin
    ):
        give_token(admin)
        admin.post(admin_connect())
        response = admin.post(admin_disconnect())
        assert response.status_code == 200 and response.json()["ok"] is True
        assert world.bot.deleted == 1 and stored(world, "telegram.webhook_secret") is None
        assert stored(world, "telegram.bot_token") == TOKEN
        assert on_telegram.get(CHANNEL).json()["status"] == "waiting_platform"

    def test_a_disconnection_telegram_did_not_confirm_keeps_the_connection(
        self, world, on_telegram, admin
    ):
        give_token(admin)
        admin.post(admin_connect())
        world.bot.delete_failure = "unreachable"
        body = admin.post(admin_disconnect()).json()
        assert body["ok"] is False and "still connected" in body["message"]
        assert stored(world, "telegram.webhook_secret") is not None

    def test_a_token_telegram_no_longer_knows_can_still_be_disconnected(
        self, world, on_telegram, admin
    ):
        give_token(admin)
        admin.post(admin_connect())
        world.bot.delete_failure = "auth"
        assert admin.post(admin_disconnect()).json()["ok"] is True
        assert stored(world, "telegram.webhook_secret") is None

    def test_only_an_owner_and_never_a_client_may_test_or_disconnect(self, world, alpha):
        assert alpha.post(admin_test()).status_code == 401
        assert alpha.post(admin_disconnect()).status_code == 401

    def test_an_unknown_client_is_not_found(self, admin):
        assert admin.post(admin_test("nobody")).status_code == 404
        assert admin.post(admin_disconnect("nobody")).status_code == 404


# ---- readiness ---------------------------------------------------------------------------------


class TestReadiness:
    def test_a_client_on_telegram_is_asked_for_its_bot_not_a_whatsapp_number(self, on_telegram):
        checks = {c["id"]: c for c in on_telegram.get(READINESS).json()["checks"]}
        assert "whatsapp_number" not in checks and checks["channel_ready"]["ok"] is False

    def test_what_is_missing_is_the_platforms_and_said_without_anything_technical(
        self, on_telegram, admin
    ):
        message = ready(on_telegram)["message"]
        assert _is_plain(message) and "nothing for you to do" in message.lower()
        give_token(admin)
        assert ready(on_telegram)["message"] == message  # still the platform's to finish
        admin.post(admin_connect())
        assert ready(on_telegram)["ok"] is True

    def test_the_agent_cannot_start_on_a_bot_that_is_not_connected(self, on_telegram, admin):
        give_token(admin)
        response = on_telegram.post(START)
        assert response.status_code == 409
        assert "channel_ready" in {r["id"] for r in response.json()["detail"]["reasons"]}
