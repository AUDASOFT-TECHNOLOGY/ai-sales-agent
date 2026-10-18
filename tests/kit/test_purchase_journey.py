"""The purchase journey the Client screen manages: a client who only configured a payment link and
switched "give my payment methods when a prospect is interested" on.

The workflow is read from the very file the web screen writes
(`web/src/client/offer/purchaseJourney.json`), so the two can never drift apart. What is proved: with
nothing but a product, an online method and this workflow, the agent hands the buyer the client's exact
link, the model never sees the link, and without the switch it does not.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from brain_support import CONFIRMED, REPLY, answer, fact, make_brain, request, settings_doc, turns

from sales_brain_kit import BrainOutputError

JOURNEY = json.loads(
    (Path(__file__).resolve().parents[2] / "web/src/client/offer/purchaseJourney.json").read_text(
        encoding="utf-8"
    )
)
LINK = "https://pay.example.org/checkout?ref=Zed-42&plan=B"
ONGOING = {
    "action": REPLY,
    "messages": turns(("out", "Hello from Acme Academy."), ("in", "Great, how do I pay?")),
}


def doc(*, journey: bool):
    document = deepcopy(settings_doc())
    document["payment"] = {
        "methods": [
            {
                "id": "carte",
                "label": "Card payment",
                "kind": "online",
                "instructions": [{"id": "main", "text": "Pay on our secure page:", "url": LINK}],
                "confirmation": {"mode": "manual"},
            }
        ]
    }
    if journey:
        document["conversion"] = {"default_workflow": JOURNEY["id"], "workflows": [JOURNEY]}
    return document


def interested():
    return [fact("interest", "alpha", CONFIRMED)]


def test_the_journey_file_is_a_valid_workflow_for_the_brain():
    brain, _ = make_brain(doc(journey=True), answer())
    assert [w.id for w in brain.settings.conversion.workflows] == [JOURNEY["id"]]


def test_an_interested_prospect_is_offered_the_payment_method_and_gets_the_exact_link():
    brain, llm = make_brain(
        doc(journey=True), answer(message="Here you go: [[payment:carte]]", language="en")
    )
    decision = brain.decide(request(facts=interested(), **ONGOING))

    # what the model was told: a placeholder, never the link
    everything = "\n".join(m.content for m in llm.calls[0].messages)
    assert "[[payment:carte]]" in everything and LINK not in everything
    # what the buyer gets: the client's exact words and link, character for character
    assert decision.message.text == f"Here you go: Pay on our secure page:\n{LINK}"


def test_without_the_switch_the_agent_cannot_hand_over_a_payment_method():
    refused = answer(message="Here you go: [[payment:carte]]")
    brain, llm = make_brain(
        doc(journey=False), refused, refused
    )  # the answer is refused, then again
    with pytest.raises(BrainOutputError) as error:
        brain.decide(request(facts=interested(), **ONGOING))
    assert "payment" in str(error.value)
    sent = [m.content for m in llm.calls[0].messages]
    assert LINK not in "".join(sent)
