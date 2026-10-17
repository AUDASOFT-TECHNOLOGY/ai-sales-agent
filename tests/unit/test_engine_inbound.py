"""Phase C: inbound replies."""

from datetime import timedelta

import pytest
from engine_harness import (
    FU,
    REPLY,
    ScriptedBrain,
    build,
    due,
    inbound,
    phone_for,
    prospect,
    send,
)
from factories import NOW

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    Channel,
    ContactInfo,
    Direction,
    InboundMessage,
    ProspectId,
    ProspectStatus,
)
from sales_core.engine import (
    EngineConfig,
    InboundPolicy,
    InboundStatus,
    UnknownSenderPolicy,
)


def handle(h, message=None, **kw):
    """Fast path then background processing, as the webhook would do."""
    receipt = h.engine.receive_inbound(message or inbound(**kw))
    return receipt, h.engine.process_inbound(receipt)


class TestReceive:
    def test_stores_the_message_and_returns_without_calling_the_brain(self):
        h = build(prospects=[prospect("p1")])
        receipt = h.engine.receive_inbound(inbound("Hello there"))
        assert receipt.status is InboundStatus.STORED
        assert receipt.prospect_id == "p1"
        (message,) = h.repo.get_conversation(ProspectId("p1")).messages
        assert message.direction is Direction.INBOUND and message.text == "Hello there"
        assert message.external_id == "wamid.1"
        assert h.prospect("p1").schedule.last_inbound_at == NOW
        assert h.brain.calls == [] and h.repo.list_actions() == []
        assert "inbound.received" in h.event_types()

    def test_the_sender_is_found_ignoring_phone_formatting(self):
        h = build(prospects=[prospect("p1")])
        digits_only = phone_for("p1").lstrip("+")  # providers often send no "+"
        assert h.engine.receive_inbound(inbound(sender=digits_only)).prospect_id == "p1"

    def test_unknown_senders_are_audited_and_nothing_is_stored(self):
        h = build(prospects=[prospect("p1")])
        receipt = h.engine.receive_inbound(inbound(sender="+19998887777"))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER
        assert h.repo.messages == ()
        assert "inbound.unknown_sender" in h.event_types()
        assert h.engine.process_inbound(receipt).outcomes == ()

    def test_email_senders_are_matched_by_address(self):
        from dataclasses import replace

        p = prospect("p1")
        p = replace(p, contact=replace(p.contact, email="alex@example.test"))
        h = build(prospects=[p])
        receipt = h.engine.receive_inbound(
            inbound(sender="ALEX@example.test", channel=Channel.EMAIL)
        )
        assert receipt.status is InboundStatus.STORED

    def test_duplicate_webhook_delivery_is_detected_by_provider_message_id(self):
        h = build(prospects=[prospect("p1")])
        first = h.engine.receive_inbound(inbound(external_id="wamid.A"))
        second = h.engine.receive_inbound(inbound(external_id="wamid.A"))
        other = h.engine.receive_inbound(inbound(external_id="wamid.B"))
        assert (first.status, second.status, other.status) == (
            InboundStatus.STORED,
            InboundStatus.DUPLICATE,
            InboundStatus.STORED,
        )
        assert len(h.repo.messages) == 2
        assert first.message_id == second.message_id

    def test_a_redelivery_repairs_an_interrupted_receive(self):
        h = build(prospects=[prospect("p1")])
        h.repo.fail_next("update_prospects")  # message stored, prospect update fails
        with pytest.raises(RuntimeError):
            h.engine.receive_inbound(inbound())
        assert h.prospect("p1").schedule.last_inbound_at is None
        receipt = h.engine.receive_inbound(inbound())  # the provider redelivers
        assert receipt.status is InboundStatus.DUPLICATE
        assert h.prospect("p1").schedule.last_inbound_at == NOW
        assert len(h.repo.messages) == 1

    def test_dry_run_persists_nothing(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.DRY_RUN)
        receipt = h.engine.receive_inbound(inbound())
        assert receipt.status is InboundStatus.DRY_RUN
        assert h.repo.messages == ()
        assert h.engine.process_inbound(receipt).outcomes == ()


class TestProcessReply:
    def test_a_reply_creates_a_reply_action_proposed_for_approval(self):
        h = build(prospects=[prospect("p1")], brain=ScriptedBrain(lambda r: send("Glad you wrote")))
        _, report = handle(h)
        assert report.ok
        action = h.action("p1")
        assert action.action_type is REPLY
        assert action.status is ActionStatus.PROPOSED
        assert action.draft.text == "Glad you wrote"
        assert h.channel.attempts == ()  # approval mode: nothing is sent yet
        (request,) = h.brain.calls
        assert [m.text for m in request.conversation.messages] == ["Hi, tell me more"]
        assert request.action.action_type is REPLY

    def test_the_prospect_becomes_active_and_stale_schedule_is_cleared(self):
        h = build(prospects=[prospect("p1")])
        handle(h)
        p = h.prospect("p1")
        assert p.status is ProspectStatus.ACTIVE
        assert p.schedule.next_action_type is None

    def test_stale_outreach_is_cancelled_when_the_prospect_replies(self):
        h = build(prospects=[due("p1", FU)])
        h.engine.prepare()
        outreach = h.action("p1")
        assert outreach.status is ActionStatus.PROPOSED
        handle(h)
        by_type = {a.action_type: a for a in h.actions_for("p1")}
        assert by_type[FU].status is ActionStatus.CANCELLED
        assert by_type[FU].last_error == "superseded by inbound reply"
        assert by_type[REPLY].status is ActionStatus.PROPOSED

    def test_approved_reply_is_sent_by_dispatch(self):
        h = build(prospects=[prospect("p1")])
        handle(h)
        h.approve("p1")
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.SENT
        assert len(h.channel.sent) == 1

    def test_autonomous_mode_answers_immediately(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        handle(h)
        assert h.status("p1") is ActionStatus.SENT
        assert len(h.channel.sent) == 1
        assert h.channel.status_during_send == [ActionStatus.SENDING]

    def test_duplicate_webhooks_produce_one_reply_and_one_brain_call(self):
        h = build(prospects=[prospect("p1")])
        handle(h)
        r2, second = handle(h)  # provider redelivers the same message
        assert r2.status is InboundStatus.DUPLICATE
        assert len(h.actions_for("p1")) == 1
        assert len(h.brain.calls) == 1
        assert second.outcomes[0].detail == "reply already handled"

    def test_processing_the_same_receipt_twice_is_harmless(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        receipt, _ = handle(h)
        h.engine.process_inbound(receipt)
        h.new_engine().process_inbound(receipt)
        assert len(h.channel.attempts) == 1 and len(h.brain.calls) == 1

    def test_a_second_message_supersedes_the_unanswered_first_reply(self):
        h = build(prospects=[prospect("p1")])
        handle(h, inbound("first", external_id="w1"))
        handle(h, inbound("second", external_id="w2"))
        statuses = sorted(a.status.value for a in h.actions_for("p1"))
        assert statuses == ["cancelled", "proposed"]
        assert [m.text for m in h.brain.calls[-1].conversation.messages] == ["first", "second"]

    def test_brain_failure_on_a_reply_is_recorded_and_retried_by_the_next_prepare(self):
        state = {"fail": True}

        def flaky(request):
            if state["fail"]:
                raise RuntimeError("model down")
            return send()

        h = build(prospects=[prospect("p1")], brain=ScriptedBrain(flaky))
        _, report = handle(h)
        assert not report.ok
        assert h.status("p1") is ActionStatus.PLANNED and h.action("p1").attempts == 1
        state["fail"] = False
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.PROPOSED


class TestOptOut:
    @pytest.mark.parametrize("text", ["STOP", " stop ", "Stop!", "stop."])
    def test_explicit_opt_out_marks_the_prospect_and_never_calls_the_brain(self, text):
        h = build(prospects=[prospect("p1")])
        _, report = handle(h, inbound(text))
        assert report.ok
        assert h.brain.calls == []
        p = h.prospect("p1")
        assert p.status is ProspectStatus.OPTED_OUT and p.schedule.next_action_type is None
        assert h.status("p1") is ActionStatus.COMPLETED
        assert h.action("p1").action_type is REPLY
        assert "prospect.opted_out" in h.event_types()
        assert h.channel.attempts == ()

    def test_opt_out_cancels_open_outreach(self):
        h = build(prospects=[due("p1", FU)])
        h.engine.prepare()
        h.approve("p1")
        handle(h, inbound("STOP"))
        statuses = {a.action_type: a.status for a in h.actions_for("p1")}
        assert statuses[FU] is ActionStatus.CANCELLED
        h.engine.dispatch()
        assert h.channel.attempts == ()

    def test_opted_out_prospects_are_never_planned_or_contacted_again(self):
        h = build(prospects=[due("p1", FU)])
        handle(h, inbound("STOP"))
        h.clock.advance(timedelta(days=30))
        h.engine.prepare()
        h.engine.dispatch()
        assert h.brain.calls == [] and h.channel.attempts == ()

    def test_keywords_are_configurable_and_matched_as_a_whole_message(self):
        h = build(prospects=[prospect("p1")], opt_out_keywords=frozenset({"arrêtez", "stop"}))
        handle(h, inbound("Arrêtez"))
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT

    def test_a_sentence_containing_stop_is_not_an_opt_out(self):
        h = build(prospects=[prospect("p1")])
        handle(h, inbound("Please don't stop calling, I'm interested"))
        assert h.prospect("p1").status is ProspectStatus.ACTIVE
        assert len(h.brain.calls) == 1

    def test_opt_out_is_idempotent_on_webhook_retries(self):
        h = build(prospects=[prospect("p1")])
        handle(h, inbound("STOP"))
        handle(h, inbound("STOP"))
        assert len(h.actions_for("p1")) == 1
        assert h.events("prospect.opted_out") and len(h.events("prospect.opted_out")) == 1

    def test_stop_from_a_paused_prospect_still_opts_them_out(self):
        h = build(prospects=[prospect("p1", status=ProspectStatus.PAUSED)])
        handle(h, inbound("STOP"))
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT


class TestSafetyForReplies:
    @pytest.mark.parametrize(
        "status", [ProspectStatus.OPTED_OUT, ProspectStatus.PAUSED, ProspectStatus.CLOSED]
    )
    def test_replies_from_blocked_prospects_are_stored_but_not_answered(self, status):
        h = build(prospects=[prospect("p1", status=status)])
        receipt, _ = handle(h)
        assert receipt.status is InboundStatus.STORED
        assert len(h.repo.messages) == 1  # a human can read it
        assert h.brain.calls == []
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.action("p1").last_error == f"safety: {status.value}"
        assert h.prospect("p1").status is status  # unchanged
        assert h.channel.attempts == ()


class TestQuotaBypass:
    def test_replies_never_consume_the_daily_outreach_slots(self):
        h = build(
            prospects=[
                prospect("p1"),
                prospect("p2"),
                prospect("p3"),
                prospect("n4"),
                prospect("n5"),
            ],
            daily_action_limit=2,
        )
        for pid in ("p1", "p2", "p3"):
            handle(h, inbound(pid=pid, external_id=f"w-{pid}"))
        assert all(h.status(p) is ActionStatus.PROPOSED for p in ("p1", "p2", "p3"))

        h.engine.prepare()  # both outreach slots are still free
        planned = [a for a in h.repo.list_actions() if a.action_type is not REPLY]
        assert len(planned) == 2

    def test_replies_are_processed_even_when_the_daily_quota_is_exhausted(self):
        h = build(prospects=[prospect(f"p{i}") for i in range(1, 4)], daily_action_limit=2)
        h.engine.prepare()  # p1 and p2 use the quota; p3 gets nothing
        assert len(h.repo.list_actions()) == 2
        handle(h, inbound(pid="p3", external_id="w3"))
        assert h.status("p3") is ActionStatus.PROPOSED
        assert h.action("p3").action_type is REPLY

    def test_reply_actions_are_not_counted_by_later_planning(self):
        h = build(
            prospects=[prospect("p1"), prospect("p2")],
            mode=ApprovalMode.AUTONOMOUS,
            daily_action_limit=1,
        )
        handle(h, inbound(pid="p1"))
        assert h.status("p1") is ActionStatus.SENT
        report = h.engine.prepare()
        assert h.status("p2") is ActionStatus.APPROVED  # the one outreach slot was still free
        assert report.ok


class TestRecovery:
    def test_a_stored_but_unprocessed_reply_is_picked_up_later_exactly_once(self):
        h = build(prospects=[prospect("p1")])
        h.engine.receive_inbound(inbound())  # the process died before process_inbound
        report = h.new_engine().recover_pending_replies()
        assert report.ok and h.status("p1") is ActionStatus.PROPOSED
        again = h.new_engine().recover_pending_replies()
        assert again.outcomes == () and len(h.brain.calls) == 1

    def test_recovery_ignores_replies_we_have_already_answered(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        handle(h)
        h.clock.advance(timedelta(minutes=1))
        assert h.engine.recover_pending_replies().outcomes == ()
        assert len(h.channel.sent) == 1

    def test_recovery_is_skipped_in_dry_run(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.DRY_RUN)
        assert h.engine.recover_pending_replies().outcomes == ()


def test_inbound_message_contract_requires_a_provider_message_id():
    with pytest.raises(ValueError, match="external_id"):
        InboundMessage(
            channel=Channel.WHATSAPP, sender="+1", external_id="", text="x", timestamp=NOW
        )


def test_engine_config_rejects_invalid_values():
    with pytest.raises(ValueError, match="max_attempts"):
        EngineConfig(mode=ApprovalMode.APPROVAL, max_attempts=0)
    with pytest.raises(ValueError, match="daily_action_limit"):
        EngineConfig(mode=ApprovalMode.APPROVAL, daily_action_limit=-1)


class TestKnowsInboundSender:
    """A question a reader of someone's own mailbox asks before letting a message through."""

    def email_prospect(self, pid="p1", address="Alex@Example.com"):
        return prospect(
            pid,
            contact=ContactInfo(
                phone=phone_for(pid), email=address, preferred_channel=Channel.EMAIL
            ),
        )

    def test_a_prospect_is_known_by_the_address_it_writes_from_whatever_its_case(self):
        h = build(prospects=[self.email_prospect()])
        assert h.engine.knows_inbound_sender(Channel.EMAIL, "alex@example.com") is True
        assert h.engine.knows_inbound_sender(Channel.EMAIL, " ALEX@EXAMPLE.COM ") is True

    def test_a_stranger_is_not_known(self):
        h = build(prospects=[self.email_prospect()])
        assert h.engine.knows_inbound_sender(Channel.EMAIL, "stranger@elsewhere.example") is False

    def test_an_identity_is_known_on_its_own_channel(self):
        h = build(prospects=[self.email_prospect()])
        assert h.engine.knows_inbound_sender(Channel.WHATSAPP, phone_for("p1")) is True
        assert h.engine.knows_inbound_sender(Channel.WHATSAPP, "+19998887777") is False

    def test_it_only_asks_it_stores_nothing_and_audits_nothing(self):
        h = build(prospects=[self.email_prospect()])
        h.engine.knows_inbound_sender(Channel.EMAIL, "alex@example.com")
        h.engine.knows_inbound_sender(Channel.EMAIL, "stranger@elsewhere.example")
        assert h.repo.messages == () and h.event_types() == []
        assert h.repo.list_actions() == [] and h.brain.calls == []

    def test_a_policy_that_would_engage_strangers_does_not_make_one_known(self):
        policy = InboundPolicy(
            spontaneous_conversations=True, unknown_sender=UnknownSenderPolicy.ENGAGE
        )
        h = build(prospects=[self.email_prospect()], inbound=policy)
        assert h.engine.knows_inbound_sender(Channel.EMAIL, "stranger@elsewhere.example") is False
        assert h.repo.find_prospect_by_contact(email="stranger@elsewhere.example") is None
        assert h.event_types() == []

    def test_receiving_is_unchanged(self):
        h = build(prospects=[self.email_prospect()])
        receipt = h.engine.receive_inbound(
            inbound("Hi", sender="alex@example.com", channel=Channel.EMAIL, external_id="m1")
        )
        assert receipt.status is InboundStatus.STORED and receipt.prospect_id == "p1"
        stranger = h.engine.receive_inbound(
            inbound("Hi", sender="x@elsewhere.example", channel=Channel.EMAIL, external_id="m2")
        )
        assert stranger.status is InboundStatus.UNKNOWN_SENDER
