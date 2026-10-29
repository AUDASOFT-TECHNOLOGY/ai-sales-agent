"""What the Core hands to a Brain that can reflect on finished conversations, and what it gets back.

A `LearningCase` is one finished conversation: its result, the anonymous steps the agent took and
what came after each, and (only while the review runs, never stored) the words exchanged. A
`CaseReview` is the Brain's closed-vocabulary verdict: labels from a list the Brain owns, a
confidence, and the step where things turned. Free text from a model never leaves a review.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from sales_core.contracts import LearnedNote
from sales_core.learning.results import Result


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseStep:
    """One decision of the agent and the reaction that followed it."""

    index: int
    at: datetime
    decision: str
    action_type: str
    channel: str | None
    length: int | None
    questions: int | None
    gap_hours: float | None
    unanswered: int
    answers_reply: bool
    labels: Mapping[str, str]
    reaction: Literal["replied", "no_reply", "not_sent", "pending"] = "pending"
    reply_after_hours: float | None = None
    reply_labels: Mapping[str, str] = field(default_factory=dict)  # the signals of the answer


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseText:
    """One line of the conversation, as it is read during a review only."""

    role: Literal["agent", "person"]
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LearningCase:
    case_ref: str
    result: Result
    ended_at: datetime
    steps: tuple[CaseStep, ...]
    product: str | None = None
    applied_notes: frozenset[str] = frozenset()
    # Present only for a review: the exchange, and the words to hide (the person's name).
    transcript: tuple[CaseText, ...] = ()
    hide: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseReview:
    """A Brain's verdict on one case, in a closed vocabulary. `unknown` is a valid answer."""

    case_ref: str
    result: Result
    ended_at: datetime
    reviewed_at: datetime
    causes: tuple[str, ...] = ()  # codes held responsible, for an unfavourable result
    practices: tuple[str, ...] = ()  # every code observed in the case, whatever the result
    confidence: Literal["low", "medium", "high"] = "low"
    turning_step: int | None = None
    unknown: bool = False
    product: str | None = None
    applied_notes: frozenset[str] = frozenset()
    steps_seen: int = 0


@runtime_checkable
class Reflector(Protocol):
    """Implemented by a Brain that can learn from results."""

    def achieved_stages(self) -> frozenset[str]:
        """The stages the client counts as the goal reached."""
        ...

    def review(self, case: LearningCase, *, now: datetime) -> CaseReview:
        """Judge one finished case. May call the model; raises like the model does."""
        ...

    def consolidate(
        self,
        reviews: Sequence[CaseReview],
        notes: Sequence[LearnedNote],
        *,
        now: datetime,
    ) -> Sequence[LearnedNote]:
        """The notes that hold today: from the retained reviews and the notes known so far. Pure:
        no model, no I/O."""
        ...
