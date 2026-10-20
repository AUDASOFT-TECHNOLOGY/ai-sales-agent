"""Adapters over the shared (database) state that lets several processes serve one deployment.

- `SharedPendingAuthorizations`: the `PendingAuthorizationStore` of the Google-style consents
  (Gmail, Sheets, HubSpot) kept in the database, so a callback may land on another process than the
  one that started the consent. Single use, atomic: the first `take` wins.
- `JobLeaseGuard`: the scheduler's per-(client, job) lease. Two processes never run the same job for
  the same client at the same time; a process that dies loses its lease at the TTL.

Neither is needed by a single process, and neither changes what one process does: without a database
the in-memory stores stay, and a guard is simply not installed.

This module knows no SQL: it talks to the row classes of `sales_app.persistence.multiprocess_rows`
through their methods, so tests can use any double of them.
"""

import contextlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Protocol

from sales_app.google_mailbox import PendingAuthorization

DEFAULT_LEASE_TTL = timedelta(minutes=30)  # longer than any job; a crashed holder frees it by then


class PendingRows(Protocol):
    def put(
        self, kind: str, session_id: str, client_id: str, verifier: str, expires_at: datetime
    ) -> None: ...

    def take(self, kind: str, session_id: str) -> Any: ...


class LeaseRows(Protocol):
    def acquire(
        self, client_id: str, job: str, holder: str, now: datetime, ttl: timedelta
    ) -> bool: ...

    def release(self, client_id: str, job: str, holder: str) -> None: ...


class SharedPendingAuthorizations:
    def __init__(self, rows: PendingRows, kind: str) -> None:
        self._rows = rows
        self._kind = kind

    def __repr__(self) -> str:
        return f"SharedPendingAuthorizations({self._kind})"

    def put(self, session_id: str, pending: PendingAuthorization) -> None:
        self._rows.put(
            self._kind, session_id, pending.client_id, pending.code_verifier, pending.expires_at
        )

    def take(self, session_id: str) -> PendingAuthorization | None:
        try:
            row = self._rows.take(self._kind, session_id)
        except Exception:  # noqa: BLE001 - an unreadable store is a consent that cannot be proven
            return None
        if row is None:
            return None
        return PendingAuthorization(row.client_id, row.verifier, row.expires_at)


class JobLeaseGuard:
    """Fail closed: when the database cannot say, the job is not started this tick (it is idempotent
    and comes round again)."""

    def __init__(
        self, rows: LeaseRows, *, holder: str | None = None, ttl: timedelta = DEFAULT_LEASE_TTL
    ) -> None:
        self._rows = rows
        self.holder = holder or uuid.uuid4().hex[:16]  # one per process
        self._ttl = ttl

    def __repr__(self) -> str:
        return "JobLeaseGuard()"

    def try_acquire(self, client_id: str, job: str, now: datetime) -> bool:
        try:
            return self._rows.acquire(client_id, job, self.holder, now, self._ttl)
        except Exception:  # noqa: BLE001
            return False

    def release(self, client_id: str, job: str) -> None:
        with contextlib.suppress(Exception):  # the lease expires by itself
            self._rows.release(client_id, job, self.holder)
