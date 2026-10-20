"""Lot 3: automatic invoicing, the Client's invoices and subscription, the financial aggregations.

SIMULATED: nothing but the clock and the stores' in-memory twins (and a throw-away SQLite file for the
SQL parity checks). REAL: the application, its routes and sessions, the billing job, the finance
service and the SQL reader.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_audit_notifications import admin_session, client_session, fx  # noqa: F401

from sales_app.audit_log import AuditQuery, AuditService, InMemoryAuditLog
from sales_app.billing import (
    DEFAULT_PRORATION,
    DailyProrata,
    InMemoryInvoiceStore,
    InvalidPeriodError,
    InvoiceFilter,
    InvoiceLine,
    InvoiceStatus,
)
from sales_app.billing_service import AutoBilling, BillingLoop, last_closed_periods
from sales_app.finance.service import FinanceService, StoreFinanceReader
from sales_app.notifications import (
    ADMIN,
    BILLING_ERROR,
    InMemoryNotificationStore,
    NotificationQuery,
    NotificationService,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.finance_rows import SqlFinanceReader
from sales_app.persistence.invoice_rows import SqlInvoiceStore
from sales_app.persistence.plan_rows import SqlPlanStore
from sales_app.plans import InMemoryPlanStore, Plan
from sales_app.sourcing.errors import StoreUnavailableError

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


def at(month, day, year=2026):
    return datetime(year, month, day, tzinfo=UTC)


def make_plan(plan_id="pro", price="30.00", currency="USD", period="monthly", quota=50):
    return Plan(
        plan_id,
        plan_id.title(),
        Decimal(price),
        currency,
        1000,
        quota,
        True,
        at(1, 1),
        at(1, 1),
        "r",
        billing_period=period,
    )


class World:
    def __init__(self, plans=None, invoices=None):
        self.plans = plans or InMemoryPlanStore()
        self.invoices = invoices or InMemoryInvoiceStore()
        self.notifications = NotificationService(InMemoryNotificationStore(), now=lambda: NOW)
        self.audit = AuditService(InMemoryAuditLog(), now=lambda: NOW)
        for plan in (make_plan(), make_plan("eu", "40.00", "EUR"), make_plan("yearly", "300", period="annual")):
            self.plans.create_plan(plan)

    def billing(self, **kw):
        return AutoBilling(self.plans, self.invoices, self.notifications, self.audit, **kw)

    def admin_notifications(self):
        return self.notifications.search(NotificationQuery(audience=ADMIN))[0]

    def audit_entries(self):
        return self.audit.search(AuditQuery(action="billing.*"))[0]


# ---- automatic invoicing -------------------------------------------------------------------------


class TestAutoBilling:
    def test_it_invoices_every_closed_month_from_the_assignment_date(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(3, 16), by="seed")
        report = w.billing().run(NOW)
        assert (report.created, report.already_invoiced, report.errors) == (2, 0, 0)
        invoices = {i.period: i for i in w.invoices.list_invoices(InvoiceFilter())}
        assert set(invoices) == {"2026-03", "2026-04"}  # May has not ended; Feb is before sign-up
        assert invoices["2026-03"].amount == Decimal("15.48")  # 16 of 31 days
        assert invoices["2026-04"].amount == Decimal("30.00")
        assert {i.created_by for i in invoices.values()} == {"system:billing"}
        assert all(i.status is InvoiceStatus.PENDING for i in invoices.values())

    def test_running_again_or_after_a_restart_never_duplicates(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(3, 16), by="seed")
        w.billing().run(NOW)
        again = w.billing().run(NOW)  # a new instance, as after a restart
        assert (again.created, again.already_invoiced) == (0, 2)
        assert len(w.invoices.list_invoices(InvoiceFilter())) == 2

    def test_a_downtime_is_caught_up_on_the_next_run(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(1, 1), by="seed")
        w.billing().run(datetime(2026, 2, 5, tzinfo=UTC))  # only January had ended
        later = w.billing().run(NOW)  # the process was down for three months
        assert later.created == 3 and later.already_invoiced == 1  # Feb, Mar, Apr; Jan existed

    def test_each_invoice_is_audited_and_nothing_is_notified_on_success(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(4, 1), by="seed")
        w.billing().run(NOW)
        [entry] = w.audit_entries()
        assert (entry.action, entry.actor, entry.client_id, entry.outcome) == (
            "billing.invoice.auto_generated",
            "system:billing",
            "alpha",
            "success",
        )
        assert entry.target == "INV-2026-04-alpha"
        assert w.admin_notifications() == []

    def test_a_failure_notifies_the_admin_once_and_does_not_stop_the_others(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(4, 1), by="seed")
        w.plans.assign("alpha", "eu", at=at(4, 11), by="seed")  # USD then EUR in April
        w.plans.assign("beta", "pro", at=at(4, 1), by="seed")
        for _ in range(2):
            report = w.billing().run(NOW)
        assert [i.client_id for i in w.invoices.list_invoices(InvoiceFilter())] == ["beta"]
        assert report.errors == 1
        [note] = w.admin_notifications()
        assert (note.kind, note.client_id) == (BILLING_ERROR, "alpha")
        rejected = [e for e in w.audit_entries() if e.outcome == "rejected"]
        assert rejected and rejected[0].client_id == "alpha"

    def test_the_notification_closes_once_the_invoice_exists(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(4, 1), by="seed")
        failing = w.billing(proration=_Broken())
        assert failing.run(NOW).errors == 1
        assert w.admin_notifications()[0].resolved_at is None
        assert w.billing().run(NOW).created == 1
        assert w.admin_notifications()[0].resolved_at is not None

    def test_an_annual_plan_is_recognised_and_not_invoiced(self):
        w = World()
        w.plans.assign("alpha", "yearly", at=at(1, 1), by="seed")
        report = w.billing().run(NOW)
        assert report.created == 0
        assert [(s.client_id, s.period) for s in report.skipped] == [
            ("alpha", p) for p in ("2026-01", "2026-02", "2026-03", "2026-04")
        ]
        assert w.invoices.list_invoices(InvoiceFilter()) == []

    def test_an_unreadable_store_notifies_and_audits(self):
        w = World()

        class Down(InMemoryPlanStore):
            def assignments_between(self, start, end):
                raise StoreUnavailableError("down")

        w.plans = Down()
        report = w.billing().run(NOW)
        assert report.errors == 1
        [note] = w.admin_notifications()
        assert (note.kind, note.client_id) == (BILLING_ERROR, None)
        assert w.audit_entries()[0].outcome == "error"

    def test_the_proration_rule_is_a_replaceable_policy(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(3, 16), by="seed")

        class FullMonth:
            def lines(self, assignments, start, end):
                a = assignments[0]
                n = (end - start).days
                return [
                    InvoiceLine(a.plan_id, a.plan_name, a.monthly_price, a.currency, 0, 1, n, n, a.monthly_price)
                ]

        w.billing(proration=FullMonth()).run(NOW)
        amounts = {i.period: i.amount for i in w.invoices.list_invoices(InvoiceFilter())}
        assert amounts == {"2026-03": Decimal("30.00"), "2026-04": Decimal("30.00")}

    def test_the_current_policy_is_the_existing_daily_prorata(self):
        assert isinstance(DEFAULT_PRORATION, DailyProrata)

    def test_last_closed_periods(self):
        assert last_closed_periods(NOW, 3) == ["2026-02", "2026-03", "2026-04"]
        assert last_closed_periods(datetime(2026, 1, 3, tzinfo=UTC), 2) == ["2025-11", "2025-12"]


class _Broken:
    def lines(self, assignments, start, end):
        raise RuntimeError("boom")


class _Leases:
    def __init__(self, free):
        self.free, self.taken, self.released = free, [], []

    def try_acquire(self, client_id, job, now):
        self.taken.append((client_id, job))
        return self.free

    def release(self, client_id, job):
        self.released.append((client_id, job))


class TestBillingLoop:
    def test_it_runs_under_the_lease_and_releases_it(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(4, 1), by="seed")
        leases = _Leases(True)
        report = BillingLoop(w.billing(), lambda: NOW, leases=leases).run_once()
        assert report is not None and report.created == 1
        assert leases.taken == leases.released == [("platform", "billing")]

    def test_it_does_nothing_when_another_process_holds_the_lease(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(4, 1), by="seed")
        leases = _Leases(False)
        assert BillingLoop(w.billing(), lambda: NOW, leases=leases).run_once() is None
        assert w.invoices.list_invoices(InvoiceFilter()) == []
        assert leases.released == []

    def test_the_async_loop_stops_when_asked(self):
        w = World()
        w.plans.assign("alpha", "pro", at=at(4, 1), by="seed")

        async def go():
            stop = asyncio.Event()
            loop = BillingLoop(w.billing(), lambda: NOW, every_seconds=0.01)
            task = asyncio.create_task(loop.run(stop))
            await asyncio.sleep(0.1)
            stop.set()
            await asyncio.wait_for(task, 2)

        asyncio.run(go())
        assert len(w.invoices.list_invoices(InvoiceFilter())) == 1


# ---- the Client's invoices and subscription -------------------------------------------------------


@pytest.fixture
def billed(fx):  # noqa: F811
    fx.application.plans = InMemoryPlanStore()
    fx.application.invoices = InMemoryInvoiceStore()
    for plan in (make_plan(), make_plan("yearly", "300", period="annual")):
        fx.application.plans.create_plan(plan)
    fx.application.plans.assign("alpha", "pro", at=at(3, 1), by="seed")
    fx.application.plans.assign("beta", "yearly", at=at(3, 1), by="seed")
    billing = AutoBilling(
        fx.application.plans, fx.application.invoices, fx.application.notifications, fx.application.audit
    )
    billing.run(NOW)
    fx.application.plans.assign("beta", "pro", at=at(4, 1), by="seed")
    billing.run(NOW)
    return fx


def ids_of(client):
    return {i["number"]: i["invoice_id"] for i in client.get("/client/v1/invoices").json()["invoices"]}


class TestClientInvoices:
    def test_a_client_lists_only_its_own_invoices(self, billed):
        alpha = client_session(billed, "alpha")
        body = alpha.get("/client/v1/invoices")
        assert body.status_code == 200 and body.headers["cache-control"] == "no-store"
        assert [i["number"] for i in body.json()["invoices"]] == ["INV-2026-04-alpha", "INV-2026-03-alpha"]
        assert set(ids_of(client_session(billed, "beta"))) == {"INV-2026-04-beta"}

    def test_another_clients_invoice_is_a_404_like_an_unknown_one(self, billed):
        mine = ids_of(client_session(billed, "alpha"))["INV-2026-04-alpha"]
        theirs = ids_of(client_session(billed, "beta"))["INV-2026-04-beta"]
        alpha = client_session(billed, "alpha")
        assert alpha.get(f"/client/v1/invoices/{mine}").status_code == 200
        assert alpha.get(f"/client/v1/invoices/{theirs}").status_code == 404
        assert alpha.get("/client/v1/invoices/9999").status_code == 404

    def test_the_detail_has_lines_and_hides_internal_traces(self, billed):
        alpha = client_session(billed, "alpha")
        detail = alpha.get(f"/client/v1/invoices/{ids_of(alpha)['INV-2026-04-alpha']}").json()
        assert detail["amount"] == "30.00" and detail["status"] == "PENDING"
        assert detail["lines"][0]["plan_name"] == "Pro" and detail["lines"][0]["days"] == 30
        assert detail["collected"] is False and detail["overdue"] is False
        assert "created_by" not in detail and "payment_transaction_id" not in detail

    def test_it_is_read_only(self, billed):
        alpha = client_session(billed, "alpha")
        n = ids_of(alpha)["INV-2026-04-alpha"]
        for verb in ("put", "post", "patch", "delete"):
            assert getattr(alpha, verb)(f"/client/v1/invoices/{n}").status_code in (404, 405)
            assert getattr(alpha, verb)(f"/client/v1/invoices/{n}/status").status_code in (404, 405)
        assert billed.application.invoices.get(n).status is InvoiceStatus.PENDING

    def test_it_needs_a_client_session(self, billed):
        assert billed.client.get("/client/v1/invoices").status_code == 401
        assert billed.client.get("/client/v1/subscription").status_code == 401
        admin = admin_session(billed, "root")
        assert admin.get("/client/v1/invoices").status_code == 401

    def test_a_discount_is_shown_but_not_as_collected(self, billed):
        alpha = client_session(billed, "alpha")
        n = ids_of(alpha)["INV-2026-04-alpha"]
        billed.application.invoices.settle(
            n, InvoiceStatus.DISCOUNTED_100, at=NOW, by="root", reason="promo"
        )
        detail = alpha.get(f"/client/v1/invoices/{n}").json()
        assert (detail["status"], detail["collected"]) == ("DISCOUNTED_100", False)
        assert (detail["amount"], detail["amount_after_discount"], detail["free_amount"]) == (
            "30.00",
            "0.00",
            "30.00",
        )


class TestClientSubscription:
    def test_it_describes_the_plan_the_quotas_and_the_last_payment(self, billed):
        alpha = client_session(billed, "alpha")
        body = alpha.get("/client/v1/subscription").json()
        assert body["status"] == "active"
        assert body["plan"]["name"] == "Pro" and body["plan"]["billing_period"] == "monthly"
        assert (body["plan"]["daily_quota"], body["plan"]["included_credits"]) == (50, 1000)
        assert body["next_billing_at"].startswith("2026-06-01")
        assert (body["unpaid_invoices"], body["overdue_invoices"]) == (2, 1)  # March was due on 16 April
        assert body["last_payment"]["invoice_number"] == "INV-2026-04-alpha"
        assert body["last_payment"]["collected"] is False

    def test_a_paid_invoice_is_the_last_payment_state(self, billed):
        alpha = client_session(billed, "alpha")
        n = ids_of(alpha)["INV-2026-04-alpha"]
        billed.application.invoices.settle(n, InvoiceStatus.PAID, at=NOW, by="root")
        last = alpha.get("/client/v1/subscription").json()["last_payment"]
        assert (last["status"], last["collected"]) == ("PAID", True)

    def test_a_client_with_no_plan_has_no_subscription(self, billed):
        billed.application.plans.assign("alpha", None, at=NOW, by="root")
        body = client_session(billed, "alpha").get("/client/v1/subscription").json()
        assert (body["status"], body["plan"], body["next_billing_at"]) == ("no_plan", None, None)

    def test_it_is_each_clients_own(self, billed):
        beta = client_session(billed, "beta").get("/client/v1/subscription").json()
        assert beta["plan"]["since"].startswith("2026-04-01")


# ---- the financial aggregations ------------------------------------------------------------------


def seed_finance(plans, invoices):
    """alpha: Mar PAID, Apr PENDING (overdue at 2026-05-20); beta: Mar FOR_FREE, Apr DISCOUNTED_100;
    gamma (EUR): Apr PAID. alpha on a plan from March; beta leaves in April; gamma joins in April."""
    for plan in (make_plan(), make_plan("eu", "40.00", "EUR")):
        plans.create_plan(plan)
    plans.assign("alpha", "pro", at=at(3, 1), by="s")
    plans.assign("beta", "pro", at=at(3, 1), by="s")
    plans.assign("beta", None, at=at(4, 16), by="s")
    plans.assign("gamma", "eu", at=at(4, 1), by="s")
    AutoBilling(
        plans, invoices, NotificationService(InMemoryNotificationStore()), AuditService(InMemoryAuditLog())
    ).run(NOW)
    by = {(i.client_id, i.period): i for i in invoices.list_invoices(InvoiceFilter())}
    invoices.settle(by["alpha", "2026-03"].invoice_id, InvoiceStatus.PAID, at=NOW, by="r")
    invoices.settle(by["beta", "2026-03"].invoice_id, InvoiceStatus.FOR_FREE, at=NOW, by="r")
    invoices.settle(by["beta", "2026-04"].invoice_id, InvoiceStatus.DISCOUNTED_100, at=NOW, by="r")
    invoices.settle(by["gamma", "2026-04"].invoice_id, InvoiceStatus.PAID, at=NOW, by="r")


LATE = datetime(2026, 5, 20, tzinfo=UTC)  # April's invoices are due on 2026-05-16


def usd(summary):
    return next(t for t in summary.totals if t.currency == "USD")


class TestFinanceService:
    def make(self):
        plans, invoices = InMemoryPlanStore(), InMemoryInvoiceStore()
        seed_finance(plans, invoices)
        return FinanceService(StoreFinanceReader(invoices, plans))

    def test_figures(self):
        s = self.make().summary(period_from="2026-03", period_to="2026-04", now=LATE)
        t = usd(s)
        assert t.billed == Decimal("105.00")
        assert t.collected == Decimal("30.00")  # alpha March only
        assert t.free == Decimal("45.00")  # beta: 30.00 for free + 15.00 discounted
        assert t.discounted == Decimal("15.00")
        assert t.after_discount == Decimal("90.00")  # 105 minus the 15.00 discount
        assert (t.pending, t.overdue) == (Decimal("0"), Decimal("30.00"))  # alpha April is late
        assert (t.issued, t.paid, t.free_count, t.discounted_count, t.overdue_count) == (4, 1, 1, 1, 1)

    def test_it_is_pending_before_the_due_date(self):
        s = self.make().summary(period_from="2026-03", period_to="2026-04", now=NOW)
        t = usd(s)
        assert (t.pending, t.overdue, t.pending_count) == (Decimal("30.00"), Decimal("0"), 1)

    def test_currencies_are_never_added(self):
        s = self.make().summary(period_from="2026-03", period_to="2026-04", now=LATE)
        assert [t.currency for t in s.totals] == ["EUR", "USD"]
        eur = next(t for t in s.totals if t.currency == "EUR")
        assert (eur.billed, eur.collected, eur.paid) == (Decimal("40.00"), Decimal("40.00"), 1)

    def test_the_client_filter(self):
        s = self.make().summary(period_from="2026-03", period_to="2026-04", now=LATE, client_id="beta")
        assert [t.currency for t in s.totals] == ["USD"]
        t = usd(s)
        assert (t.billed, t.collected, t.issued) == (Decimal("45.00"), Decimal("0"), 2)
        s = self.make().summary(
            period_from="2026-03", period_to="2026-04", now=LATE, client_ids=frozenset({"gamma"})
        )
        assert [t.currency for t in s.totals] == ["EUR"]

    def test_the_timeline_compares_billed_and_collected_by_month(self):
        s = self.make().summary(period_from="2026-03", period_to="2026-04", now=LATE)
        got = {(p.period, p.currency): (p.billed, p.collected) for p in s.timeline}
        assert got["2026-03", "USD"] == (Decimal("60.00"), Decimal("30.00"))
        assert got["2026-04", "USD"] == (Decimal("45.00"), Decimal("0"))
        assert got["2026-04", "EUR"] == (Decimal("40.00"), Decimal("40.00"))

    def test_active_and_inactive_subscriptions_over_time(self):
        s = self.make().summary(period_from="2026-02", period_to="2026-04", now=LATE)
        assert [(p.period, p.active, p.inactive) for p in s.subscriptions] == [
            ("2026-02", 0, 0),
            ("2026-03", 2, 0),
            ("2026-04", 2, 1),  # alpha and gamma; beta left on the 16th
        ]

    def test_profitability_is_an_extension_point_not_an_estimate(self):
        service = self.make()
        assert service.summary(period_from="2026-03", period_to="2026-04", now=LATE).profitability.status == "not_connected"

        class Costs:
            def costs(self, **_):
                return {"USD": Decimal("12.50")}

        plans, invoices = InMemoryPlanStore(), InMemoryInvoiceStore()
        seed_finance(plans, invoices)
        with_costs = FinanceService(StoreFinanceReader(invoices, plans), Costs())
        p = with_costs.summary(period_from="2026-03", period_to="2026-04", now=LATE).profitability
        assert p.status == "available" and p.margin["USD"] == Decimal("17.50")
        assert p.margin["EUR"] == Decimal("40.00")

    def test_a_bad_range_is_refused(self):
        with pytest.raises(InvalidPeriodError):
            self.make().summary(period_from="2026-04", period_to="2026-03", now=LATE)
        with pytest.raises(InvalidPeriodError):
            self.make().summary(period_from="2000-01", period_to="2026-03", now=LATE)


class TestFinanceApi:
    @pytest.fixture
    def seeded(self, fx):  # noqa: F811
        fx.application.plans, fx.application.invoices = InMemoryPlanStore(), InMemoryInvoiceStore()
        seed_finance(fx.application.plans, fx.application.invoices)
        return fx

    URL = "/admin/v1/finance/summary"

    def test_it_needs_an_admin(self, seeded):
        assert seeded.client.get(self.URL).status_code == 401
        assert client_session(seeded, "alpha").get(self.URL).status_code == 401

    def test_the_owner_reads_the_contract(self, seeded):
        root = admin_session(seeded, "root")
        body = root.get(self.URL, params={"from": "2026-03", "to": "2026-04"}).json()
        assert (body["period_from"], body["period_to"], body["client_id"]) == ("2026-03", "2026-04", None)
        usd_totals = next(t for t in body["totals"] if t["currency"] == "USD")
        assert usd_totals["billed"] == "105.00" and usd_totals["collected"] == "30.00"
        assert usd_totals["invoices_issued"] == 4 and usd_totals["invoices_paid"] == 1
        assert usd_totals["free"] == "45.00" and usd_totals["discounted"] == "15.00"
        assert body["subscriptions"][-1] == {"period": "2026-04", "active": 2, "inactive": 1}
        assert body["profitability"]["status"] == "not_connected"

    def test_default_range_is_the_last_twelve_closed_months(self, seeded):
        body = admin_session(seeded, "root").get(self.URL).json()
        assert (body["period_from"], body["period_to"]) == ("2025-05", "2026-04")

    def test_a_scoped_admin_sees_only_its_clients(self, seeded):
        alice = admin_session(seeded, "alice")  # reaches alpha only
        body = alice.get(self.URL, params={"from": "2026-03", "to": "2026-04"}).json()
        assert [t["currency"] for t in body["totals"]] == ["USD"]
        assert body["totals"][0]["invoices_issued"] == 2  # alpha's two invoices
        assert alice.get(self.URL, params={"client_id": "beta"}).status_code == 404
        assert alice.get(self.URL, params={"client_id": "alpha"}).status_code == 200

    def test_bad_months_are_a_422(self, seeded):
        root = admin_session(seeded, "root")
        assert root.get(self.URL, params={"from": "03-2026"}).status_code == 422
        assert root.get(self.URL, params={"from": "2026-04", "to": "2026-03"}).status_code == 422


# ---- SQL: the same figures, and the plan's billing period ---------------------------------------


@pytest.fixture
def sql(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'lot3.db'}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


class TestSql:
    def test_the_sql_figures_equal_the_in_memory_ones(self, sql):
        mem_plans, mem_invoices = InMemoryPlanStore(), InMemoryInvoiceStore()
        seed_finance(mem_plans, mem_invoices)
        sql_plans, sql_invoices = SqlPlanStore(sql), SqlInvoiceStore(sql)
        seed_finance(sql_plans, sql_invoices)
        kwargs = {"period_from": "2026-02", "period_to": "2026-04", "now": LATE}
        expected = FinanceService(StoreFinanceReader(mem_invoices, mem_plans)).summary(**kwargs)
        got = FinanceService(SqlFinanceReader(sql)).summary(**kwargs)
        assert got.totals == expected.totals
        assert got.timeline == expected.timeline
        assert got.subscriptions == expected.subscriptions
        scoped = {"client_id": "beta"}
        assert (
            FinanceService(SqlFinanceReader(sql)).summary(**kwargs, **scoped).totals
            == FinanceService(StoreFinanceReader(mem_invoices, mem_plans)).summary(**kwargs, **scoped).totals
        )

    def test_the_billing_period_is_kept_and_defaults_to_monthly(self, sql):
        plans = SqlPlanStore(sql)
        plans.create_plan(make_plan("pro"))
        plans.create_plan(make_plan("yearly", period="annual"))
        assert plans.get_plan("pro").billing_period == "monthly"
        assert plans.get_plan("yearly").billing_period == "annual"
        plans.update_plan(replace(plans.get_plan("pro"), billing_period="annual"))
        assert plans.get_plan("pro").billing_period == "annual"

    def test_the_job_on_sql_stores_is_idempotent(self, sql):
        plans, invoices = SqlPlanStore(sql), SqlInvoiceStore(sql)
        plans.create_plan(make_plan())
        plans.assign("alpha", "pro", at=at(3, 16), by="s")
        notes = NotificationService(InMemoryNotificationStore())
        audit = AuditService(InMemoryAuditLog())
        first = AutoBilling(plans, invoices, notes, audit).run(NOW)
        second = AutoBilling(plans, invoices, notes, audit).run(NOW)
        assert (first.created, second.created, second.already_invoiced) == (2, 0, 2)
