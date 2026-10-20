"""Client API: how the agent follows up with prospects who stay silent (spec 4.5).

- `GET /client/v1/followup-settings`: the plan in force in the simple shape the Client edits (how many
  follow-ups, how many days apart, and optionally the hours in which one may be due), the default, the
  bounds, and the version the plan was read on.
- `PUT /client/v1/followup-settings`: change it. The write carries the version it was based on (409 if
  the settings moved), is bounded here (422) and then validated by the Brain's own rules like every
  settings publication (422 with the Brain's words, for instance a wait shorter than its minimum), and
  is never half-applied. It changes only the silent-prospect cadence and the allowed hours; every
  other setting, other situations and a product's own cadence included, is kept as it is.

Like every Client route, none takes a client id: the session decides it.
"""

import contextlib
from datetime import timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from sales_app.api.admin_clients import publish_client_settings, read_client_settings
from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.config_store import ConfigStoreError
from sales_app.followup_settings import (
    DEFAULT_FOLLOW_UPS,
    DEFAULT_GAP_DAYS,
    MAX_FOLLOW_UPS,
    MAX_GAP_DAYS,
    MIN_GAP_DAYS,
    FollowUpPlan,
    Window,
    apply_plan,
    plan_of,
)

UNAVAILABLE = "settings are temporarily unavailable"
HOUR_MINUTE = r"^([01]\d|2[0-3]):([0-5]\d)$"
Hour = Annotated[str, StringConstraints(pattern=HOUR_MINUTE)]


class WindowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Hour
    end: Hour
    timezone: str | None = Field(
        default=None, min_length=1, max_length=64
    )  # null: the client's own

    @model_validator(mode="after")
    def _ordered(self) -> "WindowBody":
        if self.start >= self.end:  # "HH:MM" strings order like the times they are
            raise ValueError("start must be earlier than end")
        if self.timezone is not None:
            _zone(self.timezone)
        return self


class WindowResponse(BaseModel):
    start: str
    end: str
    timezone: str


class UpdateFollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int | None
    max_followups: int = Field(ge=0, le=MAX_FOLLOW_UPS)
    gap_days: int = Field(ge=MIN_GAP_DAYS, le=MAX_GAP_DAYS)
    window: WindowBody | None = None


class DefaultsResponse(BaseModel):
    max_followups: int
    gap_days: int


class BoundsResponse(BaseModel):
    max_followups: tuple[int, int]
    gap_days: tuple[int, int]


class FollowUpResponse(BaseModel):
    version: int | None
    configured: bool  # False: the business settings are not written yet, so nothing can be saved
    max_followups: int
    gap_days: int
    window: WindowResponse | None
    timezone: str | None  # the client's own, offered as the window's default
    custom_schedule: bool  # the cadence has uneven waits: saving replaces it by an even one
    defaults: DefaultsResponse
    bounds: BoundsResponse


def _zone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        raise ValueError(f"unknown time zone {name!r}") from None


def brain_settings_of(part: dict[str, Any]) -> dict[str, Any] | None:
    brain = part.get("brain")
    found = brain.get("settings") if isinstance(brain, dict) else None
    return found if isinstance(found, dict) and found else None


def plan_in_force(application: Application, client_id: str) -> FollowUpPlan:
    """The client's plan as it is now (the default when its settings cannot be read)."""
    try:
        _, part, _ = read_client_settings(application, client_id)
    except Exception:  # noqa: BLE001 - a cadence that cannot be read is the default one
        return plan_of(None)
    return plan_of(brain_settings_of(part))


def resume_after(application: Application, client_id: str) -> timedelta | None:
    """How long after a conversation is given back its first follow-up is due: the plan's wait, or
    None if the client chose no follow-up at all."""
    plan = plan_in_force(application, client_id)
    return timedelta(days=plan.gap_days) if plan.max_followups > 0 else None


def create_client_followup_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def response(client_id: str) -> FollowUpResponse:
        version, part, _ = read_client_settings(application, client_id)
        brain = brain_settings_of(part)
        plan = plan_of(brain)
        zone = part.get("timezone")
        return FollowUpResponse(
            version=version,
            configured=brain is not None,
            max_followups=plan.max_followups,
            gap_days=plan.gap_days,
            window=(
                WindowResponse(
                    start=plan.window.start, end=plan.window.end, timezone=plan.window.timezone
                )
                if plan.window is not None
                else None
            ),
            timezone=zone if isinstance(zone, str) else None,
            custom_schedule=plan.custom_schedule,
            defaults=DefaultsResponse(max_followups=DEFAULT_FOLLOW_UPS, gap_days=DEFAULT_GAP_DAYS),
            bounds=BoundsResponse(
                max_followups=(0, MAX_FOLLOW_UPS), gap_days=(MIN_GAP_DAYS, MAX_GAP_DAYS)
            ),
        )

    @router.get("/followup-settings", response_model=FollowUpResponse)
    def get_followup(client: ClientProfile = Depends(get_current_client)) -> FollowUpResponse:
        try:
            return response(client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, UNAVAILABLE) from None

    @router.put("/followup-settings", response_model=FollowUpResponse)
    def put_followup(
        body: UpdateFollowUpRequest, client: ClientProfile = Depends(get_current_client)
    ) -> FollowUpResponse:
        client_id = client.client_id
        try:
            _, part, _ = read_client_settings(application, client_id)
            brain = brain_settings_of(part)
            if brain is None:
                raise HTTPException(
                    409, "complete your business settings before changing the follow-ups"
                )
            zone = body.window.timezone if body.window is not None else None
            window = (
                Window(body.window.start, body.window.end, zone or _client_zone(part))
                if body.window is not None
                else None
            )
            if window is not None and window.timezone == "":
                raise HTTPException(422, "a time zone is needed for the allowed hours")
            before = plan_of(brain)
            plan = FollowUpPlan(body.max_followups, body.gap_days, window)
            publish_client_settings(
                application,
                client_id,
                {"brain": {"settings": apply_plan(brain, plan)}},
                expected_version=body.expected_version,
                author=f"client:{client_id}",
                note="follow-up settings",
            )
        except ConfigStoreError:
            raise HTTPException(503, UNAVAILABLE) from None
        application.audit.record(
            actor=f"client:{client_id}",
            actor_role="client",
            action="client.followup_settings.update",
            target="followup-settings",
            client_id=client_id,
            before={
                "max_followups": before.max_followups,
                "gap_days": before.gap_days,
                "window": _window_doc(before.window),
            },
            after={
                "max_followups": plan.max_followups,
                "gap_days": plan.gap_days,
                "window": _window_doc(plan.window),
            },
        )
        if application.registry.get_config(client_id) is not None:
            # The running agent takes the new cadence now; if the refresh fails, the old one stays
            # in force and the Admin's runtime status says so.
            with contextlib.suppress(Exception):
                application.runtime_manager.refresh_client(client_id)
        return response(client_id)

    return router


def _client_zone(part: dict[str, Any]) -> str:
    zone = part.get("timezone")
    return zone if isinstance(zone, str) else ""


def _window_doc(window: Window | None) -> dict[str, str] | None:
    return (
        None
        if window is None
        else {"start": window.start, "end": window.end, "tz": window.timezone}
    )
