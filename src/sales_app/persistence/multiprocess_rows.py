"""Shared state that must outlive one process: pending consents and job leases.

Both are small and short-lived. They hold no credential and no personal data (see migration 0020).

`SqlPendingConsents.take` reads and deletes in one statement (`DELETE ... RETURNING`), so two
processes racing for the same consent cannot both get it: the first callback wins, valid or not.
`SqlJobLeases.acquire` is one UPDATE (take an expired lease, or renew one's own) then one INSERT (the
primary key refuses a second holder), so two processes cannot both hold a (client, job).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, delete, insert, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.tables import job_leases, oauth_pending_consents
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the shared state is unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class PendingConsentRow:
    client_id: str
    verifier: str
    expires_at: datetime

    def __repr__(self) -> str:
        return "PendingConsentRow()"


class SqlPendingConsents:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlPendingConsents()"

    def put(
        self, kind: str, session_id: str, client_id: str, verifier: str, expires_at: datetime
    ) -> None:
        now = datetime.now(UTC)
        table = oauth_pending_consents
        try:
            with self._engine.begin() as connection:
                # Expired consents are of no use to anyone: forget them.
                connection.execute(delete(table).where(table.c.expires_at <= now))
                connection.execute(
                    delete(table).where(table.c.kind == kind, table.c.session_id == session_id)
                )
                connection.execute(
                    insert(table).values(
                        kind=kind,
                        session_id=session_id,
                        client_id=client_id,
                        verifier=verifier,
                        expires_at=expires_at,
                        created_at=now,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def take(self, kind: str, session_id: str) -> PendingConsentRow | None:
        """Return and delete, atomically. Expiry is the caller's check (it holds the clock); the row
        is gone whatever happens next."""
        table = oauth_pending_consents
        statement = (
            delete(table)
            .where(table.c.kind == kind, table.c.session_id == session_id)
            .returning(table.c.client_id, table.c.verifier, table.c.expires_at)
        )
        try:
            with self._engine.begin() as connection:
                row = connection.execute(statement).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return PendingConsentRow(row.client_id, row.verifier, expires)


class SqlJobLeases:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlJobLeases()"

    def acquire(self, client_id: str, job: str, holder: str, now: datetime, ttl: timedelta) -> bool:
        """Take the lease if it is free, expired, or already this holder's. A lost race is simply
        `False`; only an unreachable database raises."""
        expires = now + ttl
        mine = (job_leases.c.client_id == client_id) & (job_leases.c.job == job)
        try:
            with self._engine.begin() as connection:
                renewed = connection.execute(
                    update(job_leases)
                    .where(mine, (job_leases.c.holder == holder) | (job_leases.c.expires_at <= now))
                    .values(holder=holder, acquired_at=now, expires_at=expires)
                )
                if renewed.rowcount == 1:
                    return True
            with self._engine.begin() as connection:
                connection.execute(
                    insert(job_leases).values(
                        client_id=client_id,
                        job=job,
                        holder=holder,
                        acquired_at=now,
                        expires_at=expires,
                    )
                )
            return True
        except IntegrityError:
            return False  # a row exists and someone else holds it (or took it a moment ago)
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def release(self, client_id: str, job: str, holder: str) -> None:
        """Free the lease, only if it is still this holder's (it may have expired and been taken)."""
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(job_leases).where(
                        job_leases.c.client_id == client_id,
                        job_leases.c.job == job,
                        job_leases.c.holder == holder,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
