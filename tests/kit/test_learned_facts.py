"""Slice 6d: the generic Brain proposes the facts the prospect's messages really contain.

The model reports (`learned_facts`); the code decides the key, the source, the status and the
anchor; the Core validates again and is the only one that records. Everything here is invented and
client-neutral.
"""

import re
from copy import deepcopy
from pathlib import Path

import pytest
from brain_support import (
    NOW,
    answer,
    fact,
    make_brain,
    payload_of,
    request,
    set_at,
    settings_doc,
    turns,
)
from commerce_support import ONGOING, at_payment, commerce_doc
from factories import make_message

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError, build_context
from sales_brain_kit.consultative.output import MAX_LEARNED_FACTS, response_schema
from sales_core.contracts import (
    CORE_FACT_KEY_PREFIX,
    PAYMENT_CLAIMED_KEY,
    PAYMENT_CONFIRMED_KEY,
    PAYMENT_PROOF_KEY,
    Direction,
    FactProposal,
    FactSource,
    FactStatus,
    MessageId,
    SalesActionType,
)
from sales_core.facts import ProposalRejection, review_proposals

REPLY = SalesActionType.REPLY
SAYS_COUNTRY = turns(
    ("out", "Hello! How can I help?"), ("in", "I live in Country A and I lead a team")
)


def learned(key="country", value="Country A", basis="stated", excerpt="I live in Country A", **kw):
    return {"key": key, "value": value, "basis": basis, "excerpt": excerpt, "confidence": None} | kw


def guess(key="sector", value="training", confidence=0.9):
    return learned(key, value, "guess", None, confidence=confidence)


def decide(*reports, doc=None, facts=(), messages=SAYS_COUNTRY, action=REPLY, **overrides):
    """The decision for one reply over one scripted answer reporting `reports`."""
    brain, _ = make_brain(doc, answer(learned_facts=list(reports), **overrides))
    return brain.decide(request(action=action, messages=messages, facts=facts))


def refusal(*reports, doc=None, facts=(), messages=SAYS_COUNTRY, action=REPLY) -> str:
    scripted = answer(learned_facts=list(reports))
    brain, _ = make_brain(doc, scripted, scripted)  # told what was wrong, the model repeats itself
    with pytest.raises(BrainOutputError) as error:
        brain.decide(request(action=action, messages=messages, facts=facts))
    return str(error.value)


def core_review(decision, *, facts=(), messages=SAYS_COUNTRY, action=REPLY):
    request_ = request(action=action, messages=messages, facts=facts)
    return review_proposals(
        decision.proposed_facts,
        client_id=request_.client.client_id,
        prospect=request_.prospect,
        conversation=request_.conversation,
        existing=request_.facts,
        recorded_at=NOW,
        recorded_by="brain-proposal:a-1",
    )


class TestWhatTheProspectStatesBecomesAProposal:
    def test_an_explicit_statement_is_a_proposal_with_its_source_status_and_evidence(self):
        (proposal,) = decide(learned()).proposed_facts
        assert (proposal.key, proposal.value) == ("country", "Country A")
        assert proposal.source is FactSource.PROSPECT
        assert proposal.status is FactStatus.CONFIRMED
        assert proposal.message_id == "m-1"  # the prospect's message, chosen by the code
        assert proposal.excerpt == "I live in Country A"
        assert proposal.confidence is None

    def test_the_core_accepts_it_and_dates_it_from_the_message(self):
        review = core_review(decide(learned()))
        (recorded,) = review.accepted
        assert recorded.status is FactStatus.CONFIRMED and not review.rejected
        assert recorded.evidence.source is FactSource.PROSPECT
        assert recorded.evidence.message_id == "m-1"
        assert recorded.evidence.recorded_by == "brain-proposal:a-1"

    def test_several_facts_from_one_message(self):
        decision = decide(
            learned(), learned("role", "manager", excerpt="I lead a team", basis="implied")
        )
        by_key = {p.key: p for p in decision.proposed_facts}
        assert by_key["country"].status is FactStatus.CONFIRMED
        assert by_key["role"].status is FactStatus.DISCOVERED
        assert by_key["role"].source is FactSource.CONVERSATION
        assert not core_review(decision).rejected

    def test_words_match_ignoring_case_and_spacing_and_stay_as_quoted(self):
        decision = decide(learned(excerpt="i  LIVE in   country a"))
        (proposal,) = decision.proposed_facts
        assert proposal.excerpt == "i LIVE in country a"
        assert not core_review(decision).rejected

    def test_the_fact_is_anchored_to_the_message_that_contains_the_words(self):
        messages = turns(
            ("out", "Hello"), ("in", "Hi there"), ("in", "I live in Country A"), ("in", "Thanks")
        )
        (proposal,) = decide(learned(), messages=messages).proposed_facts
        assert proposal.message_id == "m-2"

    def test_interest_names_a_catalog_product_and_is_recorded_by_its_id(self):
        messages = turns(("out", "Hello"), ("in", "The Cours Alpha sounds right"))
        report = learned("interest", "Alpha", excerpt="The Cours Alpha sounds right")
        (proposal,) = decide(report, messages=messages).proposed_facts
        assert (proposal.key, proposal.value) == ("interest", "alpha")

    def test_a_field_another_client_declared_is_learned_the_same_way(self):
        doc = set_at(settings_doc(), "discovery.fields.0.key", "city")
        doc["discovery"]["sufficiency"] = {}
        decision = decide(learned("city", "Town B", excerpt="Country A"), doc=doc)
        assert [p.key for p in decision.proposed_facts] == ["city"]


class TestNothingIsInvented:
    def test_a_vague_message_produces_no_fact(self):
        messages = turns(("out", "Hello"), ("in", "I am interested"))
        assert decide(messages=messages).proposed_facts == ()

    def test_words_that_are_not_in_the_prospects_messages_are_refused(self):
        messages = turns(("out", "Hello"), ("in", "I am interested"))
        error = refusal(learned("budget", "5000", excerpt="my budget is 5000"), messages=messages)
        assert "not in what the prospect wrote" in error

    def test_words_only_we_wrote_do_not_count(self):
        messages = turns(("out", "Do you live in Country A?"), ("in", "Yes"))
        assert "not in what the prospect wrote" in refusal(learned(), messages=messages)

    def test_a_stated_fact_without_words_is_refused(self):
        assert "exact words" in refusal(learned(excerpt=None))
        assert "exact words" in refusal(learned(excerpt="  ", basis="implied"))

    def test_interest_without_saying_in_what_does_not_name_a_product(self):
        messages = turns(("out", "Hello"), ("in", "I am interested"))
        report = learned("interest", "a course", excerpt="I am interested")
        assert "is not a product of the catalog" in refusal(report, messages=messages)

    def test_a_key_nobody_declared_is_refused(self):
        assert "is not a declared field" in refusal(learned("favourite_colour", "blue"))

    def test_a_blank_value_is_refused(self):
        assert "is empty" in refusal(learned(value="  "))

    def test_two_values_for_one_key_are_refused(self):
        assert "two different values" in refusal(learned(), learned(value="Country B"))

    def test_the_same_report_twice_counts_once(self):
        assert len(decide(learned(), learned()).proposed_facts) == 1

    def test_nothing_is_learned_when_there_is_no_message_to_reply_to(self):
        error = refusal(learned(), action=SalesActionType.FOLLOW_UP)
        assert "no message from the prospect" in error

    def test_nothing_is_learned_from_what_we_wrote_last(self):
        messages = turns(("in", "I live in Country A"), ("out", "Great!"))
        assert "no message from the prospect" in refusal(learned(), messages=messages)

    def test_a_reply_without_reports_proposes_nothing(self):
        assert decide().proposed_facts == ()


class TestTheModelNeverDecidesWhatIsTrue:
    def test_a_guess_is_a_hypothesis_from_an_inference_and_never_confirmed(self):
        decision = decide(guess(confidence=0.7))
        (proposal,) = decision.proposed_facts
        assert proposal.status is FactStatus.HYPOTHESIS
        assert proposal.source is FactSource.BRAIN_INFERENCE
        assert proposal.confidence == 0.7
        assert proposal.excerpt is None and proposal.message_id == "m-1"
        assert not core_review(decision).rejected

    def test_the_status_and_source_come_from_the_basis_alone(self):
        expected = {
            "stated": (FactStatus.CONFIRMED, FactSource.PROSPECT),
            "implied": (FactStatus.DISCOVERED, FactSource.CONVERSATION),
            "guess": (FactStatus.HYPOTHESIS, FactSource.BRAIN_INFERENCE),
        }
        for basis, (status, source) in expected.items():
            report = learned("sector", "training", basis, confidence=0.9)
            (proposal,) = decide(report).proposed_facts
            assert (proposal.status, proposal.source) == (status, source)

    def test_the_model_cannot_supply_a_status_or_a_source(self):
        report = learned() | {"status": "confirmed", "source": "human"}
        assert "learned_facts" in refusal(report)

    def test_a_guess_needs_a_confidence(self):
        assert "needs a confidence" in refusal(guess(confidence=None))

    def test_a_guess_too_weak_to_be_used_is_dropped(self):
        assert decide(guess(confidence=0.1)).proposed_facts == ()

    def test_a_sensitive_field_is_never_guessed(self):
        assert "may not be guessed" in refusal(guess("budget", "5000"))

    def test_a_field_the_client_forbids_guessing_is_never_guessed(self):
        # commerce settings: a country that drives markets is never guessed
        assert "may not be guessed" in refusal(guess("country", "Country A"), doc=commerce_doc())

    def test_a_guess_does_not_compete_with_what_is_known(self):
        known = [fact("sector", "finance", FactStatus.DISCOVERED)]
        assert decide(guess(), facts=known).proposed_facts == ()

    def test_a_hypothesis_is_not_promoted_by_being_proposed_again(self):
        older = [fact("sector", "training", FactStatus.HYPOTHESIS, confidence=0.9)]
        assert decide(guess(), facts=older).proposed_facts == ()

    def test_the_prospect_stating_what_was_only_guessed_is_new_information(self):
        older = [fact("country", "Country A", FactStatus.HYPOTHESIS, confidence=0.9)]
        (proposal,) = decide(learned(), facts=older).proposed_facts
        assert proposal.status is FactStatus.CONFIRMED


class TestTheCoreStaysTheOnlyAuthority:
    @pytest.mark.parametrize(
        "key", [PAYMENT_CONFIRMED_KEY, f"{CORE_FACT_KEY_PREFIX}anything", PAYMENT_CLAIMED_KEY]
    )
    def test_the_model_cannot_report_a_key_the_core_or_a_payment_claim_owns(self, key):
        assert "not yours to report" in refusal(learned(key, "confirmed"))

    def test_the_core_refuses_a_brain_proposal_of_a_core_key_even_if_one_got_through(self):
        forged = FactProposal(
            key=PAYMENT_CONFIRMED_KEY,
            value="confirmed",
            status=FactStatus.CONFIRMED,
            source=FactSource.PROSPECT,
            message_id=MessageId("m-1"),
        )
        request_ = request(action=REPLY, messages=SAYS_COUNTRY)
        review = review_proposals(
            (forged,),
            client_id=request_.client.client_id,
            prospect=request_.prospect,
            conversation=request_.conversation,
            existing=(),
            recorded_at=NOW,
            recorded_by="brain-proposal:a-1",
        )
        assert review.accepted == ()
        assert [r.reason for r in review.rejected] == [ProposalRejection.RESERVED_KEY]

    def test_a_client_cannot_make_a_core_key_learnable(self):
        doc = deepcopy(settings_doc())
        doc["discovery"]["fields"].append({"key": "core_payment_confirmed"})
        context = build_context(request(action=REPLY, messages=SAYS_COUNTRY), parse_settings(doc))
        item = response_schema(context)["properties"]["learned_facts"]["items"]
        assert "core_payment_confirmed" not in item["properties"]["key"]["enum"]

    def test_too_many_reports_are_refused_and_the_total_stays_under_the_core_limit(self):
        many = [learned(value=f"Country {i}") for i in range(MAX_LEARNED_FACTS + 1)]
        assert "learned_facts" in refusal(*many)
        assert MAX_LEARNED_FACTS + 2 <= 20  # plus a claim and a proof


class TestWhatIsAlreadyKnownIsNotProposedAgain:
    def test_the_same_value_already_recorded_is_not_repeated(self):
        known = [fact("country", "country a", FactStatus.CONFIRMED)]
        assert decide(learned(), facts=known).proposed_facts == ()

    def test_a_firmer_statement_of_a_discovered_value_is_still_new_evidence(self):
        known = [fact("country", "Country A", FactStatus.DISCOVERED)]
        (proposal,) = decide(learned(), facts=known).proposed_facts
        assert proposal.status is FactStatus.CONFIRMED

    def test_a_weaker_statement_of_a_confirmed_value_adds_nothing(self):
        known = [fact("country", "Country A", FactStatus.CONFIRMED)]
        assert decide(learned(basis="implied"), facts=known).proposed_facts == ()

    def test_a_different_value_is_a_new_observation(self):
        known = [fact("country", "Country B", FactStatus.CONFIRMED)]
        (proposal,) = decide(learned(), facts=known).proposed_facts
        assert proposal.value == "Country A"

    def test_a_value_the_leads_own_data_holds_is_not_repeated_unless_the_prospect_states_it(self):
        attributes = {"Country": "Country A"}

        def reply(basis):
            brain, _ = make_brain(None, answer(learned_facts=[learned(basis=basis)]))
            request_ = request(action=REPLY, messages=SAYS_COUNTRY, attributes=attributes)
            return brain.decide(request_).proposed_facts

        assert reply("implied") == ()
        (proposal,) = reply("stated")  # the prospect confirming what the client's data says
        assert proposal.status is FactStatus.CONFIRMED

    def test_an_interest_already_held_is_not_repeated(self):
        messages = turns(("out", "Hello"), ("in", "I want Alpha"))
        interest = learned("interest", "alpha", excerpt="I want Alpha")
        presenting = {"selected_product": "alpha", "presented_products": ["alpha"]}
        held = [fact("interest", "Alpha", FactStatus.CONFIRMED)]
        assert decide(interest, messages=messages, facts=held, **presenting).proposed_facts == ()
        other = [fact("interest", "beta", FactStatus.CONFIRMED)]
        presenting = {"selected_product": "beta", "presented_products": ["beta"]}
        decision = decide(interest, messages=messages, facts=other, **presenting)
        assert [p.value for p in decision.proposed_facts] == ["alpha"]


class TestPaymentFactsStillComeFromTheAnalysisNotFromFreeStatements:
    def test_a_prospect_saying_they_paid_is_a_discovered_claim_never_a_confirmation(self):
        brain, _ = make_brain(
            commerce_doc(), answer(language="fr", analysis={"payment_claimed": True})
        )
        decision = brain.decide(request(facts=at_payment(), **ONGOING))
        (claim,) = decision.proposed_facts
        assert claim.key == PAYMENT_CLAIMED_KEY and claim.status is FactStatus.DISCOVERED
        assert claim.source is FactSource.PROSPECT and claim.message_id == "m-1"
        assert all(p.key != PAYMENT_CONFIRMED_KEY for p in decision.proposed_facts)

    def test_a_receipt_at_the_payment_step_is_a_discovered_proof_never_a_confirmation(self):
        media = make_message(
            message_id=MessageId("m-media"),
            direction=Direction.INBOUND,
            text="",
            metadata={"type": "image"},
        )
        brain, _ = make_brain(commerce_doc(), answer(language="fr"))
        decision = brain.decide(
            request(
                action=REPLY,
                messages=(*turns(("out", "Here is how to pay.")), media),
                facts=at_payment(),
            )
        )
        (proof,) = decision.proposed_facts
        assert proof.key == PAYMENT_PROOF_KEY and proof.status is FactStatus.DISCOVERED
        assert proof.message_id == "m-media"

    def test_a_reply_can_carry_a_learned_fact_and_a_claim_together(self):
        messages = turns(("out", "Hello"), ("in", "I am a manager and I paid"))
        brain, _ = make_brain(
            commerce_doc(),
            answer(
                language="fr",
                analysis={"payment_claimed": True},
                learned_facts=[learned("role", "manager", excerpt="I am a manager")],
            ),
        )
        decision = brain.decide(request(action=REPLY, messages=messages, facts=at_payment()))
        by_key = {p.key: p for p in decision.proposed_facts}
        assert set(by_key) == {"role", PAYMENT_CLAIMED_KEY}
        assert by_key[PAYMENT_CLAIMED_KEY].status is FactStatus.DISCOVERED


class TestWhatTheModelIsAsked:
    def context(self):
        return build_context(
            request(action=REPLY, messages=SAYS_COUNTRY), parse_settings(settings_doc())
        )

    def test_the_schema_only_offers_declared_keys_and_the_three_bases(self):
        item = response_schema(self.context())["properties"]["learned_facts"]["items"]
        keys = item["properties"]["key"]["enum"]
        assert "country" in keys and "interest" in keys and "core_payment_confirmed" not in keys
        assert item["properties"]["basis"]["enum"] == ["stated", "implied", "guess"]
        assert set(item["required"]) == set(item["properties"])
        assert "status" not in item["properties"] and "source" not in item["properties"]

    def test_the_context_lists_what_can_be_reported_and_what_cannot_be_guessed(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request(action=REPLY, messages=SAYS_COUNTRY))
        listed = {e["key"]: e for e in payload_of(llm)["learnable_facts"]}
        assert listed["country"]["may_guess"] is True
        assert listed["budget"]["may_guess"] is False and listed["budget"]["sensitive"] is True
        assert "interest" in listed

    def test_nothing_is_offered_when_there_is_nothing_to_learn_from(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request(action=SalesActionType.FIRST_CONTACT))
        assert "learnable_facts" not in payload_of(llm)

    def test_the_instructions_say_the_model_reports_and_the_system_decides(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request(action=REPLY, messages=SAYS_COUNTRY))
        system = llm.calls[0].messages[0].content
        assert "Learning" in system and "the system decides what is recorded" in system

    def test_the_metadata_names_what_was_proposed_for_the_audit_trail(self):
        assert decide(learned()).metadata["learned_facts"] == "country"


class TestNoClientLogicInTheKit:
    def test_the_generic_kit_names_no_client_market_or_country(self):
        kit = Path(__file__).resolve().parents[2] / "src" / "sales_brain_kit"
        forbidden = re.compile(r"naga|tounesimmo|tunis|alg[eé]ri|maroc|morocco", re.IGNORECASE)
        offenders = [
            path.name
            for path in kit.rglob("*.py")
            if forbidden.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []
