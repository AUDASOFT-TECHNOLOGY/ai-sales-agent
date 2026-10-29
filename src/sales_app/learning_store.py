"""What the agent keeps in order to learn from its results, for one client.

Three small registers, all anonymous:

- **traces**: how each decision was taken (see `sales_core.learning.trace`): no text, no person. They
  are only needed until the conversation has its final result and has been reviewed, then they are
  deleted; a conversation that never ends is forgotten after `TRACE_KEEP`.
- **reviews**: the closed-vocabulary verdict on a finished conversation (codes, a result, a product).
  Kept for `REVIEW_KEEP`, the window the rules are drawn from.
- **notes**: the rules held today with the evidence behind them, and what the client decided.

`forget_case` removes everything that concerns one conversation (for a deletion request). Nothing
here holds a name, a contact, a message or a quotation.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

TRACE_KEEP = timedelta(days=60)
REVIEW_KEEP = timedelta(days=180)


@dataclass(frozen=True, slots=True)
class StoredTrace:
    case_ref: str
    action_ref: str
    at: datetime
    decision: str
    action_type: str
    channel: str | None = None
    length: int | None = None
    questions: int | None = None
    gap_hours: float | None = None
    unanswered: int = 0
    answers_reply: bool = False
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredReview:
    case_ref: str
    result: str
    ended_at: datetime
    reviewed_at: datetime
    causes: tuple[str, ...] = ()
    practices: tuple[str, ...] = ()
    confidence: str = "low"
    turning_step: int | None = None
    unknown: bool = False
    product: str | None = None
    applied_notes: tuple[str, ...] = ()
    steps_seen: int = 0


@dataclass(frozen=True, slots=True)
class StoredNote:
    note_id: str
    code: str
    kind: str
    theme: str
    state: str
    product: str | None = None
    evidence: str = "low"
    cases: int = 0
    against: int = 0
    reason: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    active_since: datetime | None = None
    off_by: str | None = None
    confirmed_by_client: bool = False


class LearningStore(Protocol):
    """One client's learning registers."""

    def record_trace(self, trace: StoredTrace) -> None:
        """Keep the trace of a decision. A decision traced twice (a retry) keeps the latest."""
        ...

    def traces_of(self, case_ref: str) -> list[StoredTrace]: ...

    def case_refs(self) -> list[str]:
        """Every conversation that has a trace."""
        ...

    def reviewed_refs(self) -> set[str]: ...

    def save_review(self, review: StoredReview) -> None:
        """Keep the verdict and delete the conversation's traces: they are no longer needed."""
        ...

    def reviews(self) -> list[StoredReview]: ...

    def notes(self) -> list[StoredNote]: ...

    def replace_notes(self, notes: list[StoredNote]) -> None: ...

    def set_note(self, note: StoredNote) -> None: ...

    def forget_case(self, case_ref: str) -> None: ...

    def purge(self, now: datetime) -> None:
        """Forget traces and reviews past their retention."""
        ...


class InMemoryLearningStore:
    """For tests and for a deployment with no database: nothing survives the process."""

    def __init__(self, client_id: str) -> None:
        if not client_id:
            raise ValueError("a learning store needs a client id")
        self._client_id = client_id
        self._traces: dict[str, StoredTrace] = {}
        self._reviews: dict[str, StoredReview] = {}
        self._notes: dict[str, StoredNote] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"InMemoryLearningStore(client={self._client_id!r})"

    def record_trace(self, trace: StoredTrace) -> None:
        with self._lock:
            self._traces[trace.action_ref] = trace

    def traces_of(self, case_ref: str) -> list[StoredTrace]:
        with self._lock:
            rows = [t for t in self._traces.values() if t.case_ref == case_ref]
        return sorted(rows, key=lambda t: t.at)

    def case_refs(self) -> list[str]:
        with self._lock:
            return sorted({t.case_ref for t in self._traces.values()})

    def reviewed_refs(self) -> set[str]:
        with self._lock:
            return set(self._reviews)

    def save_review(self, review: StoredReview) -> None:
        with self._lock:
            self._reviews[review.case_ref] = review
            for ref in [k for k, t in self._traces.items() if t.case_ref == review.case_ref]:
                del self._traces[ref]

    def reviews(self) -> list[StoredReview]:
        with self._lock:
            return list(self._reviews.values())

    def notes(self) -> list[StoredNote]:
        with self._lock:
            return list(self._notes.values())

    def replace_notes(self, notes: list[StoredNote]) -> None:
        with self._lock:
            self._notes = {n.note_id: n for n in notes}

    def set_note(self, note: StoredNote) -> None:
        with self._lock:
            self._notes[note.note_id] = note

    def forget_case(self, case_ref: str) -> None:
        with self._lock:
            self._reviews.pop(case_ref, None)
            for ref in [k for k, t in self._traces.items() if t.case_ref == case_ref]:
                del self._traces[ref]

    def purge(self, now: datetime) -> None:
        with self._lock:
            for ref in [k for k, t in self._traces.items() if now - t.at > TRACE_KEEP]:
                del self._traces[ref]
            for ref in [k for k, r in self._reviews.items() if now - r.ended_at > REVIEW_KEEP]:
                del self._reviews[ref]
