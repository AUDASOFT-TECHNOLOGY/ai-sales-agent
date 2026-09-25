"""The conversion workflow: the steps that take a convinced prospect to a completed sale.

A workflow is an ordered list of typed steps, not a process engine. Each step says what it is for,
what it needs, whether a person is involved, how it is known to be done and what follows. Where
the workflow stands is *derived*, never stored: from the facts the Core holds about the prospect
(recorded by staff, by another system, or by a later Brain slice) and from the stage the prospect
has reached. Nothing here says that registration, payment or an appointment needs a person: each
step chooses.

The steps `recommend` and `qualify` are the discovery ladder already configured under `discovery`
(they are done when what `sufficiency` asks for is known); the others are conversion steps.
"""

from typing import Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.catalog import Appointment
from sales_brain_kit.settings.common import HttpsUrl, LocalizedShort, Model, ShortText, Slug

StepKind = Literal[
    "recommend",
    "qualify",
    "inform",
    "register",
    "appointment",
    "callback",
    "payment",
    "payment_confirmation",
    "handoff",
    "close",
    "custom",
]
HumanRole = Literal["none", "optional", "required"]
FALLBACK_KEYWORDS = ("handoff", "close", "park", "skip")

# Kinds whose completion the workflow cannot know by itself: the client says how it is known.
GATED_KINDS: frozenset[str] = frozenset({"register", "appointment", "callback", "custom"})
TERMINAL_KINDS: frozenset[str] = frozenset({"handoff", "close"})
PAYMENT_KINDS: frozenset[str] = frozenset({"payment", "payment_confirmation"})


class FactCondition(Model):
    """A fact that must be known (and, if `equals` is given, have one of these values)."""

    key: Slug
    equals: tuple[ShortText, ...] = Field(default=(), max_length=10)
    min_status: Literal["known", "confirmed"] = "confirmed"


class Completion(Model):
    """How a step is known to be done. Every condition given must hold."""

    facts: tuple[FactCondition, ...] = Field(default=(), max_length=10)
    stage: Slug | None = None  # the prospect has reached this stage

    @model_validator(mode="after")
    def _not_empty(self) -> "Completion":
        if not self.facts and self.stage is None:
            raise ValueError("give at least one fact condition or a stage")
        return self


class Requires(Model):
    steps: tuple[Slug, ...] = Field(default=(), max_length=10)  # must be done first
    facts: tuple[Slug, ...] = Field(default=(), max_length=10)  # must be known first


class RegistrationStep(Model):
    """`self_service`: the prospect registers through `url`. `agent_assisted`: the agent collects
    what registration needs in the conversation. `human_assisted`: a person registers them."""

    mode: Literal["self_service", "agent_assisted", "human_assisted"] = "agent_assisted"
    collects: tuple[Slug, ...] = Field(default=(), max_length=10)  # facts to gather
    url: HttpsUrl | None = None

    @model_validator(mode="after")
    def _self_service_needs_a_link(self) -> "RegistrationStep":
        if self.mode == "self_service" and self.url is None:
            raise ValueError("'self_service' registration needs a 'url'")
        return self


class PaymentStep(Model):
    methods: tuple[Slug, ...] = Field(default=(), max_length=30)  # empty: every applicable method


class WorkflowStep(Model):
    id: Slug
    kind: StepKind
    label: ShortText | None = None
    objective: LocalizedShort | None = None  # guidance for the wording of this step
    requires: Requires | None = None
    human: HumanRole | None = None  # None: `required` for a handoff step, else `optional`
    completes_when: Completion | None = None
    next: Slug | None = None  # None: the step that follows in the list
    fallback: Slug | None = None  # a step id or one of handoff, close, park, skip; see below
    stage: Slug | None = None  # the stage this step suggests for the prospect
    registration: RegistrationStep | None = None
    payment: PaymentStep | None = None
    appointment: Appointment | None = None

    @property
    def effective_human(self) -> HumanRole:
        if self.human is not None:
            return self.human
        return {"handoff": "required", "close": "none"}.get(self.kind, "optional")  # type: ignore[return-value]

    @model_validator(mode="after")
    def _kind_rules(self) -> "WorkflowStep":
        if self.registration is not None and self.kind != "register":
            raise ValueError("'registration' settings belong to a 'register' step")
        if self.payment is not None and self.kind not in PAYMENT_KINDS:
            raise ValueError("'payment' settings belong to a payment step")
        if self.appointment is not None and self.kind not in ("appointment", "callback"):
            raise ValueError("'appointment' settings belong to an appointment or callback step")
        if self.kind in PAYMENT_KINDS and self.completes_when is not None:
            raise ValueError(
                f"a '{self.kind}' step has no 'completes_when': a payment step is done when the "
                "prospect has claimed or proved a payment, and a confirmation step only when a "
                "person at the client has confirmed it"
            )
        if self.kind in GATED_KINDS and self.completes_when is None:
            raise ValueError(
                f"a '{self.kind}' step needs 'completes_when': how is it known to be done?"
            )
        if self.kind in TERMINAL_KINDS and (self.completes_when is not None or self.next):
            raise ValueError(
                f"a '{self.kind}' step ends the workflow: no 'completes_when' or 'next'"
            )
        if self.kind == "close" and self.human == "required":
            raise ValueError("a 'close' step cannot require a person")
        if self.kind == "handoff" and self.human in ("none", "optional"):
            raise ValueError("a 'handoff' step hands the conversation to a person")
        if (
            self.registration is not None
            and self.registration.mode == "human_assisted"
            and self.human == "none"
        ):
            raise ValueError(
                "'human_assisted' registration involves a person: 'human' cannot be none"
            )
        return self


class Workflow(Model):
    id: Slug
    label: ShortText | None = None
    steps: tuple[WorkflowStep, ...] = Field(min_length=1, max_length=20)


class ConversionSettings(Model):
    default_workflow: Slug | None = None  # the workflow when neither product nor market names one
    workflows: tuple[Workflow, ...] = Field(default=(), max_length=10)
    appointment: Appointment | None = None  # the default for appointment and callback steps
