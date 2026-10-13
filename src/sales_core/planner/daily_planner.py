"""Daily selection of outreach actions. A pure function: no I/O, no clock, no persistence.

Callers (the engine) read prospects and existing actions from the repository, call
`plan_daily_actions`, and persist the returned actions themselves.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sales_core.contracts import (
    ActionId,
    ActionPriority,
    ActionStatus,
    Channel,
    ClientContext,
    LaneRoute,
    Prospect,
    ProspectStatus,
    SalesAction,
    SalesActionType,
)
from sales_core.safety import awaits_opt_in

DEFAULT_DAILY_ACTION_LIMIT = 20

# Strict priority, highest first. The priority written on each action mirrors this order.
_TIER_ORDER = (
    SalesActionType.FOLLOW_UP,
    SalesActionType.REMINDER,
    SalesActionType.CALLBACK,
    SalesActionType.FIRST_CONTACT,
)
_TIER_PRIORITY = {
    SalesActionType.FOLLOW_UP: ActionPriority.URGENT,
    SalesActionType.REMINDER: ActionPriority.HIGH,
    SalesActionType.CALLBACK: ActionPriority.NORMAL,
    SalesActionType.FIRST_CONTACT: ActionPriority.LOW,
}
_ELIGIBLE_STATUSES = frozenset({ProspectStatus.NEW, ProspectStatus.ACTIVE})
_OPEN_STATUSES = frozenset(
    {ActionStatus.PLANNED, ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.SENDING}
)
_COUNTED_STATUSES = _OPEN_STATUSES | {ActionStatus.SENT, ActionStatus.COMPLETED}


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyPlan:
    local_date: date  # the client's calendar day this plan belongs to
    limit: int
    already_counted: int  # today's actions that were already using the quota
    actions: tuple[SalesAction, ...]  # new actions to persist, in execution order


def plan_daily_actions(
    *,
    client: ClientContext,
    prospects: Sequence[Prospect],
    existing_actions: Sequence[SalesAction],
    now: datetime,
    daily_limit: int = DEFAULT_DAILY_ACTION_LIMIT,
    channel: Channel | None = None,
    route_of: Callable[[Prospect], LaneRoute | None] | None = None,
) -> DailyPlan:
    """Select up to `daily_limit` outreach actions for the client's current local day.

    Order of priority: due follow-ups, due reminders, due callbacks, new prospects. Within a
    tier the most overdue comes first, then `prospect_id`. Prospects that are opted out,
    paused or closed are never selected, and there is no overdue cut-off.

    Quota: planned, proposed, approved, sending, sent and completed actions due today (client timezone)
    count toward the limit; `reply` actions neither count nor are ever planned here.
    A prospect is skipped if it already has an action in flight, or was already given an
    outreach action today, so repeated runs and re-runs after a crash create no duplicates.

    `channel`, when given, is the channel the client sends on. On one where the person must start the
    conversation (Telegram), a prospect who has not is skipped: planning them would spend the quota
    on a message that cannot be sent. They are planned the day they say hello.

    `route_of`, when given, replaces `channel` for a client that runs several lanes: it answers, for
    each prospect, the lane they belong to and so the channel to write to them on (None: the
    prospect is in no lane the client runs, and is skipped). The opt-in rule above is then applied
    per prospect with their own channel, and every action planned carries its lane and channel.

    Every planned action is due `now`, has status PLANNED, and uses its idempotency key as id.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if daily_limit < 0:
        raise ValueError("daily_limit must be >= 0")
    zone = _zone(client.timezone)
    today = now.astimezone(zone).date()
    _require_same_client(client, prospects, existing_actions)

    counted_today = [
        a
        for a in existing_actions
        if a.action_type is not SalesActionType.REPLY
        and a.status in _COUNTED_STATUSES
        and a.due_at.astimezone(zone).date() == today
    ]
    remaining = max(0, daily_limit - len(counted_today))
    blocked = {a.prospect_id for a in existing_actions if a.status in _OPEN_STATUSES}
    blocked.update(a.prospect_id for a in counted_today)
    used_keys = {a.idempotency_key for a in existing_actions}

    tiers: dict[SalesActionType, list[tuple[datetime, Prospect]]] = {t: [] for t in _TIER_ORDER}
    routes: dict[str, LaneRoute] = {}
    for prospect in prospects:
        if route_of is not None:
            route = route_of(prospect)
            if route is None or awaits_opt_in(prospect, route.channel):
                continue
            routes[prospect.prospect_id] = route
        elif channel is not None and awaits_opt_in(prospect, channel):
            continue
        tier = _tier_of(prospect, now)
        if tier is not None and prospect.prospect_id not in blocked:
            due = prospect.schedule.next_action_due_at or now  # unscheduled new prospects tie
            tiers[tier].append((due, prospect))

    selected: list[SalesAction] = []
    for tier in _TIER_ORDER:
        for _, prospect in sorted(tiers[tier], key=lambda item: (item[0], item[1].prospect_id)):
            if len(selected) >= remaining:
                break
            key = f"{client.client_id}:{prospect.prospect_id}:{tier.value}:{today.isoformat()}"
            if key in used_keys or prospect.prospect_id in blocked:
                continue
            blocked.add(prospect.prospect_id)  # also drops duplicate entries in `prospects`
            route = routes.get(prospect.prospect_id)
            selected.append(
                SalesAction(
                    action_id=ActionId(key),
                    client_id=client.client_id,
                    prospect_id=prospect.prospect_id,
                    action_type=tier,
                    due_at=now,
                    idempotency_key=key,
                    priority=_TIER_PRIORITY[tier],
                    status=ActionStatus.PLANNED,
                    lane_id=route.lane_id if route is not None else None,
                    channel=route.channel if route is not None else None,
                )
            )

    return DailyPlan(
        local_date=today,
        limit=daily_limit,
        already_counted=len(counted_today),
        actions=tuple(selected),
    )


def _tier_of(prospect: Prospect, now: datetime) -> SalesActionType | None:
    """The tier a prospect currently belongs to, or None if it is not selectable."""
    if prospect.status not in _ELIGIBLE_STATUSES:
        return None
    schedule = prospect.schedule
    if schedule.next_action_type is None:
        return SalesActionType.FIRST_CONTACT if prospect.status is ProspectStatus.NEW else None
    due = schedule.next_action_due_at
    if due is None or due > now:
        return None
    return schedule.next_action_type if schedule.next_action_type in _TIER_PRIORITY else None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError) as error:
        raise ValueError(f"unknown timezone {name!r}") from error


def _require_same_client(
    client: ClientContext, prospects: Sequence[Prospect], actions: Sequence[SalesAction]
) -> None:
    for item in (*prospects, *actions):
        if item.client_id != client.client_id:
            raise ValueError(f"{item.client_id!r} does not match client {client.client_id!r}")
