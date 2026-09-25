"""Slice 6c: what a Brain may propose as a fact, and what the Core makes of it (pure, no Engine)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from factories import CLIENT, NOW, make_conversation, make_message, make_prospect

from sales_core.contracts import (
    CORE_FACT_KEY_PREFIX,
    BrainDecision,
    ClientId,
    DecisionType,
    Direction,
    Fact,
    FactEvidence,
    FactProposal,
    FactSource,
    FactStatus,
    MessageId,
    fact_identity,
)
from sales_core.facts import ProposalRejection, review_proposals

INBOUND = make_message(
    message_id=MessageId("in-1"),
    direction=Direction.INBOUND,
    text="I need  the Big plan, thanks",
    timestamp=NOW - timedelta(minutes=5),
)
OUTBOUND = make_message(
    message_id=MessageId("out-1"), direction=Direction.OUTBOUND, text="Hello there"
)
CONVERSATION = make_conversation(messages=(OUTBOUND, INBOUND))
RECORDED_AT = NOW
SIGNATURE = "brain-proposal:a-1"


def proposal(**overrides):
    fields = {
        "key": "plan",
        "value": "big",
        "status": FactStatus.CONFIRMED,
        "source": FactSource.PROSPECT,
        "message_id": MessageId("in-1"),
    }
    return FactProposal(**(fields | overrides))


def review(*proposals, existing=(), prospect=None, conversation=CONVERSATION, client=CLIENT):
    return review_proposals(
        proposals,
        client_id=client,
        prospect=prospect or make_prospect(),
        conversation=conversation,
        existing=existing,
        recorded_at=RECORDED_AT,
        recorded_by=SIGNATURE,
    )


def reasons(result):
    return [r.reason for r in result.rejected]


class TestContract:
    def test_a_proposal_is_not_a_fact_and_carries_no_time_or_author(self):
        fields = set(FactProposal.__dataclass_fields__)
        assert not fields & {"recorded_at", "updated_at", "observed_at", "recorded_by", "evidence"}

    def test_an_excerpt_needs_the_message_it_comes_from(self):
        with pytest.raises(ValueError, match="message_id"):
            proposal(message_id=None, excerpt="big plan")

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_stays_between_zero_and_one(self, confidence):
        with pytest.raises(ValueError, match="confidence"):
            proposal(confidence=confidence)

    @pytest.mark.parametrize("field", ["key", "value", "message_id", "excerpt"])
    def test_text_fields_are_not_blank(self, field):
        with pytest.raises(ValueError):
            proposal(**({"excerpt": "big"} | {field: "  "}))

    def test_a_decision_proposes_no_facts_by_default(self):
        assert BrainDecision(decision_type=DecisionType.NO_ACTION).proposed_facts == ()

    def test_a_decision_holds_its_proposals_as_a_tuple(self):
        decision = BrainDecision(decision_type=DecisionType.NO_ACTION, proposed_facts=[proposal()])
        assert decision.proposed_facts == (proposal(),)

    def test_a_decision_cannot_propose_an_unreasonable_number_of_facts(self):
        many = [proposal(key=f"k{i}") for i in range(21)]
        with pytest.raises(ValueError, match="at most"):
            BrainDecision(decision_type=DecisionType.NO_ACTION, proposed_facts=many)


class TestAcceptance:
    def test_a_statement_by_the_prospect_becomes_a_confirmed_fact_with_its_provenance(self):
        result = review(proposal(excerpt="big plan"))
        (fact,) = result.accepted
        assert not result.rejected
        assert fact == Fact(
            key="plan",
            value="big",
            status=FactStatus.CONFIRMED,
            evidence=FactEvidence(
                source=FactSource.PROSPECT,
                message_id=MessageId("in-1"),
                excerpt="big plan",
                observed_at=INBOUND.timestamp,  # from the stored message, not from the Brain
                recorded_by=SIGNATURE,
            ),
            recorded_at=RECORDED_AT,
        )

    def test_discovered_from_the_conversation_needs_no_message(self):
        (fact,) = review(
            proposal(source=FactSource.CONVERSATION, status=FactStatus.DISCOVERED, message_id=None)
        ).accepted
        assert fact.status is FactStatus.DISCOVERED
        assert fact.evidence.source is FactSource.CONVERSATION
        assert fact.evidence.observed_at is None  # nothing to take a time from, none invented

    def test_an_inference_is_recorded_as_a_hypothesis_with_its_confidence(self):
        (fact,) = review(
            proposal(
                source=FactSource.BRAIN_INFERENCE,
                status=FactStatus.HYPOTHESIS,
                confidence=0.4,
                message_id=None,
            )
        ).accepted
        assert (fact.status, fact.confidence) == (FactStatus.HYPOTHESIS, 0.4)
        assert fact.evidence.source is FactSource.BRAIN_INFERENCE

    def test_a_message_may_be_cited_for_a_conversation_derived_fact_even_an_outbound_one(self):
        result = review(
            proposal(
                source=FactSource.CONVERSATION,
                status=FactStatus.DISCOVERED,
                message_id=MessageId("out-1"),
            )
        )
        assert len(result.accepted) == 1

    def test_the_excerpt_may_differ_in_case_and_spacing_but_must_be_in_the_message(self):
        assert review(proposal(excerpt="THE BIG   plan")).accepted

    def test_the_same_proposal_twice_in_one_result_is_one_fact(self):
        assert len(review(proposal(), proposal()).accepted) == 1

    def test_the_same_result_reviewed_twice_gives_the_same_identity(self):
        first = review(proposal()).accepted[0]
        second = review(proposal()).accepted[0]
        prospect = make_prospect()
        assert fact_identity(CLIENT, prospect.prospect_id, first) == fact_identity(
            CLIENT, prospect.prospect_id, second
        )

    def test_one_bad_proposal_does_not_take_the_others_with_it(self):
        result = review(proposal(key="a"), proposal(key="b", message_id=MessageId("nope")))
        assert [f.key for f in result.accepted] == ["a"]
        assert reasons(result) == [ProposalRejection.UNKNOWN_MESSAGE]


class TestStatusAndProvenanceRules:
    def test_an_inference_can_never_be_confirmed(self):
        result = review(
            proposal(
                source=FactSource.BRAIN_INFERENCE, status=FactStatus.CONFIRMED, message_id=None
            )
        )
        assert not result.accepted
        assert reasons(result) == [ProposalRejection.STATUS_NOT_ALLOWED]

    def test_an_inference_cannot_pass_as_discovered_either(self):
        result = review(
            proposal(
                source=FactSource.BRAIN_INFERENCE, status=FactStatus.DISCOVERED, message_id=None
            )
        )
        assert reasons(result) == [ProposalRejection.STATUS_NOT_ALLOWED]

    def test_what_is_only_read_between_the_lines_is_never_confirmed(self):
        result = review(proposal(source=FactSource.CONVERSATION, status=FactStatus.CONFIRMED))
        assert reasons(result) == [ProposalRejection.STATUS_NOT_ALLOWED]

    def test_a_statement_is_not_a_hypothesis(self):
        result = review(proposal(status=FactStatus.HYPOTHESIS))
        assert reasons(result) == [ProposalRejection.STATUS_NOT_ALLOWED]

    @pytest.mark.parametrize(
        "source", [FactSource.CLIENT_DATA, FactSource.SYSTEM, FactSource.HUMAN]
    )
    def test_a_brain_cannot_claim_a_source_it_is_not(self, source):
        result = review(proposal(source=source, status=FactStatus.CONFIRMED))
        assert reasons(result) == [ProposalRejection.SOURCE_NOT_ALLOWED]

    def test_confirming_a_prospects_statement_needs_the_message_it_is_in(self):
        assert reasons(review(proposal(message_id=None))) == [ProposalRejection.MESSAGE_REQUIRED]

    def test_a_message_we_sent_is_not_the_prospect_speaking(self):
        result = review(proposal(message_id=MessageId("out-1")))
        assert reasons(result) == [ProposalRejection.NOT_A_PROSPECT_MESSAGE]

    def test_a_message_that_is_not_in_this_conversation_is_refused(self):
        assert reasons(review(proposal(message_id=MessageId("elsewhere")))) == [
            ProposalRejection.UNKNOWN_MESSAGE
        ]

    def test_an_invented_excerpt_is_refused(self):
        result = review(proposal(excerpt="I will pay tomorrow"))
        assert reasons(result) == [ProposalRejection.EXCERPT_NOT_IN_MESSAGE]

    def test_keys_the_core_writes_itself_are_off_limits(self):
        result = review(proposal(key=f"{CORE_FACT_KEY_PREFIX}anything"))
        assert reasons(result) == [ProposalRejection.RESERVED_KEY]

    def test_oversized_keys_and_values_are_refused(self):
        assert reasons(review(proposal(key="k" * 129))) == [ProposalRejection.MALFORMED]
        assert reasons(review(proposal(value="v" * 2001))) == [ProposalRejection.MALFORMED]


class TestNothingAuthoritativeIsOverwritten:
    def authoritative(self, source, value="small", status=FactStatus.CONFIRMED):
        return Fact(
            key="plan",
            value=value,
            status=status,
            evidence=FactEvidence(source=source),
            recorded_at=NOW - timedelta(days=1),
        )

    @pytest.mark.parametrize(
        "source", [FactSource.HUMAN, FactSource.SYSTEM, FactSource.CLIENT_DATA]
    )
    def test_a_different_value_does_not_displace_a_confirmed_fact_from_a_trusted_source(
        self, source
    ):
        result = review(proposal(), existing=[self.authoritative(source)])
        assert not result.accepted
        assert reasons(result) == [ProposalRejection.CONFLICTS_WITH_AUTHORITATIVE_FACT]

    def test_a_hypothesis_does_not_either(self):
        result = review(
            proposal(
                source=FactSource.BRAIN_INFERENCE, status=FactStatus.HYPOTHESIS, message_id=None
            ),
            existing=[self.authoritative(FactSource.HUMAN)],
        )
        assert reasons(result) == [ProposalRejection.CONFLICTS_WITH_AUTHORITATIVE_FACT]

    def test_the_same_value_adds_evidence_without_disagreeing(self):
        result = review(proposal(), existing=[self.authoritative(FactSource.HUMAN, value="big")])
        assert len(result.accepted) == 1

    def test_the_prospect_may_update_what_the_prospect_said_before(self):
        earlier = self.authoritative(FactSource.PROSPECT)
        assert len(review(proposal(), existing=[earlier]).accepted) == 1

    def test_an_unconfirmed_fact_is_not_authoritative(self):
        weak = self.authoritative(FactSource.HUMAN, status=FactStatus.DISCOVERED)
        assert len(review(proposal(), existing=[weak]).accepted) == 1

    def test_only_the_current_observation_of_a_key_counts(self):
        older = self.authoritative(FactSource.HUMAN)
        newer = replace(
            self.authoritative(FactSource.PROSPECT, value="medium"),
            recorded_at=NOW - timedelta(hours=1),
        )
        assert len(review(proposal(), existing=[older, newer]).accepted) == 1

    def test_reviewing_never_changes_what_is_already_recorded(self):
        existing = [
            self.authoritative(FactSource.PROSPECT, value="x", status=FactStatus.HYPOTHESIS)
        ]
        snapshot = list(existing)
        result = review(
            proposal(
                source=FactSource.BRAIN_INFERENCE,
                status=FactStatus.HYPOTHESIS,
                message_id=None,
                value="x",
            ),
            existing=existing,
        )
        assert existing == snapshot
        assert [f.status for f in result.accepted] == [FactStatus.HYPOTHESIS]  # no promotion


class TestWrongClient:
    def test_a_prospect_of_another_client_gets_nothing_accepted(self):
        stranger = make_prospect(client_id=ClientId("other"))
        result = review(proposal(), prospect=stranger)
        assert not result.accepted
        assert reasons(result) == [ProposalRejection.WRONG_CLIENT]

    def test_a_conversation_of_another_prospect_gets_nothing_accepted(self):
        elsewhere = make_conversation(prospect_id="someone-else", messages=())
        assert reasons(review(proposal(), conversation=elsewhere)) == [
            ProposalRejection.WRONG_CLIENT
        ]

    def test_a_message_of_another_client_cannot_be_cited(self):
        foreign = make_message(message_id=MessageId("in-9"), client_id=ClientId("other"))
        # such a message can never be part of this client's conversation, so it is simply unknown
        assert reasons(review(proposal(message_id=foreign.message_id))) == [
            ProposalRejection.UNKNOWN_MESSAGE
        ]
