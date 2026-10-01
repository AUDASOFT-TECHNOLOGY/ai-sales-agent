"""For tests: same behaviour, no filesystem and no encryption."""

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sales_app.secret_store.errors import DuplicateSecretError
from sales_app.secret_store.port import SecretEntry


class InMemorySecretStore:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._values: dict[tuple[str, str], tuple[str, SecretEntry]] = {}
        self._lock = threading.Lock()

    def get(self, scope: str, key: str) -> str | None:
        found = self._values.get((scope, key))
        return None if found is None else found[0]

    def entry(self, scope: str, key: str) -> SecretEntry | None:
        found = self._values.get((scope, key))
        return None if found is None else found[1]

    def entries(self, scope: str) -> Sequence[SecretEntry]:
        return tuple(
            sorted(
                (e for (s, _), (_, e) in self._values.items() if s == scope), key=lambda e: e.key
            )
        )

    def set(
        self, scope: str, key: str, value: str, *, by: str, unique: bool = False
    ) -> SecretEntry:
        with self._lock:
            if unique:
                for (other_scope, other_key), (held, _) in self._values.items():
                    if other_key == key and other_scope != scope and held == value:
                        raise DuplicateSecretError(other_scope)
            entry = SecretEntry(scope=scope, key=key, updated_at=self._now(), updated_by=by)
            self._values[(scope, key)] = (value, entry)
            return entry

    def delete(self, scope: str, key: str) -> bool:
        with self._lock:
            return self._values.pop((scope, key), None) is not None

    def __repr__(self) -> str:
        return f"InMemorySecretStore(<{len(self._values)} values redacted>)"
