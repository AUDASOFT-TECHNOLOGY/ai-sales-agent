"""Reading a mailbox over IMAP: the unread messages, one acknowledged at a time.

A message is fetched without marking it read (`BODY.PEEK`) and is marked read only when the caller
says it has been stored (`acknowledge`). A crash in between leaves it unread, so it comes back on
the next poll: the Engine de-duplicates by `Message-ID`, so being offered twice is harmless and
being lost is impossible.

What is never turned into a prospect's reply: a message the mailbox itself sent, an automatic
answer (out-of-office, delivery failure, mailing list) and anything with no readable text. Those
are marked read and dropped, so they are not offered again and cannot make two mailboxes answer
each other.
"""

import hashlib
import html
import imaplib
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes
from email.message import Message
from email.policy import default as DEFAULT_POLICY
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Protocol, Self

from sales_core.contracts import Channel, InboundMessage
from sales_core.ports import Clock
from sales_integrations.mail.errors import MailAuthError, MailUnavailableError
from sales_integrations.mail.net import IMAP_PORTS, ensure_public_host

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TEXT_LENGTH = 5000
MAX_ID_LENGTH = 200

_AUTOMATIC_SENDERS = ("mailer-daemon", "postmaster", "no-reply", "noreply", "do-not-reply")
_QUOTE_INTRO = re.compile(r"^(on .{5,200} wrote:|le .{5,200} a écrit\s*:)\s*$", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_STYLE_OR_SCRIPT = re.compile(r"<(style|script)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_LINE_BREAKS = re.compile(r"<\s*(br|/p|/div|/tr)\s*/?>", re.IGNORECASE)
_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ImapSettings:
    host: str
    port: int
    security: str  # "ssl" or "starttls"
    username: str
    password: str = field(repr=False)
    mailbox: str = "INBOX"
    own_addresses: tuple[str, ...] = ()  # what this mailbox sends as: never a prospect
    timeout: float = DEFAULT_TIMEOUT_SECONDS


class ImapConnection(Protocol):
    def login(self, user: str, password: str) -> Any: ...

    def select(self, mailbox: str = ..., readonly: bool = ...) -> Any: ...

    def uid(self, command: str, *args: Any) -> Any: ...

    def logout(self) -> Any: ...


ImapFactory = Callable[[ImapSettings], ImapConnection]


@dataclass(frozen=True, slots=True, repr=False)
class FetchedMail:
    """A message to store, and the handle to acknowledge once it is stored. Personal data: its
    `repr` is empty."""

    handle: str
    message: InboundMessage

    def __repr__(self) -> str:
        return "FetchedMail()"


def open_imap(settings: ImapSettings) -> ImapConnection:
    """A connection to a public mail server, encrypted. Raises the adapter's errors only."""
    ensure_public_host(settings.host, settings.port, allowed_ports=IMAP_PORTS)
    context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            return imaplib.IMAP4_SSL(
                settings.host, settings.port, ssl_context=context, timeout=settings.timeout
            )
        connection = imaplib.IMAP4(settings.host, settings.port, timeout=settings.timeout)
        connection.starttls(ssl_context=context)
        return connection
    except (OSError, imaplib.IMAP4.error):
        raise MailUnavailableError("the mail server could not be reached") from None


class ImapMailbox:
    """Implements `sales_app.inbox.InboxPoller`'s shape for one mailbox. One poll, one connection:
    use it as a context manager, or call `close()`."""

    def __init__(
        self,
        settings: ImapSettings,
        *,
        factory: ImapFactory = open_imap,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._factory = factory
        self._clock = clock
        self._connection: ImapConnection | None = None

    def __repr__(self) -> str:
        return "ImapMailbox()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- the poll --------------------------------------------------------------------------

    def fetch(self, limit: int = 50) -> list[FetchedMail]:
        """The oldest unread messages that are replies from a person, at most `limit`."""
        connection = self._open()
        try:
            status, found = connection.uid("SEARCH", None, "UNSEEN")
            if status != "OK":
                raise MailUnavailableError("the mailbox could not be searched")
            uids = [u.decode() if isinstance(u, bytes) else str(u) for u in found[0].split()]
            fetched: list[FetchedMail] = []
            for uid in uids[:limit]:
                status, data = connection.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = _raw_message(status, data)
                if raw is None:
                    continue  # it vanished between the search and the fetch
                message = parse_mail(
                    raw, own_addresses=self._settings.own_addresses, now=self._now()
                )
                if message is None:
                    self.acknowledge(uid)  # not a reply: read, and never offered again
                else:
                    fetched.append(FetchedMail(uid, message))
            return fetched
        except (OSError, imaplib.IMAP4.error):
            self.close()
            raise MailUnavailableError("the mailbox could not be read") from None

    def acknowledge(self, handle: str) -> None:
        """Mark one message read: it has been stored."""
        connection = self._open()
        try:
            connection.uid("STORE", handle, "+FLAGS", "(\\Seen)")
        except (OSError, imaplib.IMAP4.error):
            self.close()
            raise MailUnavailableError("the mailbox could not be updated") from None

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.logout()
        except (OSError, imaplib.IMAP4.error):
            pass

    # ---- internals -------------------------------------------------------------------------

    def _open(self) -> ImapConnection:
        if self._connection is not None:
            return self._connection
        connection = self._factory(self._settings)
        try:
            connection.login(self._settings.username, self._settings.password)
            status, _ = connection.select(self._settings.mailbox)
        except imaplib.IMAP4.error:
            _quietly_logout(connection)
            raise MailAuthError("the mailbox refused the login") from None
        except OSError:
            _quietly_logout(connection)
            raise MailUnavailableError("the mail server could not be reached") from None
        if status != "OK":
            _quietly_logout(connection)
            raise MailUnavailableError("the mailbox folder could not be opened")
        self._connection = connection
        return connection

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)


def _raw_message(status: str, data: Any) -> bytes | None:
    if status != "OK" or not data:
        return None
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
            return part[1]
    return None


def _quietly_logout(connection: ImapConnection) -> None:
    try:
        connection.logout()
    except (OSError, imaplib.IMAP4.error):
        pass


# ---- reading one message ------------------------------------------------------------------------


def parse_mail(
    raw: bytes, *, own_addresses: tuple[str, ...] = (), now: datetime | None = None
) -> InboundMessage | None:
    """The reply a raw message stands for, or None if it is not one (see the module docstring)."""
    now = now or datetime.now(UTC)
    try:
        message = message_from_bytes(raw, policy=DEFAULT_POLICY)
        sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
    except (ValueError, TypeError, LookupError):
        return None
    local, _, domain = sender.partition("@")
    if not local or not domain or sender.count("@") != 1:
        return None
    if sender in {a.strip().lower() for a in own_addresses}:
        return None
    if _is_automatic(message, local):
        return None
    text = _text_of(message)
    if not text:
        return None
    return InboundMessage(
        channel=Channel.EMAIL,
        sender=sender,
        external_id=_external_id(message, raw),
        text=text,
        timestamp=_sent_at(message, now),
    )


def _is_automatic(message: Message, local_part: str) -> bool:
    auto = str(message.get("Auto-Submitted", "no")).strip().lower()
    precedence = str(message.get("Precedence", "")).strip().lower()
    return (
        auto != "no"
        or precedence in {"bulk", "list", "junk"}
        or "X-Failed-Recipients" in message
        or "List-Id" in message
        or local_part.startswith(_AUTOMATIC_SENDERS)
    )


def _external_id(message: Message, raw: bytes) -> str:
    identifier = str(message.get("Message-ID", "")).strip().strip("<>").strip()
    if identifier and len(identifier) <= MAX_ID_LENGTH and identifier.isprintable():
        return identifier
    return "raw-" + hashlib.sha256(raw).hexdigest()[:32]  # stable: the same bytes, the same id


def _sent_at(message: Message, now: datetime) -> datetime:
    try:
        sent = parsedate_to_datetime(str(message.get("Date", "")))
    except (TypeError, ValueError, IndexError):
        return now
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=UTC)
    return min(sent, now)  # a clock that is ahead must not put a reply in the future


def _text_of(message: Message) -> str:
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    try:
        content = body.get_content()
    except (LookupError, ValueError):
        return ""
    if body.get_content_type() == "text/html":
        content = html.unescape(
            _TAGS.sub("", _LINE_BREAKS.sub("\n", _STYLE_OR_SCRIPT.sub("", content)))
        )
    return _without_quotes(content)[:MAX_TEXT_LENGTH].strip()


def _without_quotes(text: str) -> str:
    """What the person wrote, not the conversation they replied under."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _QUOTE_INTRO.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line.rstrip())
    return _BLANK_RUNS.sub("\n\n", "\n".join(kept)).strip()
