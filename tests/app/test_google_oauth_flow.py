"""The Gmail OAuth connection flow of the app layer, against fakes (no network, no real Google).

Proved: signed single-use state (tamper, replay, expiry, cross-client), the connection service, the
token source (cache, 60 s margin, one refresh under concurrency, invalid_grant), the XOAUTH2 wrappers
(a fresh token at each operation), a login proof stable across access tokens, and no token in any
repr, log or error. Not proved: that Gmail accepts any of it.
"""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from sales_app.google_mailbox import (
    CONNECTED,
    NOT_CONNECTED,
    REAUTH_REQUIRED,
    GoogleMailboxConnection,
    GoogleTokenSource,
    InMemoryGoogleGrantStore,
    InMemoryPendingAuthorizationStore,
    InvalidGoogleStateError,
    OAuthMailChannel,
    OAuthMailInbox,
    mailbox_login_proof,
    refresh_token_fingerprint,
    sign_state,
    verify_state,
)
from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_integrations.fakes import FakeClock
from sales_integrations.google_oauth import (
    AccessGrant,
    GoogleAccessDeniedError,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
    TokenGrant,
)
from sales_integrations.mail import MailAuthError, MailUnavailableError

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
KEY = b"k" * 32
ADDRESS = "sales@gmail.com"
REFRESH = "1//SECRET-refresh"
CODE = "4/SECRET-code"


class FakeOAuth:
    def __init__(self):
        self.n = 0
        self.refresh_calls = 0
        self.refresh_error = None
        self.revoked = []
        self.revoke_error = None
        self.exchanged = []
        self.refresh_token_returned = REFRESH
        self.delay = 0.0

    def authorization_url(self, *, state, code_challenge, login_hint=None):
        return f"https://accounts.example/auth?state={state}&cc={code_challenge}"

    def exchange_code(self, *, code, code_verifier):
        self.exchanged.append((code, code_verifier))
        return TokenGrant("ya29.first", self.refresh_token_returned, 3600, "https://mail.google.com/ openid email")

    def refresh(self, refresh_token):
        self.refresh_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.refresh_error:
            raise self.refresh_error
        self.n += 1
        return AccessGrant(f"ya29.token-{self.n}", 3600)

    def revoke(self, token):
        if self.revoke_error:
            raise self.revoke_error
        self.revoked.append(token)

    def account_address(self, access_token):
        return ADDRESS


def build(**kw):
    oauth, grants, pending, clock = (
        FakeOAuth(),
        InMemoryGoogleGrantStore(),
        InMemoryPendingAuthorizationStore(),
        FakeClock(NOW),
    )
    service = GoogleMailboxConnection(
        oauth=oauth, grants=grants, pending=pending, signing_key=KEY, clock=clock, **kw
    )
    return service, oauth, grants, pending, clock


def state_of(url):
    return url.split("state=")[1].split("&")[0]


def connected(client="alpha"):
    service, oauth, grants, pending, clock = build()
    state = state_of(service.start(client))
    service.callback(state=state, client_id=client, code=CODE)
    return service, oauth, grants, pending, clock


class TestState:
    def test_a_good_callback_connects(self):
        service, oauth, grants, *_ = build()
        assert service.status("alpha").state == NOT_CONNECTED
        url = service.start("alpha")
        status = service.callback(state=state_of(url), client_id="alpha", code=CODE)
        assert (status.state, status.address) == (CONNECTED, ADDRESS)
        assert grants.load("alpha").refresh_token == REFRESH
        assert oauth.exchanged[0][0] == CODE and oauth.exchanged[0][1]

    def test_a_tampered_state_is_refused(self):
        service, *_ = build()
        state = state_of(service.start("alpha"))
        body, sig = state.split(".")
        for bad in (body + "." + sig[:-2] + "AA", "x" + body[1:] + "." + sig, "garbage", "", "a.b"):
            with pytest.raises(InvalidGoogleStateError):
                service.callback(state=bad, client_id="alpha", code=CODE)

    def test_a_state_signed_with_another_key_is_refused(self):
        with pytest.raises(InvalidGoogleStateError):
            verify_state(b"z" * 32, sign_state(KEY, "alpha", "s", NOW + timedelta(minutes=1)), NOW)

    def test_a_state_cannot_be_replayed(self):
        service, oauth, *_ = build()
        state = state_of(service.start("alpha"))
        service.callback(state=state, client_id="alpha", code=CODE)
        with pytest.raises(InvalidGoogleStateError):
            service.callback(state=state, client_id="alpha", code=CODE)
        assert len(oauth.exchanged) == 1

    def test_an_expired_state_is_refused(self):
        service, _, _, _, clock = build()
        state = state_of(service.start("alpha"))
        clock.advance(timedelta(minutes=11))
        with pytest.raises(InvalidGoogleStateError):
            service.callback(state=state, client_id="alpha", code=CODE)

    def test_a_state_of_another_client_is_refused_and_consumed(self):
        service, oauth, grants, *_ = build()
        state = state_of(service.start("alpha"))
        with pytest.raises(InvalidGoogleStateError):
            service.callback(state=state, client_id="beta", code=CODE)
        with pytest.raises(InvalidGoogleStateError):
            service.callback(state=state, client_id="alpha", code=CODE)
        assert (
            oauth.exchanged == [] and grants.load("alpha") is None and grants.load("beta") is None
        )

    def test_the_error_is_the_same_whatever_the_cause(self):
        service, *_ = build()
        state = state_of(service.start("alpha"))
        messages = set()
        for bad in ("junk", state):
            try:
                service.callback(state=bad, client_id="beta", code=CODE)
            except InvalidGoogleStateError as e:
                messages.add(str(e))
        assert len(messages) == 1

    def test_access_denied_is_typed_and_consumes_the_state(self):
        service, *_ = build()
        state = state_of(service.start("alpha"))
        with pytest.raises(GoogleAccessDeniedError):
            service.callback(state=state, client_id="alpha", error="access_denied")
        with pytest.raises(InvalidGoogleStateError):
            service.callback(state=state, client_id="alpha", code=CODE)
        assert service.status("alpha").state == NOT_CONNECTED

    def test_no_refresh_token_connects_nothing(self):
        service, oauth, *_ = build()
        oauth.refresh_token_returned = None
        with pytest.raises(GoogleProtocolError):
            service.callback(state=state_of(service.start("alpha")), client_id="alpha", code=CODE)
        assert service.status("alpha").state == NOT_CONNECTED

    def test_a_short_key_is_refused(self):
        with pytest.raises(ValueError):
            build_with_key(b"short")


def build_with_key(key):
    return GoogleMailboxConnection(
        oauth=FakeOAuth(),
        grants=InMemoryGoogleGrantStore(),
        pending=InMemoryPendingAuthorizationStore(),
        signing_key=key,
        clock=FakeClock(NOW),
    )


class TestConnection:
    def test_disconnect_revokes_and_forgets(self):
        service, oauth, grants, *_ = connected()
        assert service.disconnect("alpha") is True
        assert oauth.revoked == [REFRESH] and grants.load("alpha") is None
        assert service.status("alpha").state == NOT_CONNECTED

    def test_disconnect_forgets_even_if_google_is_down(self):
        service, oauth, grants, *_ = connected()
        oauth.revoke_error = GoogleTransientError("x")
        assert service.disconnect("alpha") is False
        assert grants.load("alpha") is None

    def test_clients_are_isolated(self):
        service, *_ = connected("alpha")
        assert service.status("beta").state == NOT_CONNECTED

    def test_reauth_state(self):
        service, _, grants, *_ = connected()
        grants.mark_reauth_required("alpha")
        assert service.status("alpha").state == REAUTH_REQUIRED
        assert service.proof("alpha") is None


class TestTokenSource:
    def source(self, oauth, grants, clock):
        return GoogleTokenSource(client_id="alpha", oauth=oauth, grants=grants, clock=clock)

    def test_cache_and_margin(self):
        _, oauth, grants, _, clock = connected()
        src = self.source(oauth, grants, clock)
        first = src.access_token()
        assert src.access_token() == first and oauth.refresh_calls == 1
        clock.advance(timedelta(seconds=3600 - 61))
        assert src.access_token() == first
        clock.advance(timedelta(seconds=2))  # inside the 60 s margin
        assert src.access_token() != first and oauth.refresh_calls == 2

    def test_concurrent_callers_share_one_refresh(self):
        _, oauth, grants, _, clock = connected()
        oauth.delay = 0.05
        src = self.source(oauth, grants, clock)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(src.access_token())) for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert oauth.refresh_calls == 1 and len(set(results)) == 1 and len(results) == 8

    def test_invalid_grant_marks_reauth_required(self):
        service, oauth, grants, _, clock = connected()
        oauth.refresh_error = GoogleReauthRequiredError("x")
        src = self.source(oauth, grants, clock)
        with pytest.raises(GoogleReauthRequiredError):
            src.access_token()
        assert service.status("alpha").state == REAUTH_REQUIRED
        oauth.refresh_error = None
        calls = oauth.refresh_calls
        with pytest.raises(GoogleReauthRequiredError):
            src.access_token()
        assert oauth.refresh_calls == calls  # no request while reauth is pending

    def test_a_transient_failure_keeps_the_grant(self):
        service, oauth, grants, _, clock = connected()
        oauth.refresh_error = GoogleTransientError("x")
        with pytest.raises(GoogleTransientError):
            self.source(oauth, grants, clock).access_token()
        assert service.status("alpha").state == CONNECTED

    def test_not_connected_needs_reauth(self):
        _, oauth, grants, _, clock = build()
        with pytest.raises(GoogleReauthRequiredError):
            self.source(oauth, grants, clock).access_token()

    def test_a_reconnection_drops_the_cache(self):
        _, oauth, grants, _, clock = connected()
        src = self.source(oauth, grants, clock)
        src.access_token()
        grants.save("alpha", "1//other-refresh", ADDRESS)
        src.access_token()
        assert oauth.refresh_calls == 2


class FakeSmtp:
    def __init__(self, log):
        self.log = log

    def auth(self, mechanism, authobject, **kw):
        self.log.append(("auth", mechanism, authobject()))

    def login(self, *a):
        raise AssertionError("password login")

    def send_message(self, message):
        self.log.append(("send",))

    def quit(self):
        pass


class FakeImap:
    def __init__(self, log):
        self.log = log

    def authenticate(self, mechanism, authobject):
        self.log.append((mechanism, authobject(b"")))

    def login(self, *a):
        raise AssertionError("password login")

    def select(self, mailbox="INBOX", readonly=False):
        return "OK", [b"0"]

    def uid(self, command, *args):
        return "OK", [b""]

    def logout(self):
        pass


def outbound():
    return OutboundMessage(
        client_id="alpha",
        prospect_id="p1",
        channel=Channel.EMAIL,
        recipient="lead@example.org",
        draft=MessageDraft(text="Hello"),
        idempotency_key="k1",
    )


class _OpenGuard:
    def reserve(self):
        return None

    def release(self):
        pass


OPEN_GUARD = _OpenGuard()


class Tokens:
    def __init__(self, *values, error=None):
        self.values = list(values)
        self.error = error

    def access_token(self):
        if self.error:
            raise self.error
        return self.values.pop(0)


class TestWrappers:
    def test_each_send_uses_a_fresh_token(self):
        log, hosts = [], []

        def factory(settings):
            hosts.append((settings.host, settings.port, settings.security))
            return FakeSmtp(log)

        channel = OAuthMailChannel(
            Tokens("ya29.one", "ya29.two"), ADDRESS, clock=FakeClock(NOW), smtp_factory=factory,
            send_guard=OPEN_GUARD,
        )
        assert channel.send(outbound()).status is SendStatus.ACCEPTED
        assert channel.send(outbound()).status is SendStatus.ACCEPTED
        auths = [e[2] for e in log if e[0] == "auth"]
        assert auths == [
            f"user={ADDRESS}\x01auth=Bearer ya29.one\x01\x01",
            f"user={ADDRESS}\x01auth=Bearer ya29.two\x01\x01",
        ]
        assert hosts == [("smtp.gmail.com", 587, "starttls")] * 2

    def test_send_reports_reauth_and_transient_without_connecting(self):
        def boom(settings):
            raise AssertionError("connected")

        for error, code in (
            (GoogleReauthRequiredError("x"), "reauth_required"),
            (GoogleTransientError("x"), "token_unavailable"),
        ):
            result = OAuthMailChannel(
                Tokens(error=error), ADDRESS, clock=FakeClock(NOW), smtp_factory=boom,
                send_guard=OPEN_GUARD,
            ).send(outbound())
            assert result.status is SendStatus.FAILED_TRANSIENT and result.error_code == code

    def test_each_poll_uses_a_fresh_token(self):
        log, hosts = [], []

        def factory(settings):
            hosts.append((settings.host, settings.port, settings.security))
            return FakeImap(log)

        inbox = OAuthMailInbox(
            Tokens("ya29.one", "ya29.two"), ADDRESS, clock=FakeClock(NOW), imap_factory=factory
        )
        for _ in range(2):
            with inbox:
                assert inbox.fetch() == []
        assert [e[1] for e in log] == [
            f"user={ADDRESS}\x01auth=Bearer ya29.one\x01\x01".encode(),
            f"user={ADDRESS}\x01auth=Bearer ya29.two\x01\x01".encode(),
        ]
        assert {e[0] for e in log} == {"XOAUTH2"}
        assert hosts == [("imap.gmail.com", 993, "ssl")] * 2

    def test_poll_errors_map_to_the_mail_errors(self):
        with pytest.raises(MailAuthError):
            OAuthMailInbox(Tokens(error=GoogleReauthRequiredError("x")), ADDRESS).fetch()
        with pytest.raises(MailUnavailableError):
            OAuthMailInbox(Tokens(error=GoogleTransientError("x")), ADDRESS).fetch()

    def test_the_cursor_key_needs_no_token(self):
        assert (
            OAuthMailInbox(Tokens(), ADDRESS).cursor_key
            == OAuthMailInbox(Tokens(), ADDRESS).cursor_key
        )


class TestProof:
    def test_stable_across_access_tokens_and_bound_to_the_grant(self):
        service, oauth, grants, _, clock = connected()
        src = GoogleTokenSource(client_id="alpha", oauth=oauth, grants=grants, clock=clock)
        before = service.proof("alpha")
        src.access_token()
        clock.advance(timedelta(hours=2))
        src.access_token()
        assert service.proof("alpha") == before == mailbox_login_proof(KEY, ADDRESS, REFRESH)
        assert mailbox_login_proof(KEY, "other@gmail.com", REFRESH) != before
        assert mailbox_login_proof(KEY, ADDRESS, "1//new") != before
        assert mailbox_login_proof(b"j" * 32, ADDRESS, REFRESH) != before
        assert REFRESH not in before and REFRESH not in refresh_token_fingerprint(REFRESH)


class TestNoLeak:
    def test_no_secret_in_reprs_errors_or_logs(self, caplog):
        caplog.set_level(logging.DEBUG)
        service, oauth, grants, pending, clock = build()
        state = state_of(service.start("alpha"))
        service.callback(state=state, client_id="alpha", code=CODE)
        src = GoogleTokenSource(client_id="alpha", oauth=oauth, grants=grants, clock=clock)
        token = src.access_token()
        secrets_ = (REFRESH, token, CODE, "ya29.first")
        things = [
            service,
            src,
            grants,
            pending,
            grants.load("alpha"),
            service.status("alpha"),
            OAuthMailChannel(src, ADDRESS),
            OAuthMailInbox(src, ADDRESS),
        ]
        with pytest.raises(InvalidGoogleStateError) as caught:
            service.callback(state=state, client_id="alpha", code=CODE)
        things.append(caught.value)
        for thing in things:
            for secret in secrets_:
                assert secret not in repr(thing) and secret not in str(thing)
        for record in caplog.records:
            for secret in secrets_:
                assert secret not in record.getMessage()
