"""An indicator and how it is known. Every dashboard figure travels as `{value, availability}`.

- `measured`: counted or read from data Revansa holds (an empty tracked count is a real 0);
- `estimated`: approximate or partial (a lower bound, a flat tariff): the value is shown as such;
- `not_tracked`: the platform does not record it (or cannot read it right now): `value` is null and
  `reason` says why. It is never shown as 0.
"""

from typing import Any

from sales_app.metrics import NotTracked

MEASURED, ESTIMATED, NOT_TRACKED = "measured", "estimated", "not_tracked"
AVAILABILITIES = (MEASURED, ESTIMATED, NOT_TRACKED)


def indicator(
    value: Any, availability: str = MEASURED, reason: str | None = None
) -> dict[str, Any]:
    if availability not in AVAILABILITIES:
        raise ValueError(f"unknown availability {availability!r}")
    if availability == NOT_TRACKED:
        return {"value": None, "availability": NOT_TRACKED, "reason": reason or "not tracked"}
    out: dict[str, Any] = {"value": value, "availability": availability}
    if reason:
        out["reason"] = reason
    return out


def not_tracked(reason: str) -> dict[str, Any]:
    return indicator(None, NOT_TRACKED, reason)


def from_figure(value: float | NotTracked) -> dict[str, Any]:
    """A metrics figure (`int | float | NotTracked`) as an indicator."""
    if isinstance(value, NotTracked):
        return not_tracked(value.reason)
    return indicator(value)


def cost_availability(basis: str) -> str:
    """A cost basis as an availability: `unavailable` (no amount) is `not_tracked`."""
    return {"measured": MEASURED, "estimated": ESTIMATED}.get(basis, NOT_TRACKED)
