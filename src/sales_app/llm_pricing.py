"""The LLM price list and what it makes of the usage register (`sales_app.llm_usage`).

The Admin keeps one price per provider and model: what a million input tokens and a million output
tokens cost, in one currency. A cost is computed only from tokens that were really recorded and a
price that is really configured. If either is missing the cost is **unknown** (None), never zero:
a call that cost nothing and a call whose cost we cannot tell are different things.

Prices are `Decimal`, never floats, so a total adds up exactly. The price list is global (it is
Revansa's, not a client's); the usage it is applied to is always one client's own register.
"""

import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sales_app.llm_usage import LLMUsageRecord

TOKENS_PER_PRICE = Decimal(1_000_000)
MAX_PRICE = Decimal(1000000)  # per million tokens: far beyond any real price, a typo guard
_PRICE_PLACES = Decimal("0.000001")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class InvalidPriceError(ValueError):
    """A price that may not be kept. The message is a fixed sentence, safe to show."""


@dataclass(frozen=True, slots=True)
class LLMPrice:
    provider: str
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    currency: str
    updated_at: datetime
    updated_by: str


def validate_price(
    provider: str,
    model: str,
    input_per_million: object,
    output_per_million: object,
    currency: str,
    *,
    updated_at: datetime,
    updated_by: str,
) -> LLMPrice:
    """A well-formed price, or `InvalidPriceError`."""
    if not _PROVIDER.match(provider):
        raise InvalidPriceError("the provider is not valid")
    if not _MODEL.match(model):
        raise InvalidPriceError("the model id is not valid")
    code = currency.strip().upper()
    if not _CURRENCY.match(code):
        raise InvalidPriceError("the currency must be a 3-letter code such as USD")
    return LLMPrice(
        provider=provider,
        model=model,
        input_per_million=_amount(input_per_million, "input price"),
        output_per_million=_amount(output_per_million, "output price"),
        currency=code,
        updated_at=updated_at,
        updated_by=updated_by,
    )


def _amount(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidPriceError(f"the {what} must be a number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidPriceError(f"the {what} must be a number") from None
    if not amount.is_finite() or amount < 0 or amount > MAX_PRICE:
        raise InvalidPriceError(f"the {what} must be between 0 and {MAX_PRICE}")
    return amount.quantize(_PRICE_PLACES)


class LLMPricingStore(Protocol):
    def list_prices(self) -> list[LLMPrice]:
        """Every price, by provider then model."""
        ...

    def set_price(self, price: LLMPrice) -> None:
        """Adds the price, or replaces the one held for that provider and model."""
        ...

    def delete_price(self, provider: str, model: str) -> bool:
        """False if there was no such price."""
        ...


class InMemoryLLMPricingStore:
    """For tests: nothing survives the process."""

    def __init__(self) -> None:
        self._prices: dict[tuple[str, str], LLMPrice] = {}
        self._lock = threading.Lock()

    def list_prices(self) -> list[LLMPrice]:
        with self._lock:
            return [self._prices[key] for key in sorted(self._prices)]

    def set_price(self, price: LLMPrice) -> None:
        with self._lock:
            self._prices[(price.provider, price.model)] = price

    def delete_price(self, provider: str, model: str) -> bool:
        with self._lock:
            return self._prices.pop((provider, model), None) is not None


# ---- costs ------------------------------------------------------------------------------------


def call_cost(record: LLMUsageRecord, price: LLMPrice | None) -> Decimal | None:
    """What one call cost; None unless both its tokens and a price are known."""
    if price is None or record.input_tokens is None or record.output_tokens is None:
        return None
    return (
        Decimal(record.input_tokens) * price.input_per_million
        + Decimal(record.output_tokens) * price.output_per_million
    ) / TOKENS_PER_PRICE


@dataclass(frozen=True, slots=True)
class ModelCost:
    """One provider's model, over a client's register."""

    provider: str
    model: str
    calls: int
    costed_calls: int  # calls whose cost is known
    input_tokens: int  # recorded tokens only: a call without a count adds nothing
    output_tokens: int
    cost: Decimal | None  # the sum over the costed calls; None when none can be costed
    currency: str | None  # of the price used; None when there is no price

    @property
    def unknown_calls(self) -> int:
        return self.calls - self.costed_calls


def summarise_costs(
    records: Iterable[LLMUsageRecord], prices: Iterable[LLMPrice]
) -> list[ModelCost]:
    """The register of one client, per provider and model, priced with the current price list."""
    by_model: Mapping[tuple[str, str], LLMPrice] = {(p.provider, p.model): p for p in prices}
    groups: dict[tuple[str, str], list[LLMUsageRecord]] = {}
    for record in records:
        groups.setdefault((record.provider, record.model), []).append(record)
    lines = []
    for (provider, model), calls in sorted(groups.items()):
        price = by_model.get((provider, model))
        costs = [c for c in (call_cost(r, price) for r in calls) if c is not None]
        lines.append(
            ModelCost(
                provider=provider,
                model=model,
                calls=len(calls),
                costed_calls=len(costs),
                input_tokens=sum(r.input_tokens or 0 for r in calls),
                output_tokens=sum(r.output_tokens or 0 for r in calls),
                cost=sum(costs, Decimal(0)) if costs else None,
                currency=price.currency if price is not None else None,
            )
        )
    return lines


def totals_by_currency(lines: Iterable[ModelCost]) -> dict[str, Decimal]:
    """Known costs added up per currency (currencies are never mixed)."""
    totals: dict[str, Decimal] = {}
    for line in lines:
        if line.cost is not None and line.currency is not None:
            totals[line.currency] = totals.get(line.currency, Decimal(0)) + line.cost
    return totals
