"""Keeping and moving a client's e-mail activation request (rules in `config.email_activation`).

The request lives in the credential store beside the client's other settings, under a key that is
not in its catalog (as the channel policy does), so it needs no table. Every move is audited with the
new status as its fixed reason: who, when, which client; never the Admin's free-text reason, never a
mailbox.

Transitions are checked and written under one lock, so two requests or two Admin clicks cannot both
win. The lock is per process, which is how the application runs (an instance lock refuses a second).
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from sales_app.config.email_activation import (
    ACTIVE,
    IN_PROGRESS,
    MAX_REASON_LENGTH,
    OPEN_STATUSES,
    PENDING,
    REJECTED,
    ActivationRecord,
    format_record,
    parse_record,
)
from sales_app.config.secrets import EMAIL_ACTIVATION, EMAIL_CREDENTIALS_SHARED
from sales_app.secret_store import client_scope
from sales_app.secret_store.audit import SecretAudit, SecretAuditEvent
from sales_app.secret_store.port import SecretStore


class ActivationConflictError(ValueError):
    """The request is not in a state this move applies to. The message is a fixed sentence."""


class InvalidReasonError(ValueError):
    """A reason that is empty or too long. The message is a fixed sentence."""


class EmailActivationService:
    def __init__(
        self,
        store: SecretStore,
        audit: SecretAudit,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def get(self, client_id: str) -> ActivationRecord | None:
        return parse_record(self._store.get(client_scope(client_id), EMAIL_ACTIVATION))

    # ---- the client ----------------------------------------------------------------------

    def request(self, client_id: str, *, actor: str) -> ActivationRecord:
        """A new request. Refused while one is open or active; allowed after a rejection, which it
        replaces."""
        with self._lock:
            current = self.get(client_id)
            if current is not None and current.status in (*OPEN_STATUSES, ACTIVE):
                raise ActivationConflictError(
                    "your email is already active"
                    if current.status == ACTIVE
                    else "an activation request is already open"
                )
            now = self._now()
            return self._write(
                client_id, ActivationRecord(PENDING, now, now, actor, None), actor=actor
            )

    # ---- the Admin -----------------------------------------------------------------------

    def start(self, client_id: str, *, actor: str) -> ActivationRecord:
        with self._lock:
            current = self._require(client_id, (PENDING,), "only a pending request can be started")
            return self._move(client_id, current, IN_PROGRESS, None, actor)

    def reject(self, client_id: str, reason: str, *, actor: str) -> ActivationRecord:
        cleaned = _clean_reason(reason)
        with self._lock:
            current = self._require(
                client_id, OPEN_STATUSES, "only a pending or in-progress request can be rejected"
            )
            return self._move(client_id, current, REJECTED, cleaned, actor)

    def activate(self, client_id: str, *, actor: str) -> ActivationRecord:
        with self._lock:
            current = self._require(
                client_id, (IN_PROGRESS,), "start the request before activating it"
            )
            return self._move(client_id, current, ACTIVE, None, actor)

    def deactivate(self, client_id: str, reason: str, *, actor: str) -> ActivationRecord:
        """An active mailbox switched off by the Admin: the client sees it as rejected, with the
        reason, and may ask again."""
        cleaned = _clean_reason(reason)
        with self._lock:
            current = self._require(client_id, (ACTIVE,), "only an active request can be ended")
            moved = self._move(client_id, current, REJECTED, cleaned, actor)
            self._store.delete(client_scope(client_id), EMAIL_CREDENTIALS_SHARED)  # no mailbox left
            return moved

    # ---- whether the client may be shown its mailbox login -----------------------------------

    def credentials_shared(self, client_id: str) -> bool:
        """The Admin's decision to show this client the login of its mailbox. False unless set."""
        return self._store.get(client_scope(client_id), EMAIL_CREDENTIALS_SHARED) == "1"

    def set_credentials_shared(self, client_id: str, shared: bool, *, actor: str) -> bool:
        """Only an active mailbox can be shared (there is nothing to show before)."""
        with self._lock:
            self._require(client_id, (ACTIVE,), "only an active mailbox can be shared")
            scope = client_scope(client_id)
            if shared:
                self._store.set(scope, EMAIL_CREDENTIALS_SHARED, "1", by=actor)
            else:
                self._store.delete(scope, EMAIL_CREDENTIALS_SHARED)
            self._audit.record(
                SecretAuditEvent(
                    self._now(),
                    actor,
                    "set" if shared else "delete",
                    scope,
                    EMAIL_CREDENTIALS_SHARED,
                    "stored" if shared else "removed",
                    "credentials_shared" if shared else "credentials_kept_back",
                )
            )
            return shared

    # ---- internals -----------------------------------------------------------------------

    def _require(self, client_id: str, allowed: tuple[str, ...], message: str) -> ActivationRecord:
        current = self.get(client_id)
        if current is None:
            raise ActivationConflictError("this client has no activation request")
        if current.status not in allowed:
            raise ActivationConflictError(message)
        return current

    def _move(
        self,
        client_id: str,
        current: ActivationRecord,
        status: str,
        reason: str | None,
        actor: str,
    ) -> ActivationRecord:
        record = ActivationRecord(status, current.requested_at, self._now(), actor, reason)
        return self._write(client_id, record, actor=actor)

    def _write(self, client_id: str, record: ActivationRecord, *, actor: str) -> ActivationRecord:
        scope = client_scope(client_id)
        self._store.set(scope, EMAIL_ACTIVATION, format_record(record), by=actor)
        self._audit.record(
            SecretAuditEvent(
                self._now(), actor, "set", scope, EMAIL_ACTIVATION, "stored", record.status
            )
        )
        return record


def _clean_reason(reason: str) -> str:
    cleaned = " ".join(reason.split())
    if not cleaned:
        raise InvalidReasonError("a reason is required")
    if len(cleaned) > MAX_REASON_LENGTH:
        raise InvalidReasonError(f"the reason must be at most {MAX_REASON_LENGTH} characters")
    return cleaned
