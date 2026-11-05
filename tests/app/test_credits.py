"""AI credits: the real cost of a call in credits, the plan's allowance, the gate and its resets.

Real SQL (SQLite) under the real service: the arithmetic is exact (whole nanocredits), a new day or
month is a new counter, an exhausted client is refused before any model call, a call is settled
once whatever the retries, and concurrent calls never reserve more than what is left.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sales_app.credits import (
    NANO,
    CreditService,
    cost_pico_usd,
    credits_nano,
    to_credits,
)
from sales_app.free_accounts import FreeAccount, InMemoryFreeAccountStore
from sales_app.llm_pricing import validate_price
from sales_app.llm_usage import LLMUsageRecord, RecordingLLMProvider
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.credit_rows import SqlCreditStore
from sales_app.persistence.llm_pricing_rows import SqlLLMPricingStore
from sales_app.persistence.llm_usage_rows import SqlLLMUsageStore
from sales_app.persistence.plan_rows import SqlPlanStore
from sales_app.plans import Plan
from sales_core.contracts import LLMMessage, LLMRequest, LLMResponse, LLMRole
from sales_core.ports import LLMCreditsExhaustedError
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
LUNE = ("openai", "gpt-lune")
SOL = ("openai", "gpt-sol")


def price(key, input_price, output_price, currency="USD"):
    return validate_price(*key, input_price, output_price, currency, updated_at=NOW, updated_by="t")


def request(max_output_tokens=100, purpose="completion"):
    return LLMRequest(
        messages=(LLMMessage(role=LLMRole.USER, content="Hello"),),
        max_output_tokens=max_output_tokens,
        purpose=purpose,
    )


class FakeModel:
    """Answers with the tokens it is told to report; counts how often it was really called."""

    def __init__(self, tokens=(1000, 0), model="gpt-lune", fail=None):
        self.tokens, self.calls, self.model, self.fail = tokens, 0, model, fail

    def complete(self, _request):
        self.calls += 1
        if self.fail:
            raise self.fail
        return LLMResponse(
            text="ok", model=self.model, input_tokens=self.tokens[0], output_tokens=self.tokens[1]
        )


class World:
    def __init__(self, tmp_path, monthly=25_000, daily=800):
        self.engine = create_database_engine(f"sqlite:///{tmp_path / 'credits.db'}")
        upgrade_database(self.engine)
        self.clock = FakeClock(NOW)
        self.plans = SqlPlanStore(self.engine)
        self.prices = SqlLLMPricingStore(self.engine)
        self.free = InMemoryFreeAccountStore()
        self.store = SqlCreditStore(self.engine)
        self.service = CreditService(self.store, self.plans, self.prices, self.free, self.clock)
        self.prices.set_price(price(LUNE, "0.20", "1.20"))
        self.plan("starter", monthly, daily)
        self.plans.assign("alpha", "starter", at=NOW, by="root")

    def plan(self, plan_id, monthly, daily, quota=10):
        self.plans.create_plan(
            Plan(plan_id, plan_id, Decimal(59), "USD", monthly, daily, quota, True, NOW, NOW, "t")
        )

    def provider(self, model, client="alpha", key=LUNE):
        return RecordingLLMProvider(
            model,
            SqlLLMUsageStore(self.engine, client),
            client_id=client,
            provider=key[0],
            model=key[1],
            now=self.clock.now,
            credits=self.service,
        )

    def balance(self, client="alpha"):
        return self.service.balance(client)

    def close(self):
        self.engine.dispose()


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.close()


# ---- the arithmetic -------------------------------------------------------------------------------


def credits_of(tokens_in, tokens_out, input_price="0.20", output_price="1.20"):
    p = price(LUNE, input_price, output_price)
    return to_credits(credits_nano(cost_pico_usd(tokens_in, tokens_out, p)))


def test_the_reference_examples_of_the_commercial_rule():
    assert credits_of(1000, 0) == 1  # 1 000 input tokens = 1 credit
    assert credits_of(0, 1_000_000) == 6000
    assert credits_of(1_000_000, 1_000_000) == 7000  # 1M in + 1M out
    assert round(credits_of(0, 167), 2) == Decimal("1.00")  # about 167 output tokens = 1 credit


def test_input_and_output_are_priced_apart_with_no_rounding_error():
    assert credits_of(1, 0) == Decimal("0.001")
    assert credits_of(0, 1) == Decimal("0.006")
    many = sum((credits_of(7, 3) for _ in range(1000)), Decimal(0))
    assert many == credits_of(7000, 3000) == Decimal(25)  # a sum of calls is the call of the sum


# ---- the allowance and the gate -------------------------------------------------------------------


def test_a_client_gets_its_plans_allowance_and_a_call_uses_its_real_cost(world):
    before = world.balance()
    assert (before.monthly_allowance, before.daily_allowance) == (25_000, 800)
    assert (before.monthly_consumed, before.daily_consumed) == (0, 0)
    model = FakeModel(tokens=(2000, 334))
    world.provider(model).complete(request(purpose="commercial_message"))
    after = world.balance()
    # 2 000 in = 2 credits; 334 out = 2.004 credits
    assert after.monthly_consumed == after.daily_consumed == Decimal("4.004")
    assert after.monthly_remaining == Decimal("24995.996") and after.daily_remaining == Decimal(
        "795.996"
    )
    [entry] = world.service.history("alpha")
    assert (entry.input_tokens, entry.output_tokens) == (2000, 334)
    assert entry.credits == Decimal("4.004") and entry.cost_usd == Decimal("0.0008008")
    assert (entry.provider, entry.model, entry.operation) == (
        "openai",
        "gpt-lune",
        "commercial_message",
    )
    assert entry.call_id and entry.occurred_at == NOW


def test_an_exhausted_day_blocks_the_next_model_call_before_it_is_made(tmp_path):
    w = World(tmp_path, monthly=10_000, daily=3)
    try:
        model = FakeModel(tokens=(2000, 0))
        provider = w.provider(model)
        provider.complete(request())  # 2 of 3 credits
        with pytest.raises(LLMCreditsExhaustedError):
            provider.complete(request(max_output_tokens=2000))  # may cost more than what is left
        assert model.calls == 1  # the model was not called
        assert w.balance().daily_consumed == 2
    finally:
        w.close()


def test_the_counters_never_go_above_the_allowance(tmp_path):
    w = World(tmp_path, monthly=10_000, daily=5)
    try:
        # the estimate fits (hello + 100 tokens) but the model really answers with a lot more
        w.provider(FakeModel(tokens=(50_000, 0))).complete(request())
        used = w.balance()
        assert used.daily_consumed == 5 and used.daily_remaining == 0  # capped at the allowance
        [entry] = w.service.history("alpha")
        assert entry.credits == 50  # the ledger keeps what the call really cost
        with pytest.raises(LLMCreditsExhaustedError):
            w.provider(FakeModel()).complete(request())
    finally:
        w.close()


def test_the_day_and_the_month_reset_on_their_own_and_nothing_carries_over(tmp_path):
    w = World(tmp_path, monthly=10, daily=4)
    try:
        for _ in range(4):
            w.provider(FakeModel(tokens=(1000, 0))).complete(request())
        with pytest.raises(LLMCreditsExhaustedError):  # the 4 credits of the day are used
            w.provider(FakeModel()).complete(request())
        w.clock.advance(timedelta(days=1))  # a new UTC day: a new daily counter
        assert w.balance().daily_consumed == 0 and w.balance().monthly_consumed == 4
        for _ in range(4):
            w.provider(FakeModel(tokens=(1000, 0))).complete(request())
        w.clock.advance(timedelta(days=1))
        w.provider(FakeModel(tokens=(1000, 0))).complete(request())
        w.provider(FakeModel(tokens=(1000, 0))).complete(request())  # 10 of 10 this month
        w.clock.advance(timedelta(days=1))
        with pytest.raises(LLMCreditsExhaustedError):  # the day is fresh, the month is not
            w.provider(FakeModel()).complete(request())
        w.clock.advance(timedelta(days=30))  # December: unused credits are not carried over
        assert w.balance().monthly_consumed == 0 and w.balance().monthly_remaining == 10
        w.provider(FakeModel(tokens=(1000, 0))).complete(request())
        assert w.balance().monthly_consumed == 1
    finally:
        w.close()


def test_changing_plan_applies_the_new_allowance_and_keeps_the_history(world):
    world.provider(FakeModel(tokens=(3000, 0))).complete(request())
    world.plan("business", 75_000, 2_500, quota=30)
    world.plans.assign("alpha", "business", at=NOW, by="root")
    moved = world.balance()
    assert (moved.monthly_allowance, moved.daily_allowance) == (75_000, 2_500)
    assert moved.monthly_consumed == 3 and len(world.service.history("alpha")) == 1


def test_an_unpriced_model_is_recorded_but_never_priced_with_another_models_tariff(world):
    sol = FakeModel(tokens=(1_000_000, 1_000_000), model="gpt-sol")
    world.provider(sol, key=SOL).complete(request())  # only GPT Lune has a price
    [entry] = world.service.history("alpha")
    assert entry.credits is None and entry.cost_usd is None
    assert world.balance().monthly_consumed == 0
    world.prices.set_price(price(SOL, "5", "15"))  # its own tariff, set by the Admin
    world.provider(FakeModel(tokens=(1000, 0), model="gpt-sol"), key=SOL).complete(request())
    assert world.balance().monthly_consumed == 25  # 1 000 tokens x 5 USD / 1M = 0.005 USD
    world.prices.set_price(price(LUNE, "0.20", "1.20", currency="EUR"))
    world.provider(FakeModel(tokens=(1000, 0)), key=LUNE).complete(request())
    assert world.balance().monthly_consumed == 25  # no USD price: not priced


def test_a_client_with_no_plan_is_not_limited_and_a_free_account_has_its_own_credits(world):
    model = FakeModel(tokens=(1000, 0))
    world.provider(model, client="legacy").complete(request())
    legacy = world.balance("legacy")
    assert legacy.metered is False and legacy.monthly_allowance is None
    assert legacy.monthly_consumed == 1  # still counted
    world.free.add(FreeAccount("newbie", 2, NOW))
    for _ in range(2):
        world.provider(FakeModel(tokens=(1000, 0)), client="newbie").complete(request())
    with pytest.raises(LLMCreditsExhaustedError):
        world.provider(FakeModel(), client="newbie").complete(request())
    assert world.balance("newbie").monthly_allowance == 2


def test_a_failed_call_costs_nothing_and_gives_its_reservation_back(tmp_path):
    w = World(tmp_path, monthly=10_000, daily=100)
    try:
        failing = FakeModel(fail=TimeoutError("slow"))
        for _ in range(30):  # reservations are not leaked: far more than the day could hold
            with pytest.raises(TimeoutError):
                w.provider(failing).complete(request(max_output_tokens=4000))
        assert w.balance().daily_consumed == 0
        w.provider(FakeModel(tokens=(1000, 0))).complete(request(max_output_tokens=4000))
    finally:
        w.close()


# ---- never twice, never beyond ---------------------------------------------------------------------


def test_one_call_is_settled_once_whatever_the_retries(world):
    hold = world.service.begin("alpha", *LUNE, request())
    record = LLMUsageRecord(
        "alpha", NOW, *LUNE, "ok", 1000, 0, call_id="req_1", operation="completion"
    )
    priced = world.service._priced(record, hold)
    assert world.store.settle(hold, priced) is True
    again = world.service.begin("alpha", *LUNE, request())
    assert world.store.settle(again, priced) is False  # the same provider call, seen again
    assert world.balance().monthly_consumed == 1 and len(world.service.history("alpha")) == 1
    # and the reservation of the second attempt was given back, not leaked
    assert world.store.consumed("alpha", ("D:2026-11-10",))["D:2026-11-10"] == NANO


def test_concurrent_calls_never_use_more_than_what_is_left(tmp_path):
    w = World(tmp_path, monthly=10_000, daily=10)
    try:
        # each call reserves about 6 credits at most (4 000 output tokens x 1.2/M = 4.8 credits...)
        def call(_):
            try:
                w.provider(FakeModel(tokens=(1000, 0))).complete(request(max_output_tokens=1500))
                return True
            except LLMCreditsExhaustedError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(call, range(40)))
        used = w.balance().daily_consumed
        assert 1 <= sum(outcomes) <= 10 and used <= 10  # 1 credit each, the day holds 10
        assert used == sum(outcomes)
        held = w.engine.connect().exec_driver_sql("select sum(held_nano) from op_credit_counters")
        assert held.scalar() == 0  # nothing is left reserved
    finally:
        w.close()


def test_a_reservation_left_by_a_dead_process_expires(tmp_path):
    w = World(tmp_path, monthly=10_000, daily=3)
    try:
        hold = w.service.begin("alpha", *LUNE, request(max_output_tokens=400))  # never settled
        assert hold.reserved > 0
        with pytest.raises(LLMCreditsExhaustedError):
            w.service.begin("alpha", *LUNE, request(max_output_tokens=400))
        w.clock.advance(timedelta(hours=1))
        w.service.begin("alpha", *LUNE, request(max_output_tokens=400))  # the old one was swept
    finally:
        w.close()
