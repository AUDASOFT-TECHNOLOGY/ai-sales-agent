"""The SQL activity of one client's agent (`sales_app.agent_activity`).

Bound to one client: every statement filters on it. Each `begin` is one transaction that registers the
client first (idempotent, so the foreign key holds) and forgets what is older than `KEEP_FOR`. A
database that cannot be reached is `StoreUnavailableError`, a fixed sentence.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, delete, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.agent_activity import DONE, FAILED, KEEP_FOR, RUNNING, ActivityStep
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import op_agent_activity as steps
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the agent's activity is unavailable"


class SqlActivityStore:
    def __init__(self, engine: Engine, client_id: str) -> None:
        if not client_id:
            raise ValueError("an activity store needs a client id")
        self._engine = engine
        self._client_id = client_id

    def __repr__(self) -> str:
        return f"SqlActivityStore(client={self._client_id!r})"

    def begin(
        self,
        kind: str,
        *,
        prospect_id: str | None,
        name: str | None,
        detail: str | None,
        at: datetime,
    ) -> int:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, self._client_id, created_at=datetime.now(UTC), created_by=None
                )
                connection.execute(
                    delete(steps).where(
                        steps.c.client_id == self._client_id, steps.c.started_at < at - KEEP_FOR
                    )
                )
                result = connection.execute(
                    insert(steps).values(
                        client_id=self._client_id,
                        kind=kind,
                        state=RUNNING,
                        started_at=at,
                        prospect_id=prospect_id,
                        name=name,
                        detail=detail,
                    )
                )
                return int(result.inserted_primary_key[0])
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def end(self, seq: int, *, ok: bool, at: datetime) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(steps)
                    .where(steps.c.client_id == self._client_id, steps.c.seq == seq)
                    .values(state=DONE if ok else FAILED, ended_at=at)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def latest(self, limit: int) -> list[ActivityStep]:
        query = (
            select(steps)
            .where(steps.c.client_id == self._client_id)
            .order_by(steps.c.seq.desc())
            .limit(limit)
        )
        try:
            with self._engine.connect() as connection:
                return [
                    ActivityStep(
                        seq=r.seq,
                        client_id=r.client_id,
                        kind=r.kind,
                        state=r.state,
                        started_at=r.started_at,
                        ended_at=r.ended_at,
                        prospect_id=r.prospect_id,
                        name=r.name,
                        detail=r.detail,
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
