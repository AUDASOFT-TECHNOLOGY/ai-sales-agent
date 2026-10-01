"""Who changed which credential, and when. Never the value, never anything derived from it.

An append-only JSON-lines file: one event per line, never rewritten. `reason` is a short fixed
code (`duplicate`, `invalid_value`, `unavailable`), not free text, so no value can slip into it.
"""

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SecretAuditEvent:
    at: datetime
    actor: str
    action: str  # "set" | "delete"
    scope: str
    key: str
    outcome: str  # "stored" | "removed" | "not_found" | "rejected"
    reason: str | None = None


@runtime_checkable
class SecretAudit(Protocol):
    def record(self, event: SecretAuditEvent) -> None: ...

    def recent(self, limit: int = 100) -> list[SecretAuditEvent]:
        """Most recent first."""
        ...


class FileSecretAudit:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def record(self, event: SecretAuditEvent) -> None:
        line = json.dumps({**asdict(event), "at": event.at.isoformat()}, sort_keys=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()

    def recent(self, limit: int = 100) -> list[SecretAuditEvent]:
        if not self._path.exists():
            return []
        events: list[SecretAuditEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            events.append(SecretAuditEvent(**{**data, "at": datetime.fromisoformat(data["at"])}))
        return list(reversed(events))[:limit]


class InMemorySecretAudit:
    def __init__(self) -> None:
        self._events: list[SecretAuditEvent] = []

    def record(self, event: SecretAuditEvent) -> None:
        self._events.append(event)

    def recent(self, limit: int = 100) -> list[SecretAuditEvent]:
        return list(reversed(self._events))[:limit]
