"""Discovery strategy: what is worth knowing, what a next step needs, and how much friction is allowed.

This is a vocabulary and a set of limits, never a list of mandatory questions. The rule behind it
is minimum necessary discovery: a field is only worth asking for if a next step requires it and it
is still missing. (The planner that applies these rules is a later slice.)
"""

from typing import Any, Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.common import Hint, Lang, Model, ShortText, Slug

Importance = Literal["critical", "useful", "optional"]
Change = Literal[
    "product_selection",
    "recommendation_angle",
    "language",
    "qualification",
    "next_action",
    "practical",
]
InterestState = Literal["possible", "probable", "confirmed"]
NextStep = Literal["recommend", "propose_next_step", "qualify"]
StopSignal = Literal[
    "direct_question", "price_request", "impatience", "declined_question", "ready_to_proceed"
]

# Keys the platform reserves for its own facts; a client cannot declare them as fields.
RESERVED_FIELD_KEYS = frozenset({"interest", "asked"})
# A hard ceiling for the number of questions in one message, whatever a client configures.
MAX_QUESTIONS_PER_MESSAGE = 2


class InferPolicy(Model):
    allowed: bool = True  # may the Brain form (never assert) a hypothesis about this field?


class DiscoveryField(Model):
    key: Slug
    label: ShortText | None = None
    importance: Importance = "useful"
    changes: tuple[Change, ...] = Field(default=(), max_length=6)  # why it matters (materiality)
    ask_hints: dict[Lang, tuple[Hint, ...]] = Field(default_factory=dict)  # examples, not scripts
    infer: InferPolicy = Field(default_factory=InferPolicy)
    source_columns: tuple[ShortText, ...] = Field(default=(), max_length=10)
    recheck_after_days: int | None = Field(default=None, ge=1, le=3650)
    sensitive: bool = False
    # Evidence (the prospect's own words) is required for important or sensitive fields, and
    # optional for ordinary conversational ones. Left out, it follows from importance/sensitivity.
    evidence: Literal["required", "optional"] = "optional"

    @model_validator(mode="before")
    @classmethod
    def _default_evidence(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("evidence") is None:
            important = data.get("importance") == "critical" or bool(data.get("sensitive"))
            data = {**data, "evidence": "required" if important else "optional"}
        return data

    @model_validator(mode="after")
    def _check(self) -> "DiscoveryField":
        if self.key in RESERVED_FIELD_KEYS:
            raise ValueError(f"{self.key!r} is reserved and cannot be declared as a field")
        if self.sensitive and self.evidence != "required":
            raise ValueError("a sensitive field must require evidence")
        for language, hints in self.ask_hints.items():
            if len(hints) > 5:
                raise ValueError(f"ask_hints[{language}]: at most 5 hints")
        return self


class Requirement(Model):
    """What must be known before a next step. Reaching it means: stop asking and act."""

    all_of: tuple[Slug, ...] = Field(default=(), max_length=10)
    any_of: tuple[Slug, ...] = Field(default=(), max_length=10)
    accept: InterestState = "probable"  # the weakest interest state that satisfies `interest`

    @model_validator(mode="after")
    def _not_empty(self) -> "Requirement":
        if not self.all_of and not self.any_of:
            raise ValueError("give at least one key in 'all_of' or 'any_of'")
        return self


class Budget(Model):
    max_questions_per_message: int = Field(default=1, ge=0, le=MAX_QUESTIONS_PER_MESSAGE)
    max_consecutive_questions: int = Field(default=2, ge=0, le=3)
    max_questions_per_conversation: int = Field(default=5, ge=0, le=10)
    give_before_ask: bool = True  # an answer or something useful comes before the next question


class BudgetOverride(Model):
    """A product may only tighten the global budget (checked with the whole document)."""

    max_questions_per_message: int | None = Field(default=None, ge=0, le=MAX_QUESTIONS_PER_MESSAGE)
    max_consecutive_questions: int | None = Field(default=None, ge=0, le=3)
    max_questions_per_conversation: int | None = Field(default=None, ge=0, le=10)


class ShortcutWhen(Model):
    interest: InterestState | None = None
    intent: Slug | None = None

    @model_validator(mode="after")
    def _has_condition(self) -> "ShortcutWhen":
        if self.interest is None and self.intent is None:
            raise ValueError("give 'interest' or 'intent'")
        return self


class Shortcut(Model):
    when: ShortcutWhen
    do: Literal["propose_next_step", "answer_then_continue", "recommend_now"]


class WhenNoProduct(Model):
    outbound: Literal[
        "lead_with_relevance_then_one_question", "present_overview", "ask_open_question"
    ] = "lead_with_relevance_then_one_question"
    inbound: Literal["overview_then_one_question", "present_overview", "ask_open_question"] = (
        "overview_then_one_question"
    )


class HypothesisPolicy(Model):
    min_confidence_to_use: float = Field(default=0.5, ge=0, le=1)
    never_state_as_fact: Literal[True] = True  # an invariant, not a preference


def _default_sufficiency() -> dict[str, Requirement]:
    return {
        "recommend": Requirement(any_of=("interest",), accept="probable"),
        "propose_next_step": Requirement(all_of=("interest",), accept="confirmed"),
    }


class DiscoverySettings(Model):
    fields: tuple[DiscoveryField, ...] = Field(default=(), max_length=40)
    sufficiency: dict[NextStep, Requirement] = Field(default_factory=_default_sufficiency)
    budget: Budget = Field(default_factory=Budget)
    stop_signals: tuple[StopSignal, ...] = (
        "direct_question",
        "price_request",
        "impatience",
        "declined_question",
        "ready_to_proceed",
    )
    shortcuts: tuple[Shortcut, ...] = Field(default=(), max_length=10)
    when_no_product: WhenNoProduct = Field(default_factory=WhenNoProduct)
    hypotheses: HypothesisPolicy = Field(default_factory=HypothesisPolicy)
