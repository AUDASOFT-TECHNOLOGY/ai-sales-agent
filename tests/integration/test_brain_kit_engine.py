"""Slice 5: the real Core Engine driving the real consultative Brain (only the model is scripted).

What this proves is the split of responsibilities: the Brain proposes a commercial decision, and
the Core keeps approval, safety, quota, persistence, idempotency and execution.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

from engine_harness import FU, REPLY, build, due, inbound, prospect
from factories import NOW

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kit"))

from brain_support import answer, closing, handoff, settings_doc

from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    Fact,
    FactStatus,
    ProspectId,
    ProspectStatus,
    SalesActionType,
)
from sales_integrations.fakes import FakeLLMProvider


def world(*answers, doc=None, mode=ApprovalMode.APPROVAL, **kwargs):
    llm = FakeLLMProvider([json.dumps(a) if isinstance(a, dict) else a for a in answers])
    brain = ConsultativeSalesBrain(llm, parse_settings(doc or settings_doc()))
    h = build(brain=brain, mode=mode, **kwargs)
    h.facts = h.repo  # the fake repository is also the FactsReader, as the Sheets adapter is
    h.engine = h.new_engine()
    h.llm = llm
    return h


def record(h, pid, key, value, status=FactStatus.CONFIRMED):
    h.repo.append_facts(
        ProspectId(pid), [Fact(key=key, value=value, status=status, recorded_at=NOW)]
    )


class TestOutbound:
    def test_the_brain_proposes_and_the_core_waits_for_approval(self):
        h = world(answer(message="Hello Alex, glad to meet you."), prospects=[prospect("p1")])
        report = h.engine.prepare()
        assert report.ok
        action = h.action("p1")
        assert action.status is ActionStatus.PROPOSED
        assert (
            action.draft.text == "Hello Alex, glad to meet you." and action.draft.language == "en"
        )
        assert (
            h.channel.attempts == ()
        )  # the Brain sent nothing: only the Core sends, once approved

    def test_the_cadence_the_brain_proposed_is_persisted_and_applied_by_the_core(self):
        h = world(answer(), prospects=[prospect("p1")])
        h.engine.prepare()
        action = h.action("p1")
        assert action.next_action_type is FU
        assert action.next_action_due_at == NOW + timedelta(days=2)  # from the Brain's settings
        h.approve("p1")
        h.engine.dispatch()
        sent = h.prospect("p1")
        assert len(h.channel.sent) == 1
        assert sent.schedule.next_action_due_at == NOW + timedelta(days=2)
        assert sent.schedule.next_action_type is FU

    def test_a_dry_run_records_nothing_and_sends_nothing(self):
        h = world(answer(), mode=ApprovalMode.DRY_RUN, prospects=[prospect("p1")])
        h.engine.prepare()
        assert len(h.repo.list_actions()) == 0  # dry run persists nothing
        assert h.channel.attempts == ()

    def test_autonomous_mode_still_goes_through_the_cores_checks(self):
        h = world(answer(), mode=ApprovalMode.AUTONOMOUS, prospects=[prospect("p1")])
        h.engine.run_cycle()
        assert h.status("p1") is ActionStatus.SENT and len(h.channel.sent) == 1

    def test_an_unusable_model_answer_is_isolated_and_nothing_is_sent(self):
        h = world(
            "not json", "still not json", "nope", "nope", prospects=[prospect("p1"), prospect("p2")]
        )
        report = h.engine.prepare()
        assert not report.ok  # recorded as a failure of that action
        assert h.channel.attempts == ()
        for pid in ("p1", "p2"):
            action = h.action(pid)
            assert action.attempts == 1 and "not valid JSON" in action.last_error
            assert action.status is ActionStatus.PLANNED  # retried by the next run

    def test_a_prospect_who_opted_out_never_reaches_the_brain(self):
        opted_out = prospect("p1", status=ProspectStatus.OPTED_OUT)
        h = world(prospects=[opted_out])
        h.engine.prepare()
        assert len(h.llm.calls) == 0  # safety is the Core's, before any model call


class TestFactsFlowToTheBrain:
    def test_a_confirmed_interest_recorded_in_the_repository_shapes_the_decision(self):
        h = world(answer(selected_product="alpha"), prospects=[prospect("p1")])
        record(h, "p1", "interest", "alpha")
        h.engine.prepare()
        payload_text = h.llm.calls[0].messages[1].content
        assert '"focus":["alpha"]' in payload_text
        assert h.action("p1").status is ActionStatus.PROPOSED

    def test_the_brain_records_no_facts(self):
        h = world(answer(), prospects=[prospect("p1")])
        h.engine.prepare()
        assert len(h.repo.get_facts(ProspectId("p1"))) == 0  # extraction is a later slice


class TestInbound:
    def test_a_reply_is_answered_by_the_same_brain(self):
        h = world(answer(message="Happy to help. What are you after?"), prospects=[prospect("p1")])
        receipt = h.engine.receive_inbound(inbound("Hi, what do you offer?"))
        h.engine.process_inbound(receipt)
        action = h.action("p1")
        assert action.action_type is REPLY and action.status is ActionStatus.PROPOSED
        assert action.draft.text.startswith("Happy to help")
        sent_context = h.llm.calls[0].messages[1].content
        assert '"origin":"inbound"' in sent_context and "what do you offer" in sent_context

    def test_a_reply_uses_no_outbound_quota_and_the_brain_holds_none(self):
        h = world(
            answer(message="Answer to the reply."),
            answer(message="First contact for the other prospect."),
            prospects=[prospect("p1"), prospect("p2")],
            mode=ApprovalMode.AUTONOMOUS,
            daily_action_limit=1,
        )
        receipt = h.engine.receive_inbound(inbound(pid="p1"))
        h.engine.process_inbound(receipt)
        assert h.status("p1") is ActionStatus.SENT
        h.engine.prepare()
        assert h.status("p2") is ActionStatus.APPROVED  # the single outreach slot was still free

    def test_a_reply_is_still_subject_to_approval(self):
        h = world(answer(), prospects=[prospect("p1")])
        h.engine.process_inbound(h.engine.receive_inbound(inbound()))
        assert h.status("p1") is ActionStatus.PROPOSED and h.channel.attempts == ()

    def test_a_handoff_decision_pauses_the_prospect_and_sends_nothing(self):
        h = world(handoff(analysis={"intent": "human_requested"}), prospects=[prospect("p1")])
        h.engine.process_inbound(h.engine.receive_inbound(inbound("I want to speak to a person")))
        assert h.prospect("p1").status is ProspectStatus.PAUSED
        assert h.status("p1") is ActionStatus.COMPLETED and h.channel.attempts == ()

    def test_a_close_decision_closes_the_prospect(self):
        h = world(closing(), prospects=[prospect("p1")])
        h.engine.process_inbound(h.engine.receive_inbound(inbound("No thanks")))
        assert h.prospect("p1").status is ProspectStatus.CLOSED

    def test_an_opt_out_word_is_handled_by_the_core_before_the_brain(self):
        h = world(prospects=[prospect("p1")])
        h.engine.process_inbound(h.engine.receive_inbound(inbound("STOP")))
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT
        assert len(h.llm.calls) == 0


class TestFollowUpOwnership:
    def test_the_planner_selects_the_due_follow_up_and_the_brain_decides_what_comes_next(self):
        h = world(
            answer(message="Just checking in."),
            prospects=[due("p1", kind=FU, status=ProspectStatus.ACTIVE)],
        )
        h.engine.prepare()
        action = h.action("p1")
        assert action.action_type is SalesActionType.FOLLOW_UP  # chosen by the Planner
        assert action.draft.text == "Just checking in."  # worded by the Brain
        assert action.next_action_type is FU  # scheduled by the Brain's cadence...
        assert action.next_action_due_at == NOW + timedelta(days=2)  # ...and persisted by the Core

    def test_a_wait_decision_is_applied_by_the_core_without_sending(self):
        wait = answer(
            decision="schedule_follow_up",
            message=None,
            language=None,
            follow_up={"situation": "after_cta"},
        )
        h = world(wait, prospects=[due("p1", kind=FU, status=ProspectStatus.ACTIVE)])
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.COMPLETED and h.channel.attempts == ()
        assert h.prospect("p1").schedule.next_action_due_at == NOW + timedelta(days=1)
