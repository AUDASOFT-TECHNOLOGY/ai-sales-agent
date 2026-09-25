"""Scaffolding for the consultative Brain tests. Everything here is invented and client-neutral."""

import json
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from factories import (
    NOW,
    make_action,
    make_client_context,
    make_conversation,
    make_message,
    make_prospect,
)

from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_brain_kit.consultative import CommercialContext, build_context
from sales_core.contracts import (
    BrainDecision,
    BrainRequest,
    ContactOrigin,
    Direction,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    LLMResponse,
    Message,
    MessageId,
    SalesActionType,
)
from sales_integrations.fakes import FakeLLMProvider

FIRST = SalesActionType.FIRST_CONTACT
FOLLOW_UP = SalesActionType.FOLLOW_UP
REPLY = SalesActionType.REPLY
CALLBACK = SalesActionType.CALLBACK
REMINDER = SalesActionType.REMINDER

CONFIRMED = FactStatus.CONFIRMED
DISCOVERED = FactStatus.DISCOVERED
HYPOTHESIS = FactStatus.HYPOTHESIS


# ---- settings -----------------------------------------------------------------------------------


def _products() -> list[dict[str, Any]]:
    return [
        {
            "id": "alpha",
            "name": {"en": "Alpha Course", "fr": "Cours Alpha"},
            "summary": "A practical course.",
            "description": "Hands-on sessions.",
            "aliases": ["Alpha"],
            "url": "https://example.org/alpha",
            "pricing": {
                "model": "fixed",
                "amount": 100,
                "currency": "USD",
                "disclose": "after_interest",
            },
            "value": {
                "benefits": ["Practical tools"],
                "proof_points": [
                    {"text": "Small groups", "verified": True},
                    {"text": "Rated the best", "verified": False},
                ],
            },
            "sales_arguments": [
                {"id": "for-managers", "text": "Built for managers", "use_when": {"role": ["manager"]}},
                {"id": "general", "text": "Fits any schedule"},
            ],
            "objections": [
                {
                    "code": "price",
                    "examples": ["too expensive"],
                    "response": "Frame the value.",
                    "max_rebuttals": 1,
                    "then": "follow_up",
                }
            ],
            "qualification": {
                "fit": [{"id": "has-role", "description": "Has a role", "fact": "role"}],
                "disqualifiers": [
                    {"id": "student", "description": "Only a student", "fact": "role",
                     "effect": "not_a_fit"},
                    {"id": "ineligible", "description": "Not eligible", "fact": "eligibility",
                     "effect": "handoff"},
                ],
            },
            "strategy": {"cta": {"kind": "request_info"}},
        },
        {"id": "beta", "name": "Beta Course", "summary": "Another course.", "aliases": ["Beta"]},
        {
            "id": "gamma",
            "status": "coming_soon",
            "name": "Gamma Course",
            "summary": "Coming later.",
            "strategy": {"cta": {"kind": "notify_me"}},
        },
        {"id": "delta", "status": "inactive", "name": "Delta Course", "summary": "Retired."},
    ]  # fmt: skip


def settings_doc() -> dict[str, Any]:
    """A generic client: four products, discovery fields, intents, cadence and adaptation packs."""
    return {
        "business": {"name": "Acme Academy", "summary": "Training for professionals"},
        "communication": {
            "languages": {"default": "en", "supported": ["en", "fr", "ar"]},
            "tone": {"style": "warm and concise", "persona_name": "Sam"},
            "principles": ["Answer the question first"],
            "message": {"max_chars": 400, "max_questions": 1, "emoji": "none"},
        },
        "objectives": {
            "goals": [{"id": "enrol", "label": "Enrolment", "default_cta": "request_info"}],
            "primary": "enrol",
        },
        "stages": [
            {"id": "new", "label": "New"},
            {"id": "qualifying", "label": "Qualifying", "milestone": "engaged"},
            {"id": "won", "label": "Won", "milestone": "won"},
        ],
        "catalog": {
            "source_interest": {"columns": ["Product"], "state": "probable"},
            "presentation": {"max_products_per_message": 2, "style": "shortlist"},
            "products": _products(),
        },
        "discovery": {
            "fields": [
                {"key": "goal", "importance": "critical",
                 "changes": ["product_selection", "recommendation_angle"],
                 "ask_hints": {"en": ["What would you like to improve?"],
                               "fr": ["Qu'aimeriez-vous améliorer ?"]}},
                {"key": "role", "importance": "useful", "changes": ["qualification"],
                 "source_columns": ["Job"]},
                {"key": "sector", "importance": "optional"},
                {"key": "country", "importance": "optional", "changes": ["practical"],
                 "source_columns": ["Country"]},
                {"key": "eligibility", "importance": "useful", "changes": ["qualification"]},
                {"key": "budget", "importance": "optional", "sensitive": True},
            ],
            "sufficiency": {
                "recommend": {"any_of": ["goal", "interest"], "accept": "probable"},
                "propose_next_step": {"all_of": ["interest"], "accept": "confirmed"},
            },
        },
        "conversation": {
            "cta": {"kind": "request_info"},
            "objections": [{"code": "not-now", "response": "Offer to follow up later."}],
            "intents": {
                "price_request": "answer_then_continue",
                "human_requested": "handoff",
                "stop_contact": "honour_and_close",
                "spam": "ignore",
                "off_topic": "polite_redirect",
            },
        },
        "follow_up": {
            "situations": {
                "no_response": {"steps": [{"after": "P2D"}, {"after": "P5D"}], "then": "park"},
                "after_cta": {"steps": [{"after": "P1D"}], "then": "park"},
                "callback_promised": {"use": "prospect_preference"},
                "appointment_reminder": {"steps": [{"before": "P1D"}, {"before": "PT2H"}]},
            },
        },
        "handoff": {
            "triggers": [{"intent": "human_requested"}],
            "message": {"en": "A colleague will contact you.", "fr": "Un collègue vous contactera."},
        },
        "adaptation": {
            "dimensions": {
                "register": {"inputs": ["role"]},
                "practical": {"inputs": ["country"], "covers": ["currency"]},
            },
            "packs": [
                {"id": "managers", "applies_when": {"role": ["manager"]},
                 "examples": ["Leading a team"], "vocabulary": ["team lead"]}
            ],
        },
    }  # fmt: skip


def doc_with(**sections: Any) -> dict[str, Any]:
    """The generic settings with whole top-level sections replaced."""
    doc = settings_doc()
    doc.update(sections)
    return doc


def without_catalog() -> dict[str, Any]:
    doc = settings_doc()
    doc["catalog"] = {}
    return doc


def with_products(*ids: str) -> dict[str, Any]:
    """The generic settings keeping only the named products."""
    doc = settings_doc()
    doc["catalog"]["products"] = [p for p in doc["catalog"]["products"] if p["id"] in ids]
    return doc


def set_at(doc: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of `doc` with the value at a dotted path set (integers index into lists)."""
    copy = deepcopy(doc)
    node: Any = copy
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node.setdefault(part, {})
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return copy


# ---- requests -------------------------------------------------------------------------------------


def fact(
    key: str,
    value: str,
    status: FactStatus = DISCOVERED,
    *,
    confidence: float | None = None,
    source: FactSource | None = None,
    age: timedelta | None = None,
) -> Fact:
    return Fact(
        key=key,
        value=value,
        status=status,
        confidence=confidence,
        evidence=FactEvidence(source=source) if source else None,
        recorded_at=NOW - age if age is not None else NOW - timedelta(days=1),
    )


def turns(*items: tuple[str, str]) -> tuple[Message, ...]:
    """A conversation from ("in" | "out", text) pairs, oldest first, one hour apart."""
    return tuple(
        make_message(
            message_id=MessageId(f"m-{index}"),
            direction=Direction.INBOUND if side == "in" else Direction.OUTBOUND,
            text=text,
            timestamp=NOW - timedelta(hours=len(items) - index),
        )
        for index, (side, text) in enumerate(items)
    )


def request(
    *,
    action: SalesActionType = FIRST,
    facts: Iterable[Fact] = (),
    messages: Sequence[Message] = (),
    origin: ContactOrigin = ContactOrigin.UNKNOWN,
    attributes: Mapping[str, str] | None = None,
    locale: str | None = None,
    now: datetime = NOW,
) -> BrainRequest:
    return BrainRequest(
        client=make_client_context(),
        prospect=make_prospect(attributes=dict(attributes or {}), locale=locale),
        conversation=make_conversation(messages=tuple(messages), origin=origin),
        action=make_action(action_type=action),
        now=now,
        facts=tuple(facts),
    )


def context_for(doc: dict[str, Any] | None = None, **request_options: Any) -> CommercialContext:
    return build_context(request(**request_options), parse_settings(doc or settings_doc()))


# ---- what the model says ------------------------------------------------------------------------


def answer(**overrides: Any) -> dict[str, Any]:
    """A complete, valid model answer (a plain message), with `overrides` applied.

    `analysis` and `follow_up` are merged into their defaults rather than replaced.
    """
    base: dict[str, Any] = {
        "decision": "send_message",
        "message": "Hello! Glad to help.",
        "language": "en",
        "analysis": {
            "intent": None,
            "objection": None,
            "prospect_language": None,
            "requested_language": None,
            "explicit_refusal": False,
            "out_of_scope": False,
            "stop_signals": [],
            "disqualified": [],
            "payment_claimed": False,
            "payment_method": None,
        },
        "product_candidates": [],
        "presented_products": [],
        "selected_product": None,
        "discovery_key": None,
        "follow_up": {"situation": None, "requested_at": None},
        "stage": None,
        "handoff_reason": None,
        "handoff_briefing": None,
        "close_reason": None,
        "requests_receipt": False,
        "payment_statement": "none",
        "rationale": "test",
    }
    for section in ("analysis", "follow_up"):
        if section in overrides:
            base[section] = {**base[section], **overrides.pop(section)}
    return {**base, **overrides}


def handoff(**overrides: Any) -> dict[str, Any]:
    defaults = {
        "decision": "handoff_to_human",
        "message": None,
        "language": None,
        "handoff_reason": "needs a person",
        "handoff_briefing": "Asked for a person.",
    }
    return answer(**{**defaults, **overrides})


def closing(**overrides: Any) -> dict[str, Any]:
    defaults = {
        "decision": "close",
        "message": None,
        "language": None,
        "close_reason": "not interested",
    }
    return answer(**{**defaults, **overrides})


def make_brain(
    doc: dict[str, Any] | None = None, *answers: Any, **options: Any
) -> tuple[ConsultativeSalesBrain, FakeLLMProvider]:
    """A Brain over scripted model answers (dicts are sent as JSON, strings as they are)."""
    scripted = [json.dumps(a) if isinstance(a, dict) else a for a in answers]
    llm = FakeLLMProvider(scripted)
    return ConsultativeSalesBrain(llm, parse_settings(doc or settings_doc()), **options), llm


def decide(
    *answers: Any, doc: dict[str, Any] | None = None, **request_options: Any
) -> BrainDecision:
    brain, _ = make_brain(doc, *answers)
    return brain.decide(request(**request_options))


def payload_of(llm: FakeLLMProvider, call: int = 0) -> dict[str, Any]:
    """The structured context the model was given in its `call`-th request."""
    user = llm.calls[call].messages[1].content
    text = user.split("CONTEXT (JSON):\n", 1)[1].rsplit("\n\nReply with", 1)[0]
    return json.loads(text)


def parsed_response(**overrides: Any) -> LLMResponse:
    data = answer(**overrides)
    return LLMResponse(text="", parsed=data)
