"""A person in charge of a conversation: nothing automatic reaches the prospect, a stop request is
still honoured, the Brain's handover waits visibly for a person, and a person's own message goes out
through the same channel and safety as the agent's (no real message: fakes only)."""

from datetime import timedelta

import pytest
from engine_harness import (
    FU,
    REPLY,
    ScriptedBrain,
    build,
    decide,
    due,
    inbound,
    prospect,
    send,
)
from factories import CLIENT, NOW

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    Channel,
    DecisionType,
    Direction,
    ProspectId,
    ProspectStatus,
    Scheduling,
    SendResult,
    SendStatus,
)
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.engine import ControlUnavailableError, ManualSendError
from sales_integrations.fakes import FakeConversationControl

P1 = ProspectId("p1")


def control() -> FakeConversationControl:
    return FakeConversationControl(CLIENT)


def handle(h, message=None, **kw):
    receipt = h.engine.receive_inbound(message or inbound(**kw))
    return receipt, h.engine.process_inbound(receipt)


class TestNothingAutomaticWhileAPersonIsInCharge:
    def test_no_first_contact_follow_up_or_brain_call(self):
        h = build(
            prospects=[due("p1", FU), prospect("p2")],
            control=control(),
            mode=ApprovalMode.AUTONOMOUS,
        )
        h.engine.take_over(P1, by="client:a")
        h.engine.run_cycle()
        assert h.brain.called_for() == ["p2"]  # the other prospect is still worked
        assert [a.prospect_id for a in h.repo.list_actions()] == ["p2"]
        assert {m.recipient for m in h.channel.sent} == {h.prospect("p2").contact.phone}

    def test_taking_over_cancels_what_was_waiting_and_keeps_the_schedule(self):
        h = build(prospects=[due("p1", FU)], control=control())
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.PROPOSED
        before = h.prospect("p1").schedule
        h.engine.take_over(P1, by="client:a", reason="wants a call")
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.prospect("p1").status is ProspectStatus.PAUSED  # visible as waiting for a person
        assert h.prospect("p1").schedule.next_action_due_at == before.next_action_due_at
        assert h.engine.control_of(P1).taken_by == "client:a"
        assert "conversation.taken_over" in h.event_types()

    def test_an_approved_action_is_not_sent_once_a_person_is_in_charge(self):
        h = build(prospects=[due("p1", FU)], control=control())
        h.engine.prepare()
        h.approve("p1")
        h.engine._control.take(P1, by="client:a", reason=None, at=NOW)  # control set elsewhere
        h.engine.dispatch()
        assert h.channel.attempts == ()
        assert h.status("p1") is ActionStatus.CANCELLED
        assert "human_control" in h.action("p1").last_error

    def test_a_reply_is_stored_but_not_answered(self):
        h = build(prospects=[prospect("p1")], control=control(), mode=ApprovalMode.AUTONOMOUS)
        h.engine.take_over(P1, by="client:a")
        _, report = handle(h, text="Can you call me?")
        assert report.ok and h.brain.calls == [] and h.channel.attempts == ()
        (message,) = h.repo.get_conversation(P1).messages
        assert message.direction is Direction.INBOUND
        assert h.action("p1").status is ActionStatus.COMPLETED
        # Giving the conversation back does not answer it late on its own.
        h.engine.release(P1, by="client:a")
        h.engine.recover_pending_replies()
        assert h.brain.calls == [] and h.channel.attempts == ()

    def test_an_unreadable_control_store_never_lets_an_automatic_message_through(self):
        store = control()
        h = build(prospects=[due("p1", FU)], control=store, mode=ApprovalMode.AUTONOMOUS)
        h.engine.prepare()  # proposes and approves
        store.fail_reads = True
        h.engine.dispatch()
        assert h.channel.attempts == ()
        with pytest.raises(OSError):
            h.engine.prepare()  # planning blind is refused outright

    def test_without_a_store_the_engine_behaves_as_before(self):
        h = build(prospects=[due("p1", FU)], mode=ApprovalMode.AUTONOMOUS)
        h.engine.run_cycle()
        assert len(h.channel.sent) == 1
        with pytest.raises(ControlUnavailableError):
            h.engine.take_over(P1, by="client:a")


class TestStopRequests:
    def test_a_stop_while_a_person_is_in_charge_is_honoured_and_survives_the_release(self):
        h = build(prospects=[due("p1", FU)], control=control(), mode=ApprovalMode.AUTONOMOUS)
        h.engine.take_over(P1, by="client:a")
        handle(h, text="STOP")
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT
        assert h.prospect("p1").schedule.next_action_type is None
        outcome = h.engine.release(P1, by="client:a", resume_after=timedelta(days=2))
        assert outcome.released and outcome.status == "opted_out"
        assert outcome.next_follow_up_at is None
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT
        assert h.prospect("p1").schedule.next_action_type is None
        h.engine.run_cycle()
        assert h.channel.attempts == () and h.brain.calls == []

    def test_a_stop_cancels_the_follow_ups_even_if_the_person_sends_afterwards_nothing(self):
        h = build(prospects=[due("p1", FU)], control=control())
        h.engine.prepare()
        handle(h, text="stop")
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT
        assert {a.status for a in h.actions_for("p1") if a.action_type is not REPLY} == {
            ActionStatus.CANCELLED
        }

    def test_a_person_cannot_write_to_someone_who_opted_out(self):
        h = build(prospects=[prospect("p1")], control=control())
        h.engine.take_over(P1, by="client:a")
        handle(h, text="stop")
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(P1, "Hello again", idempotency_key="k-0000001", actor="client:a")
        assert refusal.value.code == "opted_out"
        assert h.channel.attempts == ()
        assert "message.manual_refused" in h.event_types()


class TestHandoffByTheBrain:
    def test_the_conversation_waits_visibly_for_a_person(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.HANDOFF_TO_HUMAN, rationale="angry"))
        h = build(prospects=[due("p1", FU)], brain=brain, control=control())
        h.engine.prepare()
        found = h.engine.control_of(P1)
        assert found.awaiting_human and found.reason == "angry"
        assert h.prospect("p1").status is ProspectStatus.PAUSED
        assert "conversation.awaiting_human" in h.event_types()
        h.engine.prepare()
        assert len(brain.calls) == 1  # nothing more is asked of the Brain for this one

    def test_a_person_taking_it_keeps_the_reason(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.HANDOFF_TO_HUMAN, rationale="angry"))
        h = build(prospects=[due("p1", FU)], brain=brain, control=control())
        h.engine.prepare()
        taken = h.engine.take_over(P1, by="client:a")
        assert taken.taken_by == "client:a" and not taken.awaiting_human and taken.reason == "angry"


class TestRelease:
    def test_follow_ups_resume_two_days_later_and_the_count_starts_over(self):
        base = due("p1", FU)
        schedule = base.schedule.__class__(
            next_action_type=FU, next_action_due_at=NOW - timedelta(hours=1), follow_up_count=2
        )
        h = build(prospects=[due("p1", FU, schedule=schedule)], control=control())
        h.engine.take_over(P1, by="client:a")
        outcome = h.engine.release(P1, by="client:a", resume_after=timedelta(days=2))
        assert outcome.released and outcome.status == "active"
        assert outcome.next_follow_up_at == NOW + timedelta(days=2)
        p = h.prospect("p1")
        assert p.schedule.follow_up_count == 0 and p.schedule.next_action_type is FU
        assert h.engine.control_of(P1).released_at == NOW
        h.engine.prepare()
        assert h.brain.calls == []  # not yet due
        h.clock.advance(timedelta(days=2, minutes=1))
        h.engine.prepare()
        assert h.brain.called_for() == ["p1"]

    def test_a_future_follow_up_already_planned_is_kept(self):
        schedule = prospect("p1").schedule.__class__(
            next_action_type=FU, next_action_due_at=NOW + timedelta(days=5)
        )
        h = build(prospects=[due("p1", FU, schedule=schedule)], control=control())
        h.engine.take_over(P1, by="client:a")
        outcome = h.engine.release(P1, by="client:a", resume_after=timedelta(days=2))
        assert outcome.next_follow_up_at == NOW + timedelta(days=5)

    def test_releasing_a_conversation_nobody_held_changes_nothing(self):
        h = build(prospects=[due("p1", FU)], control=control())
        outcome = h.engine.release(P1, by="client:a", resume_after=timedelta(days=2))
        assert not outcome.released
        assert h.prospect("p1").status is ProspectStatus.ACTIVE

    def test_a_handed_over_conversation_returns_to_the_agent(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.HANDOFF_TO_HUMAN))
        talked = Scheduling(
            next_action_type=FU,
            next_action_due_at=NOW - timedelta(hours=1),
            last_contacted_at=NOW - timedelta(days=3),
        )
        h = build(prospects=[due("p1", FU, schedule=talked)], brain=brain, control=control())
        h.engine.prepare()
        outcome = h.engine.release(P1, by="client:a", resume_after=timedelta(days=2))
        assert outcome.status == "active" and outcome.next_follow_up_at == NOW + timedelta(days=2)

    def test_a_never_contacted_prospect_goes_back_to_new_and_is_first_contacted(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.HANDOFF_TO_HUMAN))
        h = build(prospects=[prospect("p1")], brain=brain, control=control())
        h.engine.prepare()
        outcome = h.engine.release(P1, by="client:a", resume_after=timedelta(days=2))
        assert outcome.status == "new" and outcome.next_follow_up_at is None


class TestManualMessages:
    def test_it_goes_out_on_the_prospects_channel_and_is_stored_as_human(self):
        h = build(prospects=[prospect("p1")], control=control())
        h.engine.take_over(P1, by="client:a")
        receipt = h.engine.send_manual(
            P1, "  Hello, it is Sam  ", idempotency_key="k-0000001", actor="client:a"
        )
        (sent,) = h.channel.sent
        assert (
            sent.draft.text == "Hello, it is Sam"
            and sent.recipient == h.prospect("p1").contact.phone
        )
        (message,) = h.repo.get_conversation(P1).messages
        assert message.direction is Direction.OUTBOUND and message.channel is Channel.WHATSAPP
        assert message.metadata["author"] == "human" and message.metadata["actor"] == "client:a"
        assert receipt.channel == "whatsapp" and not receipt.replayed
        assert h.prospect("p1").schedule.last_contacted_at is not None
        assert "message.manual_sent" in h.event_types()
        assert h.engine.control_of(P1).mode.value == "human"

    def test_the_same_key_never_sends_twice(self):
        h = build(prospects=[prospect("p1")], control=control())
        h.engine.take_over(P1, by="client:a")
        first = h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        again = h.new_engine().send_manual(
            P1, "Hello", idempotency_key="k-0000001", actor="client:a"
        )
        assert len(h.channel.attempts) == 1 and len(h.repo.get_conversation(P1).messages) == 1
        assert again.replayed and again.message_id == first.message_id

    def test_a_conversation_that_is_not_in_human_hands_is_refused(self):
        h = build(prospects=[prospect("p1")], control=control())
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        assert refusal.value.code == "not_in_human_mode" and h.channel.attempts == ()

    def test_it_taking_a_waiting_conversation_by_writing_to_it(self):
        brain = ScriptedBrain(lambda r: decide(DecisionType.HANDOFF_TO_HUMAN))
        h = build(prospects=[due("p1", FU)], brain=brain, control=control())
        h.engine.prepare()
        h.engine.send_manual(P1, "On it", idempotency_key="k-0000001", actor="client:a")
        found = h.engine.control_of(P1)
        assert found.taken_by == "client:a" and not found.awaiting_human

    @pytest.mark.parametrize("text", ["", "   \n"])
    def test_an_empty_message_is_refused(self, text):
        h = build(prospects=[prospect("p1")], control=control())
        h.engine.take_over(P1, by="client:a")
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(P1, text, idempotency_key="k-0000001", actor="client:a")
        assert refusal.value.code == "empty"

    def test_an_unknown_prospect_is_refused(self):
        h = build(prospects=[prospect("p1")], control=control())
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(
                ProspectId("nobody"), "Hello", idempotency_key="k-0000001", actor="client:a"
            )
        assert refusal.value.code == "unknown_prospect"

    def test_a_channel_that_says_not_now_leaves_nothing_to_be_sent_later(self):
        h = build(prospects=[prospect("p1")], control=control())
        h.engine.take_over(P1, by="client:a")
        h.channel.queue_results(
            SendResult(
                status=SendStatus.FAILED_TRANSIENT,
                timestamp=NOW,
                error_code="template_required",
                error_message="outside the 24-hour window and no approved template",
                metadata={DEFERRED_KEY: DEFERRED_VALUE},
            )
        )
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        assert refusal.value.code == "not_now" and "24-hour window" in refusal.value.message
        assert h.repo.get_conversation(P1).messages == ()
        h.engine.dispatch()  # the dispatcher finds nothing to send
        assert len(h.channel.attempts) == 1

    def test_a_permanent_refusal_is_reported_and_not_retried_by_the_same_key(self):
        h = build(prospects=[prospect("p1")], control=control())
        h.engine.take_over(P1, by="client:a")
        h.channel.fail_next(transient=False, error_message="number not on WhatsApp")
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        assert (
            refusal.value.code == "send_failed"
            and "number not on WhatsApp" in refusal.value.message
        )
        with pytest.raises(ManualSendError):
            h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        assert len(h.channel.attempts) == 1

    def test_dry_run_sends_nothing(self):
        h = build(prospects=[prospect("p1")], control=control(), mode=ApprovalMode.DRY_RUN)
        with pytest.raises(ManualSendError) as refusal:
            h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        assert refusal.value.code == "dry_run"

    def test_it_answers_on_the_channel_the_prospect_last_wrote_on(self):
        from dataclasses import replace

        p = prospect("p1")
        p = replace(p, contact=replace(p.contact, email="alex@example.test"))
        h = build(prospects=[p], control=control())
        handle(h, text="Hi", sender="alex@example.test", channel=Channel.EMAIL)
        # The only channel of this client is WhatsApp: the e-mail has no adapter, so it falls back.
        h.engine.take_over(P1, by="client:a")
        h.engine.send_manual(P1, "Hello", idempotency_key="k-0000001", actor="client:a")
        assert h.channel.sent[0].channel is Channel.WHATSAPP


def test_a_released_conversation_is_worked_again_by_the_agent():
    h = build(prospects=[due("p1", FU)], control=control(), mode=ApprovalMode.AUTONOMOUS)
    h.engine.take_over(P1, by="client:a")
    h.engine.run_cycle()
    assert h.channel.attempts == ()
    h.engine.release(P1, by="client:a")  # no wait asked: the follow-up is due at once
    h.engine.run_cycle()
    assert len(h.channel.sent) == 1


def test_send_decisions_of_a_brain_are_not_used_while_held():
    h = build(
        prospects=[due("p1", FU)],
        control=control(),
        brain=ScriptedBrain(lambda r: send("auto")),
        mode=ApprovalMode.AUTONOMOUS,
    )
    h.engine.take_over(P1, by="client:a")
    h.engine.run_cycle()
    assert h.brain.calls == []
