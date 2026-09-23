"""An in-memory ConfigStore for tests and for anything that needs a store without a disk."""

import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sales_app.config_store.models import (
    ConfigConflictError,
    ConfigVersion,
    check_client_id,
    freeze_document,
)


class InMemoryConfigStore:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._versions: dict[str, list[ConfigVersion]] = {}
        self._lock = threading.Lock()

    def current(self, client_id: str) -> ConfigVersion | None:
        history = self.history(client_id)
        return history[-1] if history else None

    def get(self, client_id: str, version: int) -> ConfigVersion | None:
        history = self.history(client_id)
        return history[version - 1] if 1 <= version <= len(history) else None

    def history(self, client_id: str) -> Sequence[ConfigVersion]:
        check_client_id(client_id)
        with self._lock:
            return tuple(self._versions.get(client_id, ()))

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
        freeze_document(document)
        with self._lock:
            versions = self._versions.setdefault(client_id, [])
            latest = versions[-1].version if versions else None
            if latest != expected_parent:
                raise ConfigConflictError(
                    f"{client_id}: latest version is {latest}, expected {expected_parent}"
                )
            record = ConfigVersion(
                client_id=client_id,
                version=(latest or 0) + 1,
                document=document,
                created_at=self._now(),
                author=author,
                note=note,
                parent=latest,
            )
            versions.append(record)
            return record
