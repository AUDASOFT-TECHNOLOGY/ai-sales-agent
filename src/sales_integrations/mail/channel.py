"""Sending through SMTP.

SMTP has no idempotency key of its own, so the Engine's `SENDING` guard is what prevents a
duplicate. To keep that guard meaningful this adapter distinguishes, as the WhatsApp one does:

- the message certainly did not leave (name not resolved, connection refused or timed out before
  the message was handed over, login refused, a 4xx reply): a *transient* failure;
- the server refused this message or recipient (5xx on the sender, the recipient or the data): a
  *permanent* failure;
- the outcome is unknown (the connection dropped after the message was handed over): an exception
  is raised, so the Engine leaves the action in `SENDING` for a human.

The `Message-ID` is made from the idempotency key, so the same send is the same message to anyone
who sees it twice.
"""

import hashlib
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from typing import Any, Protocol

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.ports import Clock
from sales_integrations.mail.errors import MailHostRefusedError, MailUnavailableError
from sales_integrations.mail.net import SMTP_PORTS, ensure_public_host

DEFAULT_TIMEOUT_SECONDS = 20.0
_FORBIDDEN_IN_ADDRESS = set("\r\n\t ,;<>\"'()[]\\")


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    security: str  # "starttls" or "ssl": a mailbox is never reached in the clear
    username: str
    password: str = field(repr=False)
    from_address: str
    from_name: str | None = None
    subject: str = "Following up on your enquiry"
    timeout: float = DEFAULT_TIMEOUT_SECONDS


class SmtpConnection(Protocol):
    def login(self, user: str, password: str) -> Any: ...

    def send_message(self, message: EmailMessage) -> Any: ...

    def quit(self) -> Any: ...


SmtpFactory = Callable[[SmtpSettings], SmtpConnection]


def open_smtp(settings: SmtpSettings) -> SmtpConnection:
    """A connection to a public mail server, encrypted. Raises the adapter's errors only."""
    ensure_public_host(settings.host, settings.port, allowed_ports=SMTP_PORTS)
    context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            return smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=settings.timeout, context=context
            )
        connection = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
        connection.starttls(context=context)
        return connection
    except (OSError, smtplib.SMTPException):
        raise MailUnavailableError("the mail server could not be reached") from None


class MailChannel:
    """Implements `sales_core.ports.MessagingChannel` for e-mail."""

    def __init__(
        self,
        settings: SmtpSettings,
        *,
        factory: SmtpFactory = open_smtp,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._factory = factory
        self._clock = clock

    def __repr__(self) -> str:
        return "MailChannel()"

    @property
    def channel(self) -> Channel:
        return Channel.EMAIL

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            built, message_id = self._build(message)
        except ValueError:
            return self._failure(SendStatus.FAILED_PERMANENT, "invalid_message", "invalid message")
        try:
            connection = self._factory(self._settings)
        except MailHostRefusedError:
            return self._failure(SendStatus.FAILED_TRANSIENT, "host_refused", "host refused")
        except MailUnavailableError:
            return self._failure(SendStatus.FAILED_TRANSIENT, "connect_error", "unreachable")
        try:
            connection.login(self._settings.username, self._settings.password)
        except smtplib.SMTPAuthenticationError:
            _quietly_quit(connection)
            return self._failure(SendStatus.FAILED_TRANSIENT, "auth", "login refused")
        except (OSError, smtplib.SMTPException):
            _quietly_quit(connection)
            return self._failure(SendStatus.FAILED_TRANSIENT, "connect_error", "unreachable")

        try:
            connection.send_message(built)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
        ) as e:
            _quietly_quit(connection)
            return self._refused(e)
        # Any other error (the connection dropped, a timeout) propagates: the outcome is unknown.
        _quietly_quit(connection)
        return SendResult(status=SendStatus.ACCEPTED, timestamp=self._now(), external_id=message_id)

    # ---- request ---------------------------------------------------------------------------

    def _build(self, message: OutboundMessage) -> tuple[EmailMessage, str]:
        recipient = message.recipient.strip()
        local, _, domain_part = recipient.partition("@")
        if (
            recipient.count("@") != 1
            or not local
            or not domain_part
            or any(ch in _FORBIDDEN_IN_ADDRESS for ch in recipient)
        ):
            raise ValueError("recipient")
        text = message.draft.text.strip()
        if not text:
            raise ValueError("text")
        domain = self._settings.from_address.rpartition("@")[2]
        digest = hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:32]
        message_id = f"<{digest}@{domain}>"
        built = EmailMessage(policy=SMTP_POLICY)
        built["From"] = (
            f"{self._settings.from_name} <{self._settings.from_address}>"
            if self._settings.from_name
            else self._settings.from_address
        )
        built["To"] = recipient
        built["Subject"] = self._settings.subject
        built["Message-ID"] = message_id
        built["Date"] = self._now()
        built.set_content(text)
        return built, message_id

    # ---- answers ---------------------------------------------------------------------------

    def _refused(self, error: smtplib.SMTPException) -> SendResult:
        code = getattr(error, "smtp_code", None)
        if isinstance(code, int) and 400 <= code < 500:
            return self._failure(SendStatus.FAILED_TRANSIENT, str(code), "temporarily refused")
        return self._failure(SendStatus.FAILED_PERMANENT, str(code or "refused"), "refused")

    def _failure(self, status: SendStatus, code: str, message: str) -> SendResult:
        return SendResult(
            status=status, timestamp=self._now(), error_code=code, error_message=message
        )

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)


def _quietly_quit(connection: SmtpConnection) -> None:
    try:
        connection.quit()
    except (OSError, smtplib.SMTPException):
        pass
