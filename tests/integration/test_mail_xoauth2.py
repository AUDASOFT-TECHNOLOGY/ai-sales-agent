"""XOAUTH2 authentication of the e-mail adapter, against doubles of the servers.

What is proved: the exact initial response and the commands built from it (IMAP `AUTHENTICATE`, SMTP
`AUTH`), the answer to a refusing server's error challenge, that a refusal is reported without what
the server said, that the access token reaches no `repr`, log, error or result, and that the
password way is untouched. What is not: that Gmail or Microsoft 365 accept any of it. That needs a
real, authorised mailbox and has not been done.
"""

import base64
import imaplib
import logging
import smtplib

import pytest
from factories import NOW

from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_integrations.fakes import FakeClock
from sales_integrations.mail import (
    XOAUTH2,
    ImapMailbox,
    ImapSettings,
    MailAuthError,
    MailChannel,
    MailUnavailableError,
    SmtpSettings,
    xoauth2_string,
)
from sales_integrations.mail.auth import ImapXOAuth2, SmtpXOAuth2

TOKEN = "ya29.A0-SECRET-access-token"
USER = "sales@example.org"
EXPECTED = f"user={USER}\x01auth=Bearer {TOKEN}\x01\x01"
SERVER_SAID = "AUTHENTICATE failed for sales@example.org token ya29.A0-SECRET-access-token"


def imap_settings(**changes):
    values = {
        "host": "imap.example.org",
        "port": 993,
        "security": "ssl",
        "username": USER,
        "password": "",
        "auth": XOAUTH2,
        "access_token": TOKEN,
    }
    return ImapSettings(**{**values, **changes})


def smtp_settings(**changes):
    values = {
        "host": "smtp.example.org",
        "port": 587,
        "security": "starttls",
        "username": USER,
        "password": "",
        "from_address": USER,
        "auth": XOAUTH2,
        "access_token": TOKEN,
    }
    return SmtpSettings(**{**values, **changes})


def assert_no_token(*things):
    for thing in things:
        assert TOKEN not in str(thing)
        assert TOKEN not in repr(thing)


# ---- the string ---------------------------------------------------------------------------------


class TestTheInitialResponse:
    def test_it_is_user_then_bearer_token_between_control_a(self):
        assert xoauth2_string(USER, TOKEN) == EXPECTED

    @pytest.mark.parametrize("bad", ["a\x01b", "a\r\nb", "a\x00b", "é"])
    def test_a_value_that_could_forge_the_framing_is_refused(self, bad):
        with pytest.raises(ValueError) as caught:
            xoauth2_string(USER, bad)
        with pytest.raises(ValueError):
            xoauth2_string(bad, TOKEN)
        assert bad not in str(caught.value)

    def test_an_empty_user_or_token_is_refused(self):
        with pytest.raises(ValueError):
            xoauth2_string("", TOKEN)
        with pytest.raises(ValueError):
            xoauth2_string(USER, "")


class TestTheSettings:
    def test_xoauth2_needs_a_token_and_no_password(self):
        with pytest.raises(ValueError):
            imap_settings(access_token="")
        with pytest.raises(ValueError):
            imap_settings(password="also-a-password")
        with pytest.raises(ValueError):
            smtp_settings(access_token="")
        with pytest.raises(ValueError):
            smtp_settings(password="also-a-password")

    def test_a_token_with_the_password_way_is_refused(self):
        with pytest.raises(ValueError):
            imap_settings(auth="password", password="pw")
        with pytest.raises(ValueError):
            smtp_settings(auth="password", password="pw")

    def test_an_unknown_method_is_refused(self):
        with pytest.raises(ValueError):
            imap_settings(auth="plain-ish", access_token="")

    def test_a_bad_token_is_refused_without_repeating_it(self):
        with pytest.raises(ValueError) as caught:
            imap_settings(access_token=TOKEN + "\n")
        assert TOKEN not in str(caught.value)

    def test_the_token_is_in_no_repr(self):
        assert_no_token(imap_settings(), smtp_settings(), ImapXOAuth2(USER, TOKEN))
        assert_no_token(SmtpXOAuth2(USER, TOKEN), ImapMailbox(imap_settings()))

    def test_the_historical_settings_are_unchanged(self):
        imap = ImapSettings("h", 993, "ssl", USER, "pw")
        smtp = SmtpSettings("h", 587, "starttls", USER, "pw", USER)
        assert imap.auth == smtp.auth == "password"
        assert imap.access_token == smtp.access_token == ""


# ---- IMAP ---------------------------------------------------------------------------------------


class FakeImap:
    def __init__(self, *, auth_error=None):
        self.auth_error = auth_error
        self.calls: list[tuple] = []
        self.logged_out = False

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def authenticate(self, mechanism, authobject):
        self.calls.append(("authenticate", mechanism, authobject))
        if self.auth_error:
            raise self.auth_error

    def select(self, mailbox="INBOX", readonly=False):
        return "OK", [b"0"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b""]
        raise AssertionError(command)

    def logout(self):
        self.logged_out = True


def imap_mailbox(server, **changes):
    return ImapMailbox(imap_settings(**changes), factory=lambda settings: server)


class TestImap:
    def test_it_authenticates_with_xoauth2_and_never_logs_in_with_a_password(self):
        server = FakeImap()
        assert imap_mailbox(server).fetch() == []
        assert [call[:2] for call in server.calls] == [("authenticate", "XOAUTH2")]

    def test_the_command_the_library_sends_carries_the_initial_response(self):
        server = FakeImap()
        imap_mailbox(server).fetch()
        authobject = server.calls[0][2]
        # What imaplib does with an authobject: ask it for the answer, then encode it in base64.
        authenticator = imaplib._Authenticator(authobject)
        assert base64.b64decode(authenticator.process(b"")) == EXPECTED.encode()

    def test_the_answer_to_an_error_challenge_is_an_empty_line(self):
        authobject = ImapXOAuth2(USER, TOKEN)
        authobject(b"")
        assert authobject(b'{"status":"401","schemes":"Bearer"}') == b""

    def test_a_refused_token_is_an_auth_error_that_says_nothing_of_the_server(self):
        server = FakeImap(auth_error=imaplib.IMAP4.error(SERVER_SAID))
        with pytest.raises(MailAuthError) as caught:
            imap_mailbox(server).fetch()
        assert str(caught.value) == "the mailbox refused the login"
        assert_no_token(caught.value)
        assert "sales@example.org" not in str(caught.value)
        assert server.logged_out

    def test_an_unreachable_server_during_authentication_is_unavailable(self):
        server = FakeImap(auth_error=OSError(SERVER_SAID))
        with pytest.raises(MailUnavailableError) as caught:
            imap_mailbox(server).fetch()
        assert_no_token(caught.value)

    def test_nothing_secret_is_logged(self, caplog):
        caplog.set_level(logging.DEBUG)
        server = FakeImap(auth_error=imaplib.IMAP4.error(SERVER_SAID))
        with pytest.raises(MailAuthError):
            imap_mailbox(server).fetch()
        assert TOKEN not in caplog.text

    def test_the_password_way_still_logs_in(self):
        server = FakeImap()
        mailbox = ImapMailbox(
            ImapSettings("imap.example.org", 993, "ssl", USER, "pw"), factory=lambda s: server
        )
        mailbox.fetch()
        assert server.calls == [("login", USER, "pw")]

    def test_observe_is_still_not_reachable_by_xoauth2_alone(self):
        # The read mode is a separate setting, "consume" unless given: XOAUTH2 does not change it.
        assert imap_settings().read_mode == "consume"


# ---- SMTP ---------------------------------------------------------------------------------------


class ScriptedSmtp(smtplib.SMTP):
    """The real `SMTP.auth`, over a server that answers from a script instead of a socket."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent: list[str] = []
        self.sent_messages = []
        self.quit_called = False
        self.esmtp_features = {"auth": "XOAUTH2"}

    def ehlo_or_helo_if_needed(self):
        pass

    def docmd(self, cmd, args=""):
        self.sent.append(f"{cmd} {args}".strip())
        return self.replies.pop(0)

    def send_message(self, message):
        self.sent_messages.append(message)

    def quit(self):
        self.quit_called = True


def outbound():
    return OutboundMessage(
        client_id="client-a",
        prospect_id="p1",
        channel=Channel.EMAIL,
        recipient="alex@example.com",
        draft=MessageDraft(text="Hello Alex"),
        idempotency_key="key-1",
    )


def smtp_channel(server, **changes):
    return MailChannel(smtp_settings(**changes), factory=lambda s: server, clock=FakeClock(NOW))


class TestSmtp:
    def test_the_command_sent_is_auth_xoauth2_with_the_initial_response(self):
        server = ScriptedSmtp([(235, b"2.7.0 Accepted")])
        result = smtp_channel(server).send(outbound())
        assert result.status is SendStatus.ACCEPTED
        encoded = base64.b64encode(EXPECTED.encode()).decode()
        assert server.sent == [f"AUTH XOAUTH2 {encoded}"]
        assert len(server.sent_messages) == 1

    def test_a_refusing_server_gets_an_empty_answer_to_its_challenge(self):
        challenge = base64.b64encode(b'{"status":"400","schemes":"Bearer"}')
        server = ScriptedSmtp([(334, challenge), (535, b"5.7.8 " + SERVER_SAID.encode())])
        result = smtp_channel(server).send(outbound())
        assert server.sent[1] == ""
        assert result.status is SendStatus.FAILED_TRANSIENT
        assert result.error_code == "auth"
        assert server.sent_messages == []  # nothing is sent after a refused login
        assert server.quit_called
        assert_no_token(result)
        assert SERVER_SAID not in repr(result)

    def test_the_error_raised_by_the_library_is_not_what_the_caller_sees(self):
        server = ScriptedSmtp([(535, SERVER_SAID.encode())])
        result = smtp_channel(server).send(outbound())
        assert result.error_message == "login refused"
        assert TOKEN not in (result.error_message or "")

    def test_nothing_secret_is_logged(self, caplog):
        caplog.set_level(logging.DEBUG)
        server = ScriptedSmtp([(535, SERVER_SAID.encode())])
        smtp_channel(server).send(outbound())
        assert TOKEN not in caplog.text

    def test_the_password_way_still_logs_in(self):
        class Plain:
            def __init__(self):
                self.logins = []
                self.sent = []

            def login(self, user, password):
                self.logins.append((user, password))

            def send_message(self, message):
                self.sent.append(message)

            def quit(self):
                pass

        server = Plain()
        settings = SmtpSettings("smtp.example.org", 587, "starttls", USER, "pw", USER)
        result = MailChannel(settings, factory=lambda s: server, clock=FakeClock(NOW)).send(
            outbound()
        )
        assert result.status is SendStatus.ACCEPTED
        assert server.logins == [(USER, "pw")]
