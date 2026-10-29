"""Client-level settings: who we are, how we speak, and the rules of the conversation."""

from typing import Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.catalog import Cta, CtaKind, Objection
from sales_brain_kit.settings.common import (
    HttpsUrl,
    Lang,
    LocalizedShort,
    LocalizedText,
    Model,
    Paragraph,
    ShortText,
    Slug,
)
from sales_brain_kit.settings.exact_values import strip_exact_values

# Facts about a prospect that adaptation may never key on (a client may add more, never fewer).
PROTECTED_ATTRIBUTES = frozenset(
    {
        "gender",
        "sex",
        "ethnicity",
        "race",
        "religion",
        "nationality",
        "age",
        "health",
        "disability",
        "marital_status",
        "sexual_orientation",
        "political_opinion",
    }
)
# Keys that may adjust only practical things (currency, availability), never tone or strategy.
PRACTICAL_ONLY_KEYS = frozenset({"country", "city", "region"})
# Facts every prospect has without the client declaring a field for them.
BUILTIN_ADAPTATION_INPUTS = frozenset({"language", "locale"})


class BusinessSettings(Model):
    name: ShortText
    summary: LocalizedText | None = None
    website: HttpsUrl | None = None


# Where a language can be learned from, in the client's own order of trust. `explicit_preference`
# is a language the prospect confirmed; `conversation` one seen in the exchange; `profile` what the
# client holds for the prospect (a language fact or locale); `market` the language rule of the
# prospect's market; `default` the client's default (always the last resort).
LanguageSignal = Literal["explicit_preference", "conversation", "profile", "market", "default"]
DEFAULT_LANGUAGE_PRIORITY: tuple[LanguageSignal, ...] = (
    "explicit_preference",
    "conversation",
    "profile",
    "market",
    "default",
)


class LanguageSettings(Model):
    default: Lang
    supported: tuple[Lang, ...] = Field(min_length=1, max_length=10)
    mirror_prospect_language: bool = True
    priority: tuple[LanguageSignal, ...] = DEFAULT_LANGUAGE_PRIORITY

    @model_validator(mode="after")
    def _default_is_supported(self) -> "LanguageSettings":
        if self.default not in self.supported:
            raise ValueError(f"default language {self.default!r} must be one of 'supported'")
        if len(set(self.supported)) != len(self.supported):
            raise ValueError("'supported' lists a language twice")
        if len(set(self.priority)) != len(self.priority):
            raise ValueError("'priority' lists a signal twice")
        if "explicit_preference" not in self.priority:
            raise ValueError(
                "'priority' must include 'explicit_preference': what a prospect asked for is never ignored"
            )
        return self


class Formality(Model):
    default: Literal["formal", "neutral", "informal"] = "neutral"
    allow_switch: bool = False  # may the Brain move to the prospect's own register?


class ToneSettings(Model):
    style: ShortText = "professional and friendly"
    formality: Formality = Field(default_factory=Formality)
    persona_name: ShortText | None = None


class RegisterWhen(Model):
    markets: tuple[Slug, ...] = Field(default=(), max_length=20)
    languages: tuple[Lang, ...] = Field(default=(), max_length=10)


class RegisterRule(Model):
    """The register (formality, style) for a market and/or language. The first rule that matches
    the prospect's known market and the language in use applies; without one, `tone` does."""

    id: Slug
    when: RegisterWhen
    formality: Literal["formal", "neutral", "informal"] | None = None
    allow_switch: bool | None = None  # may the Brain move to the prospect's own register?
    style: ShortText | None = None

    @model_validator(mode="after")
    def _has_a_condition_and_an_effect(self) -> "RegisterRule":
        if not self.when.markets and not self.when.languages:
            raise ValueError("'when' needs at least one market or language")
        if self.formality is None and self.allow_switch is None and self.style is None:
            raise ValueError("give at least one of 'formality', 'allow_switch' or 'style'")
        return self


class MessageSettings(Model):
    max_chars: int = Field(default=600, ge=100, le=2000)
    max_questions: int = Field(default=1, ge=0, le=2)  # 2 is the platform ceiling
    emoji: Literal["none", "light"] = "light"
    greeting: Literal["first_message_only", "every_message", "never"] = "first_message_only"


LessonKind = Literal["habit", "example", "objection", "faq", "never"]
# Lessons that answer a situation: they say when they apply (`when`) and are shown when they are close
# to what the prospect just said.
SITUATIONAL_LESSONS: frozenset[str] = frozenset({"example", "objection", "faq"})
# Lessons that hold for every conversation, whatever the product: never tied to one.
GLOBAL_LESSONS: frozenset[str] = frozenset({"habit", "never"})
MAX_LESSONS = 60


class TrainingLesson(Model):
    """One thing the client taught the agent so that it sells like the client's own people.

    - `habit`: a way of writing to keep (`text`).
    - `never`: a red line, something the agent never says or does (`text`).
    - `example`: how the client would answer a situation (`when` is what the prospect says or does,
      `text` the client's own answer). The Brain takes the spirit and the wording, not a script.
    - `objection`: how the client answers a doubt or a refusal (`when` is how it sounds, `text` the
      answer, `why` the reasoning behind it).
    - `faq`: a question prospects ask (`when`) and the answer the client gives (`text`).

    `product` ties a lesson to one product: it is shown only when that product is in play. Prices,
    links, e-mail addresses and account numbers never go in a lesson: they reach a prospect only
    through the exact values of the catalog and the payment methods."""

    kind: LessonKind
    text: Paragraph
    when: ShortText | None = None
    product: Slug | None = None
    why: ShortText | None = None

    @model_validator(mode="after")
    def _is_understandable(self) -> "TrainingLesson":
        if self.kind in SITUATIONAL_LESSONS and not self.when:
            raise ValueError(f"a '{self.kind}' lesson needs 'when': the situation it answers")
        if self.kind not in SITUATIONAL_LESSONS and self.when:
            raise ValueError(f"a '{self.kind}' lesson has no 'when'")
        if self.kind in GLOBAL_LESSONS and self.product:
            raise ValueError(f"a '{self.kind}' lesson holds for every product: it has no 'product'")
        if self.kind in GLOBAL_LESSONS and self.why:
            raise ValueError(f"a '{self.kind}' lesson has no 'why'")
        return self

    @model_validator(mode="after")
    def _holds_no_exact_value(self) -> "TrainingLesson":
        for name in ("text", "when", "why"):
            value = getattr(self, name)
            if value and strip_exact_values(value) != value:
                raise ValueError(
                    f"'{name}' holds a price, a link, an e-mail address or an account number: "
                    "those are never taught, they come from the catalog and the payment methods"
                )
        return self


# How the agent leads a conversation. The wording of each choice is the platform's (see
# `consultative/training.py`), so that a choice always means the same well-tested thing.
SellingApproach = Literal["consultative", "challenger", "guide", "direct"]
ClosingStyle = Literal["soft", "choice", "assumptive", "direct"]


class Playbook(Model):
    """The client's way of selling, in a few choices that hold for every product."""

    approach: SellingApproach = "consultative"
    closing: ClosingStyle = "choice"
    # `real_reasons_only`: the agent may mention a deadline or a limited place only if the catalog
    # or a lesson really says so; it never invents urgency.
    urgency: Literal["none", "real_reasons_only"] = "none"
    # A prospect asks for a discount: the agent never offers one, or hands the request to a person.
    discounts: Literal["never_offer", "ask_a_person"] = "never_offer"
    # A prospect names a competitor: the agent stays positive and factual, or does not discuss it.
    competitors: Literal["stay_positive", "avoid"] = "stay_positive"
    pitch: ShortText | None = None  # the business in one breath, in the client's own words
    ideal_customer: ShortText | None = None  # who the business serves best

    @model_validator(mode="after")
    def _holds_no_exact_value(self) -> "Playbook":
        for name in ("pitch", "ideal_customer"):
            value = getattr(self, name)
            if value and strip_exact_values(value) != value:
                raise ValueError(f"'{name}' holds a price, a link or an e-mail address")
        return self


class CommunicationSettings(Model):
    languages: LanguageSettings
    tone: ToneSettings = Field(default_factory=ToneSettings)
    principles: tuple[ShortText, ...] = Field(default=(), max_length=10)
    message: MessageSettings = Field(default_factory=MessageSettings)
    register_rules: tuple[RegisterRule, ...] = Field(default=(), max_length=30)
    playbook: Playbook = Field(default_factory=Playbook)
    training: tuple[TrainingLesson, ...] = Field(default=(), max_length=MAX_LESSONS)


class Goal(Model):
    id: Slug
    label: ShortText
    kind: Literal["conversion", "meeting", "lead_capture", "handoff"] = "conversion"
    default_cta: CtaKind | None = None


class Objectives(Model):
    goals: tuple[Goal, ...] = Field(default=(), max_length=10)
    primary: Slug | None = None


class Stage(Model):
    id: Slug
    label: ShortText
    milestone: Literal["engaged", "qualified", "proposal", "appointment", "won", "lost"] | None = (
        None
    )


IntentBehaviour = Literal[
    "answer_then_continue", "handoff", "polite_redirect", "honour_and_close", "ignore"
]


class RecommendationPolicy(Model):
    claims: Literal["only_from_catalog"] = "only_from_catalog"  # an invariant, not a preference
    always_end_with: Literal["next_step", "none"] = "next_step"


class MediaHandling(Model):
    voice_note: Literal["ask_for_text", "handoff", "ignore"] = "ask_for_text"
    image: Literal["acknowledge", "ask_for_text", "ignore"] = "acknowledge"


class ConversationSettings(Model):
    answer_first: bool = True
    recommendation: RecommendationPolicy = Field(default_factory=RecommendationPolicy)
    cta: Cta = Field(default_factory=Cta)
    objections: tuple[Objection, ...] = Field(default=(), max_length=20)
    # Intent names are the client's vocabulary (price_request, human_requested, ...).
    intents: dict[Slug, IntentBehaviour] = Field(default_factory=dict)
    media_handling: MediaHandling = Field(default_factory=MediaHandling)

    @model_validator(mode="after")
    def _few_intents(self) -> "ConversationSettings":
        if len(self.intents) > 20:
            raise ValueError("at most 20 intents")
        return self


class Greeting(Model):
    mention_business: bool = True


class OutOfScope(Model):
    reply: Literal["polite_redirect", "ignore"] = "polite_redirect"
    then: Literal["handoff", "none"] = "handoff"


class IncomingSettings(Model):
    """How the Brain behaves when a prospect writes first. Whether the agent handles such
    conversations at all is a client setting enforced by the Core (`inbound`), not part of this."""

    greeting: Greeting = Field(default_factory=Greeting)
    goals: tuple[
        Literal[
            "understand_need", "present_options", "qualify", "propose_next_step", "answer_questions"
        ],
        ...,
    ] = ("understand_need", "present_options", "propose_next_step")
    allowed_topics: tuple[Slug, ...] = Field(default=(), max_length=20)
    out_of_scope: OutOfScope = Field(default_factory=OutOfScope)
    no_product: Literal["overview_then_one_question", "present_overview", "ask_open_question"] = (
        "overview_then_one_question"
    )


class HandoffTrigger(Model):
    intent: Slug | None = None
    after_rebuttals: int | None = Field(default=None, ge=1, le=5)
    disqualified: Slug | None = None  # the id of a product disqualifier

    @model_validator(mode="after")
    def _exactly_one(self) -> "HandoffTrigger":
        given = [v for v in (self.intent, self.after_rebuttals, self.disqualified) if v is not None]
        if len(given) != 1:
            raise ValueError("give exactly one of 'intent', 'after_rebuttals', 'disqualified'")
        return self


class HandoffSettings(Model):
    enabled: bool = True  # False: the agent never hands a conversation to a person
    triggers: tuple[HandoffTrigger, ...] = Field(default=(), max_length=10)
    message: LocalizedShort | None = None  # what the prospect is told
    briefing: bool = True  # summarise the conversation for the human


class Dimension(Model):
    inputs: tuple[Slug, ...] = Field(default=(), max_length=10)  # fact keys that may drive it
    covers: tuple[
        Literal["currency", "availability", "delivery_mode", "dialect_vocabulary"], ...
    ] = ()


class Pack(Model):
    """Client-written examples and vocabulary, chosen by an observed fact (never a demographic)."""

    id: Slug
    applies_when: dict[Slug, tuple[ShortText, ...]] = Field(min_length=1)  # fact key -> values
    examples: tuple[ShortText, ...] = Field(default=(), max_length=10)
    vocabulary: tuple[ShortText, ...] = Field(default=(), max_length=20)


DimensionName = Literal["language", "register", "examples", "depth", "practical"]


class AdaptationSettings(Model):
    dimensions: dict[DimensionName, Dimension] = Field(default_factory=dict)
    packs: tuple[Pack, ...] = Field(default=(), max_length=20)
    min_provenance: Literal["discovered", "hypothesis"] = "discovered"
    extra_protected_attributes: tuple[Slug, ...] = Field(default=(), max_length=20)


class Template(Model):
    id: Slug
    purpose: Literal["first_contact", "follow_up", "reminder", "handoff_ack"]
    language: Lang
    provider_template_id: ShortText
    params: tuple[Slug, ...] = Field(default=(), max_length=10)
