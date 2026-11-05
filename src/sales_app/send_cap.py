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
quota.

**The effective cap** (`SendLimits`) is the Admin's cap, always required: it bounds every e-mail a
client sends (replies and follow-ups included).

**The commercial quota** is a different limit, the plan's daily quota of *new commercial contacts*:
the first message of a first-contact action (`OutboundMessage.new_contact`). Follow-ups, reminders,
callbacks and replies are never counted against it, and it does not touch the credits. It is counted
(`NewContactQuotaChannel`) on any channel, in the same shared counter under `COMMERCIAL_CHANNEL`,
only when the message is really accepted; a client on no plan has no such quota. If the plan cannot
be read (store down, plan gone) a new contact is refused.

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
UPGRADE_REQUIRED = "upgrade_required"
COMMERCIAL_QUOTA_REACHED = "commercial_quota_reached"
COMMERCIAL_CHANNEL = "commercial_new"  # the counter's channel name: not a channel, a count of contacts

_REFUSAL_TEXTS = {
    CAP_NOT_CONFIGURED: "the daily send cap is not configured",
    CAP_REACHED: "the daily send cap is reached",
    CAP_UNAVAILABLE: "the daily send cap cannot be checked",
    UPGRADE_REQUIRED: "a free account cannot send: upgrade your plan to activate your agent",
    COMMERCIAL_QUOTA_REACHED: "the daily quota of new commercial contacts is reached",
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


# (client id, channel, UTC day, effective limit, refused): called after each reservation attempt.
QuotaObserver = Callable[[str, str, date, int, bool], None]


class SendCounter(Protocol):
    """Atomic count of the sends of one client, on one channel, on one UTC day."""

    def try_increment(self, client_id: str, channel: str, day: date, limit: int) -> bool:
        """Take one place if fewer than `limit` are used; False if none is left. Atomic."""
        ...

    def decrement(self, client_id: str, channel: str, day: date) -> None:
        """Give one place back (never below zero)."""
        ...

    def used(self, client_id: str, channel: str, day: date) -> int: ...

    def usage_between(
        self, client_id: str, start: date, end: date
    ) -> dict[tuple[str, date], int]:
        """Counts per (channel, day) for days in [start, end)."""
        ...


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
        observer: "QuotaObserver | None" = None,
    ) -> None:
        if not client_id or not channel:
            raise ValueError("a send guard needs a client id and a channel")
        self._client_id = client_id
        self._channel = channel
        self._limit = limit
        self._clock = clock
        self._counter = counter
        self._observer = observer

    def __repr__(self) -> str:
        return f"DailySendGuard(client={self._client_id!r}, channel={self._channel!r})"

    def reserve(self) -> Reservation | Refusal:
        try:
            limit = valid_cap(self._limit())
            if limit is None:
                return Refusal(CAP_NOT_CONFIGURED)
            day = utc_day(self._clock.now())
            if not self._counter.try_increment(self._client_id, self._channel, day, limit):
                self._observe(day, limit, refused=True)
                return Refusal(CAP_REACHED)
        except Exception as error:  # noqa: BLE001 - a store down, whatever its type: refuse, never send
            log.warning("send cap check failed: %s", type(error).__name__)
            return Refusal(CAP_UNAVAILABLE)
        self._observe(day, limit, refused=False)
        return Reservation(day)

    def _observe(self, day: date, limit: int, *, refused: bool) -> None:
        """Tell the quota observer (alerts at 80% / 100%). It never changes the guard's answer."""
        if self._observer is None:
            return
        try:
            self._observer(self._client_id, self._channel, day, limit, refused)
        except Exception as error:  # noqa: BLE001 - an alert that fails never blocks or allows a send
            log.warning("quota observer failed: %s", type(error).__name__)

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


class CommercialGateChannel:
    """Any `MessagingChannel`, behind the Free-account rule: nothing reaches the inner channel while
    the client may not act commercially, whichever channel it is. If the rule cannot be read the
    message is refused too. The refusal is deferred and un-counted, like the cap's."""

    def __init__(self, inner, client_id: str, allowed: Callable[[str], bool]) -> None:
        self._inner = inner
        self._client_id = client_id
        self._allowed = allowed

    def __repr__(self) -> str:
        return "CommercialGateChannel()"

    @property
    def channel(self) -> Channel:
        return self._inner.channel

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            allowed = self._allowed(self._client_id)
        except Exception as error:  # noqa: BLE001 - an unreadable rule never means "send"
            log.warning("commercial access check failed: %s", type(error).__name__)
            allowed = False
        if not allowed:
            return SendResult(
                status=SendStatus.FAILED_TRANSIENT,
                timestamp=datetime.now(UTC),
                error_code=UPGRADE_REQUIRED,
                error_message=_REFUSAL_TEXTS[UPGRADE_REQUIRED],
                metadata={DEFERRED_KEY: DEFERRED_VALUE},
            )
        return self._inner.send(message)


class FirstContactCapChannel:
    """A channel whose FIRST messages to people who did not write to us are rationed: a first message
    takes a place of the day in `guard`, a reply or a follow-up on a conversation the person started
    does not. With no guard (a deployment without a database) no first message is sent at all.

    The same rules as `SendCapChannel`: a place is given back only when the failure is certain."""

    def __init__(self, inner, guard: DailySendGuard | None) -> None:
        self._inner = inner
        self._guard = guard

    def __repr__(self) -> str:
        return "FirstContactCapChannel()"

    @property
    def channel(self) -> Channel:
        return self._inner.channel

    def send(self, message: OutboundMessage) -> SendResult:
        if not message.first_contact:
            return self._inner.send(message)
        reserved = self._guard.reserve() if self._guard is not None else Refusal(CAP_UNAVAILABLE)
        if isinstance(reserved, Refusal):
            return SendResult(
                status=SendStatus.FAILED_TRANSIENT,
                timestamp=datetime.now(UTC),
                error_code=reserved.code,
                error_message=_REFUSAL_TEXTS[reserved.code],
                metadata={DEFERRED_KEY: DEFERRED_VALUE},
            )
        result = self._inner.send(message)  # if it raises, the message may have left: place kept
        if not result.ok and self._guard is not None:
            self._guard.release(reserved)
        return result


class NewContactQuotaChannel:
    """Any `MessagingChannel`, behind the plan's daily quota of new commercial contacts. Only a
    message marked `new_contact` takes a place; everything else passes untouched. No quota (the
    client is on no plan): nothing is counted. A place is given back when the send certainly failed
    (including a refusal by the channel's own cap), kept when the channel raised."""

    def __init__(self, inner, guard: DailySendGuard, quota: Callable[[], int | None]) -> None:
        self._inner = inner
        self._guard = guard
        self._quota = quota

    def __repr__(self) -> str:
        return "NewContactQuotaChannel()"

    @property
    def channel(self) -> Channel:
        return self._inner.channel

    def send(self, message: OutboundMessage) -> SendResult:
        if not message.new_contact:
            return self._inner.send(message)
        try:
            if self._quota() is None:
                return self._inner.send(message)
        except Exception as error:  # noqa: BLE001 - a plan that cannot be read: no new contact
            log.warning("commercial quota check failed: %s", type(error).__name__)
            return _deferred(CAP_UNAVAILABLE)
        reserved = self._guard.reserve()
        if isinstance(reserved, Refusal):
            return _deferred(
                COMMERCIAL_QUOTA_REACHED if reserved.code == CAP_REACHED else reserved.code
            )
        result = self._inner.send(message)  # if it raises, the message may have left: place kept
        if not result.ok:
            self._guard.release(reserved)
        return result


def _deferred(code: str) -> SendResult:
    return SendResult(
        status=SendStatus.FAILED_TRANSIENT,
        timestamp=datetime.now(UTC),
        error_code=code,
        error_message=_REFUSAL_TEXTS[code],
        metadata={DEFERRED_KEY: DEFERRED_VALUE},
    )


@dataclass(frozen=True, slots=True)
class EffectiveCap:
    """What limits a client's e-mails today. `effective` is None when no valid Admin cap exists.
    `plan_quota` is the plan's quota of new commercial contacts, shown next to the cap: it is a
    separate limit (`NewContactQuotaChannel`) and does not lower the cap."""

    admin_cap: int | None
    plan_quota: int | None
    effective: int | None


def combine_caps(admin_cap: object, plan_quota: object) -> EffectiveCap:
    """The Admin's cap is the effective one; none valid: no effective cap."""
    cap = valid_cap(admin_cap)
    return EffectiveCap(cap, valid_cap(plan_quota), cap)


class SendLimits:
    """Cap + plan + counter of a deployment: one object from which every e-mail path (prospecting in
    both modes, replies, follow-ups, the daily report) takes its guard and its figures."""

    def __init__(
        self,
        caps: SendCapStore,
        counter: SendCounter,
        clock: Clock,
        commercial_quota: Callable[[str], int | None] | None = None,
        observer: QuotaObserver | None = None,
    ) -> None:
        self._observer = observer
        self._caps = caps
        self._counter = counter
        self._clock = clock
        self._commercial_quota = commercial_quota  # None: this deployment has no plans

    def __repr__(self) -> str:
        return "SendLimits()"

    def attach_observer(self, observer: QuotaObserver) -> None:
        """Guards made after this call report each attempt to `observer` (quota alerts)."""
        self._observer = observer

    def cap_of(self, client_id: str) -> EffectiveCap:
        """Raises whatever the stores raise: the callers turn it into a refusal / "unavailable"."""
        return combine_caps(self._caps.get(client_id), self.commercial_quota(client_id))

    def commercial_quota(self, client_id: str) -> int | None:
        """The plan's daily quota of new commercial contacts now; None: no plan, no such quota."""
        return self._commercial_quota(client_id) if self._commercial_quota is not None else None

    def commercial_guard(self, client_id: str) -> DailySendGuard:
        """Takes a place of the day's quota of new contacts (limit: the plan's, read at each use)."""
        return DailySendGuard(
            client_id,
            COMMERCIAL_CHANNEL,
            lambda: self.commercial_quota(client_id),
            self._clock,
            self._counter,
        )

    def commercial_channel(self, inner, client_id: str) -> "NewContactQuotaChannel":
        return NewContactQuotaChannel(
            inner, self.commercial_guard(client_id), lambda: self.commercial_quota(client_id)
        )

    def effective(self, client_id: str) -> int | None:
        return valid_cap(self._caps.get(client_id))  # the plan is not read: it does not lower it

    def guard(self, client_id: str, channel: str) -> DailySendGuard:
        return DailySendGuard(
            client_id,
            channel,
            lambda: self.effective(client_id),
            self._clock,
            self._counter,
            self._observer,
        )

    def usage_between(self, client_id: str, start: date, end: date) -> dict[tuple[str, date], int]:
        """Real consumption per (channel, UTC day) for days in [start, end)."""
        return self._counter.usage_between(client_id, start, end)

    def used(self, client_id: str, channel: str, day: date) -> int:
        return self._counter.used(client_id, channel, day)

    def fixed_guard(self, client_id: str, channel: str, limit: int) -> DailySendGuard:
        """A guard for a count that is not the e-mail cap (Telegram's new conversations a day), with
        a limit the caller states. Counted in the same shared counter, under its own channel name."""
        return DailySendGuard(client_id, channel, lambda: limit, self._clock, self._counter)


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

    def usage_between(
        self, client_id: str, start: date, end: date
    ) -> dict[tuple[str, date], int]:
        with self._lock:
            return {
                (channel, day): used
                for (cid, channel, day), used in self._counts.items()
                if cid == client_id and start <= day < end and used > 0
            }


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
