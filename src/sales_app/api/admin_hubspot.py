"""Admin API: the state of a client's HubSpot link, the check of its status mapping, and the resumption
of failed synchronisations.

- `GET  /admin/v1/clients/{id}/hubspot/health`: where the connection stands (and, if the client must
  authorise again, the guided steps: only the client can consent, in its own browser), what the status
  mapping says, and what the write-back ledger holds (applied / waiting / failed / overruled), each
  problem with the action to take. Counts, fixed codes and sentences only: no token, no contact, no
  value read from HubSpot.
- `POST /admin/v1/clients/{id}/hubspot/status-mapping/check` (owner): the server-side validation of
  a `status_property` / `status_mapping` / write-back choice, without saving. It is the very rule the
  save applies (`HubSpotSourceSettings`), so the screen never accepts what the server would refuse.
  The mapping is the Admin's: no Client route can reach it.
- `POST /admin/v1/clients/{id}/hubspot/resume-sync` (owner): tries again, now, the writes that were
  waiting out a failure. Nothing is written twice (see `Scheduler.resume_sync`): the plan is
  recomputed from HubSpot's current values, each write names the value it replaces, and the ledger
  (`op_writeback_ledger`) records each attempt under an attempt id of its own.

Responses are `no-store`.
"""

from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, ValidationError

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client_hubspot_oauth import SOURCES_PAGE
from sales_app.bootstrap import Application
from sales_app.config_store.models import thaw_document
from sales_app.sourcing.statuses import MAPPABLE, StatusMapping, StatusMappingError, StatusWords
from sales_app.wiring.providers import hubspot

Action = Literal[
    "ask_client_to_reconnect",
    "connect",
    "set_statuses",
    "resume_sync",
    "check_hubspot_app",
    "wait",
    "none",
]

RECONNECT_STEPS = (
    "Tell the client that HubSpot needs its authorisation again.",
    f"The client opens {SOURCES_PAGE} in Revansa and chooses to connect HubSpot again.",
    "The client signs in to HubSpot and accepts every permission Revansa asks for.",
    "Come back here and resume the synchronisation: nothing is written twice.",
)


class ConnectionView(BaseModel):
    state: Literal["not_connected", "connected", "reauth_required", "unavailable"]
    platform_configured: bool
    message: str
    action: Action
    steps: list[str] = []
    client_path: str | None = None


class StatusesView(BaseModel):
    configured: bool
    status_property: str | None = None
    # Revansa's status -> the HubSpot values that mean it (the inverse of the stored mapping).
    values: dict[str, list[str]] = {}
    problem: str | None = None


class ProblemView(BaseModel):
    code: str
    message: str
    action: Action


class WritebackView(BaseModel):
    ledger_available: bool
    applied: int = 0
    waiting: int = 0  # the last try failed for a reason that waiting cures
    failed: int = 0  # refused for a reason that waiting does not cure
    overruled: int = 0  # a person changed the value in HubSpot: Revansa does not insist
    problems: list[ProblemView] = []


class HubSpotHealth(BaseModel):
    client_id: str
    uses_hubspot: bool
    connection: ConnectionView | None = None
    statuses: StatusesView | None = None
    writeback: WritebackView | None = None
    can_resume: bool = False


class MappingCheckRequest(BaseModel):
    model_config = ConfigDict(extra="allow")  # the same fields as the source settings


class MappingCheckResponse(BaseModel):
    ok: bool
    problems: list[str] = []


class ResumeResponse(BaseModel):
    ran: bool
    message: str
    counters: dict[str, int] = {}
    problems: list[str] = []


_CONNECTION_MESSAGES = {
    "not_connected": "The client has not connected its HubSpot account yet.",
    "connected": "The client's HubSpot account is connected.",
    "reauth_required": (
        "HubSpot refused the client's authorisation (expired, revoked, or a permission is missing). "
        "Nothing is read or written until the client authorises again."
    ),
    "unavailable": "HubSpot cannot be reached through the platform's application yet.",
}

_FAILURE_ADVICE: dict[str, tuple[str, Action]] = {
    "auth": (
        "HubSpot refused the authorisation. The client has to authorise Revansa again.",
        "ask_client_to_reconnect",
    ),
    "read_only": (
        (
            "HubSpot refused to write: the permission to update contacts, or the property, is "
            "missing. Check the HubSpot application's scopes and the property settings, then have "
            "the client authorise again."
        ),
        "check_hubspot_app",
    ),
    "unavailable": ("HubSpot could not be reached. It is tried again by itself.", "wait"),
    "rate_limited": (
        "HubSpot asked to slow down. It is tried again by itself.",
        "wait",
    ),
    "unexpected": (
        (
            "A write was refused for a reason Revansa does not recognise. Check the property and "
            "the status mapping, then resume."
        ),
        "resume_sync",
    ),
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _values_by_status(mapping: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {status.value: [] for status in sorted(MAPPABLE, key=str)}
    for value, status in mapping.items():
        grouped.setdefault(str(status).strip().lower(), []).append(value)
    return {status: sorted(values) for status, values in grouped.items()}


def create_admin_hubspot_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(_no_store)])

    def reach(admin: AdminUser, client_id: str) -> tuple[dict, dict]:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        found = client_wiring_and_document(application, client_id)
        if found is None:
            raise HTTPException(404, "no such client")
        return found  # type: ignore[return-value]

    def hubspot_settings(document: Any) -> dict[str, Any] | None:
        """The HubSpot source's settings (single source or lanes), or None if the client has none."""
        section = document.get("source")
        if isinstance(section, Mapping) and section.get("provider") == hubspot.KEY:
            held = section.get("settings")
            return thaw_document(held) if held else {}
        sources = document.get("sources")
        if isinstance(sources, list | tuple):
            for source in sources:
                if isinstance(source, Mapping) and source.get("provider") == hubspot.KEY:
                    held = source.get("settings")
                    return thaw_document(held) if held else {}
        return None

    def connection_view(client_id: str) -> ConnectionView:
        access = application.hubspot_access
        if access is None:
            return ConnectionView(
                state="unavailable",
                platform_configured=False,
                message=_CONNECTION_MESSAGES["unavailable"],
                action="none",
            )
        state = access.connection.status(client_id).state
        configured = access.configured()
        if state == "reauth_required":
            return ConnectionView(
                state="reauth_required",
                platform_configured=configured,
                message=_CONNECTION_MESSAGES[state],
                action="ask_client_to_reconnect",
                steps=list(RECONNECT_STEPS),
                client_path=SOURCES_PAGE,
            )
        if state == "not_connected":
            return ConnectionView(
                state="not_connected",
                platform_configured=configured,
                message=_CONNECTION_MESSAGES[state],
                action="connect" if configured else "none",
                steps=[
                    f"The client opens {SOURCES_PAGE} in Revansa and connects HubSpot.",
                ]
                if configured
                else ["Set up the platform's HubSpot application in Settings first."],
                client_path=SOURCES_PAGE if configured else None,
            )
        return ConnectionView(
            state="connected" if state == "connected" else "unavailable",
            platform_configured=configured,
            message=_CONNECTION_MESSAGES.get(state, _CONNECTION_MESSAGES["unavailable"]),
            action="none",
        )

    def statuses_view(settings: dict[str, Any] | None) -> StatusesView:
        if not settings or not settings.get("status_mapping"):
            return StatusesView(
                configured=False,
                problem="No status mapping yet: the agent does not run on HubSpot until it is set.",
            )
        try:
            StatusMapping.from_config(settings["status_mapping"])
        except StatusMappingError as error:
            return StatusesView(configured=False, problem=str(error))
        return StatusesView(
            configured=True,
            status_property=str(settings.get("status_property") or "hs_lead_status"),
            values=_values_by_status(settings["status_mapping"]),
        )

    def ledger_view(client_id: str, settings: dict[str, Any] | None) -> WritebackView:
        support = application.builders.writeback if application.builders is not None else None
        view = WritebackView(
            ledger_available=support is not None and support.open_ledger is not None,
        )
        if not view.ledger_available:
            return view
        try:
            entries = support.open_ledger(client_id).entries()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - an unreadable ledger is told, not guessed at
            view.ledger_available = False
            return view
        latest: dict[tuple[str, str], Any] = {}
        for entry in entries:  # oldest first: the last one per prospect and field is where it stands
            latest[(str(entry.prospect_id), entry.field.value)] = entry
        counts: Counter[str] = Counter(e.outcome.value for e in latest.values())
        view.applied = counts["applied"]
        view.waiting = counts["deferred"]
        view.failed = counts["failed"]
        view.overruled = counts["conflict"]
        codes = Counter(
            e.code.value
            for e in latest.values()
            if e.code is not None and e.outcome.value in ("deferred", "failed")
        )
        for code, _ in codes.most_common():
            message, action = _FAILURE_ADVICE.get(code, _FAILURE_ADVICE["unexpected"])
            view.problems.append(ProblemView(code=code, message=message, action=action))
        if view.waiting or view.failed:
            view.problems.append(
                ProblemView(
                    code="pending_writes",
                    message=f"{view.waiting + view.failed} write(s) are waiting to be sent again.",
                    action="resume_sync",
                )
            )
        return view

    @router.get("/clients/{client_id}/hubspot/health", response_model=HubSpotHealth)
    async def hubspot_health(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> HubSpotHealth:
        _, document = reach(admin, client_id)
        settings = hubspot_settings(document)
        if settings is None:
            return HubSpotHealth(client_id=client_id, uses_hubspot=False)
        connection = connection_view(client_id)
        writeback = await run_in_threadpool(ledger_view, client_id, settings)
        runtime = application.registry.get(client_id)
        return HubSpotHealth(
            client_id=client_id,
            uses_hubspot=True,
            connection=connection,
            statuses=statuses_view(settings),
            writeback=writeback,
            can_resume=(
                connection.state == "connected"
                and runtime is not None
                and runtime.writeback is not None
            ),
        )

    @router.post(
        "/clients/{client_id}/hubspot/status-mapping/check", response_model=MappingCheckResponse
    )
    def check_mapping(
        client_id: str, body: MappingCheckRequest, admin: AdminUser = Depends(require_owner)
    ) -> MappingCheckResponse:
        """The save's own rules, applied to what the screen holds, without saving anything."""
        reach(admin, client_id)
        try:
            hubspot.HubSpotSourceSettings.model_validate(body.model_dump())
        except ValidationError as error:
            return MappingCheckResponse(
                ok=False,
                problems=[
                    ".".join(str(part) for part in e["loc"]) + ": " + str(e["msg"])
                    for e in error.errors(include_input=False)
                ],
            )
        mapping = body.model_dump().get("status_mapping") or {}
        try:
            StatusWords.from_config(mapping)  # writing back needs one word each for paused, closed
            return MappingCheckResponse(ok=True)
        except StatusMappingError as error:
            # Valid for reading; only the write-back of the status needs one word per status.
            return MappingCheckResponse(ok=True, problems=[f"write-back of statuses: {error}"])

    @router.post("/clients/{client_id}/hubspot/resume-sync", response_model=ResumeResponse)
    async def resume_sync(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ResumeResponse:
        _, document = reach(admin, client_id)
        if hubspot_settings(document) is None:
            raise HTTPException(409, "this client does not use HubSpot")
        connection = connection_view(client_id)
        if connection.state != "connected":
            raise HTTPException(
                409, "HubSpot is not connected: " + connection.message + " Reconnect first."
            )
        runtime = application.registry.get(client_id)
        if runtime is None or runtime.writeback is None:
            raise HTTPException(409, "nothing is written back for this client yet")
        result = await run_in_threadpool(application.scheduler.resume_sync, runtime)
        if result is None:
            return ResumeResponse(
                ran=False,
                message="A job of this client is running right now. Try again in a minute.",
            )
        snapshot = runtime.health.snapshot().get("writeback")
        counters = {k: int(v) for k, v in (snapshot or {}).items() if isinstance(v, int)}
        return ResumeResponse(
            ran=True,
            message="The synchronisation was resumed." if result.ok else "The synchronisation ran but stopped on a problem.",
            counters=counters,
            problems=list(result.errors),
        )

    return router
