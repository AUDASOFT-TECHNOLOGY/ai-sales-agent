"""Controllable clock for tests."""

from datetime import UTC, datetime, timedelta

DEFAULT_START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock time must be timezone-aware")


class FakeClock:
    """Implements `sales_core.ports.Clock`. Time only moves when the test says so."""

    def __init__(self, start: datetime = DEFAULT_START) -> None:
        _require_aware(start)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        """Jump to an absolute time (forwards or backwards)."""
        _require_aware(moment)
        self._now = moment

    def advance(self, delta: timedelta) -> datetime:
        """Move forward by `delta` and return the new time."""
        if delta < timedelta(0):
            raise ValueError("advance() cannot go backwards; use set()")
        self._now += delta
        return self._now
