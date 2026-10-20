"""Merging what other lots compute into dashboard blocks. Pure: no store, no clock, no guess.

Rules that hold here:

- a sum of figures where one is `NotTracked` is `NotTracked` (a hole is not a zero);
- currencies are never added together;
- the margin (collected minus technical costs) exists only when the costs are complete (no cost
  line without an amount) and are in currencies that also have revenue (there is no conversion);
  its `basis` says whether those costs are `measured` or include `estimated` ones. Otherwise the
  profitability is `partial` (what is known is shown, no margin) or `unavailable`.
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from sales_app.dashboards.figures import (
    ESTIMATED,
    MEASURED,
    NOT_TRACKED,
    cost_availability,
    from_figure,
    not_tracked,
)
from sales_app.metrics import ClientMetrics, NotTracked, compute
from sales_app.provider_costs import CostReport

CONVERSIONS_REASON = "no conversion event is recorded; confirmed payments are shown under payments"


def sum_figures(values: Iterable[int | NotTracked]) -> int | NotTracked:
    total = 0
    for value in values:
        if isinstance(value, NotTracked):
            return value
        total += value
    return total


def sum_counts(maps: Iterable[Mapping[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for one in maps:
        for key, count in one.items():
            out[key] = out.get(key, 0) + count
    return dict(sorted(out.items()))


def sum_nested(maps: Iterable[Mapping[str, Mapping[str, int]]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for one in maps:
        for key, inner in one.items():
            out[key] = sum_counts([out.get(key, {}), inner])
    return dict(sorted(out.items()))


def activity_block(metrics: Sequence[ClientMetrics]) -> dict[str, Any]:
    """The commercial activity of one or several clients, as indicators plus the groupings."""
    contacted = sum_figures(m.prospects_contacted for m in metrics)
    replied = sum_figures(m.prospects_replied for m in metrics)
    if isinstance(contacted, NotTracked) or isinstance(replied, NotTracked):
        rate: float | NotTracked = NotTracked("the counts are not available")
    else:
        rate = compute.percent(replied, contacted)
    return {
        "prospects_total": from_figure(sum_figures(m.prospects_total for m in metrics)),
        "prospects_contacted": from_figure(contacted),
        "conversations": from_figure(sum_figures(m.conversations for m in metrics)),
        "messages_sent": from_figure(sum_figures(m.messages_sent for m in metrics)),
        "replies_received": from_figure(sum_figures(m.replies_received for m in metrics)),
        "prospects_replied": from_figure(replied),
        "reply_rate_percent": from_figure(rate),
        "needs_human": from_figure(sum_figures(m.needs_human for m in metrics)),
        "with_errors": from_figure(sum_figures(m.with_errors for m in metrics)),
        "conversions": not_tracked(CONVERSIONS_REASON),
        "payments": {
            "declared": from_figure(sum_figures(m.payments.declared for m in metrics)),
            "confirmed": from_figure(sum_figures(m.payments.confirmed for m in metrics)),
            "awaiting_confirmation": from_figure(
                sum_figures(m.payments.awaiting_confirmation for m in metrics)
            ),
        },
        "by_status": sum_counts(m.by_status for m in metrics),
        "by_circuit": sum_counts(m.by_lane for m in metrics),
        "by_product": sum_counts(m.by_product for m in metrics),
        "by_channel": sum_counts(m.by_channel for m in metrics),
        "by_source": sum_counts(m.by_source for m in metrics),
        "messages_by_channel": sum_nested(m.messages_by_channel for m in metrics),
    }


def activity_series(
    metrics: Sequence[ClientMetrics], days: Sequence[str], granularity: str
) -> list[dict[str, Any]]:
    """Messages sent / received per day (or per month for a long range). Days in `days` with no
    message are real zeros (the source is tracked); `days` stops at today."""
    per_day: dict[str, dict[str, int]] = {d: {"sent": 0, "received": 0} for d in days}
    for one in metrics:
        for day, by_direction in one.messages_by_day.items():
            if day not in per_day:
                continue
            per_day[day]["sent"] += by_direction.get("outbound", 0)
            per_day[day]["received"] += by_direction.get("inbound", 0)
    if granularity == "day":
        return [{"period": day, **values} for day, values in per_day.items()]
    months: dict[str, dict[str, int]] = {}
    for day, values in per_day.items():
        bucket = months.setdefault(day[:7], {"sent": 0, "received": 0})
        bucket["sent"] += values["sent"]
        bucket["received"] += values["received"]
    return [{"period": month, **values} for month, values in months.items()]


def performance_point(period: str, metrics: Sequence[ClientMetrics]) -> dict[str, Any]:
    """One month of the performance series (distinct prospects, so months are not summed)."""
    block = activity_block(metrics)
    return {
        "period": period,
        "prospects_contacted": block["prospects_contacted"],
        "prospects_replied": block["prospects_replied"],
        "reply_rate_percent": block["reply_rate_percent"],
        "conversions": block["conversions"],
    }


# ---- costs ------------------------------------------------------------------------------------


def _money(value: Decimal) -> str:
    return format(value, "f")


def _combine_basis(bases: Iterable[str]) -> str:
    seen = set(bases)
    if not seen or seen == {MEASURED}:
        return MEASURED
    if seen == {"unavailable"}:
        return "unavailable"
    return ESTIMATED


def _add_totals(a: dict[str, Decimal], b: Mapping[str, Decimal]) -> dict[str, Decimal]:
    out = dict(a)
    for currency, amount in b.items():
        out[currency] = out.get(currency, Decimal(0)) + amount
    return out


def merge_costs(reports: Sequence[CostReport]) -> dict[str, Any]:
    """Several cost reports (months, clients) as one: per provider basis, totals and completeness."""
    providers: dict[str, dict[str, Any]] = {}
    for report in reports:
        for p in report.providers:
            into = providers.setdefault(
                p.provider,
                {"bases": [], "totals": {}, "lines": 0, "unavailable_lines": 0},
            )
            if p.lines:  # a provider with no line in a month says nothing about its basis
                into["bases"].append(p.basis)
            into["totals"] = _add_totals(into["totals"], p.totals)
            into["lines"] += p.lines
            into["unavailable_lines"] += p.unavailable_lines
    shown = []
    all_totals: dict[str, Decimal] = {}
    for name, p in providers.items():
        basis = _combine_basis(p["bases"])
        if p["lines"] and p["lines"] == p["unavailable_lines"]:
            basis = "unavailable"
        all_totals = _add_totals(all_totals, p["totals"])
        shown.append(
            {
                "provider": name,
                "basis": basis,
                "availability": cost_availability(basis),
                "totals": {c: _money(v) for c, v in sorted(p["totals"].items())},
                "lines": p["lines"],
                "unavailable_lines": p["unavailable_lines"],
                "complete": p["unavailable_lines"] == 0,
            }
        )
    lines = sum(p["lines"] for p in providers.values())
    with_amount = [p for p in shown if p["totals"]]
    basis = _combine_basis(p["basis"] for p in shown if p["lines"])
    if lines and not with_amount:
        basis = "unavailable"
    return {
        "providers": shown,
        "basis": basis,
        "availability": cost_availability(basis) if lines else NOT_TRACKED,
        "complete": all(p["complete"] for p in shown),
        "lines": lines,
        "totals": {c: _money(v) for c, v in sorted(all_totals.items())},
        "_decimal_totals": all_totals,
    }


def public_costs(merged: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in merged.items() if not k.startswith("_")}


def cost_series_point(period: str, merged: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": period,
        "by_provider": [
            {
                "provider": p["provider"],
                "basis": p["basis"],
                "availability": p["availability"],
                "totals": p["totals"],
            }
            for p in merged["providers"]
            if p["lines"]
        ],
        "totals": merged["totals"],
        "basis": merged["basis"],
        "availability": merged["availability"],
        "complete": merged["complete"],
    }


def profitability(collected: Mapping[str, Decimal], costs: dict[str, Any]) -> dict[str, Any]:
    """collected (per currency) minus the technical costs, only when the costs allow it."""
    revenue = {c: _money(v) for c, v in sorted(collected.items())}
    known: Mapping[str, Decimal] = costs.get("_decimal_totals", {})
    base = {"revenue": revenue, "costs_known": {c: _money(v) for c, v in sorted(known.items())}}
    if not costs["lines"] or costs["availability"] == NOT_TRACKED or not known:
        return {
            "status": "unavailable",
            "reason": "no technical cost with an amount is recorded for the period",
            "margin": {},
            **base,
        }
    if not costs["complete"]:
        return {
            "status": "partial",
            "reason": "some costs are unavailable: the known costs are a lower bound",
            "margin": {},
            **base,
        }
    base["basis"] = costs["basis"]  # `estimated` when a cost is a flat tariff (e.g. WhatsApp)
    stray = sorted(set(known) - set(collected))
    if stray:
        return {
            "status": "partial",
            "reason": f"costs in {', '.join(stray)} have no revenue in that currency (no conversion)",
            "margin": {},
            **base,
        }
    margin = {c: _money(collected[c] - known.get(c, Decimal(0))) for c in sorted(collected)}
    return {"status": "available", "reason": None, "margin": margin, **base}
