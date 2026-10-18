"""Gmail OAuth, Mode 1: scopes actually granted, disconnect, refresh, per-client isolation and the
mandatory send cap. Fakes only: no network, no real Google, no real send."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_google_oauth_flow import (
    ADDRESS,
    CODE,
    FakeSmtp,
    Tokens,
    build,
    outbound,
    state_of,
)

from sales_app.google_mailbox import (
    CAP_NOT_CONFIGURED,
    CAP_REACHED,
    CONNECTED,
    NOT_CONNECTED,
    REAUTH_REQUIRED,
    DailySendCap,
    GoogleTokenSource,
    OAuthMailChannel,
    scopes_cover,
)
from sales_core.contracts import SendStatus
from sales_integrations.fakes import FakeClock
from sales_integrations.google_oauth import (
    AccessGrant,
    GoogleInsufficientScopeError,
    GoogleProtocolError,
    GoogleTransientError,
    TokenGrant,
)

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
MAIL = "https://mail.google.com/"


def connect_with(scope, **kw):
    service, oauth, grants, _pending, clock = build(**kw)
    oauth.exchange_code = lambda *, code, code_verifier: TokenGrant("ya29.x", "1//R", 3600, scope)
    return service, oauth, grants, clock


class TestScopes:
    def test_cover(self):
        assert scopes_cover(f"{MAIL} openid email", (MAIL,))
        assert not scopes_cover("openid email", (MAIL,))
        assert not scopes_cover("", (MAIL,))

    @pytest.mark.parametrize(
        "scope", ["openid email", "", "https://www.googleapis.com/auth/gmail.readonly"]
    )
    def test_missing_mail_scope_keeps_nothing_and_revokes(self, scope):
        service, oauth, grants, _ = connect_with(scope)
        with pytest.raises(GoogleInsufficientScopeError):
            service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        assert grants.load("a") is None and service.status("a").state == NOT_CONNECTED
        assert oauth.revoked == ["1//R"]

    def test_refresh_that_narrows_scope_requires_reauth(self):
        service, oauth, grants, clock = connect_with(f"{MAIL} openid")
        service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        oauth.refresh = lambda t: AccessGrant("ya29.n", 3600, "openid email")
        src = GoogleTokenSource(client_id="a", oauth=oauth, grants=grants, clock=clock)
        from sales_integrations.google_oauth import GoogleReauthRequiredError

        with pytest.raises(GoogleReauthRequiredError):
            src.access_token()
        assert service.status("a").state == REAUTH_REQUIRED


class TestConnection:
    def test_failed_address_lookup_revokes_and_keeps_nothing(self):
        service, oauth, grants, _ = connect_with(f"{MAIL} openid")

        def boom(token):
            raise GoogleProtocolError("x")

        oauth.account_address = boom
        with pytest.raises(GoogleProtocolError):
            service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        assert grants.load("a") is None and oauth.revoked == ["1//R"]

    def test_reconnect_revokes_the_previous_grant(self):
        service, oauth, grants, _ = connect_with(f"{MAIL} openid")
        service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        oauth.exchange_code = lambda *, code, code_verifier: TokenGrant("y", "1//R2", 3600, MAIL)
        service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        assert grants.load("a").refresh_token == "1//R2" and oauth.revoked == ["1//R"]

    def test_disconnect_deletes_and_revokes_even_if_google_is_down(self):
        service, oauth, grants, _ = connect_with(f"{MAIL} openid")
        service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        oauth.revoke_error = GoogleTransientError("x")
        assert service.disconnect("a") is False
        assert grants.load("a") is None and service.status("a").state == NOT_CONNECTED
        assert service.proof("a") is None

    def test_clients_are_isolated(self):
        service, oauth, grants, clock = connect_with(f"{MAIL} openid")
        service.callback(state=state_of(service.start("a")), client_id="a", code=CODE)
        assert service.status("b").state == NOT_CONNECTED
        # a state made for a is refused for b
        from sales_app.google_mailbox import InvalidGoogleStateError

        with pytest.raises(InvalidGoogleStateError):
            service.callback(state=state_of(service.start("a")), client_id="b", code=CODE)
        # revoking b's (absent) grant leaves a's intact
        service.disconnect("b")
        assert service.status("a").state == CONNECTED
        src_b = GoogleTokenSource(client_id="b", oauth=oauth, grants=grants, clock=clock)
        from sales_integrations.google_oauth import GoogleReauthRequiredError

        with pytest.raises(GoogleReauthRequiredError):
            src_b.access_token()


class TestSendCap:
    def channel(self, limit, log=None, tokens=None, counter=None):
        clock = FakeClock(NOW)
        cap = DailySendCap(lambda: limit, clock, counter)
        sent = log if log is not None else []
        return (
            OAuthMailChannel(
                tokens or Tokens("t1", "t2", "t3"),
                ADDRESS,
                clock=clock,
                smtp_factory=lambda s: FakeSmtp(sent),
                send_guard=cap,
            ),
            clock,
            sent,
        )

    def test_no_guard_no_send(self):
        def boom(s):
            raise AssertionError("connected")

        r = OAuthMailChannel(Tokens("t"), ADDRESS, clock=FakeClock(NOW), smtp_factory=boom).send(
            outbound()
        )
        assert r.status is SendStatus.FAILED_TRANSIENT and r.error_code == CAP_NOT_CONFIGURED

    @pytest.mark.parametrize("limit", [None, 0, -1, True])
    def test_unset_cap_refuses_before_any_token_or_connection(self, limit):
        channel, _, log = self.channel(limit, tokens=Tokens(error=AssertionError("token asked")))
        r = channel.send(outbound())
        assert r.error_code == CAP_NOT_CONFIGURED and log == []

    def test_cap_is_enforced_per_day(self):
        channel, clock, _ = self.channel(2)
        assert channel.send(outbound()).ok and channel.send(outbound()).ok
        assert channel.send(outbound()).error_code == CAP_REACHED
        clock.advance(timedelta(days=1))
        assert channel.send(outbound()).ok

    def test_failed_send_gives_the_slot_back(self):
        from sales_integrations.google_oauth import GoogleReauthRequiredError

        tokens = Tokens(error=GoogleReauthRequiredError("x"))
        channel, _, _ = self.channel(1, tokens=tokens)
        assert channel.send(outbound()).error_code == "reauth_required"
        tokens.error = None
        tokens.values = ["t"]
        assert channel.send(outbound()).ok
