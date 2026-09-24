"""What is known about a prospect, with how sure we are, as the Brain reads it.

The Core hands over every recorded observation, oldest first. This module turns them into one
current view per key without ever moving a fact from one status to another:

- the newest observation of a key is the current one, except that a hypothesis never replaces a
  fact that is confirmed or discovered (it may guide, it may not overrule);
- a hypothesis is kept only if the client allows hypotheses for that field and its confidence
  reaches the configured minimum;
- values the client already holds in the lead's source columns count as *discovered* (known, but
  not stated by the prospect), so the Brain does not ask for them again.

`interest` is multi-valued (one observation per product) and is read by `interest.py`.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sales_core.contracts import Fact, FactSource, FactStatus

INTEREST_KEY = "interest"  # reserved by the settings schema; the value names a catalog product
LANGUAGE_KEY = "language"  # a language the prospect prefers, or was seen to use


@dataclass(frozen=True, slots=True, kw_only=True)
class KnownFact:
    key: str
    value: str
    status: FactStatus
    confidence: float | None = None
    source: FactSource | None = None
    lead_data: bool = False  # taken from the lead's source columns, not from a recorded fact
    observed_at: datetime | None = None

    @property
    def settled(self) -> bool:
        """Known, as opposed to guessed."""
        return self.status is not FactStatus.HYPOTHESIS


class KnownFacts:
    """The current view of a prospect's facts, one entry per key."""

    __slots__ = ("_current",)

    def __init__(self, current: Iterable[KnownFact] = ()) -> None:
        self._current = {fact.key: fact for fact in current}

    def get(self, key: str) -> KnownFact | None:
        return self._current.get(key)

    def settled(self, key: str) -> KnownFact | None:
        """The fact if it is confirmed or discovered; a hypothesis does not count as known."""
        fact = self._current.get(key)
        return fact if fact is not None and fact.settled else None

    def with_status(self, status: FactStatus) -> tuple[KnownFact, ...]:
        return tuple(fact for fact in self._current.values() if fact.status is status)

    def __iter__(self) -> Iterator[KnownFact]:
        return iter(self._current.values())

    def __len__(self) -> int:
        return len(self._current)

    def __contains__(self, key: object) -> bool:
        return key in self._current


def known_facts(
    facts: Sequence[Fact],
    *,
    attributes: Mapping[str, str],
    columns_by_key: Mapping[str, Sequence[str]],
    min_hypothesis_confidence: float,
    no_hypothesis_for: frozenset[str] = frozenset(),
) -> KnownFacts:
    current: dict[str, KnownFact] = {}
    for fact in facts:
        if fact.key == INTEREST_KEY:
            continue
        existing = current.get(fact.key)
        if fact.status is FactStatus.HYPOTHESIS and existing is not None and existing.settled:
            continue
        current[fact.key] = KnownFact(
            key=fact.key,
            value=fact.value,
            status=fact.status,
            confidence=fact.confidence,
            source=fact.evidence.source if fact.evidence else None,
            observed_at=fact.updated_at or fact.recorded_at,
        )
    for key in [k for k, f in current.items() if not f.settled]:
        confidence = current[key].confidence
        if key in no_hypothesis_for or confidence is None or confidence < min_hypothesis_confidence:
            del current[key]

    folded = {name.casefold(): value.strip() for name, value in attributes.items() if value.strip()}
    for key, columns in columns_by_key.items():
        if key in current or key == INTEREST_KEY:
            continue
        value = next((folded[c.casefold()] for c in columns if c.casefold() in folded), None)
        if value is not None:
            current[key] = KnownFact(
                key=key,
                value=value,
                status=FactStatus.DISCOVERED,
                source=FactSource.CLIENT_DATA,
                lead_data=True,
            )
    return KnownFacts(current.values())
