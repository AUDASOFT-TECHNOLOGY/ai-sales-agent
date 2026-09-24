"""Slice 3: the Engine tells inbound conversations from outbound activity.

Inbound handling is outside the daily outbound quota, is asked of the Brain with
`ContactOrigin.INBOUND`, and a stranger is ignored or engaged according to `InboundPolicy`.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from engine_harness import FU, REPLY, ScriptedBrain, build, decide, due, inbound, prospect, send
from factories import NOW

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    Channel,
    ContactOrigin,
    DecisionType,
    Direction,
    Fact,
    FactStatus,
    ProspectId,
    ProspectStatus,
    SalesActionType,
)
from sales_core.engine import EngineConfig, InboundPolicy, InboundStatus, UnknownSenderPolicy
from sales_core.ports import FactsReader

STRANGER = "+19998887777"
STRANGER_ID = f"inbound-2026-01-15-{STRANGER.lstrip('+')}"

DISABLED = InboundPolicy()
IGNORE = InboundPolicy(spontaneous_conversations=True, unknown_sender=UnknownSenderPolicy.IGNORE)
ENGAGE = InboundPolicy(spontaneous_conversations=True, unknown_sender=UnknownSenderPolicy.ENGAGE)


def handle(h, message=None, **kw):
    """Fast path then background processing, as the webhook does."""
    receipt = h.engine.receive_inbound(message or inbound(**kw))
    return receipt, h.engine.process_inbound(receipt)


def stranger(text="Hello, I have a question", external_id="wamid.s1", **kw):
    return inbound(text, sender=STRANGER, external_id=external_id, **kw)


class Facts:
    def __init__(self, by_prospect):
        self.by_prospect = by_prospect
        self.asked = []

    def get_facts(self, prospect_id):
        self.asked.append(prospect_id)
        return self.by_prospect.get(prospect_id, ())


def fact(key="k", value="v", status=FactStatus.DISCOVERED):
    return Fact(key=key, value=value, status=status)


class TestPolicyContract:
    def test_default_is_the_safe_side(self):
        assert EngineConfig(mode=ApprovalMode.APPROVAL).inbound == DISABLED
        assert not DISABLED.engages_unknown_senders and not IGNORE.engages_unknown_senders
        assert ENGAGE.engages_unknown_senders

    def test_engage_needs_spontaneous_conversations(self):
        with pytest.raises(ValueError, match="requires spontaneous_conversations"):
            InboundPolicy(unknown_sender=UnknownSenderPolicy.ENGAGE)

    def test_the_facts_reader_port_is_a_protocol_the_test_double_satisfies(self):
        assert isinstance(Facts({}), FactsReader)


class TestExistingProspect:  # A
    def test_the_brain_is_asked_with_an_inbound_origin(self):
        h = build(prospects=[prospect("p1")])
        _, report = handle(h)
        assert report.ok
        (request,) = h.brain.calls
        assert request.conversation.origin is ContactOrigin.INBOUND
        assert request.action.action_type is REPLY
        assert h.action("p1").action_type is REPLY

    def test_the_reply_does_not_consume_the_outbound_quota(self):
        h = build(prospects=[prospect("p1"), prospect("p2")], daily_action_limit=1)
        handle(h, inbound(pid="p1"))
        h.engine.prepare()
        planned = [a for a in h.repo.list_actions() if a.action_type is not REPLY]
        assert [a.prospect_id for a in planned] == ["p2"]  # the single outreach slot was free

    def test_no_duplicate_prospect_or_conversation(self):
        h = build(prospects=[prospect("p1")])
        handle(h)
        handle(h, inbound("more", external_id="wamid.2"))
        assert [p.prospect_id for p in h.repo.list_prospects()] == ["p1"]
        assert len({m.conversation_id for m in h.repo.messages}) == 1

    def test_stale_planned_outbound_is_neutralised(self):
        h = build(prospects=[due("p1", FU)])
        h.engine.prepare()
        handle(h)
        by_type = {a.action_type: a for a in h.actions_for("p1")}
        assert by_type[FU].status is ActionStatus.CANCELLED
        assert by_type[REPLY].status is ActionStatus.PROPOSED
        assert h.prospect("p1").schedule.next_action_type is None

    def test_a_known_prospect_is_handled_whatever_the_policy(self):
        for policy in (DISABLED, IGNORE, ENGAGE):
            h = build(prospects=[prospect("p1")], inbound=policy)
            handle(h)
            assert len(h.brain.calls) == 1


class TestUnknownContact:
    @pytest.mark.parametrize(("policy", "label"), [(DISABLED, "disabled"), (IGNORE, "ignore")])
    def test_a_stranger_is_audited_and_nothing_else_happens(self, policy, label):  # B, C
        h = build(prospects=[prospect("p1")], inbound=policy)
        receipt, report = handle(h, stranger())
        assert receipt.status is InboundStatus.UNKNOWN_SENDER and receipt.prospect_id is None
        assert report.outcomes == () and report.ok
        assert [p.prospect_id for p in h.repo.list_prospects()] == ["p1"]  # nothing created
        assert h.repo.messages == () and h.repo.list_actions() == []
        assert h.brain.calls == [] and h.channel.attempts == ()
        (event,) = h.events("inbound.unknown_sender")
        assert event.details["policy"] == label and event.details["sender"] == STRANGER

    def test_engage_creates_the_prospect_and_asks_the_brain(self):  # D
        h = build(inbound=ENGAGE, brain=ScriptedBrain(lambda r: send("Welcome")))
        receipt, report = handle(h, stranger())
        assert receipt.status is InboundStatus.STORED and receipt.prospect_id == STRANGER_ID
        assert report.ok
        assert h.prospect(STRANGER_ID).status is ProspectStatus.ACTIVE
        (request,) = h.brain.calls
        assert request.prospect.prospect_id == STRANGER_ID
        assert request.conversation.origin is ContactOrigin.INBOUND
        assert [m.text for m in request.conversation.messages] == ["Hello, I have a question"]
        assert h.action(STRANGER_ID).action_type is REPLY
        assert h.action(STRANGER_ID).status is ActionStatus.PROPOSED
        (created,) = h.events("prospect.created")
        assert created.details["origin"] == "inbound"

    def test_engage_uses_only_what_the_message_carries(self):
        h = build(inbound=ENGAGE)
        handle(h, stranger())
        created = h.prospect(STRANGER_ID)
        assert created.contact.phone == STRANGER and created.contact.email is None
        assert created.contact.preferred_channel is Channel.WHATSAPP
        assert created.display_name == STRANGER
        assert created.schedule.last_inbound_at == NOW
        # nothing guessed from the number, and no fact invented
        assert created.locale is None and created.stage is None and dict(created.attributes) == {}
        assert h.brain.calls[0].facts == ()

    def test_engage_by_email_uses_the_address(self):
        h = build(inbound=ENGAGE)
        message = inbound("Hi", sender="Alex@Example.test", channel=Channel.EMAIL)
        receipt = h.engine.receive_inbound(message)
        created = h.prospect(receipt.prospect_id)
        assert created.contact.email == "Alex@Example.test" and created.contact.phone is None
        assert receipt.prospect_id == "inbound-2026-01-15-alex@example.test"

    def test_engaging_does_not_touch_the_outbound_quota(self):
        h = build(prospects=[prospect("p1"), prospect("p2")], inbound=ENGAGE, daily_action_limit=1)
        handle(h, stranger())
        h.engine.prepare()
        planned = [a for a in h.repo.list_actions() if a.action_type is not REPLY]
        assert [a.prospect_id for a in planned] == ["p1"]  # the slot is still there

    def test_the_new_prospect_is_never_offered_outreach_before_it_is_processed(self):
        h = build(inbound=ENGAGE)
        h.engine.receive_inbound(stranger())  # stored; background processing has not run yet
        h.engine.prepare()
        assert h.actions_for(STRANGER_ID) == [] and h.brain.calls == []

    def test_engage_in_a_dry_run_persists_nothing(self):
        h = build(inbound=ENGAGE, mode=ApprovalMode.DRY_RUN)
        receipt, _ = handle(h, stranger())
        assert receipt.status is InboundStatus.DRY_RUN
        assert h.repo.list_prospects() == [] and h.repo.messages == () and h.brain.calls == []

    def test_a_failed_creation_is_raised_so_the_provider_redelivers(self):
        h = build(inbound=ENGAGE)
        h.repo.fail_next("create_prospects")
        with pytest.raises(RuntimeError):
            h.engine.receive_inbound(stranger())
        assert h.repo.list_prospects() == [] and h.repo.messages == ()
        receipt, _ = handle(h, stranger())  # the redelivery succeeds
        assert receipt.status is InboundStatus.STORED and len(h.brain.calls) == 1

    def test_a_crash_after_creation_is_repaired_by_the_redelivery(self):
        h = build(inbound=ENGAGE)
        h.repo.fail_next("append_messages")
        with pytest.raises(RuntimeError):
            h.engine.receive_inbound(stranger())
        assert len(h.repo.list_prospects()) == 1 and h.repo.messages == ()
        receipt, _ = handle(h, stranger())
        assert receipt.status is InboundStatus.STORED
        assert len(h.repo.list_prospects()) == 1 and len(h.repo.messages) == 1
        assert len(h.brain.calls) == 1


def engage(limit):
    return InboundPolicy(
        spontaneous_conversations=True,
        unknown_sender=UnknownSenderPolicy.ENGAGE,
        max_new_per_day=limit,
    )


def stranger_n(n, text="Hello", external_id=None):
    return inbound(text, sender=f"+1999000{n:04d}", external_id=external_id or f"wamid.n{n}")


class TestMaxNewPerDay:
    def test_the_default_and_validation(self):
        assert InboundPolicy().max_new_per_day == 30
        with pytest.raises(ValueError, match="max_new_per_day"):
            InboundPolicy(max_new_per_day=-1)

    def test_below_the_limit_the_stranger_is_created_and_the_brain_called(self):
        h = build(inbound=engage(2))
        handle(h, stranger_n(1))
        receipt, _ = handle(h, stranger_n(2))  # the last one that fits
        assert receipt.status is InboundStatus.STORED
        assert len(h.repo.list_prospects()) == 2 and len(h.brain.calls) == 2

    def test_at_the_limit_nothing_is_created_and_the_brain_is_not_called(self):
        h = build(inbound=engage(2), mode=ApprovalMode.AUTONOMOUS)
        handle(h, stranger_n(1))
        handle(h, stranger_n(2))
        receipt, report = handle(h, stranger_n(3))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER and report.outcomes == ()
        assert len(h.repo.list_prospects()) == 2 and len(h.brain.calls) == 2
        assert all(m.prospect_id != receipt.prospect_id for m in h.repo.messages)
        assert len(h.repo.messages) == 4  # two messages in, two replies out; nothing for #3
        assert len(h.channel.attempts) == 2  # no response to the refused sender
        (event,) = h.events("inbound.unknown_sender")
        assert event.details["reason"] == "max_new_per_day reached"
        assert event.details["max_new_per_day"] == "2" and event.details["policy"] == "engage"

    def test_a_limit_of_zero_creates_nobody(self):
        h = build(inbound=engage(0))
        receipt, _ = handle(h, stranger())
        assert receipt.status is InboundStatus.UNKNOWN_SENDER
        assert h.repo.list_prospects() == [] and h.brain.calls == []

    def test_a_refused_sender_is_let_in_once_a_slot_is_free_the_next_day(self):
        h = build(inbound=engage(1))
        handle(h, stranger_n(1))
        assert handle(h, stranger_n(2))[0].status is InboundStatus.UNKNOWN_SENDER
        h.clock.advance(timedelta(days=1))  # a new local day: the counter starts again
        receipt, _ = handle(h, stranger_n(2))
        assert receipt.status is InboundStatus.STORED
        assert receipt.prospect_id == "inbound-2026-01-16-19990000002"
        assert len(h.repo.list_prospects()) == 2

    def test_the_day_is_the_clients_local_day(self):
        from factories import make_client_context

        h = build(inbound=engage(1))
        # 09:00 UTC is already the 15th, and 23:30 UTC is already the 16th in Tunis (UTC+1).
        h.engine = h.engine.__class__(
            client=make_client_context(timezone="Africa/Tunis"),
            repository=h.repo,
            brain=h.brain,
            channel=h.channel,
            clock=h.clock,
            config=EngineConfig(mode=h.mode, inbound=engage(1)),
        )
        h.clock.set(NOW.replace(hour=22, minute=30))  # 23:30 in Tunis: still the 15th
        handle(h, stranger_n(1))
        h.clock.advance(timedelta(hours=1))  # 00:30 in Tunis: the 16th, though UTC says the 15th
        assert handle(h, stranger_n(2))[0].status is InboundStatus.STORED

    def test_known_prospects_are_still_processed_after_the_limit_is_reached(self):
        h = build(prospects=[prospect("p1")], inbound=engage(1))
        handle(h, stranger_n(1))
        assert handle(h, stranger_n(2))[0].status is InboundStatus.UNKNOWN_SENDER
        receipt, report = handle(h, inbound(pid="p1", external_id="w-known"))
        assert receipt.status is InboundStatus.STORED and report.ok
        assert h.status("p1") is ActionStatus.PROPOSED

    def test_a_known_prospect_writing_again_does_not_use_a_slot(self):
        h = build(inbound=engage(1))
        handle(h, stranger_n(1))
        handle(h, stranger_n(1, "again", "wamid.n1b"))  # same sender, new message: known now
        assert len(h.repo.list_prospects()) == 1
        assert len(h.brain.calls) == 2

    def test_existing_prospects_do_not_count_toward_the_limit(self):
        h = build(prospects=[prospect("p1"), prospect("p2")], inbound=engage(1))
        assert handle(h, stranger_n(1))[0].status is InboundStatus.STORED

    def test_the_count_survives_a_restart(self):
        h = build(inbound=engage(1))
        handle(h, stranger_n(1))
        receipt = h.new_engine().receive_inbound(stranger_n(2))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER

    def test_the_slot_is_used_when_the_prospect_is_created_not_when_it_is_processed(self):
        h = build(inbound=engage(1))
        h.engine.receive_inbound(stranger_n(1))  # background processing has not run yet
        assert h.engine.receive_inbound(stranger_n(2)).status is InboundStatus.UNKNOWN_SENDER

    def test_inbound_processing_still_leaves_the_outbound_quota_alone(self):
        h = build(
            prospects=[prospect("p1"), prospect("p2")],
            inbound=engage(5),
            daily_action_limit=1,
        )
        for n in (1, 2, 3):
            handle(h, stranger_n(n))
        h.engine.prepare()
        outreach = [a for a in h.repo.list_actions() if a.action_type is not REPLY]
        assert [a.prospect_id for a in outreach] == ["p1"]  # the one outbound slot was untouched


class TestOptOut:  # E
    def test_no_brain_no_response_and_the_prospect_is_opted_out(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS, inbound=ENGAGE)
        _, report = handle(h, inbound("STOP"))
        assert report.ok and h.brain.calls == []
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT
        assert h.status("p1") is ActionStatus.COMPLETED
        assert h.channel.attempts == ()

    def test_a_stranger_who_opts_out_is_recorded_as_opted_out_and_left_alone(self):
        h = build(inbound=ENGAGE, mode=ApprovalMode.AUTONOMOUS)
        handle(h, stranger("stop"))
        assert h.prospect(STRANGER_ID).status is ProspectStatus.OPTED_OUT
        assert h.brain.calls == [] and h.channel.attempts == ()
        h.clock.advance(timedelta(days=2))
        h.engine.prepare()
        assert h.brain.calls == []


class TestIdempotency:  # F
    def test_a_redelivered_stranger_is_processed_once(self):
        h = build(inbound=ENGAGE, mode=ApprovalMode.AUTONOMOUS)
        first, _ = handle(h, stranger())
        second, again = handle(h, stranger())
        assert second.status is InboundStatus.DUPLICATE and second.prospect_id == first.prospect_id
        assert again.outcomes[0].detail == "reply already handled"
        assert len(h.repo.list_prospects()) == 1
        assert len(h.brain.calls) == 1 and len(h.channel.attempts) == 1
        assert len(h.repo.messages) == 2  # the stranger's message and our one reply
        assert len(h.events("prospect.created")) == 1

    def test_a_redelivered_known_message_is_processed_once(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        handle(h)
        handle(h)
        h.new_engine().process_inbound(h.engine.receive_inbound(inbound()))
        assert len(h.brain.calls) == 1 and len(h.channel.attempts) == 1


class TestOutboundUnchanged:  # G
    def test_first_contact_is_outbound_and_other_outreach_keeps_the_unknown_origin(self):
        h = build(prospects=[prospect("p1"), due("p2", FU)])
        assert h.engine.prepare().ok
        origins = {c.action.action_type: c.conversation.origin for c in h.brain.calls}
        assert origins == {
            SalesActionType.FIRST_CONTACT: ContactOrigin.OUTBOUND,
            FU: ContactOrigin.UNKNOWN,
        }

    def test_planning_and_quota_are_as_before(self):
        h = build(prospects=[prospect(f"p{i}") for i in range(1, 5)], daily_action_limit=2)
        h.engine.prepare()
        assert sorted(a.prospect_id for a in h.repo.list_actions()) == ["p1", "p2"]
        assert {a.status for a in h.repo.list_actions()} == {ActionStatus.PROPOSED}
        h.engine.prepare()
        assert len(h.repo.list_actions()) == 2

    def test_outbound_stays_inside_the_quota_even_with_inbound_traffic(self):
        h = build(
            prospects=[prospect(f"p{i}") for i in range(1, 5)],
            daily_action_limit=2,
            inbound=ENGAGE,
        )
        handle(h, inbound(pid="p4", external_id="w4"))
        handle(h, stranger())
        h.engine.prepare()
        outreach = [a for a in h.repo.list_actions() if a.action_type is not REPLY]
        assert sorted(a.prospect_id for a in outreach) == ["p1", "p2"]

    def test_a_follow_up_scheduled_by_an_inbound_decision_is_ordinary_outbound_later(self):
        later = decide(DecisionType.SCHEDULE_FOLLOW_UP, next_follow_up_at=NOW + timedelta(days=1))
        h = build(prospects=[prospect("p1")], brain=ScriptedBrain(lambda r: later))
        handle(h)
        h.clock.advance(timedelta(days=2))
        h.engine.prepare()
        assert {a.action_type for a in h.actions_for("p1")} == {REPLY, FU}


class TestFacts:  # H
    def test_the_brain_receives_the_prospects_facts_on_an_inbound_reply(self):
        known = (fact("a", "1", FactStatus.CONFIRMED), fact("b", "2"))
        facts = Facts({"p1": known, "p2": (fact("x", "9"),)})
        h = build(prospects=[prospect("p1"), prospect("p2")], facts=facts)
        handle(h, inbound(pid="p1"))
        (request,) = h.brain.calls
        assert request.facts == known
        assert facts.asked == [ProspectId("p1")]  # only the prospect's own facts

    def test_facts_reach_outbound_requests_too(self):
        h = build(prospects=[prospect("p1")], facts=Facts({"p1": (fact(),)}))
        h.engine.prepare()
        assert h.brain.calls[0].facts == (fact(),)

    def test_a_prospect_without_facts_gets_an_empty_collection(self):
        h = build(prospects=[prospect("p1")], facts=Facts({}))
        handle(h)
        assert h.brain.calls[0].facts == ()

    def test_no_facts_reader_means_no_facts(self):
        h = build(prospects=[prospect("p1")])
        handle(h)
        assert h.brain.calls[0].facts == ()

    def test_nothing_is_invented_from_the_message(self):
        h = build(prospects=[prospect("p1")], facts=Facts({}))
        handle(h, inbound("I'm a doctor from Tunis, interested in the villa"))
        assert h.brain.calls[0].facts == ()

    def test_a_facts_failure_is_recorded_and_retried_like_a_brain_failure(self):
        class Flaky(Facts):
            broken = True

            def get_facts(self, prospect_id):
                if self.broken:
                    raise RuntimeError("facts store down")
                return super().get_facts(prospect_id)

        facts = Flaky({})
        h = build(prospects=[prospect("p1")], facts=facts)
        _, report = handle(h)
        assert not report.ok and h.brain.calls == []
        assert h.status("p1") is ActionStatus.PLANNED and h.channel.attempts == ()
        facts.broken = False
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.PROPOSED and len(h.brain.calls) == 1


class TestApprovalSafetyDispatch:  # I
    def test_approval_mode_proposes_and_sends_nothing_until_approved(self):
        h = build(inbound=ENGAGE)
        handle(h, stranger())
        assert h.status(STRANGER_ID) is ActionStatus.PROPOSED and h.channel.attempts == ()
        h.approve(STRANGER_ID)
        h.engine.dispatch()
        assert h.status(STRANGER_ID) is ActionStatus.SENT
        (sent,) = h.channel.sent
        assert sent.recipient == STRANGER
        message = h.repo.get_conversation(ProspectId(STRANGER_ID)).messages[-1]
        assert message.direction is Direction.OUTBOUND

    def test_autonomous_mode_sends_through_the_sending_guard(self):
        h = build(inbound=ENGAGE, mode=ApprovalMode.AUTONOMOUS)
        handle(h, stranger())
        assert h.status(STRANGER_ID) is ActionStatus.SENT
        assert h.channel.status_during_send == [ActionStatus.SENDING]

    def test_a_rejected_reply_is_never_sent(self):
        h = build(inbound=ENGAGE)
        handle(h, stranger())
        h.reject(STRANGER_ID)
        h.engine.dispatch()
        assert h.channel.attempts == ()

    def test_a_handoff_decision_pauses_the_prospect_and_sends_nothing(self):
        handoff = decide(DecisionType.HANDOFF_TO_HUMAN, rationale="wants a person")
        h = build(
            inbound=ENGAGE, mode=ApprovalMode.AUTONOMOUS, brain=ScriptedBrain(lambda r: handoff)
        )
        handle(h, stranger())
        assert h.prospect(STRANGER_ID).status is ProspectStatus.PAUSED
        assert h.status(STRANGER_ID) is ActionStatus.COMPLETED and h.channel.attempts == ()
        assert h.events("prospect.handoff")

    def test_a_close_decision_closes_the_prospect(self):
        close = decide(DecisionType.CLOSE)
        h = build(prospects=[prospect("p1")], brain=ScriptedBrain(lambda r: close))
        handle(h)
        assert h.prospect("p1").status is ProspectStatus.CLOSED
        assert h.channel.attempts == ()

    @pytest.mark.parametrize("status", [ProspectStatus.PAUSED, ProspectStatus.CLOSED])
    def test_safety_still_blocks_replies_to_blocked_prospects(self, status):
        h = build(prospects=[prospect("p1", status=status)], mode=ApprovalMode.AUTONOMOUS)
        handle(h)
        assert h.brain.calls == [] and h.channel.attempts == ()
        assert h.status("p1") is ActionStatus.CANCELLED

    def test_the_final_safety_gate_still_runs_before_sending(self):
        h = build(inbound=ENGAGE)
        handle(h, stranger())
        h.approve(STRANGER_ID)
        h.repo.update_prospects([replace(h.prospect(STRANGER_ID), status=ProspectStatus.OPTED_OUT)])
        h.engine.dispatch()
        assert h.channel.attempts == () and h.status(STRANGER_ID) is ActionStatus.CANCELLED
