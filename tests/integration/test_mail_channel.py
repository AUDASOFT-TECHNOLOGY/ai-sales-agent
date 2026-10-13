"""The SMTP side of the e-mail adapter, against a double of the server.

What is proved: what a send says about itself (certainly not sent, refused for this message, or
unknown), that the same idempotency key is the same `Message-ID`, that a header cannot be injected,
and that nothing secret reaches a result, a `repr` or an error. What is not: that a real server
accepts the login or relays the message.
"""

import smtplib

import pytest
from factories import NOW

from sales_core.contracts import (
    Channel,
    MessageDraft,
    OutboundMessage,
    SendStatus,
)
from sales_integrations.fakes import FakeClock
from sales_integrations.mail import (
    MailChannel,
    MailHostRefusedError,
    MailUnavailableError,
    SmtpSettings,
)
from sales_integrations.mail.net import IMAP_PORTS, SMTP_PORTS, ensure_public_host

PASSWORD = "S3cret-smtp-pass!"
SETTINGS = SmtpSettings(
    host="smtp.example.org",
    port=587,
    security="starttls",
    username="sales@example.org",
    password=PASSWORD,
    from_address="sales@example.org",
    from_name="Naga Sales",
    subject="About your enquiry",
)


class FakeSmtp:
    def __init__(self, *, login_error=None, send_error=None):
        self.login_error = login_error
        self.send_error = send_error
        self.logins: list[tuple[str, str]] = []
        self.sent = []
        self.quit_called = False

    def login(self, user, password):
        self.logins.append((user, password))
        if self.login_error:
            raise self.login_error

    def send_message(self, message):
        if self.send_error:
            raise self.send_error
        self.sent.append(message)

    def quit(self):
        self.quit_called = True


def message(recipient="alex@example.com", text="Hello Alex", key="key-1"):
    return OutboundMessage(
        client_id="client-a",
        prospect_id="p1",
        channel=Channel.EMAIL,
        recipient=recipient,
        draft=MessageDraft(text=text),
        idempotency_key=key,
    )


def channel_with(server=None, *, factory_error=None):
    server = server or FakeSmtp()

    def factory(settings):
        if factory_error:
            raise factory_error
        return server

    return MailChannel(SETTINGS, factory=factory, clock=FakeClock(NOW)), server


def test_it_is_the_email_channel():
    assert channel_with()[0].channel is Channel.EMAIL


def test_a_message_is_sent_with_what_it_should_say():
    channel, server = channel_with()
    result = channel.send(message())
    assert result.status is SendStatus.ACCEPTED and result.external_id
    (sent,) = server.sent
    assert sent["To"] == "alex@example.com"
    assert sent["From"] == "Naga Sales <sales@example.org>"
    assert sent["Subject"] == "About your enquiry"
    assert sent.get_content().strip() == "Hello Alex"
    assert sent["Message-ID"] == result.external_id
    assert server.logins == [("sales@example.org", PASSWORD)] and server.quit_called


def test_the_same_idempotency_key_is_the_same_message_id_and_another_key_is_not():
    channel, _ = channel_with()
    first = channel.send(message(key="same")).external_id
    again = channel.send(message(key="same")).external_id
    other = channel.send(message(key="different")).external_id
    assert first == again != other
    assert first.endswith("@example.org>") and "same" not in first


@pytest.mark.parametrize(
    "recipient",
    [
        "alex@example.com\r\nBcc: spy@example.net",
        "alex@example.com,boss@example.com",
        "alex example@example.com",
        "@example.com",
        "alex@",
        "no-at-sign",
        "a@b@c.com",
        "<alex@example.com>",
    ],
)
def test_a_recipient_that_could_inject_a_header_or_is_not_an_address_is_never_sent(recipient):
    channel, server = channel_with()
    result = channel.send(message(recipient=recipient))
    assert result.status is SendStatus.FAILED_PERMANENT and server.sent == []
    assert recipient not in (result.error_message or "")


def test_a_message_with_no_text_is_refused():
    channel, server = channel_with()
    # a template-only draft reaches the channel with an empty text: there is nothing to send
    result = channel.send(
        OutboundMessage(
            client_id="client-a",
            prospect_id="p1",
            channel=Channel.EMAIL,
            recipient="alex@example.com",
            draft=MessageDraft(text="  ", template_id="t1"),
            idempotency_key="k",
        )
    )
    assert result.status is SendStatus.FAILED_PERMANENT and server.sent == []


class TestWhatCertainlyDidNotLeave:
    def test_a_refused_login_is_transient_and_the_operator_s_problem(self):
        server = FakeSmtp(
            login_error=smtplib.SMTPAuthenticationError(535, b"bad login " + PASSWORD.encode())
        )
        result = channel_with(server)[0].send(message())
        assert result.status is SendStatus.FAILED_TRANSIENT and result.error_code == "auth"
        assert PASSWORD not in (result.error_message or "") and server.sent == []
        assert server.quit_called

    def test_a_server_that_cannot_be_reached_is_transient(self):
        result = channel_with(factory_error=MailUnavailableError("down"))[0].send(message())
        assert result.status is SendStatus.FAILED_TRANSIENT and result.error_code == "connect_error"

    def test_a_host_that_may_not_be_used_is_transient_and_says_so(self):
        result = channel_with(factory_error=MailHostRefusedError("private"))[0].send(message())
        assert result.status is SendStatus.FAILED_TRANSIENT and result.error_code == "host_refused"

    def test_a_connection_lost_while_logging_in_is_transient(self):
        server = FakeSmtp(login_error=smtplib.SMTPServerDisconnected("gone"))
        result = channel_with(server)[0].send(message())
        assert result.status is SendStatus.FAILED_TRANSIENT and server.sent == []

    def test_a_4xx_answer_is_transient(self):
        server = FakeSmtp(send_error=smtplib.SMTPDataError(451, b"try later"))
        result = channel_with(server)[0].send(message())
        assert result.status is SendStatus.FAILED_TRANSIENT and result.error_code == "451"


class TestWhatTheServerRefused:
    def test_a_refused_recipient_is_permanent_and_never_repeats_the_address(self):
        server = FakeSmtp(
            send_error=smtplib.SMTPRecipientsRefused({"alex@example.com": (550, b"no such user")})
        )
        result = channel_with(server)[0].send(message())
        assert result.status is SendStatus.FAILED_PERMANENT
        assert "alex@example.com" not in (result.error_message or "")

    def test_a_refused_sender_is_permanent(self):
        server = FakeSmtp(send_error=smtplib.SMTPSenderRefused(553, b"bad sender", "x@y.z"))
        result = channel_with(server)[0].send(message())
        assert result.status is SendStatus.FAILED_PERMANENT and result.error_code == "553"

    def test_a_5xx_on_the_data_is_permanent(self):
        server = FakeSmtp(send_error=smtplib.SMTPDataError(554, b"rejected"))
        assert channel_with(server)[0].send(message()).status is SendStatus.FAILED_PERMANENT


def test_a_connection_that_drops_after_the_message_was_handed_over_is_unknown_and_raises():
    # The Engine keeps such an action in SENDING for a human: it must never be guessed.
    server = FakeSmtp(send_error=smtplib.SMTPServerDisconnected("connection lost"))
    with pytest.raises(smtplib.SMTPServerDisconnected):
        channel_with(server)[0].send(message())


def test_a_timeout_while_sending_is_unknown_and_raises():
    server = FakeSmtp(send_error=TimeoutError("timed out"))
    with pytest.raises(TimeoutError):
        channel_with(server)[0].send(message())


def test_the_password_is_in_no_repr():
    channel, _ = channel_with()
    assert PASSWORD not in repr(channel) and PASSWORD not in repr(SETTINGS)


# ---- where a client may point the server -------------------------------------------------------


class TestWhereAClientMayPointTheServer:
    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "172.16.0.1"]
    )
    def test_private_loopback_and_metadata_addresses_are_refused(self, host):
        with pytest.raises(MailHostRefusedError):
            ensure_public_host(host, 587, allowed_ports=SMTP_PORTS)

    def test_a_public_address_is_accepted(self):
        ensure_public_host("8.8.8.8", 587, allowed_ports=SMTP_PORTS)  # a literal: no lookup

    @pytest.mark.parametrize("port", [22, 80, 3306, 6379, 8080])
    def test_a_port_mail_does_not_use_is_refused(self, port):
        with pytest.raises(MailHostRefusedError):
            ensure_public_host("8.8.8.8", port, allowed_ports=SMTP_PORTS)

    def test_imap_has_its_own_ports(self):
        ensure_public_host("8.8.8.8", 993, allowed_ports=IMAP_PORTS)
        with pytest.raises(MailHostRefusedError):
            ensure_public_host("8.8.8.8", 587, allowed_ports=IMAP_PORTS)

    def test_a_name_that_does_not_resolve_is_unavailable_not_refused(self):
        with pytest.raises(MailUnavailableError):
            ensure_public_host("no-such-host.invalid", 587, allowed_ports=SMTP_PORTS)
