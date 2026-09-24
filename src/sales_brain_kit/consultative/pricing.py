"""Which configured price applies to this prospect, and how it is written down.

A price is data the client wrote and is authoritative: nothing here converts a currency, adds a
tax, applies a discount or computes a total. For a product the most specific variant that applies
to the prospect's known place wins; without one, the product's own price applies. When the client
configured no applicable price the resolution says so and no price exists to be stated: the Brain
never has one to invent.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sales_brain_kit.consultative.applicability import Place, specificity
from sales_brain_kit.settings.catalog import PriceTerms, PriceVariant, Product


class PriceStatus(StrEnum):
    STATED = "stated"  # an amount is configured: it can be written with the price placeholder
    FREE = "free"
    ON_REQUEST = "on_request"  # the client shares it person to person
    NOT_CONFIGURED = "not_configured"  # the client configured no price for this product


@dataclass(frozen=True, slots=True, kw_only=True)
class PriceResolution:
    status: PriceStatus
    terms: PriceTerms | None = None
    variant_id: str | None = None  # None: the product's own price
    disclose: str = "after_interest"

    @property
    def currency(self) -> str | None:
        return self.terms.currency if self.terms is not None else None

    @property
    def text(self) -> str | None:
        """The amount as it is written to the prospect, or None when nothing is stated."""
        if self.status is not PriceStatus.STATED or self.terms is None:
            return None
        return format_price(self.terms)


def resolve_price(product: Product, place: Place) -> PriceResolution:
    pricing = product.pricing
    if pricing is None:
        return PriceResolution(status=PriceStatus.NOT_CONFIGURED)
    best: PriceVariant | None = None
    best_score = -1
    for variant in pricing.variants:
        score = specificity(variant.applies_to, place)
        if score is not None and score > best_score:  # ties keep the one declared first
            best, best_score = variant, score
    terms: PriceTerms = best if best is not None else pricing
    disclose = (best.disclose if best is not None else None) or pricing.disclose
    status = {
        "fixed": PriceStatus.STATED,
        "from": PriceStatus.STATED,
        "range": PriceStatus.STATED,
        "free": PriceStatus.FREE,
    }.get(terms.model, PriceStatus.ON_REQUEST)
    return PriceResolution(
        status=status,
        terms=terms,
        variant_id=best.id if best is not None else None,
        disclose=disclose,
    )


def format_number(value: float) -> str:
    """The number exactly as configured, with no float noise and no trailing zeros."""
    return format(Decimal(str(value)).normalize(), "f")


def format_price(terms: PriceTerms) -> str:
    """Language-neutral: `100 XXX`, `100+ XXX` (from) or `100–200 XXX` (a range)."""
    assert terms.amount is not None and terms.currency is not None
    amount = format_number(terms.amount)
    match terms.model:
        case "from":
            return f"{amount}+ {terms.currency}"
        case "range":
            assert terms.max_amount is not None
            return f"{amount}–{format_number(terms.max_amount)} {terms.currency}"
        case _:
            return f"{amount} {terms.currency}"
