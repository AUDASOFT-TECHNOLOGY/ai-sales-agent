"""Slice 6c (payment model) in the Brain kit: what a product offers for payment, what the agent may
say, what it asks for after a payment is claimed, and what it can never do: confirm a payment.

Generic and invented: "test" URLs and accounts, no real client, provider or place.
"""

from copy import deepcopy
from dataclasses import replace

import pytest
from brain_support import NOW, answer, handoff, make_brain, payload_of, request, turns
from commerce_support import (
    ACCOUNT_NUMBER,
    ONGOING,
    PAY_URL,
    at_payment,
    claimed,
    commerce_doc,
    interested,
    paid,
    proof_sent,
)
from factories import make_message

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError, build_context
from sales_brain_kit.consultative.output import response_schema
from sales_brain_kit.spec import SettingsInvalidError
from sales_core.contracts import (
    PAYMENT_CLAIMED_KEY,
    PAYMENT_CONFIRMED_KEY,
    PAYMENT_PROOF_KEY,
    ClientId,
    DecisionType,
    Direction,
    FactSource,
    FactStatus,
    MessageId,
    PaymentProgress,
    SalesActionType,
    payment_progress,
)
from sales_core.facts import review_proposals


def say(**overrides):
    """A valid model answer in the language the prospect's market requires."""
    return answer(**({"language": "fr"} | overrides))


ONLINE = {"enabled": True, "methods": ["online-card"]}
OFFLINE = {"enabled": True, "methods": ["bank-transfer"], "receipt_required": True}


def with_payment(online=None, offline=None, *, doc=None):
    """The commerce settings whose product `alpha` declares its payment options."""
    doc = deepcopy(doc or commerce_doc())
    doc["catalog"]["products"][0]["payment"] = {"online": online or {}, "offline": offline or {}}
    return doc


def decide(doc, *answers, facts=None, **options):
    brain, llm = make_brain(doc, *answers)
    decision = brain.decide(
        request(facts=facts if facts is not None else at_payment(), **{**ONGOING, **options})
    )
    return decision, llm


def refused(doc, *answers, facts=None, **options) -> str:
    scripted = (*answers, *([answers[-1]] if len(answers) == 1 else []))
    brain, _ = make_brain(doc, *scripted)
    with pytest.raises(BrainOutputError) as error:
        brain.decide(
            request(facts=facts if facts is not None else at_payment(), **{**ONGOING, **options})
        )
    return str(error.value)


def offered(doc, facts=None):
    ctx = build_context(
        request(facts=facts if facts is not None else at_payment(), **ONGOING), parse_settings(doc)
    )
    return [o.method.id for o in ctx.plan.workflow.methods]


class TestOnlinePayment:
    def test_when_online_is_available_the_exact_configured_url_is_what_the_prospect_gets(self):
        doc = with_payment(online=ONLINE, offline=OFFLINE)
        decision, _ = decide(doc, say(message="Pay here: [[payment:online-card]]"))
        assert PAY_URL in decision.message.text
        assert decision.message.text.count(PAY_URL) == 1

    def test_when_online_is_disabled_no_online_method_is_on_offer(self):
        doc = with_payment(online={"enabled": False}, offline=OFFLINE)
        assert offered(doc) == ["bank-transfer"]

    def test_when_online_is_disabled_its_placeholder_cannot_be_used(self):
        doc = with_payment(online={"enabled": False}, offline=OFFLINE)
        message = refused(doc, say(message="Pay here: [[payment:online-card]]"))
        assert "[[payment:online-card]] is not available now" in message

    def test_when_online_is_disabled_no_url_can_be_sent_not_even_the_configured_one_written_by_hand(
        self,
    ):
        doc = with_payment(online={"enabled": False}, offline=OFFLINE)
        assert "not a configured link" in refused(doc, say(message=f"Pay here: {PAY_URL}"))

    def test_when_online_is_disabled_the_model_is_never_told_about_it(self):
        doc = with_payment(online={"enabled": False}, offline=OFFLINE)
        _, llm = decide(doc, say(message="[[payment:bank-transfer]]"))
        payload = payload_of(llm)
        assert [m["id"] for m in payload["workflow"]["payment_methods"]] == ["bank-transfer"]
        assert PAY_URL not in str(payload)
        available = [v["placeholder"] for v in payload["exact_values"]["available"]]
        assert "[[payment:online-card]]" not in available

    def test_no_default_or_invented_url_appears_when_no_online_method_is_configured(self):
        doc = with_payment(offline=OFFLINE)
        decision, _ = decide(doc, say(message="[[payment:bank-transfer]]"))
        assert "http" not in decision.message.text

    def test_a_product_that_enables_nothing_offers_no_payment_and_falls_back(self):
        doc = with_payment()
        assert offered(doc) == []
        ctx = build_context(request(facts=at_payment(), **ONGOING), parse_settings(doc))
        assert ctx.plan.workflow.forced == {DecisionType.HANDOFF_TO_HUMAN}
        assert "no payment method applies" in ctx.plan.workflow.reason

    def test_a_product_without_a_declaration_keeps_letting_the_methods_scopes_decide(self):
        assert offered(commerce_doc()) == ["online-card", "bank-transfer"]

    def test_a_product_can_offer_a_subset_of_the_methods(self):
        doc = with_payment(online=ONLINE, offline={"enabled": True, "methods": []})
        assert offered(doc) == ["online-card", "bank-transfer"]
        assert offered(with_payment(online=ONLINE)) == ["online-card"]


class TestOfflinePaymentAndTheReceipt:
    def test_the_exact_offline_instructions_are_inserted_unchanged(self):
        doc = with_payment(offline=OFFLINE)
        decision, _ = decide(doc, say(message="[[payment:bank-transfer]]", language="fr"))
        assert ACCOUNT_NUMBER in decision.message.text

    def test_after_a_claim_the_receipt_must_be_asked_for(self):
        doc = with_payment(offline=OFFLINE)
        message = refused(
            doc, say(message="Thanks, we will check.", analysis={"payment_claimed": True})
        )
        assert "requires a receipt" in message

    def test_asking_for_the_receipt_is_accepted(self):
        doc = with_payment(offline=OFFLINE)
        decision, _ = decide(
            doc,
            say(
                message="Thanks. Please send us your receipt so the team can verify the payment.",
                analysis={"payment_claimed": True},
                requests_receipt=True,
            ),
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE

    def test_a_claim_made_earlier_still_requires_the_receipt_to_be_requested(self):
        doc = with_payment(offline=OFFLINE)
        facts = [*at_payment(), claimed()]
        assert "requires a receipt" in refused(doc, say(message="Any news?"), facts=facts)
        decision, _ = decide(
            doc, say(message="Please send your receipt.", requests_receipt=True), facts=facts
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE

    def test_once_the_receipt_is_in_it_is_not_asked_for_again(self):
        doc = with_payment(offline=OFFLINE)
        facts = [*at_payment(), claimed(), proof_sent()]
        assert "no receipt is due" in refused(
            doc, say(message="Send it.", requests_receipt=True), facts=facts
        )
        decision, _ = decide(doc, say(message="Thanks, the team will verify it."), facts=facts)
        assert decision.decision_type is DecisionType.SEND_MESSAGE

    def test_a_receipt_is_not_asked_for_when_the_product_does_not_require_one(self):
        doc = with_payment(offline={"enabled": True, "methods": ["bank-transfer"]})
        decision, _ = decide(
            doc, say(message="Thanks, we will check.", analysis={"payment_claimed": True})
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE
        assert "no receipt is due" in refused(doc, say(message="Send it.", requests_receipt=True))

    def test_someone_who_paid_online_is_not_asked_for_a_receipt(self):
        doc = with_payment(online=ONLINE, offline=OFFLINE)
        decision, _ = decide(
            doc,
            say(
                message="Thanks, we will check.",
                analysis={"payment_claimed": True, "payment_method": "online-card"},
            ),
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE

    def test_when_the_method_is_unclear_the_receipt_is_asked_for(self):
        doc = with_payment(online=ONLINE, offline=OFFLINE)
        assert "requires a receipt" in refused(
            doc, say(message="Thanks.", analysis={"payment_claimed": True})
        )

    def test_the_method_the_prospect_names_must_be_one_on_offer(self):
        doc = with_payment(offline=OFFLINE)
        message = refused(doc, say(analysis={"payment_method": "online-card"}, message="ok"))
        assert "not a payment method on offer now" in message

    def test_the_receipt_rule_does_not_apply_to_a_handoff(self):
        doc = with_payment(offline=OFFLINE)
        decision, _ = decide(doc, handoff(analysis={"payment_claimed": True}))
        assert decision.decision_type is DecisionType.HANDOFF_TO_HUMAN

    def test_the_model_is_told_the_receipt_is_due_and_that_it_confirms_nothing(self):
        doc = with_payment(offline=OFFLINE)
        _, llm = decide(
            doc,
            say(message="Send your receipt.", requests_receipt=True),
            facts=[*at_payment(), claimed()],
        )
        payment = payload_of(llm)["workflow"]["payment"]
        assert payment == {
            "progress": "claimed",
            "receipt_due": True,
            "confirmed_by_the_client": False,
        }
        system = llm.calls[0].messages[0].content
        assert "never confirm that a payment was made or received" in system


def proposals_for(doc, *answers, messages=None, facts=None, action=SalesActionType.REPLY):
    brain, _ = make_brain(doc, *answers)
    decision = brain.decide(
        request(
            facts=facts if facts is not None else at_payment(),
            messages=messages or ONGOING["messages"],
            action=action,
        )
    )
    return decision


def receipt_message(kind="image", direction=Direction.INBOUND):
    media = make_message(
        message_id=MessageId("m-media"), direction=direction, text="", metadata={"type": kind}
    )
    return (*turns(("out", "Here is how to pay.")), media)


class TestClaimsAndProofsAreOnlyRecordedAsWhatTheyAre:
    doc = with_payment(offline=OFFLINE)

    def claim(self, **kw):
        return proposals_for(
            self.doc,
            say(
                message="Please send the receipt.",
                analysis={"payment_claimed": True},
                requests_receipt=True,
            ),
            **kw,
        )

    def test_a_prospect_saying_they_paid_becomes_a_claim_never_a_confirmation(self):
        (proposal,) = self.claim().proposed_facts
        assert proposal.key == PAYMENT_CLAIMED_KEY
        assert proposal.status is FactStatus.DISCOVERED and proposal.source is FactSource.PROSPECT
        assert proposal.message_id == "m-1"  # the prospect's own message, chosen by code

    def test_the_claim_survives_the_cores_review_as_discovered_and_the_prospect_is_not_paid(self):
        decision = self.claim()
        request_ = request(facts=at_payment(), **ONGOING)
        review = review_proposals(
            decision.proposed_facts,
            client_id=request_.client.client_id,
            prospect=request_.prospect,
            conversation=request_.conversation,
            existing=request_.facts,
            recorded_at=NOW,
            recorded_by="brain-proposal:a-1",
        )
        assert [f.key for f in review.accepted] == [PAYMENT_CLAIMED_KEY]
        assert not any(f.is_confirmed for f in review.accepted if f.key == PAYMENT_CLAIMED_KEY)
        assert payment_progress(review.accepted) is PaymentProgress.CLAIMED

    def test_a_claim_is_recorded_once(self):
        assert self.claim(facts=[*at_payment(), claimed()]).proposed_facts == ()

    def test_no_claim_is_recorded_when_the_prospect_did_not_say_they_paid(self):
        decision = proposals_for(self.doc, say(message="Here is how."))
        assert decision.proposed_facts == ()

    def test_only_a_reply_to_a_message_can_record_a_claim(self):
        decision = self.claim(action=SalesActionType.FOLLOW_UP)
        assert decision.proposed_facts == ()

    def test_a_receipt_arriving_at_the_payment_step_is_a_proof_never_a_confirmation(self):
        decision = proposals_for(
            self.doc,
            say(message="Thanks, the team will verify it."),
            messages=receipt_message("image"),
            facts=[*at_payment(), claimed()],
        )
        (proposal,) = decision.proposed_facts
        assert proposal.key == PAYMENT_PROOF_KEY and proposal.value == "image"
        assert proposal.status is FactStatus.DISCOVERED and proposal.message_id == "m-media"

    def test_a_document_is_a_possible_receipt_too(self):
        decision = proposals_for(
            self.doc, say(message="Thanks."), messages=receipt_message("document")
        )
        assert [p.key for p in decision.proposed_facts] == [PAYMENT_PROOF_KEY]

    @pytest.mark.parametrize("kind", ["audio", "text", "sticker"])
    def test_other_kinds_of_message_are_not_a_receipt(self, kind):
        decision = proposals_for(self.doc, say(message="Ok."), messages=receipt_message(kind))
        assert decision.proposed_facts == ()

    def test_a_photo_before_the_payment_step_is_not_a_receipt(self):
        decision = proposals_for(
            self.doc,
            answer(message="Nice."),
            messages=receipt_message("image"),
            facts=[interested()],
        )
        assert all(p.key != PAYMENT_PROOF_KEY for p in decision.proposed_facts)

    def test_the_same_receipt_is_not_recorded_twice(self):
        proof = proof_sent()
        again = replace(proof, evidence=replace(proof.evidence, message_id=MessageId("m-media")))
        decision = proposals_for(
            self.doc,
            say(message="Thanks."),
            messages=receipt_message("image"),
            facts=[*at_payment(), again],
        )
        assert decision.proposed_facts == ()

    def test_a_proof_plus_a_claim_is_still_not_a_payment(self):
        decision = proposals_for(
            self.doc,
            say(
                message="Thanks.",
                analysis={"payment_claimed": True},
            ),
            messages=receipt_message("image"),
        )
        keys = {p.key for p in decision.proposed_facts}
        assert keys <= {PAYMENT_CLAIMED_KEY, PAYMENT_PROOF_KEY}
        assert all(p.status is FactStatus.DISCOVERED for p in decision.proposed_facts)

    def test_the_agent_never_proposes_a_confirmation_whatever_the_model_says(self):
        decision = proposals_for(
            self.doc,
            say(message="Thanks, the team will verify it.", analysis={"payment_claimed": True}),
            messages=receipt_message("image"),
        )
        assert all(p.key != PAYMENT_CONFIRMED_KEY for p in decision.proposed_facts)
        assert all(p.status is not FactStatus.CONFIRMED for p in decision.proposed_facts)

    def test_once_a_person_has_confirmed_nothing_more_is_recorded_about_payment(self):
        decision = proposals_for(
            self.doc,
            closing_answer(),
            messages=receipt_message("image"),
            facts=[*at_payment(), paid()],
        )
        assert decision.proposed_facts == ()

    def test_the_model_has_no_field_that_can_confirm_a_payment(self):
        brain, llm = make_brain(self.doc, say(message="x", requests_receipt=True))
        brain.decide(request(facts=at_payment(), **ONGOING))
        schema = llm.calls[0].response_schema
        analysis = schema["properties"]["analysis"]["properties"]
        assert "payment_claimed" in analysis and "requests_receipt" in schema["properties"]
        assert not [
            k for k in (*schema["properties"], *analysis) if "confirm" in k or "paid" in k.lower()
        ]


def closing_answer(**overrides):
    from brain_support import closing

    return closing(**overrides)


class TestNoMessageCanPresentAPaymentAsConfirmed:
    """The model declares what its message says about the payment; `confirmed` needs a person's
    confirmation. Structured, not a search for words."""

    doc = with_payment(online=ONLINE, offline=OFFLINE)

    @pytest.mark.parametrize(
        "extra",
        [[], [claimed()], [claimed(), proof_sent()], [proof_sent()]],
        ids=["nothing", "claimed", "claimed+proof", "proof"],
    )
    def test_before_a_persons_confirmation_a_message_cannot_present_the_payment_as_confirmed(
        self, extra
    ):
        facts = [*at_payment(), *extra]
        message = refused(
            self.doc,
            say(
                message="Your payment is confirmed.",
                payment_statement="confirmed",
                requests_receipt=True,
            ),
            facts=facts,
        )
        assert "payment_statement: the payment is not confirmed" in message

    def test_saying_it_is_being_verified_is_fine(self):
        decision, _ = decide(
            self.doc,
            say(
                message="Thanks, the team is verifying it.",
                payment_statement="awaiting_verification",
            ),
            facts=[*at_payment(), claimed(), proof_sent()],
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE

    def test_asking_for_a_receipt_is_not_a_confirmation(self):
        decision, _ = decide(
            self.doc,
            say(
                message="Please send your receipt.",
                requests_receipt=True,
                analysis={"payment_claimed": True},
            ),
        )
        assert all(p.key != PAYMENT_CONFIRMED_KEY for p in decision.proposed_facts)
        assert payment_progress([]) is PaymentProgress.NONE

    def test_a_payment_claimed_online_stays_a_claim(self):
        decision, _ = decide(
            self.doc,
            say(
                message="Thanks, the team is verifying it.",
                payment_statement="awaiting_verification",
                analysis={"payment_claimed": True, "payment_method": "online-card"},
            ),
        )
        (proposal,) = decision.proposed_facts
        assert proposal.key == PAYMENT_CLAIMED_KEY and proposal.status is FactStatus.DISCOVERED
        assert not any(p.key == PAYMENT_CONFIRMED_KEY for p in decision.proposed_facts)

    def test_online_claim_cannot_be_presented_as_confirmed_either(self):
        message = refused(
            self.doc,
            say(
                message="Payment received, thank you!",
                payment_statement="confirmed",
                analysis={"payment_claimed": True, "payment_method": "online-card"},
            ),
        )
        assert "the payment is not confirmed" in message

    def test_only_a_message_can_say_something_about_the_payment(self):
        message = refused(
            self.doc,
            handoff(payment_statement="awaiting_verification", analysis={"payment_claimed": True}),
        )
        assert "only a message can say something about the payment" in message

    def test_the_model_can_only_choose_confirmed_once_a_person_has_confirmed(self):
        def statements(facts):
            ctx = build_context(request(facts=facts, **ONGOING), parse_settings(self.doc))
            return response_schema(ctx)["properties"]["payment_statement"]["enum"]

        assert statements(at_payment()) == ["none", "awaiting_verification"]
        assert statements([*at_payment(), claimed(), proof_sent()]) == [
            "none",
            "awaiting_verification",
        ]
        assert statements([*at_payment(), paid(FactSource.SYSTEM)]) == [
            "none",
            "awaiting_verification",
        ]
        assert statements([*at_payment(), paid()]) == ["none", "awaiting_verification", "confirmed"]

    def test_a_stage_that_marks_the_sale_won_needs_the_confirmation_too(self):
        facts = [*at_payment(), claimed(), proof_sent()]
        message = refused(self.doc, say(message="Thanks.", stage="won"), facts=facts)
        assert "marks the sale as won" in message
        decision, _ = decide(self.doc, closing_answer(stage="won"), facts=[*at_payment(), paid()])
        assert decision.new_stage == "won"


class TestNothingButAPersonConfirms:
    def test_a_confirmation_from_a_person_completes_the_payment_and_ends_at_the_closing_step(self):
        doc = with_payment(offline=OFFLINE)
        ctx = build_context(request(facts=[*at_payment(), paid()], **ONGOING), parse_settings(doc))
        assert ctx.plan.workflow.payment is PaymentProgress.PAID
        assert ctx.plan.workflow.forced == {DecisionType.CLOSE}

    @pytest.mark.parametrize(
        "source", [FactSource.SYSTEM, FactSource.PROSPECT, FactSource.CONVERSATION]
    )
    def test_the_same_fact_from_anyone_else_completes_nothing(self, source):
        doc = with_payment(offline=OFFLINE)
        ctx = build_context(
            request(facts=[*at_payment(), paid(source)], **ONGOING), parse_settings(doc)
        )
        assert ctx.plan.workflow.payment is not PaymentProgress.PAID
        assert ctx.plan.workflow.forced != {DecisionType.CLOSE}

    def test_a_claim_plus_a_proof_plus_a_message_still_leave_the_confirmation_pending(self):
        doc = with_payment(offline=OFFLINE)
        ctx = build_context(
            request(facts=[*at_payment(), claimed(), proof_sent()], **ONGOING), parse_settings(doc)
        )
        assert ctx.plan.workflow.current.kind == "payment_confirmation"
        assert ctx.plan.workflow.payment is PaymentProgress.PROOF_RECEIVED


class TestConfigurationRules:
    def problems(self, doc):
        with pytest.raises(SettingsInvalidError) as error:
            parse_settings(doc)
        return (
            " | ".join(error.value.problems)
            if hasattr(error.value, "problems")
            else str(error.value)
        )

    def test_a_valid_declaration_is_accepted(self):
        parse_settings(with_payment(online=ONLINE, offline=OFFLINE))

    def test_online_enabled_needs_an_online_method_that_applies_to_the_product(self):
        doc = with_payment(online={"enabled": True})
        doc["payment"]["methods"][0]["applies_to"] = {"products": ["beta"]}
        assert "no active online payment method applies" in self.problems(doc)

    def test_a_listed_method_must_exist_and_be_of_the_right_kind(self):
        found = self.problems(
            with_payment(online={"enabled": True, "methods": ["ghost", "bank-transfer"]})
        )
        assert (
            "'ghost' is not a payment method" in found
            and "'bank-transfer' is not an online method" in found
        )

    def test_receipt_required_belongs_to_offline_payment(self):
        found = self.problems(with_payment(offline={"receipt_required": True}))
        assert "receipt_required" in found

    def test_a_payment_step_needs_a_confirmation_step_after_it(self):
        doc = commerce_doc()
        steps = doc["conversion"]["workflows"][0]["steps"]
        doc["conversion"]["workflows"][0]["steps"] = [
            s for s in steps if s["kind"] != "payment_confirmation"
        ]
        assert "needs a payment_confirmation step" in self.problems(doc)

    @pytest.mark.parametrize("kind", ["payment", "payment_confirmation"])
    def test_a_payment_step_cannot_choose_how_it_is_completed(self, kind):
        doc = commerce_doc()
        for step in doc["conversion"]["workflows"][0]["steps"]:
            if step["kind"] == kind:
                step["completes_when"] = {"facts": [{"key": "payment_status"}]}
        assert "has no 'completes_when'" in self.problems(doc)

    def test_a_provider_cannot_be_configured_to_confirm(self):
        doc = commerce_doc()
        doc["payment"]["methods"][0]["confirmation"] = {"mode": "provider"}
        assert "never by a provider" in self.problems(doc)


class TestClientsAreIsolated:
    def test_one_clients_payment_configuration_is_invisible_to_another(self):
        a = with_payment(online=ONLINE, offline=OFFLINE)
        b = with_payment(online={"enabled": False}, offline={"enabled": True, "methods": ["cash"]})
        b["catalog"]["products"][0]["payment"]["offline"]["methods"] = ["bank-transfer"]
        b["payment"]["methods"][1]["instructions"][0]["fields"] = [
            {"label": "Account", "value": "YY99 0000 1111 2222 3333"}
        ]
        assert offered(a) == ["online-card", "bank-transfer"]
        assert offered(b) == ["bank-transfer"]
        decision_b, llm_b = decide(b, say(message="[[payment:bank-transfer]]"))
        assert "YY99 0000 1111 2222 3333" in decision_b.message.text
        assert ACCOUNT_NUMBER not in decision_b.message.text and PAY_URL not in str(
            payload_of(llm_b)
        )
        decision_a, _ = decide(a, say(message="[[payment:bank-transfer]]"))
        assert ACCOUNT_NUMBER in decision_a.message.text
        assert "YY99 0000 1111 2222 3333" not in decision_a.message.text

    def test_the_proposals_belong_to_the_prospect_of_the_request_and_no_other_client(self):
        decision = proposals_for(
            with_payment(offline=OFFLINE),
            say(
                message="Please send the receipt.",
                analysis={"payment_claimed": True},
                requests_receipt=True,
            ),
        )
        request_ = request(facts=at_payment(), **ONGOING)
        review = review_proposals(
            decision.proposed_facts,
            client_id=ClientId("another-client"),
            prospect=request_.prospect,
            conversation=request_.conversation,
            existing=(),
            recorded_at=NOW,
            recorded_by="x",
        )
        assert review.accepted == ()  # the Core refuses them for a client that is not the request's
