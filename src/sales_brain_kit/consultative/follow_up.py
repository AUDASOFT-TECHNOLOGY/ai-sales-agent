"""Follow-up cadence: when to come back, from configuration. The Core only ever gets a date.

The cadence belongs to the Brain, not to the Planner: the Planner picks what is due, the Brain
says what is due *next*, and the Core stores and executes it. The model only names the situation
we are in (no response, objection unresolved, a promised callback, ...); the delay comes from the
client's configuration, tightened by the product's own, never from the model.

Steps are counted from how many of our messages the prospect has left unanswered. A reply resets
the count, because the conversation is alive again:

    steps: [P2D, P5D]      first contact -> wait P2D -> follow-up 1 -> wait P5D -> follow-up 2
    then:  park | close | handoff
    used up: `park` schedules nothing; `close` and `handoff` schedule one last check, and when
    that check is due (`exhausted_then`) the conversation is closed or handed over, no model needed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sales_brain_kit.settings.catalog import Product
from sales_brain_kit.settings.common import duration_seconds
from sales_brain_kit.settings.followup import FollowUpSettings, Situation, Step, Then
from sales_brain_kit.settings.market import Market
from sales_core.contracts import SalesActionType

NO_RESPONSE = "no_response"
_ACTION_TYPES: Mapping[str, SalesActionType] = {
    "callback_promised": SalesActionType.CALLBACK,
    "appointment_reminder": SalesActionType.REMINDER,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class NextTouch:
    situation: str
    action_type: SalesActionType
    due_at: datetime | None = None  # None: nothing is scheduled
    then: Then | None = None  # what the configuration does once the steps are used up
    final: bool = False  # the scheduled touch only closes or hands over
    problem: str | None = None  # why no time could be worked out


class FollowUpPolicy:
    """The cadence: the product's own, else the prospect's market's, else the client's."""

    def __init__(self, settings: FollowUpSettings, market: Market | None = None) -> None:
        self._settings = settings
        self._global: dict[str, Situation] = dict(settings.situations)
        if market is not None and market.follow_up is not None:
            self._global |= dict(market.follow_up.situations)

    def situations(self, product: Product | None = None) -> tuple[str, ...]:
        names = dict.fromkeys(self._global)
        if product is not None and product.follow_up is not None:
            names |= dict.fromkeys(product.follow_up.situations)
        return tuple(names)

    def table(self, situation: str, product: Product | None = None) -> Situation | None:
        """The product's own cadence for a situation, else the market's or the client's."""
        if product is not None and product.follow_up is not None:
            own: dict[str, Situation] = dict(product.follow_up.situations)
            if situation in own:
                return own[situation]
        return self._global.get(situation)

    def default_situation(self, product: Product | None = None) -> str | None:
        return NO_RESPONSE if self.table(NO_RESPONSE, product) is not None else None

    @staticmethod
    def action_type(situation: str) -> SalesActionType:
        return _ACTION_TYPES.get(situation, SalesActionType.FOLLOW_UP)

    def _in_window(self, due: datetime) -> datetime:
        """A follow-up due outside the client's allowed hours waits for the next opening."""
        window = self._settings.send_window
        return due if window is None else window.next_allowed(due)

    @staticmethod
    def _forward_steps(table: Situation) -> list[Step]:
        """The waits that follow our own messages. (The schema keeps them within `max_touches`.)"""
        return [step for step in table.steps if step.after is not None]

    def next_touch(
        self,
        situation: str,
        *,
        product: Product | None,
        unanswered: int,
        now: datetime,
        requested_at: datetime | None = None,
    ) -> NextTouch:
        """When to act next in `situation`. `requested_at` is the time the prospect asked for
        (a callback) or the time of their appointment (a reminder)."""
        action_type = self.action_type(situation)
        table = self.table(situation, product)
        if table is None:
            return NextTouch(
                situation=situation,
                action_type=action_type,
                problem=f"follow-up situation {situation!r} is not configured",
            )

        def touch(
            *,
            due_at: datetime | None = None,
            then: Then | None = None,
            final: bool = False,
            problem: str | None = None,
        ) -> NextTouch:
            return NextTouch(
                situation=situation,
                action_type=action_type,
                due_at=due_at,
                then=then,
                final=final,
                problem=problem,
            )

        if table.use == "prospect_preference":
            if requested_at is None:
                return touch(
                    problem="this situation needs follow_up.requested_at (the time asked for)"
                )
            return touch(due_at=requested_at, then=table.then)
        if situation == "appointment_reminder":
            if requested_at is None:
                return touch(
                    problem="this situation needs follow_up.requested_at (the appointment)"
                )
            offsets = [duration_seconds(s.before) for s in table.steps if s.before is not None]
            upcoming = sorted(
                t for t in (requested_at - timedelta(seconds=o) for o in offsets) if t > now
            )
            return touch(due_at=upcoming[0] if upcoming else None, then=table.then)

        steps = self._forward_steps(table)
        if unanswered < len(steps):
            after = steps[unanswered].after
            assert after is not None
            due = self._in_window(now + timedelta(seconds=duration_seconds(after)))
            return touch(due_at=due, then=table.then)
        if table.then == "park":
            return touch(then="park")
        last = steps[-1].after if steps else self._settings.limits.min_gap
        assert last is not None
        return touch(
            due_at=self._in_window(now + timedelta(seconds=duration_seconds(last))),
            then=table.then,
            final=True,
        )

    def exhausted_then(self, unanswered: int, product: Product | None = None) -> Then | None:
        """What to do now that a follow-up is due and every configured step was already used."""
        table = self.table(NO_RESPONSE, product)
        if table is None:
            return None
        return table.then if unanswered > len(self._forward_steps(table)) else None

    def step_intent(self, unanswered: int, product: Product | None = None) -> str | None:
        """What the follow-up that is due now is meant to do, if the cadence says."""
        table = self.table(NO_RESPONSE, product)
        if table is None:
            return None
        steps = self._forward_steps(table)
        index = unanswered - 1
        return steps[index].intent if 0 <= index < len(steps) else None
