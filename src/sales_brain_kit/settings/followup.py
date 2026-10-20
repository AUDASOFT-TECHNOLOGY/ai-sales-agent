"""Follow-up strategy: when to come back, in which situation. The Core only ever gets a date."""

import re
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

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


DEFAULT_FOLLOW_UPS = 3  # follow-ups when a prospect stays silent, unless the client changes it
DEFAULT_GAP = "P2D"  # and the wait before each of them

_HOUR_MINUTE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _default_situations() -> dict[str, Situation]:
    steps = tuple(Step(after=DEFAULT_GAP) for _ in range(DEFAULT_FOLLOW_UPS))
    return {"no_response": Situation(steps=steps, then="park")}


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class SendWindow(Model):
    """The hours of the day (in `timezone`) during which a follow-up may be due. A follow-up that
    would fall outside is moved to the next opening. The window is within one day: `start` < `end`."""

    start: str = Field(pattern=_HOUR_MINUTE.pattern)  # "09:00"
    end: str = Field(pattern=_HOUR_MINUTE.pattern)  # "18:00"
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _valid(self) -> "SendWindow":
        if _minutes(self.start) >= _minutes(self.end):
            raise ValueError("'start' must be earlier than 'end'")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError, OSError):
            raise ValueError(f"unknown time zone {self.timezone!r}") from None
        return self

    def next_allowed(self, moment: datetime) -> datetime:
        """`moment` if it falls in the window, else the next time the window opens."""
        zone = ZoneInfo(self.timezone)
        local = moment.astimezone(zone)
        opens, closes = _minutes(self.start), _minutes(self.end)
        now = local.hour * 60 + local.minute
        if opens <= now < closes:
            return moment
        day = local.date() if now < opens else local.date() + timedelta(days=1)
        opening = datetime.combine(day, time(opens // 60, opens % 60), tzinfo=zone)
        return opening.astimezone(moment.tzinfo or UTC)


class FollowUpSettings(Model):
    situations: dict[SituationId, Situation] = Field(default_factory=_default_situations)
    limits: FollowUpLimits = Field(default_factory=FollowUpLimits)
    # None: a follow-up may be due at any hour (the historical behaviour).
    send_window: SendWindow | None = None
    stop_conditions: tuple[StopCondition, ...] = (
        "rejected_all_interests",
        "explicit_no",
        "handoff_active",
    )


class ProductFollowUp(Model):
    """A product's overrides, by situation. Situations it does not list use the global ones."""

    situations: dict[SituationId, Situation] = Field(default_factory=dict)
