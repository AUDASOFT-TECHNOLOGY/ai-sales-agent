"""The follow-up settings a Client may change (spec 4.5): how many follow-ups, how far apart, and in
which hours, translated to and from the Brain's own settings document.

The default is three follow-ups two days apart. The Client chooses a number (0 to 6, 0 meaning none),
a wait in whole days (1 to 30) and, optionally, the hours of the day (in a time zone) in which a
follow-up may be due. This module only moves values between that simple shape and the document's
`follow_up` section; it does not validate the Brain's rules. The caller publishes the result through
the same validation as every settings publication, so a value the Brain refuses (a wait shorter than
its `min_gap`, more steps than its `max_touches`) is a 422 with the Brain's own words.

Everything else in the section is kept as it was: other situations (callbacks, reminders), a
product's own cadence, the stop conditions, the `then` of the silent-prospect cadence and the
`intent` of the steps that stay.
"""

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_FOLLOW_UPS = 3
DEFAULT_GAP_DAYS = 2
MAX_FOLLOW_UPS = 6  # what the Brain's schema allows in one cadence
MIN_GAP_DAYS, MAX_GAP_DAYS = 1, 30
NO_RESPONSE = "no_response"

_DURATION = re.compile(r"^P(?=\d|T\d)(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
_DAY = 86400


@dataclass(frozen=True, slots=True)
class Window:
    start: str  # "HH:MM"
    end: str
    timezone: str


@dataclass(frozen=True, slots=True)
class FollowUpPlan:
    max_followups: int
    gap_days: int
    window: Window | None = None
    # The document holds a cadence the simple shape cannot express (waits that differ, or in hours):
    # `gap_days` is then the first wait, and saving this plan replaces the cadence by an even one.
    custom_schedule: bool = False


DEFAULT_PLAN = FollowUpPlan(DEFAULT_FOLLOW_UPS, DEFAULT_GAP_DAYS)


def _seconds(value: object) -> int | None:
    match = _DURATION.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    weeks, days, hours, minutes = (int(part or 0) for part in match.groups())
    return (((weeks * 7 + days) * 24 + hours) * 60 + minutes) * 60


def plan_of(brain_settings: Mapping[str, Any] | None) -> FollowUpPlan:
    """What the Client sees: the document's silent-prospect cadence in the simple shape. A document
    with no `follow_up` section, or a missing Brain document, is the default plan."""
    follow = (brain_settings or {}).get("follow_up")
    if not isinstance(follow, Mapping):
        return DEFAULT_PLAN
    window = _window_of(follow.get("send_window"))
    situations = follow.get("situations")
    if not isinstance(situations, Mapping):
        return FollowUpPlan(DEFAULT_FOLLOW_UPS, DEFAULT_GAP_DAYS, window)
    cadence = situations.get(NO_RESPONSE)
    if not isinstance(cadence, Mapping):
        return FollowUpPlan(0, DEFAULT_GAP_DAYS, window)  # configured without one: no follow-up
    steps = [s for s in cadence.get("steps") or () if isinstance(s, Mapping) and "after" in s]
    waits = [_seconds(s["after"]) for s in steps]
    if not steps or None in waits:
        return FollowUpPlan(
            0, DEFAULT_GAP_DAYS, window, custom_schedule=cadence.get("use") is not None
        )
    first = waits[0] or _DAY
    even = all(w == first for w in waits) and first % _DAY == 0
    return FollowUpPlan(
        max_followups=len(steps),
        gap_days=max(MIN_GAP_DAYS, min(MAX_GAP_DAYS, round(first / _DAY))),
        window=window,
        custom_schedule=not even,
    )


def _window_of(raw: object) -> Window | None:
    if not isinstance(raw, Mapping):
        return None
    start, end, zone = raw.get("start"), raw.get("end"), raw.get("timezone")
    if isinstance(start, str) and isinstance(end, str) and isinstance(zone, str):
        return Window(start, end, zone)
    return None


def apply_plan(brain_settings: Mapping[str, Any], plan: FollowUpPlan) -> dict[str, Any]:
    """A copy of the Brain's settings document with the plan written into its `follow_up` section."""
    document = copy.deepcopy(dict(brain_settings))
    follow = dict(document.get("follow_up") or {})
    situations = dict(follow.get("situations") or {})
    old = situations.get(NO_RESPONSE) or {}
    if plan.max_followups == 0:
        situations.pop(NO_RESPONSE, None)
    else:
        kept = [s for s in old.get("steps") or () if isinstance(s, Mapping) and "after" in s]
        steps: list[dict[str, str]] = []
        for index in range(plan.max_followups):
            step = {"after": f"P{plan.gap_days}D"}
            intent = kept[index].get("intent") if index < len(kept) else None
            if intent:
                step["intent"] = intent
            steps.append(step)
        situations[NO_RESPONSE] = {"steps": steps, "then": old.get("then", "park")}
        limits = dict(follow.get("limits") or {})
        if plan.max_followups > int(limits.get("max_touches", 4)):
            limits["max_touches"] = plan.max_followups
            follow["limits"] = limits
    follow["situations"] = situations
    if plan.window is None:
        follow.pop("send_window", None)
    else:
        follow["send_window"] = {
            "start": plan.window.start,
            "end": plan.window.end,
            "timezone": plan.window.timezone,
        }
    document["follow_up"] = follow
    return document
