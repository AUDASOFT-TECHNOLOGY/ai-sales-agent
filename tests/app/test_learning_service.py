"""The agent learns from its results: migration 0028, the registers (memory and SQL), the service that
ties traces to finished conversations, and a simulated run from conversations to prompt.

The language model is a scripted fake: what is checked here is the machinery, the thresholds and the
protections, not the quality of a real model's judgement."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from brain_support import settings_doc
from engine_harness import ScriptedBrain, build, prospect, send
from factories import CLIENT, NOW, make_client_context
from learning_support import verdict
from sqlalchemy import inspect

from sales_app.learning_service import LearningService, StoreNotes, StoreTraceSink
from sales_app.learning_store import (
    REVIEW_KEEP,
    TRACE_KEEP,
    InMemoryLearningStore,
    StoredNote,
    StoredReview,
    StoredTrace,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.learning_rows import SqlLearningStore
from sales_app.persistence.schema import current_revision
from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_brain_kit.consultative import build_context
from sales_brain_kit.consultative.prompt import build_system_prompt
from sales_core.approval import ApprovalMode
from sales_core.contracts import Direction, Message, MessageId, ProspectStatus
from sales_core.engine import Engine, EngineConfig
from sales_core.learning import anonymous_ref
from sales_core.ports import LLMError
from sales_integrations.fakes import FakeLLMProvider

TODAY = NOW + timedelta(days=1)
AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)

# ---- the registers -------------------------------------------------------------------------------


@pytest.fixture
def sql():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return engine


def both(sql):
    return [InMemoryLearningStore("alpha"), SqlLearningStore(sql, "alpha")]


def trace(ref="c1", action="a1", at=AT, **kw):
    return StoredTrace(
        case_ref=ref, action_ref=action, at=at, decision="send_message", action_type="reply", **kw
    )


def review_row(ref="c1", ended=AT):
    return StoredReview(
        case_ref=ref,
        result="declined",
        ended_at=ended,
        reviewed_at=ended,
        causes=("doubt_ignored",),
        practices=("short_messages",),
        confidence="high",
        applied_notes=("short_messages",),
    )


def note_row(note_id="short_messages", **kw):
    fields = {
        "note_id": note_id,
        "code": note_id.split(":")[0],
        "kind": "do",
        "theme": "general",
        "state": "active",
    }
    return StoredNote(**(fields | kw))


def test_0028_adds_the_three_registers_and_nothing_else():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0027")
    before = set(inspect(engine).get_table_names())
    upgrade_database(engine, "0028")
    assert current_revision(engine) == "0028"
    assert set(inspect(engine).get_table_names()) - before == {
        "op_learning_traces",
        "op_learning_reviews",
        "op_learning_notes",
    }


def test_no_register_has_a_column_for_a_name_a_contact_or_a_message():
    from sales_app.persistence.tables import metadata

    for name in ("op_learning_traces", "op_learning_reviews", "op_learning_notes"):
        columns = {c.name for c in metadata.tables[name].columns}
        assert not columns & {"name", "display_name", "phone", "email", "contact", "text", "body"}


def test_a_trace_is_kept_and_a_retry_keeps_the_latest(sql):
    for store in both(sql):
        store.record_trace(trace(length=10, labels={"objection": "price"}, gap_hours=2.5))
        store.record_trace(trace(length=99, labels={"objection": "price"}, gap_hours=2.5))
        (kept,) = store.traces_of("c1")
        assert (kept.length, kept.labels, kept.gap_hours) == (99, {"objection": "price"}, 2.5)
        assert store.case_refs() == ["c1"]


def test_a_verdict_replaces_the_traces_it_came_from(sql):
    for store in both(sql):
        store.record_trace(trace())
        store.record_trace(trace(ref="c2", action="a2"))
        store.save_review(review_row("c1"))
        assert store.traces_of("c1") == [] and len(store.traces_of("c2")) == 1
        assert store.reviewed_refs() == {"c1"}
        (kept,) = store.reviews()
        assert kept.causes == ("doubt_ignored",) and kept.applied_notes == ("short_messages",)


def test_notes_are_replaced_as_a_whole_and_one_can_be_set(sql):
    for store in both(sql):
        store.replace_notes([note_row("a"), note_row("b")])
        store.set_note(note_row("a", state="off", off_by="client"))
        by_id = {n.note_id: n for n in store.notes()}
        assert by_id["a"].state == "off" and by_id["a"].off_by == "client" and "b" in by_id
        store.replace_notes([])
        assert store.notes() == []


def test_a_conversation_can_be_forgotten_entirely(sql):
    for store in both(sql):
        store.record_trace(trace())
        store.save_review(review_row("c1"))
        store.record_trace(trace(action="a9"))
        store.forget_case("c1")
        assert store.reviews() == [] and store.traces_of("c1") == []


def test_what_is_past_its_retention_is_purged(sql):
    for store in both(sql):
        store.record_trace(trace(at=AT - TRACE_KEEP - timedelta(days=1)))
        store.record_trace(trace(action="a2", at=AT))
        store.save_review(review_row("old", ended=AT - REVIEW_KEEP - timedelta(days=1)))
        store.save_review(review_row("new", ended=AT))
        store.purge(AT)
        assert [t.action_ref for t in store.traces_of("c1")] == ["a2"]
        assert [r.case_ref for r in store.reviews()] == ["new"]


def test_one_clients_registers_never_show_another_clients(sql):
    mine, theirs = SqlLearningStore(sql, "alpha"), SqlLearningStore(sql, "beta")
    mine.record_trace(trace())
    mine.replace_notes([note_row()])
    assert theirs.case_refs() == [] and theirs.notes() == []


# ---- a simulated world ---------------------------------------------------------------------------

SHORT = "Hi! Which plan fits you?"
LONG = ("This is a very long message about everything we do and all of our offers. " * 10) + "Ok?"


class World:
    """A client whose agent writes to `winners` briefly and to `losers` at length; the winners reach
    the goal stage, the losers stop us. The model is a scripted fake."""

    def __init__(self, winners=6, losers=6, *, review=None, extra=()):
        self.store = InMemoryLearningStore(CLIENT)
        self.llm = FakeLLMProvider(default=review or verdict(unknown=True))
        self.reflector = ConsultativeSalesBrain(self.llm, parse_settings(settings_doc()))
        self.win_ids = [f"w{i}" for i in range(1, winners + 1)]
        self.lose_ids = [f"l{i}" for i in range(1, losers + 1)]
        everyone = [prospect(pid) for pid in (*self.win_ids, *self.lose_ids)]
        self.brain = ScriptedBrain(
            lambda r: send(SHORT if r.prospect.prospect_id in self.win_ids else LONG)
        )
        self.h = build(
            prospects=[*everyone, *extra], brain=self.brain, mode=ApprovalMode.AUTONOMOUS,
            daily_action_limit=500,
        )  # fmt: skip
        self.engine = self.engine_over(self.brain)
        self.service = LearningService(CLIENT, self.store, self.reflector)

    def engine_over(self, brain):
        return Engine(
            client=make_client_context(),
            repository=self.h.repo,
            brain=brain,
            channel=self.h.channel,
            clock=self.h.clock,
            config=EngineConfig(mode=ApprovalMode.AUTONOMOUS, daily_action_limit=500),
            trace_sink=StoreTraceSink(self.store),
            learned_notes=StoreNotes(self.store),
        )

    def play(self):
        self.engine.run_cycle()
        for pid in self.win_ids:
            self.reached_goal(pid)
        for pid in self.lose_ids:
            self.said_stop(pid)

    def reached_goal(self, pid):
        self.h.repo.update_prospects(
            [replace(self.h.prospect(pid), stage="won", status=ProspectStatus.ACTIVE)]
        )

    def said_stop(self, pid):
        conversation = self.h.repo.get_conversation(pid)
        self.h.repo.append_messages(
            [
                Message(
                    message_id=MessageId(f"in-{pid}"),
                    client_id=CLIENT,
                    prospect_id=pid,
                    conversation_id=conversation.conversation_id,
                    direction=Direction.INBOUND,
                    channel=conversation.messages[0].channel,
                    text="STOP",
                    timestamp=NOW + timedelta(hours=3),
                )
            ]
        )
        self.h.repo.update_prospects(
            [replace(self.h.prospect(pid), status=ProspectStatus.OPTED_OUT)]
        )

    def learn(self):
        return self.service.run(self.engine, TODAY)

    def dump(self):
        return repr((self.store.reviews(), self.store.notes(), self.store.case_refs()))

    def next_request(self):
        """What the Brain is given for a newcomer, once something has been learned."""
        self.newcomers = getattr(self, "newcomers", 0) + 1
        pid = f"n{self.newcomers}"
        self.h.repo.create_prospects([prospect(pid)])
        self.brain.calls.clear()
        self.engine_over(self.brain).prepare()
        (request,) = [c for c in self.brain.calls if c.prospect.prospect_id == pid]
        return request


def prompt_of(request):
    return build_system_prompt(build_context(request, parse_settings(settings_doc())))


def test_conversations_won_and_lost_become_rules_that_reach_the_next_prompt():
    world = World()
    world.play()
    run = world.learn()
    assert run.reviewed == 12 and run.failed == 0
    notes = {n.note_id: n for n in world.service.notes()}
    assert notes["short_messages"].state == "active" and notes["short_messages"].kind == "do"
    assert notes["long_messages"].state == "active" and notes["long_messages"].kind == "avoid"
    assert notes["long_messages"].cases == 6 and notes["long_messages"].evidence == "medium"

    request = world.next_request()
    assert {n.code for n in request.learned_notes} >= {"short_messages", "long_messages"}
    prompt = prompt_of(request)
    assert "Learned from your own past conversations" in prompt
    assert "Works well: Keep messages short" in prompt
    assert "Avoid: Avoid long messages" in prompt


def test_the_clients_lessons_come_first_and_the_rules_still_win():
    world = World()
    world.play()
    world.learn()
    doc = settings_doc()
    doc["communication"]["training"] = [{"kind": "habit", "text": "Write like a chat message"}]
    prompt = build_system_prompt(build_context(world.next_request(), parse_settings(doc)))
    assert prompt.index("Habit of the business") < prompt.index("Learned from your own")
    assert prompt.index("Learned from your own") < prompt.index("\nRules\n")
    assert "the Rules below always win" in prompt


def test_a_rule_the_client_switches_off_disappears_from_the_next_prompt():
    world = World()
    world.play()
    world.learn()
    assert world.service.decide("long_messages", "off", TODAY).state == "off"
    request = world.next_request()
    assert "long_messages" not in {n.code for n in request.learned_notes}
    assert "Avoid long messages" not in prompt_of(request)
    world.learn()  # the next run does not bring it back
    assert {n.note_id: n for n in world.service.notes()}["long_messages"].state == "off"


def test_the_client_can_confirm_a_rule_and_hand_it_back():
    world = World(winners=3, losers=3)  # too little to act: everything is only watched
    world.play()
    world.learn()
    notes = {n.note_id: n for n in world.service.notes()}
    assert notes and all(n.state == "watching" for n in notes.values())
    assert world.next_request().learned_notes == ()
    pinned = world.service.decide("short_messages", "confirm", TODAY)
    assert pinned.state == "active" and pinned.confirmed_by_client
    assert [n.code for n in world.next_request().learned_notes] == ["short_messages"]
    assert world.service.decide("short_messages", "on", TODAY).state == "watching"
    assert world.service.decide("nothing", "off", TODAY) is None
    with pytest.raises(ValueError):
        world.service.decide("short_messages", "delete", TODAY)


def test_too_little_data_teaches_nothing_and_says_so():
    world = World(winners=2, losers=2)
    world.play()
    world.learn()
    assert world.service.notes() == []
    assert world.service.counts() == {"reviewed": 4, "achieved": 2, "unfavourable": 2}


def test_results_that_contradict_each_other_make_no_rule():
    # Long messages everywhere, and as many wins as losses: nothing separates them.
    world = World(winners=6, losers=6)
    world.brain = ScriptedBrain(lambda r: send(LONG))
    world.engine = world.engine_over(world.brain)
    world.play()
    world.learn()
    assert "long_messages" not in {n.code for n in world.service.notes() if n.state == "active"}


def test_a_client_with_no_goal_stage_has_no_wins_to_learn_from():
    doc = settings_doc()
    doc["stages"] = [s for s in doc["stages"] if s.get("milestone") != "won"]
    world = World()
    world.reflector = ConsultativeSalesBrain(world.llm, parse_settings(doc))
    world.service = LearningService(CLIENT, world.store, world.reflector)
    world.play()
    run = world.learn()
    assert run.reviewed == 6  # only the lost ones are final; the others are still open
    assert all(n.kind != "do" or n.state != "active" for n in world.service.notes())


def test_a_conversation_handed_to_a_person_is_kept_apart():
    world = World(winners=0, losers=7)
    world.engine.run_cycle()
    for pid in world.lose_ids:
        world.h.repo.update_prospects(
            [replace(world.h.prospect(pid), status=ProspectStatus.PAUSED)]
        )
    run = world.learn()
    assert run.reviewed == 0 and world.service.notes() == []


def test_silence_is_not_read_as_a_refusal():
    world = World(winners=0, losers=7)
    world.engine.run_cycle()  # nobody answers
    run = world.service.run(world.engine, NOW + timedelta(days=60))
    assert run.reviewed == 0 and world.service.counts()["unfavourable"] == 0


def test_a_test_person_never_teaches_even_if_a_trace_got_in():
    world = World(winners=0, losers=1)
    ghost = prospect("inbound-test-9")
    world.h.repo.create_prospects([ghost])
    world.store.record_trace(trace(ref=anonymous_ref(CLIENT, "inbound-test-9")))
    world.learn()
    assert world.store.reviewed_refs() == set()


def test_when_the_credits_are_used_up_nothing_is_reviewed_and_conversations_wait():
    world = World()
    world.service = LearningService(CLIENT, world.store, world.reflector, may_spend=lambda: False)
    world.play()
    run = world.learn()
    assert run.reviewed == 0 and run.skipped_for_credits and world.llm.calls == ()
    assert len(world.store.case_refs()) == 12  # still there, to be reviewed later
    world.service = LearningService(CLIENT, world.store, world.reflector)
    assert world.learn().reviewed == 12


def test_a_model_that_fails_leaves_the_conversations_to_try_again():
    world = World()
    world.llm = FakeLLMProvider([LLMError("down")] * 12, default=verdict(unknown=True))
    world.reflector = ConsultativeSalesBrain(world.llm, parse_settings(settings_doc()))
    world.service = LearningService(CLIENT, world.store, world.reflector)
    world.play()
    first = world.learn()
    assert first.reviewed == 0 and first.failed == 12
    assert len(world.store.case_refs()) == 12
    assert world.learn().reviewed == 12


def test_a_run_reviews_a_bounded_number_of_conversations():
    world = World(winners=10, losers=10)
    world.play()
    assert world.learn().reviewed == 12
    assert world.learn().reviewed == 8


def test_every_review_is_a_model_call_that_is_made_once_per_conversation():
    world = World()
    world.play()
    world.learn()
    calls = len(world.llm.calls)
    world.learn()
    assert len(world.llm.calls) == calls == 12


def test_nothing_personal_is_kept():
    world = World(review=verdict(causes=["generic_message"], confidence="high"))
    world.play()
    world.learn()
    kept = world.dump() + repr(world.store.notes())
    for private in (
        "Alex",
        "Example",
        "+1555",
        "STOP",
        "Which plan",
        "very long message",
        "w1",
        "l1",
    ):
        assert private not in kept, private
    assert not world.store.case_refs()  # the traces went with the verdicts


def test_a_prospect_cannot_plant_a_rule_by_what_they_write():
    world = World(review=verdict(causes=["generic_message"], practices=["acknowledge_first"]))
    world.play()
    world.learn()
    codes = {n.code for n in world.service.notes()}
    assert codes <= {"short_messages", "long_messages", "single_question", "generic_message",
                     "acknowledge_first"}  # fmt: skip
