"""What the Admin and Client dashboards share: reading metrics, notifications and the incident series.

Every reader here degrades to `not_tracked` with a reason (never to 0, never to a guess) when its
store is missing or cannot be read.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date
from typing import Any

from sales_app.api.client_prospects import lane_products
from sales_app.bootstrap import Application
from sales_app.dashboards.assemble import activity_block
from sales_app.dashboards.figures import MEASURED, indicator, not_tracked
from sales_app.dashboards.ranges import DashboardRange
from sales_app.metrics import ClientMetrics, MetricsService, Period
from sales_app.notifications import (
    AUTHORIZATION_REVOKED,
    CONFIG_PROBLEM,
    CONNECTION_EXPIRED,
    INFO,
    RECEIVE_FAILURE,
    SEND_FAILURE,
    SYNC_FAILURE,
    Notification,
    NotificationQuery,
)
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

NO_DATABASE = "this figure needs the database"
UNREADABLE = "this figure cannot be read right now"
NOTIFICATION_PAGE = 200
NOTIFICATION_PAGES = 10  # at most 2000 notifications are read for a dashboard
CONNECTION_KINDS = frozenset(
    {
        CONNECTION_EXPIRED,
        AUTHORIZATION_REVOKED,
        SEND_FAILURE,
        RECEIVE_FAILURE,
        SYNC_FAILURE,
        CONFIG_PROBLEM,
    }
)


def client_metrics(
    application: Application, client_id: str, period: Period
) -> ClientMetrics | None:
    """One client's metrics; None when there is no database or it cannot be read."""
    factory = application.metrics_readers
    if factory is None:
        return None
    mapping = lane_products(application, client_id)
    try:
        service = MetricsService(factory(client_id), lambda: mapping or {})
        return service.client_metrics(period)
    except StoreUnavailableError:
        return None
    except Exception:
        log.warning("metrics of %s could not be read", client_id, exc_info=True)
        return None


def empty_activity(reason: str) -> dict[str, Any]:
    nt = not_tracked(reason)
    return {
        "prospects_total": nt,
        "prospects_contacted": nt,
        "conversations": nt,
        "messages_sent": nt,
        "replies_received": nt,
        "prospects_replied": nt,
        "reply_rate_percent": nt,
        "needs_human": nt,
        "with_errors": nt,
        "conversions_declared": nt,
        "conversions_confirmed": nt,
        "conversion_rate_declared_percent": nt,
        "conversion_rate_confirmed_percent": nt,
        "payments": {"declared": nt, "confirmed": nt, "awaiting_confirmation": nt},
        "by_status": {},
        "by_circuit": {},
        "by_product": {},
        "by_channel": {},
        "by_source": {},
        "messages_by_channel": {},
    }


def activity_of(
    application: Application, client_ids: list[str], period: Period
) -> tuple[dict[str, Any], list[ClientMetrics]]:
    """The merged activity block and the per-client metrics it was built from."""
    if application.metrics_readers is None:
        return empty_activity(NO_DATABASE), []
    found: list[ClientMetrics] = []
    for client_id in client_ids:
        one = client_metrics(application, client_id, period)
        if one is None:
            return empty_activity(UNREADABLE), []
        found.append(one)
    if not found:
        return activity_block([]), []
    return activity_block(found), found


@dataclass(frozen=True, slots=True)
class NotificationRead:
    items: list[Notification]
    total: int

    @property
    def complete(self) -> bool:
        return len(self.items) >= self.total


def read_notifications(
    application: Application,
    make_query: Callable[..., NotificationQuery],
    *,
    include_resolved: bool,
) -> NotificationRead | None:
    """Pages through the notifications of the scope; None if the store cannot be read."""
    items: list[Notification] = []
    total = 0
    try:
        for page in range(NOTIFICATION_PAGES):
            found, total = application.notifications.search(
                make_query(
                    include_resolved=include_resolved,
                    limit=NOTIFICATION_PAGE,
                    offset=page * NOTIFICATION_PAGE,
                )
            )
            items.extend(found)
            if len(found) < NOTIFICATION_PAGE or len(items) >= total:
                break
    except StoreUnavailableError:
        return None
    return NotificationRead(items, total)


def incident_block(
    open_read: NotificationRead | None, all_read: NotificationRead | None
) -> dict[str, Any]:
    """Open problems by kind and severity (the notifications that are not merely informative)."""
    if open_read is None:
        return {
            "open": not_tracked(UNREADABLE),
            "by_severity": {},
            "by_kind": {},
            "sync_failures": not_tracked(UNREADABLE),
            "connection_problems": not_tracked(UNREADABLE),
            "items": [],
        }
    problems = [n for n in open_read.items if n.severity != INFO]
    reason = None if open_read.complete else "only the most recent notifications were counted"
    availability = MEASURED if open_read.complete else "estimated"
    kinds = Counter(n.kind for n in problems)
    return {
        "open": indicator(len(problems), availability, reason),
        "by_severity": dict(sorted(Counter(n.severity for n in problems).items())),
        "by_kind": dict(sorted(kinds.items())),
        "sync_failures": indicator(kinds.get(SYNC_FAILURE, 0), availability, reason),
        "connection_problems": indicator(
            sum(c for k, c in kinds.items() if k in CONNECTION_KINDS), availability, reason
        ),
        "items": [
            {
                "id": n.id,
                "created_at": n.created_at.isoformat(),
                "kind": n.kind,
                "severity": n.severity,
                "title": n.title,
                "message": n.message,
                "action": n.action,
                "client_id": n.client_id,
            }
            for n in problems[:20]
        ],
    }


def incident_series(
    read: NotificationRead | None, chosen: DashboardRange, days: list[str]
) -> dict[str, Any]:
    """Problems raised per day (month for a long range), from the notifications' creation date."""
    if read is None:
        return {"availability": "not_tracked", "reason": UNREADABLE, "points": []}
    first, last = chosen.first_day, chosen.last_day
    counts: Counter[str] = Counter()
    for n in read.items:
        if n.severity == INFO:
            continue
        day: date = n.created_at.astimezone(UTC).date()
        if first <= day <= last:
            key = day.isoformat() if chosen.granularity == "day" else day.isoformat()[:7]
            counts[key] += 1
    keys = days if chosen.granularity == "day" else sorted({d[:7] for d in days})
    return {
        "availability": MEASURED if read.complete else "estimated",
        "reason": None if read.complete else "only the most recent notifications were counted",
        "points": [{"period": key, "incidents": counts.get(key, 0)} for key in keys],
    }
