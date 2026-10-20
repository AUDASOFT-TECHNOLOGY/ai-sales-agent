"""The central audit log: who did what, to which client, when, with which outcome (spec 12.4).

One append-only register for the sensitive operations of the Admin (clients, models, plans, quotas,
invoices, integrations, channels, permissions). It complements, and does not replace, the
specialised registers that already exist: `secret_store.audit` (who changed which credential),
`op_audit` (the operational store) and `payment_events` (payments).

**Never a secret.** `before` / `after` go through `redact` before they are kept: a key that names a
secret (token, password, secret, key, value, authorization...) is replaced, a string that looks like a
credential too, and everything is size-bounded. Callers should still pass only what is useful.

The service never raises: an audit that cannot be written is logged (a fixed sentence) and the
operation it describes is not undone. `AuditLog` is the port; the SQL adapter is in `persistence`.
"""

import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

REDACTED = "[redacted]"
OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_REJECTED, OUTCOME_ERROR = (
    "success",
    "denied",
    "rejected",
    "error",
)
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_REJECTED, OUTCOME_ERROR)

_SECRET_KEY = re.compile(
    r"(secret|token|passw|pwd|credential|authorization|cookie|api[_-]?key|private|signature|"
    r"(^|[_-])key($|[_-])|^value$|^code$|^otp$|session)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|bearer\s+[A-Za-z0-9._\-]{8,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"
    r"|EAA[A-Za-z0-9]{20,}|[A-Za-z0-9+/_\-]{40,})",
    re.IGNORECASE,
)
MAX_TEXT = 200
MAX_ITEMS = 25
MAX_DEPTH = 4


def redact(value: Any, _depth: int = 0) -> Any:
    """A copy of `value` that is safe to keep: JSON types only, secrets replaced, size bounded."""
    if _depth > MAX_DEPTH:
        return "..."
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if _SECRET_TEXT.search(value):
            return REDACTED
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "..."
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                out["..."] = "truncated"
                break
            name = str(key)[:64]
            out[name] = REDACTED if _SECRET_KEY.search(name) else redact(item, _depth + 1)
        return out
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(item, _depth + 1) for item in list(value)[:MAX_ITEMS]]
    return REDACTED  # an object we do not know how to show


@dataclass(frozen=True, slots=True)
class AuditEntry:
    at: datetime
    actor: str
    actor_role: str
    client_id: str | None
    action: str
    target: str
    outcome: str = OUTCOME_SUCCESS
    before: Any = None
    after: Any = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """`client_ids`: the clients the reader may see (None: all, including entries without a client)."""

    client_ids: frozenset[str] | None = None
    client_id: str | None = None
    action: str | None = None  # exact, or a prefix when it ends with "*"
    actor: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50
    offset: int = 0


@runtime_checkable
class AuditLog(Protocol):
    def append(self, entry: AuditEntry) -> None: ...

    def search(self, query: AuditQuery) -> tuple[list[AuditEntry], int]:
        """(page, total matching), most recent first."""
        ...


def matches(entry: AuditEntry, query: AuditQuery) -> bool:
    if query.client_ids is not None and entry.client_id not in query.client_ids:
        return False
    if query.client_id is not None and entry.client_id != query.client_id:
        return False
    if query.actor is not None and entry.actor != query.actor:
        return False
    if query.action is not None:
        wanted = query.action
        if wanted.endswith("*"):
            if not entry.action.startswith(wanted[:-1]):
                return False
        elif entry.action != wanted:
            return False
    if query.since is not None and entry.at < query.since:
        return False
    return not (query.until is not None and entry.at >= query.until)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(
                AuditEntry(**{**_fields(entry), "id": len(self._entries) + 1})
            )

    def search(self, query: AuditQuery) -> tuple[list[AuditEntry], int]:
        with self._lock:
            found = [e for e in reversed(self._entries) if matches(e, query)]
        return found[query.offset : query.offset + query.limit], len(found)


def _fields(entry: AuditEntry) -> dict[str, Any]:
    return {
        "at": entry.at,
        "actor": entry.actor,
        "actor_role": entry.actor_role,
        "client_id": entry.client_id,
        "action": entry.action,
        "target": entry.target,
        "outcome": entry.outcome,
        "before": entry.before,
        "after": entry.after,
    }


class AuditService:
    """What the rest of the application calls: `record(...)`. Never raises, always redacts."""

    def __init__(self, log_store: AuditLog, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = log_store
        self._now = now or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return "AuditService()"

    def record(
        self,
        *,
        actor: str,
        actor_role: str,
        action: str,
        target: str = "",
        client_id: str | None = None,
        outcome: str = OUTCOME_SUCCESS,
        before: Any = None,
        after: Any = None,
    ) -> None:
        try:
            self._store.append(
                AuditEntry(
                    at=self._now(),
                    actor=str(actor)[:128],
                    actor_role=str(actor_role)[:32],
                    client_id=client_id,
                    action=str(action)[:96],
                    target=redact(str(target)[:200]),
                    outcome=outcome if outcome in OUTCOMES else OUTCOME_ERROR,
                    before=redact(before),
                    after=redact(after),
                )
            )
        except Exception:  # noqa: BLE001 - an audit failure never undoes the operation
            log.error("could not write an audit entry (%s)", action)

    def search(self, query: AuditQuery) -> tuple[list[AuditEntry], int]:
        return self._store.search(query)
