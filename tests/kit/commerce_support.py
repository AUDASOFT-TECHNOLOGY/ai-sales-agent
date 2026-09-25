"""Settings with markets, prices per place, payment methods and a conversion workflow, for the
generic tests. Everything is invented and client-neutral: "Country A" is not a place, `XX00 ...`
is not an account, `pay.example.org` is not a payment page."""

from copy import deepcopy
from typing import Any

from brain_support import (
    CONFIRMED,
    DISCOVERED,
    REPLY,
    answer,
    fact,
    make_brain,
    payload_of,
    request,
    settings_doc,
    turns,
)

from sales_core.contracts import (
    PAYMENT_CLAIMED_KEY,
    PAYMENT_CONFIRMED_KEY,
    PAYMENT_PROOF_KEY,
    FactSource,
)

ONGOING = {
    "action": REPLY,
    "messages": turns(("out", "Hello from Acme Academy."), ("in", "I am ready to go ahead")),
}

# Exact values, written the awkward way on purpose: spacing, case and leading zeros must survive.
ACCOUNT_NAME = "Acme  Test SARL"
ACCOUNT_NUMBER = "XX00 0000 0000 0000 1234"
OTHER_ACCOUNT_NUMBER = "YY99 9999 8888 7777 0001"
PAY_URL = "https://pay.example.org/checkout?ref=Acme-01&plan=A"


def interested(product: str = "alpha", status=CONFIRMED):
    return fact("interest", product, status)


def known(key: str, value: str, status=CONFIRMED, source: FactSource | None = FactSource.PROSPECT):
    return fact(key, value, status, source=source)


def country(value: str = "Country A", status=CONFIRMED, source=FactSource.PROSPECT):
    return known("country", value, status, source)


def commerce_doc() -> dict[str, Any]:
    """The generic settings plus two markets, place-specific prices, payment methods for several
    places and currencies, and a workflow from registration to a confirmed payment."""
    doc = deepcopy(settings_doc())
    fields = doc["discovery"]["fields"]
    for field in fields:
        if field["key"] == "country":
            field["infer"] = {"allowed": False}  # a country that drives markets is never guessed
    fields += [
        {"key": "full_name", "importance": "useful", "changes": ["next_action"]},
        {"key": "registration_status", "importance": "optional"},
        {"key": "payment_status", "importance": "optional"},
    ]
    doc["markets"] = {
        "markets": [
            {
                "id": "market-a",
                "label": "Market A",
                "countries": ["Country A", "CA"],
                "languages": {"allowed": ["fr", "en"], "default": "fr"},
                "style": "warm and direct",
            },
            {
                "id": "market-b",
                "countries": ["Country B"],
                "languages": {"allowed": ["ar"], "default": "ar"},
            },
        ]
    }
    doc["communication"]["register_rules"] = [
        {
            "id": "a-in-fr",
            "when": {"markets": ["market-a"], "languages": ["fr"]},
            "formality": "informal",
            "allow_switch": True,
            "style": "friendly",
        },
        {"id": "b-any", "when": {"markets": ["market-b"]}, "formality": "formal"},
    ]
    alpha = doc["catalog"]["products"][0]
    alpha["pricing"]["variants"] = [
        {
            "id": "market-a-price",
            "applies_to": {"markets": ["market-a"]},
            "model": "fixed",
            "amount": 90,
            "currency": "EUR",
        },
        {
            "id": "country-a-price",
            "applies_to": {"countries": ["Country A"]},
            "model": "from",
            "amount": 85.5,
            "currency": "EUR",
        },
        {
            "id": "market-b-price",
            "applies_to": {"markets": ["market-b"]},
            "model": "range",
            "amount": 1000,
            "max_amount": 1250.75,
            "currency": "GBP",
            "disclose": "always",
        },
    ]
    doc["payment"] = {
        "methods": [
            {
                "id": "online-card",
                "kind": "online",
                "label": {"en": "Card payment", "fr": "Paiement par carte"},
                "provider": "Test Provider",
                "applies_to": {"currencies": ["EUR", "USD"]},
                "confirmation": {"mode": "manual", "note": "A person confirms the payment."},
                "instructions": [
                    {"id": "default", "text": "Pay securely here:", "url": PAY_URL},
                ],
                "metadata": {"reference": "card-01"},
            },
            {
                "id": "bank-transfer",
                "kind": "offline",
                "label": {"en": "Bank transfer", "fr": "Virement bancaire"},
                "confirmation": {"mode": "manual", "note": "A person confirms the transfer."},
                "instructions": [
                    {
                        "id": "default",
                        "text": {"en": "Transfer to:", "fr": "Virement vers :"},
                        "fields": [
                            {
                                "label": {"en": "Account name", "fr": "Titulaire"},
                                "value": ACCOUNT_NAME,
                            },
                            {"label": {"en": "Account", "fr": "Compte"}, "value": ACCOUNT_NUMBER},
                        ],
                    },
                    {
                        "id": "for-market-b",
                        "applies_to": {"markets": ["market-b"]},
                        "text": "Transfer to the other account:",
                        "fields": [{"label": "Account", "value": OTHER_ACCOUNT_NUMBER}],
                    },
                ],
            },
            {
                "id": "cash",
                "kind": "offline",
                "label": "Cash at the office",
                "applies_to": {"countries": ["Country B"]},
                "instructions": [{"id": "default", "text": "Bring the amount to the office."}],
            },
        ]
    }
    doc["conversion"] = {
        "default_workflow": "sale",
        "workflows": [
            {
                "id": "sale",
                "steps": [
                    {"id": "recommend", "kind": "recommend"},
                    {
                        "id": "register",
                        "kind": "register",
                        "registration": {"mode": "agent_assisted", "collects": ["full_name"]},
                        "completes_when": {
                            "facts": [{"key": "registration_status", "equals": ["registered"]}]
                        },
                        "objective": "Collect what registration needs.",
                    },
                    {
                        "id": "pay",
                        "kind": "payment",
                        "requires": {"steps": ["register"]},
                    },
                    {
                        "id": "confirm",
                        "kind": "payment_confirmation",
                    },
                    {"id": "done", "kind": "close"},
                ],
            }
        ],
    }
    return doc


def registered() -> list:
    """Facts of a prospect who chose alpha and finished registering."""
    return [interested(), known("registration_status", "registered"), known("full_name", "Alex")]


def claimed():
    """The prospect said they paid: discovered, from the prospect. Nothing verified."""
    return fact(PAYMENT_CLAIMED_KEY, "claimed", DISCOVERED, source=FactSource.PROSPECT)


def proof_sent():
    """The prospect sent something that may be a receipt."""
    return fact(PAYMENT_PROOF_KEY, "image", DISCOVERED, source=FactSource.PROSPECT)


def paid(source: FactSource = FactSource.HUMAN):
    """What the Core records when a person authorised by the client confirms a payment."""
    return fact(PAYMENT_CONFIRMED_KEY, "confirmed", CONFIRMED, source=source)


def at_payment(place: str = "Country A") -> list:
    return [*registered(), country(place)]


def brain_answers(doc, *answers, **request_options):
    """(decision, llm) for one request over scripted answers."""
    brain, llm = make_brain(doc, *answers)
    return brain.decide(request(**{**ONGOING, **request_options})), llm


__all__ = [
    "ACCOUNT_NAME",
    "ACCOUNT_NUMBER",
    "CONFIRMED",
    "DISCOVERED",
    "ONGOING",
    "OTHER_ACCOUNT_NUMBER",
    "PAY_URL",
    "answer",
    "at_payment",
    "brain_answers",
    "claimed",
    "commerce_doc",
    "country",
    "fact",
    "interested",
    "known",
    "make_brain",
    "paid",
    "payload_of",
    "proof_sent",
    "registered",
    "request",
]
