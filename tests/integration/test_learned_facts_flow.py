"""What a prospect says, end to end: the real Brain kit proposes, the Engine has the Core validate,
and only the Core records. Nothing is client-specific.

    prospect: "I live in Country A"  -> Brain proposal -> Core review -> stored Fact (with evidence)
"""

import json
import sys
from pathlib import Path

from engine_harness import build, inbound, prospect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kit"))

from brain_support import answer, settings_doc

from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_core.approval import ApprovalMode
from sales_core.contracts import FactSource, FactStatus, ProspectId
from sales_integrations.fakes import FakeLLMProvider

P1 = ProspectId("p1")


def report(key, value, excerpt, basis="stated", confidence=None):
    return {
        "key": key,
        "value": value,
        "basis": basis,
        "excerpt": excerpt,
        "confidence": confidence,
    }


class Flow:
    def __init__(self, *answers):
        llm = FakeLLMProvider([json.dumps(a) for a in answers])
        brain = ConsultativeSalesBrain(llm, parse_settings(settings_doc()))
        self.h = build(brain=brain, mode=ApprovalMode.APPROVAL, prospects=[prospect("p1")])
        self.h.facts = self.h.facts_writer = self.h.repo
        self.h.engine = self.h.new_engine()
        self.n = 0

    def say(self, text):
        self.n += 1
        message = inbound(text, external_id=f"wamid.{self.n}")
        return self.h.engine.process_inbound(self.h.engine.receive_inbound(message))

    def facts(self):
        return list(self.h.repo.get_facts(P1))


def test_a_stated_fact_is_recorded_by_the_core_with_its_evidence():
    flow = Flow(answer(learned_facts=[report("country", "Country A", "I live in Country A")]))
    assert flow.say("Hello, I live in Country A").ok
    (stored,) = flow.facts()
    assert (stored.key, stored.value) == ("country", "Country A")
    assert stored.status is FactStatus.CONFIRMED
    assert stored.evidence.source is FactSource.PROSPECT
    assert stored.evidence.message_id == "in:whatsapp:wamid.1"
    assert stored.evidence.excerpt == "I live in Country A"
    assert stored.evidence.recorded_by.startswith("brain-proposal:")


def test_a_guess_is_recorded_as_a_hypothesis_and_never_as_known():
    guessed = report("sector", "training", None, basis="guess", confidence=0.8)
    flow = Flow(answer(learned_facts=[guessed]))
    assert flow.say("I run workshops").ok
    (stored,) = flow.facts()
    assert stored.status is FactStatus.HYPOTHESIS
    assert stored.evidence.source is FactSource.BRAIN_INFERENCE


def test_a_vague_message_records_nothing():
    flow = Flow(answer())
    assert flow.say("I am interested").ok
    assert flow.facts() == []


def test_a_fact_that_is_already_recorded_is_not_recorded_again():
    said = answer(learned_facts=[report("country", "Country A", "I live in Country A")])
    flow = Flow(said, said)
    flow.say("I live in Country A")
    flow.say("Yes, I live in Country A, as I said")
    assert [f.key for f in flow.facts()] == ["country"]


def test_a_refused_answer_records_nothing():
    forged = answer(learned_facts=[report("country", "Country A", "words nobody wrote")])
    flow = Flow(forged, forged)
    assert not flow.say("Hello").ok
    assert flow.facts() == []
