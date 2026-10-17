"""The LLM price list: migration 0007, the SQL and in-memory stores, validation, and cost maths."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect

from sales_app.llm_pricing import (
    InMemoryLLMPricingStore,
    InvalidPriceError,
    call_cost,
    summarise_costs,
    totals_by_currency,
    validate_price,
)
from sales_app.llm_usage import STATUS_ERROR, STATUS_OK, LLMUsageRecord
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.llm_pricing_rows import SqlLLMPricingStore
from sales_app.persistence.schema import current_revision
from sales_app.persistence.tables import metadata
from sales_app.sourcing.errors import StoreUnavailableError

AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def price(model="m", inp="2.5", out="10", currency="usd", provider="openai", by="root"):
    return validate_price(provider, model, inp, out, currency, updated_at=AT, updated_by=by)


def usage(
    model="m", status=STATUS_OK, inp: int | None = 1000, out: int | None = 500, provider="openai"
):
    return LLMUsageRecord(
        client_id="alpha",
        occurred_at=AT,
        provider=provider,
        model=model,
        status=status,
        input_tokens=inp,
        output_tokens=out,
    )


# ---- migration 0007 -----------------------------------------------------------------------------


def test_0007_adds_the_price_table_and_nothing_else():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0006")
    before = set(inspect(engine).get_table_names())
    assert "llm_pricing" not in before
    upgrade_database(engine, "0007")
    assert current_revision(engine) == "0007"
    after = set(inspect(engine).get_table_names())
    assert after - before == {"llm_pricing"}
    assert after - {"alembic_version"} < set(metadata.tables)
    assert inspect(engine).get_pk_constraint("llm_pricing")["constrained_columns"] == [
        "provider",
        "model",
    ]


# ---- validation ---------------------------------------------------------------------------------


def test_a_valid_price_is_normalised():
    kept = price(inp="2.5", currency=" usd ")
    assert (kept.input_per_million, kept.currency) == (Decimal("2.5"), "USD")


@pytest.mark.parametrize("bad", ["-1", "abc", "NaN", "Infinity", "1000001", True, None])
def test_a_bad_amount_is_refused(bad):
    with pytest.raises(InvalidPriceError):
        price(inp=bad)
    with pytest.raises(InvalidPriceError):
        price(out=bad)


@pytest.mark.parametrize(
    "kw",
    [{"currency": "dollars"}, {"currency": ""}, {"model": ""}, {"model": "a b"}, {"provider": "/"}],
)
def test_a_bad_identifier_or_currency_is_refused(kw):
    with pytest.raises(InvalidPriceError):
        price(**kw)


def test_a_free_model_is_a_real_zero_price():
    assert price(inp="0", out="0").input_per_million == 0


# ---- the stores ---------------------------------------------------------------------------------


@pytest.fixture(params=["sql", "memory"])
def store(request):
    if request.param == "memory":
        return InMemoryLLMPricingStore()
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return SqlLLMPricingStore(engine)


def test_a_price_is_kept_replaced_listed_in_order_and_deleted(store):
    store.set_price(price("b"))
    store.set_price(price("a", inp="1"))
    store.set_price(price("b", inp="3", by="other"))
    prices = store.list_prices()
    assert [p.model for p in prices] == ["a", "b"]
    b = prices[1]
    assert (b.input_per_million, b.updated_by, b.updated_at) == (Decimal(3), "other", AT)
    assert store.delete_price("openai", "a") is True
    assert store.delete_price("openai", "a") is False
    assert [p.model for p in store.list_prices()] == ["b"]


def test_decimals_survive_the_database_exactly():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    sql = SqlLLMPricingStore(engine)
    sql.set_price(price(inp="0.150000", out="0.600000"))
    [kept] = sql.list_prices()
    assert (kept.input_per_million, kept.output_per_million) == (Decimal("0.15"), Decimal("0.6"))


def test_an_unreachable_database_is_a_fixed_sentence():
    broken = SqlLLMPricingStore(create_database_engine("sqlite://"))  # no migration
    with pytest.raises(StoreUnavailableError):
        broken.list_prices()
    with pytest.raises(StoreUnavailableError):
        broken.set_price(price())
    with pytest.raises(StoreUnavailableError):
        broken.delete_price("openai", "m")


# ---- costs --------------------------------------------------------------------------------------


def test_a_cost_is_tokens_times_the_price_per_million():
    # 1000 in * 2.5/M + 500 out * 10/M = 0.0025 + 0.005
    assert call_cost(usage(), price()) == Decimal("0.0075")


def test_unknown_tokens_or_no_price_means_unknown_not_zero():
    assert call_cost(usage(inp=None), price()) is None
    assert call_cost(usage(out=None), price()) is None
    assert call_cost(usage(), None) is None


def test_zero_tokens_with_a_price_is_a_real_zero():
    assert call_cost(usage(inp=0, out=0), price()) == 0


def test_a_summary_prices_each_model_and_counts_what_it_could_not():
    records = [
        usage(),
        usage(),
        usage(status=STATUS_ERROR, inp=None, out=None),  # a failed call: no count, no cost
        usage("unpriced"),
        usage("zero-calls", inp=None, out=None),
    ]
    by_model = {line.model: line for line in summarise_costs(records, [price()])}
    priced = by_model["m"]
    assert (priced.calls, priced.costed_calls, priced.unknown_calls) == (3, 2, 1)
    assert priced.cost == Decimal("0.015") and priced.currency == "USD"
    assert (priced.input_tokens, priced.output_tokens) == (2000, 1000)
    unpriced = by_model["unpriced"]
    assert (unpriced.cost, unpriced.currency, unpriced.unknown_calls) == (None, None, 1)
    assert by_model["zero-calls"].cost is None  # tokens unknown, even though a price exists


def test_the_price_in_force_is_matched_by_provider_and_model():
    lines = summarise_costs([usage(provider="other")], [price()])
    assert lines[0].cost is None


def test_totals_are_per_currency_and_leave_out_unknown_costs():
    prices = [price("m", currency="USD"), price("n", currency="EUR")]
    lines = summarise_costs([usage("m"), usage("n"), usage("n"), usage("x")], prices)
    assert totals_by_currency(lines) == {"USD": Decimal("0.0075"), "EUR": Decimal("0.015")}
    assert totals_by_currency(summarise_costs([usage("x")], prices)) == {}
