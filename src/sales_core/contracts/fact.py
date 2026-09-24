"""What is known about a prospect, and how sure we are.

Generic on purpose: a fact is a free-form key with a text value. Which keys exist is a Brain's
business. Provenance is optional and meant for facts whose source matters (sensitive or
decisive ones), so ordinary conversational facts stay light.
"""

from dataclasses import dataclass
from datetime import datetime

from sales_core.contracts._validation import require_aware, require_text
from sales_core.contracts.enums import FactSource, FactStatus
from sales_core.contracts.ids import MessageId


@dataclass(frozen=True, slots=True, kw_only=True)
class FactEvidence:
    """Where an important fact comes from."""

    source: FactSource
    message_id: MessageId | None = None  # the message it was read from, when there is one
    excerpt: str | None = None  # the words it rests on
    observed_at: datetime | None = None
    recorded_by: str | None = None  # operator or component, opaque to the Core

    def __post_init__(self) -> None:
        if self.message_id is not None:
            require_text(self.message_id, "message_id")
        if self.excerpt is not None:
            require_text(self.excerpt, "excerpt")
        if self.recorded_by is not None:
            require_text(self.recorded_by, "recorded_by")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class Fact:
    """One thing known, discovered or suspected about a prospect."""

    key: str  # stable, free-form name chosen by the Brain
    value: str
    status: FactStatus
    confidence: float | None = None  # 0..1, mostly useful for hypotheses
    evidence: FactEvidence | None = None  # expected for important facts, optional otherwise
    recorded_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        require_text(self.key, "key")
        require_text(self.value, "value")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if (
            self.status is FactStatus.CONFIRMED
            and self.evidence is not None
            and self.evidence.source is FactSource.BRAIN_INFERENCE
        ):
            raise ValueError("a fact resting on a Brain inference cannot be CONFIRMED")
        require_aware(self.recorded_at, "recorded_at")
        require_aware(self.updated_at, "updated_at")
        if (
            self.recorded_at is not None
            and self.updated_at is not None
            and self.updated_at < self.recorded_at
        ):
            raise ValueError("updated_at cannot be before recorded_at")

    @property
    def is_confirmed(self) -> bool:
        return self.status is FactStatus.CONFIRMED
