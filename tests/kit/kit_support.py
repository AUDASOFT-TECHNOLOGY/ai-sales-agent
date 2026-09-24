"""Generic settings documents for the kit's tests. No real client: everything here is invented."""

from copy import deepcopy
from typing import Any

from sales_brain_kit import ConsultativeSalesSpec

SPEC = ConsultativeSalesSpec()


def minimal() -> dict[str, Any]:
    """The smallest valid document: a business name and its languages."""
    return {
        "business": {"name": "Acme Academy"},
        "communication": {"languages": {"default": "en", "supported": ["en"]}},
    }


def with_product(**overrides: Any) -> dict[str, Any]:
    doc = minimal()
    product = {"id": "course-a", "name": "Course A", "summary": "A course."}
    product.update(overrides)
    doc["catalog"] = {"products": [product]}
    return doc


def full() -> dict[str, Any]:
    """A document that uses every section, with three products of very different richness."""
    return {
        "business": {
            "name": "Acme Academy",
            "summary": {"en": "Training for professionals", "fr": "Formation pour professionnels"},
            "website": "https://example.org",
        },
        "communication": {
            "languages": {
                "default": "en",
                "supported": ["en", "fr", "ar"],
                "mirror_prospect_language": True,
            },
            "tone": {
                "style": "warm and concise",
                "formality": {"default": "formal", "allow_switch": False},
                "persona_name": "Sam",
            },
            "principles": ["Answer the question first", "Never invent prices or dates"],
            "message": {"max_chars": 500, "max_questions": 1, "emoji": "none"},
        },
        "objectives": {
            "goals": [
                {"id": "enrol", "label": "Enrolment", "kind": "conversion", "default_cta": "enrol_link"},
                {"id": "callback", "label": "Advisor callback", "kind": "meeting",
                 "default_cta": "request_callback"},
            ],
            "primary": "enrol",
        },
        "stages": [
            {"id": "new", "label": "New"},
            {"id": "qualifying", "label": "Qualifying", "milestone": "engaged"},
            {"id": "won", "label": "Won", "milestone": "won"},
        ],
        "catalog": {
            "source_interest": {"columns": ["Product", "Produit"], "state": "probable"},
            "presentation": {"max_products_per_message": 2, "style": "shortlist"},
            "products": [
                {
                    "id": "leadership-essentials",
                    "status": "active",
                    "name": {"en": "Leadership Essentials", "fr": "Leadership Essentiel"},
                    "summary": "Core skills for leading a team.",
                    "description": "A practical programme on communication, delegation and motivation.",
                    "category": "leadership",
                    "url": "https://example.org/leadership",
                    "aliases": ["Leadership"],
                    "tags": ["management", "team"],
                    "availability": {"regions": "all", "delivery": ["online", "onsite"],
                                     "next_start": "2026-10-05"},
                    "pricing": {"model": "fixed", "amount": 450, "currency": "TND",
                                "disclose": "on_request"},
                    "audience": {"suited_for": ["managers"], "not_for": ["students"]},
                    "value": {
                        "proposition": "Lead with confidence",
                        "benefits": ["Practical tools"],
                        "proof_points": [{"text": "Small groups", "verified": True}],
                    },
                    "sales_arguments": [
                        {"id": "practical", "text": "Hands-on exercises", "use_when": {"role": ["manager"]}}
                    ],
                    "objections": [
                        {"code": "price", "examples": ["too expensive"], "response": "Frame the value.",
                         "max_rebuttals": 1, "then": "follow_up"}
                    ],
                    "qualification": {
                        "fit": [{"id": "leads-people", "description": "Leads people", "fact": "role"}],
                        "disqualifiers": [
                            {"id": "student", "description": "No work context", "fact": "role",
                             "effect": "not_a_fit"}
                        ],
                    },
                    "discovery": {
                        "extra_fields": [{"key": "team_size", "importance": "optional"}],
                        "budget": {"max_questions_per_conversation": 3},
                    },
                    "strategy": {
                        "cta": {"kind": "enrol_link", "label": "Reserve a seat",
                                "url": "https://example.org/leadership/enrol"},
                        "angle": "confidence",
                    },
                    "follow_up": {"situations": {"no_response": {
                        "steps": [{"after": "P3D", "intent": "add_value"}], "then": "park"}}},
                    "appointment": {"supported": True, "kind": "call", "min_notice_hours": 24},
                    "restrictions": {"do_not_claim": ["guaranteed promotion"],
                                     "requires_human_for": ["discounts"], "languages": ["en", "fr"]},
                },
                {"id": "sales-management", "name": "Sales Management",
                 "summary": "For leads who run a sales team."},
                {"id": "digital-marketing", "status": "coming_soon", "name": "Digital Marketing",
                 "summary": "Online marketing basics.",
                 "strategy": {"cta": {"kind": "notify_me"}}},
            ],
        },
        "discovery": {
            "fields": [
                {"key": "goal", "importance": "critical",
                 "changes": ["product_selection", "recommendation_angle"],
                 "ask_hints": {"en": ["What would you most like to improve?"],
                               "fr": ["Qu'aimeriez-vous améliorer ?"]}},
                {"key": "role", "importance": "useful", "changes": ["qualification"],
                 "source_columns": ["Job"]},
                {"key": "sector", "importance": "optional"},
                {"key": "country", "importance": "optional", "changes": ["practical"],
                 "source_columns": ["Country"]},
                {"key": "budget", "importance": "optional", "sensitive": True},
            ],
            "sufficiency": {
                "recommend": {"any_of": ["goal", "interest"], "accept": "probable"},
                "propose_next_step": {"all_of": ["interest"], "accept": "confirmed"},
                "qualify": {"any_of": ["role", "goal"]},
            },
            "budget": {"max_questions_per_message": 1, "max_consecutive_questions": 2,
                       "max_questions_per_conversation": 5, "give_before_ask": True},
            "shortcuts": [
                {"when": {"interest": "confirmed"}, "do": "propose_next_step"},
                {"when": {"intent": "price_request"}, "do": "answer_then_continue"},
            ],
            "hypotheses": {"min_confidence_to_use": 0.6},
        },
        "conversation": {
            "answer_first": True,
            "cta": {"kind": "request_info"},
            "objections": [{"code": "not-now", "response": "Offer to follow up later."}],
            "intents": {"price_request": "answer_then_continue", "human_requested": "handoff",
                        "complaint": "handoff", "off_topic": "polite_redirect"},
            "media_handling": {"voice_note": "ask_for_text", "image": "acknowledge"},
        },
        "follow_up": {
            "situations": {
                "no_response": {"steps": [{"after": "P2D"}, {"after": "P5D"}, {"after": "P10D"}],
                                "then": "park"},
                "after_cta": {"steps": [{"after": "P1D"}], "then": "park"},
                "callback_promised": {"use": "prospect_preference"},
                "appointment_reminder": {"steps": [{"before": "P1D"}, {"before": "PT2H"}]},
            },
            "limits": {"max_touches": 4, "min_gap": "P1D"},
            "stop_conditions": ["explicit_no"],
        },
        "incoming": {
            "goals": ["understand_need", "present_options"],
            "allowed_topics": ["catalog", "pricing"],
            "out_of_scope": {"reply": "polite_redirect", "then": "handoff"},
            "no_product": "overview_then_one_question",
        },
        "handoff": {
            "triggers": [{"intent": "human_requested"}, {"after_rebuttals": 2},
                         {"disqualified": "student"}],
            "message": {"en": "A colleague will contact you.", "fr": "Un collègue vous contactera."},
        },
        "adaptation": {
            "dimensions": {
                "language": {"inputs": ["language", "locale"]},
                "register": {"inputs": ["role"]},
                "examples": {"inputs": ["sector", "role", "goal"]},
                "practical": {"inputs": ["country"], "covers": ["currency", "availability"]},
            },
            "packs": [{"id": "owners", "applies_when": {"role": ["business_owner"]},
                       "examples": ["Running a business while leading a team"]}],
            "extra_protected_attributes": ["income_level"],
        },
        "templates": [{"id": "follow-up-en", "purpose": "follow_up", "language": "en",
                       "provider_template_id": "follow_up_v1", "params": ["first_name"]}],
    }  # fmt: skip


def problems(document: dict[str, Any]) -> list[str]:
    return SPEC.validate(document, 1)


def edited(base: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of `base` with the value at a dotted path set (integers index into lists).
    A value of `...` deletes the key."""
    document = deepcopy(base)
    parts = path.split(".")
    node: Any = document
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if value is ...:
        del node[int(last) if isinstance(node, list) else last]
    elif isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return document
