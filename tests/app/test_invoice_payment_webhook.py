# ruff: noqa: F401, F811, C408  (the fixtures are imported to be used by name)
"""Reliable payment confirmation: signature, idempotence, mismatches, isolation, persistence, and the
conflict between a manual and an electronic settlement."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_admin_billing_api import APRIL, BASE, generate, make_plan, put_on
from test_admin_clients_api import as_alice, as_root, fx, fx_secrets

from sales_app.billing import (
    InMemoryInvoiceStore,
    InvoiceFilter,
    InvoiceStatus,
    PaymentOrigin,
    PaymentOutcome,
    build_invoice,
)
from sales_app.payment_provider import (
    FakePaymentProvider,
    InvalidSignatureError,
    MalformedPaymentEventError,
    PaymentEvent,
    PaymentStatus,
    SignedWebhookProvider,
    sign,
)
from sales_app.persistence.database import create_database_engine
from sales_app.persistence.invoice_rows import SqlInvoiceStore
from sales_app.persistence.schema import upgrade_database
from sales_app.plans import InMemoryPlanStore, PlanAssignment

SECRET = "s3cret-s3cret-s3cret"
WEBHOOK = "/api/payments/webhook"
NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


def body_of(**over):
    data = {
        "event_id": "evt_1",
        "invoice": "INV-2026-04-alpha",
        "amount": "30.00",
        "currency": "USD",
        "transaction_id": "txn_1",
        "status": "succeeded",
    }
    data.update(over)
    return json.dumps(data).encode()


def signed_headers(body, *, secret=SECRET, when=NOW):
    ts = str(int(when.timestamp()))
    return {"X-Payment-Timestamp": ts, "X-Payment-Signature": sign(secret, ts, body)}


def event(**over):
    base = dict(
        event_id="evt_1",
        invoice_reference="INV-2026-04-alpha",
        amount=Decimal("30.00"),
        currency="USD",
        transaction_id="txn_1",
        status=PaymentStatus.SUCCEEDED,
    )
    base.update(over)
    return PaymentEvent(**base)


# ---- the signed-webhook adapter -----------------------------------------------------------------


class TestSignedWebhookProvider:
    provider = SignedWebhookProvider(SECRET)

    def test_a_valid_signature_gives_the_normalised_event(self):
        body = body_of()
        got = self.provider.parse_webhook(body, signed_headers(body), now=NOW)
        assert got == event()

    def test_header_names_are_case_insensitive(self):
        body = body_of()
        headers = {k.lower(): v for k, v in signed_headers(body).items()}
        assert self.provider.parse_webhook(body, headers, now=NOW).event_id == "evt_1"

    @pytest.mark.parametrize("secret", ["another-secret-another", ""])
    def test_a_wrong_secret_is_refused(self, secret):
        body = body_of()
        with pytest.raises(InvalidSignatureError):
            self.provider.parse_webhook(body, signed_headers(body, secret=secret or "x"), now=NOW)

    def test_a_tampered_body_is_refused(self):
        headers = signed_headers(body_of())
        with pytest.raises(InvalidSignatureError):
            self.provider.parse_webhook(body_of(amount="1.00"), headers, now=NOW)

    @pytest.mark.parametrize("missing", ["X-Payment-Timestamp", "X-Payment-Signature"])
    def test_a_missing_header_is_refused(self, missing):
        body = body_of()
        headers = signed_headers(body)
        del headers[missing]
        with pytest.raises(InvalidSignatureError):
            self.provider.parse_webhook(body, headers, now=NOW)

    def test_a_non_ascii_signature_is_refused_not_crashed(self):
        body = body_of()
        headers = {**signed_headers(body), "X-Payment-Signature": "é" * 64}
        with pytest.raises(InvalidSignatureError):
            self.provider.parse_webhook(body, headers, now=NOW)

    def test_a_stale_timestamp_is_refused_even_if_signed(self):
        body = body_of()
        old = datetime(2026, 5, 1, 11, tzinfo=UTC)
        with pytest.raises(InvalidSignatureError):
            self.provider.parse_webhook(body, signed_headers(body, when=old), now=NOW)

    @pytest.mark.parametrize(
        "over",
        [
            {"amount": "abc"},
            {"amount": "10.005"},
            {"amount": -1},
            {"amount": 1.5},
            {"currency": "usd"},
            {"status": "pending"},
            {"event_id": ""},
            {"invoice": "nope"},
        ],
    )
    def test_an_authentic_but_malformed_event_is_refused(self, over):
        body = body_of(**over)
        with pytest.raises(MalformedPaymentEventError):
            self.provider.parse_webhook(body, signed_headers(body), now=NOW)

    def test_the_body_must_be_a_json_object(self):
        for body in (b"not json", b"[1]"):
            with pytest.raises(MalformedPaymentEventError):
                self.provider.parse_webhook(body, signed_headers(body), now=NOW)

    def test_the_fake_provider_trusts_only_its_header(self):
        fake = FakePaymentProvider()
        assert fake.parse_webhook(body_of(), {"x-fake-signature": "valid"}, now=NOW) == event()
        with pytest.raises(InvalidSignatureError):
            fake.parse_webhook(body_of(), {}, now=NOW)

    def test_a_provider_needs_a_secret(self):
        with pytest.raises(ValueError):
            SignedWebhookProvider("")


# ---- the store contract (memory and SQL) --------------------------------------------------------


def make_invoice(client="alpha", period="2026-04", price="30"):
    a = PlanAssignment(
        client_id=client,
        plan_id="pro",
        plan_name="Pro",
        monthly_price=Decimal(price),
        currency="USD",
        included_credits=1,
        daily_quota=1,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        assigned_by="root",
    )
    return build_invoice(client, period, [a], now=NOW, created_by="root")


@pytest.fixture(params=["memory", "sql"])
def store(request):
    if request.param == "memory":
        s = InMemoryInvoiceStore()
    else:
        engine = create_database_engine("sqlite://")
        upgrade_database(engine)
        s = SqlInvoiceStore(engine)
    s.create(make_invoice("alpha"))
    s.create(make_invoice("beta"))
    return s


def alpha(store):
    [found] = store.list_invoices(InvoiceFilter(client_id="alpha"))
    return found


def pay(store, **over):
    return store.process_payment(event(**over), at=NOW, by="payment:test")


def test_a_succeeded_event_marks_the_invoice_paid_electronically(store):
    result = pay(store)
    assert result.outcome is PaymentOutcome.APPLIED
    paid = alpha(store)
    assert paid.status is InvoiceStatus.PAID
    assert paid.payment_origin is PaymentOrigin.ELECTRONIC
    assert (paid.payment_transaction_id, paid.payment_event_id) == ("txn_1", "evt_1")
    assert (paid.status_changed_at, paid.status_changed_by) == (NOW, "payment:test")


def test_replaying_the_same_event_or_the_same_transaction_changes_nothing(store):
    pay(store)
    before = alpha(store)
    assert pay(store).outcome is PaymentOutcome.DUPLICATE  # same event id
    assert pay(store, event_id="evt_2").outcome is PaymentOutcome.DUPLICATE  # same transaction
    assert alpha(store) == before
    ids = [r.event_id for r in store.payment_events(before.invoice_id)]
    assert ids == ["evt_2", "evt_1"]  # the new id is traced once, the replayed id is not again


@pytest.mark.parametrize(
    ("over", "outcome"),
    [
        ({"amount": Decimal("29.99")}, PaymentOutcome.AMOUNT_MISMATCH),
        ({"amount": Decimal("31.00")}, PaymentOutcome.AMOUNT_MISMATCH),
        ({"currency": "EUR"}, PaymentOutcome.CURRENCY_MISMATCH),
        ({"invoice_reference": "INV-2026-04-ghost"}, PaymentOutcome.UNKNOWN_INVOICE),
        ({"invoice_reference": "garbage"}, PaymentOutcome.UNKNOWN_INVOICE),
    ],
)
def test_a_mismatch_is_refused_traced_and_pays_nothing(store, over, outcome):
    assert pay(store, **over).outcome is outcome
    assert alpha(store).status is InvoiceStatus.PENDING
    assert alpha(store).payment_transaction_id is None
    [record] = store.payment_events()
    assert (record.event_id, record.outcome, record.source) == ("evt_1", outcome.value, "webhook")


def test_a_failed_event_marks_failed_and_a_later_success_still_pays(store):
    assert pay(store, status=PaymentStatus.FAILED).outcome is PaymentOutcome.FAILURE_RECORDED
    assert alpha(store).status is InvoiceStatus.FAILED
    assert pay(store, event_id="evt_9", status=PaymentStatus.FAILED).outcome is (
        PaymentOutcome.FAILURE_RECORDED
    )
    assert pay(store, event_id="evt_2", transaction_id="txn_2").outcome is PaymentOutcome.APPLIED
    assert alpha(store).status is InvoiceStatus.PAID


def test_a_failure_never_undoes_a_payment(store):
    pay(store)
    result = pay(store, event_id="evt_3", transaction_id="txn_3", status=PaymentStatus.FAILED)
    assert result.outcome is PaymentOutcome.ALREADY_SETTLED
    assert alpha(store).status is InvoiceStatus.PAID


def test_clients_are_isolated_a_transaction_cannot_pay_two_invoices(store):
    pay(store)
    other = pay(store, event_id="evt_2", invoice_reference="INV-2026-04-beta")
    assert other.outcome is PaymentOutcome.TRANSACTION_REUSED
    [beta] = store.list_invoices(InvoiceFilter(client_id="beta"))
    assert beta.status is InvoiceStatus.PENDING and beta.payment_origin is None


def test_paying_one_client_leaves_the_others_pending(store):
    pay(store)
    [beta] = store.list_invoices(InvoiceFilter(client_id="beta"))
    assert beta.status is InvoiceStatus.PENDING


def test_a_manual_settlement_is_traced_and_blocks_a_later_electronic_payment(store):
    invoice = alpha(store)
    store.settle(invoice.invoice_id, InvoiceStatus.PAID, at=NOW, by="root")
    manual = alpha(store)
    assert manual.payment_origin is PaymentOrigin.MANUAL
    assert (manual.status_changed_by, manual.payment_transaction_id) == ("root", None)
    assert pay(store).outcome is PaymentOutcome.ALREADY_SETTLED
    assert alpha(store) == manual
    sources = {r.source: r for r in store.payment_events(invoice.invoice_id)}
    assert sources["manual"].actor == "root" and sources["manual"].outcome == "applied"
    assert sources["webhook"].outcome == "already_settled"


def test_a_manual_payment_can_follow_a_failed_one_and_an_electronic_one_cannot_be_redone(store):
    from sales_app.billing import InvoiceStatusError

    pay(store, status=PaymentStatus.FAILED)
    invoice = alpha(store)
    store.settle(invoice.invoice_id, InvoiceStatus.PAID, at=NOW, by="root")
    with pytest.raises(InvoiceStatusError):
        store.settle(invoice.invoice_id, InvoiceStatus.FOR_FREE, at=NOW, by="root")

    [beta] = store.list_invoices(InvoiceFilter(client_id="beta"))
    pay(store, event_id="e5", invoice_reference=beta.number, transaction_id="t5")
    with pytest.raises(InvoiceStatusError, match="electronically"):
        store.settle(beta.invoice_id, InvoiceStatus.PAID, at=NOW, by="root")


def test_overdue_is_computed_from_the_end_of_the_month(store):
    invoice = alpha(store)  # April: due 15 days after 2026-05-01
    assert not invoice.is_overdue(datetime(2026, 5, 15, 23, tzinfo=UTC))
    assert invoice.is_overdue(datetime(2026, 5, 16, tzinfo=UTC))
    pay(store, status=PaymentStatus.FAILED)
    assert alpha(store).is_overdue(datetime(2026, 6, 1, tzinfo=UTC))
    pay(store, event_id="e2", transaction_id="t2")
    assert not alpha(store).is_overdue(datetime(2026, 6, 1, tzinfo=UTC))


# ---- persistence --------------------------------------------------------------------------------


def test_the_confirmation_survives_a_restart_and_the_database_refuses_replays(tmp_path):
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    first = SqlInvoiceStore(engine)
    first.create(make_invoice())
    first.process_payment(event(), at=NOW, by="payment:test")
    engine.dispose()

    second = SqlInvoiceStore(create_database_engine(url))  # a new process
    [paid] = second.list_invoices(InvoiceFilter())
    assert paid.status is InvoiceStatus.PAID and paid.payment_event_id == "evt_1"
    assert second.process_payment(event(), at=NOW, by="x").outcome is PaymentOutcome.DUPLICATE

    from sqlalchemy import text

    with pytest.raises(Exception, match="(?i)unique"), second._engine.begin() as connection:
        connection.execute(
            text(
                "insert into payment_events (event_id, source, event_status, outcome, actor,"
                " received_at) values ('evt_1', 'webhook', 'succeeded', 'applied', 'x',"
                " '2026-05-01 00:00:00')"
            )
        )


def test_0012_adds_the_columns_and_the_table_and_marks_paid_invoices_manual():
    from sqlalchemy import inspect, text

    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0011")
    s = SqlInvoiceStore(engine)
    s.create(make_invoice())
    with engine.begin() as connection:
        connection.execute(text("update invoices set status = 'PAID'"))
    upgrade_database(engine, "0012")
    names = {c["name"] for c in inspect(engine).get_columns("invoices")}
    assert {"payment_origin", "payment_transaction_id", "payment_event_id"} <= names
    assert "payment_events" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("select payment_origin from invoices")).scalar() == "manual"


# ---- the HTTP surface ---------------------------------------------------------------------------


@pytest.fixture
def api(fx):
    fx.application.plans = InMemoryPlanStore()
    fx.application.invoices = InMemoryInvoiceStore()
    fx.application.plans.create_plan(make_plan())
    put_on(fx, "alpha")
    put_on(fx, "beta")
    assert generate(as_root(fx)).status_code == 200
    fx.client.cookies.clear()
    fx.application.payment_provider = lambda: SignedWebhookProvider(SECRET)
    return fx


def post(fx, body, headers=None):
    return fx.client.post(WEBHOOK, content=body, headers=headers or signed_headers(body))


def now_headers(fx, body, secret=SECRET):
    return signed_headers(body, secret=secret, when=fx.clock.now())


class TestWebhookRoute:
    def test_without_a_configured_provider_it_says_so(self, fx):
        fx.application.invoices = InMemoryInvoiceStore()
        response = fx.client.post(WEBHOOK, content=b"{}")  # the default resolver: no secret stored
        assert response.status_code == 409
        assert "no payment provider configured" in response.text

    def test_an_unsigned_or_wrongly_signed_request_is_a_bare_401(self, api):
        body = body_of()
        for headers in ({}, now_headers(api, body, secret="wrong-wrong-wrong-wrong")):
            response = post(api, body, headers)
            assert response.status_code == 401
            assert "alpha" not in response.text and "INV" not in response.text
        listing = as_root(api).get(BASE).json()["invoices"]
        assert {i["status"] for i in listing} == {"PENDING"}

    def test_a_signed_payment_marks_the_invoice_paid_electronically(self, api):
        body = body_of()
        response = post(api, body, now_headers(api, body))
        assert (response.status_code, response.json()) == (200, {"status": "applied"})
        [paid] = as_root(api).get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        assert paid["status"] == "PAID" and paid["payment_origin"] == "electronic"
        assert (paid["payment_transaction_id"], paid["payment_event_id"]) == ("txn_1", "evt_1")
        assert paid["overdue"] is False

    def test_a_replay_is_a_harmless_200(self, api):
        body = body_of()
        post(api, body, now_headers(api, body))
        again = post(api, body, now_headers(api, body))
        assert (again.status_code, again.json()) == (200, {"status": "duplicate"})

    def test_a_mismatch_is_a_200_rejection_that_pays_nothing_and_is_traced(self, api):
        body = body_of(amount="1.00")
        response = post(api, body, now_headers(api, body))
        assert (response.status_code, response.json()) == (200, {"status": "rejected"})
        root = as_root(api)
        [alpha_invoice] = root.get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        assert alpha_invoice["status"] == "PENDING"
        events = root.get(f"{BASE.rsplit('/', 1)[0]}/payment-events").json()["events"]
        assert [(e["event_id"], e["outcome"]) for e in events] == [("evt_1", "amount_mismatch")]

    def test_a_failed_payment_marks_failed(self, api):
        body = body_of(status="failed")
        assert post(api, body, now_headers(api, body)).json() == {"status": "failed_recorded"}
        [i] = as_root(api).get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        assert i["status"] == "FAILED"

    def test_a_malformed_authentic_body_is_a_400(self, api):
        body = body_of(amount="abc")
        assert post(api, body, now_headers(api, body)).status_code == 400

    def test_an_oversized_body_is_refused(self, api):
        body = b"x" * 70000
        assert post(api, body, now_headers(api, body)).status_code == 400

    def test_the_webhook_needs_no_admin_session(self, api):
        body = body_of()
        assert api.client.cookies.get("sales_admin_session") is None
        assert post(api, body, now_headers(api, body)).status_code == 200

    def test_the_fake_provider_plugs_in(self, api):
        api.application.payment_provider = lambda: FakePaymentProvider()
        response = post(api, body_of(), {"x-fake-signature": "valid"})
        assert response.json() == {"status": "applied"}

    def test_a_secret_stored_by_the_admin_turns_the_webhook_on(self, fx_secrets):
        fx = fx_secrets
        fx.application.invoices = InMemoryInvoiceStore()
        fx.application.plans = InMemoryPlanStore()
        assert post(fx, b"{}", {}).status_code == 409
        fx.application.credentials.save("global", "payments.webhook_secret", SECRET, actor="root")
        assert post(fx, b"{}", {}).status_code == 401  # configured now: a signature is required
        status = [
            s
            for s in fx.application.credentials.describe("global")
            if s.key == "payments.webhook_secret"
        ]
        assert status and SECRET not in repr(status)  # a status, never the value


class TestManualVersusElectronic:
    def test_a_manual_settlement_keeps_who_when_and_origin(self, api):
        root = as_root(api)
        [i] = root.get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        done = root.put(f"{BASE}/{i['invoice_id']}/status", json={"status": "PAID"}).json()
        assert (done["payment_origin"], done["status_changed_by"]) == ("manual", "root")
        assert done["status_changed_at"] is not None
        events = root.get(f"{BASE.rsplit('/', 1)[0]}/payment-events").json()["events"]
        assert [(e["source"], e["actor"], e["event_status"]) for e in events] == [
            ("manual", "root", "PAID")
        ]

    def test_an_electronically_paid_invoice_refuses_a_manual_marking_with_409(self, api):
        body = body_of()
        post(api, body, now_headers(api, body))
        root = as_root(api)
        [i] = root.get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        for status in ("PAID", "FOR_FREE"):
            response = root.put(f"{BASE}/{i['invoice_id']}/status", json={"status": status})
            assert response.status_code == 409
            assert "paid electronically" in response.json()["detail"]

    def test_a_manually_paid_invoice_ignores_a_later_electronic_payment(self, api):
        root = as_root(api)
        [i] = root.get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        root.put(f"{BASE}/{i['invoice_id']}/status", json={"status": "PAID"})
        body = body_of()
        assert post(api, body, now_headers(api, body)).json() == {"status": "rejected"}
        [after] = root.get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        assert after["payment_origin"] == "manual" and after["payment_transaction_id"] is None

    def test_a_failed_invoice_can_still_be_marked_paid_by_hand(self, api):
        body = body_of(status="failed")
        post(api, body, now_headers(api, body))
        root = as_root(api)
        [i] = root.get(BASE, params={"client_id": "alpha"}).json()["invoices"]
        assert (
            root.put(f"{BASE}/{i['invoice_id']}/status", json={"status": "PAID"}).status_code == 200
        )

    def test_payment_events_are_scoped_to_the_clients_an_admin_reaches(self, api):
        body = body_of(invoice="INV-2026-04-beta", transaction_id="txn_b", event_id="evt_b")
        post(api, body, now_headers(api, body))
        ghost = body_of(invoice="INV-2026-04-ghost", event_id="evt_g")
        post(api, ghost, now_headers(api, ghost))
        assert (
            len(as_root(api).get(f"{BASE.rsplit('/', 1)[0]}/payment-events").json()["events"]) == 2
        )
        api.client.cookies.clear()
        alice = as_alice(api)  # reaches alpha (and naga) only
        assert alice.get(f"{BASE.rsplit('/', 1)[0]}/payment-events").json()["events"] == []


class TestBillingStartAndGeneration:
    def test_billing_starts_the_day_the_plan_is_assigned_and_generation_never_duplicates(
        self,
        fx,
    ):
        fx.application.plans = InMemoryPlanStore()
        fx.application.invoices = InMemoryInvoiceStore()
        fx.application.plans.create_plan(make_plan())
        put_on(fx, "alpha", when=datetime(2026, 4, 16, tzinfo=UTC))  # 15 of 30 days
        root = as_root(fx)
        first = generate(root).json()
        second = generate(root).json()
        assert [i["amount"] for i in first["created"]] == ["15.00"]
        assert second["created"] == [] and second["already_invoiced"] == 1
        assert len(root.get(BASE).json()["invoices"]) == 1
