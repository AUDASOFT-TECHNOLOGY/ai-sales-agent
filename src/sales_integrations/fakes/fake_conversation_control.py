"""In-memory conversation control for tests. Persists nothing."""

from datetime import datetime

from sales_core.contracts import ClientId, ProspectId
from sales_core.ports import ControlMode, ConversationControl


class FakeConversationControl:
    """Implements `sales_core.ports.ConversationControlStore`, bound to one client."""

    def __init__(self, client_id: ClientId) -> None:
        self._client_id = client_id
        self._rows: dict[ProspectId, ConversationControl] = {}
        self.fail_reads = False  # a store that cannot be read: the Engine must fail closed

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    def get(self, prospect_id: ProspectId) -> ConversationControl | None:
        if self.fail_reads:
            raise OSError("store unavailable")
        return self._rows.get(prospect_id)

    def humans(self) -> frozenset[ProspectId]:
        if self.fail_reads:
            raise OSError("store unavailable")
        return frozenset(p for p, c in self._rows.items() if c.is_human)

    def await_human(
        self, prospect_id: ProspectId, *, reason: str | None, at: datetime
    ) -> ConversationControl:
        current = self._rows.get(prospect_id)
        if current is not None and current.is_human:
            return current
        row = ConversationControl(
            client_id=self._client_id,
            prospect_id=prospect_id,
            mode=ControlMode.HUMAN,
            taken_at=at,
            reason=reason,
        )
        self._rows[prospect_id] = row
        return row

    def take(
        self, prospect_id: ProspectId, *, by: str, reason: str | None, at: datetime
    ) -> ConversationControl:
        current = self._rows.get(prospect_id)
        if current is not None and current.is_human and current.taken_by == by:
            return current
        row = ConversationControl(
            client_id=self._client_id,
            prospect_id=prospect_id,
            mode=ControlMode.HUMAN,
            taken_by=by,
            taken_at=at,
            reason=reason if reason is not None else (current.reason if current else None),
        )
        self._rows[prospect_id] = row
        return row

    def release(self, prospect_id: ProspectId, *, at: datetime) -> ConversationControl | None:
        current = self._rows.get(prospect_id)
        if current is None or not current.is_human:
            return None
        row = ConversationControl(
            client_id=self._client_id,
            prospect_id=prospect_id,
            mode=ControlMode.AGENT,
            taken_by=current.taken_by,
            taken_at=current.taken_at,
            reason=current.reason,
            released_at=at,
        )
        self._rows[prospect_id] = row
        return row
