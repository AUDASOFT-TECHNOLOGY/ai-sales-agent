"""The shape of a client's figures, as dashboards consume them. Pure data: no SQL, no I/O.

Two rules hold everywhere:

- a figure the platform does not track is `NotTracked(reason)`, never zero, never an estimate;
- a figure the platform tracks but that is empty is a real `0`.

JSON form (`to_json`): a count is an integer, a figure not tracked is
`{"tracked": false, "reason": "..."}`. Keys of groupings that are `None` in the data (a prospect
with no lane, no channel) are written `"unassigned"`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

UNASSIGNED = "unassigned"


@dataclass(frozen=True, slots=True)
class NotTracked:
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"tracked": False, "reason": self.reason}


Figure = int | NotTracked


def figure_json(value: Figure | float) -> Any:
    return value.to_json() if isinstance(value, NotTracked) else value


@dataclass(frozen=True, slots=True, kw_only=True)
class Period:
    """`[since, until)`; a bound left None is open. Both must be timezone-aware."""

    since: datetime | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        for moment in (self.since, self.until):
            if moment is not None and moment.tzinfo is None:
                raise ValueError("a period bound must be timezone-aware")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("the period must end after it starts")


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentFigures:
    """What buyers said versus what the client confirmed. Revansa is not a payment gateway: a
    declared payment is only what the buyer said or sent; only a confirmation by a person at the
    client counts as confirmed (see `sales_core.contracts.payment`)."""

    declared: Figure  # prospects who said they paid or sent a receipt
    confirmed: Figure  # prospects whose payment a person at the client confirmed
    awaiting_confirmation: Figure  # declared and not yet confirmed


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientMetrics:
    period: Period
    # Snapshot of the prospects the platform holds (filtered by last activity when a period is set).
    prospects_total: Figure
    by_status: dict[str, int]
    by_lane: dict[str, int]  # the "circuit"
    by_product: dict[
        str, int
    ]  # derived from the lanes' products; a lane selling several counts in each
    by_channel: dict[str, int]
    by_source: dict[str, int]
    # Activity in the period.
    prospects_contacted: Figure  # different prospects who received at least one message from us
    conversations: Figure  # different prospects with at least one message exchanged
    messages_sent: Figure
    replies_received: Figure  # messages received from prospects
    prospects_replied: Figure  # different prospects who wrote back
    reply_rate_percent: (
        float | NotTracked
    )  # prospects_replied / prospects_contacted; NotTracked if none contacted
    needs_human: Figure  # prospects waiting for a person (a snapshot, not a period figure)
    with_errors: Figure  # prospects with a failed action in the period
    messages_by_day: dict[str, dict[str, int]] = field(default_factory=dict)
    messages_by_channel: dict[str, dict[str, int]] = field(default_factory=dict)
    # A conversion is a prospect's payment. DECLARED: the prospect said they paid (never counted as
    # confirmed). CONFIRMED (real): a person at the client confirmed it. Rates are over the
    # prospects contacted in the period (NotTracked when nobody was contacted).
    conversions_declared: Figure = field(
        default_factory=lambda: NotTracked("payments are not recorded")
    )
    conversions_confirmed: Figure = field(
        default_factory=lambda: NotTracked("payments are not recorded")
    )
    conversion_rate_declared_percent: float | NotTracked = field(
        default_factory=lambda: NotTracked("payments are not recorded")
    )
    conversion_rate_confirmed_percent: float | NotTracked = field(
        default_factory=lambda: NotTracked("payments are not recorded")
    )
    payments: PaymentFigures = field(
        default_factory=lambda: PaymentFigures(
            declared=NotTracked("payments are not recorded"),
            confirmed=NotTracked("payments are not recorded"),
            awaiting_confirmation=NotTracked("payments are not recorded"),
        )
    )

    def to_json(self) -> dict[str, Any]:
        return {
            "period": {
                "since": self.period.since.isoformat() if self.period.since else None,
                "until": self.period.until.isoformat() if self.period.until else None,
            },
            "prospects_total": figure_json(self.prospects_total),
            "by_status": self.by_status,
            "by_lane": self.by_lane,
            "by_product": self.by_product,
            "by_channel": self.by_channel,
            "by_source": self.by_source,
            "prospects_contacted": figure_json(self.prospects_contacted),
            "conversations": figure_json(self.conversations),
            "messages_sent": figure_json(self.messages_sent),
            "replies_received": figure_json(self.replies_received),
            "prospects_replied": figure_json(self.prospects_replied),
            "reply_rate_percent": figure_json(self.reply_rate_percent),
            "needs_human": figure_json(self.needs_human),
            "with_errors": figure_json(self.with_errors),
            "messages_by_day": self.messages_by_day,
            "messages_by_channel": self.messages_by_channel,
            "conversions_declared": figure_json(self.conversions_declared),
            "conversions_confirmed": figure_json(self.conversions_confirmed),
            "conversion_rate_declared_percent": figure_json(self.conversion_rate_declared_percent),
            "conversion_rate_confirmed_percent": figure_json(
                self.conversion_rate_confirmed_percent
            ),
            "payments": {
                "declared": figure_json(self.payments.declared),
                "confirmed": figure_json(self.payments.confirmed),
                "awaiting_confirmation": figure_json(self.payments.awaiting_confirmation),
            },
        }
