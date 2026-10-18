"""The client's daily report: built from what is stored, kept, and sent once a day.

**Source of truth.** The figures are read from the client's own stored prospects and conversations
(through the Engine's read-only methods), from the send-cap counter and from the agent's health.
Nothing is estimated: what the platform does not track (purchase intentions, appointments,
conversions) is reported as "not tracked", never as zero and never as a sale.

**One report per client and day.** A report is the 24 hours that end at the client's chosen time of
day, in the client's chosen IANA time zone (`window_for`). It is stored under (client, day) with a
unique constraint, so a restart or a second process cannot make a second one. It is generated even
when it cannot be delivered; it is then kept with the reason.

**Delivery** goes through a messaging channel that is not the prospecting channel's recipient: the
client chooses e-mail or Telegram and an address of its own. WhatsApp is not supported and says so.
An e-mail report is an outgoing e-mail: it takes a place in the daily send cap (`SendCapChannel`),
fail-closed. A report is `sent` only when the channel accepted it; a channel that is down leaves it
`pending` or `failed` and retryable; a configuration problem makes it `undeliverable` (and it is
retried once the configuration is fixed). A send whose outcome is unknown (the channel raised, or
the process died mid-send) is `failed` with `send_unconfirmed` and is never retried by itself.

Errors are stored as short codes, never as what a server answered (which could carry an address).
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sales_app.daily_report_text import LANGUAGES, render_text
from sales_app.send_cap import DailySendGuard, SendCapChannel
from sales_core.report_figures import activity_figures, report_message

log = logging.getLogger(__name__)

EMAIL = "email"
TELEGRAM = "telegram"
REPORT_CHANNELS = (EMAIL, TELEGRAM)

PENDING, SENDING, SENT, FAILED, UNDELIVERABLE = (
    "pending",
    "sending",
    "sent",
    "failed",
    "undeliverable",
)
STATUSES = (PENDING, SENDING, SENT, FAILED, UNDELIVERABLE)

# Error codes (stored and shown; fixed words).
SETTINGS_MISSING = "report_settings_missing"
SETTINGS_DISABLED = "report_disabled"
CHANNEL_UNSUPPORTED = "channel_unsupported"
CHANNEL_NOT_AUTHORIZED = "channel_not_authorized"
CHANNEL_UNAVAILABLE = "channel_unavailable"
SEND_UNCONFIRMED = "send_unconfirmed"
SEND_FAILED = "send_failed"
SEND_REFUSED = "send_refused"
CAP_CODES = frozenset({"send_cap_not_configured", "send_cap_reached", "send_cap_unavailable"})
# An `undeliverable` report with one of these is retried by itself once the cause is gone.
CONFIG_CODES = frozenset({SETTINGS_MISSING, SETTINGS_DISABLED, CHANNEL_NOT_AUTHORIZED})

MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
STALE_SENDING = timedelta(minutes=10)
RETRY_WINDOW_DAYS = 2  # only a report this recent is delivered by the scheduler
FIGURES_VERSION = 1
_CODE = re.compile(r"[^a-z0-9_]")
_EMAIL = re.compile(r"^[^@\s<>,;]{1,64}@[^@\s<>,;]{1,255}\.[^@\s<>,;]{2,}$")
_CHAT = re.compile(r"^-?\d{1,20}$")


class InvalidReportSettingsError(ValueError):
    """The code says what is wrong (`unsupported_channel`, `invalid_recipient`, ...)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReportStoreError(Exception):
    """A report or its settings could not be read or written (a fixed sentence)."""


class ReportChannelUnavailableError(Exception):
    """The channel for a report cannot be opened right now; `code` is a fixed word."""

    def __init__(self, code: str = CHANNEL_UNAVAILABLE) -> None:
        super().__init__(code)
        self.code = code


# ---- settings -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportSettings:
    client_id: str
    enabled: bool
    channel: str  # email | telegram
    recipient: str  # an e-mail address, or a Telegram chat id: the client's own, not a prospect's
    send_time: time  # naive wall-clock time in `timezone`
    timezone: str  # an explicit IANA name
    language: str
    updated_at: datetime
    updated_by: str


def valid_timezone(name: object) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise InvalidReportSettingsError("invalid_timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise InvalidReportSettingsError("invalid_timezone") from None


def parse_send_time(value: object) -> time:
    if isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        return time(int(value[:2]), int(value[3:]))
    raise InvalidReportSettingsError("invalid_time")


def check_recipient(channel: str, recipient: object) -> str:
    if not isinstance(recipient, str):
        raise InvalidReportSettingsError("invalid_recipient")
    value = recipient.strip()
    pattern = _EMAIL if channel == EMAIL else _CHAT
    if len(value) > 254 or not pattern.fullmatch(value):
        raise InvalidReportSettingsError("invalid_recipient")
    return value


def build_settings(
    client_id: str,
    *,
    enabled: bool,
    channel: str,
    recipient: str,
    send_time: str,
    timezone: str,
    language: str,
    at: datetime,
    by: str,
) -> ReportSettings:
    """Validated settings, or `InvalidReportSettingsError`. WhatsApp is refused as unsupported."""
    if channel in ("whatsapp", "sms"):
        raise InvalidReportSettingsError("unsupported_channel")
    if channel not in REPORT_CHANNELS:
        raise InvalidReportSettingsError("unknown_channel")
    if language not in LANGUAGES:
        raise InvalidReportSettingsError("invalid_language")
    valid_timezone(timezone)
    return ReportSettings(
        client_id=client_id,
        enabled=enabled,
        channel=channel,
        recipient=check_recipient(channel, recipient),
        send_time=parse_send_time(send_time),
        timezone=timezone,
        language=language,
        updated_at=at,
        updated_by=by,
    )


def mask_recipient(channel: str, recipient: str) -> str:
    """What may be shown or stored about a destination: enough to recognise it."""
    if channel == EMAIL and "@" in recipient:
        name, _, domain = recipient.partition("@")
        return f"{name[:1]}***@{domain}"
    return f"***{recipient[-3:]}"


# ---- the report -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportRecord:
    client_id: str
    day: date  # the local day the report is for (the day its window ends)
    window_start: datetime
    window_end: datetime
    timezone: str
    generated_at: datetime
    language: str
    figures: Mapping[str, Any]
    text: str
    status: str = PENDING
    attempts: int = 0
    next_attempt_at: datetime | None = None
    error_code: str | None = None
    channel: str | None = None
    recipient_hint: str | None = None
    sent_at: datetime | None = None
    updated_at: datetime | None = None


def window_for(day: date, send_time: time, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The 24 hours (wall clock) that end at `send_time` on `day`, as UTC instants."""
    end = datetime.combine(day, send_time, tzinfo=zone)
    start = datetime.combine(day - timedelta(days=1), send_time, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def latest_report_day(now: datetime, send_time: time, zone: ZoneInfo) -> date:
    """The newest day whose report time has come: today from `send_time` on, else yesterday."""
    local = now.astimezone(zone)
    if local >= datetime.combine(local.date(), send_time, tzinfo=zone):
        return local.date()
    return local.date() - timedelta(days=1)


class ReportReader(Protocol):
    """What the figures are read from. The Engine satisfies it (read-only, this client only)."""

    def list_prospects(self) -> Sequence[Any]: ...

    def conversation_of(self, prospect_id: str) -> Any: ...


def collect_figures(
    reader: ReportReader,
    *,
    day: date,
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
    now: datetime,
    quota: Mapping[str, Any] | None,
    health: Mapping[str, Any] | None,
) -> dict[str, Any]:
    core = activity_figures(
        reader.list_prospects(), reader.conversation_of, start=start, end=end, now=now
    )
    required: list[dict[str, Any]] = []
    if core["needs_human"]:
        required.append({"code": "needs_human", "count": core["needs_human"]})
    if quota is not None and quota.get("status") == "not_configured":
        required.append({"code": "send_cap_not_configured"})
    if quota is not None and quota.get("status") == "ok" and quota.get("remaining") == 0:
        required.append({"code": "send_cap_reached"})
    health_view = _health_view(health)
    if health_view and health_view["status"] == "degraded":
        required.append({"code": "channel_degraded"})
    if health_view and health_view.get("sync_failures"):
        required.append({"code": "sync_failing"})
    if health_view and health_view.get("inbox_failures"):
        required.append({"code": "inbox_failing"})
    return {
        "version": FIGURES_VERSION,
        "period": {
            "day": day.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "local_start": start.astimezone(zone).isoformat(),
            "local_end": end.astimezone(zone).isoformat(),
            "timezone": zone.key,
        },
        "generated_at": now.isoformat(),
        "prospects": core["prospects"],
        "activity": core["activity"],
        "follow_ups": core["follow_ups"],
        # Not tracked by the platform: said so instead of reported as zero.
        "purchase_intents": {"tracked": False},
        "appointments": {"tracked": False},
        "conversions": {"tracked": False},
        "quota": dict(quota) if quota is not None else None,
        "health": health_view,
        "actions_required": required,
    }


def _health_view(health: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Counters and a status only: the message of a failure could carry a value."""
    if health is None:
        return None
    writeback = health.get("writeback")
    sync_failures = writeback.get("consecutive_failures", 0) if isinstance(writeback, dict) else 0
    return {
        "status": str(health.get("status", "ready")),
        "consecutive_failures": int(health.get("consecutive_failures") or 0),
        "inbox_failures": int(health.get("inbox_failures") or 0),
        "sync_failures": int(sync_failures or 0),
        "last_error_at": health.get("last_error_at"),
    }


# ---- ports ---------------------------------------------------------------------------------------


class ReportSettingsStore(Protocol):
    def get(self, client_id: str) -> ReportSettings | None: ...

    def set(self, settings: ReportSettings) -> None: ...


class ReportStore(Protocol):
    def add(self, record: ReportRecord) -> bool:
        """Insert; False (nothing written) when the client already has a report for that day."""
        ...

    def get(self, client_id: str, day: date) -> ReportRecord | None: ...

    def list(self, client_id: str, limit: int) -> list[ReportRecord]:
        """Newest day first."""
        ...

    def claim(
        self, client_id: str, day: date, allowed: Sequence[str], now: datetime
    ) -> ReportRecord | None:
        """Atomically move a report whose status is in `allowed` to `sending`; None if it is not."""
        ...

    def save(self, record: ReportRecord) -> None:
        """Write the delivery fields (status, attempts, next attempt, error, channel, sent_at)."""
        ...


class ReportChannels(Protocol):
    def open(self, client_id: str, channel: str):
        """A `MessagingChannel` for the client's report, or raises `ReportChannelUnavailableError`."""
        ...


class SendQuota(Protocol):
    def snapshot(self, client_id: str, now: datetime) -> dict[str, Any]: ...


class InMemoryReportSettingsStore:
    """FOR TESTS ONLY."""

    def __init__(self) -> None:
        self._rows: dict[str, ReportSettings] = {}

    def get(self, client_id: str) -> ReportSettings | None:
        return self._rows.get(client_id)

    def set(self, settings: ReportSettings) -> None:
        self._rows[settings.client_id] = settings


class InMemoryReportStore:
    """FOR TESTS ONLY."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, date], ReportRecord] = {}

    def add(self, record: ReportRecord) -> bool:
        key = (record.client_id, record.day)
        if key in self._rows:
            return False
        self._rows[key] = record
        return True

    def get(self, client_id: str, day: date) -> ReportRecord | None:
        return self._rows.get((client_id, day))

    def list(self, client_id: str, limit: int) -> list[ReportRecord]:
        rows = [r for (c, _), r in self._rows.items() if c == client_id]
        return sorted(rows, key=lambda r: r.day, reverse=True)[:limit]

    def claim(self, client_id, day, allowed, now):
        row = self._rows.get((client_id, day))
        if row is None or row.status not in allowed:
            return None
        row = replace(row, status=SENDING, updated_at=now)
        self._rows[(client_id, day)] = row
        return row

    def save(self, record: ReportRecord) -> None:
        self._rows[(record.client_id, record.day)] = record


class SendCapQuota:
    """The email quota of a client, read from the cap and the counter of `sales_app.send_cap`."""

    def __init__(self, cap_of: Callable[[str], int | None], used: Callable[[str, str, date], int]):
        self._cap_of = cap_of
        self._used = used

    def snapshot(self, client_id: str, now: datetime) -> dict[str, Any]:
        day = now.astimezone(UTC).date()
        try:
            cap = self._cap_of(client_id)
            if cap is None:
                return {"channel": EMAIL, "status": "not_configured", "day_utc": day.isoformat()}
            used = self._used(client_id, EMAIL, day)
        except Exception:  # noqa: BLE001 - a store down: said as unavailable, never as a number
            return {"channel": EMAIL, "status": "unavailable", "day_utc": day.isoformat()}
        return {
            "channel": EMAIL,
            "status": "ok",
            "day_utc": day.isoformat(),
            "cap": cap,
            "used": used,
            "remaining": max(cap - used, 0),
        }


# ---- the service -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportRun:
    ok: bool
    errors: tuple[str, ...] = ()


class DailyReportService:
    def __init__(
        self,
        *,
        settings: ReportSettingsStore,
        reports: ReportStore,
        channels: ReportChannels,
        now: Callable[[], datetime],
        quota: SendQuota | None = None,
        send_guard: Callable[[str], DailySendGuard | None] | None = None,
        channel_allowed: Callable[[str, str], bool] | None = None,
        default_timezone: Callable[[str], str | None] | None = None,
    ) -> None:
        self._settings = settings
        self._reports = reports
        self._channels = channels
        self._now = now
        self._quota = quota
        self._guard = send_guard
        self._allowed = channel_allowed
        self._default_tz = default_timezone

    def __repr__(self) -> str:
        return "DailyReportService()"

    # ---- settings ----

    def settings_of(self, client_id: str) -> ReportSettings | None:
        return self._settings.get(client_id)

    def save_settings(self, settings: ReportSettings) -> None:
        self._settings.set(settings)

    def reports_of(self, client_id: str, limit: int = 30) -> list[ReportRecord]:
        return self._reports.list(client_id, limit)

    def report_of(self, client_id: str, day: date) -> ReportRecord | None:
        return self._reports.get(client_id, day)

    # ---- generation ----

    def generate(
        self,
        client_id: str,
        reader: ReportReader,
        health: Mapping[str, Any] | None,
        *,
        day: date | None = None,
    ) -> ReportRecord:
        """The report of `day` (default: the newest one due), made once: an existing one is
        returned as it is. Without usable settings it is still made, as `undeliverable`."""
        now = self._now()
        stored = self._settings.get(client_id)
        zone_name = (
            stored.timezone
            if stored
            else (self._default_tz(client_id) if self._default_tz else None)
        )
        try:
            zone = valid_timezone(zone_name)
        except InvalidReportSettingsError:
            zone = ZoneInfo("UTC")  # a report with no zone is still a report; it says UTC
        send_time = stored.send_time if stored else time(0, 0)
        target = day or latest_report_day(now, send_time, zone)
        existing = self._reports.get(client_id, target)
        if existing is not None:
            return existing
        start, end = window_for(target, send_time, zone)
        quota = self._quota.snapshot(client_id, now) if self._quota is not None else None
        figures = collect_figures(
            reader,
            day=target,
            start=start,
            end=end,
            zone=zone,
            now=now,
            quota=quota,
            health=health,
        )
        language = stored.language if stored else "en"
        record = ReportRecord(
            client_id=client_id,
            day=target,
            window_start=start,
            window_end=end,
            timezone=zone.key,
            generated_at=now,
            language=language,
            figures=figures,
            text=render_text(figures, language),
            status=PENDING if self._usable(stored) else UNDELIVERABLE,
            error_code=None if self._usable(stored) else self._unusable_code(stored),
            channel=stored.channel if stored else None,
            recipient_hint=mask_recipient(stored.channel, stored.recipient) if stored else None,
            updated_at=now,
        )
        if not self._reports.add(record):  # another process made it first
            return self._reports.get(client_id, target) or record
        return record

    @staticmethod
    def _usable(settings: ReportSettings | None) -> bool:
        return settings is not None and settings.enabled

    @staticmethod
    def _unusable_code(settings: ReportSettings | None) -> str:
        return SETTINGS_MISSING if settings is None else SETTINGS_DISABLED

    # ---- delivery ----

    def deliver(self, client_id: str, day: date) -> ReportRecord | None:
        """Try to send the report of `day` once. Idempotent: a `sent` or `sending` report is never
        sent again. It does not look at the backoff: the scheduler decides when, the client's own
        "send now" may retry any report that has not been sent (even one of unknown outcome)."""
        now = self._now()
        record = self._reports.get(client_id, day)
        if record is None:
            return None
        if record.status in (SENT, SENDING):
            return record
        stored = self._settings.get(client_id)
        problem = self._config_problem(client_id, stored)
        claimed = self._reports.claim(client_id, day, (PENDING, FAILED, UNDELIVERABLE), now)
        if claimed is None:
            return self._reports.get(client_id, day)
        if problem is not None or stored is None:
            return self._settle(
                claimed, UNDELIVERABLE, now, error=problem or SETTINGS_MISSING, stored=stored
            )
        return self._send(claimed, stored, now)

    def _config_problem(self, client_id: str, stored: ReportSettings | None) -> str | None:
        if stored is None:
            return SETTINGS_MISSING
        if not stored.enabled:
            return SETTINGS_DISABLED
        if stored.channel not in REPORT_CHANNELS:
            return CHANNEL_UNSUPPORTED
        if self._allowed is not None:
            try:
                if not self._allowed(client_id, stored.channel):
                    return CHANNEL_NOT_AUTHORIZED
            except Exception:  # noqa: BLE001 - an unreadable policy means no
                return CHANNEL_NOT_AUTHORIZED
        return None

    def _send(self, record: ReportRecord, stored: ReportSettings, now: datetime) -> ReportRecord:
        attempts = record.attempts + 1
        record = replace(record, attempts=attempts)
        try:
            channel = self._channels.open(record.client_id, stored.channel)
            if stored.channel == EMAIL:
                guard = self._guard(record.client_id) if self._guard else None
                if guard is None:  # no cap control wired: nothing goes out
                    return self._settle(
                        record, FAILED, now, error="send_cap_unavailable", stored=stored
                    )
                channel = SendCapChannel(channel, guard)
        except ReportChannelUnavailableError as error:
            return self._settle(record, FAILED, now, error=error.code, stored=stored)
        except Exception:  # noqa: BLE001 - building a channel failed: nothing was sent
            return self._settle(record, FAILED, now, error=CHANNEL_UNAVAILABLE, stored=stored)
        message = report_message(
            client_id=record.client_id,
            channel=stored.channel,
            recipient=stored.recipient,
            text=record.text,
            language=record.language,
            idempotency_key=f"daily-report:{record.client_id}:{record.day.isoformat()}",
        )
        try:
            result = channel.send(message)
        except Exception:  # noqa: BLE001 - it may have left: never retried by itself
            log.warning("daily report send raised for %s", record.client_id)
            return self._settle(record, FAILED, now, error=SEND_UNCONFIRMED, stored=stored)
        if result.ok:
            return self._settle(record, SENT, now, error=None, stored=stored)
        code = _CODE.sub("", (result.error_code or "").lower())[:48] or SEND_FAILED
        if code in CAP_CODES:
            # A refusal: nothing left, and it does not count as an attempt. Tomorrow's places.
            tomorrow = datetime.combine(
                now.astimezone(UTC).date() + timedelta(days=1), time(0, 5), tzinfo=UTC
            )
            return self._settle(
                replace(record, attempts=record.attempts - 1),
                FAILED,
                now,
                error=code,
                stored=stored,
                retry_at=tomorrow if code == "send_cap_reached" else now + timedelta(minutes=30),
            )
        if result.retryable is False:
            return self._settle(record, UNDELIVERABLE, now, error=code, stored=stored)
        return self._settle(record, FAILED, now, error=code, stored=stored)

    def _settle(
        self,
        record: ReportRecord,
        status: str,
        now: datetime,
        *,
        error: str | None,
        stored: ReportSettings | None,
        retry_at: datetime | None = None,
    ) -> ReportRecord:
        retry = retry_at
        if status == FAILED and retry is None and error != SEND_UNCONFIRMED:
            delay = min(
                BACKOFF_BASE_SECONDS * 2 ** max(record.attempts - 1, 0), BACKOFF_MAX_SECONDS
            )
            retry = now + timedelta(seconds=delay)
        settled = replace(
            record,
            status=status,
            error_code=error,
            next_attempt_at=None if status in (SENT, UNDELIVERABLE) else retry,
            sent_at=now if status == SENT else record.sent_at,
            channel=stored.channel if stored else record.channel,
            recipient_hint=(
                mask_recipient(stored.channel, stored.recipient)
                if stored
                else record.recipient_hint
            ),
            updated_at=now,
        )
        self._reports.save(settled)
        return settled

    # ---- the scheduler's side ----

    def _retryable(
        self, record: ReportRecord, stored: ReportSettings | None, now: datetime
    ) -> bool:
        if record.status == PENDING:
            return True
        if record.status == FAILED:
            return (
                record.error_code != SEND_UNCONFIRMED
                and record.attempts < MAX_ATTEMPTS
                and (record.next_attempt_at is None or record.next_attempt_at <= now)
            )
        if record.status == UNDELIVERABLE:
            return (
                record.error_code in CONFIG_CODES
                and stored is not None
                and stored.enabled
                and self._config_problem(record.client_id, stored) is None
            )
        return False

    def _recent(self, client_id: str, stored: ReportSettings, now: datetime) -> list[ReportRecord]:
        zone = valid_timezone(stored.timezone)
        newest = latest_report_day(now, stored.send_time, zone)
        floor = newest - timedelta(days=RETRY_WINDOW_DAYS)
        return [r for r in self._reports.list(client_id, 10) if r.day >= floor]

    def _settle_stale(self, client_id: str, records: Sequence[ReportRecord], now: datetime) -> None:
        """A report left `sending` by a process that died: whether it left is unknown."""
        for record in records:
            if record.status == SENDING and (
                record.updated_at is None or now - record.updated_at > STALE_SENDING
            ):
                self._reports.save(
                    replace(
                        record,
                        status=FAILED,
                        error_code=SEND_UNCONFIRMED,
                        next_attempt_at=None,
                        updated_at=now,
                    )
                )

    def due(self, client_id: str, now: datetime) -> bool:
        stored = self._settings.get(client_id)
        if stored is None or not stored.enabled:
            return False
        zone = valid_timezone(stored.timezone)
        target = latest_report_day(now, stored.send_time, zone)
        recent = self._recent(client_id, stored, now)
        if not any(r.day == target for r in recent):
            return True
        return any(self._retryable(r, stored, now) or r.status == SENDING for r in recent)

    def run_scheduled(
        self,
        client_id: str,
        reader: ReportReader,
        health: Mapping[str, Any] | None,
        now: datetime,
    ) -> ReportRun:
        """Make today's report if it is missing, then send what is due. Safe to repeat."""
        stored = self._settings.get(client_id)
        if stored is None or not stored.enabled:
            return ReportRun(ok=True)
        self.generate(client_id, reader, health)
        recent = self._recent(client_id, stored, now)
        self._settle_stale(client_id, recent, now)
        errors: list[str] = []
        for record in self._recent(client_id, stored, now):
            if self._retryable(record, stored, now):
                outcome = self.deliver(client_id, record.day)
                if outcome is not None and outcome.status in (FAILED, UNDELIVERABLE):
                    errors.append(f"report {outcome.day.isoformat()}: {outcome.error_code}")
        return ReportRun(ok=not errors, errors=tuple(errors))
