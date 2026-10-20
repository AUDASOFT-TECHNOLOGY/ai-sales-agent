"""Who is in charge of a conversation, as the application keeps it: the store (memory and SQL), the
notification a handover opens, and the follow-up settings a Client may change (spec 4.5, 9.3, 9.4).

SIMULATED: the clock, the database (SQLite), the notifications' store. No message is sent."""

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from sales_app.audit_log import AuditQuery, AuditService, InMemoryAuditLog
from sales_app.conversation_control import (
    ConversationControlHub,
    InMemoryControlRows,
    notification_key,
)
from sales_app.followup_settings import (
    DEFAULT_PLAN,
    FollowUpPlan,
    Window,
    apply_plan,
    plan_of,
)
from sales_app.notifications import (
    CLIENT,
    HUMAN_INTERVENTION,
    InMemoryNotificationStore,
    NotificationQuery,
    NotificationService,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.control_rows import SqlControlRows
from sales_app.sourcing.errors import StoreUnavailableError
from sales_core.contracts import ProspectId
from sales_core.ports import ConversationControlStore

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
P1 = ProspectId("p1")


def sql_rows():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return SqlControlRows(engine), engine


@pytest.fixture(params=["memory", "sql"])
def rows(request):
    if request.param == "memory":
        return InMemoryControlRows()
    found, _ = sql_rows()
    return found


def hub_for(rows):
    notifications = NotificationService(InMemoryNotificationStore(), now=lambda: NOW)
    audit = AuditService(InMemoryAuditLog(), now=lambda: NOW)
    return (
        ConversationControlHub(rows, notifications=notifications, audit=audit),
        notifications,
        audit,
    )


def open_for(notifications, client_id="alpha"):
    found, _ = notifications.search(
        NotificationQuery(audience=CLIENT, client_id=client_id, include_resolved=False)
    )
    return found


class TestStore:
    def test_it_is_a_conversation_control_store(self, rows):
        hub, _, _ = hub_for(rows)
        assert isinstance(hub.for_client("alpha"), ConversationControlStore)

    def test_the_agent_is_in_charge_until_a_handover(self, rows):
        control = hub_for(rows)[0].for_client("alpha")
        assert control.get(P1) is None and control.humans() == frozenset()

    def test_handover_take_and_release(self, rows):
        control = hub_for(rows)[0].for_client("alpha")
        waiting = control.await_human(P1, reason="  asks for a manager \n", at=NOW)
        assert waiting.awaiting_human and waiting.reason == "asks for a manager"
        assert control.humans() == frozenset({P1})
        taken = control.take(P1, by="client:alpha", reason=None, at=NOW + timedelta(minutes=5))
        assert taken.taken_by == "client:alpha" and not taken.awaiting_human
        assert taken.reason == "asks for a manager" and taken.taken_at == NOW
        again = control.await_human(P1, reason="other", at=NOW)  # a person already has it
        assert again.taken_by == "client:alpha"
        released = control.release(P1, at=NOW + timedelta(hours=1))
        assert not released.is_human and released.released_at == NOW + timedelta(hours=1)
        assert control.humans() == frozenset() and not control.get(P1).is_human
        assert control.release(P1, at=NOW) is None  # nothing to release any more

    def test_a_client_never_sees_another_clients_conversations(self, rows):
        hub = hub_for(rows)[0]
        hub.for_client("alpha").take(P1, by="a", reason=None, at=NOW)
        assert hub.for_client("beta").get(P1) is None
        assert hub.for_client("beta").humans() == frozenset()

    def test_it_survives_a_restart(self):
        found, engine = sql_rows()
        hub_for(found)[0].for_client("alpha").await_human(P1, reason="x", at=NOW)
        reopened = hub_for(SqlControlRows(engine))[0].for_client("alpha")
        assert reopened.get(P1).awaiting_human and reopened.humans() == frozenset({P1})

    def test_a_database_that_fails_is_a_fixed_sentence(self):
        _, engine = sql_rows()
        engine.dispose()
        broken = SqlControlRows(create_database_engine("sqlite:////nonexistent-dir/x.db"))
        with pytest.raises(StoreUnavailableError) as problem:
            broken.humans("alpha")
        assert "alpha" not in str(problem.value)


class TestNotification:
    def test_a_handover_opens_one_notification_per_prospect(self, rows):
        hub, notifications, audit = hub_for(rows)
        control = hub.for_client("alpha")
        control.await_human(P1, reason="angry", at=NOW)
        control.await_human(P1, reason="angry", at=NOW)  # nothing new
        control.await_human(ProspectId("p2"), reason=None, at=NOW)
        opened = open_for(notifications)
        assert [n.kind for n in opened] == [HUMAN_INTERVENTION, HUMAN_INTERVENTION]
        assert {n.dedupe_key for n in opened} == {
            f"client|alpha|{notification_key('p1')}",
            f"client|alpha|{notification_key('p2')}",
        }
        assert open_for(notifications, "beta") == []
        entries, _ = audit.search(AuditQuery())
        assert [e.action for e in entries].count("conversation.awaiting_human") == 2
        assert all("angry" not in str(e.after) for e in entries)  # no free text in the audit

    def test_taking_the_conversation_closes_its_notification(self, rows):
        hub, notifications, _ = hub_for(rows)
        control = hub.for_client("alpha")
        control.await_human(P1, reason=None, at=NOW)
        control.take(P1, by="client:alpha", reason=None, at=NOW)
        assert open_for(notifications) == []

    def test_a_second_handover_after_a_release_opens_it_again(self, rows):
        hub, notifications, _ = hub_for(rows)
        control = hub.for_client("alpha")
        control.await_human(P1, reason=None, at=NOW)
        control.release(P1, at=NOW)
        assert open_for(notifications) == []
        control.await_human(P1, reason=None, at=NOW)
        assert len(open_for(notifications)) == 1

    def test_a_notification_that_fails_never_breaks_the_handover(self):
        class Broken(InMemoryNotificationStore):
            def open_or_reopen(self, notification):
                raise RuntimeError("down")

        notifications = NotificationService(Broken(), now=lambda: NOW)
        hub = ConversationControlHub(InMemoryControlRows(), notifications=notifications)
        waiting = hub.for_client("alpha").await_human(P1, reason=None, at=NOW)
        assert waiting.awaiting_human


class TestFollowUpPlan:
    BRAIN: ClassVar = {"tone": "warm", "follow_up": {"limits": {"max_touches": 4}}}

    def test_the_default_is_three_follow_ups_two_days_apart(self):
        assert (DEFAULT_PLAN.max_followups, DEFAULT_PLAN.gap_days) == (3, 2)
        assert plan_of(None) == DEFAULT_PLAN and plan_of({"tone": "x"}) == DEFAULT_PLAN
        assert plan_of({"follow_up": {"limits": {"max_touches": 4}}}).max_followups == 3

    def test_an_existing_cadence_is_read_back(self):
        brain = {
            "follow_up": {
                "situations": {"no_response": {"steps": [{"after": "P2D"}, {"after": "P5D"}]}}
            }
        }
        plan = plan_of(brain)
        assert plan.max_followups == 2 and plan.gap_days == 2 and plan.custom_schedule

    def test_applying_a_plan_keeps_everything_else(self):
        brain = {
            "tone": "warm",
            "follow_up": {
                "situations": {
                    "no_response": {
                        "steps": [{"after": "P2D", "intent": "nudge"}],
                        "then": "close",
                    },
                    "callback_promised": {"use": "prospect_preference"},
                },
                "stop_conditions": ["explicit_no"],
                "limits": {"max_touches": 4, "min_gap": "PT6H"},
            },
        }
        new = apply_plan(brain, FollowUpPlan(5, 3, Window("09:00", "18:00", "Africa/Tunis")))
        follow = new["follow_up"]
        assert new["tone"] == "warm" and brain["follow_up"]["limits"]["max_touches"] == 4
        assert follow["situations"]["no_response"] == {
            "steps": [{"after": "P3D", "intent": "nudge"}] + [{"after": "P3D"}] * 4,
            "then": "close",
        }
        assert follow["situations"]["callback_promised"] == {"use": "prospect_preference"}
        assert follow["stop_conditions"] == ["explicit_no"]
        assert follow["limits"] == {"max_touches": 5, "min_gap": "PT6H"}
        assert follow["send_window"] == {
            "start": "09:00",
            "end": "18:00",
            "timezone": "Africa/Tunis",
        }
        assert plan_of(new) == FollowUpPlan(5, 3, Window("09:00", "18:00", "Africa/Tunis"))

    def test_zero_follow_ups_removes_the_cadence_and_no_window_removes_the_window(self):
        with_window = apply_plan(self.BRAIN, FollowUpPlan(2, 2, Window("09:00", "18:00", "UTC")))
        none = apply_plan(with_window, FollowUpPlan(0, 2))
        assert "no_response" not in none["follow_up"]["situations"]
        assert "send_window" not in none["follow_up"]
        assert plan_of(none).max_followups == 0
