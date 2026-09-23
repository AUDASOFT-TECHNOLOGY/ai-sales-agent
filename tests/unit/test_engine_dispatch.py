"""Phase B: dispatch, failures, idempotency, SENDING safety, persistence across restarts."""

from dataclasses import replace
from datetime import timedelta

from engine_harness import FU, REM, Crash, ScriptedBrain, build, due, prospect, send
from factories import NOW, make_action

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionId,
    ActionStatus,
    Channel,
    Direction,
    MessageDraft,
    ProspectId,
    ProspectStatus,
)
from sales_core.engine.engine import UNCONFIRMED


def proposed(pid="p1", brain=None, **build_kw):
    """A harness that has prepared one proposal for `pid`."""
    h = build(
        prospects=[due(pid, FU)],
        brain=brain
        or ScriptedBrain(lambda r: send("Hi!", follow_up_in=timedelta(days=5), stage="engaged")),
        **build_kw,
    )
    h.engine.prepare()
    return h


class TestApprovedToSent:
    def test_approved_action_is_sent_and_everything_is_recorded(self):
        h = proposed()
        h.approve("p1")
        report = h.engine.dispatch()

        assert report.ok and h.status("p1") is ActionStatus.SENT
        (sent,) = h.channel.sent
        assert sent.draft == MessageDraft(text="Hi!")
        assert sent.recipient == "+15550000001" and sent.channel is Channel.WHATSAPP

        (message,) = h.repo.get_conversation(ProspectId("p1")).messages
        assert message.direction is Direction.OUTBOUND and message.text == "Hi!"
        assert message.external_id == "fake-1" and message.timestamp == NOW

        p = h.prospect("p1")
        assert p.stage == "engaged"
        assert p.schedule.last_contacted_at == NOW
        assert p.schedule.follow_up_count == 1
        assert p.schedule.next_action_type is FU
        assert p.schedule.next_action_due_at == NOW + timedelta(days=5)

    def test_sending_is_persisted_before_the_channel_is_called(self):
        h = proposed()
        h.approve("p1")
        h.engine.dispatch()
        assert h.channel.status_during_send == [ActionStatus.SENDING]

    def test_a_new_prospect_becomes_active_after_the_first_message(self):
        h = build(prospects=[prospect("p1")])
        h.engine.prepare()
        h.approve("p1")
        h.engine.dispatch()
        assert h.prospect("p1").status is ProspectStatus.ACTIVE

    def test_a_message_without_a_next_action_clears_the_schedule(self):
        h = build(prospects=[due("p1")], brain=ScriptedBrain(lambda r: send("Bye")))
        h.engine.prepare()
        h.approve("p1")
        h.engine.dispatch()
        assert h.prospect("p1").schedule.next_action_type is None

    def test_reminder_scheduled_with_a_message_is_applied_after_the_send(self):
        brain = ScriptedBrain(lambda r: send(follow_up_in=timedelta(days=1), next_type=REM))
        h = proposed(brain=brain)
        h.approve("p1")
        h.engine.dispatch()
        assert h.prospect("p1").schedule.next_action_type is REM

    def test_prospect_is_not_touched_by_a_proposal_only(self):
        h = proposed()
        assert h.prospect("p1") == due("p1", FU)

    def test_only_approved_actions_are_sent(self):
        h = build(prospects=[prospect("p1"), prospect("p2"), prospect("p3")])
        h.engine.prepare()
        h.approve("p2")
        h.engine.dispatch()
        assert [h.status(p) for p in ("p1", "p2", "p3")] == [
            ActionStatus.PROPOSED,
            ActionStatus.SENT,
            ActionStatus.PROPOSED,
        ]
        assert len(h.channel.sent) == 1


class TestRejectedApproval:
    def test_rejected_actions_are_never_sent_and_leave_the_prospect_unchanged(self):
        h = proposed()
        before = h.prospect("p1")
        h.reject("p1")
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.REJECTED
        assert h.channel.attempts == ()
        assert h.prospect("p1") == before


class TestHumanEditsAndPersistence:
    def test_human_edits_to_text_stage_and_next_action_are_honoured(self):
        h = proposed()
        h.human_edit(
            "p1",
            status=ActionStatus.APPROVED,
            draft=MessageDraft(text="Edited by a human"),
            next_stage="edited-stage",
            next_action_type=REM,
            next_action_due_at=NOW + timedelta(days=9),
        )
        h.engine.dispatch()
        assert h.channel.sent[0].draft.text == "Edited by a human"
        p = h.prospect("p1")
        assert p.stage == "edited-stage"
        assert p.schedule.next_action_type is REM
        assert p.schedule.next_action_due_at == NOW + timedelta(days=9)

    def test_proposal_survives_a_restart_and_a_delayed_approval(self):
        h = proposed()  # Brain proposed: stage "engaged", follow-up in 5 days
        h.clock.advance(timedelta(days=2))  # the human approves two days later
        h.approve("p1")

        def must_not_be_called(request):
            raise AssertionError("the Brain must not be consulted again at dispatch time")

        restarted = h.new_engine(ScriptedBrain(must_not_be_called))
        assert restarted.dispatch().ok
        assert h.status("p1") is ActionStatus.SENT
        p = h.prospect("p1")
        assert p.stage == "engaged"
        assert p.schedule.next_action_type is FU
        assert p.schedule.next_action_due_at == NOW + timedelta(days=5)
        assert p.schedule.last_contacted_at == NOW + timedelta(days=2)  # sent when dispatched


class TestFailedSends:
    def test_transient_failure_records_the_error_and_keeps_the_prospect_unchanged(self):
        h = proposed()
        before = h.prospect("p1")
        h.approve("p1")
        h.channel.fail_next(error_message="rate limited")
        report = h.engine.dispatch()

        action = h.action("p1")
        assert action.status is ActionStatus.APPROVED  # will be retried
        assert action.attempts == 1 and action.last_error == "rate limited"
        assert h.prospect("p1") == before
        assert h.repo.get_conversation(ProspectId("p1")).messages == ()
        assert h.events("action.send_failed")[0].details["error"] == "rate limited"
        assert report.outcomes[0].status is ActionStatus.APPROVED

    def test_a_transient_failure_is_retried_by_the_next_dispatch(self):
        h = proposed()
        h.approve("p1")
        h.channel.fail_next()
        h.engine.dispatch()
        assert h.engine.dispatch().ok
        assert h.status("p1") is ActionStatus.SENT
        assert len(h.channel.sent) == 1

    def test_fails_for_good_after_max_attempts(self):
        h = proposed()
        h.approve("p1")
        h.channel.fail_next(count=3)
        for _ in range(3):
            h.engine.dispatch()
        assert h.status("p1") is ActionStatus.FAILED
        assert h.action("p1").attempts == 3
        h.engine.dispatch()  # nothing left to retry
        assert len(h.channel.attempts) == 3

    def test_permanent_failure_fails_the_action_and_pauses_the_prospect_only(self):
        h = proposed()
        before = h.prospect("p1")
        h.approve("p1")
        h.channel.fail_next(transient=False, error_message="invalid number")
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.FAILED
        after = h.prospect("p1")
        assert after == replace(before, status=ProspectStatus.PAUSED)  # stage/schedule untouched
        assert "prospect.paused" in h.event_types()

    def test_a_failed_action_is_not_resent_by_later_dispatches(self):
        h = proposed()
        h.approve("p1")
        h.channel.fail_next(transient=False)
        h.engine.dispatch()
        h.engine.dispatch()
        assert len(h.channel.attempts) == 1


class TestSafetyAtSendTime:
    def test_opt_out_after_approval_blocks_the_send(self):
        h = proposed()
        h.approve("p1")
        h.repo.update_prospects([replace(h.prospect("p1"), status=ProspectStatus.OPTED_OUT)])
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.action("p1").last_error == "safety: opted_out"
        assert h.channel.attempts == ()
        assert "safety.blocked" in h.event_types()

    def test_a_prospect_paused_or_closed_after_approval_is_not_contacted(self):
        for status in (ProspectStatus.PAUSED, ProspectStatus.CLOSED):
            h = proposed()
            h.approve("p1")
            h.repo.update_prospects([replace(h.prospect("p1"), status=status)])
            h.engine.dispatch()
            assert h.channel.attempts == ()
            assert h.status("p1") is ActionStatus.CANCELLED

    def test_an_approved_action_without_a_draft_is_never_sent(self):
        h = build(prospects=[prospect("p1")])
        h.engine.prepare()
        h.human_edit("p1", status=ActionStatus.APPROVED, draft=None)
        h.engine.dispatch()
        assert h.channel.attempts == ()
        assert h.status("p1") is ActionStatus.CANCELLED

    def test_a_missing_prospect_fails_closed(self):
        orphan = make_action(
            action_id=ActionId("a-1"),
            prospect_id=ProspectId("ghost"),
            status=ActionStatus.APPROVED,
            draft=MessageDraft(text="Hi"),
            idempotency_key="k1",
        )
        h = build(actions=[orphan])
        h.engine.dispatch()
        assert h.channel.attempts == ()
        assert h.repo.list_actions()[0].status is ActionStatus.CANCELLED


class TestIdempotency:
    def test_duplicate_planner_execution_creates_one_action_and_one_brain_call(self):
        h = build(prospects=[due("p1"), prospect("p2")])
        h.engine.prepare()
        h.engine.prepare()
        h.new_engine().prepare()  # even from a restarted process
        assert len(h.repo.list_actions()) == 2
        assert len(h.brain.calls) == 2

    def test_dispatching_twice_sends_once(self):
        h = proposed()
        h.approve("p1")
        h.engine.dispatch()
        h.engine.dispatch()
        h.new_engine().dispatch()
        assert len(h.channel.attempts) == 1
        assert h.prospect("p1").schedule.follow_up_count == 1

    def test_an_already_sent_action_is_not_picked_up_again(self):
        h = proposed()
        h.approve("p1")
        h.engine.dispatch()
        # a stale human edit cannot resurrect it except by explicitly re-approving
        assert h.engine.dispatch().outcomes == ()

    def test_a_restarted_engine_does_not_replan_or_resend_after_a_full_cycle(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        h.engine.run_cycle()
        again = h.new_engine().run_cycle()
        assert again.outcomes == ()
        assert len(h.channel.sent) == 1


class TestActionStuckInSending:
    def _stuck(self):
        stuck = make_action(
            action_id=ActionId("a-1"),
            prospect_id=ProspectId("p1"),
            status=ActionStatus.SENDING,
            draft=MessageDraft(text="Hi"),
            idempotency_key="k-stuck",
        )
        return build(prospects=[prospect("p1")], actions=[stuck])

    def test_is_never_resent_and_is_flagged_for_a_human(self):
        h = self._stuck()
        report = h.engine.dispatch()
        assert h.channel.attempts == ()
        assert h.status("p1") is ActionStatus.SENDING
        assert h.action("p1").last_error.startswith(UNCONFIRMED)
        assert not report.ok and UNCONFIRMED in report.errors[0]
        assert len(h.events("action.send_unconfirmed")) == 1

    def test_keeps_being_reported_but_audited_only_once(self):
        h = self._stuck()
        h.engine.dispatch()
        second = h.new_engine().dispatch()
        assert h.channel.attempts == ()
        assert not second.ok  # still visible to whoever runs the engine
        assert len(h.events("action.send_unconfirmed")) == 1

    def test_a_process_crash_during_the_send_leaves_sending_and_nothing_is_resent(self):
        h = proposed()
        h.approve("p1")

        def dying_send(message):
            raise Crash

        h.channel.send = dying_send  # type: ignore[method-assign]
        try:
            h.engine.dispatch()
        except Crash:
            pass
        assert h.status("p1") is ActionStatus.SENDING

        del h.channel.send  # the "restarted" process gets a working channel again
        report = h.new_engine().dispatch()
        assert h.channel.attempts == ()
        assert not report.ok and h.status("p1") is ActionStatus.SENDING

    def test_a_channel_that_raises_leaves_the_outcome_unconfirmed(self):
        h = proposed()
        h.approve("p1")

        def broken_send(message):
            raise ConnectionError("socket closed mid-request")

        h.channel.send = broken_send  # type: ignore[method-assign]
        report = h.engine.dispatch()
        assert h.status("p1") is ActionStatus.SENDING
        assert UNCONFIRMED in h.action("p1").last_error
        assert not report.ok
        del h.channel.send
        h.engine.dispatch()
        assert h.channel.attempts == ()  # never retried automatically

    def test_if_sending_cannot_be_persisted_nothing_is_sent(self):
        h = proposed()
        h.approve("p1")
        h.repo.fail_next("save_actions")
        report = h.engine.dispatch()
        assert h.channel.attempts == ()
        assert not report.ok
        assert h.status("p1") is ActionStatus.APPROVED
        assert h.action("p1").attempts == 1


class TestBookkeepingAfterSend:
    def test_a_history_write_failure_is_reported_but_the_rest_completes(self):
        h = proposed()
        h.approve("p1")
        h.repo.fail_next("append_messages")
        report = h.engine.dispatch()
        assert h.status("p1") is ActionStatus.SENT
        assert h.prospect("p1").status is ProspectStatus.ACTIVE  # still updated
        assert h.repo.messages == ()
        assert any("append_message failed after send" in e for e in report.errors)
        assert "action.bookkeeping_failed" in h.event_types()
        assert len(h.channel.sent) == 1  # and never resent
