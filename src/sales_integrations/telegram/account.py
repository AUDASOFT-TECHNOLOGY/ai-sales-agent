"""Telegram from the client's own account: what the application asks of an MTProto client.

A *user account* (not a bot) can open a conversation with someone it can resolve, which a bot never
can. It is also a person's account: it is limited by Telegram when it writes to people who did not
expect it, and everything in it that is not a prospect is private. This module is the port the rest
of the system uses (`AccountClient`) with its plain-value types and failures, then the two things
built on it: the channel that sends (`TelegramAccountChannel`) and the inbox that reads replies
(`TelegramAccountInbox`). The real MTProto client is `telegram.telethon_client`, loaded only when it
is used; tests use a fake of this port.

Rules kept here, whatever the client behind the port does:

- **Only prospects are read.** Messages are listed per private conversation, and a conversation is
  opened only for a sender the caller accepts (a known prospect). Nothing else of the account is
  fetched, stored or logged.
- **Nothing is read back into the past.** The first poll only records where the account stands;
  messages written before the connection are never ingested.
- **Never lost, never twice.** A position moves only past messages the Engine stored; the Engine
  de-duplicates by `<account id>:<message id>`.
- **A failure is a fixed sentence.** No phone number, code, password, session or text is ever in an
  error, a log or a result.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from sales_core.contracts import (
    Channel,
    InboundMessage,
    OutboundMessage,
    SendResult,
    SendStatus,
)
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.ports import Clock
from sales_integrations.telegram.api import chunks

log = logging.getLogger(__name__)

# ---- what can go wrong, as kinds (never a Telegram sentence) --------------------------------------

UNREACHABLE = "unreachable"  # Telegram could not be reached: nothing left, try later
AUTH_LOST = "auth_lost"  # the session no longer works (ended from Telegram, or the account is gone)
FLOOD_WAIT = "flood_wait"  # Telegram says: wait `retry_after` seconds
PEER_FLOOD = "peer_flood"  # Telegram limits this account's messages to people it does not know
BLOCKED = "blocked"  # this person cannot be written to (privacy, blocked us, deactivated)
NOT_FOUND = "not_found"  # no such person
BAD_PHONE = "bad_phone"
BAD_CODE = "bad_code"
CODE_EXPIRED = "code_expired"
BAD_PASSWORD = "bad_password"
LOGIN_FLOOD = "login_flood"  # too many login attempts for this number: Telegram says wait
REJECTED = "rejected"  # Telegram refused the platform's application or the request itself


class AccountFailure(Exception):
    """A failure of the account, by `kind`. Carries no number, code, password, session or text."""

    def __init__(self, kind: str, *, retry_after: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True, repr=False)
class LoginTicket:
    """A login under way: the code Telegram sent, and the temporary session it belongs to. Held in
    memory only, for minutes."""

    phone_code_hash: str
    session: str  # temporary; becomes the account's session once signed in

    def __repr__(self) -> str:
        return "LoginTicket()"


class PasswordNeeded(AccountFailure):
    """The code was right and the account has a two-step password: continue with this ticket."""

    def __init__(self, ticket: LoginTicket) -> None:
        super().__init__("password_needed")
        self.ticket = ticket


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str  # the account's numeric id: not a secret, and what tells two accounts apart
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class SignedIn:
    session: str  # a full access key to the account: stored encrypted, never shown
    identity: Identity

    def __repr__(self) -> str:
        return f"SignedIn(user_id={self.identity.user_id!r})"


@dataclass(frozen=True, slots=True)
class Sent:
    message_id: int


@dataclass(frozen=True, slots=True)
class Incoming:
    sender_id: str
    message_id: int
    text: str
    at: datetime
    name: str = ""
    phone: str | None = None  # the number Telegram reports for the sender, when it shows it
    attachment: str | None = None  # the kind of a file that came with it; never downloaded


@dataclass(frozen=True, slots=True)
class Batch:
    """What a read found: the messages (oldest first), the highest message id the read looked at,
    and whether more are waiting (then the position must stop at the last message returned)."""

    messages: Sequence[Incoming]
    scanned_to: int
    truncated: bool = False


# Is this person one of the client's prospects? `(sender id, the number Telegram reports | None)`.
Accept = Callable[[str, str | None], bool]


class AccountClient(Protocol):
    """One session of one account, used for one operation at a time. Methods are blocking."""

    def request_code(self, phone: str) -> LoginTicket: ...

    def sign_in_code(self, ticket: LoginTicket, phone: str, code: str) -> SignedIn:
        """Raises `PasswordNeeded` when the account has a two-step password."""
        ...

    def sign_in_password(self, ticket: LoginTicket, password: str) -> SignedIn: ...

    def me(self) -> Identity: ...

    def send_text(self, peer: str, text: str) -> Sent: ...

    def resolve_phone(self, phone: str, name: str) -> str | None:
        """The account id of whoever holds `phone` on Telegram, or None."""
        ...

    def newest_message_id(self) -> int:
        """The highest message id in the account's private conversations (0: none)."""
        ...

    def fetch_incoming(self, after_id: int, limit: int, accept: Accept) -> Batch: ...

    def log_out(self) -> None:
        """Ends this session at Telegram."""
        ...


# ---- sending --------------------------------------------------------------------------------------

_LIMITED = "telegram_account_limited"


class TelegramAccountChannel:
    """Implements `sales_core.ports.MessagingChannel` through the client's own Telegram account.

    The recipient is the person's account id, which the Engine holds as `telegram_chat_id`: the
    account is linked to a prospect when the person wrote first or, from the phone number the client
    holds for them, when Telegram says who holds it (`Engine.link_telegram_by_phone`). Failures follow the other
    channels: certainly not sent (transient), refused for this person (permanent), or unknown (an
    exception: the Engine leaves the action in `SENDING` for a human).

    `limited` says whether Telegram has already limited this account for unsolicited messages: then
    no first message goes out at all (a reply to someone who wrote does). `on_limited` and
    `on_auth_lost` tell the application so it can show it; they never receive any detail.
    """

    def __init__(
        self,
        client: AccountClient,
        *,
        clock: Clock | None = None,
        limited: Callable[[], bool] = lambda: False,
        on_limited: Callable[[], None] = lambda: None,
        on_auth_lost: Callable[[], None] = lambda: None,
    ) -> None:
        self._client = client
        self._clock = clock
        self._limited = limited
        self._on_limited = on_limited
        self._on_auth_lost = on_auth_lost

    def __repr__(self) -> str:
        return "TelegramAccountChannel()"

    @property
    def channel(self) -> Channel:
        return Channel.TELEGRAM

    def send(self, message: OutboundMessage) -> SendResult:
        peer = message.recipient.strip()
        if not peer.isdigit():
            return self._failure(
                SendStatus.FAILED_PERMANENT, "invalid_message", "the recipient is not an account"
            )
        text = message.draft.text
        if not text.strip():
            return self._failure(
                SendStatus.FAILED_PERMANENT, "invalid_message", "the message has no text"
            )
        if message.first_contact and self._limited():
            return self._deferred(
                _LIMITED, "Telegram limited this account: no first message is sent"
            )
        first_id: str | None = None
        for position, piece in enumerate(chunks(text)):
            try:
                sent = self._client.send_text(peer, piece)
            except AccountFailure as failure:
                return self._partial_or(self._failed(failure), position)
            # Any other error (a timeout after the request left) propagates: the outcome is unknown.
            if position == 0:
                first_id = f"{peer}:{sent.message_id}"
        return SendResult(status=SendStatus.ACCEPTED, timestamp=self._now(), external_id=first_id)

    def _failed(self, failure: AccountFailure) -> SendResult:
        kind = failure.kind
        if kind == AUTH_LOST:
            self._on_auth_lost()
            return self._failure(
                SendStatus.FAILED_TRANSIENT, kind, "the Telegram connection must be renewed"
            )
        if kind == PEER_FLOOD:
            self._on_limited()
            return self._deferred(kind, "Telegram limited this account: first messages are stopped")
        if kind == FLOOD_WAIT:
            return self._deferred(kind, f"Telegram asks to wait {failure.retry_after or 0}s")
        if kind in (BLOCKED, NOT_FOUND, BAD_PHONE):
            return self._failure(
                SendStatus.FAILED_PERMANENT, kind, "this person cannot be written to on Telegram"
            )
        return self._failure(SendStatus.FAILED_TRANSIENT, kind, "Telegram could not be reached")

    @staticmethod
    def _partial_or(result: SendResult, sent_pieces: int) -> SendResult:
        if sent_pieces == 0:
            return result
        raise RuntimeError(
            f"a long message was delivered in part ({sent_pieces} piece(s)), then: "
            f"{result.error_message}"
        )

    def _deferred(self, code: str, text: str) -> SendResult:
        return SendResult(
            status=SendStatus.FAILED_TRANSIENT,
            timestamp=self._now(),
            error_code=code,
            error_message=text,
            metadata={DEFERRED_KEY: DEFERRED_VALUE},
        )

    def _failure(self, status: SendStatus, code: str, message: str) -> SendResult:
        return SendResult(
            status=status, timestamp=self._now(), error_code=code, error_message=message
        )

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)


# ---- reading replies ------------------------------------------------------------------------------


class PositionStore(Protocol):
    """Where the inbox keeps its place (`sales_app.inbox.PositionStore`, plain values)."""

    def load(self, mailbox: str) -> tuple[int, int, datetime] | None: ...

    def save(self, mailbox: str, uidvalidity: int, next_uid: int, updated_at: datetime) -> None: ...


_VALIDITY = 1  # message ids of one account never restart: the position has one generation
READ_BATCH = 50


class InboxNotReadyError(RuntimeError):
    """The inbox cannot read safely (no place to keep its position)."""


@dataclass(frozen=True, slots=True)
class FetchedAccountMessage:
    handle: str
    message: InboundMessage


@dataclass(slots=True)
class _Position:
    next_id: int  # the first message id not yet dealt with
    dirty: bool = False


@dataclass(slots=True)
class TelegramAccountInbox:
    """Replies to the client's prospects, read from its own Telegram account.

    `filters_by_sender`: the Engine's answer to "is this sender a prospect?" is required, so nothing
    of the rest of the account is read. `needs_cursor`: the scheduler gives the position store
    before the first read. `account_id` names the account the position belongs to.
    """

    client: AccountClient
    account_id: str
    clock: Clock | None = None
    link_limit: int = 0  # prospects whose account is looked up per poll; 0: none
    on_auth_lost: Callable[[], None] = lambda: None
    needs_cursor: bool = True
    filters_by_sender: bool = True
    _cursors: PositionStore | None = field(default=None, repr=False)
    _position: _Position | None = field(default=None, repr=False)
    _pending: dict[int, bool] = field(
        default_factory=dict, repr=False
    )  # fetched id -> acknowledged
    _scanned_to: int = field(default=0, repr=False)
    _truncated: bool = field(default=False, repr=False)

    def __repr__(self) -> str:
        return "TelegramAccountInbox()"

    @property
    def cursor_key(self) -> str:
        return "tg:" + sha256(self.account_id.encode("utf-8")).hexdigest()[:24]

    def use_cursor(self, store: PositionStore | None) -> None:
        self._cursors = store

    def link_by_phone(self, engine) -> int:
        """Ask Telegram for the account of each prospect that has a phone number, so a first message can
        be sent to them. A few per poll (see `Engine.link_telegram_by_phone`)."""
        if self.link_limit < 1:
            return 0
        try:
            return engine.link_telegram_by_phone(
                lambda phone, name: self.client.resolve_phone(phone, name), limit=self.link_limit
            )
        except AccountFailure as failure:
            raise self._unreadable(failure) from None

    def _unreadable(self, failure: AccountFailure) -> InboxNotReadyError:
        if failure.kind == AUTH_LOST:
            self.on_auth_lost()
        return InboxNotReadyError(f"Telegram could not be read ({failure.kind})")

    def fetch(
        self, limit: int = READ_BATCH, accept: Callable[..., bool] | None = None
    ) -> list[FetchedAccountMessage]:
        if accept is None:
            raise ValueError("an account is read only for the people the caller accepts")
        if self._cursors is None:
            raise InboxNotReadyError("the Telegram inbox has nowhere to keep its position")
        held = self._cursors.load(self.cursor_key)
        if held is None:
            # First read of this account: start from now. What was written before the client
            # connected is not ours to read.
            try:
                newest = self.client.newest_message_id()
            except AccountFailure as failure:
                raise self._unreadable(failure) from None
            self._position = _Position(next_id=newest + 1, dirty=True)
            self._flush()
            return []
        self._position = _Position(next_id=held[1])

        def known(sender: str, phone: str | None) -> bool:
            return accept(Channel.TELEGRAM, sender, phone)

        try:
            batch = self.client.fetch_incoming(self._position.next_id - 1, max(1, limit), known)
        except AccountFailure as failure:
            raise self._unreadable(failure) from None
        self._scanned_to = batch.scanned_to
        self._truncated = batch.truncated
        found = []
        for item in batch.messages:
            inbound = _inbound(item)
            if inbound is None:
                continue
            self._pending[item.message_id] = False
            found.append(FetchedAccountMessage(handle=str(item.message_id), message=inbound))
        if not found:
            self._advance(batch.scanned_to + 1)
        return found

    def acknowledge(self, handle: str) -> None:
        try:
            message_id = int(handle)
        except ValueError:
            return
        if message_id in self._pending:
            self._pending[message_id] = True

    def close(self) -> None:
        """Move the position past what was stored; never past a message that was not."""
        if self._position is not None and self._pending:
            unacked = [i for i, done in self._pending.items() if not done]
            if unacked:
                self._advance(min(unacked))  # offered again next time; the Engine de-duplicates
            elif self._truncated:
                self._advance(max(self._pending) + 1)
            else:
                self._advance(max(max(self._pending) + 1, self._scanned_to + 1))
            self._pending.clear()
        self._flush()

    def _advance(self, next_id: int) -> None:
        position = self._position
        if position is not None and next_id > position.next_id:
            position.next_id = next_id
            position.dirty = True

    def _flush(self) -> None:
        position = self._position
        if position is None or not position.dirty or self._cursors is None:
            return
        now = self.clock.now() if self.clock is not None else datetime.now(UTC)
        self._cursors.save(self.cursor_key, _VALIDITY, position.next_id, now)
        position.dirty = False


def _inbound(item: Incoming) -> InboundMessage | None:
    if not item.text.strip() and not item.attachment:
        return None
    metadata = {}
    if item.name.strip():
        metadata["sender_name"] = item.name.strip()
    if item.attachment:
        metadata["attachment"] = item.attachment
    return InboundMessage(
        channel=Channel.TELEGRAM,
        sender=item.sender_id,
        external_id=f"{item.sender_id}:{item.message_id}",
        text=item.text,
        timestamp=item.at if item.at.tzinfo else item.at.replace(tzinfo=UTC),
        metadata=metadata,
        shared_phone=item.phone,
    )
