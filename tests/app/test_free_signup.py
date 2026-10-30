"""Free sign-up, the 50 free credits, and the backend wall that keeps a Free account from selling.

Real routes on a SQLite database built by the project's migrations. A Free account (signed up by
itself, on no plan) prepares its agent; every commercial entry point refuses it, and the 50 credits do
not change that. A paid plan lifts the wall; clients created before this existed are untouched.
"""

import base64
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app_support import (
    InlineExecutor,
    declare_models,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from fastapi.testclient import TestClient
from test_admin_clients_api import PASSWORD as ADMIN_PASSWORD
from test_admin_clients_api import owner

from sales_app.api import create_app
from sales_app.billing import InvoiceFilter, InvoiceStatus
from sales_app.bootstrap import build_application
from sales_app.free_accounts import FREE_CREDITS
from sales_app.notifications import ADMIN, CLIENT, NotificationQuery
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.plans import Plan
from sales_app.secret_store import generate_master_key
from sales_app.send_cap import UPGRADE_REQUIRED, CommercialGateChannel
from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
SIGNUP = "/client/v1/auth/signup"
PASSWORD = "chosen-pass-123"
BODY = {"name": "Acme Corp", "email": "Owner@Acme.test", "password": PASSWORD}


@pytest.fixture
def world(tmp_path):
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    engine.dispose()
    write_client(tmp_path, "alpha")
    builders, _made = fake_builders(FakeClock(NOW))
    application = build_application(
        make_settings(
            tmp_path,
            database_url=url,
            secrets_dir=tmp_path / "_secrets",
            secrets_key=generate_master_key(),
        ),
        env=env_for("alpha"),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
        refresh_executor=InlineExecutor(),
    )
    declare_models(application.credentials, "gpt-4o")
    application.account_stores.admin_users.create(owner())
    for plan_id, name, price, credits, quota, active in (
        ("starter", "Starter", 49, 100_000, 5_000, True),
        ("business", "Business", 129, 350_000, 15_000, True),
        ("retired", "Retired", 9, 10, 1, False),
    ):
        application.plans.create_plan(
            Plan(plan_id, name, Decimal(price), "USD", credits, quota, active, NOW, NOW, "test")
        )
    yield application
    application.close()


def browser(application) -> TestClient:
    return TestClient(create_app(application, run_scheduler=False), base_url="https://testserver")


def sign_up(application, **change):
    client = browser(application)
    return client, client.post(SIGNUP, json={**BODY, **change})


class TestPublicPlans:
    def test_it_shows_the_active_plans_as_configured_without_signing_in(self, world):
        found = browser(world).get("/client/v1/public/plans")
        assert found.status_code == 200
        plans = found.json()["plans"]
        assert [(p["plan_id"], p["monthly_price"], p["included_credits"], p["daily_quota"]) for p in plans] == [
            ("starter", "49.00", 100_000, 5_000),
            ("business", "129.00", 350_000, 15_000),
        ]


class TestSignup:
    def test_it_creates_a_free_account_with_50_credits_and_signs_in(self, world):
        client, made = sign_up(world)
        assert made.status_code == 201, made.text
        me = client.get("/client/v1/auth/me")  # the cookie opened a session
        assert me.status_code == 200 and me.json()["login"] == "owner@acme.test"
        assert me.json()["name"] == "Acme Corp" and me.json()["must_change_password"] is False
        record = world.commercial_access.free.get("acme-corp")
        assert record is not None and record.credits == FREE_CREDITS == 50
        access = client.get("/client/v1/account/access").json()
        assert (access["tier"], access["free_credits"], access["credits_remaining"]) == ("free", 50, 50)

    def test_the_password_is_hashed_and_the_email_is_unique(self, world):
        sign_up(world)
        stored = world.client_profiles.get_by_login("owner@acme.test")
        assert stored is not None and stored.password_hash != PASSWORD
        _, again = sign_up(world, name="Other Co")
        assert again.status_code == 409
        assert world.client_profiles.get_by_login("owner@acme.test").client_id == "acme-corp"

    @pytest.mark.parametrize(
        "change", [{"email": "not-an-email"}, {"name": "   "}, {"name": "!!!"}]
    )
    def test_invalid_fields_are_refused_and_nothing_is_created(self, world, change):
        _, made = sign_up(world, **change)
        assert made.status_code == 400
        assert not world.client_profiles.list()

    def test_a_short_password_is_refused(self, world):
        assert sign_up(world, password="short")[1].status_code == 422

    def test_a_filled_honeypot_creates_nothing(self, world):
        _, made = sign_up(world, website="http://spam.test")
        assert made.status_code == 201 and not world.client_profiles.list()

    def test_repeated_attempts_from_one_caller_are_limited(self, world):
        client = browser(world)
        statuses = [client.post(SIGNUP, json={**BODY, "email": "bad"}).status_code for _ in range(7)]
        assert statuses[:5] == [400] * 5 and statuses[5:] == [429, 429]

    def test_an_existing_client_is_not_a_free_account(self, world):
        assert world.commercial_access.blocked("alpha") is False
        assert world.agent_may_work("alpha") is False  # not started: its own switch, unchanged


class TestFreeAccountCannotSell:
    def test_starting_the_agent_is_refused_by_the_server(self, world):
        client, _ = sign_up(world)
        refused = client.post("/client/v1/agent/start")
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "upgrade_required"
        assert world.agent_state.get("acme-corp") is None

    def test_other_selling_routes_are_refused_too(self, world):
        client, _ = sign_up(world)
        for call in (
            lambda: client.post("/client/v1/approvals/x/approve"),
            lambda: client.post("/client/v1/conversations/p1/release"),
            lambda: client.post("/client/v1/conversations/p1/messages", json={"text": "hi", "idempotency_key": "key-0001"}),
        ):
            refused = call()
            assert refused.status_code == 403, refused.text
            assert refused.json()["detail"]["code"] == "upgrade_required"
        # rejecting a proposal sends nothing: it is not part of the wall
        assert client.post("/client/v1/approvals/x/reject").status_code != 403

    def test_the_free_credits_do_not_unlock_the_agent_even_if_its_switch_is_on(self, world):
        sign_up(world)
        world.agent_state.set("acme-corp", running=True, by="test", at=NOW)
        assert world.commercial_access.blocked("acme-corp") is True
        assert world.agent_may_work("acme-corp") is False

    def test_no_message_leaves_through_any_channel(self, world):
        sign_up(world)
        sent = []

        class Inner:
            channel = Channel.WHATSAPP

            def send(self, message):
                sent.append(message)

        gate = CommercialGateChannel(Inner(), "acme-corp", lambda cid: not world.commercial_access.blocked(cid))
        message = OutboundMessage(
            client_id="acme-corp",
            prospect_id="p1",
            channel=Channel.WHATSAPP,
            recipient="+21622768307",
            draft=MessageDraft(text="hello", language="en"),
            idempotency_key="k1",
        )
        result = gate.send(message)
        assert sent == [] and result.status is SendStatus.FAILED_TRANSIENT
        assert result.error_code == UPGRADE_REQUIRED

    def test_an_unreadable_rule_fails_closed(self):
        class Inner:
            channel = Channel.EMAIL

            def send(self, message):  # pragma: no cover - must not be reached
                raise AssertionError("sent")

        def broken(_client_id):
            raise RuntimeError("store down")

        message = OutboundMessage(
            client_id="c",
            prospect_id="p",
            channel=Channel.EMAIL,
            recipient="a@b.test",
            draft=MessageDraft(text="hello", language="en"),
            idempotency_key="k",
        )
        assert CommercialGateChannel(Inner(), "c", broken).send(message).status is SendStatus.FAILED_TRANSIENT

    def test_a_paid_plan_lifts_the_wall_and_the_upgrade_banner(self, world):
        client, _ = sign_up(world)
        world.plans.assign("acme-corp", "business", at=NOW, by="admin:root")
        assert world.commercial_access.blocked("acme-corp") is False
        assert client.get("/client/v1/account/access").json()["tier"] == "paid"
        started = client.post("/client/v1/agent/start")
        assert started.status_code == 409  # past the wall: now only the readiness checks speak
        assert "reasons" in started.json()["detail"]




def admin_browser(application) -> TestClient:
    client = browser(application)
    client.post("/admin/v1/auth/login", json={"username": "root", "password": ADMIN_PASSWORD})
    return client


def receipt(kind="application/pdf", data=b"%PDF-1.4 receipt"):
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


class TestNoModelYet:
    def test_a_signed_up_client_works_before_the_admin_assigns_any_model(self, world):
        client, _ = sign_up(world)
        for path in ("/profile", "/settings", "/readiness", "/agent", "/usage", "/subscription"):
            assert client.get(f"/client/v1{path}").status_code == 200, path
        checks = client.get("/client/v1/readiness").json()["checks"]
        assert "model_assigned" in {c["id"] for c in checks if not c["ok"]}  # said plainly

    def test_the_admin_can_create_an_account_without_model_or_whatsapp_number(self, world):
        admin = admin_browser(world)
        made = admin.post(
            "/admin/v1/clients",
            json={
                "display_name": "Bare Co",
                "login": "bare.admin",
                "temporary_password": "temp-pass-123",
                "status": "active",
            },
        )
        assert made.status_code == 201, made.text


class TestPublicPages:
    def test_the_admin_writes_pages_and_visitors_read_only_what_exists(self, world):
        admin, visitor = admin_browser(world), browser(world)
        assert visitor.get("/client/v1/public/pages").json() == {"pages": []}
        assert visitor.get("/client/v1/public/pages/privacy").status_code == 404
        saved = admin.put(
            "/admin/v1/site-pages/privacy/en", json={"title": "Privacy", "body": "We keep little."}
        )
        assert saved.status_code == 200
        admin.put("/admin/v1/site-pages/privacy/fr", json={"title": "Confidentialite", "body": "Peu."})
        assert visitor.get("/client/v1/public/pages").json() == {"pages": ["privacy"]}
        french = visitor.get("/client/v1/public/pages/privacy?locale=fr").json()
        assert french["title"] == "Confidentialite"
        assert visitor.get("/client/v1/public/pages/privacy?locale=ar").json()["title"] == "Privacy"
        admin.put("/admin/v1/site-pages/privacy/fr", json={"title": "", "body": ""})  # emptied
        assert visitor.get("/client/v1/public/pages/privacy?locale=fr").json()["locale"] == "en"

    def test_only_known_pages_are_written_and_the_bank_details_are_not_public(self, world):
        admin, visitor = admin_browser(world), browser(world)
        put = admin.put
        assert put("/admin/v1/site-pages/other/en", json={"title": "x", "body": "y"}).status_code == 404
        assert put("/admin/v1/site-pages/terms/de", json={"title": "x", "body": "y"}).status_code == 404
        assert put("/admin/v1/site-pages/terms/en", json={"title": "", "body": "t"}).status_code == 400
        put("/admin/v1/site-pages/bank/en", json={"title": "Bank", "body": "IBAN TN59 0000"})
        assert visitor.get("/client/v1/public/pages/bank").status_code == 404
        assert visitor.get("/client/v1/public/pages").json() == {"pages": []}
        assert visitor.get("/admin/v1/site-pages").status_code == 401


class TestOfflinePayment:
    def send(self, client, **change):
        return client.post(
            "/client/v1/account/payment-requests",
            json={"plan_id": "business", "reference": "VIR-42", "receipt": receipt(), **change},
        )

    def test_the_client_sees_the_bank_details_once_the_admin_wrote_them(self, world):
        client, _ = sign_up(world)
        assert client.get("/client/v1/account/bank-details").status_code == 404
        admin_browser(world).put(
            "/admin/v1/site-pages/bank/en", json={"title": "Bank", "body": "IBAN TN59 0000"}
        )
        assert client.get("/client/v1/account/bank-details").json()["body"] == "IBAN TN59 0000"
        assert browser(world).get("/client/v1/account/bank-details").status_code == 401

    def test_a_receipt_is_checked_and_one_request_waits_at_a_time(self, world):
        client, _ = sign_up(world)
        assert self.send(client, receipt="data:image/svg+xml;base64,PHN2Zz4=").status_code == 400
        assert self.send(client, receipt=receipt("image/png", b"not a png")).status_code == 400
        assert self.send(client, plan_id="retired").status_code == 400
        assert self.send(client, plan_id="nope").status_code == 400
        sent = self.send(client)
        assert sent.status_code == 201 and sent.json()["status"] == "pending"
        assert sent.json()["amount"] == "129.00"
        assert "receipt" not in sent.json()
        assert self.send(client).status_code == 409
        listed = client.get("/client/v1/account/payment-requests").json()["requests"]
        assert [r["status"] for r in listed] == ["pending"]
        assert client.get("/client/v1/account/access").json()["tier"] == "free"  # nothing activated

    def test_the_admin_approves_and_the_plan_is_active_and_paid(self, world):
        client, _ = sign_up(world)
        self.send(client)
        found, _total = world.notifications.search(NotificationQuery(audience=ADMIN))
        assert [n.kind for n in found] == ["plan_request"]
        admin = admin_browser(world)
        waiting = admin.get("/admin/v1/payment-requests?status=pending").json()["requests"]
        assert [(r["client_name"], r["plan_name"]) for r in waiting] == [("Acme Corp", "Business")]
        rid = waiting[0]["request_id"]
        file = admin.get(f"/admin/v1/payment-requests/{rid}/receipt")
        assert file.content == b"%PDF-1.4 receipt"
        assert file.headers["content-type"] == "application/pdf"
        assert client.get(f"/admin/v1/payment-requests/{rid}/receipt").status_code == 401

        done = admin.post(f"/admin/v1/payment-requests/{rid}/approve")
        assert done.status_code == 200 and done.json()["status"] == "approved"
        assert world.plans.current_assignment("acme-corp").plan_id == "business"
        assert client.get("/client/v1/account/access").json()["tier"] == "paid"
        invoices = world.invoices.list_invoices(InvoiceFilter(client_id="acme-corp"))
        assert [i.status for i in invoices] == [InvoiceStatus.PAID]
        query = NotificationQuery(audience=CLIENT, client_id="acme-corp")
        assert [n.kind for n in world.notifications.search(query)[0]] == ["plan_activated"]
        assert admin.post(f"/admin/v1/payment-requests/{rid}/approve").status_code == 409
        assert client.post("/client/v1/agent/start").status_code == 409  # past the wall

    def test_a_rejection_needs_a_reason_the_client_reads_and_it_can_try_again(self, world):
        client, _ = sign_up(world)
        rid = self.send(client).json()["request_id"]
        admin = admin_browser(world)
        assert admin.post(f"/admin/v1/payment-requests/{rid}/reject", json={"reason": ""}).status_code == 422
        rejected = admin.post(
            f"/admin/v1/payment-requests/{rid}/reject", json={"reason": "Amount does not match."}
        )
        assert rejected.status_code == 200
        listed = client.get("/client/v1/account/payment-requests").json()["requests"]
        assert (listed[0]["status"], listed[0]["reason"]) == ("rejected", "Amount does not match.")
        assert world.plans.current_assignment("acme-corp") is None
        assert client.get("/client/v1/account/access").json()["tier"] == "free"
        assert self.send(client).status_code == 201  # a new receipt is accepted

    def test_the_decisions_are_the_admins(self, world):
        client, _ = sign_up(world)
        self.send(client)
        assert browser(world).get("/admin/v1/payment-requests").status_code == 401
        assert client.get("/admin/v1/payment-requests").status_code == 401
