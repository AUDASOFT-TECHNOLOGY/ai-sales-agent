"""Where the agent states live: memory (tests) or one JSON file rewritten atomically (the fallback
when no database is configured; the SQL store is in `sales_app.persistence`).

Few records, changed by a person's click, single-process deployment: the same reasoning as
`FileClientProfileStore`.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from sales_app.agent_state.models import AgentState, AgentStateStoreCorruptError

FORMAT = 1


@runtime_checkable
class AgentStateStore(Protocol):
    """Implementations are safe to share between threads."""

    def get(self, client_id: str) -> AgentState | None:
        """None: the client never started its agent."""
        ...

    def set(self, client_id: str, *, running: bool, by: str, at: datetime) -> AgentState:
        """Record the client's decision and return it."""
        ...


class InMemoryAgentStateStore:
    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}
        self._lock = threading.Lock()

    def get(self, client_id: str) -> AgentState | None:
        with self._lock:
            return self._states.get(client_id)

    def set(self, client_id: str, *, running: bool, by: str, at: datetime) -> AgentState:
        state = AgentState(client_id=client_id, running=running, changed_at=at, changed_by=by)
        with self._lock:
            self._states[client_id] = state
        return state


class FileAgentStateStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def get(self, client_id: str) -> AgentState | None:
        with self._lock:
            return self._read().get(client_id)

    def set(self, client_id: str, *, running: bool, by: str, at: datetime) -> AgentState:
        state = AgentState(client_id=client_id, running=running, changed_at=at, changed_by=by)
        with self._lock:
            states = self._read()
            states[client_id] = state
            self._write(states)
        return state

    def _read(self) -> dict[str, AgentState]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            return {
                item["client_id"]: AgentState(
                    client_id=item["client_id"],
                    running=_boolean(item["running"]),
                    changed_at=datetime.fromisoformat(item["changed_at"]),
                    changed_by=str(item["changed_by"]),
                )
                for item in raw["states"]
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AgentStateStoreCorruptError(
                f"{self._path.name}: unreadable ({error.__class__.__name__})"
            ) from error

    def _write(self, states: dict[str, AgentState]) -> None:
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": FORMAT,
            "states": [
                {
                    "client_id": s.client_id,
                    "running": s.running,
                    "changed_at": s.changed_at.isoformat(),
                    "changed_by": s.changed_by,
                }
                for s in sorted(states.values(), key=lambda s: s.client_id)
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
