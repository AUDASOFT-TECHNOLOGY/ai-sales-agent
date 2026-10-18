"""The consent log of one client in the database (`sales_app.sourcing.consent.log.ConsentLog`).

Every statement is filtered on the client, so a log can only ever show its own client's events.
Appends are compare-and-append in ONE transaction: the last sequence number is read and the next
row inserted together, and `(client_id, seq)` is the primary key, so two writers racing for the same
position cannot both succeed whatever the isolation level: the loser gets `ConsentConflictError` and
nothing is written. No file, no file lock: the database is the only place the log lives.
"""

from datetime import datetime

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.tables import consent_events
from sales_app.sourcing.consent.errors import (
    ConsentConflictError,
    ConsentFormatError,
    ConsentLogUnavailableError,
)
from sales_app.sourcing.consent.events import ConsentEvent, EventType, Origin, ReasonCode
from sales_app.sourcing.consent.events import _stamp as stamp
from sales_app.sourcing.consent.log import LogContents, _Reentrant


def _event(row) -> ConsentEvent:
    return ConsentEvent(
        seq=row.seq,
        event_id=row.event_id,
        key=row.key,
        type=EventType(row.type),
        origin=Origin(row.origin),
        at=datetime.fromisoformat(row.at),
        actor=row.actor,
        reason=ReasonCode(row.reason),
        evidence_ref=row.evidence_ref,
        prev_mac=row.prev_mac,
        mac=row.mac,
    )


class SqlConsentLog:
    def __init__(self, engine: Engine, client_id: str) -> None:
        self._engine = engine
        self._client_id = client_id
        # In-process re-entry only: ordering across processes is the primary key's job, not a lock's.
        self.exclusive = _Reentrant(lambda: None, lambda: None)

    def exists(self) -> bool:
        try:
            with self._engine.connect() as connection:
                return (
                    connection.execute(
                        select(consent_events.c.seq)
                        .where(consent_events.c.client_id == self._client_id)
                        .limit(1)
                    ).first()
                    is not None
                )
        except SQLAlchemyError:
            raise ConsentLogUnavailableError("the consent log could not be read") from None

    def read(self) -> LogContents:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(consent_events)
                    .where(consent_events.c.client_id == self._client_id)
                    .order_by(consent_events.c.seq)
                ).all()
        except SQLAlchemyError:
            raise ConsentLogUnavailableError("the consent log could not be read") from None
        try:
            return LogContents(tuple(_event(r) for r in rows))
        except ValueError:  # an unknown enum value: the row was not written by this registry
            raise ConsentFormatError("a stored event is not valid") from None

    def append(self, event: ConsentEvent, *, expected_last_seq: int) -> None:
        try:
            with self._engine.begin() as connection:
                last = connection.execute(
                    select(func.max(consent_events.c.seq)).where(
                        consent_events.c.client_id == self._client_id
                    )
                ).scalar()
                if (last or 0) != expected_last_seq or event.seq != expected_last_seq + 1:
                    raise ConsentConflictError("the consent log moved on; nothing was written")
                connection.execute(
                    insert(consent_events).values(
                        client_id=self._client_id,
                        seq=event.seq,
                        event_id=event.event_id,
                        key=event.key,
                        type=event.type.value,
                        origin=event.origin.value,
                        at=stamp(event.at),
                        actor=event.actor,
                        reason=event.reason.value,
                        evidence_ref=event.evidence_ref,
                        prev_mac=event.prev_mac,
                        mac=event.mac,
                    )
                )
        except IntegrityError:
            # Another writer took this position (or this event id) first.
            raise ConsentConflictError("the consent log moved on; nothing was written") from None
        except SQLAlchemyError:
            raise ConsentLogUnavailableError("the consent log could not be written") from None
