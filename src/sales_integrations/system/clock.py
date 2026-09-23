"""The real clock."""

from datetime import UTC, datetime


class SystemClock:
    """Implements `sales_core.ports.Clock` with the wall clock, always timezone-aware (UTC)."""

    def now(self) -> datetime:
        return datetime.now(UTC)
