"""Slice 6c: facts a Brain proposes travel through the Engine: validated, recorded by the Core,
visible to the next request. The Brain here is a stub; nothing is client-specific."""

from dataclasses import fields, replace

import pytest
from engine_harness import ScriptedBrain, decide, due, inbound, prospect, send
from factories import CLIENT, NOW, make_client_context

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    CORE_FACT_KEY_PREFIX,
    BrainRequest,
    ClientId,
    DecisionType,
    Fact,
    FactEvidence,
    FactProposal,
    FactSource,
    FactStatus,
    MessageId,
    ProspectId,
)
from sales_core.engine import Engine, EngineConfig
from sales_core.ports import FactsWriter
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository

P1 = ProspectId("p1")
INBOUND_ID = MessageId("in:whatsapp:wamid.1")
SECOND_ID = MessageId("in:whatsapp:wamid.2")


def statement(**overrides):
    fields_ = {
        "key": "plan",
        "value": "big",
        "status": FactStatus.CONFIRMED,
        "source": FactSource.PROSPECT,
        "message_id": INBOUND_ID,
    }
    return FactProposal(**(fields_ | overrides))


class RecordingRepository(FakeSalesRepository):
    """Remembers when facts were written relative to Brain calls."""

    def __init__(self, *args, log, **kwargs):
        super().__init__(*args, **kwargs)
        self.log = log

    def append_facts(self, prospect_id, facts):
        self.log.append(("append_facts", len(facts)))
        super().append_facts(prospect_id, facts)


class World:
    def __init__(
        self, decisions, *, mode=ApprovalMode.APPROVAL, writer=True, prospects=None, **config
    ):
        self.log = []
        self.clock = FakeClock(NOW)
        self.repo = RecordingRepository(
            CLIENT, prospects=prospects or [prospect("p1")], log=self.log
        )
        self.decisions = list(decisions)

        def fn(request):
            self.log.append(("decide", len(request.facts)))
            return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]

        self.brain = ScriptedBrain(fn)
        self.engine = Engine(
            client=make_client_context(),
            repository=self.repo,
            brain=self.brain,
            channel=FakeMessagingChannel(clock=self.clock),
            clock=self.clock,
            config=EngineConfig(mode=mode, **config),
            facts=self.repo,
            facts_writer=self.repo if writer else None,
        )

    def reply(self, external_id="wamid.1", text="I need the big plan"):
        receipt = self.engine.receive_inbound(inbound(text, external_id=external_id))
        return self.engine.process_inbound(receipt)

    @property
    def facts(self):
        return list(self.repo.get_facts(P1))

    def events(self, event_type):
        return [e for e in self.repo.audit_events if e.event_type == event_type]


def proposing(*proposals):
    return replace(send("Noted"), proposed_facts=tuple(proposals))


class TestFlow:
    def test_the_brain_proposes_the_core_validates_and_records_and_the_next_request_sees_it(self):
        world = World([proposing(statement(excerpt="big plan")), proposing()])
        assert world.reply().ok
        (fact,) = world.facts
        assert fact.evidence.source is FactSource.PROSPECT
        assert fact.evidence.message_id == INBOUND_ID
        assert fact.evidence.recorded_by.startswith("brain-proposal:")  # signed by the Core
        assert fact.evidence.observed_at == NOW  # the stored message's time
        assert fact.status is FactStatus.CONFIRMED

        assert world.reply("wamid.2").ok
        assert world.brain.calls[1].facts == (fact,)

    def test_facts_are_recorded_after_the_brain_answers_and_by_the_core(self):
        world = World([proposing(statement())])
        world.reply()
        assert world.log == [("decide", 0), ("append_facts", 1)]

    def test_the_brain_is_handed_no_way_to_write_facts(self):
        world = World([proposing()])
        world.reply()
        request = world.brain.calls[0]
        assert isinstance(request, BrainRequest)
        for field in fields(request):
            assert not isinstance(getattr(request, field.name), FactsWriter), field.name

    def test_a_decision_without_proposals_writes_nothing(self):
        world = World([send("Hello")])
        world.reply()
        assert world.facts == []
        assert ("append_facts", 1) not in world.log
        assert not world.events("fact.proposal_accepted")

    def test_facts_are_recorded_even_when_the_decision_sends_nothing(self):
        handoff = replace(decide(DecisionType.HANDOFF_TO_HUMAN), proposed_facts=(statement(),))
        world = World([handoff])
        world.reply()
        assert len(world.facts) == 1

    def test_accepted_and_rejected_proposals_are_audited_without_the_value(self):
        world = World(
            [
                proposing(
                    statement(value="secret-value"),
                    statement(key="other", value="hidden", message_id=MessageId("nope")),
                )
            ]
        )
        world.reply()
        (accepted,) = world.events("fact.proposal_accepted")
        (rejected,) = world.events("fact.proposal_rejected")
        assert accepted.details["key"] == "plan" and accepted.details["source"] == "prospect"
        assert rejected.details["reason"] == "unknown_message"
        assert accepted.actor == rejected.actor == "brain"
        for event in (accepted, rejected):
            assert "secret-value" not in str(dict(event.details))
            assert "hidden" not in str(dict(event.details))


class TestFailClosed:
    def test_a_refused_proposal_is_not_recorded_but_the_decision_still_applies(self):
        world = World([proposing(statement(message_id=MessageId("nope")))])
        report = world.reply()
        assert report.ok
        assert world.facts == []
        assert [a.status.value for a in world.repo.list_actions()] == ["proposed"]
        assert world.events("fact.proposal_rejected")

    def test_a_brain_inference_cannot_become_confirmed_through_the_engine(self):
        bad = FactProposal(
            key="plan", value="big", status=FactStatus.CONFIRMED, source=FactSource.BRAIN_INFERENCE
        )
        world = World([proposing(bad)])
        world.reply()
        assert world.facts == []
        assert world.events("fact.proposal_rejected")[0].details["reason"] == "status_not_allowed"

    def test_a_message_of_another_prospect_cannot_be_cited(self):
        world = World([proposing(statement())])
        world.repo.add_prospects(prospect("p2"))
        world.engine.receive_inbound(inbound("mine", pid="p2", external_id="wamid.other"))
        stranger_message = MessageId("in:whatsapp:wamid.other")
        world.decisions[:] = [proposing(statement(message_id=stranger_message))]
        world.reply()
        assert world.facts == []
        assert world.events("fact.proposal_rejected")[0].details["reason"] == "unknown_message"

    def test_without_a_writer_proposals_are_refused_and_audited_not_silently_lost(self):
        world = World([proposing(statement())], writer=False)
        assert world.reply().ok
        assert world.facts == []
        assert world.events("fact.proposal_rejected")[0].details["reason"] == "no_writer"

    def test_a_dry_run_asks_the_brain_and_records_nothing(self):
        world = World([proposing(statement())], mode=ApprovalMode.DRY_RUN, prospects=[due("p9")])
        world.engine.prepare()
        assert world.brain.calls
        assert list(world.repo.get_facts(ProspectId("p9"))) == []
        assert not world.events("fact.proposal_accepted")

    def test_a_storage_failure_records_nothing_of_the_decision_and_the_action_is_retried(self):
        world = World([proposing(statement())])
        world.repo.fail_next("append_facts")
        report = world.reply()
        assert not report.ok
        assert world.facts == []
        assert world.repo.list_actions()[0].status.value == "planned"  # nothing was applied
        assert not world.events("fact.proposal_accepted")  # only audited once recorded

    def test_a_confirmed_fact_from_a_trusted_source_is_not_displaced(self):
        world = World([proposing(statement(value="small"))])
        world.repo.append_facts(
            P1,
            [
                Fact(
                    key="plan",
                    value="big",
                    status=FactStatus.CONFIRMED,
                    evidence=FactEvidence(source=FactSource.HUMAN, recorded_by="operator"),
                    recorded_at=NOW,
                )
            ],
        )
        world.reply()
        assert [f.value for f in world.facts] == ["big"]
        rejected = world.events("fact.proposal_rejected")[0]
        assert rejected.details["reason"] == "conflicts_with_authoritative_fact"

    def test_a_key_the_core_writes_itself_cannot_be_proposed(self):
        world = World(
            [proposing(statement(key=f"{CORE_FACT_KEY_PREFIX}anything", value="confirmed"))]
        )
        world.reply()
        assert world.facts == []
        assert world.events("fact.proposal_rejected")[0].details["reason"] == "reserved_key"

    def test_a_stage_set_by_the_brain_records_no_fact_at_all(self):
        world = World([send("Great", stage="some-stage")])
        world.reply()
        assert world.facts == []


class TestIdempotencyAndHistory:
    def test_a_retry_after_a_failed_step_does_not_duplicate_the_fact(self):
        close = replace(
            decide(DecisionType.CLOSE),
            proposed_facts=(
                statement(
                    message_id=None, source=FactSource.CONVERSATION, status=FactStatus.DISCOVERED
                ),
            ),
        )
        world = World([close], prospects=[due("p9")])
        world.repo.fail_next("update_prospects")  # the decision cannot be applied the first time
        assert not world.engine.prepare().ok
        assert len(list(world.repo.get_facts(ProspectId("p9")))) == 1
        assert world.engine.prepare().ok  # the leftover PLANNED action is asked again
        assert len(list(world.repo.get_facts(ProspectId("p9")))) == 1

    def test_processing_the_same_result_twice_keeps_one_observation(self):
        world = World([proposing(statement())])
        receipt = world.engine.receive_inbound(inbound("I need the big plan"))
        world.engine.process_inbound(receipt)
        world.engine.process_inbound(receipt)  # webhook redelivery
        assert len(world.facts) == 1

    def test_a_hypothesis_repeated_is_never_promoted(self):
        guess = FactProposal(
            key="plan",
            value="big",
            status=FactStatus.HYPOTHESIS,
            source=FactSource.BRAIN_INFERENCE,
            confidence=0.6,
        )
        world = World([proposing(guess)])
        for n in range(1, 4):
            world.reply(f"wamid.{n}")
        assert len(world.facts) == 3  # each was a separate observation of the same guess
        assert {f.status for f in world.facts} == {FactStatus.HYPOTHESIS}
        assert {f.evidence.source for f in world.facts} == {FactSource.BRAIN_INFERENCE}

    def test_a_later_trusted_fact_keeps_its_own_evidence_and_the_hypothesis_stays_in_history(self):
        guess = FactProposal(
            key="plan", value="big", status=FactStatus.HYPOTHESIS, source=FactSource.BRAIN_INFERENCE
        )
        world = World([proposing(guess), proposing(statement(message_id=SECOND_ID))])
        world.reply("wamid.1")
        world.reply("wamid.2")
        hypothesis, confirmed = world.facts
        assert hypothesis.status is FactStatus.HYPOTHESIS
        assert confirmed.status is FactStatus.CONFIRMED
        assert confirmed.evidence.message_id == SECOND_ID
        assert confirmed.evidence != hypothesis.evidence

    def test_the_engine_still_needs_a_reader_beside_a_writer_and_the_same_client(self):
        repo = FakeSalesRepository(CLIENT)
        common = {
            "client": make_client_context(),
            "repository": repo,
            "brain": ScriptedBrain(),
            "channel": FakeMessagingChannel(clock=FakeClock(NOW)),
            "clock": FakeClock(NOW),
            "config": EngineConfig(mode=ApprovalMode.APPROVAL),
        }
        with pytest.raises(ValueError, match="reader"):
            Engine(**common, facts_writer=repo)
        other = FakeSalesRepository(ClientId("other"))
        with pytest.raises(ValueError, match="different client"):
            Engine(**common, facts=repo, facts_writer=other)
