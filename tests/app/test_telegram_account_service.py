"""The client's own Telegram account, in the application: signing in, what is kept, and what ends it.

Telegram is a fake (`FakeAccountClient`). It proves what is stored and when, what is refused, and that
nothing sensitive leaves; not that the real service behaves so.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))


from dataclasses import replace
from datetime import timedelta

import pytest
from factories import NOW
from telegram_account_double import FakeAccountClient

from sales_app.config.secrets import (
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_USER_ACCOUNT,
    TELEGRAM_USER_LIMITED,
    TELEGRAM_USER_REAUTH,
    TELEGRAM_USER_SESSION,
)
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore, client_scope
from sales_app.telegram_account import (
    CONNECTED,
    NOT_CONNECTED,
    REAUTH_REQUIRED,
    AccountLoginError,
    AccountNotReadyError,
    TelegramAccountService,
    normalise_phone,
)
from sales_integrations.fakes import FakeClock
from sales_integrations.telegram import TelegramAccountChannel, TelegramAccountInbox

PHONE = "+216 20 123 456"
CODE = FakeAccountClient.CODE
PASSWORD = FakeAccountClient.PASSWORD


class Telegram:
    """What the service talks to: one account per phone number, as a real Telegram would."""

    def __init__(self):
        self.accounts: dict[str, FakeAccountClient] = {}
        self.default = FakeAccountClient()
        self.built: list[tuple[int, str, str | None]] = []

    def __call__(self, api_id, api_hash, session):
        self.built.append((api_id, api_hash, session))
        return self.default


class Config:
    """Just what `channel` and `inbox` read of a client's configuration."""

    def __init__(self, client_id):
        self.client_id = client_id


def make(*, platform=True, available=True):
    store, clock, telegram = InMemorySecretStore(), FakeClock(NOW), Telegram()
    if platform:
        store.set(GLOBAL_SCOPE, TELEGRAM_API_ID, "123456", by="admin")
        store.set(GLOBAL_SCOPE, TELEGRAM_API_HASH, "0123456789abcdef0123456789abcdef", by="admin")
    service = TelegramAccountService(
        secrets=store, clock=clock, factory=telegram, available=lambda: available
    )
    return service, store, clock, telegram


def signed_in(service, client_id="alpha", **kw):
    service.start(client_id, PHONE)
    return service.confirm_code(client_id, kw.get("code", CODE))


class TestThePlatform:
    def test_it_is_offered_only_with_the_platform_application_and_the_library(self):
        assert make()[0].configured()
        assert not make(platform=False)[0].configured()
        assert not make(available=False)[0].configured()

    def test_what_is_missing_is_named_never_valued(self):
        missing = make(platform=False, available=False)[0].missing()
        assert any("api_id" in m for m in missing) and any("telethon" in m for m in missing)

    def test_nothing_can_be_started_without_it(self):
        service = make(platform=False)[0]
        with pytest.raises(AccountLoginError) as error:
            service.start("alpha", PHONE)
        assert error.value.kind == "not_configured"


class TestSigningIn:
    def test_a_phone_is_normalised_and_nonsense_refused(self):
        assert normalise_phone("+216 20 123 456") == "+21620123456"
        assert normalise_phone("(216) 20-123-456") == "+21620123456"
        for bad in ("", "abc", "12", "+1234567890123456789", "+216 20 123 45a"):
            assert normalise_phone(bad) is None

    def test_a_correct_code_connects_the_account_and_keeps_only_the_session(self):
        service, store, _, telegram = make()
        service.start("alpha", PHONE)
        assert (
            service.status("alpha").login == "code"
            and service.status("alpha").state == NOT_CONNECTED
        )
        status = service.confirm_code("alpha", CODE)
        assert status.state == CONNECTED and status.login is None and not status.limited
        scope = client_scope("alpha")
        assert store.get(scope, TELEGRAM_USER_SESSION) == "session-of-900001"
        assert store.get(scope, TELEGRAM_USER_ACCOUNT) == "900001"
        # the code and the phone number are kept nowhere
        for entry in store.entries(scope):
            value = store.get(scope, entry.key)
            assert CODE not in value and "20123456" not in value
        assert telegram.default.requested == ["+21620123456"]

    def test_a_wrong_code_is_refused_and_can_be_retried(self):
        service = make()[0]
        service.start("alpha", PHONE)
        with pytest.raises(AccountLoginError) as error:
            service.confirm_code("alpha", "99999")
        assert error.value.kind == "bad_code"
        assert service.confirm_code("alpha", CODE).state == CONNECTED

    def test_a_code_that_is_not_digits_is_refused_before_telegram_is_asked(self):
        service = make()[0]
        service.start("alpha", PHONE)
        with pytest.raises(AccountLoginError) as error:
            service.confirm_code("alpha", "ab")
        assert error.value.kind == "bad_code"

    def test_a_two_step_password_is_asked_after_the_code_and_never_kept(self):
        service, store, _, telegram = make()
        telegram.default = FakeAccountClient(two_step=True)
        service.start("alpha", PHONE)
        status = service.confirm_code("alpha", CODE)
        assert status.state == NOT_CONNECTED and status.login == "password"
        with pytest.raises(AccountLoginError) as error:
            service.confirm_password("alpha", "wrong")
        assert error.value.kind == "bad_password"
        assert service.confirm_password("alpha", PASSWORD).state == CONNECTED
        scope = client_scope("alpha")
        assert all(PASSWORD not in (store.get(scope, e.key) or "") for e in store.entries(scope))

    def test_steps_out_of_order_are_refused(self):
        service = make()[0]
        for step in (
            lambda: service.confirm_code("alpha", CODE),
            lambda: service.confirm_password("alpha", "x"),
        ):
            with pytest.raises(AccountLoginError) as error:
                step()
            assert error.value.kind == "no_login"
        service.start("alpha", PHONE)
        with pytest.raises(AccountLoginError):
            service.confirm_password("alpha", PASSWORD)  # a code is expected first

    def test_a_sign_in_expires_after_ten_minutes(self):
        service, _, clock, _ = make()
        service.start("alpha", PHONE)
        clock.advance(timedelta(minutes=11))
        assert service.status("alpha").login is None
        with pytest.raises(AccountLoginError) as error:
            service.confirm_code("alpha", CODE)
        assert error.value.kind == "no_login"

    def test_too_many_wrong_codes_end_the_sign_in(self):
        service = make()[0]
        service.start("alpha", PHONE)
        kinds = []
        for _ in range(7):
            try:
                service.confirm_code("alpha", "00000")
            except AccountLoginError as error:
                kinds.append(error.kind)
        assert kinds[:5] == ["bad_code"] * 5 and kinds[5:] == ["too_many", "no_login"]

    def test_a_client_cannot_ask_for_codes_without_limit(self):
        service = make()[0]
        for _ in range(5):
            service.start("alpha", PHONE)
        with pytest.raises(AccountLoginError) as error:
            service.start("alpha", PHONE)
        assert error.value.kind == "too_many"
        service.start("beta", PHONE)  # another client is not affected

    def test_a_telegram_flood_answer_is_passed_on_with_its_delay(self):
        service, _, _, telegram = make()
        telegram.default.fail("request_code", "login_flood", retry_after=3600)
        with pytest.raises(AccountLoginError) as error:
            service.start("alpha", PHONE)
        assert error.value.kind == "flood" and error.value.retry_after == 3600

    def test_nothing_is_kept_when_the_sign_in_does_not_finish(self):
        service, store, _, _ = make()
        service.start("alpha", PHONE)
        assert store.entries(client_scope("alpha")) == ()


class TestOneAccountOneClient:
    def test_an_account_cannot_serve_two_clients(self):
        service, store, _, telegram = make()
        signed_in(service, "alpha")
        service.start("beta", PHONE)
        with pytest.raises(AccountLoginError) as error:
            service.confirm_code("beta", CODE)
        assert error.value.kind == "already_used"
        assert store.get(client_scope("beta"), TELEGRAM_USER_SESSION) is None
        assert telegram.default.logged_out  # the session just made was ended at Telegram

    def test_signing_in_again_replaces_the_session_and_clears_the_marks(self):
        service, store, _, _ = make()
        signed_in(service)
        service.mark_reauth_required("alpha")
        service.mark_limited("alpha")
        assert service.status("alpha").state == REAUTH_REQUIRED and service.status("alpha").limited
        signed_in(service)
        status = service.status("alpha")
        assert status.state == CONNECTED
        assert store.get(client_scope("alpha"), TELEGRAM_USER_REAUTH) is None
        assert status.limited  # the same account: Telegram's limit is still on it

    def test_a_different_account_does_not_inherit_the_limit(self):
        service, store, _, telegram = make()
        signed_in(service)
        service.mark_limited("alpha")
        telegram.default = FakeAccountClient(user_id="900002")
        signed_in(service)
        assert not service.status("alpha").limited
        assert store.get(client_scope("alpha"), TELEGRAM_USER_LIMITED) is None


class TestDisconnecting:
    def test_it_ends_the_session_at_telegram_and_forgets_it_here(self):
        service, store, _, telegram = make()
        signed_in(service)
        assert service.disconnect("alpha") is True
        assert telegram.default.logged_out
        assert service.status("alpha").state == NOT_CONNECTED
        assert store.entries(client_scope("alpha")) == ()

    def test_it_is_forgotten_here_even_when_telegram_cannot_be_reached(self):
        service, store, _, telegram = make()
        signed_in(service)
        telegram.default.fail("log_out", "unreachable")
        assert service.disconnect("alpha") is False  # the person is told to end it from Telegram
        assert store.entries(client_scope("alpha")) == ()

    def test_a_session_telegram_already_ended_counts_as_ended(self):
        service, _, _, telegram = make()
        signed_in(service)
        telegram.default.fail("log_out", "auth_lost")
        assert service.disconnect("alpha") is True

    def test_disconnecting_when_nothing_is_connected_is_harmless(self):
        assert make()[0].disconnect("alpha") is True

    def test_it_cancels_a_sign_in_under_way(self):
        service = make()[0]
        service.start("alpha", PHONE)
        service.disconnect("alpha")
        assert service.status("alpha").login is None

    def test_one_clients_disconnect_leaves_another_client_alone(self):
        service, store, _, telegram = make()
        signed_in(service, "alpha")
        telegram.default = FakeAccountClient(user_id="900002")
        signed_in(service, "beta")
        service.disconnect("alpha")
        assert service.status("beta").state == CONNECTED
        assert store.get(client_scope("beta"), TELEGRAM_USER_ACCOUNT) == "900002"


class TestTheRuntime:
    def test_nothing_is_built_for_a_client_that_is_not_connected(self):
        service = make()[0]
        for build in (service.channel, service.inbox):
            with pytest.raises(AccountNotReadyError):
                build(Config("alpha"))

    def test_a_session_to_renew_builds_nothing(self):
        service = make()[0]
        signed_in(service)
        service.mark_reauth_required("alpha")
        with pytest.raises(AccountNotReadyError, match="renewed"):
            service.channel(Config("alpha"))

    def test_the_channel_and_inbox_use_the_clients_own_session(self):
        service, _, _, telegram = make()
        signed_in(service)
        telegram.built.clear()
        channel, inbox = service.channel(Config("alpha")), service.inbox(Config("alpha"))
        assert isinstance(channel, TelegramAccountChannel)
        assert isinstance(inbox, TelegramAccountInbox) and inbox.account_id == "900001"
        assert (
            telegram.built
            == [(123456, "0123456789abcdef0123456789abcdef", "session-of-900001")] * 2
        )

    def test_the_channel_reports_what_telegram_did_to_the_account(self):
        service, _, _, telegram = make()
        signed_in(service)
        channel = service.channel(Config("alpha"))
        telegram.default.fail("send_text", "peer_flood")
        from sales_core.contracts import Channel, MessageDraft, OutboundMessage

        message = OutboundMessage(
            client_id="alpha",
            prospect_id="p1",
            channel=Channel.TELEGRAM,
            recipient="424242",
            draft=MessageDraft(text="Hi"),
            idempotency_key="k",
            first_contact=True,
        )
        assert not channel.send(message).ok
        assert service.status("alpha").limited
        telegram.default.fail("send_text", "auth_lost")
        channel.send(replace(message, first_contact=False))
        assert service.status("alpha").state == REAUTH_REQUIRED

    def test_the_admin_lifts_a_limit_and_only_the_admin(self):
        service = make()[0]
        signed_in(service)
        service.mark_limited("alpha")
        service.clear_limited("alpha")
        assert not service.status("alpha").limited
