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
    ShortText,
    Slug,
)

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


class LanguageSettings(Model):
    default: Lang
    supported: tuple[Lang, ...] = Field(min_length=1, max_length=10)
    mirror_prospect_language: bool = True

    @model_validator(mode="after")
    def _default_is_supported(self) -> "LanguageSettings":
        if self.default not in self.supported:
            raise ValueError(f"default language {self.default!r} must be one of 'supported'")
        if len(set(self.supported)) != len(self.supported):
            raise ValueError("'supported' lists a language twice")
        return self


class Formality(Model):
    default: Literal["formal", "neutral", "informal"] = "neutral"
    allow_switch: bool = False  # may the Brain move to the prospect's own register?


class ToneSettings(Model):
    style: ShortText = "professional and friendly"
    formality: Formality = Field(default_factory=Formality)
    persona_name: ShortText | None = None


class MessageSettings(Model):
    max_chars: int = Field(default=600, ge=100, le=2000)
    max_questions: int = Field(default=1, ge=0, le=2)  # 2 is the platform ceiling
    emoji: Literal["none", "light"] = "light"
    greeting: Literal["first_message_only", "every_message", "never"] = "first_message_only"


class CommunicationSettings(Model):
    languages: LanguageSettings
    tone: ToneSettings = Field(default_factory=ToneSettings)
    principles: tuple[ShortText, ...] = Field(default=(), max_length=10)
    message: MessageSettings = Field(default_factory=MessageSettings)


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
