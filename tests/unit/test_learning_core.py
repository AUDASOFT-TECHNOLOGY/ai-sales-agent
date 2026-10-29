"""The result of a conversation, the anonymous trace of a decision, and what the Engine does with
them (`sales_core.learning`)."""

from datetime import timedelta

from engine_harness import ScriptedBrain, build, prospect, send
from factories import CLIENT, NOW, make_client_context, make_conversation, make_message

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    BrainDecision,
    DecisionType,
    Direction,
    LearnedNote,
    MessageDraft,
    MessageId,
    ProspectStatus,
    SalesActionType,
)
from sales_core.engine import Engine, EngineConfig
from sales_core.learning import (
    ConversationSignals,
    Result,
    anonymous_ref,
    build_trace,
    clean_labels,
    count_questions,
    resolve_result,
)

LATER = NOW + timedelta(days=1)


def talk(*sides: str, start=NOW, gap=timedelta(hours=2)):
    """A conversation from "out" / "in" sides, `gap` apart."""
    return make_conversation(
        messages=tuple(
            make_message(
                message_id=MessageId(f"m-{i}"),
                direction=Direction.OUTBOUND if side == "out" else Direction.INBOUND,
                timestamp=start + gap * i,
                text="x",
            )
            for i, side in enumerate(sides)
        )
    )


def result_of(conversation, *, now=LATER, achieved=frozenset({"won"}), pid="p-1", **signals):
    signals.setdefault("status", ProspectStatus.ACTIVE)
    return resolve_result(
        pid,
        conversation,
        ConversationSignals(**signals),
        achieved_stages=achieved,
        now=now,
    )


# ---- the result ----------------------------------------------------------------------------------


def test_a_confirmed_payment_or_the_goal_stage_is_achieved():
    c = talk("out", "in", "out")
    assert result_of(c, paid=True) is Result.ACHIEVED
    assert result_of(c, stage="won") is Result.ACHIEVED
    assert result_of(c, stage="qualifying") is Result.OPEN


def test_a_claim_of_payment_alone_is_not_a_result():
    # `paid` comes only from a confirmation by a person; nothing the prospect says sets it.
    assert result_of(talk("out", "in", "out")) is Result.OPEN


def test_a_test_person_never_teaches():
    assert result_of(talk("out", "in"), pid="inbound-test-abc", paid=True) is None


def test_a_conversation_the_agent_never_wrote_in_has_no_result():
    assert result_of(talk("in"), paid=True) is None


def test_a_conversation_with_a_person_is_kept_apart_even_if_it_then_fails():
    c = talk("out", "in")
    assert result_of(c, with_a_person=True, status=ProspectStatus.OPTED_OUT) is Result.HANDED_OVER
    assert result_of(c, status=ProspectStatus.PAUSED) is Result.HANDED_OVER


def test_a_plain_stop_is_declined_but_a_stop_after_unanswered_messages_is_uneasy():
    assert result_of(talk("out", "in"), status=ProspectStatus.OPTED_OUT) is Result.DECLINED
    insisted = talk("out", "out", "out", "in")
    assert result_of(insisted, status=ProspectStatus.OPTED_OUT) is Result.UNEASY
    assert result_of(talk("out", "in"), status=ProspectStatus.OPTED_OUT, impatient=True) is (
        Result.UNEASY
    )


def test_an_explicit_refusal_or_a_closed_conversation_is_declined():
    c = talk("out", "in")
    assert result_of(c, refused=True) is Result.DECLINED
    assert result_of(c, status=ProspectStatus.CLOSED) is Result.DECLINED


def test_silence_is_not_a_refusal():
    c = talk("out", "out")
    assert result_of(c, now=NOW + timedelta(days=3)) is Result.OPEN
    quiet = result_of(c, now=NOW + timedelta(days=30))
    assert quiet is Result.QUIET
    assert not quiet.is_unfavourable and not quiet.is_final


def test_only_achieved_declined_and_uneasy_are_final():
    finals = {r for r in Result if r.is_final}
    assert finals == {Result.ACHIEVED, Result.DECLINED, Result.UNEASY}


# ---- the trace -----------------------------------------------------------------------------------


def decision(text="Hi, which plan?", **metadata):
    return BrainDecision(
        decision_type=DecisionType.SEND_MESSAGE,
        message=MessageDraft(text=text),
        metadata=metadata,
    )


def trace_of(dec, messages=()):
    return build_trace(
        client_id=CLIENT,
        prospect_id="p-1",
        action_id="act-1",
        action_type=SalesActionType.FOLLOW_UP,
        decision=dec,
        channel="whatsapp",
        messages=tuple(messages),
        now=NOW + timedelta(days=2),
    )


def test_a_trace_measures_the_message_and_never_keeps_its_words():
    text = "Hello Alex, call me on +21612345678, ok? Or mail a@b.co?"
    trace = trace_of(decision(text))
    assert trace.questions == 2 and trace.length == len(text)
    flat = repr(trace)
    assert "Alex" not in flat and "p-1" not in flat and "act-1" not in flat
    assert "+216" not in flat and "a@b.co" not in flat


def test_the_references_are_stable_opaque_and_per_client():
    assert anonymous_ref("c", "p-1") == anonymous_ref("c", "p-1")
    assert anonymous_ref("c", "p-1") != anonymous_ref("c", "p-2")
    assert anonymous_ref("c", "p-1") != anonymous_ref("d", "p-1")
    assert "p-1" not in anonymous_ref("c", "p-1")


def test_the_trace_counts_unanswered_messages_and_the_time_since_the_last_one():
    messages = talk("out", "in", "out", "out").messages
    trace = trace_of(decision(), messages)
    assert trace.unanswered == 2
    assert trace.gap_hours == round(
        (NOW + timedelta(days=2) - messages[-1].timestamp).total_seconds() / 3600, 1
    )


def test_only_plain_labels_reach_a_trace():
    labels = clean_labels(
        {
            "objection": "price",
            "intent": "Call me at 12345678 and tell Anna",  # free text: dropped
            "selected_product": "alpha,beta",
            "stop": "impatience,direct_question",
            "rationale": "never kept",
            "learned_notes": "short_messages,doubt_ignored:alpha",
        }
    )
    assert labels == {
        "objection": "price",
        "selected_product": "alpha,beta",
        "stop": "impatience,direct_question",
        "learned_notes": "short_messages,doubt_ignored:alpha",
    }


def test_an_email_or_a_name_cannot_pass_as_a_label():
    assert clean_labels({"objection": "anna@example.com"}) == {}
    assert clean_labels({"objection": "Anna Smith"}) == {}


def test_questions_are_counted_in_every_script():
    assert count_questions("Ok? Vraiment ? ما هذا؟ 本当？") == 4


# ---- the Engine ----------------------------------------------------------------------------------


class Sink:
    def __init__(self):
        self.traces = []

    def record(self, trace):
        self.traces.append(trace)


class Notes:
    def __init__(self, notes):
        self._notes = notes

    def active_notes(self):
        return self._notes


def engine_for(h, brain, mode=ApprovalMode.AUTONOMOUS, **extra):
    return Engine(
        client=make_client_context(),
        repository=h.repo,
        brain=brain,
        channel=h.channel,
        clock=h.clock,
        config=EngineConfig(mode=mode),
        **extra,
    )


def test_each_decision_is_traced_without_the_person_or_the_words():
    sink = Sink()
    brain = ScriptedBrain(lambda r: send("Hello Alex, secret words?"))
    h = build(prospects=[prospect("p1")], brain=brain, mode=ApprovalMode.AUTONOMOUS)
    engine_for(h, brain, trace_sink=sink).run_cycle()
    (trace,) = sink.traces
    assert trace.case_ref == anonymous_ref(CLIENT, "p1")
    assert trace.decision == "send_message" and trace.questions == 1
    assert "secret" not in repr(trace) and "p1" not in repr(trace)


def test_a_test_person_and_a_dry_run_leave_no_trace():
    sink = Sink()
    brain = ScriptedBrain()
    h = build(prospects=[prospect("p1")], brain=brain, mode=ApprovalMode.DRY_RUN)
    engine_for(h, brain, ApprovalMode.DRY_RUN, trace_sink=sink).run_cycle()
    assert sink.traces == []
    sink2 = Sink()
    h2 = build(prospects=[prospect("inbound-test-1")], brain=brain, mode=ApprovalMode.AUTONOMOUS)
    engine_for(h2, brain, trace_sink=sink2).run_cycle()
    assert sink2.traces == []


def test_a_sink_that_fails_never_stops_the_engine():
    class Broken:
        def record(self, trace):
            raise RuntimeError("disk full")

    brain = ScriptedBrain()
    h = build(prospects=[prospect("p1")], brain=brain, mode=ApprovalMode.AUTONOMOUS)
    engine_for(h, brain, trace_sink=Broken()).run_cycle()
    assert len(h.channel.sent) == 1


def test_the_brain_is_given_what_was_learned_and_a_failing_source_changes_nothing():
    note = LearnedNote(
        note_id="short_messages", code="short_messages", kind="do", theme="general", state="active"
    )
    brain = ScriptedBrain()
    h = build(prospects=[prospect("p1")], brain=brain, mode=ApprovalMode.AUTONOMOUS)
    engine_for(h, brain, learned_notes=Notes([note])).run_cycle()
    assert brain.calls[0].learned_notes == (note,)

    class Broken:
        def active_notes(self):
            raise RuntimeError("down")

    brain2 = ScriptedBrain()
    h2 = build(prospects=[prospect("p1")], brain=brain2, mode=ApprovalMode.AUTONOMOUS)
    engine_for(h2, brain2, learned_notes=Broken()).run_cycle()
    assert brain2.calls[0].learned_notes == ()
    assert len(h2.channel.sent) == 1


def test_the_engine_tells_how_a_conversation_stands():
    brain = ScriptedBrain()
    h = build(prospects=[prospect("p1", stage="won")], brain=brain, mode=ApprovalMode.AUTONOMOUS)
    signals = h.engine.outcome_signals("p1")
    assert signals is not None and signals.stage == "won" and not signals.paid
    assert h.engine.outcome_signals("nobody") is None
