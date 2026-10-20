"""A client's metrics, composed from a reader of counts and the lanes' products.

`MetricsService` is what dashboards call: `client_metrics(period)`. It depends on a `MetricsReader`
(the SQL adapter `sales_app.persistence.metrics_rows.SqlMetricsRows` in production, a fake in tests)
and never on the Engine, so it answers whether or not the agent is loaded.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from sales_app.metrics import compute
from sales_app.metrics.contracts import ClientMetrics, NotTracked, PaymentFigures, Period
from sales_app.sourcing import store_codec
from sales_core.contracts import (
    CONFIRMATION_KEYS,
    UNVERIFIED_CLAIM_KEYS,
    Fact,
)

PAYMENT_KEYS = tuple(sorted(UNVERIFIED_CLAIM_KEYS | CONFIRMATION_KEYS))


class MetricsReader(Protocol):
    """The counts, for one client. See `sales_app.persistence.metrics_rows.SqlMetricsRows`."""

    def prospect_counts(
        self, by: str, *, since: datetime | None = None, until: datetime | None = None
    ) -> dict[str | None, int]: ...
    def prospect_total(self) -> int: ...
    def needs_human(self) -> int: ...
    def message_counts(
        self, *, since: datetime | None, until: datetime | None
    ) -> dict[tuple[str | None, str | None], int]: ...
    def distinct_prospects_by_direction(
        self, *, since: datetime | None, until: datetime | None
    ) -> dict[str | None, int]: ...
    def distinct_prospects_with_messages(
        self, *, since: datetime | None, until: datetime | None
    ) -> int: ...
    def messages_by_day(
        self, *, since: datetime | None, until: datetime | None
    ) -> dict[str, dict[str | None, int]]: ...
    def prospects_with_failed_actions(
        self, *, since: datetime | None, until: datetime | None
    ) -> int: ...
    def fact_documents(
        self, keys: tuple[str, ...], *, since: datetime | None, until: datetime | None
    ) -> list[tuple[str, Any]]: ...


class MetricsService:
    def __init__(
        self,
        reader: MetricsReader,
        lane_products: Callable[[], Mapping[str, Sequence[str]]] = dict,
    ) -> None:
        """`lane_products` gives lane id -> the products it sells (empty: the whole catalog), read
        from the client's settings each time it is asked."""
        self._reader = reader
        self._lane_products = lane_products

    def client_metrics(self, period: Period | None = None) -> ClientMetrics:
        period = period or Period()
        since, until = period.since, period.until
        r = self._reader
        by_lane = r.prospect_counts("lane", since=since, until=until)
        sent, received = compute.sent_and_received(r.message_counts(since=since, until=until))
        directions = r.distinct_prospects_by_direction(since=since, until=until)
        contacted = directions.get("outbound", 0)
        replied = directions.get("inbound", 0)
        return ClientMetrics(
            period=period,
            prospects_total=sum(r.prospect_counts("status", since=since, until=until).values())
            if (since or until)
            else r.prospect_total(),
            by_status=compute.labelled(r.prospect_counts("status", since=since, until=until)),
            by_lane=compute.labelled(by_lane),
            by_product=compute.fold_by_product(by_lane, self._lane_products()),
            by_channel=compute.labelled(r.prospect_counts("channel", since=since, until=until)),
            by_source=compute.labelled(r.prospect_counts("source", since=since, until=until)),
            prospects_contacted=contacted,
            conversations=r.distinct_prospects_with_messages(since=since, until=until),
            messages_sent=sent,
            replies_received=received,
            prospects_replied=replied,
            reply_rate_percent=compute.percent(replied, contacted),
            needs_human=r.needs_human(),
            with_errors=r.prospects_with_failed_actions(since=since, until=until),
            messages_by_day=compute.day_buckets(r.messages_by_day(since=since, until=until)),
            messages_by_channel=compute.nested_labelled(r.message_counts(since=since, until=until)),
            conversions=NotTracked(
                "no conversion event is recorded; confirmed payments are under `payments`"
            ),
            payments=self._payments(period),
        )

    def _payments(self, period: Period) -> PaymentFigures:
        grouped: dict[str, list[Fact]] = {}
        for prospect_id, document in self._reader.fact_documents(
            PAYMENT_KEYS, since=period.since, until=period.until
        ):
            try:
                grouped.setdefault(prospect_id, []).append(store_codec.fact_from_doc(document))
            except (KeyError, TypeError, ValueError):
                continue  # a record that cannot be read is not counted, never guessed
        declared, confirmed, awaiting = compute.payment_counts(grouped)
        return PaymentFigures(
            declared=declared, confirmed=confirmed, awaiting_confirmation=awaiting
        )
