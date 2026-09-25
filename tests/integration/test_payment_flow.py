"""The payment workflow end to end, through the real Engine, the real Brain kit and the Core:

    the prospect says they paid  -> a claim (discovered)
    the prospect sends a receipt -> a proof (discovered)
    a person at the client checks and confirms -> a confirmation (confirmed, human) -> paid

Nothing before the last step makes the prospect paid, and nothing but the last step can.
"""

import json
import sys
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from engine_harness import build, inbound, prospect
from factories import NOW
from sheets_support import make_repository

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kit"))

from brain_support import answer, closing
from commerce_support import commerce_doc

from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    CONFIRM_PAYMENTS,
    PAYMENT_CLAIMED_KEY,
    PAYMENT_CONFIRMED_KEY,
    PAYMENT_PROOF_KEY,
    ConfirmingActor,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    PaymentConfirmation,
    PaymentConfirmationStatus,
    PaymentProgress,
    ProspectId,
    ProspectStatus,
    payment_progress,
)
from sales_core.payments import PaymentConfirmationService
from sales_integrations.fakes import FakeClock, FakeLLMProvider

P1 = ProspectId("p1")
ASK = "Merci. Envoyez-nous votre recu pour verification."


def doc_with_receipts():
    doc = deepcopy(commerce_doc())
    doc["catalog"]["products"][0]["payment"] = {
        "online": {"enabled": False},
        "offline": {"enabled": True, "methods": ["bank-transfer"], "receipt_required": True},
    }
    return doc


def fr(**kw):
    return answer(**({"language": "fr"} | kw))


def ask_for_the_receipt():
    return fr(message=ASK, analysis={"payment_claimed": True}, requests_receipt=True)


class Flow:
    def __init__(self, *answers):
        llm = FakeLLMProvider([json.dumps(a) for a in answers])
        brain = ConsultativeSalesBrain(llm, parse_settings(doc_with_receipts()))
        self.h = build(brain=brain, mode=ApprovalMode.APPROVAL, prospects=[prospect("p1")])
        self.h.facts = self.h.facts_writer = self.h.repo
        self.h.engine = self.h.new_engine()
        known = {
            "interest": "alpha",
            "country": "Country A",
            "registration_status": "registered",
            "full_name": "Alex",
        }
        self.h.repo.append_facts(
            P1,
            [
                Fact(key=k, value=v, status=FactStatus.CONFIRMED, recorded_at=NOW)
                for k, v in known.items()
            ],
        )
        self.service = PaymentConfirmationService(
            repository=self.h.repo, facts=self.h.repo, facts_writer=self.h.repo, clock=self.h.clock
        )
        self.n = 0

    def say(self, text="", **metadata):
        self.n += 1
        message = inbound(text, external_id=f"wamid.{self.n}", metadata=metadata or None)
        return self.h.engine.process_inbound(self.h.engine.receive_inbound(message))

    def facts(self):
        return list(self.h.repo.get_facts(P1))

    def progress(self):
        return payment_progress(self.facts())

    def payment_keys(self):
        return [f.key for f in self.facts() if f.key.startswith("payment")]

    def confirm(self, **kw):
        fields = {
            "client_id": self.h.repo.client_id,
            "prospect_id": P1,
            "confirmation_id": "c-1",
            "actor": ConfirmingActor(actor_id="ceo-1", permissions={CONFIRM_PAYMENTS}),
            "confirmed_at": NOW,
            "payment_reference": "bank-ref-1",
            "amount": Decimal("85.50"),
            "currency": "EUR",
        }
        return self.service.confirm(PaymentConfirmation(**(fields | kw)))


def test_a_claim_then_a_receipt_then_a_person_confirms():
    flow = Flow(
        ask_for_the_receipt(),
        fr(message="Merci, notre equipe va verifier le paiement."),
        closing(),
    )

    assert flow.say("J'ai paye par virement").ok
    assert flow.payment_keys() == [PAYMENT_CLAIMED_KEY]
    claim = flow.facts()[-1]
    assert claim.status is FactStatus.DISCOVERED and claim.evidence.source is FactSource.PROSPECT
    assert flow.progress() is PaymentProgress.CLAIMED
    assert flow.h.action("p1").draft.text == ASK  # the receipt was requested

    assert flow.say("", type="image", media_id="media-1").ok
    assert flow.payment_keys() == [PAYMENT_CLAIMED_KEY, PAYMENT_PROOF_KEY]
    proof = flow.facts()[-1]
    assert proof.evidence.message_id == "in:whatsapp:wamid.2"
    assert proof.status is FactStatus.DISCOVERED
    assert flow.progress() is PaymentProgress.PROOF_RECEIVED
    assert PAYMENT_CONFIRMED_KEY not in flow.payment_keys()

    receipt = flow.confirm(proof_message_id="in:whatsapp:wamid.2")
    assert receipt.status is PaymentConfirmationStatus.RECORDED
    confirmed = [f for f in flow.facts() if f.key == PAYMENT_CONFIRMED_KEY]
    assert confirmed[0].evidence.source is FactSource.HUMAN
    assert confirmed[0].evidence.recorded_by == "human:ceo-1|confirmation:c-1"
    assert flow.progress() is PaymentProgress.PAID

    flow.say("Merci")
    assert flow.h.prospect("p1").status is ProspectStatus.CLOSED  # only now does the workflow close


def test_without_a_person_the_conversation_never_reaches_the_end():
    flow = Flow(
        ask_for_the_receipt(),
        fr(message="Merci, notre equipe va verifier."),
        fr(message="Toujours en cours de verification."),
    )
    flow.say("J'ai paye")
    flow.say("", type="document")
    flow.say("Alors, c'est bon ?")
    assert flow.progress() is PaymentProgress.PROOF_RECEIVED
    assert flow.h.prospect("p1").status is not ProspectStatus.CLOSED
    assert PAYMENT_CONFIRMED_KEY not in flow.payment_keys()


def test_a_model_that_forgets_the_receipt_produces_no_draft_and_no_fact():
    silent = fr(message="Merci, on verifie.", analysis={"payment_claimed": True})
    flow = Flow(silent, silent)
    report = flow.say("J'ai paye")
    assert not report.ok and "requires a receipt" in report.errors[0]
    assert flow.payment_keys() == [] and flow.h.channel.attempts == ()


def test_the_confirmation_is_refused_without_the_permission_and_changes_nothing():
    flow = Flow(ask_for_the_receipt())
    flow.say("J'ai paye")
    denied = flow.confirm(actor=ConfirmingActor(actor_id="intern-1", permissions=set()))
    assert denied.status is PaymentConfirmationStatus.REJECTED
    assert denied.reason == "not_authorized"
    assert flow.progress() is PaymentProgress.CLAIMED


def test_confirming_twice_is_harmless_and_a_contradiction_is_refused():
    flow = Flow()
    assert flow.confirm().status is PaymentConfirmationStatus.RECORDED
    count = len(flow.facts())
    assert flow.confirm().status is PaymentConfirmationStatus.DUPLICATE
    assert flow.confirm(amount=Decimal(1)).reason == "conflicting_confirmation"
    assert len(flow.facts()) == count and flow.progress() is PaymentProgress.PAID


def test_a_confirmation_survives_the_sheets_round_trip_with_the_person_named():
    repo, api = make_repository()
    api.human_add_row(
        "Prospects", prospect_id="p1", display_name="Alex", phone="+15550000001", status="new"
    )
    service = PaymentConfirmationService(
        repository=repo, facts=repo, facts_writer=repo, clock=FakeClock(NOW)
    )
    confirmation = PaymentConfirmation(
        client_id=repo.client_id,
        prospect_id=P1,
        confirmation_id="c-9",
        actor=ConfirmingActor(actor_id="ceo-1", permissions={CONFIRM_PAYMENTS}),
        confirmed_at=NOW - timedelta(minutes=1),
        amount=Decimal("1200.500"),
        currency="XTS",
    )
    assert service.confirm(confirmation).accepted
    rows = len(api.rows("Facts"))
    facts = {f.key: f for f in repo.get_facts(P1)}
    confirmed = facts[PAYMENT_CONFIRMED_KEY]
    assert confirmed.is_confirmed and confirmed.evidence.source is FactSource.HUMAN
    assert confirmed.evidence.recorded_by == "human:ceo-1|confirmation:c-9"
    assert facts["core_payment_amount"].value == "1200.500"
    assert payment_progress(facts.values()) is PaymentProgress.PAID
    assert service.confirm(confirmation).status is PaymentConfirmationStatus.DUPLICATE
    assert len(api.rows("Facts")) == rows


@pytest.mark.parametrize("source", [FactSource.SYSTEM, FactSource.PROSPECT])
def test_the_same_fact_written_by_anyone_else_is_not_a_confirmation(source):
    flow = Flow()
    forged = Fact(
        key=PAYMENT_CONFIRMED_KEY,
        value="confirmed",
        status=FactStatus.CONFIRMED,
        evidence=FactEvidence(source=source),
        recorded_at=NOW,
    )
    flow.h.repo.append_facts(P1, [forged])
    assert flow.progress() is PaymentProgress.NONE
