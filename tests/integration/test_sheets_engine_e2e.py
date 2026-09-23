"""The real Engine running on the Google Sheets adapter (over an in-memory sheet).

These are the scenarios that matter to a client: a person approves in the sheet, edits a draft,
changes their mind at the last second, opts a prospect out, while the agent is working.
"""

from datetime import timedelta

import pytest
from engine_harness import ScriptedBrain, phone_for, send
from factories import NOW, make_client_context
from sheets_support import FakeSheetsApi, make_repository

from sales_core.approval import ApprovalMode
from sales_core.contracts import ActionStatus, Channel, InboundMessage
from sales_core.engine import Engine, EngineConfig, InboundStatus
from sales_integrations.fakes import FakeClock, FakeMessagingChannel
from sales_integrations.google_sheets import GoogleSheetsRepository

FOLLOW_UP_IN = timedelta(days=3)


class Channel3(FakeMessagingChannel):
    """A channel that lets a test act as the human at the moment of sending."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.on_send = None

    def send(self, message):
        if self.on_send:
            self.on_send()
        return super().send(message)


class World:
    def __init__(self, mode=ApprovalMode.APPROVAL, brain=None, limit=20):
        self.api = FakeSheetsApi()
        self.repo, _ = make_repository(self.api)
        self.clock = FakeClock(NOW)
        self.channel = Channel3(clock=self.clock)
        self.brain = brain or ScriptedBrain(
            lambda r: send("Hello!", follow_up_in=FOLLOW_UP_IN, stage="warm")
        )
        self.mode = mode
        self.limit = limit
        self.engine = self.new_engine(self.repo)

    def new_engine(self, repo):
        return Engine(
            client=make_client_context(),
            repository=repo,
            brain=self.brain,
            channel=self.channel,
            clock=self.clock,
            config=EngineConfig(mode=self.mode, daily_action_limit=self.limit),
        )

    def restart(self):
        """A new process: a fresh adapter (no memory of earlier reads) over the same sheet."""
        repo, _ = make_repository(self.api, layout=False)
        self.repo, self.engine = repo, self.new_engine(repo)

    def add_prospect(self, pid="p1", **cells):
        values = {
            "prospect_id": pid,
            "display_name": "Alex",
            "phone": phone_for(pid),
            "status": "new",
        }
        return self.api.human_add_row("Prospects", **(values | cells))

    def action_row(self, pid="p1"):
        return self.api.find_row("Actions", "prospect_id", pid)

    def prospect_row(self, pid="p1"):
        return self.api.find_row("Prospects", "prospect_id", pid)

    def human_approves(self, pid="p1"):
        self.api.human_set("Actions", self.action_row(pid), "status", "approved")

    def status(self, pid="p1"):
        return self.api.cell("Actions", self.action_row(pid), "status")

    def log_types(self):
        return [r["event_type"] for r in self.api.rows("Log")]


@pytest.fixture
def world():
    return World()


class TestApprovalFlowThroughTheSheet:
    def test_prepare_leaves_a_reviewable_proposal_in_the_actions_tab(self, world):
        world.add_prospect()
        assert world.engine.prepare().ok
        (row,) = world.api.rows("Actions")
        assert row["status"] == "proposed" and row["draft_text"] == "Hello!"
        assert row["prospect_id"] == "p1" and row["action_type"] == "first_contact"
        assert row["next_stage"] == "warm" and row["next_action_type"] == "follow_up"
        assert world.channel.attempts == ()

    def test_a_person_approves_in_the_sheet_and_the_agent_sends(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.human_approves()
        assert world.engine.dispatch().ok

        assert world.status() == "sent"
        (sent,) = world.channel.sent
        assert sent.draft.text == "Hello!" and sent.recipient == phone_for("p1")
        (message,) = world.api.rows("Messages")
        assert message["direction"] == "outbound" and message["text"] == "Hello!"
        assert message["external_id"] == "fake-1" and message["prospect_id"] == "p1"

        row = world.prospect_row()
        cell = lambda h: world.api.cell("Prospects", row, h)
        assert cell("status") == "active" and cell("stage") == "warm"
        assert cell("last_contacted_at") == "2026-01-15T10:00:00+01:00"
        assert cell("next_action_type") == "follow_up"
        assert cell("next_action_due_at") == "2026-01-18T10:00:00+01:00"

    def test_the_audit_trail_lands_in_the_log_tab(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.human_approves()
        world.engine.dispatch()
        assert world.log_types() == [
            "action.planned",
            "brain.decision",
            "action.proposed",
            "action.sent",
        ]

    def test_a_draft_edited_by_a_person_is_what_is_sent(self, world):
        world.add_prospect()
        world.engine.prepare()
        row = world.action_row()
        world.api.human_set("Actions", row, "draft_text", "Salut Alex, version corrigée")
        world.api.human_set("Actions", row, "next_stage", "hot")
        world.human_approves()
        world.engine.dispatch()
        assert world.channel.sent[0].draft.text == "Salut Alex, version corrigée"
        assert world.api.cell("Prospects", world.prospect_row(), "stage") == "hot"

    def test_a_rejected_proposal_is_never_sent_and_the_prospect_is_unchanged(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.api.human_set("Actions", world.action_row(), "status", "rejected")
        world.engine.dispatch()
        assert world.channel.attempts == ()
        assert world.api.cell("Prospects", world.prospect_row(), "status") == "new"

    def test_the_proposal_survives_a_restart_and_a_delayed_approval(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.restart()  # a new process, with no memory
        world.clock.advance(timedelta(days=2))
        world.human_approves()
        assert world.engine.dispatch().ok
        assert world.status() == "sent"
        assert world.api.cell("Prospects", world.prospect_row(), "stage") == "warm"


class TestRacesWithPeople:
    def test_a_rejection_made_just_before_sending_wins(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.human_approves()
        row, api, original, fired = world.action_row(), world.api, world.repo.list_actions, []

        def list_then_human_changes_mind(statuses=None):
            result = original(statuses)
            if statuses and ActionStatus.APPROVED in statuses and not fired:
                fired.append(1)
                api.human_set("Actions", row, "status", "rejected")
            return result

        world.repo.list_actions = list_then_human_changes_mind
        report = world.engine.dispatch()
        assert world.channel.attempts == ()  # nothing was sent
        assert not report.ok and "changed to 'rejected'" in report.errors[0]
        assert world.status() == "rejected"  # the person's decision stands
        world.engine.dispatch()
        assert world.channel.attempts == ()

    def test_an_opt_out_typed_while_a_message_is_in_flight_is_not_reverted(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.human_approves()
        world.channel.on_send = lambda: world.api.human_set(
            "Prospects", world.prospect_row(), "status", "opted_out"
        )
        report = world.engine.dispatch()
        assert world.status() == "sent"  # the message had already gone
        assert any("update_prospect failed after send" in e for e in report.errors)
        assert world.api.cell("Prospects", world.prospect_row(), "status") == "opted_out"

    def test_an_opt_out_before_dispatch_blocks_the_send(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.human_approves()
        world.api.human_set("Prospects", world.prospect_row(), "status", "opted_out")
        world.engine.dispatch()
        assert world.channel.attempts == ()
        assert world.status() == "cancelled"

    def test_a_paused_prospect_is_not_contacted(self, world):
        world.add_prospect(status="paused")
        world.engine.prepare()
        assert world.api.rows("Actions") == [] and world.brain.calls == []

    def test_an_action_stuck_in_sending_is_flagged_never_resent(self, world):
        world.add_prospect()
        world.engine.prepare()
        world.api.human_set("Actions", world.action_row(), "status", "sending")
        report = world.engine.dispatch()
        assert not report.ok and world.channel.attempts == ()
        assert world.api.cell("Actions", world.action_row(), "last_error").startswith(
            "unconfirmed send"
        )


class TestPlanningAgainstTheSheet:
    def test_the_daily_quota_is_twenty_and_reruns_add_nothing(self, world):
        for i in range(25):
            world.add_prospect(f"p{i:02d}")
        world.engine.prepare()
        assert len(world.api.rows("Actions")) == 20
        world.restart()
        world.engine.prepare()
        assert len(world.api.rows("Actions")) == 20 and len(world.brain.calls) == 20

    def test_prospects_added_without_an_id_are_still_planned(self, world):
        world.api.human_add_row(
            "Prospects", display_name="No Id", phone=phone_for("p9"), status="new"
        )
        world.engine.prepare()
        (action,) = world.api.rows("Actions")
        assert action["prospect_id"].startswith("p-")
        assert world.api.rows("Prospects")[0]["prospect_id"] == action["prospect_id"]

    def test_a_row_with_a_bad_value_is_never_contacted_and_is_reported(self, world):
        world.add_prospect("good")
        world.add_prospect("bad", status="maybe")
        world.engine.prepare()
        assert [r["prospect_id"] for r in world.api.rows("Actions")] == ["good"]
        assert any("status" in p.message for p in world.repo.problems)

    def test_a_callback_typed_by_a_person_is_planned_when_due(self, world):
        world.add_prospect(
            status="active", next_action_type="callback", next_action_due_at="2026-01-15 09:30"
        )
        world.engine.prepare()  # NOW is 10:00 in Tunis: the callback is due
        (action,) = world.api.rows("Actions")
        assert action["action_type"] == "callback"

    def test_autonomous_mode_sends_without_a_human(self):
        world = World(mode=ApprovalMode.AUTONOMOUS)
        world.add_prospect()
        world.engine.run_cycle()
        assert world.status() == "sent" and len(world.channel.sent) == 1


class TestInboundThroughTheSheet:
    def inbound(self, text="Hi, tell me more", wamid="wamid.1", pid="p1"):
        return InboundMessage(
            channel=Channel.WHATSAPP,
            sender=phone_for(pid).lstrip("+"),
            external_id=wamid,
            text=text,
            timestamp=NOW,
        )

    def test_a_reply_is_stored_and_answered_with_a_proposal(self, world):
        world.add_prospect()
        receipt = world.engine.receive_inbound(self.inbound())
        assert receipt.status is InboundStatus.STORED
        (message,) = world.api.rows("Messages")
        assert message["direction"] == "inbound" and message["text"] == "Hi, tell me more"
        assert (
            world.api.cell("Prospects", world.prospect_row(), "last_inbound_at")
            == "2026-01-15T10:00:00+01:00"
        )

        assert world.engine.process_inbound(receipt).ok
        (row,) = world.api.rows("Actions")
        assert row["action_type"] == "reply" and row["status"] == "proposed"

    def test_a_redelivered_webhook_is_a_duplicate(self, world):
        world.add_prospect()
        world.engine.receive_inbound(self.inbound())
        world.restart()
        assert world.engine.receive_inbound(self.inbound()).status is InboundStatus.DUPLICATE
        assert len(world.api.rows("Messages")) == 1

    def test_stop_opts_the_prospect_out_in_the_sheet(self, world):
        world.add_prospect()
        receipt = world.engine.receive_inbound(self.inbound("STOP"))
        world.engine.process_inbound(receipt)
        assert world.api.cell("Prospects", world.prospect_row(), "status") == "opted_out"
        assert world.brain.calls == []

    def test_the_conversation_the_brain_sees_comes_from_the_messages_tab(self, world):
        world.add_prospect()
        world.engine.receive_inbound(self.inbound("first", "w1"))
        world.engine.process_inbound(world.engine.receive_inbound(self.inbound("second", "w2")))
        (request,) = world.brain.calls[-1:]
        assert [m.text for m in request.conversation.messages] == ["first", "second"]


def test_the_adapter_is_the_repository_the_engine_uses(world):
    assert isinstance(world.repo, GoogleSheetsRepository)


class TestTheSheetChangesBetweenCycles:
    """Clients keep working in the sheet all day; every cycle sees the sheet as it is now."""

    def test_a_prospect_added_after_the_morning_cycle_is_planned_by_a_later_one(self, world):
        world.add_prospect("p1")
        world.engine.prepare()  # the morning cycle
        world.add_prospect("p2")  # added by the client at 11:00
        world.clock.advance(timedelta(hours=3))
        world.engine.prepare()  # a later planning pass
        assert [r["prospect_id"] for r in world.api.rows("Actions")] == ["p1", "p2"]
        assert world.brain.called_for() == ["p1", "p2"]  # nobody was asked about twice

    def test_the_daily_quota_is_shared_by_the_morning_and_the_later_passes(self):
        world = World(limit=3)
        world.add_prospect("p1")
        world.add_prospect("p2")
        world.engine.prepare()
        for pid in ("p3", "p4"):
            world.add_prospect(pid)
        world.clock.advance(timedelta(hours=3))
        world.engine.prepare()
        assert [r["prospect_id"] for r in world.api.rows("Actions")] == ["p1", "p2", "p3"]
        world.clock.advance(timedelta(days=1))  # the next day the quota is fresh
        world.engine.prepare()
        assert "p4" in {r["prospect_id"] for r in world.api.rows("Actions")}

    def test_a_callback_a_person_schedules_for_later_today_is_planned_when_it_falls_due(
        self, world
    ):
        world.add_prospect("p1", status="active")
        world.engine.prepare()
        assert world.api.rows("Actions") == []  # nothing is due yet
        row = world.prospect_row()
        world.api.human_set("Prospects", row, "next_action_type", "callback")
        world.api.human_set("Prospects", row, "next_action_due_at", "2026-01-15 16:00")
        world.clock.advance(timedelta(hours=1))
        world.engine.prepare()
        assert world.api.rows("Actions") == []  # 11:00 in Tunis: still before 16:00
        world.clock.advance(timedelta(hours=5))
        world.engine.prepare()
        (action,) = world.api.rows("Actions")
        assert action["action_type"] == "callback"

    def test_repeated_cycles_never_duplicate_actions_or_prospect_rows(self, world):
        world.add_prospect("p1")
        for _ in range(3):
            world.engine.prepare()
            world.restart()
        assert len(world.api.rows("Actions")) == 1 and len(world.api.rows("Prospects")) == 1
        assert len(world.brain.calls) == 1

    def test_a_prospect_deleted_after_its_proposal_is_never_contacted(self, world):
        world.add_prospect("p1")
        world.engine.prepare()
        world.human_approves()
        world.api.tabs["Prospects"].pop(world.prospect_row() - 1)  # the client removes the row
        world.engine.dispatch()
        assert world.channel.attempts == () and world.status() == "cancelled"

    def test_a_prospect_paused_after_its_proposal_is_never_contacted(self, world):
        world.add_prospect("p1")
        world.engine.prepare()
        world.human_approves()
        world.api.human_set("Prospects", world.prospect_row(), "status", "paused")
        world.engine.dispatch()
        assert world.channel.attempts == () and world.status() == "cancelled"

    def test_history_and_actions_follow_the_prospect_when_the_sheet_is_rearranged(self, world):
        world.add_prospect("p1")
        world.add_prospect("p2")
        world.engine.prepare()
        world.human_approves("p1")
        world.api.tabs["Prospects"][1:] = list(reversed(world.api.tabs["Prospects"][1:]))
        world.api.tabs["Prospects"].insert(1, [""] * len(world.api.headers("Prospects")))
        world.restart()
        world.engine.dispatch()
        assert [m["prospect_id"] for m in world.api.rows("Messages")] == ["p1"]
        assert world.api.cell("Prospects", world.prospect_row("p1"), "stage") == "warm"
        assert world.api.cell("Prospects", world.prospect_row("p2"), "stage") == ""
