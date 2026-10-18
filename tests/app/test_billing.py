"""Monthly invoices: periods, amounts, statuses, history, migration 0009 and the two stores."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from sales_app.billing import (
    InMemoryInvoiceStore,
    InvalidPeriodError,
    InvoiceFilter,
    InvoiceNotFoundError,
    InvoiceStatus,
    InvoiceStatusError,
    MixedCurrenciesError,
    build_invoice,
    parse_period,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.invoice_rows import SqlInvoiceStore
from sales_app.persistence.plan_rows import SqlPlanStore
from sales_app.persistence.schema import current_revision
from sales_app.persistence.tables import metadata
from sales_app.plans import InMemoryPlanStore, Plan, PlanAssignment
from sales_app.sourcing.errors import StoreUnavailableError

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def at(month, day, hour=0):
    return datetime(2026, month, day, hour, tzinfo=UTC)


def assignment(
    start,
    end=None,
    *,
    client="alpha",
    plan="pro",
    price="31",
    currency="USD",
    credits=1000,
    quota=50,
):
    return PlanAssignment(
        client_id=client,
        plan_id=plan,
        plan_name=plan.title(),
        monthly_price=Decimal(price),
        currency=currency,
        included_credits=credits,
        daily_quota=quota,
        started_at=start,
        assigned_by="root",
        ended_at=end,
    )


def build(assignments, period="2026-05", client="alpha", now=NOW):
    return build_invoice(client, period, assignments, now=now, created_by="root")


# ---- periods ------------------------------------------------------------------------------------


def test_a_period_is_a_calendar_month_in_utc():
    assert parse_period("2026-02") == (at(2, 1), at(3, 1))
    assert parse_period("2026-12") == (at(12, 1), datetime(2027, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "26-05", "2026/05", "2026-5", "", "May"])
def test_a_malformed_period_is_refused(bad):
    with pytest.raises(InvalidPeriodError):
        parse_period(bad)


def test_only_a_month_that_has_ended_can_be_invoiced():
    on_plan = [assignment(at(1, 1))]
    with pytest.raises(InvalidPeriodError):
        build(on_plan, period="2026-07")  # NOW is 2026-07-10: July is not over
    with pytest.raises(InvalidPeriodError):
        build(on_plan, period="2026-08")
    assert build(on_plan, period="2026-06") is not None
    just_after = datetime(2026, 8, 1, tzinfo=UTC)
    assert build(on_plan, period="2026-07", now=just_after) is not None  # exactly at month end


# ---- amounts ------------------------------------------------------------------------------------


def test_a_full_month_costs_exactly_the_monthly_price():
    invoice = build([assignment(at(1, 1), price="49.90")])
    assert invoice.amount == Decimal("49.90")
    [line] = invoice.lines
    assert (line.days, line.days_in_period, line.amount) == (31, 31, Decimal("49.90"))
    assert (invoice.status, invoice.currency, invoice.number) == (
        InvoiceStatus.PENDING,
        "USD",
        "INV-2026-05-alpha",
    )


def test_every_month_length_is_a_full_month():
    for period, days in (("2026-02", 28), ("2024-02", 29), ("2026-04", 30)):
        invoice = build([assignment(datetime(2023, 1, 1, tzinfo=UTC), price="30")], period=period)
        assert invoice.lines[0].days == invoice.lines[0].days_in_period == days
        assert invoice.amount == Decimal("30.00")


def test_joining_mid_month_is_prorated_from_the_day_it_started():
    # started on 21 May at any hour: the day belongs to the plan in force at its end -> 11 days
    invoice = build([assignment(at(5, 21, 14), price="31")])
    assert invoice.lines[0].days == 11
    assert invoice.amount == Decimal("11.00")


def test_leaving_mid_month_stops_at_the_day_before():
    # ended at 10 May 00:00: the 10th ends with no plan -> days 1..9
    invoice = build([assignment(at(1, 1), at(5, 10))])
    assert invoice.lines[0].days == 9


def test_a_plan_change_splits_the_month_into_lines_that_add_up():
    old = assignment(at(1, 1), at(5, 11), plan="basic", price="31", credits=100, quota=10)
    new = assignment(at(5, 11), plan="pro", price="62", credits=900, quota=90)
    invoice = build([new, old])
    assert [(l.plan_id, l.days, l.amount) for l in invoice.lines] == [
        ("basic", 10, Decimal("10.00")),
        ("pro", 21, Decimal("42.00")),
    ]
    assert invoice.amount == Decimal("52.00")
    assert (invoice.lines[0].included_credits, invoice.lines[1].daily_quota) == (100, 90)


def test_a_same_instant_handover_gives_each_day_to_one_plan_only():
    old = assignment(at(1, 1), at(5, 15, 12), plan="a", price="31")
    new = assignment(at(5, 15, 12), plan="b", price="31")
    invoice = build([old, new])
    assert sum(l.days for l in invoice.lines) == 31


def test_a_gap_without_a_plan_costs_nothing():
    first = assignment(at(5, 1), at(5, 6))  # days 1..5
    second = assignment(at(5, 21))  # days 21..31
    invoice = build([first, second])
    assert sum(l.days for l in invoice.lines) == 5 + 11
    assert invoice.amount == Decimal("5.00") + Decimal("11.00")


def test_rounding_is_to_the_cent_per_line_half_up():
    invoice = build([assignment(at(5, 1), at(5, 2), price="10")])  # 1 day of 31
    assert invoice.lines[0].amount == Decimal("0.32")  # 0.3225...
    # 0.14 / 28 = 0.005 exactly: half-up gives a cent where banker's rounding would give none
    february = build([assignment(at(2, 1), at(2, 2), price="0.14")], period="2026-02")
    assert february.lines[0].amount == Decimal("0.01")


def test_no_plan_day_means_no_invoice():
    assert build([]) is None
    assert build([assignment(at(7, 1))]) is None  # starts after May
    assert build([assignment(at(1, 1), at(5, 1))]) is None  # ended before May 1st ended
    assert build([assignment(at(1, 1))], client="beta") is None  # someone else's plan


def test_a_free_plan_gives_a_zero_invoice_not_no_invoice():
    invoice = build([assignment(at(1, 1), price="0")])
    assert invoice.amount == Decimal("0.00") and invoice.status is InvoiceStatus.PENDING


def test_mixed_currencies_are_not_added_up():
    usd = assignment(at(1, 1), at(5, 11), currency="USD")
    eur = assignment(at(5, 11), plan="eu", currency="EUR")
    with pytest.raises(MixedCurrenciesError):
        build([usd, eur])


def test_only_the_clients_own_assignments_count():
    mine = assignment(at(1, 1), price="31")
    theirs = assignment(at(1, 1), client="beta", price="1000")
    assert build([theirs, mine]).amount == Decimal("31.00")


# ---- the stores (one contract) ------------------------------------------------------------------


@pytest.fixture(params=["sql", "memory"])
def store(request):
    if request.param == "memory":
        return InMemoryInvoiceStore()
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return SqlInvoiceStore(engine)


def invoice(client="alpha", period="2026-05", price="31"):
    return build([assignment(at(1, 1), client=client, price=price)], period=period, client=client)


def test_an_invoice_is_kept_with_its_lines_and_an_id(store):
    kept = store.create(invoice())
    assert kept.invoice_id > 0
    assert store.get(kept.invoice_id) == kept
    assert store.get(kept.invoice_id).lines[0].days == 31
    assert store.get(999) is None


def test_a_second_invoice_for_the_same_client_and_period_is_refused_and_changes_nothing(store):
    first = store.create(invoice(price="31"))
    assert store.create(invoice(price="9999")) is None
    assert store.get(first.invoice_id).amount == Decimal("31.00")
    assert len(store.list_invoices(InvoiceFilter())) == 1


def test_the_same_period_for_other_clients_and_other_periods_are_separate(store):
    store.create(invoice("alpha", "2026-05"))
    assert store.create(invoice("beta", "2026-05")) is not None
    assert store.create(invoice("alpha", "2026-06")) is not None


def test_filters_by_client_period_range_and_status(store):
    a5 = store.create(invoice("alpha", "2026-05"))
    store.create(invoice("beta", "2026-05"))
    store.create(invoice("alpha", "2026-06"))
    store.settle(a5.invoice_id, InvoiceStatus.PAID, at=NOW, by="root")

    def ids(**kw):
        return [(i.client_id, i.period) for i in store.list_invoices(InvoiceFilter(**kw))]

    assert ids() == [("alpha", "2026-06"), ("alpha", "2026-05"), ("beta", "2026-05")]
    assert ids(client_id="alpha") == [("alpha", "2026-06"), ("alpha", "2026-05")]
    assert ids(period_from="2026-06") == [("alpha", "2026-06")]
    assert ids(period_to="2026-05") == [("alpha", "2026-05"), ("beta", "2026-05")]
    assert ids(period_from="2026-05", period_to="2026-05", client_id="beta") == [
        ("beta", "2026-05")
    ]
    assert ids(status=InvoiceStatus.PAID) == [("alpha", "2026-05")]
    assert ids(status=InvoiceStatus.PENDING) == [("alpha", "2026-06"), ("beta", "2026-05")]
    assert ids(client_ids={"beta"}) == [("beta", "2026-05")]
    assert ids(client_ids=set()) == []
    assert ids(client_ids={"beta"}, client_id="alpha") == []


def test_settling_marks_paid_or_for_free_once_with_who_and_when(store):
    paid = store.create(invoice("alpha"))
    free = store.create(invoice("beta"))
    done = store.settle(paid.invoice_id, InvoiceStatus.PAID, at=NOW, by="root")
    assert (done.status, done.status_changed_at, done.status_changed_by) == (
        InvoiceStatus.PAID,
        NOW,
        "root",
    )
    assert store.settle(free.invoice_id, InvoiceStatus.FOR_FREE, at=NOW, by="root").status is (
        InvoiceStatus.FOR_FREE
    )
    for target in (InvoiceStatus.PAID, InvoiceStatus.FOR_FREE, InvoiceStatus.PENDING):
        with pytest.raises(InvoiceStatusError):
            store.settle(paid.invoice_id, target, at=NOW + timedelta(days=1), by="other")
    again = store.get(paid.invoice_id)
    assert (again.status, again.status_changed_by, again.status_changed_at) == (
        InvoiceStatus.PAID,
        "root",
        NOW,
    )


def test_a_pending_invoice_cannot_be_settled_as_pending_and_a_missing_one_is_not_found(store):
    kept = store.create(invoice())
    with pytest.raises(InvoiceStatusError):
        store.settle(kept.invoice_id, InvoiceStatus.PENDING, at=NOW, by="root")
    assert store.get(kept.invoice_id).status is InvoiceStatus.PENDING
    with pytest.raises(InvoiceNotFoundError):
        store.settle(999, InvoiceStatus.PAID, at=NOW, by="root")
    with pytest.raises(InvoiceNotFoundError):
        store.settle(999, InvoiceStatus.PENDING, at=NOW, by="root")


# ---- history that does not move -----------------------------------------------------------------


def test_editing_a_plan_afterwards_changes_no_invoice_and_no_assignment_snapshot():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    plans, invoices = SqlPlanStore(engine), SqlInvoiceStore(engine)
    plans.create_plan(
        Plan("pro", "Pro", Decimal(31), "USD", 100, 10, True, at(1, 1), at(1, 1), "r")
    )
    plans.assign("alpha", "pro", at=at(1, 1), by="root")
    start, end = parse_period("2026-05")
    kept = invoices.create(build(plans.assignments_between(start, end)))

    plans.update_plan(
        Plan("pro", "Pro v2", Decimal(999), "EUR", 1, 1, False, at(1, 1), at(6, 1), "r")
    )
    plans.assign("alpha", None, at=at(6, 2), by="root")

    again = invoices.get(kept.invoice_id)
    assert again == kept
    assert (again.amount, again.currency, again.lines[0].plan_name) == (
        Decimal("31.00"),
        "USD",
        "Pro",
    )
    # and rebuilding the same month from what the plans now say gives the same figures
    rebuilt = build(plans.assignments_between(start, end))
    assert rebuilt.amount == kept.amount and rebuilt.lines == kept.lines


def test_assignments_between_returns_only_those_in_force_in_the_window():
    plans = InMemoryPlanStore()
    plans.create_plan(Plan("pro", "Pro", Decimal(1), "USD", 1, 1, True, at(1, 1), at(1, 1), "r"))
    plans.assign("alpha", "pro", at=at(1, 1), by="r")
    plans.assign("alpha", None, at=at(4, 1), by="r")
    plans.assign("beta", "pro", at=at(5, 20), by="r")
    plans.assign("gamma", "pro", at=at(6, 1), by="r")
    start, end = parse_period("2026-05")
    assert [a.client_id for a in plans.assignments_between(start, end)] == ["beta"]


# ---- migration 0009 -----------------------------------------------------------------------------


def test_0009_adds_the_invoice_tables_and_nothing_else():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0008")
    before = set(inspect(engine).get_table_names())
    upgrade_database(engine, "0009")
    assert current_revision(engine) == "0009"
    after = set(inspect(engine).get_table_names())
    assert after - before == {"invoices", "invoice_lines"}
    # later migrations (0010 and after) add tables that are modelled but not here yet
    assert after - {"alembic_version"} < set(metadata.tables)


def test_the_database_itself_refuses_a_second_invoice_for_a_client_and_period():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    SqlInvoiceStore(engine).create(invoice())
    with pytest.raises(Exception) as caught, engine.begin() as connection:
        connection.execute(
            text(
                "insert into invoices (client_id, period, currency, amount, status, created_at,"
                " created_by) values ('alpha', '2026-05', 'USD', 1, 'PENDING',"
                " '2026-07-10 00:00:00', 'x')"
            )
        )
    assert "UNIQUE" in str(caught.value).upper()


def test_an_unreachable_database_is_a_fixed_sentence():
    broken = SqlInvoiceStore(create_database_engine("sqlite://"))  # no migration
    for call in (
        lambda: broken.create(invoice()),
        lambda: broken.list_invoices(InvoiceFilter()),
        lambda: broken.get(1),
        lambda: broken.settle(1, InvoiceStatus.PAID, at=NOW, by="root"),
    ):
        with pytest.raises(StoreUnavailableError):
            call()
