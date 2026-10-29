"""The agent's live activity: what it is doing at this very moment, and what it did a moment ago.

The Core tells its steps through `sales_core.ports.activity`; this module keeps them for the Client's
"Live" screen. A step is a kind (reading the prospect file, thinking about a message, sending it,
reading a reply...), the person it is about when there is one, and when it started and ended. Only
the step is kept, never a message, an address or a credential: the conversation itself is read from
the conversation, as always.

Keeping is best effort (the reporter's `doing` swallows a failure). A step still running after
`STALE_AFTER` is shown as `abandoned`: the process that began it is gone, and a screen that says the
agent is still reading a file since yesterday would lie. Steps are forgotten after `KEEP_FOR`.
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

RUNNING = "running"
DONE = "done"
FAILED = "failed"
ABANDONED = "abandoned"

STALE_AFTER = timedelta(minutes=10)
KEEP_FOR = timedelta(days=2)
MAX_KEPT = 500  # per client, for a store that has no clock-based pruning
DEFAULT_LIMIT = 80
MAX_LIMIT = 300


@dataclass(frozen=True, slots=True)
class ActivityStep:
    seq: int
    client_id: str
    kind: str
    state: str  # running | done | failed | abandoned
    started_at: datetime
    ended_at: datetime | None = None
    prospect_id: str | None = None
    name: str | None = None
    detail: str | None = None


def settled(step: ActivityStep, now: datetime) -> ActivityStep:
    """The step as it should be shown: a running one nobody finished is abandoned."""
    if step.state == RUNNING and now - step.started_at > STALE_AFTER:
        return ActivityStep(**{**_fields(step), "state": ABANDONED})
    return step


def _fields(step: ActivityStep) -> dict:
    return {name: getattr(step, name) for name in step.__slots__}


class ActivityStore(Protocol):
    """One client's steps."""

    def begin(
        self,
        kind: str,
        *,
        prospect_id: str | None,
        name: str | None,
        detail: str | None,
        at: datetime,
    ) -> int: ...

    def end(self, seq: int, *, ok: bool, at: datetime) -> None: ...

    def latest(self, limit: int) -> list[ActivityStep]:
        """The most recent steps, newest first."""
        ...


class InMemoryActivityStore:
    """For tests and for a deployment with no database: nothing survives the process."""

    def __init__(self, client_id: str) -> None:
        if not client_id:
            raise ValueError("an activity store needs a client id")
        self._client_id = client_id
        self._steps: dict[int, ActivityStep] = {}
        self._next = 1
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"InMemoryActivityStore(client={self._client_id!r})"

    def begin(
        self,
        kind: str,
        *,
        prospect_id: str | None,
        name: str | None,
        detail: str | None,
        at: datetime,
    ) -> int:
        with self._lock:
            seq = self._next
            self._next += 1
            self._steps[seq] = ActivityStep(
                seq=seq,
                client_id=self._client_id,
                kind=kind,
                state=RUNNING,
                started_at=at,
                prospect_id=prospect_id,
                name=name,
                detail=detail,
            )
            for old in sorted(self._steps)[: max(0, len(self._steps) - MAX_KEPT)]:
                del self._steps[old]
            return seq

    def end(self, seq: int, *, ok: bool, at: datetime) -> None:
        with self._lock:
            step = self._steps.get(seq)
            if step is not None:
                self._steps[seq] = ActivityStep(
                    **{**_fields(step), "state": DONE if ok else FAILED, "ended_at": at}
                )

    def latest(self, limit: int) -> list[ActivityStep]:
        with self._lock:
            return [self._steps[seq] for seq in sorted(self._steps, reverse=True)[:limit]]


class ActivityRecorder:
    """The `ActivityReporter` of one client: hands each step to its store."""

    def __init__(self, store: ActivityStore) -> None:
        self._store = store

    def __repr__(self) -> str:
        return "ActivityRecorder()"

    def begin(
        self,
        kind: str,
        *,
        prospect_id: str | None = None,
        name: str | None = None,
        detail: str | None = None,
    ) -> int | None:
        return self._store.begin(
            kind,
            prospect_id=prospect_id,
            name=_short(name, 120),
            detail=_short(detail, 200),
            at=datetime.now(UTC),
        )

    def end(self, step: int | None, *, ok: bool = True, detail: str | None = None) -> None:
        if step is not None:
            self._store.end(step, ok=ok, at=datetime.now(UTC))


def _short(text: str | None, limit: int) -> str | None:
    return text[:limit] if text else None
