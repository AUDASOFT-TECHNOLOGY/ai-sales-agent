"""The credential store and its audit in the database.

Every value is encrypted by the same AES-256-GCM cipher as the file store, with the scope and the
key bound in as associated data, before it reaches a statement: the database only ever holds
ciphertext. The master key is the deployment's (`SALES_AGENT_SECRETS_KEY`), never stored here.
"""

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.tables import secret_audit, secrets
from sales_app.secret_store.audit import SecretAuditEvent
from sales_app.secret_store.cipher import SecretCipher
from sales_app.secret_store.errors import (
    DuplicateSecretError,
    InvalidMasterKeyError,
    SecretDecryptError,
    SecretStoreUnavailableError,
)
from sales_app.secret_store.port import SecretEntry

_FORMAT = 1
_UNAVAILABLE = "the credential store is unavailable"


def _context(scope: str, key: str) -> str:
    # The same binding as the file store, so a record moves between the two unchanged.
    return f"revansa-secret|v{_FORMAT}|{scope}|{key}"


def _entry(row) -> SecretEntry:
    return SecretEntry(row.scope, row.key, row.updated_at, row.updated_by)


class SqlSecretStore:
    def __init__(
        self, engine: Engine, cipher: SecretCipher, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self._engine = engine
        self._cipher = cipher
        self._now = now or (lambda: datetime.now(UTC))
        self._write_lock = threading.Lock()  # one writer at a time inside this process

    def count(self) -> int:
        return self._run(lambda c: c.execute(select(func.count()).select_from(secrets)).scalar())

    def verify(self) -> None:
        """Open every record. Raises InvalidMasterKeyError if the key does not match."""
        rows = self._run(lambda c: c.execute(select(secrets)).all())
        try:
            for row in rows:
                self._cipher.decrypt(row.value, _context(row.scope, row.key))
        except SecretDecryptError:
            raise InvalidMasterKeyError(
                "SALES_AGENT_SECRETS_KEY does not open the stored credentials "
                "(wrong key, or a record was altered)"
            ) from None

    def get(self, scope: str, key: str) -> str | None:
        row = self._one(scope, key)
        return None if row is None else self._cipher.decrypt(row.value, _context(scope, key))

    def entry(self, scope: str, key: str) -> SecretEntry | None:
        row = self._one(scope, key)
        return None if row is None else _entry(row)

    def entries(self, scope: str) -> Sequence[SecretEntry]:
        rows = self._run(
            lambda c: c.execute(
                select(secrets).where(secrets.c.scope == scope).order_by(secrets.c.key)
            ).all()
        )
        return tuple(_entry(r) for r in rows)

    def set(
        self, scope: str, key: str, value: str, *, by: str, unique: bool = False
    ) -> SecretEntry:
        sealed = self._cipher.encrypt(value, _context(scope, key))
        at = self._now()
        with self._write_lock:
            try:
                with self._engine.begin() as connection:
                    if unique:
                        others = connection.execute(
                            select(secrets).where(secrets.c.key == key, secrets.c.scope != scope)
                        ).all()
                        for other in others:
                            held = self._cipher.decrypt(other.value, _context(other.scope, key))
                            if held == value:
                                raise DuplicateSecretError(other.scope)
                    exists = connection.execute(
                        select(secrets.c.scope).where(
                            secrets.c.scope == scope, secrets.c.key == key
                        )
                    ).first()
                    values = {"value": sealed, "updated_at": at, "updated_by": by}
                    if exists:
                        connection.execute(
                            update(secrets)
                            .where(secrets.c.scope == scope, secrets.c.key == key)
                            .values(**values)
                        )
                    else:
                        connection.execute(insert(secrets).values(scope=scope, key=key, **values))
            except SQLAlchemyError:
                raise SecretStoreUnavailableError(_UNAVAILABLE) from None
        return SecretEntry(scope, key, at, by)

    def delete(self, scope: str, key: str) -> bool:
        with self._write_lock:
            try:
                with self._engine.begin() as connection:
                    result = connection.execute(
                        delete(secrets).where(secrets.c.scope == scope, secrets.c.key == key)
                    )
            except SQLAlchemyError:
                raise SecretStoreUnavailableError(_UNAVAILABLE) from None
        return bool(result.rowcount)

    # ---- internals ---------------------------------------------------------------------

    def _one(self, scope: str, key: str):
        return self._run(
            lambda c: c.execute(
                select(secrets).where(secrets.c.scope == scope, secrets.c.key == key)
            ).first()
        )

    def _run(self, work):
        try:
            with self._engine.connect() as connection:
                return work(connection)
        except SQLAlchemyError:
            raise SecretStoreUnavailableError(_UNAVAILABLE) from None


class SqlSecretAudit:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, event: SecretAuditEvent) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(secret_audit).values(
                    at=event.at,
                    actor=event.actor,
                    action=event.action,
                    scope=event.scope,
                    key=event.key,
                    outcome=event.outcome,
                    reason=event.reason,
                )
            )

    def recent(self, limit: int = 100) -> list[SecretAuditEvent]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(secret_audit).order_by(secret_audit.c.id.desc()).limit(limit)
            ).all()
        return [
            SecretAuditEvent(r.at, r.actor, r.action, r.scope, r.key, r.outcome, r.reason)
            for r in rows
        ]
