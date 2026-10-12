"""Where the last test result of each client lives: memory (tests) or one JSON file rewritten
atomically (the fallback when no database is configured; the SQL store is in
`sales_app.persistence`). One record per client: a new test replaces the previous one."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from sales_app.source_checks.models import SourceCheck, SourceCheckStoreCorruptError

FORMAT = 1


@runtime_checkable
class SourceCheckStore(Protocol):
    """Implementations are safe to share between threads."""

    def get(self, client_id: str) -> SourceCheck | None:
        """None: this client never tested its source."""
        ...

    def set(self, client_id: str, *, fingerprint: str, ok: bool, at: datetime) -> SourceCheck:
        """Record the outcome of a test and return it."""
        ...


class InMemorySourceCheckStore:
    def __init__(self) -> None:
        self._checks: dict[str, SourceCheck] = {}
        self._lock = threading.Lock()

    def get(self, client_id: str) -> SourceCheck | None:
        with self._lock:
            return self._checks.get(client_id)

    def set(self, client_id: str, *, fingerprint: str, ok: bool, at: datetime) -> SourceCheck:
        check = SourceCheck(client_id, fingerprint, ok, at)
        with self._lock:
            self._checks[client_id] = check
        return check


class FileSourceCheckStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def get(self, client_id: str) -> SourceCheck | None:
        with self._lock:
            return self._read().get(client_id)

    def set(self, client_id: str, *, fingerprint: str, ok: bool, at: datetime) -> SourceCheck:
        check = SourceCheck(client_id, fingerprint, ok, at)
        with self._lock:
            checks = self._read()
            checks[client_id] = check
            self._write(checks)
        return check

    def _read(self) -> dict[str, SourceCheck]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            return {
                item["client_id"]: SourceCheck(
                    client_id=item["client_id"],
                    fingerprint=str(item["fingerprint"]),
                    ok=_boolean(item["ok"]),
                    checked_at=datetime.fromisoformat(item["checked_at"]),
                )
                for item in raw["checks"]
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SourceCheckStoreCorruptError(
                f"{self._path.name}: unreadable ({error.__class__.__name__})"
            ) from error

    def _write(self, checks: dict[str, SourceCheck]) -> None:
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": FORMAT,
            "checks": [
                {
                    "client_id": c.client_id,
                    "fingerprint": c.fingerprint,
                    "ok": c.ok,
                    "checked_at": c.checked_at.isoformat(),
                }
                for c in sorted(checks.values(), key=lambda c: c.client_id)
            ],
        }
        temporary = directory / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("not a boolean")
    return value
