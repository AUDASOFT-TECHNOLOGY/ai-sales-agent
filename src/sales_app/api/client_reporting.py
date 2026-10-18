"""Client API: the daily report of the logged-in client, its settings and its delivery state.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, and every read and write is keyed by it, so one client can never reach another's settings or
reports. Every answer is `Cache-Control: no-store`.

- `GET /client/v1/reporting`: the settings (null until saved), the channels with whether each can be
  chosen (WhatsApp is listed as not supported), and the newest report.
- `PUT /client/v1/reporting/settings`: channel (email or Telegram), the client's own address or chat
  id, time of day, explicit IANA time zone, language, on/off. 422 with a code for anything invalid
  (a WhatsApp report included), 403 for a channel the platform has not opened to this account.
- `GET /client/v1/reports?limit=30` and `GET /client/v1/reports/{day}`: the reports kept, newest first,
  each with its honest delivery state (`pending`, `sending`, `sent`, `failed`, `undeliverable`).
- `POST /client/v1/reports/generate`: makes the newest due report if it does not exist yet (never a
  second one for the same day) and tries to send it. With no valid setting it is kept and says so.
- `POST /client/v1/reports/{day}/resend`: tries again a report that was not delivered. 409 for one
  already sent or being sent: a report is delivered once.

The report's figures come from the client's stored data, read through its own running Engine. What
the platform does not track (purchase intentions, appointments, conversions) is reported as not
tracked: nothing here is ever presented as a confirmed sale.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.channel_status import allowed_channels
from sales_app.client_profiles import ClientProfile
from sales_app.daily_report import (
    FAILED,
    PENDING,
    REPORT_CHANNELS,
    SENDING,
    SENT,
    UNDELIVERABLE,
    DailyReportService,
    InvalidReportSettingsError,
    ReportRecord,
    ReportSettings,
    ReportStoreError,
    build_settings,
)
from sales_app.daily_report_text import LANGUAGES
from sales_app.errors import ClientNotReadyError
from sales_core.engine import Engine

UNAVAILABLE = "reports are temporarily unavailable"
NO_SERVICE = "daily reports are not available on this deployment"

# What a code means, in a sentence a client can act on. Never what a server answered.
PROBLEMS = {
    "report_settings_missing": "Choose where to receive your report: nothing was sent.",
    "report_disabled": "Your daily report is switched off: nothing was sent.",
    "channel_unsupported": "This channel cannot carry a report.",
    "channel_not_authorized": "This channel is not open to your account yet: nothing was sent.",
    "channel_unavailable": "The channel could not be reached: Revansa will try again.",
    "channel_not_wired": "This channel is not available on the platform: nothing was sent.",
    "dry_run": "Your agent runs in test mode: nothing is sent outside.",
    "send_unconfirmed": "The channel did not confirm: the report may or may not have arrived.",
    "send_failed": "The channel refused the report: Revansa will try again.",
    "send_cap_not_configured": "Your daily email limit is not set: contact your administrator.",
    "send_cap_reached": "Your daily email limit is reached: Revansa will try again tomorrow.",
    "send_cap_unavailable": "The daily email limit cannot be checked: Revansa will try again.",
}


class ReportingSettingsBody(BaseModel):
    """`extra="forbid"`: any other key answers 422 instead of being ignored."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    channel: str
    recipient: str
    send_time: str
    timezone: str
    language: str = "en"


class SettingsResponse(BaseModel):
    enabled: bool
    channel: str
    recipient: str
    send_time: str
    timezone: str
    language: str
    updated_at: datetime


class ChannelOption(BaseModel):
    key: str
    available: bool
    reason: str | None = None  # not_supported | not_authorized


class ReportSummary(BaseModel):
    day: date
    status: str  # pending | sending | sent | failed | undeliverable
    channel: str | None
    recipient_hint: str | None
    generated_at: datetime
    sent_at: datetime | None
    attempts: int
    next_attempt_at: datetime | None
    error_code: str | None
    problem: str | None
    period_start: datetime
    period_end: datetime
    timezone: str
    messages_sent: int
    replies_received: int


class ReportDetail(ReportSummary):
    language: str
    figures: dict[str, Any]
    text: str


class ReportingResponse(BaseModel):
    settings: SettingsResponse | None
    channels: list[ChannelOption]
    languages: list[str]
    latest: ReportSummary | None


class ReportListResponse(BaseModel):
    reports: list[ReportSummary]


def _summary(record: ReportRecord) -> dict[str, Any]:
    activity = record.figures.get("activity", {})
    return {
        "day": record.day,
        "status": record.status,
        "channel": record.channel,
        "recipient_hint": record.recipient_hint,
        "generated_at": record.generated_at,
        "sent_at": record.sent_at,
        "attempts": record.attempts,
        "next_attempt_at": record.next_attempt_at,
        "error_code": record.error_code,
        "problem": PROBLEMS.get(record.error_code or "", None) if record.status != SENT else None,
        "period_start": record.window_start,
        "period_end": record.window_end,
        "timezone": record.timezone,
        "messages_sent": int(activity.get("messages_sent", 0)),
        "replies_received": int(activity.get("replies_received", 0)),
    }


def _settings_response(settings: ReportSettings) -> SettingsResponse:
    return SettingsResponse(
        enabled=settings.enabled,
        channel=settings.channel,
        recipient=settings.recipient,
        send_time=settings.send_time.strftime("%H:%M"),
        timezone=settings.timezone,
        language=settings.language,
        updated_at=settings.updated_at,
    )


def create_client_reporting_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def service() -> DailyReportService:
        found = application.daily_reports
        if found is None:
            raise HTTPException(503, NO_SERVICE)
        return found

    def guarded[T](response: Response, work: Callable[[DailyReportService], T]) -> T:
        response.headers["Cache-Control"] = "no-store"
        reports = service()
        try:
            return work(reports)
        except ReportStoreError:
            raise HTTPException(503, UNAVAILABLE) from None

    def with_engine[T](client_id: str, work: Callable[[Engine, dict], T]) -> T:
        runtime = application.registry.get(client_id)
        if runtime is None:
            raise HTTPException(409, "your agent is not loaded by the platform yet")
        try:
            with runtime.lease() as engine:
                return work(engine, runtime.health.snapshot())
        except ClientNotReadyError:
            raise HTTPException(409, "your agent is not ready: see your readiness checks") from None
        except ReportStoreError:
            raise
        except Exception:  # noqa: BLE001 - a store failure says nothing about its cause
            raise HTTPException(503, UNAVAILABLE) from None

    def options(client_id: str) -> list[ChannelOption]:
        allowed = allowed_channels(application, client_id)
        found = [
            ChannelOption(
                key=key,
                available=key in allowed,
                reason=None if key in allowed else "not_authorized",
            )
            for key in REPORT_CHANNELS
        ]
        found.append(ChannelOption(key="whatsapp", available=False, reason="not_supported"))
        return found

    @router.get("/reporting", response_model=ReportingResponse)
    def get_reporting(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ReportingResponse:
        def work(reports: DailyReportService) -> ReportingResponse:
            stored = reports.settings_of(client.client_id)
            newest = reports.reports_of(client.client_id, 1)
            return ReportingResponse(
                settings=_settings_response(stored) if stored else None,
                channels=options(client.client_id),
                languages=list(LANGUAGES),
                latest=ReportSummary(**_summary(newest[0])) if newest else None,
            )

        return guarded(response, work)

    @router.put("/reporting/settings", response_model=SettingsResponse)
    def put_settings(
        body: ReportingSettingsBody,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> SettingsResponse:
        def work(reports: DailyReportService) -> SettingsResponse:
            try:
                settings = build_settings(
                    client.client_id,
                    enabled=body.enabled,
                    channel=body.channel,
                    recipient=body.recipient,
                    send_time=body.send_time,
                    timezone=body.timezone,
                    language=body.language,
                    at=application.clock.now(),
                    by=f"client:{client.client_id}",
                )
            except InvalidReportSettingsError as error:
                raise HTTPException(422, error.code) from None
            if body.channel not in allowed_channels(application, client.client_id):
                # Said as plainly for a channel that exists as for one that does not.
                raise HTTPException(403, "this channel is not available for your account")
            reports.save_settings(settings)
            return _settings_response(settings)

        return guarded(response, work)

    @router.get("/reports", response_model=ReportListResponse)
    def list_reports(
        response: Response,
        limit: int = Query(default=30, ge=1, le=100),
        client: ClientProfile = Depends(get_current_client),
    ) -> ReportListResponse:
        def work(reports: DailyReportService) -> ReportListResponse:
            return ReportListResponse(
                reports=[
                    ReportSummary(**_summary(r))
                    for r in reports.reports_of(client.client_id, limit)
                ]
            )

        return guarded(response, work)

    def parse_day(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, "the day must be YYYY-MM-DD") from None

    def detail(record: ReportRecord) -> ReportDetail:
        return ReportDetail(
            **_summary(record),
            language=record.language,
            figures=dict(record.figures),
            text=record.text,
        )

    @router.get("/reports/{day}", response_model=ReportDetail)
    def get_report(
        day: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ReportDetail:
        wanted = parse_day(day)

        def work(reports: DailyReportService) -> ReportDetail:
            record = reports.report_of(client.client_id, wanted)
            if record is None:  # the same words for a day with no report and for another client's
                raise HTTPException(404, "no such report")
            return detail(record)

        return guarded(response, work)

    @router.post("/reports/generate", response_model=ReportDetail)
    def generate(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ReportDetail:
        def work(reports: DailyReportService) -> ReportDetail:
            record = with_engine(
                client.client_id,
                lambda engine, health: reports.generate(client.client_id, engine, health),
            )
            if record.status in (PENDING, FAILED, UNDELIVERABLE):
                record = reports.deliver(client.client_id, record.day) or record
            return detail(record)

        return guarded(response, work)

    @router.post("/reports/{day}/resend", response_model=ReportDetail)
    def resend(
        day: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ReportDetail:
        wanted = parse_day(day)

        def work(reports: DailyReportService) -> ReportDetail:
            record = reports.report_of(client.client_id, wanted)
            if record is None:
                raise HTTPException(404, "no such report")
            if record.status in (SENT, SENDING):
                raise HTTPException(409, "this report was already sent or is being sent")
            return detail(reports.deliver(client.client_id, wanted) or record)

        return guarded(response, work)

    return router
