"""The only thing the Admin API talks to: catalog rules, the store and the audit trail together.

Every write is audited, accepted or refused, and a status never contains a sensitive value.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sales_app.secret_store.audit import SecretAudit, SecretAuditEvent
from sales_app.secret_store.catalog import (
    CredentialSpec,
    ScopeKind,
    normalize_value,
    scope_kind,
    spec_for,
    specs_for,
)
from sales_app.secret_store.errors import (
    DuplicateSecretError,
    InvalidCredentialError,
    SecretStoreUnavailableError,
)
from sales_app.secret_store.port import SecretStore

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """What an admin may see about one credential. `value` is set only for non-sensitive ones."""

    key: str
    label: str
    sensitive: bool
    configured: bool
    value: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class CredentialService:
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
        self._on_change: Callable[[str, str], None] | None = None

    def set_on_change(self, callback: Callable[[str, str], None] | None) -> None:
        """Called with (scope, key) after a credential was stored or removed, so the runtime can
        pick the change up. It only schedules work; a failure here never undoes the save."""
        self._on_change = callback

    def describe(self, scope: str) -> list[CredentialStatus]:
        """Every credential this kind of scope can hold, set or not."""
        return [self._status(scope, spec) for spec in specs_for(scope_kind(scope))]

    def status(self, scope: str, key: str) -> CredentialStatus:
        return self._status(scope, spec_for(scope, key))

    def reveal(self, scope: str, key: str) -> str | None:
        """The stored value, for server-side use only (a connection test, a runtime). It must
        never be placed in an API response; `describe` and `status` are what an admin sees."""
        return self._store.get(scope, spec_for(scope, key).key)

    def save(
        self, scope: str, key: str, value: str, *, actor: str, apply: bool = True
    ) -> CredentialStatus:
        """Stores `value`. `apply=False` is for a value no running client reads (the record of
        a model test): the change is not handed to the runtime, so nothing is rebuilt."""
        spec = spec_for(scope, key)
        try:
            cleaned = normalize_value(spec, value)
        except InvalidCredentialError:
            self._log(actor, "set", scope, key, "rejected", "invalid_value")
            raise
        try:
            self._store.set(scope, key, cleaned, by=actor, unique=spec.unique)
        except DuplicateSecretError:
            self._log(actor, "set", scope, key, "rejected", "duplicate")
            raise
        except SecretStoreUnavailableError:
            self._log(actor, "set", scope, key, "rejected", "unavailable")
            raise
        self._log(actor, "set", scope, key, "stored")
        if apply:
            self._changed(scope, key)
        return self._status(scope, spec)

    def remove(self, scope: str, key: str, *, actor: str) -> CredentialStatus:
        spec = spec_for(scope, key)
        try:
            removed = self._store.delete(scope, key)
        except SecretStoreUnavailableError:
            self._log(actor, "delete", scope, key, "rejected", "unavailable")
            raise
        self._log(actor, "delete", scope, key, "removed" if removed else "not_found")
        if removed:
            self._changed(scope, key)
        return self._status(scope, spec)

    # ---- internals -----------------------------------------------------------------------

    def _status(self, scope: str, spec: CredentialSpec) -> CredentialStatus:
        entry = self._store.entry(scope, spec.key)
        if entry is None:
            return CredentialStatus(spec.key, spec.label, spec.sensitive, configured=False)
        return CredentialStatus(
            key=spec.key,
            label=spec.label,
            sensitive=spec.sensitive,
            configured=True,
            value=None if spec.sensitive else self._store.get(scope, spec.key),
            updated_at=entry.updated_at,
            updated_by=entry.updated_by,
        )

    def _changed(self, scope: str, key: str) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(scope, key)
        except Exception:
            log.exception("could not schedule applying a changed credential")

    def _log(
        self, actor: str, action: str, scope: str, key: str, outcome: str, reason: str | None = None
    ) -> None:
        self._audit.record(
            SecretAuditEvent(self._now(), actor, action, scope, key, outcome, reason)
        )


__all__ = ["CredentialService", "CredentialStatus", "ScopeKind"]
