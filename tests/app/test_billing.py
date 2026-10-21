"""Invoices: cycles, fixed amounts, statuses, history, migration 0009/0026 and the two stores."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
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
    add_months,
    build_invoice,
    cycle_dates,
    due_at_of,
    next_cycle_date,
    parse_period,
    parse_reference,
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


def build(a=None, cycle=date(2026, 5, 20), now=NOW):
    return build_invoice(a or assignment(at(5, 20)), cycle, now=now, created_by="root")


# ---- periods ------------------------------------------------------------------------------------


def test_a_period_is_a_cycle_day_or_a_legacy_calendar_month_in_utc():
    assert parse_period("2026-10-20") == (at(10, 20), at(10, 21))
    assert parse_period("2026-02") == (at(2, 1), at(3, 1))
    assert parse_period("2026-12") == (at(12, 1), datetime(2027, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    "bad", ["2026-13", "2026-00", "26-05", "2026/05", "2026-5", "", "May", "2026-02-30", "2026-05-1"]
)
def test_a_malformed_period_is_refused(bad):
    with pytest.raises(InvalidPeriodError):
        parse_period(bad)


def test_the_reference_of_an_invoice_reads_back_in_both_forms():
    assert parse_reference("INV-2026-10-20-alpha") == ("alpha", "2026-10-20")
    assert parse_reference("INV-2026-05-alpha") == ("alpha", "2026-05")
    assert parse_reference("INV-2026-05-alpha-2") == ("alpha-2", "2026-05")
    assert parse_reference("nonsense") is None


# ---- cycles -------------------------------------------------------------------------------------


def test_the_anchor_day_is_kept_and_clamped_to_the_end_of_short_months():
    anchor = date(2026, 1, 31)
    assert [add_months(anchor, n) for n in range(5)] == [
        date(2026, 1, 31),
        date(2026, 2, 28),  # clamped, but...
        date(2026, 3, 31),  # ...the original anchor is kept
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]
    assert add_months(date(2024, 1, 30), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 20), 2) == date(2027, 1, 20)


def test_the_first_cycle_is_the_assignment_day_then_the_same_day_each_month():
    start = datetime(2026, 10, 20, 15, tzinfo=UTC)
    assert cycle_dates(start, None, datetime(2026, 10, 20, 15, tzinfo=UTC)) == [date(2026, 10, 20)]
    assert cycle_dates(start, None, datetime(2026, 11, 19, 23, tzinfo=UTC)) == [date(2026, 10, 20)]
    assert cycle_dates(start, None, datetime(2026, 12, 21, tzinfo=UTC)) == [
        date(2026, 10, 20),
        date(2026, 11, 20),
        date(2026, 12, 20),
    ]
    assert cycle_dates(start, None, datetime(2026, 10, 19, tzinfo=UTC)) == []


def test_the_cycles_of_an_ended_assignment_stop():
    start, end = at(1, 20), at(3, 20, 10)
    assert cycle_dates(start, end, at(8, 1)) == [date(2026, 1, 20), date(2026, 2, 20)]


def test_the_next_cycle_date_is_the_next_anchor_day():
    assert next_cycle_date(at(10, 20), datetime(2026, 10, 20, 9, tzinfo=UTC)) == date(2026, 11, 20)
    assert next_cycle_date(at(1, 31), at(2, 10)) == date(2026, 2, 28)


def test_an_invoice_falls_due_after_its_date_and_a_legacy_one_after_its_month():
    assert due_at_of("2026-10-20") == at(10, 20) + (at(11, 4) - at(10, 20))  # 15 days
    assert due_at_of("2026-05") == at(6, 1) + (at(6, 16) - at(6, 1))


# ---- amounts: always the plan's price -----------------------------------------------------------


def test_the_invoice_is_dated_by_its_cycle_and_costs_the_plan_price():
    invoice = build(assignment(at(5, 20), price="49.90"))
    assert invoice.amount == Decimal("49.90")
    assert invoice.period == "2026-05-20" and invoice.issued_on == date(2026, 5, 20)
    [line] = invoice.lines
    assert (line.plan_id, line.monthly_price, line.currency, line.amount) == (
        "pro",
        Decimal("49.90"),
        "USD",
        Decimal("49.90"),
    )
    assert (invoice.status, invoice.currency, invoice.number) == (
        InvoiceStatus.PENDING,
        "USD",
        "INV-2026-05-20-alpha",
    )


def test_there_is_no_prorata_whatever_the_day_of_the_month():
    for cycle in (date(2026, 2, 28), date(2026, 5, 31), date(2026, 6, 1)):
        assert build(assignment(at(1, 1), price="30"), cycle=cycle).amount == Decimal("30")


def test_a_free_plan_gives_a_zero_invoice():
    invoice = build(assignment(at(5, 20), price="0"))
    assert invoice.amount == Decimal("0") and invoice.status is InvoiceStatus.PENDING


def test_a_legacy_monthly_invoice_stays_readable():
    legacy = replace(build(), period="2026-05")
    assert legacy.number == "INV-2026-05-alpha"
    assert legacy.issued_on == date(2026, 5, 1)
    assert legacy.due_at == at(6, 16)


# ---- the stores (one contract) ------------------------------------------------------------------


@pytest.fixture(params=["sql", "memory"])
def store(request):
    if request.param == "memory":
        return InMemoryInvoiceStore()
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return SqlInvoiceStore(engine)


def invoice(client="alpha", period="2026-05", price="31"):
    made = build(assignment(at(1, 1), client=client, price=price))
    return replace(made, period=period)  # "2026-05" is a legacy month: both forms are kept


def test_an_invoice_is_kept_with_its_lines_and_an_id(store):
    kept = store.create(invoice())
    assert kept.invoice_id > 0
    assert store.get(kept.invoice_id) == kept
    assert store.get(kept.invoice_id).lines[0].amount == Decimal("31.00")
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
    kept = invoices.create(build(plans.current_assignment("alpha")))

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
    # and the assignment still carries the terms it recorded
    [old] = plans.assignment_history("alpha")
    assert build(old).lines == kept.lines


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


def test_a_100_percent_discount_is_its_own_status_and_never_a_collection(store):
    pending = store.create(invoice("alpha"))
    done = store.settle(
        pending.invoice_id, InvoiceStatus.DISCOUNTED_100, at=NOW, by="root", reason="  goodwill "
    )
    assert done.status is InvoiceStatus.DISCOUNTED_100
    assert done.status not in (InvoiceStatus.PAID, InvoiceStatus.FOR_FREE)
    assert not done.is_collected
    assert done.amount == pending.amount  # the billed amount, before discount, is kept
    assert done.amount_after_discount == 0 and done.discount_percent == 100
    assert done.discount_amount == pending.amount and done.free_amount == pending.amount
    assert done.discount_reason == "goodwill"
    assert (done.status_changed_by, done.status_changed_at) == ("root", NOW)
    assert [r.event_status for r in store.payment_events(done.invoice_id)] == ["DISCOUNTED_100"]
    # final: nothing changes it afterwards
    for target in (InvoiceStatus.PAID, InvoiceStatus.FOR_FREE, InvoiceStatus.DISCOUNTED_100):
        with pytest.raises(InvoiceStatusError):
            store.settle(done.invoice_id, target, at=NOW, by="other")
    assert store.get(done.invoice_id).status is InvoiceStatus.DISCOUNTED_100


def test_for_free_is_not_a_collection_either_and_has_no_discount(store):
    free = store.settle(store.create(invoice("beta")).invoice_id, InvoiceStatus.FOR_FREE, at=NOW, by="root")
    assert not free.is_collected and free.discount_percent == 0 and free.free_total == free.amount


def test_a_paid_invoice_cannot_be_discounted(store):
    paid = store.settle(store.create(invoice("gamma")).invoice_id, InvoiceStatus.PAID, at=NOW, by="root")
    with pytest.raises(InvoiceStatusError):
        store.settle(paid.invoice_id, InvoiceStatus.DISCOUNTED_100, at=NOW, by="root")
    assert store.get(paid.invoice_id).discount_percent == 0
    assert store.get(paid.invoice_id).is_collected


def test_a_month_filter_covers_cycle_dates_and_legacy_months(store):
    store.create(invoice("alpha", "2026-05"))
    store.create(invoice("alpha", "2026-06-20"))
    store.create(invoice("alpha", "2026-07-20"))

    def periods(**kw):
        return [i.period for i in store.list_invoices(InvoiceFilter(**kw))]

    assert periods() == ["2026-07-20", "2026-06-20", "2026-05"]
    assert periods(period_from="2026-06", period_to="2026-06") == ["2026-06-20"]
    assert periods(period_to="2026-06") == ["2026-06-20", "2026-05"]
    assert periods(period_from="2026-06-21") == ["2026-07-20"]


def test_0026_widens_the_period_to_hold_a_cycle_date_and_keeps_old_invoices():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0025")
    legacy_store = SqlInvoiceStore(engine)
    legacy_store.create(invoice(period="2026-05"))
    upgrade_database(engine, "0026")
    assert current_revision(engine) == "0026"
    kept = SqlInvoiceStore(engine)
    assert kept.create(invoice(period="2026-10-20")) is not None
    assert kept.create(invoice(period="2026-10-20")) is None  # still unique per client and period
    assert [i.period for i in kept.list_invoices(InvoiceFilter())] == ["2026-10-20", "2026-05"]
