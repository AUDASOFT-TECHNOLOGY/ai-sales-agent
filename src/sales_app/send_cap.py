"""The mandatory daily send cap: no outgoing message leaves without a valid cap.

One control for every e-mail mode (a Google mailbox and the platform's dedicated mailbox share it),
placed around the channel so a mode cannot forget it. Rules, all fail-closed:

- The cap is set by the Admin for one client. There is no default and no "unlimited": no cap, or a
  value that is not a whole number of at least 1, refuses every send.
- A send first RESERVES one place for the UTC day in a counter shared by every process (the SQL
  counter: `sales_app.persistence.send_cap_rows`); the database decides atomically, so concurrent
  senders can never go past the cap. The counter is per client, channel and UTC day.
- A store that cannot be reached refuses the send. Nothing is sent "to be counted later".
- A place is given back only when the failure is certain (the channel returned a failed result).
  If the channel raises, the message may have left: the place stays used. Over-counting is safe,
  exceeding the cap is not.

This module knows no database: it holds the ports, the guard, the channel wrapper and in-memory
doubles that are for tests only (a restart forgets them). The Admin may set a cap above the plan's
quota; it is the effective cap (below) that is applied, never the cap alone.

**The effective cap** (`SendLimits`) is `min(the Admin's cap, the daily quota of the client's current
plan)`. A plan is not required to send: a client with no plan is held by the Admin's cap alone; a
client with a plan can never send more than its quota, whatever the cap says. The Admin's cap itself
is always required. If the plan cannot be read (store down, plan gone) the send is refused.

A refusal is reported as a transient failure marked "deferred" (`DEFERRED_KEY`): the Engine keeps the
action approved with the reason visible and does not count the try, so a day's worth of refusals
never burns an action's attempts; the action goes out on a later dispatch once a place is free.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.ports import Clock

log = logging.getLogger(__name__)

CAP_NOT_CONFIGURED = "send_cap_not_configured"
CAP_REACHED = "send_cap_reached"
CAP_UNAVAILABLE = "send_cap_unavailable"

_REFUSAL_TEXTS = {
    CAP_NOT_CONFIGURED: "the daily send cap is not configured",
    CAP_REACHED: "the daily send cap is reached",
    CAP_UNAVAILABLE: "the daily send cap cannot be checked",
}


MAX_SEND_CAP = 100_000  # same ceiling as a plan's daily quota (`sales_app.plans.MAX_DAILY_QUOTA`)


class InvalidSendCapError(ValueError):
    """A cap that is not a whole number of at least 1."""


def valid_cap(value: object) -> int | None:
    """The cap if it is a whole number of at least 1 (a bool, a string or a float is not), else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def check_cap(value: object) -> int:
    cap = valid_cap(value)
    if cap is None or cap > MAX_SEND_CAP:
        raise InvalidSendCapError(
            f"the daily send cap must be a whole number from 1 to {MAX_SEND_CAP}"
        )
    return cap


def utc_day(at: datetime) -> date:
    return at.astimezone(UTC).date()


class SendCounter(Protocol):
    """Atomic count of the sends of one client, on one channel, on one UTC day."""

    def try_increment(self, client_id: str, channel: str, day: date, limit: int) -> bool:
        """Take one place if fewer than `limit` are used; False if none is left. Atomic."""
        ...

    def decrement(self, client_id: str, channel: str, day: date) -> None:
        """Give one place back (never below zero)."""
        ...

    def used(self, client_id: str, channel: str, day: date) -> int: ...


class SendCapStore(Protocol):
    """The cap the Admin set for each client. No row: no cap."""

    def get(self, client_id: str) -> int | None: ...

    def set(self, client_id: str, cap: int, *, by: str, at: datetime) -> None:
        """Raises `InvalidSendCapError` for anything but a whole number of at least 1."""
        ...

    def remove(self, client_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Reservation:
    day: date


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str


class DailySendGuard:
    """Reserves one place of a client's channel for today (UTC), or says why not."""

    def __init__(
        self,
        client_id: str,
        channel: str,
        limit: Callable[[], int | None],
        clock: Clock,
        counter: SendCounter,
    ) -> None:
        if not client_id or not channel:
            raise ValueError("a send guard needs a client id and a channel")
        self._client_id = client_id
        self._channel = channel
        self._limit = limit
        self._clock = clock
        self._counter = counter

    def __repr__(self) -> str:
        return f"DailySendGuard(client={self._client_id!r}, channel={self._channel!r})"

    def reserve(self) -> Reservation | Refusal:
        try:
            limit = valid_cap(self._limit())
            if limit is None:
                return Refusal(CAP_NOT_CONFIGURED)
            day = utc_day(self._clock.now())
            if not self._counter.try_increment(self._client_id, self._channel, day, limit):
                return Refusal(CAP_REACHED)
        except Exception as error:  # noqa: BLE001 - a store down, whatever its type: refuse, never send
            log.warning("send cap check failed: %s", type(error).__name__)
            return Refusal(CAP_UNAVAILABLE)
        return Reservation(day)

    def release(self, reservation: Reservation) -> None:
        """Give a place back. If the store is down the place stays used: that is the safe side."""
        try:
            self._counter.decrement(self._client_id, self._channel, reservation.day)
        except Exception as error:  # noqa: BLE001 - the place stays used: the safe side
            log.warning("send cap release failed: %s", type(error).__name__)


class SendCapChannel:
    """Any `MessagingChannel`, behind the cap. Nothing reaches the inner channel without a place."""

    def __init__(self, inner, guard: DailySendGuard) -> None:
        self._inner = inner
        self._guard = guard

    def __repr__(self) -> str:
        return "SendCapChannel()"

    @property
    def channel(self) -> Channel:
        return self._inner.channel

    def send(self, message: OutboundMessage) -> SendResult:
        reserved = self._guard.reserve()
        if isinstance(reserved, Refusal):
            return SendResult(
                status=SendStatus.FAILED_TRANSIENT,
                timestamp=datetime.now(UTC),
                error_code=reserved.code,
                error_message=_REFUSAL_TEXTS[reserved.code],
                metadata={DEFERRED_KEY: DEFERRED_VALUE},
            )
        # If `send` raises, the message may have left: the place stays used on purpose.
        result = self._inner.send(message)
        if not result.ok:
            self._guard.release(reserved)
        return result


@dataclass(frozen=True, slots=True)
class EffectiveCap:
    """What limits a client's e-mails today. `effective` is None when no valid Admin cap exists."""

    admin_cap: int | None
    plan_quota: int | None
    effective: int | None


def combine_caps(admin_cap: object, plan_quota: object) -> EffectiveCap:
    """`min(Admin cap, plan quota)`. No valid Admin cap: no effective cap. A plan quota that is not a
    whole number of at least 1 is a plan we cannot trust: no effective cap either (fail closed)."""
    cap = valid_cap(admin_cap)
    if plan_quota is None:
        return EffectiveCap(cap, None, cap)
    quota = valid_cap(plan_quota)
    if cap is None or quota is None:
        return EffectiveCap(cap, quota, None)
    return EffectiveCap(cap, quota, min(cap, quota))


class SendLimits:
    """Cap + plan + counter of a deployment: one object from which every e-mail path (prospecting in
    both modes, replies, follow-ups, the daily report) takes its guard and its figures."""

    def __init__(
        self,
        caps: SendCapStore,
        counter: SendCounter,
        clock: Clock,
        plan_quota: Callable[[str], int | None] | None = None,
    ) -> None:
        self._caps = caps
        self._counter = counter
        self._clock = clock
        self._plan_quota = plan_quota  # None: this deployment has no plans

    def __repr__(self) -> str:
        return "SendLimits()"

    def cap_of(self, client_id: str) -> EffectiveCap:
        """Raises whatever the stores raise: the callers turn it into a refusal / "unavailable"."""
        quota = self._plan_quota(client_id) if self._plan_quota is not None else None
        return combine_caps(self._caps.get(client_id), quota)

    def effective(self, client_id: str) -> int | None:
        return self.cap_of(client_id).effective

    def guard(self, client_id: str, channel: str) -> DailySendGuard:
        return DailySendGuard(
            client_id, channel, lambda: self.effective(client_id), self._clock, self._counter
        )

    def used(self, client_id: str, channel: str, day: date) -> int:
        return self._counter.used(client_id, channel, day)


class RefusingChannel:
    """Stands in for a metered channel that has no cap control wired: nothing is ever sent."""

    def __init__(self, inner_channel: Channel) -> None:
        self._channel = inner_channel

    def __repr__(self) -> str:
        return "RefusingChannel()"

    @property
    def channel(self) -> Channel:
        return self._channel

    def send(self, message: OutboundMessage) -> SendResult:
        return SendResult(
            status=SendStatus.FAILED_TRANSIENT,
            timestamp=datetime.now(UTC),
            error_code=CAP_UNAVAILABLE,
            error_message=_REFUSAL_TEXTS[CAP_UNAVAILABLE],
            metadata={DEFERRED_KEY: DEFERRED_VALUE},
        )


class InMemorySendCounter:
    """FOR TESTS ONLY: a restart forgets it and processes do not share it."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, date], int] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "InMemorySendCounter()"

    def try_increment(self, client_id: str, channel: str, day: date, limit: int) -> bool:
        if valid_cap(limit) is None:
            return False
        with self._lock:
            used = self._counts.get((client_id, channel, day), 0)
            if used >= limit:
                return False
            self._counts[(client_id, channel, day)] = used + 1
            return True

    def decrement(self, client_id: str, channel: str, day: date) -> None:
        with self._lock:
            key = (client_id, channel, day)
            if self._counts.get(key, 0) > 0:
                self._counts[key] -= 1

    def used(self, client_id: str, channel: str, day: date) -> int:
        with self._lock:
            return self._counts.get((client_id, channel, day), 0)


class InMemorySendCapStore:
    """FOR TESTS ONLY."""

    def __init__(self) -> None:
        self._caps: dict[str, int] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "InMemorySendCapStore()"

    def get(self, client_id: str) -> int | None:
        with self._lock:
            return self._caps.get(client_id)

    def set(self, client_id: str, cap: int, *, by: str, at: datetime) -> None:
        value = check_cap(cap)
        with self._lock:
            self._caps[client_id] = value

    def remove(self, client_id: str) -> bool:
        with self._lock:
            return self._caps.pop(client_id, None) is not None
