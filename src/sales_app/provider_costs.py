"""Technical costs by provider (OpenAI, Meta/WhatsApp, Google, OVH, infra, other).

One general structure, `CostLine`: client (or none for a platform cost), provider, category, quantity,
unit, unit price, amount, currency, **basis** and source. The basis says how the figure is known and
is never blurred:

- `measured`: counted AND priced from data Revansa holds (LLM tokens recorded x the price list);
- `estimated`: a figure that is approximate or partial (a flat per-message tariff for WhatsApp, an
  amount the Admin typed in, a lower bound where some calls carry no token count);
- `unavailable`: no amount (a price the Admin did not provide, an unknown amount). Never a zero and
  never an invented price: with no tariff there is no amount.

Prices are `Decimal`. The Admin supplies the external prices (`CostRate`); none is built in. Amounts
of different currencies are never added together. `CostService.costs_by_provider(period, client_id)`
is what the dashboard reads; it computes the measured lines live from the LLM usage register and the
WhatsApp delivery register, and adds the cost lines the Admin entered (`provider_costs`). `materialize`
writes the computed lines into `provider_costs` for a period (idempotent) so they can be kept.
"""

import logging
import re
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sales_app.llm_pricing import TOKENS_PER_PRICE, LLMPricingStore

log = logging.getLogger(__name__)

OPENAI, META_WHATSAPP, GOOGLE, OVH, INFRA, OTHER = (
    "openai",
    "meta_whatsapp",
    "google",
    "ovh",
    "infra",
    "other",
)
PROVIDERS = (OPENAI, META_WHATSAPP, GOOGLE, OVH, INFRA, OTHER)

MEASURED, ESTIMATED, UNAVAILABLE = "measured", "estimated", "unavailable"
BASES = (MEASURED, ESTIMATED, UNAVAILABLE)

META_MESSAGE_KEY = "message_sent"  # the one tariff the engine reads: price of one sent message
COMPUTED_LLM = "computed:llm_usage"
COMPUTED_META = "computed:wa_deliveries"
COMPUTED_PREFIX = "computed:"
ADMIN_SOURCE = "admin"

MAX_PRICE = Decimal(1_000_000)
MAX_AMOUNT = Decimal(1_000_000_000)
_PLACES = Decimal("0.000001")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_WORD = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. /-]{0,31}$")
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?$")


class InvalidCostError(ValueError):
    """A period, price or cost line that may not be kept. Fixed sentence, safe to show."""


@dataclass(frozen=True, slots=True)
class Period:
    """A day or a calendar month (UTC). `end` is exclusive."""

    kind: str  # day | month
    start: date
    end: date

    @property
    def label(self) -> str:
        return self.start.isoformat() if self.kind == "day" else self.start.isoformat()[:7]

    @property
    def start_at(self) -> datetime:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC)

    @property
    def end_at(self) -> datetime:
        return datetime(self.end.year, self.end.month, self.end.day, tzinfo=UTC)


def parse_period(text: str) -> Period:
    """`YYYY-MM` (a month) or `YYYY-MM-DD` (a day)."""
    match = _PERIOD.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise InvalidCostError("the period must be YYYY-MM or YYYY-MM-DD")
    year, month, day = int(match[1]), int(match[2]), match[3]
    if day is not None:
        try:
            start = date(year, month, int(day))
        except ValueError:
            raise InvalidCostError("the period is not a real date") from None
        return Period("day", start, start + timedelta(days=1))
    return Period(
        "month",
        date(year, month, 1),
        date(year, month, monthrange(year, month)[1]) + timedelta(days=1),
    )


def month_of(day: date) -> Period:
    return parse_period(f"{day.year:04d}-{day.month:02d}")


# ---- the Admin's cost parameters -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostRate:
    provider: str
    key: str
    unit: str
    unit_price: Decimal
    currency: str
    updated_at: datetime
    updated_by: str


def _decimal(value: object, what: str, ceiling: Decimal) -> Decimal:
    if isinstance(value, bool):
        raise InvalidCostError(f"the {what} must be a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidCostError(f"the {what} must be a number") from None
    if not number.is_finite() or number < 0 or number > ceiling:
        raise InvalidCostError(f"the {what} must be between 0 and {ceiling}")
    return number.quantize(_PLACES)


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise InvalidCostError(f"the provider must be one of {', '.join(PROVIDERS)}")
    return provider


def _currency(currency: str) -> str:
    code = str(currency).strip().upper()
    if not _CURRENCY.match(code):
        raise InvalidCostError("the currency must be a 3-letter code such as USD")
    return code


def validate_rate(
    provider: str,
    key: str,
    unit: str,
    unit_price: object,
    currency: str,
    *,
    updated_at: datetime,
    updated_by: str,
) -> CostRate:
    if not _WORD.match(key):
        raise InvalidCostError("the key must be lower-case letters, digits, '_', '.' or '-'")
    if not _UNIT.match(unit):
        raise InvalidCostError("the unit is not valid")
    return CostRate(
        _provider(provider),
        key,
        unit,
        _decimal(unit_price, "unit price", MAX_PRICE),
        _currency(currency),
        updated_at,
        updated_by,
    )


# ---- cost lines ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostLine:
    """One cost: `amount` None means unavailable (and then `basis` is `unavailable`)."""

    client_id: str | None
    provider: str
    category: str
    period: Period
    quantity: Decimal | None
    unit: str
    unit_price: Decimal | None
    amount: Decimal | None
    currency: str | None
    basis: str
    source: str
    detail: str = ""  # e.g. the model of an LLM line; not stored
    id: int | None = None


def validate_entry(
    *,
    client_id: str | None,
    provider: str,
    category: str,
    period: str,
    quantity: object | None,
    unit: str,
    unit_price: object | None,
    amount: object | None,
    currency: str | None,
    basis: str,
    by: str,
) -> CostLine:
    """A cost line the Admin enters by hand. Its basis is `measured` (read off an invoice),
    `estimated`, or `unavailable` (no amount); the amount is the Admin's, never computed here unless
    quantity and unit price are both given and the amount is not."""
    if basis not in BASES:
        raise InvalidCostError(f"the basis must be one of {', '.join(BASES)}")
    if not _WORD.match(category):
        raise InvalidCostError("the category must be lower-case letters, digits, '_', '.' or '-'")
    if not _UNIT.match(unit):
        raise InvalidCostError("the unit is not valid")
    when = parse_period(period)
    qty = None if quantity is None else _decimal(quantity, "quantity", MAX_AMOUNT)
    price = None if unit_price is None else _decimal(unit_price, "unit price", MAX_PRICE)
    total = None if amount is None else _decimal(amount, "amount", MAX_AMOUNT)
    if total is None and qty is not None and price is not None:
        total = (qty * price).quantize(_PLACES)
    if basis == UNAVAILABLE:
        total, code = None, (_currency(currency) if currency else None)
    else:
        if total is None:
            raise InvalidCostError("an amount is needed unless the basis is unavailable")
        if not currency:
            raise InvalidCostError("a currency is needed with an amount")
        code = _currency(currency)
    return CostLine(
        client_id=client_id,
        provider=_provider(provider),
        category=category,
        period=when,
        quantity=qty,
        unit=unit,
        unit_price=price,
        amount=total,
        currency=code,
        basis=basis,
        source=ADMIN_SOURCE,
    )


@dataclass(frozen=True, slots=True)
class LLMUsageTotal:
    """A client's calls to one provider's model over a period. The token counts add up the calls
    that reported both (the costed ones); a call without a count is only counted in `calls`."""

    client_id: str
    provider: str
    model: str
    calls: int
    costed_calls: int
    input_tokens: int
    output_tokens: int


class CostStore(Protocol):
    def list_rates(self) -> list[CostRate]: ...

    def set_rate(self, rate: CostRate) -> None: ...

    def delete_rate(self, provider: str, key: str) -> bool: ...

    def llm_totals(self, period: Period, client_id: str | None) -> list[LLMUsageTotal]: ...

    def whatsapp_sent(self, period: Period, client_id: str | None) -> dict[str, int]:
        """Messages Meta accepted (status sent, delivered or read; not failed) per client."""
        ...

    def entries(self, period: Period, client_id: str | None) -> list[CostLine]:
        """The lines kept by the Admin (not the computed ones), dated inside the period. With a
        client id, only that client's (platform lines have none and are left out)."""
        ...

    def add_entry(self, line: CostLine, *, by: str, at: datetime) -> CostLine: ...

    def delete_entry(self, entry_id: int) -> bool:
        """Only a line the Admin entered (never a computed one)."""
        ...

    def replace_computed(
        self, period: Period, client_id: str | None, lines: list[CostLine], *, by: str, at: datetime
    ) -> int:
        """Replace the computed lines of the period (and client) by `lines`; returns how many."""
        ...


# ---- report --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderCosts:
    provider: str
    basis: str
    totals: dict[str, Decimal]  # known amounts per currency (a lower bound when not complete)
    lines: int
    unavailable_lines: int

    @property
    def complete(self) -> bool:
        return self.unavailable_lines == 0


@dataclass(frozen=True, slots=True)
class CostReport:
    period: Period
    client_id: str | None
    providers: list[ProviderCosts]
    lines: list[CostLine]
    totals: dict[str, Decimal]
    basis: str

    @property
    def complete(self) -> bool:
        return all(p.complete for p in self.providers)


def basis_of(lines: list[CostLine]) -> str:
    """measured when every line is measured; unavailable when no line has an amount; else estimated."""
    if not lines:
        return MEASURED
    if all(line.amount is None for line in lines):
        return UNAVAILABLE
    return MEASURED if all(line.basis == MEASURED for line in lines) else ESTIMATED


def _totals(lines: list[CostLine]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for line in lines:
        if line.amount is not None and line.currency is not None:
            out[line.currency] = out.get(line.currency, Decimal(0)) + line.amount
    return out


class CostService:
    def __init__(
        self,
        store: CostStore,
        pricing: LLMPricingStore | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._pricing = pricing
        self._now = now or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return "CostService()"

    # rates
    def list_rates(self) -> list[CostRate]:
        return self._store.list_rates()

    def set_rate(self, rate: CostRate) -> None:
        self._store.set_rate(rate)

    def delete_rate(self, provider: str, key: str) -> bool:
        return self._store.delete_rate(provider, key)

    # entries
    def add_entry(self, line: CostLine, *, by: str) -> CostLine:
        return self._store.add_entry(line, by=by, at=self._now())

    def delete_entry(self, entry_id: int) -> bool:
        return self._store.delete_entry(entry_id)

    # computation
    def computed_lines(self, period: Period, client_id: str | None = None) -> list[CostLine]:
        return [*self._llm_lines(period, client_id), *self._meta_lines(period, client_id)]

    def _llm_lines(self, period: Period, client_id: str | None) -> list[CostLine]:
        prices = {
            (p.provider, p.model): p for p in (self._pricing.list_prices() if self._pricing else [])
        }
        lines = []
        for total in self._store.llm_totals(period, client_id):
            price = prices.get((total.provider, total.model))
            quantity = Decimal(total.input_tokens + total.output_tokens)
            amount = currency = None
            basis = UNAVAILABLE
            if price is not None and total.costed_calls > 0:
                amount = (
                    Decimal(total.input_tokens) * price.input_per_million
                    + Decimal(total.output_tokens) * price.output_per_million
                ) / TOKENS_PER_PRICE
                currency = price.currency
                # Calls with no token count cannot be priced: the amount is then a lower bound.
                basis = MEASURED if total.costed_calls == total.calls else ESTIMATED
            elif price is not None:
                currency = price.currency
            lines.append(
                CostLine(
                    client_id=total.client_id,
                    provider=OPENAI if total.provider == OPENAI else OTHER,
                    category="llm_tokens",
                    period=period,
                    quantity=quantity,
                    unit="tokens",
                    unit_price=None,
                    amount=amount,
                    currency=currency,
                    basis=basis,
                    source=COMPUTED_LLM,
                    detail=f"{total.provider}/{total.model}",
                )
            )
        return lines

    def _meta_lines(self, period: Period, client_id: str | None) -> list[CostLine]:
        rate = next(
            (
                r
                for r in self._store.list_rates()
                if (r.provider, r.key) == (META_WHATSAPP, META_MESSAGE_KEY)
            ),
            None,
        )
        sent = self._store.whatsapp_sent(period, client_id)
        if not sent:
            sent = {client_id: 0} if client_id else {}
        if not sent:
            sent = {None: 0}  # type: ignore[dict-item]
        lines = []
        for owner, count in sorted(sent.items(), key=lambda item: item[0] or ""):
            quantity = Decimal(count)
            lines.append(
                CostLine(
                    client_id=owner,
                    provider=META_WHATSAPP,
                    category="messages_sent",
                    period=period,
                    quantity=quantity,
                    unit="message",
                    unit_price=rate.unit_price if rate else None,
                    amount=(quantity * rate.unit_price) if rate else None,
                    currency=rate.currency if rate else None,
                    # A flat tariff per message only approximates Meta's own pricing: estimated.
                    basis=ESTIMATED if rate else UNAVAILABLE,
                    source=COMPUTED_META,
                )
            )
        return lines

    def costs_by_provider(self, period: Period, client_id: str | None = None) -> CostReport:
        """Every provider's cost for the period (all clients, or one) with how it is known."""
        lines = [*self.computed_lines(period, client_id), *self._store.entries(period, client_id)]
        providers = []
        for name in PROVIDERS:
            mine = [line for line in lines if line.provider == name]
            if not mine and name != OPENAI:
                continue
            providers.append(
                ProviderCosts(
                    provider=name,
                    basis=basis_of(mine),
                    totals=_totals(mine),
                    lines=len(mine),
                    unavailable_lines=sum(1 for line in mine if line.amount is None),
                )
            )
        return CostReport(period, client_id, providers, lines, _totals(lines), basis_of(lines))

    def materialize(self, period: Period, client_id: str | None, *, by: str) -> int:
        """Keep the computed lines of the period in `provider_costs` (replacing the earlier ones)."""
        lines = [replace(line, id=None) for line in self.computed_lines(period, client_id)]
        return self._store.replace_computed(period, client_id, lines, by=by, at=self._now())
