"""AI credits: what a client's model calls cost, and how much of its plan's credits they use up.

**The rule.** One credit is 0.0002 USD of real API cost. A call costs
`input tokens x input price + output tokens x output price` with the prices of *its own model*
(the Admin's price list, `sales_app.llm_pricing`, one price per provider and model, in USD); the
tokens are the ones the provider reported, input and output kept apart. With GPT Lune's 0.20 / 1.20
USD per million tokens, 1 000 input tokens are 1 credit and about 167 output tokens are 1 credit. A
model with no USD price is never priced with another model's: its calls are recorded without cost or
credits (and the Admin sees them as unpriced), they use up nothing.

**Exact arithmetic.** Costs are whole numbers of picodollars (a price per million tokens with six
decimals is a whole number of picodollars per token) and credits whole numbers of nanocredits
(1 credit = 1e9; 1 picodollar = 5 nanocredits). No float is ever added, so a sum has no rounding error.

**The allowance** is read from the client's current plan at each use: `daily_credits` a day,
`included_credits` a month (UTC calendar, nothing carries over). A new day or month is a new counter
row: that is the reset, no job needed. Changing plan changes the allowance at once; what was consumed
stays. A Free account has its one-time credits (`sales_app.free_accounts`) as a lifetime allowance. A
client with neither is not metered (consumption is still counted), which keeps clients that predate
plans working as they did.

**The gate.** Before a call, an estimate of its cost is *reserved* in the daily and monthly counters
by one conditional statement per counter (`consumed + held + estimate <= allowance`): the database
decides, so concurrent calls can never reserve more than what is left. A refused reservation raises
`LLMCreditsExhaustedError` and the model is not called. After the call the real cost replaces the
reservation, in one transaction with the ledger row (`op_llm_usage`), whose unique `(client, call id)`
makes a call settle once whatever retries surround it. A counter never goes above its allowance; the
ledger keeps the real credits. A reservation left by a process that died expires after `HOLD_TTL`.
Operations that use no model are never gated.
"""

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sales_app.free_accounts import FreeAccountStore
from sales_app.llm_pricing import LLMPrice, LLMPricingStore
from sales_app.llm_usage import LLMUsageRecord
from sales_app.plans import PlanStore, current_plan
from sales_app.send_cap import utc_day
from sales_core.contracts import LLMRequest
from sales_core.ports import Clock, LLMCreditsExhaustedError

log = logging.getLogger(__name__)

USD_PER_CREDIT = Decimal("0.0002")
PRICE_CURRENCY = "USD"
NANO = 10**9  # nanocredits in a credit
PICO_PER_CREDIT = 200_000_000  # USD_PER_CREDIT in picodollars
PRICE_UNITS = 10**6  # a price per million tokens, in whole millionths: picodollars per token
HOLD_TTL = timedelta(minutes=15)
DEFAULT_OUTPUT_ESTIMATE = 2048  # output tokens assumed when the request sets no limit
CHARS_PER_TOKEN = 3  # a cautious estimate of the input size, only used to reserve

EXHAUSTED = "your AI credits are used up for now: they renew automatically"
UNAVAILABLE = "your AI credits cannot be checked right now"


# ---- the arithmetic ---------------------------------------------------------------------------


def cost_pico_usd(input_tokens: int, output_tokens: int, price: LLMPrice) -> int:
    """The real cost of a call in picodollars, exact. Input and output are priced separately."""
    return input_tokens * _units(price.input_per_million) + output_tokens * _units(
        price.output_per_million
    )


def credits_nano(pico_usd: int) -> int:
    """Nanocredits for a cost: `cost / 0.0002 USD`, exact (1 picodollar is 5 nanocredits)."""
    return pico_usd * NANO // PICO_PER_CREDIT


def to_credits(nano: int) -> Decimal:
    return Decimal(nano) / NANO


def _units(price_per_million: Decimal) -> int:
    return int(price_per_million * PRICE_UNITS)


def day_period(day: date) -> str:
    return f"D:{day.isoformat()}"


def month_period(day: date) -> str:
    return f"M:{day.isoformat()[:7]}"


LIFETIME = "L"


# ---- what a client may spend ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Allowance:
    """In nanocredits. A None limit is no limit for that period."""

    monthly: int | None
    daily: int | None
    lifetime: int | None = None  # a Free account's one-time credits

    def periods(self, day: date) -> tuple[tuple[str, int | None], ...]:
        if self.lifetime is not None:
            return ((LIFETIME, self.lifetime),)
        return ((day_period(day), self.daily), (month_period(day), self.monthly))


@dataclass(frozen=True, slots=True)
class Hold:
    hold_id: str
    client_id: str
    periods: tuple[tuple[str, int | None], ...]  # (period, allowance) at the time of the call
    reserved: int  # nanocredits held; 0: nothing was reserved (not metered)
    provider: str = ""  # the model the client is configured with: what its calls are priced as
    model: str = ""


@dataclass(frozen=True, slots=True)
class CreditBalance:
    """A client's credits. Amounts are credits (exact decimals); None: no such limit."""

    monthly_allowance: Decimal | None
    monthly_consumed: Decimal
    daily_allowance: Decimal | None
    daily_consumed: Decimal
    metered: bool  # False: the client has no plan nor free credits, so nothing is limited

    @property
    def monthly_remaining(self) -> Decimal | None:
        return _remaining(self.monthly_allowance, self.monthly_consumed)

    @property
    def daily_remaining(self) -> Decimal | None:
        return _remaining(self.daily_allowance, self.daily_consumed)


def _remaining(allowance: Decimal | None, consumed: Decimal) -> Decimal | None:
    return None if allowance is None else max(allowance - consumed, Decimal(0))


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    call_id: str | None
    occurred_at: datetime
    provider: str
    model: str
    operation: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None  # None: not priced
    credits: Decimal | None


class CreditStore(Protocol):
    """The counters, holds and ledger of the credits. Every method is atomic."""

    def reserve(
        self,
        client_id: str,
        hold_id: str,
        amount: int,
        periods: tuple[tuple[str, int | None], ...],
        *,
        now: datetime,
        ttl: timedelta,
    ) -> bool:
        """Holds `amount` in every limited period if each still has room for it, else nothing."""
        ...

    def release(self, client_id: str, hold_id: str) -> None: ...

    def settle(
        self,
        hold: Hold,
        record: LLMUsageRecord,
    ) -> bool:
        """Writes the ledger row and counts `record.credits_nano` in the hold's periods, replacing
        the reservation. False: a call with this id was already settled, nothing changed."""
        ...

    def consumed(self, client_id: str, periods: tuple[str, ...]) -> dict[str, int]: ...

    def ledger(self, client_id: str, *, limit: int, offset: int) -> list[LLMUsageRecord]:
        """The client's model calls, newest first."""
        ...


class CreditService:
    def __init__(
        self,
        store: CreditStore,
        plans: PlanStore | None,
        prices: LLMPricingStore,
        free: FreeAccountStore | None,
        clock: Clock,
    ) -> None:
        self._store = store
        self._plans = plans
        self._prices = prices
        self._free = free
        self._clock = clock

    def __repr__(self) -> str:
        return "CreditService()"

    # ---- the allowance ------------------------------------------------------------------------

    def allowance(self, client_id: str) -> Allowance | None:
        """What the client's plan grants now; None when it has neither a plan nor free credits.
        Raises whatever the stores raise: the gate then refuses."""
        if self._plans is not None:
            plan = current_plan(self._plans, client_id)
            if plan is not None:
                return Allowance(plan.included_credits * NANO, plan.daily_credits * NANO)
        if self._free is not None:
            free = self._free.get(client_id)
            if free is not None:
                return Allowance(None, None, free.credits * NANO)
        return None

    def balance(self, client_id: str) -> CreditBalance:
        today = utc_day(self._clock.now())
        allowance = self.allowance(client_id)
        keys = (day_period(today), month_period(today), LIFETIME)
        used = self._store.consumed(client_id, keys)
        if allowance is not None and allowance.lifetime is not None:
            # A Free account's one-time credits are shown in the monthly slot: one figure.
            return CreditBalance(
                to_credits(allowance.lifetime),
                to_credits(used.get(LIFETIME, 0)),
                None,
                to_credits(used.get(day_period(today), 0)),
                True,
            )
        return CreditBalance(
            None if allowance is None else to_credits(allowance.monthly or 0),
            to_credits(used.get(month_period(today), 0)),
            None if allowance is None else to_credits(allowance.daily or 0),
            to_credits(used.get(day_period(today), 0)),
            allowance is not None,
        )

    def history(self, client_id: str, *, limit: int = 50, offset: int = 0) -> list[LedgerEntry]:
        return [
            LedgerEntry(
                call_id=r.call_id,
                occurred_at=r.occurred_at,
                provider=r.provider,
                model=r.model,
                operation=r.operation,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cost_usd=None if r.cost_pico_usd is None else Decimal(r.cost_pico_usd) / 10**12,
                credits=None if r.credits_nano is None else to_credits(r.credits_nano),
            )
            for r in self._store.ledger(client_id, limit=limit, offset=offset)
        ]

    # ---- the gate (the `CreditMeter` the recording wrapper uses) -------------------------------

    def begin(self, client_id: str, provider: str, model: str, request: LLMRequest) -> Hold:
        try:
            now = self._clock.now()
            day = utc_day(now)
            allowance = self.allowance(client_id)
            # No allowance: not limited, but the consumption is still counted.
            periods = (
                allowance.periods(day)
                if allowance is not None
                else ((day_period(day), None), (month_period(day), None))
            )
            hold_id = uuid.uuid4().hex
            if allowance is None:
                return Hold(hold_id, client_id, periods, 0, provider, model)
            price = self._price(provider, model)
            amount = max(self._estimate(request, price), 1)
            reserved = self._store.reserve(
                client_id, hold_id, amount, periods, now=now, ttl=HOLD_TTL
            )
        except Exception as error:  # noqa: BLE001 - credits that cannot be checked: no model call
            log.warning("credits unreadable (%s): %s", client_id, type(error).__name__)
            raise LLMCreditsExhaustedError(UNAVAILABLE) from None
        if not reserved:
            raise LLMCreditsExhaustedError(EXHAUSTED)
        return Hold(hold_id, client_id, periods, amount, provider, model)

    def cancel(self, hold: object) -> None:
        if isinstance(hold, Hold) and hold.reserved:
            self._store.release(hold.client_id, hold.hold_id)

    def settle(self, hold: object, record: LLMUsageRecord) -> None:
        assert isinstance(hold, Hold)
        self._store.settle(hold, self._priced(record, hold))

    # ---- pricing ------------------------------------------------------------------------------

    def _price(self, provider: str, model: str) -> LLMPrice | None:
        """The USD price of exactly this provider and model, or None. Never another model's."""
        for price in self._prices.list_prices():
            if (price.provider, price.model) == (provider, model) and (
                price.currency == PRICE_CURRENCY
            ):
                return price
        return None

    def _priced(self, record: LLMUsageRecord, hold: Hold) -> LLMUsageRecord:
        """The record with its real cost and credits, when its tokens and a USD price are known.
        The model the provider says it ran is priced first (a dated variant may have its own price),
        then the model the client is configured with; never any other."""
        if record.input_tokens is None or record.output_tokens is None:
            return record
        price = self._price(record.provider, record.model) or self._price(hold.provider, hold.model)
        if price is None:
            return record
        pico = cost_pico_usd(record.input_tokens, record.output_tokens, price)
        return LLMUsageRecord(
            **{**_fields(record), "cost_pico_usd": pico, "credits_nano": credits_nano(pico)}
        )

    @staticmethod
    def _estimate(request: LLMRequest, price: LLMPrice | None) -> int:
        """Nanocredits a call may cost at most, about: reserved before it runs. 0 without a price."""
        if price is None:
            return 0
        chars = sum(len(m.content) for m in request.messages)
        input_tokens = math.ceil(chars / CHARS_PER_TOKEN)
        output_tokens = request.max_output_tokens or DEFAULT_OUTPUT_ESTIMATE
        return credits_nano(cost_pico_usd(input_tokens, output_tokens, price))


def _fields(record: LLMUsageRecord) -> dict[str, object]:
    return {
        "client_id": record.client_id,
        "occurred_at": record.occurred_at,
        "provider": record.provider,
        "model": record.model,
        "status": record.status,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "call_id": record.call_id,
        "operation": record.operation,
    }
