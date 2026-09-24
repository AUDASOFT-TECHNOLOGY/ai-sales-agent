from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest
from factories import NAIVE_NOW, NOW, make_brain_request, make_conversation

from sales_core.contracts import (
    BrainDecision,
    ContactOrigin,
    DecisionType,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    MessageId,
)


class TestFactStatus:
    def test_confirmed_fact(self):
        fact = Fact(key="k", value="v", status=FactStatus.CONFIRMED)
        assert fact.status is FactStatus.CONFIRMED
        assert fact.is_confirmed

    def test_discovered_fact(self):
        fact = Fact(key="k", value="v", status=FactStatus.DISCOVERED)
        assert fact.status is FactStatus.DISCOVERED
        assert not fact.is_confirmed

    def test_hypothesis_fact_carries_a_confidence(self):
        fact = Fact(key="k", value="v", status=FactStatus.HYPOTHESIS, confidence=0.4)
        assert fact.status is FactStatus.HYPOTHESIS
        assert fact.confidence == 0.4

    def test_a_hypothesis_is_never_treated_as_confirmed(self):
        fact = Fact(key="k", value="v", status=FactStatus.HYPOTHESIS, confidence=1.0)
        assert not fact.is_confirmed
        assert fact != Fact(key="k", value="v", status=FactStatus.CONFIRMED, confidence=1.0)

    def test_status_cannot_change_in_place(self):
        fact = Fact(key="k", value="v", status=FactStatus.HYPOTHESIS)
        with pytest.raises(FrozenInstanceError):
            fact.status = FactStatus.CONFIRMED  # type: ignore[misc]

    def test_a_brain_inference_cannot_be_confirmed(self):
        evidence = FactEvidence(source=FactSource.BRAIN_INFERENCE)
        with pytest.raises(ValueError, match="cannot be CONFIRMED"):
            Fact(key="k", value="v", status=FactStatus.CONFIRMED, evidence=evidence)
        Fact(key="k", value="v", status=FactStatus.HYPOTHESIS, evidence=evidence)

    def test_status_values_are_stable(self):
        assert {s.value for s in FactStatus} == {"confirmed", "discovered", "hypothesis"}


class TestFactShape:
    def test_keys_are_free_form_and_ordinary_facts_need_no_metadata(self):
        fact = Fact(key="anything-a-brain-wants", value="x", status=FactStatus.DISCOVERED)
        assert fact.evidence is None
        assert fact.confidence is None
        assert fact.recorded_at is None

    @pytest.mark.parametrize("overrides", [{"key": " "}, {"value": ""}])
    def test_blank_key_or_value_is_rejected(self, overrides):
        fields = {"key": "k", "value": "v", "status": FactStatus.DISCOVERED} | overrides
        with pytest.raises(ValueError, match="non-empty"):
            Fact(**fields)

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_must_be_a_probability(self, confidence):
        with pytest.raises(ValueError, match="between 0 and 1"):
            Fact(key="k", value="v", status=FactStatus.HYPOTHESIS, confidence=confidence)

    def test_timestamps_must_be_aware_and_ordered(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Fact(key="k", value="v", status=FactStatus.DISCOVERED, recorded_at=NAIVE_NOW)
        with pytest.raises(ValueError, match="timezone-aware"):
            Fact(key="k", value="v", status=FactStatus.DISCOVERED, updated_at=NAIVE_NOW)
        with pytest.raises(ValueError, match="before recorded_at"):
            Fact(
                key="k",
                value="v",
                status=FactStatus.DISCOVERED,
                recorded_at=NOW,
                updated_at=NOW - timedelta(seconds=1),
            )


class TestFactEvidence:
    def test_evidence_can_be_attached(self):
        evidence = FactEvidence(
            source=FactSource.PROSPECT,
            message_id=MessageId("m-1"),
            excerpt="the prospect's own words",
            observed_at=NOW,
        )
        fact = Fact(key="k", value="v", status=FactStatus.CONFIRMED, evidence=evidence)
        assert fact.evidence is evidence
        assert fact.evidence.source is FactSource.PROSPECT

    @pytest.mark.parametrize("source", list(FactSource))
    def test_every_source_is_a_valid_origin(self, source):
        assert FactEvidence(source=source).source is source

    def test_source_vocabulary_is_stable(self):
        assert {s.value for s in FactSource} == {
            "prospect",
            "client_data",
            "conversation",
            "system",
            "human",
            "brain_inference",
        }

    def test_blank_parts_and_naive_time_are_rejected(self):
        with pytest.raises(ValueError, match="excerpt"):
            FactEvidence(source=FactSource.PROSPECT, excerpt=" ")
        with pytest.raises(ValueError, match="message_id"):
            FactEvidence(source=FactSource.PROSPECT, message_id=MessageId(""))
        with pytest.raises(ValueError, match="timezone-aware"):
            FactEvidence(source=FactSource.HUMAN, observed_at=NAIVE_NOW)


class TestContactOrigin:
    def test_inbound_and_outbound_are_distinct(self):
        inbound = make_conversation(origin=ContactOrigin.INBOUND)
        outbound = make_conversation(origin=ContactOrigin.OUTBOUND)
        assert inbound.origin is not outbound.origin
        assert {o.value for o in ContactOrigin} == {"inbound", "outbound", "unknown"}

    def test_history_without_an_origin_is_unknown(self):
        assert make_conversation().origin is ContactOrigin.UNKNOWN

    def test_the_brain_sees_the_origin_through_the_conversation(self):
        request = make_brain_request(conversation=make_conversation(origin=ContactOrigin.INBOUND))
        assert request.conversation.origin is ContactOrigin.INBOUND


class TestBackwardCompatibility:
    def test_a_request_built_the_old_way_has_no_facts(self):
        request = make_brain_request()
        assert request.facts == ()
        assert request.conversation.origin is ContactOrigin.UNKNOWN

    def test_facts_reach_the_brain_as_an_immutable_tuple(self):
        fact = Fact(key="k", value="v", status=FactStatus.HYPOTHESIS)
        request = make_brain_request(facts=[fact])
        assert request.facts == (fact,)
        assert isinstance(request.facts, tuple)

    def test_an_existing_brain_still_works_with_the_richer_request(self):
        class LegacyBrain:
            def decide(self, request):
                return BrainDecision(decision_type=DecisionType.NO_ACTION, rationale="legacy")

        fact = Fact(key="k", value="v", status=FactStatus.DISCOVERED)
        request = make_brain_request(
            facts=(fact,), conversation=make_conversation(origin=ContactOrigin.OUTBOUND)
        )
        assert LegacyBrain().decide(request).decision_type is DecisionType.NO_ACTION

    def test_brain_decision_is_unchanged(self):
        assert BrainDecision(decision_type=DecisionType.CLOSE).metadata == {}
