"""A client's usage against its quota, and the alerts at 80% and 100%.

The quota itself is enforced where the message leaves (`sales_app.send_cap`): the effective daily cap
is `min(Admin cap, plan daily quota)` and a place is reserved atomically per client, channel and UTC
day. A refusal is a deferred, un-counted try, so a reached quota never loops on retries or burns an
action's attempts. This module only **reads** that real consumption (day and month) and **tells**:

- `QuotaService.status(client_id)`: today's and this month's consumption against the limit, with a
  state (`ok`, `warning` from 80%, `reached`, `not_configured`, `unavailable`) and a plain message
  with the action to take. Every figure is `measured` (a real counter); the plan's `included_credits`
  is shown as the plan's own term, and the credits consumed are `unavailable` (no credit-per-message
  rule is defined, so none is invented).
- `QuotaService.observe(...)`: called by the send guard after each reservation attempt. It opens a
  `QUOTA_REACHED` notification for the client and for the Admin the first time a threshold (80, 100)
  is crossed for a client, channel and UTC day, with a message and a suggested action. The
  notification is idempotent by `dedupe_key`; a process-local memo avoids even asking for it again.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import date

from sales_app.notifications import ADMIN, CLIENT, INFO, QUOTA_REACHED, WARNING, NotificationService
from sales_app.plans import PlanStore
from sales_app.provider_costs import MEASURED, UNAVAILABLE
from sales_app.send_cap import SendLimits, utc_day
from sales_core.ports import Clock

log = logging.getLogger(__name__)

THRESHOLDS = (80, 100)
OK, WARNING_STATE, REACHED, NOT_CONFIGURED, UNAVAILABLE_STATE = (
    "ok",
    "warning",
    "reached",
    "not_configured",
    "unavailable",
)
CAP_CHANNEL = "email"  # the channel the Admin cap and the plan quota bound
CAP_CHANNELS_EXCLUDED = frozenset({"telegram_new"})  # a sub-count of Telegram, not a message total

_LABEL = {"email": "email", "whatsapp": "WhatsApp", "telegram": "Telegram"}


def quota_key(channel: str, day: date, threshold: int) -> str:
    return f"quota:{channel}:{day.isoformat()}:{threshold}"


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    client_id: str
    day_utc: str
    month: str
    plan_name: str | None
    plan_daily_quota: int | None
    admin_cap: int | None
    daily_limit: int | None  # the effective cap
    daily_used: int | None  # the metered sends of today (the cap's channel)
    daily_remaining: int | None
    daily_percent: int | None
    month_used: int | None  # every metered channel, this calendar month (UTC)
    included_credits: int | None
    credits_used: int | None  # always None: no credit-per-message rule exists
    credits_basis: str
    usage_basis: str  # measured: read from the real counters
    state: str
    message: str
    action: str


def _percent(used: int, limit: int) -> int:
    return min(100, used * 100 // limit) if limit > 0 else 100


def _texts(state: str) -> tuple[str, str]:
    if state == REACHED:
        return (
            "Your daily sending limit is reached: sending resumes tomorrow.",
            "Wait for tomorrow (UTC) or ask Revansa to raise your limit or change your plan.",
        )
    if state == WARNING_STATE:
        return (
            "You have used more than 80% of today's sending limit.",
            "Prioritise your best prospects, or ask Revansa to raise the limit or change your plan.",
        )
    if state == NOT_CONFIGURED:
        return (
            "Your daily sending limit is not set yet: nothing is sent until Revansa sets it.",
            "Contact Revansa to set your limit.",
        )
    if state == UNAVAILABLE_STATE:
        return (
            "Your usage cannot be read right now.",
            "Try again in a moment; sending is paused until it can be checked.",
        )
    return "", ""


class QuotaService:
    def __init__(
        self,
        limits: SendLimits,
        notifications: NotificationService,
        clock: Clock,
        plans: PlanStore | None = None,
    ) -> None:
        self._limits = limits
        self._notifications = notifications
        self._clock = clock
        self._plans = plans
        self._seen: set[tuple[str, str, date, int]] = set()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "QuotaService()"

    # ---- reading ------------------------------------------------------------------------------

    def status(self, client_id: str) -> QuotaStatus:
        """Never raises: a store that cannot be read gives the `unavailable` state."""
        today = utc_day(self._clock.now())
        month_start = today.replace(day=1)
        month_end = (
            date(today.year + 1, 1, 1)
            if today.month == 12
            else date(today.year, today.month + 1, 1)
        )
        plan_name, credits = None, None
        base = dict(
            client_id=client_id,
            day_utc=today.isoformat(),
            month=month_start.isoformat()[:7],
            credits_used=None,
            credits_basis=UNAVAILABLE,
            usage_basis=MEASURED,
        )
        try:
            if self._plans is not None:
                assignment = self._plans.current_assignment(client_id)
                if assignment is not None:
                    plan_name, credits = assignment.plan_name, assignment.included_credits
            figures = self._limits.cap_of(client_id)
            by_day = self._limits.usage_between(client_id, month_start, month_end)
        except Exception as error:  # noqa: BLE001 - say "cannot be read", never the store's text
            log.warning("quota status unreadable: %s", type(error).__name__)
            message, action = _texts(UNAVAILABLE_STATE)
            return QuotaStatus(
                **base,
                plan_name=plan_name,
                plan_daily_quota=None,
                admin_cap=None,
                daily_limit=None,
                daily_used=None,
                daily_remaining=None,
                daily_percent=None,
                month_used=None,
                included_credits=credits,
                state=UNAVAILABLE_STATE,
                message=message,
                action=action,
            )
        month_used = sum(n for (ch, _), n in by_day.items() if ch not in CAP_CHANNELS_EXCLUDED)
        daily_used = sum(n for (ch, d), n in by_day.items() if d == today and ch == CAP_CHANNEL)
        limit = figures.effective
        if limit is None:
            state, percent, remaining = NOT_CONFIGURED, None, None
        else:
            percent = _percent(daily_used, limit)
            remaining = max(limit - daily_used, 0)
            state = REACHED if daily_used >= limit else WARNING_STATE if percent >= 80 else OK
        message, action = _texts(state)
        return QuotaStatus(
            **base,
            plan_name=plan_name,
            plan_daily_quota=figures.plan_quota,
            admin_cap=figures.admin_cap,
            daily_limit=limit,
            daily_used=daily_used,
            daily_remaining=remaining,
            daily_percent=percent,
            month_used=month_used,
            included_credits=credits,
            state=state,
            message=message,
            action=action,
        )

    # ---- alerts -------------------------------------------------------------------------------

    def observe(self, client_id: str, channel: str, day: date, limit: int, refused: bool) -> None:
        """The `QuotaObserver` the send guard calls after each reservation attempt."""
        if refused:
            used = limit
        else:
            used = self._limits.used(client_id, channel, day)
        percent = _percent(used, limit)
        for threshold in THRESHOLDS:
            reached = used >= limit if threshold == 100 else percent >= threshold
            if not reached:
                continue
            memo = (client_id, channel, day, threshold)
            with self._lock:
                if memo in self._seen:
                    continue
                if len(self._seen) > 5000:
                    self._seen.clear()
                self._seen.add(memo)
            self._alert(client_id, channel, day, threshold, used, limit)

    def _alert(
        self, client_id: str, channel: str, day: date, threshold: int, used: int, limit: int
    ) -> None:
        label = _LABEL.get(channel, channel)
        key = quota_key(channel, day, threshold)
        figures = f"{used} of {limit} {label} messages used today (UTC)"
        if threshold >= 100:
            title = f"Daily {label} limit reached"
            client_action = (
                "Sending resumes tomorrow. To send more today, ask Revansa to raise your limit "
                "or change your plan."
            )
            admin_action = (
                "Raise the client's send cap or change its plan if the usage is expected."
            )
            severity = WARNING
        else:
            title = f"{threshold}% of the daily {label} limit used"
            client_action = (
                "Prioritise your best prospects, or ask Revansa to raise your limit or change "
                "your plan."
            )
            admin_action = "Check the client's plan and send cap before the limit is reached."
            severity = INFO
        message = f"{figures}."
        self._notifications.notify(
            QUOTA_REACHED,
            CLIENT,
            client_id,
            title=title,
            message=message,
            action=client_action,
            severity=severity,
            dedupe_key=key,
        )
        self._notifications.notify(
            QUOTA_REACHED,
            ADMIN,
            client_id,
            title=f"{title} for {client_id}",
            message=message,
            action=admin_action,
            severity=severity,
            dedupe_key=key,
        )
