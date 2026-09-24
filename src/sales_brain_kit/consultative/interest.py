"""A prospect's interest in catalog products: unknown, possible, probable or confirmed.

Interest is dynamic and per product, so a prospect may have none, one or several. It is read from
facts under the reserved key `interest` (the value names a catalog product by id, name or alias),
one observation per product, and from the lead's source columns when the catalog maps them
(`catalog.source_interest`). The fact's status decides the level, and nothing here changes it:

    confirmed fact  -> confirmed interest
    discovered fact -> probable interest
    hypothesis      -> possible interest (only if confident enough, and never asserted)
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sales_brain_kit.consultative.catalog import ProductCatalog
from sales_brain_kit.consultative.facts import INTEREST_KEY
from sales_core.contracts import Fact, FactStatus

_CELL_SEPARATORS = re.compile(r"[,;|\n]")


class InterestLevel(StrEnum):
    POSSIBLE = "possible"
    PROBABLE = "probable"
    CONFIRMED = "confirmed"

    @property
    def rank(self) -> int:
        return _RANKS[self]

    def at_least(self, other: "InterestLevel") -> bool:
        return self.rank >= other.rank


_RANKS = {InterestLevel.POSSIBLE: 1, InterestLevel.PROBABLE: 2, InterestLevel.CONFIRMED: 3}
_FROM_STATUS = {
    FactStatus.CONFIRMED: InterestLevel.CONFIRMED,
    FactStatus.DISCOVERED: InterestLevel.PROBABLE,
    FactStatus.HYPOTHESIS: InterestLevel.POSSIBLE,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductInterest:
    product_id: str
    level: InterestLevel
    basis: str  # "fact:confirmed", "fact:discovered", "fact:hypothesis" or "lead_data"


def derive_interests(
    catalog: ProductCatalog,
    facts: Sequence[Fact],
    attributes: Mapping[str, str],
    *,
    min_hypothesis_confidence: float,
) -> tuple[ProductInterest, ...]:
    """The strongest interest per product, strongest first (then in the order first seen)."""
    found: dict[str, ProductInterest] = {}

    def note(product_id: str, level: InterestLevel, basis: str) -> None:
        current = found.get(product_id)
        if current is None or level.rank > current.level.rank:
            found[product_id] = ProductInterest(product_id=product_id, level=level, basis=basis)

    for fact in facts:
        if fact.key != INTEREST_KEY:
            continue
        if fact.status is FactStatus.HYPOTHESIS and (
            fact.confidence is None or fact.confidence < min_hypothesis_confidence
        ):
            continue
        product = catalog.resolve(fact.value)
        if product is not None:
            note(product.id, _FROM_STATUS[fact.status], f"fact:{fact.status}")

    source = catalog.source_interest
    if source is not None:
        columns = {column.casefold() for column in source.columns}
        for name, cell in attributes.items():
            if name.casefold() not in columns or not cell.strip():
                continue
            for reference in (cell, *_CELL_SEPARATORS.split(cell)):
                product = catalog.resolve(reference, source.match_by)
                if product is not None:
                    note(product.id, InterestLevel(source.state), "lead_data")

    ordered = sorted(found.values(), key=lambda interest: -interest.level.rank)  # stable
    return tuple(ordered)
