"""Port for the current time, so scheduling and tests never depend on the wall clock."""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a timezone-aware datetime."""
        ...
