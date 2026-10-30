"""Notifications by role (spec 14): what an Admin or a Client needs to know and act on.

`notify(kind, audience, client_id, ...)` is the one call other parts of the application make. It is
**idempotent by dedupe key**: while a notification with the same key is still open (not resolved) the
call changes nothing and returns it; once it was resolved, the same event opens it again (unread). It
never raises: a notification that cannot be kept is logged and the caller carries on.

Texts are fixed sentences with a suggested action, never a secret, an address, a token or a message
body. A Client sees only its own `client` notifications (the session decides the client); an Admin
sees the `admin` ones of the clients they can reach. `NotificationStore` is the port, the SQL adapter
is in `persistence`.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sales_app.audit_log import redact

log = logging.getLogger(__name__)

ADMIN, CLIENT = "admin", "client"
AUDIENCES = (ADMIN, CLIENT)
INFO, WARNING, CRITICAL = "info", "warning", "critical"
SEVERITIES = (INFO, WARNING, CRITICAL)

# The minimum kinds of spec 14. Other parts call `notify` with these names.
CONNECTION_EXPIRED = "connection_expired"
AUTHORIZATION_REVOKED = "authorization_revoked"
SEND_FAILURE = "send_failure"
RECEIVE_FAILURE = "receive_failure"
SYNC_FAILURE = "sync_failure"
QUOTA_REACHED = "quota_reached"
CONFIG_PROBLEM = "config_problem"
EMAIL_MAILBOX_REQUEST = "email_mailbox_request"
BILLING_ERROR = "billing_error"
HUMAN_INTERVENTION = "human_intervention"
REPORT_FAILURE = "report_failure"
PLAN_REQUEST = "plan_request"


@dataclass(frozen=True, slots=True)
class KindInfo:
    severity: str
    title: str
    message: str
    action: str


KINDS: dict[str, KindInfo] = {
    CONNECTION_EXPIRED: KindInfo(
        WARNING,
        "A connection has expired",
        "An external connection needs to be authorised again.",
        "Reconnect the account.",
    ),
    AUTHORIZATION_REVOKED: KindInfo(
        WARNING,
        "An authorisation was revoked",
        "The access that was granted to Revansa was withdrawn.",
        "Authorise Revansa again.",
    ),
    SEND_FAILURE: KindInfo(
        WARNING,
        "A message could not be sent",
        "Sending failed on a channel.",
        "Check the channel status and try again.",
    ),
    RECEIVE_FAILURE: KindInfo(
        WARNING,
        "Incoming messages could not be read",
        "Receiving failed on a channel.",
        "Check the channel status.",
    ),
    SYNC_FAILURE: KindInfo(
        WARNING,
        "Synchronisation failed",
        "Prospect data could not be synchronised with the connected system.",
        "Check the connection to the system and retry.",
    ),
    QUOTA_REACHED: KindInfo(
        WARNING,
        "A quota was reached",
        "A usage limit of the plan was reached.",
        "Wait for the next period or change the plan.",
    ),
    CONFIG_PROBLEM: KindInfo(
        WARNING,
        "A configuration problem needs attention",
        "Something in the configuration prevents part of the agent from working.",
        "Open the settings and complete the missing step.",
    ),
    EMAIL_MAILBOX_REQUEST: KindInfo(
        INFO,
        "An email mailbox is waiting for activation",
        "A client asked for its email mailbox to be activated.",
        "Review the request in Email activation.",
    ),
    BILLING_ERROR: KindInfo(
        CRITICAL,
        "A billing error occurred",
        "An invoice or a payment could not be processed.",
        "Check the invoice and the payment provider.",
    ),
    HUMAN_INTERVENTION: KindInfo(
        WARNING,
        "A conversation needs a person",
        "The agent stopped on a conversation that needs a human decision.",
        "Open the conversation and answer or approve.",
    ),
    REPORT_FAILURE: KindInfo(
        WARNING,
        "The daily report could not be delivered",
        "The report was not sent.",
        "Check the report channel and recipient.",
    ),
    PLAN_REQUEST: KindInfo(
        INFO,
        "A client asked to subscribe to a plan",
        "A client on a free account chose a paid plan.",
        "Assign the plan to the client from its page.",
    ),
}


@dataclass(frozen=True, slots=True)
class Notification:
    id: int | None
    created_at: datetime
    audience: str
    client_id: str | None
    kind: str
    severity: str
    title: str
    message: str
    action: str
    dedupe_key: str
    read_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotificationQuery:
    """`client_ids`: for an Admin, the clients they may see (None: every one, global included)."""

    audience: str
    client_id: str | None = None
    client_ids: frozenset[str] | None = None
    unread_only: bool = False
    include_resolved: bool = True
    limit: int = 50
    offset: int = 0


@runtime_checkable
class NotificationStore(Protocol):
    def open_or_reopen(self, notification: Notification) -> Notification:
        """Insert it, or (same `dedupe_key`) leave an open one untouched / reopen a resolved one.
        Atomic: two concurrent calls never make two rows."""
        ...

    def search(self, query: NotificationQuery) -> tuple[list[Notification], int]:
        """(page, total), most recent first."""
        ...

    def unread_count(self, query: NotificationQuery) -> int: ...

    def mark_read(self, query: NotificationQuery, notification_id: int, at: datetime) -> bool:
        """Only a notification the query could see; False otherwise (not found, or not yours)."""
        ...

    def mark_all_read(self, query: NotificationQuery, at: datetime) -> int: ...

    def resolve(self, audience: str, client_id: str | None, dedupe_key: str, at: datetime) -> int:
        """`dedupe_key` is the caller's key (scoping is applied here)."""
        ...


def scoped_key(audience: str, client_id: str | None, dedupe_key: str) -> str:
    return f"{audience}|{client_id or '*'}|{dedupe_key}"[:255]


def _visible(n: Notification, q: NotificationQuery) -> bool:
    if n.audience != q.audience:
        return False
    if q.client_id is not None and n.client_id != q.client_id:
        return False
    if q.client_ids is not None and n.client_id not in q.client_ids:
        return False
    if q.unread_only and n.read_at is not None:
        return False
    return q.include_resolved or n.resolved_at is None


class InMemoryNotificationStore:
    def __init__(self) -> None:
        self._rows: dict[str, Notification] = {}
        self._lock = threading.Lock()
        self._next = 1

    def open_or_reopen(self, notification: Notification) -> Notification:
        with self._lock:
            current = self._rows.get(notification.dedupe_key)
            if current is None:
                created = replace(notification, id=self._next)
                self._next += 1
                self._rows[notification.dedupe_key] = created
                return created
            if current.resolved_at is None:
                return current
            reopened = replace(notification, id=current.id, read_at=None, resolved_at=None)
            self._rows[notification.dedupe_key] = reopened
            return reopened

    def search(self, query: NotificationQuery) -> tuple[list[Notification], int]:
        with self._lock:
            found = sorted(
                (n for n in self._rows.values() if _visible(n, query)),
                key=lambda n: (n.created_at, n.id or 0),
                reverse=True,
            )
        return found[query.offset : query.offset + query.limit], len(found)

    def unread_count(self, query: NotificationQuery) -> int:
        counted = replace(query, unread_only=True, include_resolved=False)
        with self._lock:
            return sum(1 for n in self._rows.values() if _visible(n, counted))

    def mark_read(self, query: NotificationQuery, notification_id: int, at: datetime) -> bool:
        with self._lock:
            for key, n in self._rows.items():
                if n.id == notification_id and _visible(n, query):
                    if n.read_at is None:
                        self._rows[key] = replace(n, read_at=at)
                    return True
        return False

    def mark_all_read(self, query: NotificationQuery, at: datetime) -> int:
        count = 0
        with self._lock:
            for key, n in list(self._rows.items()):
                if n.read_at is None and _visible(n, query):
                    self._rows[key] = replace(n, read_at=at)
                    count += 1
        return count

    def resolve(self, audience: str, client_id: str | None, dedupe_key: str, at: datetime) -> int:
        key = scoped_key(audience, client_id, dedupe_key)
        with self._lock:
            n = self._rows.get(key)
            if n is None or n.resolved_at is not None:
                return 0
            self._rows[key] = replace(n, resolved_at=at, read_at=n.read_at or at)
            return 1


class NotificationService:
    def __init__(
        self, store: NotificationStore, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return "NotificationService()"

    def notify(
        self,
        kind: str,
        audience: str,
        client_id: str | None = None,
        *,
        title: str | None = None,
        message: str | None = None,
        action: str | None = None,
        severity: str | None = None,
        dedupe_key: str | None = None,
    ) -> Notification | None:
        """Open a notification, once per `dedupe_key` while it is open. Never raises.

        Omitted texts come from the kind's catalog; `dedupe_key` defaults to the kind (so one open
        notification per kind, audience and client). A client notification needs a client."""
        try:
            if audience not in AUDIENCES or (audience == CLIENT and not client_id):
                log.error("notification %s refused: bad audience", kind)
                return None
            info = KINDS.get(kind) or KindInfo(INFO, str(kind), "", "")
            chosen = severity if severity in SEVERITIES else info.severity
            notification = Notification(
                id=None,
                created_at=self._now(),
                audience=audience,
                client_id=client_id,
                kind=str(kind)[:48],
                severity=chosen,
                title=_text(title or info.title, 160),
                message=_text(message if message is not None else info.message, 400),
                action=_text(action if action is not None else info.action, 300),
                dedupe_key=scoped_key(audience, client_id, dedupe_key or kind),
            )
            return self._store.open_or_reopen(notification)
        except Exception:  # noqa: BLE001 - a notification never breaks the operation behind it
            log.error("could not keep a notification (%s)", kind)
            return None

    def notify_both(self, kind: str, client_id: str, **texts: str) -> None:
        """The same event for the client and for the Admin (e.g. an authorisation to renew)."""
        self.notify(kind, CLIENT, client_id, **texts)
        self.notify(kind, ADMIN, client_id, **texts)

    def resolve(self, audience: str, client_id: str | None, dedupe_key: str) -> int:
        """Close the open notification with this (caller's) key, if any."""
        try:
            return self._store.resolve(audience, client_id, dedupe_key, self._now())
        except Exception:  # noqa: BLE001
            log.error("could not resolve a notification")
            return 0

    def resolve_both(self, client_id: str, dedupe_key: str) -> None:
        self.resolve(CLIENT, client_id, dedupe_key)
        self.resolve(ADMIN, client_id, dedupe_key)

    # ---- reading (the API decides the query from the session, never from the request) ---------

    def search(self, query: NotificationQuery) -> tuple[list[Notification], int]:
        return self._store.search(query)

    def unread_count(self, query: NotificationQuery) -> int:
        return self._store.unread_count(query)

    def mark_read(self, query: NotificationQuery, notification_id: int) -> bool:
        return self._store.mark_read(query, notification_id, self._now())

    def mark_all_read(self, query: NotificationQuery) -> int:
        return self._store.mark_all_read(query, self._now())


def tell(callback: Callable[[str], None] | None, client_id: str) -> None:
    """Call an optional hook with a client id; a hook that fails never fails the caller."""
    if callback is None:
        return
    try:
        callback(client_id)
    except Exception:  # noqa: BLE001
        log.error("a notification hook failed")


def _text(value: str, limit: int) -> str:
    cleaned = redact(" ".join(str(value).split()))
    return (cleaned if isinstance(cleaned, str) else "")[:limit]
