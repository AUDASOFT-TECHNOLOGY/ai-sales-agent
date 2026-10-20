"""The period a dashboard covers: `from` / `to` as months (`YYYY-MM`) or days (`YYYY-MM-DD`).

Default: the current month and the five before it. The range is inclusive on both ends and capped
at 24 months.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

MAX_MONTHS = 24
DAILY_UP_TO_DAYS = 92  # a longer range is drawn by month
_TEXT = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


class InvalidRangeError(ValueError):
    pass


def _parse(text: str, *, end: bool) -> date:
    match = _TEXT.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise InvalidRangeError("dates must be YYYY-MM or YYYY-MM-DD")
    year, month, day = int(match[1]), int(match[2]), match[3]
    try:
        if day is not None:
            return date(year, month, int(day))
        first = date(year, month, 1)
    except ValueError:
        raise InvalidRangeError("the date is not a real date") from None
    if not end:
        return first
    following = date(year + month // 12, month % 12 + 1, 1)
    return following - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DashboardRange:
    first_day: date  # inclusive
    last_day: date  # inclusive

    @property
    def since(self) -> datetime:
        return datetime(self.first_day.year, self.first_day.month, self.first_day.day, tzinfo=UTC)

    @property
    def until(self) -> datetime:
        nxt = self.last_day + timedelta(days=1)
        return datetime(nxt.year, nxt.month, nxt.day, tzinfo=UTC)

    @property
    def days(self) -> int:
        return (self.last_day - self.first_day).days + 1

    @property
    def granularity(self) -> str:
        return "day" if self.days <= DAILY_UP_TO_DAYS else "month"

    def months(self) -> list[str]:
        out: list[str] = []
        year, month = self.first_day.year, self.first_day.month
        while (year, month) <= (self.last_day.year, self.last_day.month):
            out.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return out

    def label(self) -> dict[str, str]:
        return {"from": self.first_day.isoformat(), "to": self.last_day.isoformat()}


def resolve_range(from_: str | None, to: str | None, now: datetime) -> DashboardRange:
    today = now.astimezone(UTC).date()
    last = _parse(to, end=True) if to else today
    if from_:
        first = _parse(from_, end=False)
    else:
        index = last.year * 12 + last.month - 1 - 5  # five months before `last`'s month
        first = date(index // 12, index % 12 + 1, 1)
    if first > last:
        raise InvalidRangeError("the range must end after it starts")
    chosen = DashboardRange(first, last)
    if len(chosen.months()) > MAX_MONTHS:
        raise InvalidRangeError(f"the range is at most {MAX_MONTHS} months")
    return chosen


def days_of(chosen: DashboardRange, today: date) -> list[str]:
    """Every day of the range up to today (a day not reached yet has no figure at all)."""
    end = min(chosen.last_day, today)
    out: list[str] = []
    day = chosen.first_day
    while day <= end:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out
