"""Phase A: planning, quota reservation, Brain decisions, approval gate."""

from dataclasses import replace
from datetime import timedelta

import pytest
from engine_harness import (
    CB,
    FU,
    REM,
    Crash,
    ScriptedBrain,
    build,
    decide,
    due,
    prospect,
    schedule,
    send,
)
from factories import NOW, make_action

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionId,
    ActionStatus,
    DecisionType,
    ProspectId,
    ProspectStatus,
)


class TestPlanningToProposal:
    def test_plan_then_brain_then_proposed(self):
        h = build(
            prospects=[due("p1", FU), prospect("p2")], brain=ScriptedBrain(lambda r: send("Hi!"))
        )
        report = h.engine.prepare()
        assert report.ok
        assert h.status("p1") is ActionStatus.PROPOSED
        assert h.status("p2") is ActionStatus.PROPOSED
        assert h.action("p1").draft.text == "Hi!"
        assert h.channel.attempts == ()  # nothing is sent before approval

    def test_brain_receives_prospect_conversation_client_and_action(self):
        h = build(prospects=[prospect("p1")])
        h.engine.prepare()
        (request,) = h.brain.calls
        assert request.prospect.prospect_id == "p1"
        assert request.client.client_id == "client-a"
        assert request.conversation.messages == ()
        assert request.action.action_type.value == "first_contact"
        assert request.now == NOW

    def test_all_actions_are_persisted_as_planned_before_the_first_brain_call(self):
        seen = []

        def brain_fn(request):
            seen.append([a.status for a in h.repo.list_actions()])
            return send()

        h = build(
            prospects=[prospect("p1"), prospect("p2"), prospect("p3")],
            brain=ScriptedBrain(brain_fn),
        )
        h.engine.prepare()
        assert seen[0] == [ActionStatus.PLANNED] * 3  # quota reserved up front

    def test_a_crash_mid_run_keeps_the_reservation_and_a_rerun_finishes_the_job(self):
        def crashing(request):
            raise Crash

        h = build(prospects=[prospect("p1"), prospect("p2")], brain=ScriptedBrain(crashing))
        with pytest.raises(Crash):
            h.engine.prepare()
        assert {a.status for a in h.repo.list_actions()} == {ActionStatus.PLANNED}

        restarted = h.new_engine(ScriptedBrain())
        report = restarted.prepare()
        assert report.ok
        assert {a.status for a in h.repo.list_actions()} == {ActionStatus.PROPOSED}
        assert len(h.repo.list_actions()) == 2  # no duplicates

    def test_daily_quota_is_twenty(self):
        h = build(prospects=[prospect(f"p{i:02d}") for i in range(25)])
        h.engine.prepare()
        assert len(h.repo.list_actions()) == 20
        assert len(h.brain.calls) == 20

    def test_configured_daily_limit_is_used(self):
        h = build(prospects=[prospect(f"p{i}") for i in range(9)], daily_action_limit=4)
        h.engine.prepare()
        assert len(h.repo.list_actions()) == 4

    def test_tiers_are_processed_in_priority_order(self):
        h = build(prospects=[prospect("p4"), due("p3", CB), due("p2", REM), due("p1", FU)])
        h.engine.prepare()
        assert h.brain.called_for() == ["p1", "p2", "p3", "p4"]


class TestApprovalGate:
    def test_approval_mode_waits_for_a_human(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.APPROVAL)
        h.engine.prepare()
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.PROPOSED
        assert h.channel.sent == ()

    def test_autonomous_mode_approves_automatically_and_sends_in_one_cycle(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.APPROVED
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.SENT

    def test_run_cycle_prepares_and_dispatches(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        report = h.engine.run_cycle()
        assert h.status("p1") is ActionStatus.SENT
        assert report.statuses()[h.action("p1").action_id] is ActionStatus.SENT

    def test_dry_run_calls_the_brain_but_persists_and_sends_nothing(self):
        h = build(prospects=[prospect("p1"), due("p2")], mode=ApprovalMode.DRY_RUN)
        before = {p.prospect_id: p for p in h.repo.list_prospects()}
        report = h.engine.prepare()
        h.engine.dispatch()
        assert len(h.brain.calls) == 2
        assert h.repo.list_actions() == []
        assert h.channel.attempts == ()
        assert {p.prospect_id: p for p in h.repo.list_prospects()} == before
        assert set(h.event_types()) == {"brain.decision", "dryrun.decision"}
        assert all(o.status is None for o in report.outcomes)


class TestDecisions:
    def test_no_action_completes_the_action_and_clears_the_schedule(self):
        h = build(
            prospects=[due("p1")], brain=ScriptedBrain(lambda r: decide(DecisionType.NO_ACTION))
        )
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.COMPLETED
        assert h.prospect("p1").schedule.next_action_type is None
        assert h.channel.attempts == ()

    def test_completed_actions_count_toward_the_daily_quota(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.NO_ACTION))
        h = build(prospects=[prospect(f"p{i:02d}") for i in range(25)], brain=brain)
        h.engine.prepare()
        h.engine.prepare()
        assert len(h.repo.list_actions()) == 20
        assert len(brain.calls) == 20  # the rerun did not ask the Brain for more

    def test_schedule_follow_up_persists_the_next_action_and_completes(self):
        brain = ScriptedBrain(lambda r: schedule(timedelta(days=4), stage="warm"))
        h = build(prospects=[due("p1")], brain=brain)
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.COMPLETED
        p = h.prospect("p1")
        assert p.schedule.next_action_type is FU
        assert p.schedule.next_action_due_at == NOW + timedelta(days=4)
        assert p.stage == "warm"
        assert h.channel.attempts == ()

    @pytest.mark.parametrize("kind", [FU, REM, CB])
    def test_brain_can_schedule_followup_reminder_and_callback(self, kind):
        brain = ScriptedBrain(lambda r: schedule(timedelta(days=2), next_type=kind))
        h = build(prospects=[due("p1")], brain=brain)
        h.engine.prepare()
        assert h.prospect("p1").schedule.next_action_type is kind

    def test_a_scheduled_reminder_comes_back_as_a_reminder_when_due(self):
        brain = ScriptedBrain(lambda r: schedule(timedelta(days=2), next_type=REM))
        h = build(prospects=[due("p1")], brain=brain)
        h.engine.prepare()
        h.clock.advance(timedelta(days=3))
        brain.calls.clear()
        h.engine.prepare()
        assert brain.calls[0].action.action_type is REM

    def test_handoff_pauses_the_prospect_and_completes(self):
        brain = ScriptedBrain(
            lambda r: decide(DecisionType.HANDOFF_TO_HUMAN, new_stage="needs-human")
        )
        h = build(prospects=[due("p1")], brain=brain)
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.COMPLETED
        p = h.prospect("p1")
        assert p.status is ProspectStatus.PAUSED and p.stage == "needs-human"
        assert p.schedule.next_action_type is None
        assert "prospect.handoff" in h.event_types()
        assert h.channel.attempts == ()

    def test_close_closes_the_prospect_and_completes(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.CLOSE, new_stage="lost"))
        h = build(prospects=[due("p1")], brain=brain)
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.COMPLETED
        assert h.prospect("p1").status is ProspectStatus.CLOSED
        assert "prospect.closed" in h.event_types()

    def test_a_paused_or_closed_prospect_is_never_planned_again(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.CLOSE))
        h = build(prospects=[due("p1")], brain=brain)
        h.engine.prepare()
        h.clock.advance(timedelta(days=2))
        h.engine.prepare()
        assert len(brain.calls) == 1

    def test_send_message_proposal_keeps_stage_and_next_action_on_the_action(self):
        brain = ScriptedBrain(
            lambda r: send(follow_up_in=timedelta(days=3), next_type=REM, stage="s1")
        )
        h = build(prospects=[prospect("p1")], brain=brain)
        h.engine.prepare()
        action = h.action("p1")
        assert action.next_stage == "s1"
        assert action.next_action_type is REM
        assert action.next_action_due_at == NOW + timedelta(days=3)
        # the prospect itself is untouched until the message is really sent
        assert h.prospect("p1") == prospect("p1")

    def test_a_decision_dated_in_the_past_is_rejected_and_changes_nothing(self):
        bad = replace(send(), next_follow_up_at=NOW - timedelta(days=1), next_action_type=FU)
        h = build(prospects=[prospect("p1")], brain=ScriptedBrain(lambda r: bad))
        report = h.engine.prepare()
        assert not report.ok and "future" in report.errors[0]
        assert h.status("p1") is ActionStatus.PLANNED
        assert h.action("p1").attempts == 1
        assert h.channel.attempts == ()


class TestSafetyBeforeTheBrain:
    @pytest.mark.parametrize(
        "status", [ProspectStatus.OPTED_OUT, ProspectStatus.PAUSED, ProspectStatus.CLOSED]
    )
    def test_the_planner_never_selects_these_prospects(self, status):
        h = build(prospects=[prospect("p1", status=status), due("p2", status=status)])
        h.engine.prepare()
        assert h.brain.calls == [] and h.repo.list_actions() == []

    @pytest.mark.parametrize(
        "status", [ProspectStatus.OPTED_OUT, ProspectStatus.PAUSED, ProspectStatus.CLOSED]
    )
    def test_a_prospect_that_changed_after_planning_is_blocked_before_the_brain(self, status):
        stale = make_action(
            action_id=ActionId("a-1"), prospect_id=ProspectId("p1"), idempotency_key="k-stale"
        )
        h = build(prospects=[prospect("p1", status=status)], actions=[stale])
        report = h.engine.prepare()
        assert h.brain.calls == []
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.action("p1").last_error == f"safety: {status.value}"
        assert report.outcomes[0].status is ActionStatus.CANCELLED
        assert "safety.blocked" in h.event_types()

    def test_a_prospect_without_a_contact_address_is_paused_for_a_human(self):
        p = prospect("p1", contact=replace(prospect("p1").contact, phone=None))
        h = build(prospects=[p])
        h.engine.prepare()
        assert h.brain.calls == []
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.prospect("p1").status is ProspectStatus.PAUSED
        assert h.channel.attempts == ()

    def test_blocking_one_prospect_does_not_stop_the_others(self):
        no_phone = prospect("p1", contact=replace(prospect("p1").contact, phone=None))
        h = build(prospects=[no_phone, prospect("p2")])
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.status("p2") is ActionStatus.PROPOSED
