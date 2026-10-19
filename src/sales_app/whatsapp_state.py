"""What Revansa keeps about a client's WhatsApp account: the 24-hour windows, the delivery status of
each message sent, and the message templates.

Three small registers, all per client, none holding a message's content:

- **windows**: the last time each person wrote to the account. WhatsApp allows free text only
  within 24 hours of it; the channel asks here before every send;
- **deliveries**: Meta's status of each message we sent (`sent`, `delivered`, `read`, `failed`
  with Meta's error code), by the message id Meta gave;
- **templates**: the account's message templates as Meta knows them (status, category, variables),
  and which approved one opens a conversation.

`InMemoryWhatsAppState` is the test double and the fallback of a deployment with no database: its
windows and statuses are lost on restart, which errs on the safe side (an unknown window is a
closed one, so a template is used rather than free text).
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

APPROVED = "APPROVED"
PENDING = "PENDING"
REJECTED = "REJECTED"
PAUSED = "PAUSED"
DISABLED = "DISABLED"
# Statuses in which Meta lets the template be sent.
SENDABLE = frozenset({APPROVED})

SENT, DELIVERED, READ, FAILED = "sent", "delivered", "read", "failed"
_RANK = {SENT: 1, DELIVERED: 2, READ: 3}


@dataclass(frozen=True, slots=True)
class TemplateRecord:
    client_id: str
    name: str
    language: str
    category: str = ""
    status: str = PENDING
    body: str = ""
    parameter_names: tuple[str, ...] = ()
    reason: str = ""
    meta_id: str = ""
    is_opening: bool = False  # the approved template that opens / reopens a conversation
    updated_at: datetime | None = None

    @property
    def sendable(self) -> bool:
        return self.status in SENDABLE


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    client_id: str
    message_id: str  # the id Meta gave when it accepted the message
    status: str
    at: datetime
    recipient: str = ""
    error_code: str = ""
    error_title: str = ""


class WhatsAppState(Protocol):
    """Implementations are safe to share between threads."""

    def record_inbound(self, client_id: str, wa_id: str, at: datetime) -> None:
        """Only moves forward: an older message never shortens a window."""
        ...

    def last_inbound(self, client_id: str, wa_id: str) -> datetime | None: ...

    def close_window(self, client_id: str, wa_id: str) -> None:
        """Meta says the 24 hours are over: forget the window."""
        ...

    def record_delivery(self, record: DeliveryRecord) -> None:
        """Only moves forward (`sent` < `delivered` < `read`); `failed` replaces anything but
        `read`."""
        ...

    def delivery(self, client_id: str, message_id: str) -> DeliveryRecord | None: ...

    def deliveries(
        self, client_id: str, message_ids: Sequence[str]
    ) -> dict[str, DeliveryRecord]: ...

    def delivery_counts(self, client_id: str, since: datetime) -> dict[str, int]: ...

    def recent_failures(self, client_id: str, limit: int = 20) -> Sequence[DeliveryRecord]: ...

    def templates(self, client_id: str) -> Sequence[TemplateRecord]: ...

    def save_template(self, record: TemplateRecord) -> None:
        """Create or replace by (name, language); keeps `is_opening` only if the record asks."""
        ...

    def replace_templates(self, client_id: str, records: Sequence[TemplateRecord]) -> None:
        """What Meta lists is the truth: the account's templates become exactly these, and the
        opening choice survives for a template that is still listed."""
        ...

    def delete_template(self, client_id: str, name: str) -> None: ...

    def set_opening(self, client_id: str, name: str, language: str) -> bool:
        """Make this approved template the opening one (and no other); False if it is not
        approved or unknown."""
        ...

    def clear_opening(self, client_id: str) -> None: ...

    def opening_template(self, client_id: str) -> TemplateRecord | None:
        """The opening template, only while it is approved."""
        ...

    def forget_client(self, client_id: str) -> None:
        """Disconnection: the templates and the windows of this account go; the delivery history
        stays (it belongs to the conversations)."""
        ...


def moves_forward(old: DeliveryRecord | None, new: DeliveryRecord) -> bool:
    """Whether `new` should replace `old`: statuses arrive out of order."""
    if old is None:
        return True
    if new.status == FAILED:
        return old.status != READ
    if old.status == FAILED:
        return False
    return _RANK.get(new.status, 0) > _RANK.get(old.status, 0)


class InMemoryWhatsAppState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], datetime] = {}
        self._deliveries: dict[tuple[str, str], DeliveryRecord] = {}
        self._templates: dict[tuple[str, str, str], TemplateRecord] = {}

    def __repr__(self) -> str:
        return "InMemoryWhatsAppState()"

    def record_inbound(self, client_id: str, wa_id: str, at: datetime) -> None:
        with self._lock:
            key = (client_id, wa_id)
            if key not in self._windows or self._windows[key] < at:
                self._windows[key] = at

    def last_inbound(self, client_id: str, wa_id: str) -> datetime | None:
        with self._lock:
            return self._windows.get((client_id, wa_id))

    def close_window(self, client_id: str, wa_id: str) -> None:
        with self._lock:
            self._windows.pop((client_id, wa_id), None)

    def record_delivery(self, record: DeliveryRecord) -> None:
        with self._lock:
            key = (record.client_id, record.message_id)
            if moves_forward(self._deliveries.get(key), record):
                self._deliveries[key] = record

    def delivery(self, client_id: str, message_id: str) -> DeliveryRecord | None:
        with self._lock:
            return self._deliveries.get((client_id, message_id))

    def deliveries(self, client_id: str, message_ids: Sequence[str]) -> dict[str, DeliveryRecord]:
        with self._lock:
            found = ((m, self._deliveries.get((client_id, m))) for m in message_ids)
            return {m: r for m, r in found if r is not None}

    def delivery_counts(self, client_id: str, since: datetime) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for (owner, _), record in self._deliveries.items():
                if owner == client_id and record.at >= since:
                    counts[record.status] = counts.get(record.status, 0) + 1
        return counts

    def recent_failures(self, client_id: str, limit: int = 20) -> Sequence[DeliveryRecord]:
        with self._lock:
            failed = [
                r for (c, _), r in self._deliveries.items() if c == client_id and r.status == FAILED
            ]
        return sorted(failed, key=lambda r: r.at, reverse=True)[:limit]

    def templates(self, client_id: str) -> Sequence[TemplateRecord]:
        with self._lock:
            rows = [t for (c, _, _), t in self._templates.items() if c == client_id]
        return sorted(rows, key=lambda t: (t.name, t.language))

    def save_template(self, record: TemplateRecord) -> None:
        with self._lock:
            self._templates[(record.client_id, record.name, record.language)] = record

    def replace_templates(self, client_id: str, records: Sequence[TemplateRecord]) -> None:
        with self._lock:
            kept = {
                (t.name, t.language)
                for (c, *_), t in self._templates.items()
                if c == client_id and t.is_opening
            }
            for key in [k for k in self._templates if k[0] == client_id]:
                del self._templates[key]
            for record in records:
                opening = (record.name, record.language) in kept
                self._templates[(client_id, record.name, record.language)] = replace(
                    record, is_opening=opening and record.sendable
                )

    def delete_template(self, client_id: str, name: str) -> None:
        with self._lock:
            for key in [k for k in self._templates if k[0] == client_id and k[1] == name]:
                del self._templates[key]

    def set_opening(self, client_id: str, name: str, language: str) -> bool:
        with self._lock:
            chosen = self._templates.get((client_id, name, language))
            if chosen is None or not chosen.sendable:
                return False
            for key, template in list(self._templates.items()):
                if key[0] == client_id:
                    self._templates[key] = replace(
                        template, is_opening=key == (client_id, name, language)
                    )
            return True

    def clear_opening(self, client_id: str) -> None:
        with self._lock:
            for key, template in list(self._templates.items()):
                if key[0] == client_id and template.is_opening:
                    self._templates[key] = replace(template, is_opening=False)

    def opening_template(self, client_id: str) -> TemplateRecord | None:
        with self._lock:
            return next(
                (
                    t
                    for (c, _, _), t in self._templates.items()
                    if c == client_id and t.is_opening and t.sendable
                ),
                None,
            )

    def forget_client(self, client_id: str) -> None:
        with self._lock:
            for table in (self._windows,):
                for key in [k for k in table if k[0] == client_id]:
                    del table[key]
            for key in [k for k in self._templates if k[0] == client_id]:
                del self._templates[key]
