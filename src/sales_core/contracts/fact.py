"""What is known about a prospect, and how sure we are.

Generic on purpose: a fact is a free-form key with a text value. Which keys exist is a Brain's
business. Provenance is optional and meant for facts whose source matters (sensitive or
decisive ones), so ordinary conversational facts stay light.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sales_core.contracts._validation import require_aware, require_text
from sales_core.contracts.enums import FactSource, FactStatus
from sales_core.contracts.ids import ClientId, MessageId, ProspectId


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


def _instant(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def fact_identity(client_id: ClientId, prospect_id: ProspectId, fact: Fact) -> str:
    """Stable identity of one recorded observation, the same in every storage backend.

    It is derived from everything that makes the observation what it is (whose fact, key, value,
    status, confidence, evidence and when it was recorded), never from the key alone: the same key
    may legitimately take another value later, and each value stays in the history. Submitting the
    very same fact again yields the same identity, which is what lets a store ignore a retry.
    `updated_at` is left out (it says when a record was touched, not what was observed).

    A retry must therefore carry the same `recorded_at`: stamp the fact once, before the first
    attempt to persist it. A fact without `recorded_at` has no identity and cannot be persisted.
    """
    if fact.recorded_at is None:
        raise ValueError(f"fact {fact.key!r} has no recorded_at; it cannot be persisted")
    evidence = fact.evidence
    parts = [
        client_id,
        prospect_id,
        fact.key,
        fact.value,
        str(fact.status),
        None if fact.confidence is None else repr(float(fact.confidence)),
        None
        if evidence is None
        else [
            str(evidence.source),
            evidence.message_id,
            evidence.excerpt,
            _instant(evidence.observed_at),
            evidence.recorded_by,
        ],
        _instant(fact.recorded_at),
    ]
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"fact-{digest[:24]}"
