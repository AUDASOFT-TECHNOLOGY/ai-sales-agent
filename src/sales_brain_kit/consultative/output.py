"""The structured answer the model must give, its JSON Schema, and how it is parsed.

This is the Brain Kit's own model, richer than a `BrainDecision`: besides the decision it carries
what the model understood (intent, objection, language evidence) and what it chose (products,
the question it asks). It holds no chain of thought: `rationale` is one short operational line
for the audit trail, and nothing in it ever reaches the prospect.

Parsing only checks shape. Whether the answer is *acceptable* (allowed here, consistent with the
configuration, safe) is decided by `validation.py`.
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, get_args

from pydantic import Field, ValidationError

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.consultative.errors import BrainOutputError
from sales_brain_kit.settings.common import Model
from sales_brain_kit.settings.discovery import StopSignal
from sales_core.contracts import LLMResponse

STOP_SIGNALS = frozenset(get_args(StopSignal))
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class Disqualification(Model):
    product_id: str = Field(max_length=64)
    rule_id: str = Field(max_length=64)


class Analysis(Model):
    """What the model understood from the prospect's latest message."""

    intent: str | None = Field(default=None, max_length=64)
    objection: str | None = Field(default=None, max_length=64)
    prospect_language: str | None = Field(default=None, max_length=16)  # the language they wrote in
    requested_language: str | None = Field(default=None, max_length=16)  # one they asked us to use
    explicit_refusal: bool = False
    out_of_scope: bool = False
    stop_signals: tuple[str, ...] = Field(default=(), max_length=10)
    disqualified: tuple[Disqualification, ...] = Field(default=(), max_length=10)


class FollowUpChoice(Model):
    """The situation the next touch belongs to. The delay itself always comes from settings."""

    situation: str | None = Field(default=None, max_length=64)
    requested_at: str | None = Field(default=None, max_length=40)  # ISO 8601, with an offset


class BrainOutput(Model):
    decision: str = Field(max_length=32)
    message: str | None = Field(default=None, max_length=4000)
    language: str | None = Field(default=None, max_length=16)
    analysis: Analysis = Field(default_factory=Analysis)
    product_candidates: tuple[str, ...] = Field(default=(), max_length=20)
    presented_products: tuple[str, ...] = Field(default=(), max_length=10)
    selected_product: str | None = Field(default=None, max_length=64)
    discovery_key: str | None = Field(default=None, max_length=64)
    follow_up: FollowUpChoice = Field(default_factory=FollowUpChoice)
    stage: str | None = Field(default=None, max_length=64)
    handoff_reason: str | None = Field(default=None, max_length=500)
    handoff_briefing: str | None = Field(default=None, max_length=1500)
    close_reason: str | None = Field(default=None, max_length=500)
    rationale: str = Field(default="", max_length=2000)


def parse_output(response: LLMResponse) -> BrainOutput:
    """The model's answer as a `BrainOutput`. Raises `BrainOutputError` if it is not one."""
    data: object = _plain(response.parsed) if response.parsed is not None else _load(response.text)
    if not isinstance(data, dict):
        raise BrainOutputError(["the answer must be a single JSON object"])
    try:
        return BrainOutput.model_validate(data)
    except ValidationError as error:
        raise BrainOutputError(_problems(error)) from None


def _load(text: str) -> object:
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    try:
        return json.loads(fenced.group(1) if fenced else stripped)
    except json.JSONDecodeError as error:
        raise BrainOutputError([f"the answer is not valid JSON ({error.msg})"]) from None


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _problems(error: ValidationError) -> list[str]:
    problems = []
    for item in error.errors():
        path = ".".join(str(part) for part in item["loc"]) or "answer"
        problems.append(f"{path}: {item['msg']}")
    return problems


# ---- the JSON Schema handed to the provider ---------------------------------------------------


def _choice(values: Sequence[str]) -> dict[str, Any]:
    """One of `values`, or null. With nothing to choose from, only null."""
    if not values:
        return {"type": "null"}
    return {"type": ["string", "null"], "enum": [*values, None]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _text() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def response_schema(context: CommercialContext) -> dict[str, Any]:
    """JSON Schema of `BrainOutput` for this request.

    Every property is required (nullable where optional) and no other is allowed, which is what
    strict structured-output providers need. The enumerations narrow the model to the decisions
    open in this situation and to the client's own intents, objections, follow-up situations,
    stages and discovery targets; validation enforces them again for providers that ignore them.
    """
    settings, plan = context.settings, context.plan
    primary = context.selection.focus[0] if context.selection.focus else None
    objections = {o.code for o in settings.conversation.objections}
    for product in settings.catalog.products:
        objections |= {o.code for o in product.objections}
    return _object(
        {
            "decision": {"type": "string", "enum": sorted(d.value for d in plan.allowed_decisions)},
            "message": _text(),
            "language": _text(),
            "analysis": _object(
                {
                    "intent": _choice(list(settings.conversation.intents)),
                    "objection": _choice(sorted(objections)),
                    "prospect_language": _text(),
                    "requested_language": _text(),
                    "explicit_refusal": {"type": "boolean"},
                    "out_of_scope": {"type": "boolean"},
                    "stop_signals": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(STOP_SIGNALS)},
                    },
                    "disqualified": {
                        "type": "array",
                        "items": _object(
                            {"product_id": {"type": "string"}, "rule_id": {"type": "string"}}
                        ),
                    },
                }
            ),
            "product_candidates": {"type": "array", "items": {"type": "string"}},
            "presented_products": {"type": "array", "items": {"type": "string"}},
            "selected_product": _text(),
            "discovery_key": _choice(list(plan.discovery.target_keys)),
            "follow_up": _object(
                {
                    "situation": _choice(list(context.follow_up.situations(primary))),
                    "requested_at": _text(),
                }
            ),
            "stage": _choice([stage.id for stage in settings.stages]),
            "handoff_reason": _text(),
            "handoff_briefing": _text(),
            "close_reason": _text(),
            "rationale": {"type": "string"},
        }
    )
