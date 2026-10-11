"""The Client's agent switch in SQL: the `AgentStateStore` port."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from sales_app.agent_state import AgentState
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_agent_state


class SqlAgentStateStore:
    def __init__(self, engine: Engine, *, now: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))

    def get(self, client_id: str) -> AgentState | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(client_agent_state).where(client_agent_state.c.client_id == client_id)
            ).first()
        return None if row is None else _state(row)

    def set(self, client_id: str, *, running: bool, by: str, at: datetime) -> AgentState:
        state = AgentState(client_id=client_id, running=running, changed_at=at, changed_by=by)
        values = {"running": running, "changed_at": at, "changed_by": by}
        try:
            self._write(client_id, values)
        except IntegrityError:  # two first writes raced: the second one now updates
            self._write(client_id, values)
        return state

    def _write(self, client_id: str, values: dict) -> None:
        with self._engine.begin() as connection:
            ensure_client(connection, client_id, created_at=self._now(), created_by=None)
            changed = connection.execute(
                update(client_agent_state)
                .where(client_agent_state.c.client_id == client_id)
                .values(**values)
            ).rowcount
            if not changed:
                connection.execute(insert(client_agent_state).values(client_id=client_id, **values))


def _state(row) -> AgentState:
    return AgentState(
        client_id=row.client_id,
        running=row.running,
        changed_at=row.changed_at,
        changed_by=row.changed_by,
    )
