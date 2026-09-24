"""Fact status semantics: confirmed is relied on, discovered is known but unconfirmed, and a
hypothesis guides without ever being asserted or promoted."""

from datetime import timedelta

import pytest
from brain_support import (
    CONFIRMED,
    DISCOVERED,
    HYPOTHESIS,
    REPLY,
    answer,
    context_for,
    decide,
    fact,
    handoff,
    make_brain,
    payload_of,
    request,
    set_at,
    settings_doc,
    turns,
)

from sales_brain_kit.consultative import BrainOutputError
from sales_core.contracts import FactSource


def known(**options):
    return {f.key: f for f in context_for(**options).facts}


class TestTheStatusOfAFactIsKept:
    def test_a_confirmed_fact_is_confirmed(self):
        view = known(facts=[fact("goal", "lead a team", CONFIRMED, source=FactSource.PROSPECT)])
        assert view["goal"].status is CONFIRMED
        assert view["goal"].settled

    def test_a_discovered_fact_is_discovered_and_not_upgraded(self):
        view = known(facts=[fact("goal", "lead a team", DISCOVERED)])
        assert view["goal"].status is DISCOVERED
        assert view["goal"].status is not CONFIRMED
        assert view["goal"].settled

    def test_a_hypothesis_stays_a_hypothesis_and_is_not_settled(self):
        view = known(facts=[fact("goal", "lead a team", HYPOTHESIS, confidence=0.9)])
        assert view["goal"].status is HYPOTHESIS
        assert not view["goal"].settled
        assert (
            context_for(facts=[fact("goal", "x", HYPOTHESIS, confidence=0.9)]).facts.settled("goal")
            is None
        )

    def test_the_newest_observation_of_a_key_is_the_current_one(self):
        view = known(
            facts=[fact("role", "student", DISCOVERED), fact("role", "manager", CONFIRMED)]
        )
        assert view["role"].value == "manager"
        assert view["role"].status is CONFIRMED

    def test_a_later_hypothesis_never_overrules_a_known_fact(self):
        view = known(
            facts=[
                fact("role", "manager", CONFIRMED),
                fact("role", "student", HYPOTHESIS, confidence=1.0),
            ]
        )
        assert view["role"].value == "manager" and view["role"].status is CONFIRMED

    def test_a_hypothesis_below_the_configured_confidence_is_dropped(self):
        doc = set_at(settings_doc(), "discovery.hypotheses.min_confidence_to_use", 0.7)
        assert "goal" not in {
            f.key
            for f in context_for(doc, facts=[fact("goal", "x", HYPOTHESIS, confidence=0.5)]).facts
        }
        assert "goal" in {
            f.key
            for f in context_for(doc, facts=[fact("goal", "x", HYPOTHESIS, confidence=0.7)]).facts
        }

    def test_a_hypothesis_without_a_confidence_is_not_used(self):
        assert "goal" not in known(facts=[fact("goal", "x", HYPOTHESIS)])

    def test_a_field_that_forbids_inference_never_keeps_a_hypothesis(self):
        doc = set_at(settings_doc(), "discovery.fields.0.infer", {"allowed": False})
        view = {
            f.key
            for f in context_for(doc, facts=[fact("goal", "x", HYPOTHESIS, confidence=0.99)]).facts
        }
        assert "goal" not in view

    def test_a_value_the_client_already_holds_counts_as_discovered(self):
        view = known(attributes={"Job": "manager"})
        assert view["role"].status is DISCOVERED
        assert view["role"].lead_data and view["role"].source is FactSource.CLIENT_DATA

    def test_a_recorded_fact_takes_precedence_over_lead_data(self):
        view = known(attributes={"Job": "student"}, facts=[fact("role", "manager", CONFIRMED)])
        assert view["role"].value == "manager" and not view["role"].lead_data

    def test_the_brain_never_records_or_changes_facts(self):
        facts = [fact("goal", "lead a team", HYPOTHESIS, confidence=0.9)]
        brain, _ = make_brain(None, answer())
        request_ = request(facts=facts)
        brain.decide(request_)
        assert request_.facts == tuple(facts)  # untouched: nothing is promoted or written


class TestFactsInTheModelContext:
    def payload(self, **options):
        brain, llm = make_brain(None, answer())
        brain.decide(request(**options))
        return payload_of(llm)["facts"]

    def test_facts_are_grouped_by_status(self):
        facts = self.payload(
            facts=[
                fact("role", "manager", CONFIRMED),
                fact("goal", "lead a team", DISCOVERED),
                fact("sector", "retail", HYPOTHESIS, confidence=0.8),
            ]
        )
        assert [f["key"] for f in facts["confirmed"]] == ["role"]
        assert [f["key"] for f in facts["discovered"]] == ["goal"]
        assert [f["key"] for f in facts["hypotheses"]] == ["sector"]
        assert facts["hypotheses"][0]["confidence"] == 0.8

    def test_a_hypothesis_is_never_listed_as_known(self):
        facts = self.payload(facts=[fact("sector", "retail", HYPOTHESIS, confidence=0.8)])
        assert "confirmed" not in facts and "discovered" not in facts
        assert facts["hypotheses"][0]["value"] == "retail"

    def test_the_model_is_told_never_to_state_a_hypothesis_as_fact(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        system = llm.calls[0].messages[0].content
        assert (
            "never state one as fact" in system and "'confirmed' facts may be relied on" in system
        )

    def test_lead_data_is_marked_as_such(self):
        facts = self.payload(attributes={"Job": "manager"})
        assert facts["discovered"][0]["from"] == "lead_data"

    def test_facts_nobody_declared_stay_out_of_the_context(self):
        facts = self.payload(
            facts=[fact("shoe_size", "42", CONFIRMED), fact("role", "manager", CONFIRMED)]
        )
        assert [f["key"] for f in facts["confirmed"]] == ["role"]

    def test_protected_attributes_are_kept_out_even_if_recorded(self):
        facts = self.payload(
            facts=[
                fact("nationality", "x", CONFIRMED),
                fact("religion", "y", DISCOVERED),
                fact("role", "manager", CONFIRMED),
            ]
        )
        keys = {f["key"] for group in facts.values() for f in group}
        assert keys == {"role"}

    def test_a_sensitive_field_is_flagged(self):
        facts = self.payload(facts=[fact("budget", "low", CONFIRMED, source=FactSource.PROSPECT)])
        assert facts["confirmed"][0]["sensitive"] is True

    def test_place_may_only_adjust_practical_matters(self):
        facts = self.payload(facts=[fact("country", "somewhere", CONFIRMED)])
        assert facts["confirmed"][0]["use"] == "practical_only"

    def test_a_protected_fact_reaches_the_model_only_when_a_qualification_rule_needs_it(self):
        doc = set_at(
            settings_doc(),
            "discovery.fields.5",
            {"key": "age", "sensitive": True, "importance": "optional"},
        )
        doc = set_at(doc, "catalog.products.0.qualification.fit.0.fact", "age")
        brain, llm = make_brain(doc, answer(selected_product="alpha"))
        brain.decide(
            request(
                facts=[
                    fact("interest", "alpha", CONFIRMED),
                    fact("age", "40", CONFIRMED, source=FactSource.PROSPECT),
                ]
            )
        )
        keys = {f["key"] for f in payload_of(llm)["facts"]["confirmed"]}
        assert "age" in keys  # the client's own rule depends on it, so the model may see it
        brain, llm = make_brain(doc, answer())
        brain.decide(request(facts=[fact("age", "40", CONFIRMED, source=FactSource.PROSPECT)]))
        assert "facts" not in payload_of(llm)  # not needed for a product in focus: withheld


class TestFactStatusInDecisions:
    def test_a_hypothesis_cannot_disqualify_a_prospect(self):
        facts = [
            fact("interest", "alpha", CONFIRMED),
            fact("role", "student", HYPOTHESIS, confidence=0.99),
        ]
        wrong = answer(
            selected_product="alpha",
            analysis={"disqualified": [{"product_id": "alpha", "rule_id": "student"}]},
        )
        with pytest.raises(BrainOutputError) as error:
            decide(wrong, wrong, facts=facts)
        assert "cannot disqualify" in str(error.value)

    def test_a_missing_fact_cannot_disqualify_a_prospect(self):
        wrong = answer(
            selected_product="alpha",
            analysis={"disqualified": [{"product_id": "alpha", "rule_id": "student"}]},
        )
        with pytest.raises(BrainOutputError):
            decide(wrong, wrong, facts=[fact("interest", "alpha", CONFIRMED)])

    def test_a_discovered_fact_may_disqualify_and_the_configured_effect_follows(self):
        facts = [fact("interest", "alpha", CONFIRMED), fact("eligibility", "no", DISCOVERED)]
        flagged = {"disqualified": [{"product_id": "alpha", "rule_id": "ineligible"}]}
        decision = decide(handoff(analysis=flagged), facts=facts, action=REPLY)
        assert decision.decision_type.value == "handoff_to_human"  # this rule's effect is a handoff
        keep_selling = answer(selected_product="alpha", analysis=flagged)
        with pytest.raises(BrainOutputError) as error:
            decide(keep_selling, keep_selling, facts=facts, action=REPLY)
        assert "disqualifier 'ineligible' is handed to a human" in str(error.value)

    def test_a_hypothesis_does_not_satisfy_what_a_next_step_requires(self):
        plan = context_for(facts=[fact("goal", "lead a team", HYPOTHESIS, confidence=0.9)]).plan
        assert plan.discovery.status.value == "ask"
        (goal,) = [t for t in plan.discovery.targets if t.key == "goal"]
        assert goal.hypothesis == "lead a team"  # it may be offered to the prospect to confirm

    def test_a_discovered_fact_satisfies_it(self):
        plan = context_for(facts=[fact("goal", "lead a team", DISCOVERED)]).plan
        assert plan.discovery.status.value == "not_needed"

    def test_an_old_fact_can_be_due_for_confirmation_again(self):
        doc = set_at(settings_doc(), "discovery.fields.0.recheck_after_days", 30)
        fresh = context_for(doc, facts=[fact("goal", "x", CONFIRMED, age=timedelta(days=5))]).plan
        stale = context_for(doc, facts=[fact("goal", "x", CONFIRMED, age=timedelta(days=60))]).plan
        assert fresh.discovery.status.value == "not_needed"
        assert stale.discovery.status.value == "ask"
        assert stale.discovery.targets[0].recheck is True

    def test_relevant_history_is_in_the_context_too(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request(action=REPLY, messages=turns(("out", "Hi!"), ("in", "Tell me more"))))
        payload = payload_of(llm)
        assert payload["latest_prospect_message"] == "Tell me more"
        assert [m["from"] for m in payload["transcript"]] == ["us", "prospect"]
