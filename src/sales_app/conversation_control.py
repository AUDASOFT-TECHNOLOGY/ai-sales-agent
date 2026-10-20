"""Who is in charge of each conversation, kept apart from the agent (spec 9.3, 9.4).

A conversation is the agent's until the Brain hands it over or a person takes it. While a person is
in charge the Engine sends nothing on its own to that prospect (see
`sales_core.ports.conversation_control`); this module is where that state lives and what surrounds it:

- `ControlRows` is the storage port (one row per client and prospect; the SQL adapter is in
  `persistence.control_rows`, `InMemoryControlRows` serves a deployment without a database and the
  tests);
- `ConversationControlHub.for_client(client_id)` is the store the Engine is given, bound to one client.
  When the Brain hands a conversation over it opens the client's `human_intervention` notification
  (one per prospect, closed when a person takes the conversation or gives it back) and writes the
  central audit entry.

A control store that cannot be read raises; the Engine treats that as "a person may be in charge".
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sales_app.audit_log import AuditService
from sales_app.notifications import CLIENT, HUMAN_INTERVENTION, NotificationService
from sales_core.contracts import ClientId, ProspectId
from sales_core.ports import ControlMode, ConversationControl

AGENT, HUMAN = ControlMode.AGENT.value, ControlMode.HUMAN.value
MAX_REASON = 300


@dataclass(frozen=True, slots=True)
class ControlRecord:
    client_id: str
    prospect_id: str
    mode: str  # agent | human
    taken_by: str | None = None
    taken_at: datetime | None = None
    reason: str | None = None
    released_at: datetime | None = None


@runtime_checkable
class ControlRows(Protocol):
    def get(self, client_id: str, prospect_id: str) -> ControlRecord | None: ...

    def humans(self, client_id: str) -> frozenset[str]: ...

    def put(self, record: ControlRecord) -> None:
        """Insert or replace the row of (client, prospect)."""
        ...


class InMemoryControlRows:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ControlRecord] = {}
        self._lock = threading.Lock()

    def get(self, client_id: str, prospect_id: str) -> ControlRecord | None:
        with self._lock:
            return self._rows.get((client_id, prospect_id))

    def humans(self, client_id: str) -> frozenset[str]:
        with self._lock:
            return frozenset(
                r.prospect_id
                for r in self._rows.values()
                if r.client_id == client_id and r.mode == HUMAN
            )

    def put(self, record: ControlRecord) -> None:
        with self._lock:
            self._rows[(record.client_id, record.prospect_id)] = record


def notification_key(prospect_id: str) -> str:
    """The dedupe key of the "needs a person" notification of one conversation."""
    return f"human:{prospect_id}"


def _reason(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split())[:MAX_REASON]
    return cleaned or None


class ConversationControlHub:
    def __init__(
        self,
        rows: ControlRows,
        *,
        notifications: NotificationService | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self._rows = rows
        self._notifications = notifications
        self._audit = audit

    def __repr__(self) -> str:
        return "ConversationControlHub()"

    def for_client(self, client_id: str) -> "BoundControl":
        return BoundControl(self, client_id)


class BoundControl:
    """The `ConversationControlStore` of one client."""

    def __init__(self, hub: ConversationControlHub, client_id: str) -> None:
        if not client_id:
            raise ValueError("a conversation control needs a client id")
        self._hub = hub
        self._client_id = ClientId(client_id)

    def __repr__(self) -> str:
        return f"BoundControl(client={self._client_id!r})"

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    # ---- reading ---------------------------------------------------------------------------

    def get(self, prospect_id: ProspectId) -> ConversationControl | None:
        record = self._hub._rows.get(self._client_id, prospect_id)
        return None if record is None else self._control(record)

    def humans(self) -> frozenset[ProspectId]:
        return frozenset(ProspectId(p) for p in self._hub._rows.humans(self._client_id))

    # ---- changing --------------------------------------------------------------------------

    def await_human(
        self, prospect_id: ProspectId, *, reason: str | None, at: datetime
    ) -> ConversationControl:
        current = self._hub._rows.get(self._client_id, prospect_id)
        if current is not None and current.mode == HUMAN:
            return self._control(current)  # a person already has it, or it already waits
        record = ControlRecord(
            client_id=self._client_id,
            prospect_id=prospect_id,
            mode=HUMAN,
            taken_at=at,
            reason=_reason(reason),
        )
        self._hub._rows.put(record)
        self._notify(prospect_id)
        self._record("conversation.awaiting_human", "agent", prospect_id, after=HUMAN)
        return self._control(record)

    def take(
        self, prospect_id: ProspectId, *, by: str, reason: str | None, at: datetime
    ) -> ConversationControl:
        current = self._hub._rows.get(self._client_id, prospect_id)
        if current is not None and current.mode == HUMAN and current.taken_by == by:
            return self._control(current)
        kept = current.reason if current is not None and current.mode == HUMAN else None
        record = ControlRecord(
            client_id=self._client_id,
            prospect_id=prospect_id,
            mode=HUMAN,
            taken_by=by,
            taken_at=at if current is None or current.mode != HUMAN else current.taken_at or at,
            reason=_reason(reason) or kept,
        )
        self._hub._rows.put(record)
        self._resolve(prospect_id)
        return self._control(record)

    def release(self, prospect_id: ProspectId, *, at: datetime) -> ConversationControl | None:
        current = self._hub._rows.get(self._client_id, prospect_id)
        if current is None or current.mode != HUMAN:
            return None
        record = ControlRecord(
            client_id=self._client_id,
            prospect_id=prospect_id,
            mode=AGENT,
            taken_by=current.taken_by,
            taken_at=current.taken_at,
            reason=current.reason,
            released_at=at,
        )
        self._hub._rows.put(record)
        self._resolve(prospect_id)
        return self._control(record)

    # ---- around it -------------------------------------------------------------------------

    @staticmethod
    def _control(record: ControlRecord) -> ConversationControl:
        return ConversationControl(
            client_id=ClientId(record.client_id),
            prospect_id=ProspectId(record.prospect_id),
            mode=ControlMode(record.mode),
            taken_by=record.taken_by,
            taken_at=record.taken_at,
            reason=record.reason,
            released_at=record.released_at,
        )

    def _notify(self, prospect_id: str) -> None:
        service = self._hub._notifications
        if service is not None:
            service.notify(
                HUMAN_INTERVENTION,
                CLIENT,
                self._client_id,
                dedupe_key=notification_key(prospect_id),
            )

    def _resolve(self, prospect_id: str) -> None:
        service = self._hub._notifications
        if service is not None:
            service.resolve(CLIENT, self._client_id, notification_key(prospect_id))

    def _record(self, action: str, actor: str, prospect_id: str, *, after: str) -> None:
        audit = self._hub._audit
        if audit is not None:
            audit.record(
                actor=actor,
                actor_role="agent",
                action=action,
                target=f"prospect:{prospect_id}",
                client_id=self._client_id,
                after={"mode": after},
            )
