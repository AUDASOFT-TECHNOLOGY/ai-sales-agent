"""Technical costs by provider (measured / estimated / unavailable) and quotas with alerts (lot 2C)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from test_admin_clients_api import NOW, PASSWORD, Fixture, editor, owner

from sales_app.admin_auth.passwords import hash_password
from sales_app.client_profiles import ClientProfile
from sales_app.llm_pricing import InMemoryLLMPricingStore, validate_price
from sales_app.llm_usage import STATUS_ERROR, STATUS_OK, LLMUsageRecord
from sales_app.notifications import (
    ADMIN,
    CLIENT,
    InMemoryNotificationStore,
    NotificationQuery,
    NotificationService,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.llm_usage_rows import SqlLLMUsageStore
from sales_app.persistence.provider_cost_rows import SqlCostStore
from sales_app.persistence.send_cap_rows import SqlSendCounter
from sales_app.persistence.tables import wa_deliveries
from sales_app.plans import InMemoryPlanStore
from sales_app.provider_costs import (
    ESTIMATED,
    MEASURED,
    UNAVAILABLE,
    CostService,
    InvalidCostError,
    parse_period,
    validate_entry,
    validate_rate,
)
from sales_app.quotas import QuotaService
from sales_app.send_cap import (
    CAP_REACHED,
    InMemorySendCapStore,
    InMemorySendCounter,
    Refusal,
    SendLimits,
)
from sales_integrations.fakes import FakeClock

MAY = parse_period("2026-05")
AT = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'costs.db'}")
    upgrade_database(engine)
    return engine


def seed_usage(
    engine,
    client_id,
    model="gpt-x",
    inp: int | None = 1000,
    out: int | None = 500,
    status=STATUS_OK,
    at=AT,
):
    SqlLLMUsageStore(engine, client_id).record(
        LLMUsageRecord(client_id, at, "openai", model, status, inp, out)
    )


def seed_delivery(engine, client_id, message_id, status="sent", at=AT):
    with engine.begin() as connection:
        ensure_client(connection, client_id, created_at=at, created_by=None)
        connection.execute(
            insert(wa_deliveries).values(
                client_id=client_id,
                message_id=message_id,
                status=status,
                at=at,
                recipient="2166",
                error_code="",
                error_title="",
            )
        )


def service(engine, price=True) -> CostService:
    pricing = InMemoryLLMPricingStore()
    if price:
        pricing.set_price(
            validate_price("openai", "gpt-x", "2.50", "10", "usd", updated_at=AT, updated_by="root")
        )
    return CostService(SqlCostStore(engine), pricing, now=lambda: AT)


def rate(price="0.05", currency="eur"):
    return validate_rate(
        "meta_whatsapp",
        "message_sent",
        "message",
        price,
        currency,
        updated_at=AT,
        updated_by="root",
    )


class TestCosts:
    def test_llm_is_measured_from_usage_and_the_configured_price(self, engine):
        seed_usage(engine, "alpha")
        seed_usage(engine, "alpha", inp=2000, out=0)
        report = service(engine).costs_by_provider(MAY, "alpha")
        [openai] = [p for p in report.providers if p.provider == "openai"]
        # (3000 x 2.50 + 500 x 10) / 1e6
        assert openai.basis == MEASURED and openai.totals == {"USD": Decimal("0.012500")}
        assert openai.complete

    def test_a_call_without_token_count_makes_the_amount_estimated_not_zero(self, engine):
        seed_usage(engine, "alpha")
        seed_usage(engine, "alpha", inp=None, out=None)
        seed_usage(
            engine, "alpha", inp=None, out=None, status=STATUS_ERROR
        )  # not a measurable call
        [line] = [line for line in service(engine).computed_lines(MAY) if line.provider == "openai"]
        assert line.basis == ESTIMATED and line.amount == Decimal("0.007500")

    def test_llm_without_a_price_is_unavailable_never_zero(self, engine):
        seed_usage(engine, "alpha")
        report = service(engine, price=False).costs_by_provider(MAY)
        [openai] = [p for p in report.providers if p.provider == "openai"]
        assert openai.basis == UNAVAILABLE and openai.totals == {} and not openai.complete

    def test_meta_has_no_cost_without_the_tariff_the_admin_provides(self, engine):
        seed_delivery(engine, "alpha", "m1")
        report = service(engine).costs_by_provider(MAY, "alpha")
        [meta] = [p for p in report.providers if p.provider == "meta_whatsapp"]
        assert meta.basis == UNAVAILABLE and meta.totals == {}
        [line] = [line for line in report.lines if line.provider == "meta_whatsapp"]
        assert line.quantity == 1 and line.amount is None and line.unit_price is None

    def test_meta_uses_delivered_messages_times_the_tariff_and_ignores_failures(self, engine):
        for n, status in enumerate(("sent", "delivered", "read", "failed")):
            seed_delivery(engine, "alpha", f"m{n}", status)
        seed_delivery(engine, "beta", "b1")
        seed_delivery(engine, "alpha", "old", at=datetime(2026, 4, 30, tzinfo=UTC))
        costs = service(engine)
        costs.set_rate(rate())
        alpha = costs.costs_by_provider(MAY, "alpha")
        [meta] = [p for p in alpha.providers if p.provider == "meta_whatsapp"]
        assert meta.basis == ESTIMATED and meta.totals == {"EUR": Decimal("0.15")}
        everyone = costs.costs_by_provider(MAY)
        assert everyone.totals["EUR"] == Decimal("0.20")

    def test_manual_entries_carry_their_basis_and_currencies_are_not_mixed(self, engine):
        costs = service(engine)
        costs.add_entry(
            validate_entry(
                client_id=None,
                provider="ovh",
                category="hosting",
                period="2026-05",
                quantity=None,
                unit="month",
                unit_price=None,
                amount="30",
                currency="eur",
                basis="measured",
                by="root",
            ),
            by="root",
        )
        costs.add_entry(
            validate_entry(
                client_id=None,
                provider="infra",
                category="backup",
                period="2026-05",
                quantity=None,
                unit="month",
                unit_price=None,
                amount=None,
                currency=None,
                basis="unavailable",
                by="root",
            ),
            by="root",
        )
        seed_usage(engine, "alpha")
        report = costs.costs_by_provider(MAY)
        by = {p.provider: p for p in report.providers}
        assert by["ovh"].basis == MEASURED and by["ovh"].totals == {"EUR": Decimal("30.000000")}
        assert by["infra"].basis == UNAVAILABLE and by["infra"].unavailable_lines == 1
        assert set(report.totals) == {"EUR", "USD"} and not report.complete
        assert costs.costs_by_provider(MAY, "alpha").totals.keys() == {
            "USD"
        }  # platform lines apart

    def test_entries_are_validated(self):
        base = dict(
            client_id=None,
            provider="ovh",
            category="hosting",
            period="2026-05",
            quantity=None,
            unit="month",
            unit_price=None,
            amount="1",
            currency="eur",
            basis="measured",
            by="root",
        )
        for bad in (
            {"provider": "acme"},
            {"basis": "guess"},
            {"amount": None},
            {"currency": None},
            {"amount": "-1"},
            {"period": "2026-13"},
            {"category": "Bad Cat"},
        ):
            with pytest.raises(InvalidCostError):
                validate_entry(**{**base, **bad})
        with pytest.raises(InvalidCostError):
            validate_rate(
                "meta_whatsapp", "k", "message", "-1", "eur", updated_at=AT, updated_by="x"
            )

    def test_materialize_is_idempotent_and_not_double_counted(self, engine):
        seed_usage(engine, "alpha")
        costs = service(engine)
        first = costs.materialize(MAY, None, by="root")
        assert costs.materialize(MAY, None, by="root") == first
        store = SqlCostStore(engine)
        assert store.entries(MAY, None) == []  # computed lines are not Admin entries
        assert costs.costs_by_provider(MAY).totals == {"USD": Decimal("0.007500")}
        with engine.connect() as connection:
            from sqlalchemy import func, select

            from sales_app.persistence.tables import provider_costs

            assert (
                connection.execute(select(func.count()).select_from(provider_costs)).scalar()
                == first
            )

    def test_costs_are_limited_to_the_client_asked_for(self, engine):
        seed_usage(engine, "alpha")
        seed_usage(engine, "beta", inp=1_000_000, out=0)
        [line] = [
            line
            for line in service(engine).computed_lines(MAY, "alpha")
            if line.provider == "openai"
        ]
        assert line.client_id == "alpha" and line.amount == Decimal("0.007500")


# ---- quotas --------------------------------------------------------------------------------------


class Rig:
    def __init__(self, cap=10, plan_quota=None):
        self.clock = FakeClock(NOW)
        self.counter = InMemorySendCounter()
        self.caps = InMemorySendCapStore()
        self.caps.set("alpha", cap, by="root", at=NOW)
        self.notes = NotificationService(InMemoryNotificationStore(), now=lambda: NOW)
        self.plans = InMemoryPlanStore()
        self.limits = SendLimits(self.caps, self.counter, self.clock, lambda _c: plan_quota)
        self.quotas = QuotaService(self.limits, self.notes, self.clock, self.plans)
        self.limits.attach_observer(self.quotas.observe)

    def send(self, client="alpha"):
        return self.limits.guard(client, "email").reserve()

    def notes_of(self, audience, client="alpha"):
        found, _ = self.notes.search(NotificationQuery(audience=audience, client_id=client))
        return found


class TestQuotas:
    def test_the_alerts_open_at_80_and_100_percent_once_each(self):
        rig = Rig(cap=10)
        for _ in range(7):
            rig.send()
        assert rig.notes_of(CLIENT) == []
        rig.send()  # 8 of 10
        [warn] = rig.notes_of(CLIENT)
        assert warn.kind == "quota_reached" and "80%" in warn.title and warn.action
        rig.send(), rig.send()  # 9, 10
        assert len(rig.notes_of(CLIENT)) == 2
        for _ in range(5):  # refused: nothing new, nothing is retried into a flood
            assert isinstance(rig.send(), Refusal)
        assert len(rig.notes_of(CLIENT)) == 2 and len(rig.notes_of(ADMIN)) == 2
        reached = next(n for n in rig.notes_of(CLIENT) if "reached" in n.title)
        assert "10 of 10" in reached.message and "ask Revansa" in reached.action

    def test_the_alert_dedupe_key_carries_client_period_and_threshold(self):
        rig = Rig(cap=1)
        rig.send()
        keys = {n.dedupe_key for n in rig.notes_of(CLIENT)}
        assert keys == {
            "client|alpha|quota:email:2026-05-01:80",
            "client|alpha|quota:email:2026-05-01:100",
        }

    def test_a_new_day_alerts_again_and_a_restart_does_not_duplicate_the_open_one(self):
        rig = Rig(cap=1)
        rig.send()
        again = QuotaService(rig.limits, rig.notes, rig.clock, rig.plans)  # a "restarted" process
        again.observe("alpha", "email", date(2026, 5, 1), 1, True)
        assert len(rig.notes_of(CLIENT)) == 2
        again.observe("alpha", "email", date(2026, 5, 2), 1, True)
        assert len(rig.notes_of(CLIENT)) == 4

    def test_status_reports_day_and_month_from_the_real_counters(self):
        rig = Rig(cap=10, plan_quota=4)  # the plan quota is lower: it wins
        for _ in range(3):
            rig.send()
        rig.counter.try_increment("alpha", "email", date(2026, 5, 1), 99)  # same day, still counted
        status = rig.quotas.status("alpha")
        assert (status.daily_limit, status.plan_daily_quota, status.admin_cap) == (4, 4, 10)
        assert status.daily_used == 4 and status.state == "reached" and status.daily_percent == 100
        assert status.month_used == 4 and status.usage_basis == MEASURED
        assert status.credits_used is None and status.credits_basis == UNAVAILABLE
        assert "reached" in status.message and status.action

    def test_credits_come_from_the_model_register_a_credit_being_a_thousand_tokens(self):
        from sales_app.llm_usage import InMemoryLLMUsageStore, LLMUsageRecord

        rig = Rig(cap=10)
        store = InMemoryLLMUsageStore("alpha")
        for at, tin, tout in ((NOW, 1200, 300), (NOW, None, None), (datetime(2026, 4, 30, tzinfo=UTC), 9000, 0)):
            store.record(LLMUsageRecord("alpha", at, "openai", "m", "ok", tin, tout))
        quotas = QuotaService(rig.limits, rig.notes, rig.clock, rig.plans, lambda _c: store)
        rig.send()
        status = quotas.status("alpha")
        assert (status.included_credits, status.credits_used, status.credits_today) == (5000, 2, 2)
        assert (status.credits_remaining, status.calls, status.calls_without_figures) == (4998, 2, 1)
        assert status.credits_basis == MEASURED and status.month_limit == 310
        assert status.month_actions_used == 1 and status.days[0]["actions"] == 1

    def test_the_effective_cap_refuses_without_burning_attempts(self):
        rig = Rig(cap=2)
        assert [type(rig.send()).__name__ for _ in range(3)] == [
            "Reservation",
            "Reservation",
            "Refusal",
        ]
        assert rig.send().code == CAP_REACHED

    def test_no_cap_is_a_not_configured_state(self):
        rig = Rig()
        rig.caps.remove("alpha")
        assert rig.quotas.status("alpha").state == "not_configured"

    def test_an_unreadable_store_says_unavailable(self):
        rig = Rig()
        rig.limits._counter = None  # type: ignore[assignment]
        assert rig.quotas.status("alpha").state == "unavailable"

    def test_sql_counter_gives_the_usage_per_channel_and_day(self, engine):
        counter = SqlSendCounter(engine)
        counter.try_increment("alpha", "email", date(2026, 5, 1), 5)
        counter.try_increment("alpha", "email", date(2026, 5, 2), 5)
        counter.try_increment("beta", "email", date(2026, 5, 2), 5)
        assert counter.usage_between("alpha", date(2026, 5, 1), date(2026, 6, 1)) == {
            ("email", date(2026, 5, 1)): 1,
            ("email", date(2026, 5, 2)): 1,
        }


# ---- the API -------------------------------------------------------------------------------------


@pytest.fixture
def fx(tmp_path, engine):
    fixture = Fixture(tmp_path, "alpha", "beta")
    fixture.users.create(editor())  # reaches alpha only
    fixture.users.create(owner())
    application = fixture.application
    application.costs = service(engine)
    rig = Rig(cap=10)
    rig.clock = fixture.clock
    application.quotas = QuotaService(rig.limits, application.notifications, fixture.clock)
    fixture.rig, fixture.engine = rig, engine
    for cid in ("alpha", "beta"):
        application.client_profiles.create(
            ClientProfile(
                client_id=cid,
                login=f"{cid}.login",
                password_hash=hash_password("client-secret-123"),
                whatsapp_number=f"+216 20 000 00{1 if cid == 'alpha' else 2}",
                created_at=NOW,
            )
        )
    return fixture


def session(fx, who, role="admin") -> TestClient:
    s = TestClient(fx.client.app, base_url="https://testserver")
    if role == "admin":
        assert (
            s.post("/admin/v1/auth/login", json={"username": who, "password": PASSWORD}).status_code
            == 200
        )
    else:
        r = s.post(
            "/client/v1/auth/login", json={"login": f"{who}.login", "password": "client-secret-123"}
        )
        assert r.status_code == 200, r.text
    return s


RATE = {
    "provider": "meta_whatsapp",
    "key": "message_sent",
    "unit": "message",
    "unit_price": "0.05",
    "currency": "eur",
}


class TestApi:
    def test_rates_are_the_owners_and_the_change_is_audited(self, fx):
        root = session(fx, "root")
        listed = root.get("/admin/v1/settings/costs/rates").json()
        assert listed["rates"] == [] and listed["missing"] == ["meta_whatsapp/message_sent"]
        assert (
            session(fx, "alice").put("/admin/v1/settings/costs/rates", json=RATE).status_code == 403
        )
        assert root.put("/admin/v1/settings/costs/rates", json=RATE).status_code == 200
        assert (
            root.put(
                "/admin/v1/settings/costs/rates", json={**RATE, "unit_price": "-1"}
            ).status_code
            == 422
        )
        assert (
            root.put(
                "/admin/v1/settings/costs/rates", json={**RATE, "provider": "acme"}
            ).status_code
            == 422
        )
        again = root.get("/admin/v1/settings/costs/rates").json()
        assert again["missing"] == [] and again["rates"][0]["updated_by"] == "root"
        audit = {i["action"]: i for i in root.get("/admin/v1/audit").json()["items"]}
        assert audit["settings.cost_rate_set"]["after"]["unit_price"] == "0.05"
        assert (
            root.delete("/admin/v1/settings/costs/rates/meta_whatsapp/message_sent").status_code
            == 200
        )
        assert (
            root.delete("/admin/v1/settings/costs/rates/meta_whatsapp/message_sent").status_code
            == 404
        )

    def test_the_cost_report_states_the_basis_and_respects_client_access(self, fx):
        seed_usage(fx.engine, "alpha")
        seed_delivery(fx.engine, "alpha", "m1")
        root = session(fx, "root")
        body = root.get(
            "/admin/v1/costs", params={"period": "2026-05", "client_id": "alpha"}
        ).json()
        by = {p["provider"]: p for p in body["providers"]}
        assert by["openai"]["basis"] == "measured" and by["openai"]["totals"] == {"USD": "0.007500"}
        assert by["meta_whatsapp"]["basis"] == "unavailable" and by["meta_whatsapp"]["totals"] == {}
        assert body["complete"] is False
        assert root.get("/admin/v1/costs", params={"period": "May"}).status_code == 422
        alice = session(fx, "alice")
        assert (
            alice.get(
                "/admin/v1/costs", params={"period": "2026-05", "client_id": "alpha"}
            ).status_code
            == 200
        )
        assert (
            alice.get(
                "/admin/v1/costs", params={"period": "2026-05", "client_id": "beta"}
            ).status_code
            == 404
        )
        assert alice.get("/admin/v1/costs", params={"period": "2026-05"}).status_code == 403
        assert fx.client.get("/admin/v1/costs", params={"period": "2026-05"}).status_code == 401

    def test_entries_can_be_added_and_removed_by_the_owner(self, fx):
        root = session(fx, "root")
        body = {
            "provider": "ovh",
            "category": "hosting",
            "period": "2026-05",
            "unit": "month",
            "amount": "30",
            "currency": "eur",
            "basis": "estimated",
        }
        made = root.post("/admin/v1/costs/entries", json=body)
        assert made.status_code == 201 and made.json()["source"] == "admin"
        assert (
            root.post("/admin/v1/costs/entries", json={**body, "basis": "guess"}).status_code == 422
        )
        costs = root.get("/admin/v1/costs", params={"period": "2026-05"}).json()
        assert {p["provider"]: p["basis"] for p in costs["providers"]}["ovh"] == "estimated"
        assert root.delete(f"/admin/v1/costs/entries/{made.json()['id']}").status_code == 200
        assert root.delete("/admin/v1/costs/entries/999").status_code == 404
        assert (
            root.post("/admin/v1/costs/materialize", json={"period": "2026-05"}).status_code == 200
        )

    def test_the_client_sees_only_its_own_usage_and_nothing_technical(self, fx):
        for _ in range(8):
            fx.rig.send("alpha")
        fx.rig.caps.set("beta", 10, by="root", at=NOW)
        for _ in range(2):
            fx.rig.send("beta")
        alpha = session(fx, "alpha", "client").get("/client/v1/usage")
        body = alpha.json()
        assert (
            body["client_id"] == "alpha" and body["daily_used"] == 8 and body["state"] == "warning"
        )
        assert body["usage_basis"] == "measured" and body["credits_basis"] == "unavailable"
        assert body["message"] and body["action"]
        assert "admin_cap" not in {k for k, v in body.items() if v is not None}
        assert fx.client.get("/client/v1/usage").status_code == 401
        beta = session(fx, "beta", "client").get("/client/v1/usage").json()
        assert beta["client_id"] == "beta" and beta["daily_used"] == 2

    def test_the_admin_reads_a_clients_quota_within_its_scope(self, fx):
        fx.rig.send("alpha")
        alice = session(fx, "alice")
        body = alice.get("/admin/v1/clients/alpha/quota").json()
        assert body["admin_cap"] == 10 and body["daily_limit"] == 10 and body["daily_used"] == 1
        assert alice.get("/admin/v1/clients/beta/quota").status_code == 404
