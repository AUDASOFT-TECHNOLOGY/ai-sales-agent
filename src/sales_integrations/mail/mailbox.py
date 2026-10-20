"""Reading a mailbox over IMAP: the unread messages, one acknowledged at a time.

A message is fetched without marking it read (`BODY.PEEK`) and is marked read only when the caller
says it has been stored (`acknowledge`). A crash in between leaves it unread, so it comes back on
the next poll: the Engine de-duplicates by `Message-ID`, so being offered twice is harmless and
being lost is impossible.

What is never turned into a prospect's reply: a message the mailbox itself sent, an automatic
answer (out-of-office, delivery failure, mailing list) and anything with no readable text. Those
are marked read and dropped, so they are not offered again and cannot make two mailboxes answer
each other.

**Two ways to read.** `consume` (the default, and everything above) is for a mailbox that exists for
Revansa alone: unread means new, and what is stored is marked read. `observe` is for a mailbox
that belongs to a person and holds their own mail: nothing is ever marked read, nothing is written
(the folder is opened read-only and no `STORE` is ever sent), and "new" is a saved position (a UID
and the UIDVALIDITY it belongs to, `sales_integrations.mail.cursor`). The first time it is read, the
position starts at the end of the mailbox, so no history is touched, **unless the caller gives the
moment the mailbox was connected** (`ImapSettings.read_from`): then only the messages that arrived
since that moment are looked at (a reply that came between the connection and the first poll is not
lost), still only from senders the caller accepts, so older mail stays untouched. Only the headers that say who wrote
are read; the body of a message is read only when its sender is one the caller accepts (a prospect),
so the text of anyone else's mail never leaves the server.
"""

import hashlib
import html
import imaplib
import logging
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import Message
from email.policy import default as DEFAULT_POLICY
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Protocol, Self

from sales_core.contracts import Channel, InboundMessage
from sales_core.ports import Clock
from sales_integrations.mail.auth import (
    PASSWORD,
    XOAUTH2,
    ImapXOAuth2,
    check_credentials,
    xoauth2_string,
)
from sales_integrations.mail.cursor import MailCursor, MailCursorStore, mailbox_digest
from sales_integrations.mail.errors import (
    MailAuthError,
    MailCursorUnavailableError,
    MailUnavailableError,
)
from sales_integrations.mail.net import IMAP_PORTS, ensure_public_host

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TEXT_LENGTH = 5000
MAX_ID_LENGTH = 200

CONSUME = "consume"
OBSERVE = "observe"

# What `observe` reads of a message before it knows whether it is for Revansa: who wrote it, and the
# headers that say it is automatic. Never the subject, never the body.
_SCREEN_FIELDS = "FROM SENDER AUTO-SUBMITTED PRECEDENCE LIST-ID X-FAILED-RECIPIENTS"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

log = logging.getLogger(__name__)

# Whether a sender is someone the caller wants to hear from: (channel, address) -> yes/no.
Accept = Callable[[Channel, str], bool]

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
    read_mode: str = CONSUME  # "consume" (the historical way) or "observe" (see the module doc)
    auth: str = PASSWORD  # "password" (the historical way) or "xoauth2" (see `mail.auth`)
    access_token: str = field(default="", repr=False)  # given by the layer above, for "xoauth2"
    # `observe` only: when the mailbox was connected. The first read then starts from this moment
    # instead of the end of the mailbox (None: the end, as before).
    read_from: datetime | None = None

    def __post_init__(self) -> None:
        check_credentials(self.auth, self.password, self.access_token)
        if self.auth == XOAUTH2:
            xoauth2_string(self.username, self.access_token)  # refuse a value that cannot be sent


class ImapConnection(Protocol):
    def login(self, user: str, password: str) -> Any: ...

    def authenticate(self, mechanism: str, authobject: Callable[[bytes], bytes]) -> Any: ...

    def select(self, mailbox: str = ..., readonly: bool = ...) -> Any: ...

    def uid(self, command: str, *args: Any) -> Any: ...

    def response(self, code: str) -> Any: ...

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
        # Only `observe` uses the rest.
        self._cursors: MailCursorStore | None = None
        self._uidvalidity: int | None = None
        self._uidnext: int | None = None
        self._position: MailCursor | None = None  # where this reader is, in memory
        self._dirty = False  # the position in memory is ahead of the one kept
        self._batch: list[int] = []  # the UIDs of this poll, oldest first, not all dealt with yet
        self._done: set[int] = set()
        self._next_uid = 1
        self.revalidated = False  # the server renumbered the folder since the position was kept

    @property
    def needs_cursor(self) -> bool:
        """Whether a position store must be given (`use_cursor`) before this can read."""
        return self._settings.read_mode == OBSERVE

    @property
    def filters_by_sender(self) -> bool:
        """Whether `fetch` must be told whom to accept (`accept=`): see the module doc."""
        return self._settings.read_mode == OBSERVE

    def use_cursor(self, store: MailCursorStore | None) -> None:
        self._cursors = store

    @property
    def cursor_key(self) -> str:
        s = self._settings
        return mailbox_digest(s.host, s.username, s.mailbox)

    def __repr__(self) -> str:
        return "ImapMailbox()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- the poll --------------------------------------------------------------------------

    def fetch(self, limit: int = 50, accept: Accept | None = None) -> list[FetchedMail]:
        """The oldest unread messages that are replies from a person, at most `limit`. In `observe`
        mode: the messages that arrived since the saved position, from senders `accept` wants."""
        if self._settings.read_mode == OBSERVE:
            return self._fetch_observe(limit, accept)
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
        """Mark one message read: it has been stored. In `observe` mode nothing is marked: the
        saved position moves instead."""
        if self._settings.read_mode == OBSERVE:
            self._acknowledge_observe(handle)
            return
        connection = self._open()
        try:
            connection.uid("STORE", handle, "+FLAGS", "(\\Seen)")
        except (OSError, imaplib.IMAP4.error):
            self.close()
            raise MailUnavailableError("the mailbox could not be updated") from None

    def close(self) -> None:
        self._flush_cursor()  # a no-op unless a position could not be kept earlier
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
            if self._settings.auth == XOAUTH2:
                connection.authenticate(
                    "XOAUTH2", ImapXOAuth2(self._settings.username, self._settings.access_token)
                )
            else:
                connection.login(self._settings.username, self._settings.password)
            if self._settings.read_mode == OBSERVE:
                status, _ = connection.select(self._settings.mailbox, readonly=True)
            else:
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
        if self._settings.read_mode == OBSERVE:
            self._learn_numbering(connection)
        self._connection = connection
        return connection

    # ---- reading without consuming (`observe`) ---------------------------------------------

    def _fetch_observe(self, limit: int, accept: Accept | None) -> list[FetchedMail]:
        connection = self._open()
        if limit <= 0:
            return []  # a connection check: log in, open the folder, touch nothing else
        if accept is None:
            raise ValueError("a mailbox read without consuming needs to know whom to accept")
        if self._cursors is None:
            raise MailCursorUnavailableError("the mailbox's reading position cannot be kept")
        try:
            now = self._now()
            saved = self._load_position()
            assert self._uidvalidity is not None

            def server_next_uid() -> int:
                if self._uidnext is not None:
                    return self._uidnext
                return self._past_the_last(connection)

            not_before: datetime | None = None
            criteria: tuple[str, ...]
            if saved is None and self._settings.read_from is None:
                # First time: everything already here is history, and none of it is ours.
                first = MailCursor(self._uidvalidity, server_next_uid(), now)
                self._save_position(first)
                self._position = first
                return []
            if saved is None:
                # First time, for a mailbox connected at a known moment: what arrived since then
                # counts (a reply may come before the first poll); what was there before does not.
                assert self._settings.read_from is not None
                not_before = self._settings.read_from
                criteria = ("SINCE", _imap_date(not_before - timedelta(days=1)))
                floor, empty_next = 1, server_next_uid()
                self._position = None
            elif saved.uidvalidity != self._uidvalidity:
                # The server renumbered the folder: no UID kept means anything now. Start from the
                # date of the last progress instead (a day earlier, as a date has no hour); the
                # Engine de-duplicates what it had already stored.
                self.revalidated = True
                log.warning("a mailbox's UIDVALIDITY changed: reading again from the last date")
                criteria = (
                    "SINCE",
                    _imap_date(saved.updated_at - timedelta(days=1)),
                )
                floor, empty_next = 1, server_next_uid()
                self._position = None
            else:
                criteria = ("UID", f"{saved.next_uid}:*")
                floor, empty_next = saved.next_uid, None
                self._position = saved
            status, found = connection.uid("SEARCH", None, *criteria)
            if status != "OK":
                raise MailUnavailableError("the mailbox could not be searched")
            # `n:*` always includes the last message, even when its UID is before n: drop those.
            uids = sorted({int(u) for u in (found[0] or b"").split() if int(u) >= floor})
            self._batch, self._done = uids[:limit], set()
            self._next_uid = floor
            if not uids and empty_next is not None:
                self._position = MailCursor(self._uidvalidity, empty_next, now)
                self._dirty = True
            fetched: list[FetchedMail] = []
            for uid in self._batch:
                if not_before is not None:
                    received = self._received_at(connection, uid)
                    if received is None or received < not_before:
                        self._done.add(uid)  # here before the connection: history, not ours
                        continue
                sender = self._screen(connection, uid)
                if sender is None or not accept(Channel.EMAIL, sender):
                    self._done.add(uid)  # not a person's reply, or not a prospect: nothing to keep
                    continue
                message = self._read_whole(connection, uid, now)
                if message is None:
                    self._done.add(uid)
                else:
                    fetched.append(FetchedMail(str(uid), message))
            self._advance()
            self._flush_cursor()
            return fetched
        except (OSError, imaplib.IMAP4.error):
            self._advance()  # what was dealt with before the failure stays dealt with
            self.close()
            raise MailUnavailableError("the mailbox could not be read") from None

    def _acknowledge_observe(self, handle: str) -> None:
        self._done.add(int(handle))
        self._advance()
        self._flush_cursor()

    def _advance(self) -> None:
        """Move the position past every message at the front of the batch that is dealt with. One
        that is not (still waiting, or failed) holds it back, whatever follows it."""
        moved = False
        while self._batch and self._batch[0] in self._done:
            uid = self._batch.pop(0)
            self._done.discard(uid)
            self._next_uid = uid + 1
            moved = True
        if moved and self._uidvalidity is not None:
            self._position = MailCursor(self._uidvalidity, self._next_uid, self._now())
            self._dirty = True

    def _flush_cursor(self) -> None:
        """Keep the position if it moved. A position that cannot be kept is not an error here: the
        messages are stored, and being offered again is harmless (the Engine de-duplicates); it is
        tried again at the next step and when the reader is closed."""
        if not self._dirty or self._cursors is None or self._position is None:
            return
        try:
            self._save_position(self._position)
        except MailCursorUnavailableError:
            log.warning("a mailbox's reading position could not be kept; it will be read again")
            return
        self._dirty = False

    def _load_position(self) -> MailCursor | None:
        assert self._cursors is not None
        try:
            held = self._cursors.load(self.cursor_key)
        except Exception as error:  # noqa: BLE001 - any failure of the store is the same to a reader
            log.warning("a mailbox's reading position could not be read (%s)", type(error).__name__)
            raise MailCursorUnavailableError(
                "the mailbox's reading position is unavailable"
            ) from None
        return None if held is None else MailCursor(*held)

    def _save_position(self, cursor: MailCursor) -> None:
        assert self._cursors is not None
        try:
            self._cursors.save(
                self.cursor_key, cursor.uidvalidity, cursor.next_uid, cursor.updated_at
            )
        except Exception as error:  # noqa: BLE001 - see `_load_position`
            log.warning("a mailbox's reading position could not be kept (%s)", type(error).__name__)
            raise MailCursorUnavailableError(
                "the mailbox's reading position is unavailable"
            ) from None

    def _learn_numbering(self, connection: ImapConnection) -> None:
        """What the server said when the folder was opened: the UIDVALIDITY its UIDs belong to, and
        the UID the next message will get."""
        self._uidvalidity = _untagged_number(connection, "UIDVALIDITY")
        self._uidnext = _untagged_number(connection, "UIDNEXT")
        if self._uidvalidity is None:
            _quietly_logout(connection)
            raise MailUnavailableError("the mailbox did not say which UIDs it uses")

    def _past_the_last(self, connection: ImapConnection) -> int:
        """The UID after the last message, for a server that did not say (it is only a start)."""
        status, found = connection.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise MailUnavailableError("the mailbox could not be searched")
        uids = [int(u) for u in (found[0] or b"").split()]
        return max(uids) + 1 if uids else 1

    def _received_at(self, connection: ImapConnection, uid: int) -> datetime | None:
        """When the server received message `uid` (its INTERNALDATE, nothing else is read)."""
        status, data = connection.uid("FETCH", str(uid), "(INTERNALDATE)")
        if status != "OK" or not data:
            return None
        for part in data:
            blob = part[0] if isinstance(part, tuple) else part
            if isinstance(blob, bytes):
                found = _INTERNALDATE.search(blob.decode("ascii", "replace"))
                if found:
                    return _parse_internaldate(found)
        return None

    def _screen(self, connection: ImapConnection, uid: int) -> str | None:
        """Who wrote message `uid`, read from its headers alone; None if it is not a person's
        message (the mailbox's own, automatic, unreadable) or has vanished."""
        status, data = connection.uid(
            "FETCH", str(uid), f"(BODY.PEEK[HEADER.FIELDS ({_SCREEN_FIELDS})])"
        )
        raw = _raw_message(status, data)
        if raw is None:
            return None
        return screen_sender(raw, own_addresses=self._settings.own_addresses)

    def _read_whole(
        self, connection: ImapConnection, uid: int, now: datetime
    ) -> InboundMessage | None:
        status, data = connection.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = _raw_message(status, data)
        if raw is None:
            return None
        return parse_mail(raw, own_addresses=self._settings.own_addresses, now=now)

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)


def _raw_message(status: str, data: Any) -> bytes | None:
    if status != "OK" or not data:
        return None
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
            return part[1]
    return None


def _untagged_number(connection: ImapConnection, code: str) -> int | None:
    """A number the server announced when the folder was opened (`[UIDVALIDITY n]`), if it did."""
    try:
        _, data = connection.response(code)
    except (OSError, imaplib.IMAP4.error):
        return None
    for item in reversed(data or []):
        if item is None:
            continue
        found = re.match(r"\s*(\d+)", item.decode() if isinstance(item, bytes) else str(item))
        if found:
            return int(found.group(1))
    return None


_INTERNALDATE = re.compile(
    r'INTERNALDATE "\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})"'
)


def _parse_internaldate(found: "re.Match[str]") -> datetime | None:
    day, month, year, hour, minute, second, sign, off_h, off_m = found.groups()
    if month.title() not in _MONTHS:
        return None
    try:
        local = datetime(
            int(year), _MONTHS.index(month.title()) + 1, int(day), int(hour), int(minute), int(second)
        )
    except ValueError:
        return None
    offset = timedelta(hours=int(off_h), minutes=int(off_m))
    return (local - offset if sign == "+" else local + offset).replace(tzinfo=UTC)


def _imap_date(moment: datetime) -> str:
    """A date as `SEARCH SINCE` wants it (day-month-year, English month, whatever the locale)."""
    moment = moment.astimezone(UTC)
    return f"{moment.day:02d}-{_MONTHS[moment.month - 1]}-{moment.year}"


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
        sender = _address_of(message)
    except (ValueError, TypeError, LookupError):
        return None
    if sender is None:
        return None
    if sender in {a.strip().lower() for a in own_addresses}:
        return None
    if _is_automatic(message, sender.partition("@")[0]):
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


def _address_of(message: Message) -> str | None:
    """The address in `From`, in lowercase, if it looks like one."""
    sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
    local, _, domain = sender.partition("@")
    if not local or not domain or sender.count("@") != 1:
        return None
    return sender


def screen_sender(raw_headers: bytes, *, own_addresses: tuple[str, ...] = ()) -> str | None:
    """Who wrote a message, from its headers alone, if it is a person's message: not the mailbox's
    own, not automatic (out-of-office, delivery failure, list), not unreadable. Reads no body."""
    try:
        message = message_from_bytes(raw_headers, policy=DEFAULT_POLICY)
        sender = _address_of(message)
        if sender is None or sender in {a.strip().lower() for a in own_addresses}:
            return None
        if _is_automatic(message, sender.partition("@")[0]):
            return None
    except (ValueError, TypeError, LookupError):
        return None
    return sender


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
