"""The SQL half of a client's metrics: counts and groupings, for one client, over a period.

Knows SQL and nothing of what the figures mean: it returns plain counts, the pure side
(`sales_app.metrics`) names, folds and presents them. Every statement filters on the client. A
period is `[since, until)`; either bound may be None. Failures are one fixed sentence.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.tables import op_actions, op_facts, op_messages, op_prospect_index

_UNAVAILABLE = "the metrics are unavailable"
_I = op_prospect_index
_DIMENSIONS = {
    "status": _I.c.status,
    "lane": _I.c.lane_id,
    "channel": _I.c.channel,
    "source": _I.c.source_id,
}


class MetricsUnavailableError(Exception):
    """The store cannot be read; says nothing of the cause."""


class SqlMetricsRows:
    def __init__(self, engine: Engine, client_id: str) -> None:
        if not client_id:
            raise ValueError("metrics need a client id")
        self._engine = engine
        self._client_id = client_id

    def __repr__(self) -> str:
        return f"SqlMetricsRows(client={self._client_id!r})"

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        try:
            with self._engine.connect() as connection:
                yield connection
        except SQLAlchemyError:
            raise MetricsUnavailableError(_UNAVAILABLE) from None

    # ---- prospects -----------------------------------------------------------------------

    def prospect_counts(
        self, by: str, *, since: datetime | None = None, until: datetime | None = None
    ) -> dict[str | None, int]:
        """Prospects grouped by `status`, `lane`, `channel` or `source`. With a period, only those
        whose last activity falls in it. A key of None is "not attributed", counted as such."""
        column = _DIMENSIONS[by]
        query = select(column, func.count()).where(_I.c.client_id == self._client_id)
        if since is not None:
            query = query.where(_I.c.last_activity_at >= since)
        if until is not None:
            query = query.where(_I.c.last_activity_at < until)
        with self._read() as connection:
            return {k: n for k, n in connection.execute(query.group_by(column))}

    def prospect_total(self) -> int:
        query = select(func.count()).select_from(_I).where(_I.c.client_id == self._client_id)
        with self._read() as connection:
            return connection.execute(query).scalar_one()

    def needs_human(self) -> int:
        query = (
            select(func.count())
            .select_from(_I)
            .where(_I.c.client_id == self._client_id, _I.c.needs_human.is_(True))
        )
        with self._read() as connection:
            return connection.execute(query).scalar_one()

    # ---- messages ------------------------------------------------------------------------

    def message_counts(
        self, *, since: datetime | None, until: datetime | None
    ) -> dict[tuple[str | None, str | None], int]:
        """Messages by (direction, channel) sent or received in the period."""
        query = self._messages(
            select(op_messages.c.direction, op_messages.c.channel, func.count()), since, until
        ).group_by(op_messages.c.direction, op_messages.c.channel)
        with self._read() as connection:
            return {(d, c): n for d, c, n in connection.execute(query)}

    def distinct_prospects_by_direction(
        self, *, since: datetime | None, until: datetime | None
    ) -> dict[str | None, int]:
        """How many different prospects have at least one message of each direction in the period."""
        query = self._messages(
            select(op_messages.c.direction, func.count(op_messages.c.prospect_id.distinct())),
            since,
            until,
        ).group_by(op_messages.c.direction)
        with self._read() as connection:
            return {d: n for d, n in connection.execute(query)}

    def distinct_prospects_with_messages(
        self, *, since: datetime | None, until: datetime | None
    ) -> int:
        query = self._messages(
            select(func.count(op_messages.c.prospect_id.distinct())), since, until
        )
        with self._read() as connection:
            return connection.execute(query).scalar_one()

    def messages_by_day(
        self, *, since: datetime | None, until: datetime | None
    ) -> dict[str, dict[str | None, int]]:
        """Messages per UTC day and direction, for a chart."""
        day = func.date(op_messages.c.sent_at)
        query = self._messages(select(day, op_messages.c.direction, func.count()), since, until)
        with self._read() as connection:
            found = connection.execute(query.group_by(day, op_messages.c.direction)).all()
        result: dict[str, dict[str | None, int]] = {}
        for d, direction, n in found:
            if d is not None:
                result.setdefault(str(d), {})[direction] = n
        return result

    def _messages(self, query: Any, since: datetime | None, until: datetime | None) -> Any:
        query = query.where(op_messages.c.client_id == self._client_id)
        if since is not None:
            query = query.where(op_messages.c.sent_at >= since)
        if until is not None:
            query = query.where(op_messages.c.sent_at < until)
        return query

    # ---- actions -------------------------------------------------------------------------

    def prospects_with_failed_actions(
        self, *, since: datetime | None, until: datetime | None
    ) -> int:
        query = select(func.count(op_actions.c.prospect_id.distinct())).where(
            op_actions.c.client_id == self._client_id, op_actions.c.status == "failed"
        )
        if since is not None:
            query = query.where(op_actions.c.due_at >= since)
        if until is not None:
            query = query.where(op_actions.c.due_at < until)
        with self._read() as connection:
            return connection.execute(query).scalar_one()

    # ---- facts ---------------------------------------------------------------------------

    def fact_documents(
        self, keys: tuple[str, ...], *, since: datetime | None, until: datetime | None
    ) -> list[tuple[str, Any]]:
        """(prospect id, fact document) of the facts with these keys recorded in the period.
        Only payment facts are asked for, a handful per paying prospect."""
        query = select(op_facts.c.prospect_id, op_facts.c.document).where(
            op_facts.c.client_id == self._client_id, op_facts.c.fact_key.in_(keys)
        )
        if since is not None:
            query = query.where(op_facts.c.recorded_at >= since)
        if until is not None:
            query = query.where(op_facts.c.recorded_at < until)
        with self._read() as connection:
            return [(p, d) for p, d in connection.execute(query.order_by(op_facts.c.seq))]
