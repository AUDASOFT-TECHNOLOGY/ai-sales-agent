"""`/client/v1/channel` for a Telegram bot: choosing it, the token, connecting it, testing it.

Covers: the channel is the Admin's to allow and offered only when allowed; the bot's token is
written, never read (no response, error or audit event carries it) and never accepted under another
name; connecting generates the webhook secret and tells Telegram where to deliver, and stores the
secret only once Telegram agreed; another token drops the connection it belonged to; the connection
test says what is wrong in fixed sentences; the readiness check keeps the agent from starting on a
bot that is not connected; and a client sees only its own bot.

Telegram is a double (`BotDouble`): nothing here proves a real bot accepts anything.
"""

import json
import logging
from dataclasses import asdict

import pytest
from test_admin_clients_api import PASSWORD  # noqa: F401  (the fixtures of the shared World)
from test_client_agent_api import AGENT, READINESS, START, World

from sales_app.connection_tests import BotProbeFailure
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, client_scope

CHANNEL = "/client/v1/channel"
CREDENTIALS = f"{CHANNEL}/credentials"
TEST = f"{CHANNEL}/test"
CONNECT = f"{CHANNEL}/connect"
INVITATIONS = f"{CHANNEL}/invitations"
GLOBAL = "/admin/v1/settings/channels"
TOKEN = "123456789:AAH-secret-token-value-of-the-bot_xx"
OTHER_TOKEN = "987654321:AAH-another-token-value-of-the-bot_yy"
URL = "https://revansa.example/webhooks/telegram"


class BotDouble:
    """Stands for Telegram: records what it was asked, fails as told."""

    def __init__(self):
        self.tokens: list[str] = []
        self.registered: list[tuple[str, str]] = []  # (url, secret) as Telegram was told
        self.bot_failure: str | None = None
        self.register_failure: str | None = None
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
def world(tmp_path, monkeypatch):
    from sales_app.api import client_channel

    monkeypatch.setattr(client_channel, "TEST_MIN_INTERVAL_SECONDS", 0.0)
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


def save_token(client, value=TOKEN):
    return client.put(CREDENTIALS, json={"bot_token": value})


def stored(world, key, cid="alpha"):
    return world.application.credentials.reveal(client_scope(cid), key)


def ready(client):
    checks = {c["id"]: c for c in client.get(READINESS).json()["checks"]}
    return checks["channel_ready"]


@pytest.fixture
def on_telegram(world, alpha, admin):
    allow_telegram(admin)
    assert choose(alpha).status_code == 200
    return alpha


# ---- authentication and the Admin's authority -------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [("post", CONNECT), ("get", INVITATIONS), ("post", TEST), ("put", CREDENTIALS)],
)
def test_every_telegram_route_needs_a_client_session(world, method, url):
    response = getattr(world.anonymous(), method)(url, **({"json": {}} if method == "put" else {}))
    assert response.status_code == 401


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
        save_token(alpha)
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["telegram"]})
        assert alpha.get(CHANNEL).json()["authorized"] is False
        assert "not authorized" in ready(alpha)["message"]
        assert alpha.post(CONNECT).status_code == 403
        assert alpha.post(TEST).status_code == 403
        assert alpha.get(INVITATIONS).status_code == 403
        assert save_token(alpha).status_code == 403


# ---- choosing it --------------------------------------------------------------------------------


class TestChoosing:
    def test_a_fresh_choice_shows_the_defaults_and_what_is_missing(self, on_telegram):
        body = on_telegram.get(CHANNEL).json()
        assert body["channel"] == "telegram" and body["authorized"] is True
        assert body["telegram"]["connected"] is False
        assert "WhatsApp" not in body["telegram"]["contact_prompt"]
        assert body["credential"] == {
            "label": "Telegram bot token",
            "configured": False,
            "updated_at": None,
        }
        assert "token" in body["problem"].lower()
        assert body["email"] is None

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

    def test_a_token_cannot_be_smuggled_in_with_the_settings(self, on_telegram):
        response = choose(on_telegram, telegram={"bot_token": TOKEN})
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
        save_token(alpha)
        assert beta.get(CHANNEL).json()["credential"]["configured"] is False
        assert stored(world, "telegram.bot_token", "beta") is None


# ---- the token ---------------------------------------------------------------------------------


class TestTheToken:
    def test_it_is_stored_and_never_returned(self, world, on_telegram):
        response = save_token(on_telegram)
        assert response.status_code == 200 and response.json()["configured"] is True
        assert TOKEN not in response.text
        assert TOKEN not in on_telegram.get(CHANNEL).text
        assert stored(world, "telegram.bot_token") == TOKEN

    def test_it_is_in_no_audit_event_and_no_log(self, world, on_telegram, caplog):
        with caplog.at_level(logging.DEBUG):
            save_token(on_telegram)
            on_telegram.post(CONNECT)
        events = json.dumps([asdict(e) | {"at": ""} for e in world.audit.recent()], default=str)
        assert TOKEN not in events and "telegram.bot_token" in events
        assert TOKEN not in caplog.text

    def test_a_token_that_is_not_one_is_refused_without_being_repeated(self, on_telegram):
        response = save_token(on_telegram, "not-a-token")
        assert response.status_code == 422 and "not-a-token" not in response.text
        assert "BotFather" in response.text

    def test_only_the_expected_field_is_accepted(self, on_telegram):
        for body in ({"password": TOKEN}, {"bot_token": TOKEN, "extra": 1}, {}, {"bot_token": 1}):
            response = on_telegram.put(CREDENTIALS, json=body)
            assert response.status_code == 422 and TOKEN not in response.text

    def test_a_client_on_whatsapp_has_no_token_to_give(self, alpha):
        assert save_token(alpha).status_code == 409

    def test_a_client_on_telegram_cannot_write_a_mailbox_password(self, on_telegram):
        assert (
            on_telegram.put(CREDENTIALS, json={"password": "S3cret-mail-pass!"}).status_code == 422
        )

    def test_it_can_be_removed(self, world, on_telegram):
        save_token(on_telegram)
        response = on_telegram.delete(CREDENTIALS)
        assert response.status_code == 200 and response.json()["configured"] is False
        assert stored(world, "telegram.bot_token") is None


# ---- connecting the bot -----------------------------------------------------------------------


class TestConnecting:
    def test_connecting_tells_telegram_where_to_deliver_with_a_secret_only_we_hold(
        self, world, on_telegram
    ):
        save_token(on_telegram)
        response = on_telegram.post(CONNECT)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True and body["details"] == {"bot": "ok", "webhook": "ok"}
        ((url, secret),) = world.bot.registered
        assert url == URL and len(secret) >= 32
        assert stored(world, "telegram.webhook_secret") == secret
        assert secret not in response.text and TOKEN not in response.text
        assert world.bot.tokens[-1] == TOKEN

    def test_afterwards_the_client_is_connected_and_ready(self, world, on_telegram):
        save_token(on_telegram)
        assert ready(on_telegram)["ok"] is False
        on_telegram.post(CONNECT)
        body = on_telegram.get(CHANNEL).json()
        assert body["telegram"]["connected"] is True and body["problem"] is None
        assert ready(on_telegram)["ok"] is True

    def test_connecting_again_keeps_the_secret_and_so_the_links_already_sent(
        self, world, on_telegram
    ):
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        on_telegram.post(CONNECT)
        assert len({secret for _, secret in world.bot.registered}) == 1
        assert len(world.bot.registered) == 2

    def test_the_secret_is_different_for_each_client(self, world, on_telegram, beta, admin):
        allow_telegram(admin)
        choose(beta)
        save_token(on_telegram)
        save_token(beta, OTHER_TOKEN)
        on_telegram.post(CONNECT)
        beta.post(CONNECT)
        assert stored(world, "telegram.webhook_secret", "alpha") != stored(
            world, "telegram.webhook_secret", "beta"
        )

    def test_another_bot_is_not_the_one_that_was_connected(self, world, on_telegram):
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        save_token(on_telegram, OTHER_TOKEN)
        assert stored(world, "telegram.webhook_secret") is None
        assert on_telegram.get(CHANNEL).json()["telegram"]["connected"] is False
        assert "connect" in ready(on_telegram)["message"].lower()

    def test_a_mistyped_token_never_disconnects_the_bot_that_works(self, world, on_telegram):
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        secret = stored(world, "telegram.webhook_secret")
        assert save_token(on_telegram, "oops").status_code == 422
        assert stored(world, "telegram.webhook_secret") == secret
        assert on_telegram.get(CHANNEL).json()["telegram"]["connected"] is True

    def test_saving_the_same_token_again_keeps_the_connection(self, world, on_telegram):
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        secret = stored(world, "telegram.webhook_secret")
        assert save_token(on_telegram, f"  {TOKEN}  ").status_code == 200
        assert stored(world, "telegram.webhook_secret") == secret

    def test_removing_the_token_disconnects(self, world, on_telegram):
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        on_telegram.delete(CREDENTIALS)
        assert stored(world, "telegram.webhook_secret") is None

    def test_there_is_nothing_to_connect_without_a_token(self, on_telegram, world):
        response = on_telegram.post(CONNECT)
        assert response.status_code == 409 and world.bot.registered == []

    def test_a_client_on_another_channel_has_nothing_to_connect(self, alpha):
        assert alpha.post(CONNECT).status_code == 409

    def test_a_deployment_without_a_public_address_cannot_connect_a_bot(self, world, on_telegram):
        world.application.settings.public_base_url = None
        save_token(on_telegram)
        response = on_telegram.post(CONNECT)
        assert response.status_code == 409 and "administrator" in response.json()["detail"]
        assert world.bot.registered == []

    @pytest.mark.parametrize("address", ["http://revansa.example", "revansa.example", "https://"])
    def test_telegram_only_delivers_to_https(self, world, on_telegram, address):
        world.application.settings.public_base_url = address
        save_token(on_telegram)
        assert on_telegram.post(CONNECT).status_code == 409

    def test_a_public_address_with_a_path_is_kept(self, world, on_telegram):
        world.application.settings.public_base_url = "https://example.org/revansa/"
        save_token(on_telegram)
        on_telegram.post(CONNECT)
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
        self, world, on_telegram, kind, words
    ):
        save_token(on_telegram)
        world.bot.bot_failure = kind
        response = on_telegram.post(CONNECT)
        assert response.status_code == 200 and response.json()["ok"] is False
        assert words in response.json()["message"] and TOKEN not in response.text
        assert stored(world, "telegram.webhook_secret") is None
        assert on_telegram.get(CHANNEL).json()["telegram"]["connected"] is False

    def test_an_address_telegram_refuses_leaves_the_bot_not_connected(self, world, on_telegram):
        save_token(on_telegram)
        world.bot.register_failure = "rejected"
        response = on_telegram.post(CONNECT)
        assert response.json()["ok"] is False and "address" in response.json()["message"]
        assert stored(world, "telegram.webhook_secret") is None

    def test_a_connect_made_too_soon_after_another_waits(self, world, on_telegram, monkeypatch):
        from sales_app.api import client_channel

        monkeypatch.setattr(client_channel, "TEST_MIN_INTERVAL_SECONDS", 60.0)
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        assert on_telegram.post(CONNECT).status_code == 429


# ---- the test ----------------------------------------------------------------------------------


class TestTheTest:
    def test_without_a_token_it_says_so(self, on_telegram):
        body = on_telegram.post(TEST).json()
        assert body["ok"] is False and "not set yet" in body["message"]

    def test_a_working_connected_bot(self, world, on_telegram):
        save_token(on_telegram)
        on_telegram.post(CONNECT)
        body = on_telegram.post(TEST).json()
        assert body["ok"] is True and body["details"] == {"bot": "ok", "webhook": "ok"}
        assert body["channel"] == "telegram" and body["checked_at"]

    def test_a_bot_that_was_never_connected_says_to_connect_it(self, on_telegram):
        save_token(on_telegram)
        body = on_telegram.post(TEST).json()
        assert body["ok"] is False and body["details"]["webhook"] == "not_set"
        assert "connect the bot" in body["message"]

    @pytest.mark.parametrize(
        ("state", "words"),
        [("other_url", "somewhere else"), ("failing", "cannot deliver")],
    )
    def test_a_webhook_that_is_not_as_it_should_be(self, world, on_telegram, state, words):
        save_token(on_telegram)
        world.bot.state = state
        body = on_telegram.post(TEST).json()
        assert body["ok"] is False and words in body["message"]

    def test_a_refused_token(self, world, on_telegram):
        save_token(on_telegram)
        world.bot.bot_failure = "auth"
        body = on_telegram.post(TEST).json()
        assert body["ok"] is False and body["details"] == {"bot": "auth"}
        assert TOKEN not in json.dumps(body)

    def test_a_deployment_with_no_public_address_says_it_cannot_receive(self, world, on_telegram):
        save_token(on_telegram)
        world.application.settings.public_base_url = None
        body = on_telegram.post(TEST).json()
        assert body["ok"] is False and "public address" in body["message"]
        assert body["details"]["bot"] == "ok" and body["details"]["webhook"] == "unchecked"

    def test_testing_sends_nothing_to_anyone(self, world, on_telegram):
        save_token(on_telegram)
        on_telegram.post(TEST)
        assert world.bot.registered == []  # a test never changes where Telegram delivers


# ---- readiness ---------------------------------------------------------------------------------


class TestReadiness:
    def test_a_client_on_telegram_is_asked_for_its_bot_not_a_whatsapp_number(self, on_telegram):
        checks = {c["id"]: c for c in on_telegram.get(READINESS).json()["checks"]}
        assert "whatsapp_number" not in checks and checks["channel_ready"]["ok"] is False

    def test_the_bot_is_what_is_missing_then_the_connection(self, on_telegram):
        assert "token" in ready(on_telegram)["message"].lower()
        save_token(on_telegram)
        assert "connect" in ready(on_telegram)["message"].lower()
        on_telegram.post(CONNECT)
        assert ready(on_telegram)["ok"] is True

    def test_what_is_missing_is_something_the_client_can_do_itself(self, on_telegram):
        assert "administrator" not in ready(on_telegram)["message"].lower()

    def test_the_agent_cannot_start_on_a_bot_that_is_not_connected(self, on_telegram):
        save_token(on_telegram)
        response = on_telegram.post(START)
        assert response.status_code == 409
        assert "channel_ready" in {r["id"] for r in response.json()["detail"]["reasons"]}
