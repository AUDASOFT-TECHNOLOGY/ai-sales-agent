"""Taking a conversation over, writing to the prospect yourself, giving it back, and the follow-up
settings, through the Client API (spec 4.5, 9.3, 9.4).

SIMULATED: the messaging channel (a fake that delivers nothing), the Brain, the login, the repository
(in memory). REAL: the application, the routes, the Engine and the conversation control it obeys."""

import json
from datetime import timedelta

import pytest
from engine_harness import phone_for
from factories import NOW, make_prospect
from test_client_agent_api import SETTINGS, World

from sales_app.audit_log import AuditQuery
from sales_core.contracts import (
    ActionStatus,
    Channel,
    ContactInfo,
    InboundMessage,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)

FOLLOWUP = "/client/v1/followup-settings"


def conversation(pid):
    return f"/client/v1/conversations/{pid}"


def key(n=1):
    return f"key-{n:08d}"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.application.close()


def add(world, cid="alpha", pid="p1", **overrides):
    fields = {
        "client_id": cid,
        "prospect_id": ProspectId(pid),
        "display_name": "Amel",
        "status": ProspectStatus.ACTIVE,
        "contact": ContactInfo(phone=phone_for(pid), preferred_channel=Channel.WHATSAPP),
    } | overrides
    world.made.repositories[cid].add_prospects(make_prospect(**fields))


def channel_of(world, cid="alpha"):
    return world.made.channels[cid]


def lease(world, cid="alpha"):
    return world.application.registry.get(cid).lease()


def prospect_of(client, pid="p1"):
    """The prospect as the list shows it (the detail has no prospect without a database)."""
    found = client.get("/client/v1/prospects").json()["prospects"]
    return next(p for p in found if p["id"] == pid)


def audited(world, action):
    entries, _ = world.application.audit.search(AuditQuery(client_id="alpha", action=action))
    return entries


class TestAccess:
    def test_every_route_needs_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.post(f"{conversation('p1')}/take-over").status_code == 401
        assert anonymous.post(f"{conversation('p1')}/release").status_code == 401
        body = {"text": "hello", "idempotency_key": key()}
        assert anonymous.post(f"{conversation('p1')}/messages", json=body).status_code == 401
        assert anonymous.get(FOLLOWUP).status_code == 401

    def test_a_client_cannot_reach_another_clients_prospect(self, world):
        add(world, "beta", "p1")
        alpha = world.browser("alpha")
        for path in ("take-over", "release"):
            response = alpha.post(f"{conversation('p1')}/{path}")
            assert response.status_code == 404 and response.json()["detail"] == "no such prospect"
        body = {"text": "hello", "idempotency_key": key()}
        sent = alpha.post(f"{conversation('p1')}/messages", json=body)
        assert sent.status_code == 404 and sent.json()["detail"]["code"] == "unknown_prospect"
        assert world.application.conversation_controls("beta").get(ProspectId("p1")) is None


class TestTakeOver:
    def test_it_holds_the_conversation_and_shows_it(self, world):
        add(world)
        alpha = world.browser("alpha")
        before = alpha.get(conversation("p1")).json()
        assert before["control"]["mode"] == "agent" and not before["control"]["awaiting_human"]
        response = alpha.post(f"{conversation('p1')}/take-over", json={"reason": "wants a call"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["mode"] == "human" and body["taken_by"] == "client:alpha"
        assert body["awaiting_human"] is False and body["reason"] == "wants a call"
        shown = alpha.get(conversation("p1")).json()
        assert shown["control"]["mode"] == "human"
        assert prospect_of(alpha)["status"] == "paused" and prospect_of(alpha)["needs_human"]
        assert [e.outcome for e in audited(world, "conversation.take_over")] == ["success"]

    def test_taking_it_twice_is_harmless(self, world):
        add(world)
        alpha = world.browser("alpha")
        first = alpha.post(f"{conversation('p1')}/take-over").json()
        second = alpha.post(f"{conversation('p1')}/take-over").json()
        assert first["taken_at"] == second["taken_at"]

    def test_the_agent_sends_nothing_while_you_hold_it(self, world):
        add(
            world,
            schedule=Scheduling(
                next_action_type=SalesActionType.FOLLOW_UP,
                next_action_due_at=NOW - timedelta(hours=1),
            ),
        )
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        with lease(world) as engine:
            engine.prepare()
            engine.dispatch()
        assert world.made.repositories["alpha"].list_actions() == []
        assert channel_of(world).attempts == ()


class TestWritingYourself:
    def test_it_is_refused_until_you_take_the_conversation(self, world):
        add(world)
        response = world.browser("alpha").post(
            f"{conversation('p1')}/messages", json={"text": "Hello", "idempotency_key": key()}
        )
        assert (
            response.status_code == 409 and response.json()["detail"]["code"] == "not_in_human_mode"
        )
        assert not world.made.channels.get("alpha") or channel_of(world).attempts == ()

    def test_it_goes_out_on_the_prospects_channel_and_is_stored_as_human(self, world):
        add(world)
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        response = alpha.post(
            f"{conversation('p1')}/messages",
            json={"text": " Hello Amel ", "idempotency_key": key()},
        )
        assert response.status_code == 201, response.text
        assert response.json()["channel"] == "whatsapp" and response.json()["replayed"] is False
        (sent,) = channel_of(world).sent
        assert sent.draft.text == "Hello Amel" and sent.recipient == phone_for("p1")
        shown = alpha.get(conversation("p1")).json()
        (message,) = shown["messages"]
        assert message["author"] == "human" and message["direction"] == "outbound"
        (entry,) = audited(world, "conversation.message")
        assert "Hello Amel" not in json.dumps(entry.after)  # the text is never audited

    def test_the_same_key_never_sends_twice(self, world):
        add(world)
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        body = {"text": "Hello", "idempotency_key": key()}
        first = alpha.post(f"{conversation('p1')}/messages", json=body)
        again = alpha.post(f"{conversation('p1')}/messages", json=body)
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["replayed"] is True
        assert again.json()["message_id"] == first.json()["message_id"]
        assert len(channel_of(world).sent) == 1

    @pytest.mark.parametrize(
        "body",
        [
            {"text": "", "idempotency_key": key()},
            {"text": "   ", "idempotency_key": key()},
            {"text": "hi", "idempotency_key": "short"},
            {"text": "hi", "idempotency_key": "has spaces in it"},
            {"text": "hi"},
            {"text": "x" * 4001, "idempotency_key": key()},
            {"text": "hi", "idempotency_key": key(), "channel": "email"},
        ],
    )
    def test_a_bad_request_is_refused_before_anything_happens(self, world, body):
        add(world)
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        assert alpha.post(f"{conversation('p1')}/messages", json=body).status_code == 422
        assert not world.made.channels.get("alpha") or channel_of(world).attempts == ()

    def test_a_channel_that_is_not_ready_gives_a_clear_error(self, world, monkeypatch):
        add(world)
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        monkeypatch.setattr(
            "sales_app.api.client_conversation_control.channel_problem",
            lambda application, client: "Connect your WhatsApp number first.",
        )
        response = alpha.post(
            f"{conversation('p1')}/messages", json={"text": "Hello", "idempotency_key": key()}
        )
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "channel_not_ready",
            "message": "Connect your WhatsApp number first.",
        }
        assert not world.made.channels.get("alpha") or channel_of(world).attempts == ()

    def test_a_channel_that_refuses_is_reported_and_nothing_is_stored(self, world):
        add(world)
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        with lease(world):
            pass
        channel_of(world).fail_next(transient=False, error_message="number not on WhatsApp")
        response = alpha.post(
            f"{conversation('p1')}/messages", json={"text": "Hello", "idempotency_key": key()}
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "send_failed"
        assert alpha.get(conversation("p1")).json()["messages"] == []


class TestStopRequests:
    def inbound(self, world, text):
        with lease(world) as engine:
            receipt = engine.receive_inbound(
                InboundMessage(
                    channel=Channel.WHATSAPP,
                    sender=phone_for("p1"),
                    external_id=f"wamid.{text}",
                    text=text,
                    timestamp=NOW,
                )
            )
            engine.process_inbound(receipt)

    def test_a_stop_while_you_hold_it_is_honoured_after_you_give_it_back(self, world):
        add(world)
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        self.inbound(world, "STOP")
        assert prospect_of(alpha)["status"] == "opted_out"
        refused = alpha.post(
            f"{conversation('p1')}/messages", json={"text": "Wait!", "idempotency_key": key()}
        )
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "opted_out"
        released = alpha.post(f"{conversation('p1')}/release").json()
        assert released["released"] is True and released["status"] == "opted_out"
        assert released["next_follow_up_at"] is None
        assert released["control"]["mode"] == "agent"
        assert prospect_of(alpha)["next_action_due_at"] is None
        with lease(world) as engine:
            engine.prepare()
            engine.dispatch()
        settled = {ActionStatus.COMPLETED, ActionStatus.CANCELLED}  # the stop, the refused message
        actions = world.made.repositories["alpha"].list_actions()
        assert actions and {a.status for a in actions} <= settled
        assert {a.action_type for a in actions} == {SalesActionType.REPLY}
        assert channel_of(world).attempts == ()


class TestRelease:
    def test_the_agent_comes_back_after_the_clients_own_wait(self, world):
        add(
            world,
            schedule=Scheduling(last_contacted_at=NOW - timedelta(days=4), follow_up_count=2),
        )
        alpha = world.browser("alpha")
        alpha.post(f"{conversation('p1')}/take-over")
        response = alpha.post(f"{conversation('p1')}/release")
        assert response.status_code == 200
        body = response.json()
        assert body["released"] is True and body["status"] == "active"
        due = world.clock.now() + timedelta(days=2)  # the default wait
        assert body["next_follow_up_at"].startswith(due.strftime("%Y-%m-%dT%H:%M"))
        shown = alpha.get(conversation("p1")).json()
        assert shown["control"]["mode"] == "agent" and shown["control"]["released_at"]
        found = prospect_of(alpha)
        assert found["next_action_type"] == "follow_up" and found["follow_up_count"] == 0
        assert [e.outcome for e in audited(world, "conversation.release")] == ["success"]

    def test_releasing_what_you_never_took_changes_nothing(self, world):
        add(world)
        body = world.browser("alpha").post(f"{conversation('p1')}/release").json()
        assert body["released"] is False and body["control"]["mode"] == "agent"


class TestFollowUpSettings:
    def read(self, client):
        response = client.get(FOLLOWUP)
        assert response.status_code == 200, response.text
        return response.json()

    def put(self, client, version, **plan):
        body = {"expected_version": version, "max_followups": 3, "gap_days": 2} | plan
        return client.put(FOLLOWUP, json=body)

    def test_the_default_is_three_follow_ups_two_days_apart(self, world):
        found = self.read(world.browser("alpha"))
        assert found["defaults"] == {"max_followups": 3, "gap_days": 2}
        assert found["bounds"] == {"max_followups": [0, 6], "gap_days": [1, 30]}
        assert found["configured"] is True and found["window"] is None
        assert found["timezone"] == "Africa/Tunis"

    def test_the_client_adapts_the_number_the_gap_and_the_hours(self, world):
        alpha = world.browser("alpha")
        before = self.read(alpha)
        settings_before = alpha.get(SETTINGS).json()["settings"]
        window = {"start": "09:00", "end": "17:30"}
        response = self.put(alpha, before["version"], max_followups=5, gap_days=3, window=window)
        assert response.status_code == 200, response.text
        found = response.json()
        assert (found["max_followups"], found["gap_days"]) == (5, 3)
        assert found["window"] == {"start": "09:00", "end": "17:30", "timezone": "Africa/Tunis"}
        assert before["version"] is None and found["version"] == 1
        after = alpha.get(SETTINGS).json()["settings"]
        assert after["timezone"] == settings_before["timezone"]
        assert after["commercial"]["catalog"] == settings_before["commercial"]["catalog"]
        assert [e.action for e in audited(world, "client.followup_settings.update")]
        # And back to none, with no hours.
        none = self.put(alpha, found["version"], max_followups=0, gap_days=1)
        assert none.status_code == 200 and none.json()["max_followups"] == 0
        assert none.json()["window"] is None

    @pytest.mark.parametrize(
        "plan",
        [
            {"max_followups": 7},
            {"max_followups": -1},
            {"gap_days": 0},
            {"gap_days": 31},
            {"window": {"start": "18:00", "end": "09:00"}},
            {"window": {"start": "9:00", "end": "18:00"}},
            {"window": {"start": "09:00", "end": "18:00", "timezone": "Mars/Base"}},
            {"unknown": 1},
        ],
    )
    def test_values_out_of_bounds_are_refused_and_change_nothing(self, world, plan):
        alpha = world.browser("alpha")
        before = self.read(alpha)
        assert self.put(alpha, before["version"], **plan).status_code == 422
        assert self.read(alpha) == before

    def test_a_stale_version_is_a_conflict(self, world):
        alpha = world.browser("alpha")
        before = self.read(alpha)
        assert self.put(alpha, before["version"], gap_days=4).status_code == 200
        assert self.put(alpha, before["version"], gap_days=5).status_code == 409

    def test_one_client_cannot_change_another(self, world):
        alpha, beta = world.browser("alpha"), world.browser("beta")
        base = self.read(beta)
        changed = self.put(alpha, self.read(alpha)["version"], max_followups=6, gap_days=9)
        assert changed.status_code == 200
        assert self.read(beta)["max_followups"] == base["max_followups"]
        assert self.read(beta)["gap_days"] == base["gap_days"]
