"""A client's usage against its quota, and the alerts at 80% and 100%.

The quota itself is enforced where the message leaves (`sales_app.send_cap`): the effective daily cap
is `min(Admin cap, plan daily quota)` and a place is reserved atomically per client, channel and UTC
day. A refusal is a deferred, un-counted try, so a reached quota never loops on retries or burns an
action's attempts. This module only **reads** that real consumption (day and month) and **tells**:

- `QuotaService.status(client_id)`: today's and this month's consumption against the limit, with a
  state (`ok`, `warning` from 80%, `reached`, `not_configured`, `unavailable`) and a plain message
  with the action to take. Every figure is `measured` (a real counter). The month's quota is the
  effective daily cap times the days of the month. The credits consumed are read from the LLM usage
  register: `TOKENS_PER_CREDIT` tokens make one credit (5 000 credits = 5M tokens), out of the plan's
  `included_credits` (`DEFAULT_CREDITS` while the client is on no plan). Without a register, or when
  it cannot be read, the credits are `unavailable`: no figure is invented.
- `QuotaService.observe(...)`: called by the send guard after each reservation attempt. It opens a
  `QUOTA_REACHED` notification for the client and for the Admin the first time a threshold (80, 100)
  is crossed for a client, channel and UTC day, with a message and a suggested action. The
  notification is idempotent by `dedupe_key`; a process-local memo avoids even asking for it again.
"""

import calendar
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from sales_app.free_accounts import FreeAccountStore
from sales_app.llm_usage import LLMUsageStore
from sales_app.notifications import ADMIN, CLIENT, INFO, QUOTA_REACHED, WARNING, NotificationService
from sales_app.plans import PlanStore
from sales_app.provider_costs import MEASURED, UNAVAILABLE
from sales_app.send_cap import SendLimits, utc_day
from sales_core.ports import Clock

log = logging.getLogger(__name__)

THRESHOLDS = (80, 100)
TOKENS_PER_CREDIT = 1000  # 5 000 credits = 5M tokens
DEFAULT_CREDITS = 5000  # until the Admin's plans carry the exact figure
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
    credits_used: int | None  # this month, from the LLM usage register; None: unavailable
    credits_basis: str
    usage_basis: str  # measured: read from the real counters
    state: str
    message: str
    action: str
    month_limit: int | None = None  # the effective daily cap times the days of the month
    month_actions_used: int | None = None  # the metered sends of the cap's channel this month
    month_percent: int | None = None
    credits_today: int | None = None
    credits_remaining: int | None = None
    credits_percent: int | None = None
    tokens_used: int | None = None
    calls: int | None = None
    calls_without_figures: int | None = None
    # One entry per day of the month so far: {"day", "actions", "credits"}.
    days: tuple[dict[str, object], ...] = field(default_factory=tuple)


def _credits(tokens: int) -> int:
    return -(-tokens // TOKENS_PER_CREDIT)  # a started credit counts


@dataclass(frozen=True, slots=True)
class _Spent:
    tokens: int
    credits: int
    today: int
    by_day: dict[date, int]
    calls: int
    without_figures: int


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
        llm_usage: Callable[[str], LLMUsageStore] | None = None,
        free_accounts: FreeAccountStore | None = None,
    ) -> None:
        self._free = free_accounts
        self._llm_usage = llm_usage
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
            usage_basis=MEASURED,
        )
        try:
            if self._plans is not None:
                assignment = self._plans.current_assignment(client_id)
                if assignment is not None:
                    plan_name, credits = assignment.plan_name, assignment.included_credits
            if credits is None and self._free is not None:
                free = self._free.get(client_id)  # the free credits: for preparing the agent
                credits = free.credits if free is not None else None
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
                included_credits=credits or DEFAULT_CREDITS,
                credits_used=None,
                credits_basis=UNAVAILABLE,
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
        included = credits or DEFAULT_CREDITS
        month_actions = sum(n for (ch, _), n in by_day.items() if ch == CAP_CHANNEL)
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        month_limit = None if limit is None else limit * days_in_month
        month_percent = None if not month_limit else _percent(month_actions, month_limit)
        spent = self._credit_figures(client_id, month_start, month_end, today)
        per_day: dict[date, int] = {}
        for (ch, d), n in by_day.items():
            if ch == CAP_CHANNEL:
                per_day[d] = per_day.get(d, 0) + n
        days = tuple(
            {
                "day": d.isoformat(),
                "actions": per_day.get(d, 0),
                "credits": None if spent is None else spent.by_day.get(d, 0),
            }
            for d in (month_start.replace(day=i) for i in range(1, today.day + 1))
        )
        return QuotaStatus(
            **base,
            credits_used=None if spent is None else spent.credits,
            credits_basis=UNAVAILABLE if spent is None else MEASURED,
            credits_today=None if spent is None else spent.today,
            credits_remaining=None if spent is None else max(included - spent.credits, 0),
            credits_percent=None if spent is None else _percent(spent.credits, included),
            tokens_used=None if spent is None else spent.tokens,
            calls=None if spent is None else spent.calls,
            calls_without_figures=None if spent is None else spent.without_figures,
            month_limit=month_limit,
            month_actions_used=month_actions,
            month_percent=month_percent,
            days=days,
            plan_name=plan_name,
            plan_daily_quota=figures.plan_quota,
            admin_cap=figures.admin_cap,
            daily_limit=limit,
            daily_used=daily_used,
            daily_remaining=remaining,
            daily_percent=percent,
            month_used=month_used,
            included_credits=included,
            state=state,
            message=message,
            action=action,
        )

    def _credit_figures(
        self, client_id: str, month_start: date, month_end: date, today: date
    ) -> _Spent | None:
        """The month's model consumption, or None when there is no register or it cannot be read."""
        if self._llm_usage is None:
            return None
        try:
            records = self._llm_usage(client_id).list_usage()
        except Exception as error:  # noqa: BLE001 - say "unavailable", never the store's text
            log.warning("credit usage unreadable: %s", type(error).__name__)
            return None
        tokens_by_day: dict[date, int] = {}
        calls = without_figures = 0
        for record in records:
            day = utc_day(record.occurred_at)
            if not month_start <= day < month_end:
                continue
            calls += 1
            if record.input_tokens is None and record.output_tokens is None:
                without_figures += 1
                continue
            used = (record.input_tokens or 0) + (record.output_tokens or 0)
            tokens_by_day[day] = tokens_by_day.get(day, 0) + used
        tokens = sum(tokens_by_day.values())
        return _Spent(
            tokens=tokens,
            credits=_credits(tokens),
            today=_credits(tokens_by_day.get(today, 0)),
            by_day={d: _credits(n) for d, n in tokens_by_day.items()},
            calls=calls,
            without_figures=without_figures,
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
