"""Today's exchange rates, for telling an offline buyer what a price comes to in their currency.

The rates come from a free, open source (open.er-api.com, mid-market rates refreshed daily, close to
the ones public converters such as Wise show). They are read at most once an hour, shared by every
client, and a failure is remembered for a few minutes: the conversation never waits on this and,
without a rate, simply says nothing about the local amount.
"""

import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx

RATES_URL = "https://open.er-api.com/v6/latest/USD"
_FRESH_FOR = 3600.0
_RETRY_AFTER = 300.0
_TIMEOUT = 3.0

type Rates = tuple[Mapping[str, float], datetime]

_lock = threading.Lock()
_cached: tuple[float, Rates | None] | None = None


def today_rates() -> Rates | None:
    """Units of each currency for one US dollar, and when they were read; None when unavailable."""
    global _cached
    with _lock:
        now = time.monotonic()
        if _cached is not None and now < _cached[0]:
            return _cached[1] or None
        try:
            body = httpx.get(RATES_URL, timeout=_TIMEOUT).raise_for_status().json()
            rates = {str(k).upper(): float(v) for k, v in body["rates"].items() if float(v) > 0}
            if body.get("result") != "success" or not rates:
                raise ValueError("no rates")
            found: Rates | None = (rates, datetime.now(UTC))
            _cached = (now + _FRESH_FOR, found)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Keep the last good rates while they are at most a day old, else nothing.
            previous = _cached[1] if _cached else None
            keep = previous if previous and (datetime.now(UTC) - previous[1]).days < 1 else None
            _cached = (now + _RETRY_AFTER, keep)
            found = keep
        return found
