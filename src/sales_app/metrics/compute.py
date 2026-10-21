"""The pure arithmetic of the metrics: folding, labelling and rates. No SQL, no clock, no I/O."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sales_app.metrics.contracts import UNASSIGNED, NotTracked
from sales_core.contracts import (
    ALL_PRODUCTS,
    PAYMENT_CLAIMED_KEY,
    PAYMENT_PROOF_KEY,
    Fact,
    FactStatus,
    PaymentProgress,
    payment_progress,
)


def labelled(counts: Mapping[str | None, int]) -> dict[str, int]:
    """Counts by key with the missing key named `unassigned`, ordered by size then key."""
    folded: dict[str, int] = {}
    for key, n in counts.items():
        label = UNASSIGNED if key in (None, "") else str(key)
        folded[label] = folded.get(label, 0) + n
    return dict(sorted(folded.items(), key=lambda kv: (-kv[1], kv[0])))


def fold_by_product(
    by_lane: Mapping[str | None, int], lane_products: Mapping[str, Sequence[str]]
) -> dict[str, int]:
    """Prospects by product, from the prospects by lane and the products each lane sells.

    A lane that sells several products counts its prospects under each of them (a prospect is
    offered all of them), so the sum can exceed the number of prospects. A lane that offers the
    whole catalog (no product listed, or `ALL`) is counted as `ALL`; a prospect with no lane, or in a
    lane the configuration no longer knows, is `unassigned`."""
    folded: dict[str, int] = {}
    for lane, n in by_lane.items():
        products = lane_products.get(lane) if lane is not None else None
        if products is None:
            keys: Iterable[str] = (UNASSIGNED,)
        elif not products or ALL_PRODUCTS in products:
            keys = (ALL_PRODUCTS,)
        else:
            keys = products
        for key in keys:
            folded[key] = folded.get(key, 0) + n
    return dict(sorted(folded.items(), key=lambda kv: (-kv[1], kv[0])))


def lanes_selling(product_id: str, lane_products: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """The lanes whose prospects are offered this product (the reverse of `fold_by_product`)."""
    return tuple(
        sorted(
            lane
            for lane, products in lane_products.items()
            if not products or ALL_PRODUCTS in products or product_id in products
        )
    )


def percent(part: int, whole: int) -> float | NotTracked:
    if whole <= 0:
        return NotTracked("nobody was contacted in the period")
    return round(100 * part / whole, 1)


def conversion_rate(conversions: int | NotTracked, contacted: int) -> float | NotTracked:
    """Conversions (declared or confirmed) over the prospects contacted; a hole stays a hole."""
    if isinstance(conversions, NotTracked):
        return conversions
    return percent(conversions, contacted)


def nested_labelled(rows: Mapping[tuple[str | None, str | None], int]) -> dict[str, dict[str, int]]:
    """{(direction, channel): n} as {channel: {direction: n}}."""
    result: dict[str, dict[str, int]] = {}
    for (direction, channel), n in rows.items():
        by_direction = result.setdefault(UNASSIGNED if not channel else channel, {})
        key = UNASSIGNED if not direction else direction
        by_direction[key] = by_direction.get(key, 0) + n
    return dict(sorted(result.items()))


def sent_and_received(rows: Mapping[tuple[str | None, str | None], int]) -> tuple[int, int]:
    sent = sum(n for (direction, _), n in rows.items() if direction == "outbound")
    received = sum(n for (direction, _), n in rows.items() if direction == "inbound")
    return sent, received


def payment_counts(facts_by_prospect: Mapping[str, Sequence[Fact]]) -> tuple[int, int, int]:
    """(declared, confirmed, awaiting) prospects, from each prospect's payment facts.

    Declared: the prospect said they paid or sent a receipt. Confirmed: a person at the client
    confirmed it (and only then). Awaiting: declared and not confirmed."""
    declared = confirmed = awaiting = 0
    for facts in facts_by_prospect.values():
        said = _declared(facts)
        paid = payment_progress(facts) is PaymentProgress.PAID
        declared += said
        confirmed += paid
        awaiting += said and not paid
    return declared, confirmed, awaiting


def _declared(facts: Iterable[Fact]) -> bool:
    return any(
        f.key in {PAYMENT_CLAIMED_KEY, PAYMENT_PROOF_KEY} and f.status is not FactStatus.HYPOTHESIS
        for f in facts
    )


def day_buckets(rows: Mapping[str, Mapping[Any, int]]) -> dict[str, dict[str, int]]:
    """{day: {direction: n}} in date order, a missing direction written as `unassigned`."""
    return {
        day: {(UNASSIGNED if not d else str(d)): n for d, n in sorted(by.items(), key=str)}
        for day, by in sorted(rows.items())
    }
