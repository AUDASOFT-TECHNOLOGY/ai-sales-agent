"""Free sign-up, the 50 free credits, and the backend wall that keeps a Free account from selling.

Real routes on a SQLite database built by the project's migrations. A Free account (signed up by
itself, on no plan) prepares its agent; every commercial entry point refuses it, and the 50 credits do
not change that. A paid plan lifts the wall; clients created before this existed are untouched.
"""

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

from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.free_accounts import FREE_CREDITS
from sales_app.notifications import ADMIN, NotificationQuery
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


class TestUpgrade:
    def test_choosing_a_plan_tells_the_admin_and_assigns_nothing(self, world):
        client, _ = sign_up(world)
        asked = client.post("/client/v1/account/plan-request", json={"plan_id": "business"})
        assert asked.status_code == 202
        found, _total = world.notifications.search(NotificationQuery(audience=ADMIN))
        assert [(n.kind, n.client_id) for n in found] == [("plan_request", "acme-corp")]
        assert world.plans.current_assignment("acme-corp") is None
        assert client.get("/client/v1/account/access").json()["tier"] == "free"

    def test_an_unknown_or_retired_plan_is_refused(self, world):
        client, _ = sign_up(world)
        for plan_id in ("nope", "retired"):
            assert client.post("/client/v1/account/plan-request", json={"plan_id": plan_id}).status_code == 400

    def test_it_needs_a_session(self, world):
        assert browser(world).post("/client/v1/account/plan-request", json={"plan_id": "business"}).status_code == 401
        assert browser(world).get("/client/v1/account/access").status_code == 401
