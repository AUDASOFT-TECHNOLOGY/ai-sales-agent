"""Lot 4: the Admin and Client dashboards.

SIMULATED: the metrics readers (fake counts per client), the clock, in-memory stores and a throw-away
SQLite file for the cost register. REAL: the application, its routes and sessions, the finance
service, the cost service, the quota service and the dashboard assembly.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_audit_notifications import admin_session, client_session, fx  # noqa: F401
from test_billing_lot3 import seed_finance
from test_costs_and_quotas import (  # noqa: F401
    Rig,
    engine,
    rate,
    seed_usage,
    service,
)

from sales_app.billing import InMemoryInvoiceStore
from sales_app.dashboards import assemble
from sales_app.dashboards.figures import indicator
from sales_app.dashboards.ranges import InvalidRangeError, resolve_range
from sales_app.notifications import SYNC_FAILURE
from sales_app.plans import InMemoryPlanStore
from sales_app.provider_costs import CostReport, ProviderCosts, parse_period
from sales_app.quotas import QuotaService

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
URL = "/admin/v1/dashboard"


class FakeReader:
    """Counts of one client; `sent` / `received` messages, `contacted` / `replied` prospects."""

    def __init__(self, sent, received, contacted, replied, total=5, human=1):
        self.n = (sent, received, contacted, replied, total, human)

    def prospect_counts(self, by, *, since=None, until=None):
        return {"new": self.n[4]} if by == "status" else {}

    def prospect_total(self):
        return self.n[4]

    def needs_human(self):
        return self.n[5]

    def message_counts(self, *, since, until):
        return {("outbound", "email"): self.n[0], ("inbound", "email"): self.n[1]}

    def distinct_prospects_by_direction(self, *, since, until):
        return {"outbound": self.n[2], "inbound": self.n[3]}

    def distinct_prospects_with_messages(self, *, since, until):
        return self.n[2]

    def messages_by_day(self, *, since, until):
        return {"2026-04-20": {"outbound": self.n[0], "inbound": self.n[1]}}

    def prospects_with_failed_actions(self, *, since, until):
        return 2

    def fact_documents(self, keys, *, since, until):
        return []


READERS = {
    "alpha": FakeReader(4, 2, 4, 2, total=5),
    "beta": FakeReader(10, 5, 10, 5, total=7),
}


@pytest.fixture
def world(fx, engine):  # noqa: F811
    app = fx.application
    app.plans, app.invoices = InMemoryPlanStore(), InMemoryInvoiceStore()
    seed_finance(app.plans, app.invoices)
    app.metrics_readers = lambda cid: READERS[cid]
    app.costs = service(engine)
    app.costs.set_rate(rate("0.05", "usd"))  # without the WhatsApp tariff the costs are incomplete
    seed_usage(engine, "alpha", at=datetime(2026, 3, 10, 8, tzinfo=UTC))
    rig = Rig(cap=10)
    rig.clock = fx.clock
    app.quotas = QuotaService(rig.limits, app.notifications, fx.clock, app.plans)
    return fx


def ind(block):
    assert set(block) >= {"value", "availability"}
    return block


# ---- the Admin dashboard -------------------------------------------------------------------------


class TestAdminDashboard:
    def test_it_needs_an_admin(self, world):
        assert world.client.get(URL).status_code == 401
        assert client_session(world, "alpha").get(URL).status_code == 401

    def test_the_owner_sees_every_client_with_real_sums(self, world):
        body = admin_session(world, "root").get(URL).json()
        assert body["scope"] == "all"
        assert body["clients"]["total"] == {"value": 2, "availability": "measured"}
        a = body["activity"]
        assert ind(a["messages_sent"])["value"] == 14  # alpha 4 + beta 10
        assert a["replies_received"]["value"] == 7
        assert a["prospects_contacted"]["value"] == 14
        assert a["reply_rate_percent"]["value"] == 50.0  # 7 replied / 14 contacted
        assert a["prospects_total"]["value"] == 12
        assert a["needs_human"]["value"] == 2
        assert a["conversions_declared"] == {"value": 0, "availability": "measured"}
        assert a["conversions_confirmed"] == {"value": 0, "availability": "measured"}
        assert a["conversion_rate_declared_percent"]["value"] == 0.0
        assert a["conversion_rate_confirmed_percent"]["value"] == 0.0

    def test_a_scoped_admin_sees_only_its_clients(self, world):
        alice = admin_session(world, "alice")  # reaches alpha only
        body = alice.get(URL).json()
        assert body["scope"] == "scoped"
        assert body["clients"]["total"]["value"] == 1
        assert [c["client_id"] for c in body["clients_list"]] == ["alpha"]
        assert body["activity"]["messages_sent"]["value"] == 4
        assert [c["client_id"] for c in body["usage"]["clients"]] == ["alpha"]
        assert [c["client_id"] for c in body["integrations"]["clients"]] == ["alpha"]
        assert alice.get(URL, params={"client_id": "beta"}).status_code == 404

    def test_a_client_filter_narrows_every_block(self, world):
        body = admin_session(world, "root").get(URL, params={"client_id": "beta"}).json()
        assert body["client_id"] == "beta"
        assert body["activity"]["messages_sent"]["value"] == 10
        assert body["finance"]["totals"] == [] or all(
            t["currency"] == "USD" for t in body["finance"]["totals"]
        )
        assert (
            admin_session(world, "root").get(URL, params={"client_id": "ghost"}).status_code == 404
        )

    def test_finance_reflects_invoices_and_distinguishes_billed_collected_free(self, world):
        body = (
            admin_session(world, "root")
            .get(URL, params={"from": "2026-03", "to": "2026-04"})
            .json()
        )
        f = body["finance"]
        usd = next(t for t in f["totals"] if t["currency"] == "USD")
        assert usd["billed"] == "120.00" and usd["collected"] == "30.00"
        assert usd["free"] == "60.00" and usd["discounted"] == "30.00"
        assert f["availability"] == "measured"
        assert body["series"]["subscriptions"]["points"][-1]["period"] == "2026-04"
        assert body["series"]["finance"]["timeline"]

    def test_costs_carry_their_basis_and_the_margin_only_when_complete_and_measured(self, world):
        body = (
            admin_session(world, "root")
            .get(URL, params={"from": "2026-03", "to": "2026-04"})
            .json()
        )
        costs = body["costs"]
        [openai] = [p for p in costs["providers"] if p["provider"] == "openai"]
        assert openai["basis"] == "measured" and openai["totals"] == {"USD": "0.007500"}
        assert openai["availability"] == "measured"
        profit = body["profitability"]
        assert profit["status"] == "available", profit
        assert profit["basis"] == "estimated"  # the WhatsApp tariff is flat, not measured
        assert profit["margin"]["USD"] == "29.992500"  # collected 30.00 - measured cost
        series = {p["period"]: p for p in body["series"]["costs"]["points"]}
        assert series["2026-03"]["by_provider"][0]["totals"] == {"USD": "0.007500"}

    def test_without_a_price_the_cost_is_not_tracked_and_the_profit_is_partial(self, world, engine):  # noqa: F811
        world.application.costs = service(engine, price=False)  # the LLM has no price
        body = (
            admin_session(world, "root")
            .get(URL, params={"from": "2026-03", "to": "2026-04"})
            .json()
        )
        [openai] = [p for p in body["costs"]["providers"] if p["provider"] == "openai"]
        assert openai["basis"] == "unavailable" and openai["availability"] == "not_tracked"
        assert openai["totals"] == {}
        assert body["profitability"]["status"] == "partial"  # a cost is unavailable: no margin
        assert body["profitability"]["margin"] == {}

    def test_nothing_is_invented_without_a_database(self, world):
        app = world.application
        app.metrics_readers = app.costs = app.quotas = None
        app.invoices = app.plans = app.finance = None
        body = admin_session(world, "root").get(URL).json()
        assert body["activity"]["messages_sent"]["value"] is None
        assert body["activity"]["messages_sent"]["availability"] == "not_tracked"
        assert body["activity"]["messages_sent"]["reason"]
        assert body["finance"]["availability"] == "not_tracked" and "totals" not in body["finance"]
        assert body["costs"]["availability"] == "not_tracked"
        assert body["profitability"]["status"] == "unavailable"
        assert body["usage"]["availability"] == "not_tracked"
        assert body["series"]["activity"]["points"] == []
        assert body["series"]["performance"]["points"] == []
        assert body["clients"]["total"]["value"] == 2  # what is really held stays

    def test_open_incidents_are_counted_per_scope(self, world):
        notifications = world.application.notifications
        notifications.notify_both(SYNC_FAILURE, "alpha")
        notifications.notify_both(SYNC_FAILURE, "beta")
        root = admin_session(world, "root").get(URL).json()
        assert root["reliability"]["open"]["value"] == 2
        assert root["reliability"]["sync_failures"]["value"] == 2
        alice = admin_session(world, "alice").get(URL).json()
        assert alice["reliability"]["open"]["value"] == 1
        assert sum(p["incidents"] for p in root["series"]["reliability"]["points"]) == 2

    def test_the_series_cover_the_six_families(self, world):
        series = (
            admin_session(world, "root")
            .get(URL, params={"from": "2026-04", "to": "2026-04"})
            .json()["series"]
        )
        assert set(series) == {
            "activity",
            "performance",
            "finance",
            "subscriptions",
            "costs",
            "reliability",
        }
        day = next(p for p in series["activity"]["points"] if p["period"] == "2026-04-20")
        assert (day["sent"], day["received"]) == (14, 7)
        perf = series["performance"]["points"][0]
        assert perf["reply_rate_percent"]["value"] == 50.0
        assert perf["conversions_declared"]["availability"] == "measured"
        assert perf["conversions_confirmed"]["value"] == 0

    def test_a_bad_range_is_refused(self, world):
        root = admin_session(world, "root")
        assert root.get(URL, params={"from": "oops"}).status_code == 422
        assert root.get(URL, params={"from": "2026-05", "to": "2026-01"}).status_code == 422
        assert root.get(URL, params={"from": "2020-01", "to": "2026-05"}).status_code == 422


# ---- the Client dashboard ------------------------------------------------------------------------


class TestClientDashboard:
    CURL = "/client/v1/dashboard"

    def test_it_needs_a_client_session(self, world):
        assert world.client.get(self.CURL).status_code == 401
        assert admin_session(world, "root").get(self.CURL).status_code == 401

    def test_a_client_sees_only_itself(self, world):
        alpha = client_session(world, "alpha").get(self.CURL).json()
        beta = client_session(world, "beta").get(self.CURL).json()
        assert alpha["activity"]["messages_sent"]["value"] == 4
        assert beta["activity"]["messages_sent"]["value"] == 10
        assert alpha["activity"]["conversions_confirmed"]["availability"] == "measured"
        assert alpha["activity"]["payments"]["declared"]["value"] == 0

    def test_a_client_cannot_choose_another_client(self, world):
        session = client_session(world, "alpha")
        body = session.get(self.CURL, params={"client_id": "beta"}).json()
        assert body["activity"]["messages_sent"]["value"] == 4  # the parameter is ignored

    def test_nothing_technical_or_financial_of_the_platform_is_shown(self, world):
        text = client_session(world, "alpha").get(self.CURL).text
        for forbidden in ("provider", "openai", "tokens", "margin", "profitab", "cost"):
            assert forbidden not in text.lower(), forbidden

    def test_it_carries_the_client_billing_and_usage(self, world):
        body = client_session(world, "alpha").get(self.CURL).json()
        assert body["billing"]["subscription"]["status"] == "active"
        assert body["billing"]["invoices"]["issued"]["value"] == 2
        assert body["usage"]["availability"] == "measured"
        assert "admin_cap" not in body["usage"] or body["usage"]["admin_cap"] is None
        assert body["agent"]["availability"] == "measured"

    def test_a_clients_incidents_are_its_own(self, world):
        world.application.notifications.notify_both(SYNC_FAILURE, "beta")
        alpha = client_session(world, "alpha").get(self.CURL).json()
        beta = client_session(world, "beta").get(self.CURL).json()
        assert alpha["incidents"]["open"]["value"] == 0
        assert beta["incidents"]["open"]["value"] == 1
        assert all("client_id" not in item for item in beta["incidents"]["items"])

    def test_without_a_database_the_figures_are_not_tracked(self, world):
        app = world.application
        app.metrics_readers = app.quotas = None
        app.invoices = app.plans = None
        body = client_session(world, "alpha").get(self.CURL).json()
        assert body["activity"]["messages_sent"]["value"] is None
        assert body["usage"]["availability"] == "not_tracked"
        assert body["billing"]["availability"] == "not_tracked"


# ---- the pure rules ------------------------------------------------------------------------------


def report(month, *providers):
    return CostReport(parse_period(month), None, list(providers), [], {}, "measured")


def provider(name, basis, totals, lines=1, unavailable=0):
    return ProviderCosts(
        name, basis, {c: Decimal(v) for c, v in totals.items()}, lines, unavailable
    )


class TestRules:
    def test_a_hole_in_a_sum_is_not_a_zero(self):
        from sales_app.metrics import NotTracked

        assert isinstance(assemble.sum_figures([1, NotTracked("x")]), NotTracked)
        assert assemble.sum_figures([1, 2]) == 3

    def test_indicator_rejects_an_unknown_availability(self):
        with pytest.raises(ValueError):
            indicator(1, "guessed")

    def test_profitability_is_available_only_with_complete_measured_costs(self):
        costs = assemble.merge_costs(
            [report("2026-03", provider("openai", "measured", {"USD": "2"}))]
        )
        result = assemble.profitability({"USD": Decimal("30.00")}, costs)
        assert result["status"] == "available" and result["margin"] == {"USD": "28.00"}
        assert result["basis"] == "measured"

    def test_incomplete_costs_make_it_partial_without_a_margin(self):
        costs = assemble.merge_costs(
            [
                report(
                    "2026-03",
                    provider("openai", "measured", {"USD": "2"}),
                    provider("ovh", "unavailable", {}, lines=1, unavailable=1),
                )
            ]
        )
        result = assemble.profitability({"USD": Decimal("30.00")}, costs)
        assert result["status"] == "partial" and result["margin"] == {}
        assert result["costs_known"] == {"USD": "2"}

    def test_estimated_costs_give_a_margin_flagged_estimated(self):
        costs = assemble.merge_costs(
            [report("2026-03", provider("meta_whatsapp", "estimated", {"EUR": "3"}))]
        )
        result = assemble.profitability({"EUR": Decimal(9)}, costs)
        assert (result["status"], result["basis"], result["margin"]) == (
            "available",
            "estimated",
            {"EUR": "6"},
        )

    def test_costs_in_a_currency_without_revenue_are_not_netted(self):
        costs = assemble.merge_costs(
            [report("2026-03", provider("openai", "measured", {"EUR": "2"}))]
        )
        result = assemble.profitability({"USD": Decimal(30)}, costs)
        assert result["status"] == "partial" and result["margin"] == {}

    def test_no_cost_at_all_is_unavailable_not_free(self):
        costs = assemble.merge_costs(
            [report("2026-03", provider("openai", "measured", {}, lines=0))]
        )
        assert assemble.profitability({"USD": Decimal(30)}, costs)["status"] == "unavailable"

    def test_ranges(self):
        r = resolve_range(None, None, NOW)
        assert r.months() == ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
        assert resolve_range("2026-02-10", "2026-02-20", NOW).granularity == "day"
        assert resolve_range("2025-01", "2026-05", NOW).granularity == "month"
        with pytest.raises(InvalidRangeError):
            resolve_range("2024-01", "2026-05", NOW)
