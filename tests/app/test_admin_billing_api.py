"""Admin API: generating, reading, filtering and settling monthly invoices."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_admin_clients_api import as_alice, as_root, fx  # noqa: F401 - fx is a fixture

from sales_app.billing import InMemoryInvoiceStore
from sales_app.plans import InMemoryPlanStore, Plan

BASE = "/admin/v1/billing/invoices"
APRIL = "2026-04"  # the fixture's clock is 2026-05-01: April has just ended


def at(month, day):
    return datetime(2026, month, day, tzinfo=UTC)


def make_plan(plan_id="pro", price="30.00", currency="USD", credits=1000, quota=50, active=True):
    return Plan(
        plan_id,
        plan_id.title(),
        Decimal(price),
        currency,
        credits,
        quota,
        active,
        at(1, 1),
        at(1, 1),
        "r",
    )


@pytest.fixture(autouse=True)
def with_billing(fx):  # noqa: F811
    fx.application.plans = InMemoryPlanStore()
    fx.application.invoices = InMemoryInvoiceStore()
    fx.application.plans.create_plan(make_plan())
    fx.application.plans.create_plan(make_plan("eu", price="40.00", currency="EUR"))


def put_on(fx, client, plan="pro", when=None):  # noqa: F811
    when = when or at(3, 1)
    fx.application.plans.assign(client, plan, at=when, by="seed")


def generate(client, period=APRIL):
    return client.post(f"{BASE}/generate", json={"period": period})


class TestGenerate:
    def test_it_requires_an_owner(self, fx):  # noqa: F811
        assert fx.client.post(f"{BASE}/generate", json={"period": APRIL}).status_code == 401
        assert generate(as_alice(fx)).status_code == 403

    def test_it_invoices_every_client_that_was_on_a_plan_that_month(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        put_on(fx, "beta", "eu")
        body = generate(as_root(fx)).json()
        assert body["period"] == APRIL and body["already_invoiced"] == 0 and body["skipped"] == []
        got = {i["client_id"]: i for i in body["created"]}
        assert set(got) == {"alpha", "beta"}
        alpha = got["alpha"]
        assert (alpha["amount"], alpha["currency"], alpha["status"]) == ("30.00", "USD", "PENDING")
        assert (alpha["number"], alpha["created_by"]) == ("INV-2026-04-alpha", "root")
        assert alpha["lines"][0]["days"] == 30 and alpha["lines"][0]["plan_name"] == "Pro"
        assert got["beta"]["currency"] == "EUR" and got["beta"]["amount"] == "40.00"

    def test_a_client_that_joined_mid_month_is_prorated(self, fx):  # noqa: F811
        put_on(fx, "alpha", when=at(4, 16))  # days 16..30 = 15 of 30
        [created] = generate(as_root(fx)).json()["created"]
        assert (created["amount"], created["lines"][0]["days"]) == ("15.00", 15)

    def test_a_client_that_changed_plan_gets_one_line_per_plan(self, fx):  # noqa: F811
        put_on(fx, "alpha", "pro", at(3, 1))
        put_on(fx, "alpha", "eu", at(4, 11))
        # EUR after USD in the same month cannot be added up: reported, not invoiced
        body = generate(as_root(fx)).json()
        assert body["created"] == []
        assert body["skipped"] == [
            {"client_id": "alpha", "reason": "plans in different currencies during the month"}
        ]

    def test_a_client_without_a_plan_that_month_gets_nothing(self, fx):  # noqa: F811
        put_on(fx, "alpha", when=at(5, 1))
        fx.application.plans.assign("beta", "pro", at=at(3, 1), by="s")
        fx.application.plans.assign("beta", None, at=at(4, 1), by="s")
        assert generate(as_root(fx)).json()["created"] == []

    def test_generating_again_never_touches_an_existing_invoice(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        client = as_root(fx)
        first = generate(client).json()["created"][0]
        client.put(f"{BASE}/{first['invoice_id']}/status", json={"status": "PAID"})
        again = generate(client).json()
        assert again["created"] == [] and again["already_invoiced"] == 1
        kept = client.get(f"{BASE}/{first['invoice_id']}").json()
        assert kept["status"] == "PAID" and kept["amount"] == "30.00"

    def test_an_unfinished_or_malformed_month_is_refused(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        client = as_root(fx)
        for period in ("2026-05", "2027-01", "2026-13", "April"):
            assert generate(client, period).status_code == 422
        assert client.get(BASE).json() == {"invoices": []}

    def test_without_a_database_it_says_so(self, fx):  # noqa: F811
        fx.application.invoices = None
        assert generate(as_root(fx)).status_code == 503
        assert as_root(fx).get(BASE).status_code == 503


class TestHistoryDoesNotMove:
    def test_editing_or_deactivating_a_plan_changes_no_existing_invoice(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        client = as_root(fx)
        made = generate(client).json()["created"][0]
        fx.application.plans.update_plan(
            make_plan(price="999", currency="EUR", credits=1, quota=1, active=False)
        )
        fx.application.plans.assign("alpha", None, at=at(5, 1), by="seed")
        kept = client.get(f"{BASE}/{made['invoice_id']}").json()
        assert kept == made
        assert kept["lines"][0]["monthly_price"] == "30.00" and kept["currency"] == "USD"


class TestRead:
    def seed(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        put_on(fx, "beta")
        client = as_root(fx)
        generate(client, APRIL)
        generate(client, "2026-03")
        return client

    def test_it_requires_authentication(self, fx):  # noqa: F811
        assert fx.client.get(BASE).status_code == 401
        assert fx.client.get(f"{BASE}/1").status_code == 401

    def test_the_list_filters_by_client_period_and_status(self, fx):  # noqa: F811
        client = self.seed(fx)
        listing = lambda **q: [
            (i["client_id"], i["period"]) for i in client.get(BASE, params=q).json()["invoices"]
        ]
        assert listing() == [
            ("alpha", APRIL),
            ("beta", APRIL),
            ("alpha", "2026-03"),
            ("beta", "2026-03"),
        ]
        assert listing(client_id="beta") == [("beta", APRIL), ("beta", "2026-03")]
        assert listing(period="2026-03") == [("alpha", "2026-03"), ("beta", "2026-03")]
        assert listing(period_from=APRIL) == [("alpha", APRIL), ("beta", APRIL)]
        assert listing(period_to="2026-03", client_id="alpha") == [("alpha", "2026-03")]
        first = client.get(BASE, params={"client_id": "alpha", "period": APRIL}).json()["invoices"][
            0
        ]
        client.put(f"{BASE}/{first['invoice_id']}/status", json={"status": "FOR_FREE"})
        assert listing(status="FOR_FREE") == [("alpha", APRIL)]
        assert len(listing(status="PENDING")) == 3
        assert listing(status="PAID") == []

    def test_a_bad_filter_is_a_422(self, fx):  # noqa: F811
        client = as_root(fx)
        assert client.get(BASE, params={"period": "2026-13"}).status_code == 422
        assert client.get(BASE, params={"status": "LATE"}).status_code == 422

    def test_a_scoped_admin_sees_only_the_invoices_of_clients_it_reaches(self, fx):  # noqa: F811
        self.seed(fx)
        beta = as_root(fx).get(BASE, params={"client_id": "beta"}).json()["invoices"][0]
        alice = as_alice(fx)  # reaches alpha (and naga), not beta; same cookie jar: root is out
        seen = alice.get(BASE).json()["invoices"]
        assert {i["client_id"] for i in seen} == {"alpha"}
        assert alice.get(BASE, params={"client_id": "beta"}).json() == {"invoices": []}
        assert alice.get(f"{BASE}/{seen[0]['invoice_id']}").status_code == 200
        assert alice.get(f"{BASE}/{beta['invoice_id']}").status_code == 404
        assert alice.get(f"{BASE}/9999").status_code == 404


class TestSettle:
    def one(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        client = as_root(fx)
        return client, generate(client).json()["created"][0]["invoice_id"]

    @pytest.mark.parametrize("status", ["PAID", "FOR_FREE"])
    def test_an_owner_marks_an_invoice_with_who_and_when(self, fx, status):  # noqa: F811
        client, invoice_id = self.one(fx)
        response = client.put(f"{BASE}/{invoice_id}/status", json={"status": status})
        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["status_changed_by"]) == (status, "root")
        assert body["status_changed_at"] is not None
        assert client.get(f"{BASE}/{invoice_id}").json()["status"] == status

    def test_an_owner_applies_a_100_percent_discount_distinct_from_paid_and_free(self, fx):  # noqa: F811
        client, invoice_id = self.one(fx)
        billed = client.get(f"{BASE}/{invoice_id}").json()["amount"]
        response = client.put(
            f"{BASE}/{invoice_id}/status",
            json={"status": "DISCOUNTED_100", "reason": "launch offer"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "DISCOUNTED_100" and body["collected"] is False
        assert body["amount"] == billed  # billed amount, before discount
        assert float(body["amount_after_discount"]) == 0 and body["discount_percent"] == 100
        assert body["discount_amount"] == billed and body["free_amount"] == billed
        assert body["discount_reason"] == "launch offer"
        assert body["status_changed_by"] == "root" and body["status_changed_at"] is not None
        events = client.get(f"/admin/v1/billing/payment-events?invoice_id={invoice_id}").json()["events"]
        assert [(e["event_status"], e["actor"]) for e in events] == [("DISCOUNTED_100", "root")]
        # never modified again, and a payment is not collected on it
        for status in ("PAID", "FOR_FREE", "DISCOUNTED_100"):
            assert (
                client.put(f"{BASE}/{invoice_id}/status", json={"status": status}).status_code == 409
            )
        assert client.get(f"{BASE}/{invoice_id}").json()["status"] == "DISCOUNTED_100"

    def test_a_paid_invoice_is_never_discounted_and_only_an_owner_discounts(self, fx):  # noqa: F811
        client, invoice_id = self.one(fx)
        client.put(f"{BASE}/{invoice_id}/status", json={"status": "PAID"})
        body = {"status": "DISCOUNTED_100"}
        assert client.put(f"{BASE}/{invoice_id}/status", json=body).status_code == 409
        paid = client.get(f"{BASE}/{invoice_id}").json()
        assert paid["status"] == "PAID" and paid["discount_percent"] == 0 and paid["collected"] is True
        assert as_alice(fx).put(f"{BASE}/{invoice_id}/status", json=body).status_code == 403

    def test_a_settled_invoice_cannot_be_changed_again(self, fx):  # noqa: F811
        client, invoice_id = self.one(fx)
        client.put(f"{BASE}/{invoice_id}/status", json={"status": "PAID"})
        for status in ("FOR_FREE", "PAID", "PENDING"):
            assert (
                client.put(f"{BASE}/{invoice_id}/status", json={"status": status}).status_code
                == 409
            )
        assert client.get(f"{BASE}/{invoice_id}").json()["status"] == "PAID"

    def test_pending_is_not_a_target_and_an_unknown_status_is_refused(self, fx):  # noqa: F811
        client, invoice_id = self.one(fx)
        assert (
            client.put(f"{BASE}/{invoice_id}/status", json={"status": "PENDING"}).status_code == 409
        )
        assert client.put(f"{BASE}/{invoice_id}/status", json={"status": "LATE"}).status_code == 422

    def test_only_an_owner_settles_and_an_unknown_invoice_is_a_404(self, fx):  # noqa: F811
        put_on(fx, "alpha")
        invoice_id = generate(as_root(fx)).json()["created"][0]["invoice_id"]
        fx.client.cookies.clear()
        status = {"status": "PAID"}
        assert fx.client.put(f"{BASE}/{invoice_id}/status", json=status).status_code == 401
        assert as_alice(fx).put(f"{BASE}/{invoice_id}/status", json=status).status_code == 403
        client = as_root(fx)
        assert client.put(f"{BASE}/9999/status", json=status).status_code == 404
        assert client.get(f"{BASE}/{invoice_id}").json()["status"] == "PENDING"
