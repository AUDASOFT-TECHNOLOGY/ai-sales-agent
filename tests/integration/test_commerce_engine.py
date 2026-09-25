"""Slice 6b through the real Core: a payment instruction, a price and a handoff decided by the
workflow travel the ordinary Engine path (approval, safety, persistence, sending) with no change to
the Core, and the exact configured text is what is stored and sent."""

import json
import sys
from pathlib import Path

from engine_harness import build, inbound, prospect
from factories import NOW

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kit"))

from brain_support import answer, handoff
from commerce_support import ACCOUNT_NAME, ACCOUNT_NUMBER, PAY_URL, commerce_doc

from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    CONFIRM_PAYMENTS,
    ActionStatus,
    ConfirmingActor,
    Fact,
    FactStatus,
    PaymentConfirmation,
    ProspectId,
    ProspectStatus,
)
from sales_core.payments import PaymentConfirmationService
from sales_integrations.fakes import FakeLLMProvider


def world(*answers, doc=None):
    llm = FakeLLMProvider([json.dumps(a) if isinstance(a, dict) else a for a in answers])
    brain = ConsultativeSalesBrain(llm, parse_settings(doc or commerce_doc()))
    h = build(brain=brain, mode=ApprovalMode.APPROVAL, prospects=[prospect("p1")])
    h.facts = h.repo  # the fake repository is also the FactsReader, as the Sheets adapter is
    h.facts_writer = h.repo
    h.engine = h.new_engine()
    h.llm = llm
    return h


def record(h, **facts):
    h.repo.append_facts(
        ProspectId("p1"),
        [
            Fact(key=key, value=value, status=FactStatus.CONFIRMED, recorded_at=NOW)
            for key, value in facts.items()
        ],
    )


def confirm_as_a_person(h):
    """What the client's CRM does once someone checked the payment: the Core records it."""
    receipt = PaymentConfirmationService(
        repository=h.repo, facts=h.repo, facts_writer=h.repo, clock=h.clock
    ).confirm(
        PaymentConfirmation(
            client_id=h.repo.client_id,
            prospect_id=ProspectId("p1"),
            confirmation_id="c-1",
            actor=ConfirmingActor(actor_id="user-1", permissions={CONFIRM_PAYMENTS}),
            confirmed_at=NOW,
        )
    )
    assert receipt.accepted


def registered_in_market_a(h):
    record(
        h,
        interest="alpha",
        country="Country A",
        registration_status="registered",
        full_name="Alex",
    )


class TestPaymentThroughTheEngine:
    def test_the_exact_instructions_are_what_is_stored_for_approval_and_what_is_sent(self):
        h = world(answer(message="Here is how to pay:\n[[payment:bank-transfer]]", language="fr"))
        registered_in_market_a(h)
        h.engine.process_inbound(h.engine.receive_inbound(inbound("I am ready to pay")))

        action = h.action("p1")
        expected = (
            "Here is how to pay:\nVirement vers :\n"
            f"Titulaire: {ACCOUNT_NAME}\nCompte: {ACCOUNT_NUMBER}"
        )
        assert action.status is ActionStatus.PROPOSED and action.draft.text == expected
        assert action.draft.language == "fr" and h.channel.attempts == ()

        h.approve("p1")
        h.engine.dispatch()
        assert h.status("p1") is ActionStatus.SENT
        assert h.channel.sent[0].draft.text == expected  # nothing altered on the way out

    def test_an_online_payment_link_arrives_intact(self):
        h = world(answer(message="[[payment:online-card]]", language="fr"))
        registered_in_market_a(h)
        h.engine.process_inbound(h.engine.receive_inbound(inbound("Let us pay by card")))
        assert h.action("p1").draft.text == f"Pay securely here:\n{PAY_URL}"

    def test_the_price_in_the_draft_is_the_one_configured_for_the_prospects_country(self):
        h = world(answer(message="It is [[price:alpha]].", language="fr", selected_product="alpha"))
        registered_in_market_a(h)
        h.engine.process_inbound(h.engine.receive_inbound(inbound("How much is it?")))
        assert h.action("p1").draft.text == "It is 85.5+ EUR."

    def test_a_model_that_invents_payment_details_produces_no_draft_and_sends_nothing(self):
        invented = answer(message="Send it to XX12 3456 7890 1234 5678", language="fr")
        h = world(invented, invented)
        registered_in_market_a(h)
        report = h.engine.process_inbound(h.engine.receive_inbound(inbound("I am ready to pay")))
        assert h.channel.attempts == ()
        assert not report.ok and "account-like number" in report.errors[0]
        action = h.action("p1")
        assert action.status is ActionStatus.PLANNED and action.draft is None  # retried later
        assert action.attempts == 1 and "account-like number" in action.last_error

    def test_the_core_is_unchanged_it_stores_the_same_draft_shape_as_for_any_message(self):
        h = world(answer(message="Hello", language="fr"))
        registered_in_market_a(h)
        h.engine.process_inbound(h.engine.receive_inbound(inbound("Hi")))
        assert type(h.action("p1").draft).__name__ == "MessageDraft"


class TestWorkflowDecisionsThroughTheEngine:
    def test_a_step_that_requires_a_person_pauses_the_prospect_and_sends_nothing(self):
        doc = commerce_doc()
        doc["conversion"]["workflows"][0]["steps"][2]["human"] = "required"
        h = world(handoff(analysis={"intent": None}), doc=doc)
        registered_in_market_a(h)
        h.engine.process_inbound(h.engine.receive_inbound(inbound("Ready to pay")))
        assert h.prospect("p1").status is ProspectStatus.PAUSED and h.channel.attempts == ()

    def test_the_closing_step_closes_the_prospect(self):
        from brain_support import closing

        h = world(closing())
        registered_in_market_a(h)
        confirm_as_a_person(h)
        h.engine.process_inbound(h.engine.receive_inbound(inbound("Thank you")))
        assert h.prospect("p1").status is ProspectStatus.CLOSED

    def test_a_first_contact_without_any_workflow_facts_is_unchanged(self):
        h = world(answer(message="Bonjour", language="en"))
        h.engine.prepare()
        assert h.status("p1") is ActionStatus.PROPOSED
