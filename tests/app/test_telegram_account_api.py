"""Telegram from the client's own account, through the Client and Admin APIs.

The client chooses its own account, signs in with Telegram (phone, code, two-step password) and can
disconnect; it never sees or sends anything technical, and nothing it types comes back. The Admin sets
the platform's Telegram application and can see, disconnect and un-limit an account. Telegram is a
fake (`FakeAccountClient`): this proves the API's behaviour, not a real account's.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))


import json

import pytest
from telegram_account_double import FakeAccountClient
from test_client_agent_api import READINESS, World
from test_telegram_channel_api import CHANNEL, allow_telegram, choose, ready

from sales_app.config.secrets import TELEGRAM_API_HASH, TELEGRAM_API_ID
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    InMemorySecretAudit,
    InMemorySecretStore,
    client_scope,
)
from sales_app.send_cap import InMemorySendCapStore, InMemorySendCounter, SendLimits
from sales_app.telegram_account import TelegramAccountService

ACCOUNT = f"{CHANNEL}/telegram/account"
PHONE = "+216 20 123 456"
CODE = FakeAccountClient.CODE
PASSWORD = FakeAccountClient.PASSWORD


class Telegram:
    def __init__(self):
        self.default = FakeAccountClient()

    def __call__(self, api_id, api_hash, session):
        return self.default


def admin_account(cid="alpha"):
    return f"/admin/v1/clients/{cid}/integrations/telegram/account"


@pytest.fixture
def world(tmp_path):
    store = InMemorySecretStore()
    w = World(tmp_path, secret_store=store, secret_audit=InMemorySecretAudit())
    w.secrets, w.telegram = store, Telegram()
    app = w.application
    app.settings.public_base_url = "https://revansa.example"
    app.telegram_accounts = TelegramAccountService(
        secrets=store,
        clock=w.clock,
        factory=w.telegram,
        available=lambda: True,
    )
    app.send_limits = SendLimits(InMemorySendCapStore(), InMemorySendCounter(), w.clock)
    yield w
    app.close()


@pytest.fixture
def alpha(world):
    return world.browser("alpha")


@pytest.fixture
def admin(world):
    return world.admin()


def set_platform(world):
    store = world.secrets
    store.set(GLOBAL_SCOPE, TELEGRAM_API_ID, "123456", by="admin")
    store.set(GLOBAL_SCOPE, TELEGRAM_API_HASH, "0123456789abcdef0123456789abcdef", by="admin")


@pytest.fixture
def on_account(world, alpha, admin):
    allow_telegram(admin)
    set_platform(world)
    response = choose(alpha, telegram={"use_account": True})
    assert response.status_code == 200, response.text
    return alpha


def sign_in(client, phone=PHONE, code=CODE):
    assert client.post(f"{ACCOUNT}/start", json={"phone": phone}).status_code == 200
    return client.post(f"{ACCOUNT}/code", json={"code": code})


class TestChoosingTheAccount:
    def test_the_client_chooses_its_own_account_and_the_bot_sentences_stay_as_they_were(
        self, on_account
    ):
        body = on_account.get(CHANNEL).json()
        assert body["telegram"]["mode"] == "account"
        assert "consent_field" not in body["telegram"]  # nobody has to be marked any more
        assert body["telegram"]["max_new_conversations_per_day"] == 5

    def test_the_client_can_lower_the_new_conversations_a_day(self, on_account):
        body = choose(on_account, telegram={"max_new_conversations_per_day": 3}).json()
        assert body["telegram"]["max_new_conversations_per_day"] == 3

    def test_the_consent_column_no_longer_exists(self, on_account):
        assert choose(on_account, telegram={"consent_field": "opt in"}).status_code == 422
        assert on_account.get(f"{CHANNEL}/telegram/waiting").status_code in (404, 405)

    def test_more_than_five_new_conversations_a_day_is_refused(self, on_account):
        assert choose(on_account, telegram={"max_new_conversations_per_day": 6}).status_code == 422
        assert (
            choose(on_account, telegram={"max_new_conversations_per_day": 500}).status_code == 422
        )
        assert choose(on_account, telegram={"max_new_conversations_per_day": 0}).status_code == 422

    def test_the_client_can_go_back_to_the_bot(self, on_account):
        body = choose(on_account, telegram={"use_account": False}).json()
        assert body["telegram"]["mode"] == "bot"
        # The sign-in does not depend on today's channel: it is made from the Connections screen.
        assert on_account.get(ACCOUNT).status_code == 200

    def test_the_account_routes_need_telegram_to_be_open_and_a_session(self, world, alpha, admin):
        assert alpha.get(ACCOUNT).status_code == 403  # Telegram is not open to this client yet
        allow_telegram(admin)
        assert alpha.get(ACCOUNT).status_code == 200  # open: whatever the channel is today
        assert world.anonymous().get(ACCOUNT).status_code == 401
        for method, path in (
            ("post", f"{ACCOUNT}/start"),
            ("post", f"{ACCOUNT}/code"),
            ("post", f"{ACCOUNT}/password"),
            ("delete", ACCOUNT),
        ):
            assert getattr(world.anonymous(), method)(path).status_code == 401

    def test_taking_the_channel_away_closes_the_routes(self, on_account, admin):
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["telegram"]})
        assert on_account.get(ACCOUNT).status_code == 403


class TestWhereItStands:
    def test_it_waits_for_the_platform_when_the_platform_has_not_set_telegram_up(
        self, world, alpha, admin
    ):
        allow_telegram(admin)
        choose(alpha, telegram={"use_account": True})
        body = alpha.get(CHANNEL).json()
        assert body["status"] == "waiting_platform"
        assert alpha.get(ACCOUNT).json()["available"] is False

    def test_it_asks_the_client_to_connect_then_is_ready(self, on_account):
        body = on_account.get(CHANNEL).json()
        assert (
            body["status"] == "action_required"
            and "Connect your Telegram" in body["status_message"]
        )
        assert on_account.get(ACCOUNT).json()["state"] == "not_connected"
        assert sign_in(on_account).status_code == 200
        body = on_account.get(CHANNEL).json()
        assert body["status"] == "ready" and body["status_message"] is None
        assert ready(on_account)["ok"] in (True, False)  # the agent's own checks say the rest

    def test_a_session_telegram_ended_asks_for_a_new_sign_in(self, world, on_account):
        sign_in(on_account)
        world.application.telegram_accounts.mark_reauth_required("alpha")
        body = on_account.get(CHANNEL).json()
        assert body["status"] == "action_required" and "sign in again" in body["status_message"]
        assert on_account.get(ACCOUNT).json()["state"] == "reauth_required"
        sign_in(on_account)
        assert on_account.get(CHANNEL).json()["status"] == "ready"

    def test_a_limited_account_stays_ready_for_replies_and_says_why(self, world, on_account):
        sign_in(on_account)
        world.application.telegram_accounts.mark_limited("alpha")
        body = on_account.get(CHANNEL).json()
        assert body["status"] == "ready" and "limited" in body["status_message"]
        assert on_account.get(ACCOUNT).json()["limited"] is True

    def test_a_platform_without_a_database_is_not_ready_to_ration_first_messages(
        self, world, on_account
    ):
        sign_in(on_account)
        world.application.send_limits = None
        assert on_account.get(CHANNEL).json()["status"] == "waiting_platform"

    def test_the_agent_is_not_ready_until_the_account_is_connected(self, world, on_account):
        checks = {c["id"]: c for c in on_account.get(READINESS).json()["checks"]}
        assert not checks["channel_ready"]["ok"]


class TestSigningInThroughTheApi:
    def test_the_phone_code_and_password_are_never_echoed(self, world, on_account):
        world.telegram.default = FakeAccountClient(two_step=True)
        started = on_account.post(f"{ACCOUNT}/start", json={"phone": PHONE})
        assert started.json()["login"] == "code"
        asked = on_account.post(f"{ACCOUNT}/code", json={"code": CODE})
        assert asked.json()["login"] == "password"
        done = on_account.post(f"{ACCOUNT}/password", json={"password": PASSWORD})
        assert done.json()["state"] == "connected" and done.json()["login"] is None
        for response in (started, asked, done):
            text = response.text
            assert "20123456" not in text and CODE not in text and PASSWORD not in text
            assert "session" not in json.dumps(list(response.json())).lower()

    def test_refusals_say_what_went_wrong_without_repeating_what_was_typed(self, on_account):
        bad_phone = on_account.post(f"{ACCOUNT}/start", json={"phone": "zzz-private"})
        assert bad_phone.status_code == 422 and "zzz-private" not in bad_phone.text
        on_account.post(f"{ACCOUNT}/start", json={"phone": PHONE})
        wrong = on_account.post(f"{ACCOUNT}/code", json={"code": "00000"})
        assert wrong.status_code == 422 and "00000" not in wrong.text
        extra = on_account.post(f"{ACCOUNT}/code", json={"code": CODE, "token": "x"})
        assert extra.status_code == 422 and "token" in extra.text and '"x"' not in extra.text
        stray = on_account.post(f"{ACCOUNT}/password", json={"password": "hunter2"})
        assert stray.status_code == 409 and "hunter2" not in stray.text

    def test_a_step_without_a_sign_in_under_way_is_a_conflict(self, on_account):
        assert on_account.post(f"{ACCOUNT}/code", json={"code": CODE}).status_code == 409

    def test_telegram_asking_to_wait_is_a_429_with_the_delay(self, world, on_account):
        world.telegram.default.fail("request_code", "login_flood", retry_after=900)
        response = on_account.post(f"{ACCOUNT}/start", json={"phone": PHONE})
        assert response.status_code == 429 and response.headers["Retry-After"] == "900"

    def test_an_account_already_used_by_another_client_is_refused(self, world, on_account, admin):
        beta = world.browser("beta")
        admin.put("/admin/v1/clients/beta/channels", json={"added": ["telegram"]})
        choose(beta, telegram={"use_account": True})
        assert sign_in(on_account).status_code == 200
        response = sign_in(beta)
        assert response.status_code == 409 and "another account" in response.text
        assert beta.get(ACCOUNT).json()["state"] == "not_connected"

    def test_the_client_can_give_up_a_sign_in(self, on_account):
        on_account.post(f"{ACCOUNT}/start", json={"phone": PHONE})
        assert on_account.post(f"{ACCOUNT}/cancel").json()["login"] is None

    def test_disconnecting_ends_the_session_and_the_agent_is_no_longer_ready(
        self, world, on_account
    ):
        sign_in(on_account)
        response = on_account.delete(ACCOUNT)
        assert response.status_code == 200 and response.json() == {
            "disconnected": True,
            "ended_at_telegram": True,
        }
        assert world.telegram.default.logged_out
        assert on_account.get(ACCOUNT).json()["state"] == "not_connected"
        assert on_account.get(CHANNEL).json()["status"] == "action_required"
        assert world.secrets.entries(client_scope("alpha")) == ()

    def test_disconnecting_says_so_when_telegram_could_not_be_told(self, world, on_account):
        sign_in(on_account)
        world.telegram.default.fail("log_out", "unreachable")
        assert on_account.delete(ACCOUNT).json()["ended_at_telegram"] is False

    def test_the_daily_figures_count_new_conversations(self, world, on_account):
        sign_in(on_account)
        guard = world.application.send_limits.fixed_guard("alpha", "telegram_new", 5)
        guard.reserve()
        body = on_account.get(ACCOUNT).json()
        assert body["new_today"] == 1 and body["daily_limit"] == 5

    def test_one_client_never_reaches_another_clients_account(self, world, on_account, admin):
        beta = world.browser("beta")
        admin.put("/admin/v1/clients/beta/channels", json={"added": ["telegram"]})
        choose(beta, telegram={"use_account": True})
        sign_in(on_account)
        assert beta.get(ACCOUNT).json()["state"] == "not_connected"
        beta.delete(ACCOUNT)
        assert on_account.get(ACCOUNT).json()["state"] == "connected"


class TestTheAdmin:
    def test_it_sees_where_the_account_stands_without_any_secret(self, world, on_account, admin):
        body = admin.get(admin_account()).json()
        assert body["state"] == "not_connected" and body["platform_configured"] is True
        sign_in(on_account)
        text = admin.get(admin_account()).text
        assert "connected" in text and "session-of" not in text and "900001" not in text

    def test_it_is_told_what_the_platform_still_lacks(self, world, alpha, admin):
        world.application.telegram_accounts._available = lambda: False
        body = admin.get(admin_account()).json()
        assert body["platform_configured"] is False
        assert any("api_id" in m for m in body["platform_missing"])

    def test_it_can_disconnect_an_account_and_lift_a_limit(self, world, on_account, admin):
        sign_in(on_account)
        world.application.telegram_accounts.mark_limited("alpha")
        assert admin.post(f"{admin_account()}/clear-limit").json()["limited"] is False
        assert admin.post(f"{admin_account()}/disconnect").json()["state"] == "not_connected"
        assert world.telegram.default.logged_out

    def test_a_client_cannot_use_the_admin_routes(self, on_account):
        for method, path in (
            ("get", admin_account()),
            ("post", f"{admin_account()}/disconnect"),
            ("post", f"{admin_account()}/clear-limit"),
        ):
            assert getattr(on_account, method)(path).status_code == 401

    def test_only_an_owner_may_disconnect_or_lift_a_limit(self, world):
        from test_admin_clients_api import PASSWORD
        from test_email_channel_api import _admin

        from sales_app.admin_auth import AdminRole

        world.users.create(_admin("eve", AdminRole.EDITOR))
        editor = world.anonymous()
        editor.post("/admin/v1/auth/login", json={"username": "eve", "password": PASSWORD})
        assert editor.post(f"{admin_account()}/disconnect").status_code == 403
        assert editor.post(f"{admin_account()}/clear-limit").status_code == 403

    def test_the_platforms_application_is_stored_write_only(self, admin):
        for key, value in (
            ("telegram.api_id", "123456"),
            ("telegram.api_hash", "0123456789abcdef0123456789abcdef"),
        ):
            response = admin.put(f"/admin/v1/settings/integrations/{key}", json={"value": value})
            assert response.status_code == 200, response.text
            assert value not in response.text or key == "telegram.api_id"  # the id is public
        assert (
            admin.put(
                "/admin/v1/settings/integrations/telegram.api_hash", json={"value": "too short"}
            ).status_code
            == 422
        )

    def test_the_platform_status_names_what_is_missing_and_never_a_value(self, world, admin):
        status = "/admin/v1/settings/integrations/telegram-account/status"
        assert admin.get(status).json()["configured"] is False
        set_platform(world)
        body = admin.get(status).json()
        assert body["configured"] is True and "0123456789abcdef" not in admin.get(status).text
