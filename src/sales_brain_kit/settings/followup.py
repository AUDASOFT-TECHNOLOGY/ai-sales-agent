"""Follow-up strategy: when to come back, in which situation. The Core only ever gets a date."""

from typing import Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.common import Duration, Model, Slug

SituationId = Literal[
    "no_response",
    "after_recommendation",
    "after_cta",
    "objection_unresolved",
    "callback_promised",
    "appointment_reminder",
    "interest_cooled",
]
Then = Literal["park", "close", "handoff"]
StopCondition = Literal["rejected_all_interests", "explicit_no", "handoff_active"]

MAX_STEPS = 6
MIN_GAP_FLOOR_SECONDS = 3600  # a follow-up gap shorter than an hour is never sensible


class Step(Model):
    after: Duration | None = None  # since our last message
    before: Duration | None = None  # before an appointment (appointment_reminder only)
    intent: Slug | None = None

    @model_validator(mode="after")
    def _exactly_one_timing(self) -> "Step":
        if (self.after is None) == (self.before is None):
            raise ValueError("give exactly one of 'after' or 'before'")
        return self


class Situation(Model):
    steps: tuple[Step, ...] = Field(default=(), max_length=MAX_STEPS)
    then: Then = "park"  # what happens once the steps are used up
    use: Literal["prospect_preference"] | None = None  # take the time from what the prospect asked

    @model_validator(mode="after")
    def _steps_or_use(self) -> "Situation":
        if self.use is not None and self.steps:
            raise ValueError("'use' and 'steps' are alternatives: give one")
        if self.use is None and not self.steps:
            raise ValueError("give at least one step, or 'use'")
        return self


class FollowUpLimits(Model):
    max_touches: int = Field(default=4, ge=1, le=10)
    min_gap: Duration = "P1D"


def _default_situations() -> dict[str, Situation]:
    return {"no_response": Situation(steps=(Step(after="P2D"), Step(after="P5D")), then="park")}


class FollowUpSettings(Model):
    situations: dict[SituationId, Situation] = Field(default_factory=_default_situations)
    limits: FollowUpLimits = Field(default_factory=FollowUpLimits)
    stop_conditions: tuple[StopCondition, ...] = (
        "rejected_all_interests",
        "explicit_no",
        "handoff_active",
    )


class ProductFollowUp(Model):
    """A product's overrides, by situation. Situations it does not list use the global ones."""

    situations: dict[SituationId, Situation] = Field(default_factory=dict)
