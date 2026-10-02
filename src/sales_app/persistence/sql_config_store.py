"""Versioned client settings in SQL: the `ConfigStore` port.

Append-only like the file store: a version is never changed or removed (the migration installs
triggers that refuse it). The primary key `(client_id, version)` is what makes two concurrent
publishers unable to both claim the same number: the loser gets a `ConfigConflictError`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, func, insert, select
from sqlalchemy.exc import IntegrityError

from sales_app.config_store.models import (
    ConfigConflictError,
    ConfigCorruptError,
    ConfigVersion,
    check_client_id,
    document_checksum,
    freeze_document,
    thaw_document,
)
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import config_versions


def _version_from_row(row) -> ConfigVersion:
    try:
        document = row.document
        if not isinstance(document, dict):
            raise TypeError("document must be an object")
        if row.checksum != document_checksum(document):
            raise ValueError("checksum mismatch: the document was modified after publishing")
        return ConfigVersion(
            client_id=row.client_id,
            version=row.version,
            document=document,
            created_at=row.created_at,
            author=row.author,
            note=row.note,
            parent=row.parent,
        )
    except (ValueError, TypeError) as error:
        raise ConfigCorruptError(f"{row.client_id} v{row.version}: {error}") from error


def read_versions(connection: Connection, client_id: str) -> list[ConfigVersion]:
    """Every version, oldest first. Anything but 1..n without gaps is corruption."""
    rows = connection.execute(
        select(config_versions)
        .where(config_versions.c.client_id == client_id)
        .order_by(config_versions.c.version)
    ).all()
    versions = [_version_from_row(row) for row in rows]
    if [v.version for v in versions] != list(range(1, len(versions) + 1)):
        raise ConfigCorruptError(f"{client_id}: versions are not numbered 1..n")
    return versions


def insert_version(connection: Connection, record: ConfigVersion) -> None:
    connection.execute(
        insert(config_versions).values(
            client_id=record.client_id,
            version=record.version,
            parent=record.parent,
            document=thaw_document(record.document),
            checksum=document_checksum(record.document),
            created_at=record.created_at,
            author=record.author,
            note=record.note,
        )
    )


class SqlConfigStore:
    def __init__(self, engine: Engine, *, now: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))

    def current(self, client_id: str) -> ConfigVersion | None:
        history = self.history(client_id)
        return history[-1] if history else None

    def get(self, client_id: str, version: int) -> ConfigVersion | None:
        history = self.history(client_id)
        return history[version - 1] if 1 <= version <= len(history) else None

    def history(self, client_id: str) -> Sequence[ConfigVersion]:
        check_client_id(client_id)
        with self._engine.connect() as connection:
            return tuple(read_versions(connection, client_id))

    def publish(
        self,
        client_id: str,
        document: Mapping[str, Any],
        *,
        author: str,
        note: str = "",
        expected_parent: int | None,
    ) -> ConfigVersion:
        check_client_id(client_id)
        freeze_document(document)  # refuse anything that is not JSON before touching the database
        now = self._now()
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, client_id, created_at=now, created_by=author)
                latest = connection.execute(
                    select(func.max(config_versions.c.version)).where(
                        config_versions.c.client_id == client_id
                    )
                ).scalar()
                if latest is not None:
                    read_versions(connection, client_id)  # never append to an untrusted history
                if latest != expected_parent:
                    raise ConfigConflictError(
                        f"{client_id}: latest version is {latest}, expected {expected_parent}"
                    )
                record = ConfigVersion(
                    client_id=client_id,
                    version=(latest or 0) + 1,
                    document=document,
                    created_at=now,
                    author=author,
                    note=note,
                    parent=latest,
                )
                insert_version(connection, record)
                return record
        except IntegrityError as error:
            raise ConfigConflictError(f"{client_id}: published concurrently") from error
