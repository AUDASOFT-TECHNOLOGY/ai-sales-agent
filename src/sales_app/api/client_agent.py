"""Client API: a client's business settings, whether its agent is ready, and starting or pausing it.

Like `client.py`, no route takes a client id: who is asking comes only from the session cookie, so no
id supplied by the browser can reach another client's settings or switch.

- **Settings** (`GET/PUT /client/v1/settings`) are the part of the client's versioned settings
  document that it may edit: its commercial settings (products, languages, tone, follow-up,
  payment...), its schedule, its time zone and its opt-out words. Never the model (the Admin's), the
  mode (the platform's), the source (its own route, later), the daily quota or the inbound policy.
  A write carries the version it was based on and is refused (409) if the settings moved, validated
  by the same rules as every publication (422 with every problem), and never half-applied.
- **Readiness** (`GET /client/v1/readiness`) lists what the agent still needs, in words a client can
  act on. It names no credential and no operator setting.
- **The agent** (`GET /client/v1/agent`, `POST .../start`, `POST .../pause`) is the client's own
  switch, stored apart from the Admin's `enabled`: the agent works only if the platform enabled it
  **and** its client started it, and this is checked at every scheduler tick and for every inbound
  reply, so a pause or an Admin's disabling takes effect without a restart (work already running
  finishes). The client can never switch on what the Admin switched off, and a client the Admin
  disabled and enabled again must start its agent again.
"""

import contextlib
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from sales_app.agent_state import AgentStateStoreCorruptError, AgentStateStoreUnavailableError
from sales_app.api.admin_clients import (
    client_wiring_and_document,
    publish_client_settings,
    read_client_settings,
)
from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_auth import is_client_active
from sales_app.client_profiles import ClientProfile
from sales_app.config import validate_settings_document
from sales_app.config.schema import DEFAULT_SOURCE
from sales_app.config_store import ConfigStoreError
from sales_app.secret_store import ScopeKind, SecretStoreError, client_scope
from sales_app.source_checks import SourceCheckStoreCorruptError, SourceCheckStoreUnavailableError
from sales_app.wiring.providers import ProviderKind, Requirement, google_sheets
from sales_app.wiring.providers.types import CredentialDeclaration, ProviderDescriptor

PLACEHOLDER_MODELS = ("", "todo")


# ---- settings ---------------------------------------------------------------------------------


class ScheduleChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_at: str | None = None
    days: list[str] | None = None


class SettingsChanges(BaseModel):
    """What a client may change. `extra="forbid"`: any other key (the model, the source, the
    quota...) answers 422 instead of being ignored or half-applied. A key left out keeps its value."""

    model_config = ConfigDict(extra="forbid")

    timezone: str | None = None
    opt_out_keywords: list[str] | None = None
    schedule: ScheduleChanges | None = None
    # The client's commercial settings (business, languages, products, follow-up, payment...): the
    # Brain's own document, whose schema validates it and rejects any unknown key.
    commercial: dict[str, Any] | None = None


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The version these changes were made on (null: none was published yet). Required, never
    # defaulted: a write that does not say what it is based on could silently undo someone else's.
    expected_version: int | None
    settings: SettingsChanges


class SettingsResponse(BaseModel):
    version: int | None
    settings: dict[str, Any]
    updated_at: datetime | None = None
    updated_by: Literal["client", "administrator"] | None = None


# ---- readiness and the agent ------------------------------------------------------------------


class CheckResponse(BaseModel):
    id: str
    ok: bool
    message: str | None = None  # what to do, when it is not ok


class ReadinessResponse(BaseModel):
    ready: bool
    checks: list[CheckResponse]


class AgentResponse(BaseModel):
    state: Literal["not_started", "running", "paused"]  # the client's own switch
    active: bool  # works now: started by the client, enabled by the Admin and ready
    ready: bool
    changed_at: datetime | None = None
    reasons: list[CheckResponse] = []  # what is not ok yet


@dataclass(frozen=True, slots=True)
class _Check:
    id: str
    ok: bool
    message: str


def evaluate_readiness(application: Application, profile: ClientProfile) -> tuple[_Check, ...]:
    """Everything the agent needs before it may start, as fixed sentences a client can act on."""
    client_id = profile.client_id
    registry = application.registry
    enabled = is_client_active(application.settings.clients_dir, client_id)
    config = registry.get_config(client_id)

    found = client_wiring_and_document(application, client_id)
    model = ""
    problems: tuple[str, ...] = ("no settings",)
    source_problem = "Choose where your prospects come from, in your data source settings."
    if found is not None:
        raw, document = found
        source_problem = _source_problem(
            application, client_id, source_provider(raw, document), document
        )
        llm = document.get("llm")
        value = llm.get("model") if isinstance(llm, Mapping) else None
        model = value.strip() if isinstance(value, str) else ""
        problems = validate_settings_document(client_id, raw, document).errors

    return (
        _Check(
            "enabled_by_administrator",
            enabled,
            "Your agent is switched off by the platform administrator.",
        ),
        _Check(
            "loaded_by_platform",
            enabled and config is not None,
            "The platform has not loaded your agent yet. Contact your administrator.",
        ),
        _Check(
            "model_assigned",
            model.lower() not in PLACEHOLDER_MODELS,
            "No AI model is assigned to your agent yet. Contact your administrator.",
        ),
        _Check(
            "settings_valid",
            not problems,
            "Your business settings are incomplete or invalid. Review and save them.",
        ),
        _Check("source_connected", source_problem is None, source_problem or ""),
        _Check(
            "platform_connections",
            not registry.missing(client_id),
            "The platform connections for your agent are not complete. Contact your administrator.",
        ),
        _Check(
            "whatsapp_number",
            bool(profile.whatsapp_number) and not profile.whatsapp_reconnect_required,
            "Your WhatsApp number is missing, or was changed and is not connected yet.",
        ),
    )


def source_provider(raw: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    """The chosen source, else the store's provider (what `client.yaml` names), else Google Sheets."""
    section = document.get("source")
    chosen = section.get("provider") if isinstance(section, Mapping) else None
    if isinstance(chosen, str) and chosen:
        return chosen
    sheets = raw.get("sheets")
    store = sheets.get("provider") if isinstance(sheets, Mapping) else None
    return store if isinstance(store, str) and store else DEFAULT_SOURCE


def sheets_of(document: Mapping[str, Any]) -> tuple[str, str]:
    """The spreadsheet id and the prospects tab the settings document holds."""
    section = document.get("sheets")
    section = section if isinstance(section, Mapping) else {}
    return str(section.get("spreadsheet_id", "")), str(section.get("prospects_tab", "Prospects"))


def _client_credentials(descriptor: ProviderDescriptor) -> tuple[CredentialDeclaration, ...]:
    return tuple(
        c
        for c in descriptor.credentials
        if c.scope is ScopeKind.CLIENT and c.required_when is Requirement.ALWAYS
    )


def source_fingerprint(
    application: Application, client_id: str, provider: str, document: Mapping[str, Any]
) -> str:
    """What a connection test is made *for*: the source and every parameter of its connection that
    the client controls (the spreadsheet and its tab; the moment a stored secret was last written,
    never the secret). A test result counts only while this is unchanged. Opaque, holds no secret."""
    parts: dict[str, Any] = {"source": provider}
    if provider == google_sheets.KEY:
        parts["spreadsheet_id"], parts["prospects_tab"] = sheets_of(document)
    descriptor = application.provider_registry.get(provider)
    for credential in _client_credentials(descriptor) if descriptor is not None else ():
        try:
            status = application.credentials.status(client_scope(client_id), credential.key)
            parts[credential.key] = status.updated_at.isoformat() if status.updated_at else None
        except SecretStoreError:
            parts[credential.key] = "unreadable"
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def current_source_fingerprint(application: Application, client_id: str) -> str | None:
    """`source_fingerprint` of the client as it is now, or None if it has no settings at all."""
    found = client_wiring_and_document(application, client_id)
    if found is None:
        return None
    raw, document = found
    return source_fingerprint(application, client_id, source_provider(raw, document), document)


def _source_problem(
    application: Application, client_id: str, provider: str, document: Mapping[str, Any]
) -> str | None:
    """Why the prospect source the client chose cannot run its agent yet, or None.

    A source passes only when all of this holds: the runtime can run it (HubSpot, until its connector
    exists, never does), the secret it needs from the client is stored, and the client's last real
    connection test (`POST /client/v1/source/test`) succeeded **for the configuration it has now**.
    Change the source, the spreadsheet, the tab or the token and the result stops counting."""
    descriptor = application.provider_registry.get(provider)
    if descriptor is None or not descriptor.plays(ProviderKind.SOURCE):
        return "The prospect source you chose is not available. Choose another one."
    if not descriptor.runnable:
        return f"{descriptor.label} cannot run your agent yet. Choose another source to start it."
    for credential in _client_credentials(descriptor):
        try:
            stored = application.credentials.status(
                client_scope(client_id), credential.key
            ).configured
        except SecretStoreError:
            stored = False
        if not stored:
            return f"Your {credential.label} is missing. Add it in your data source settings."
    try:
        check = application.source_checks.get(client_id)
    except (SourceCheckStoreCorruptError, SourceCheckStoreUnavailableError):
        return "The result of your connection test is temporarily unavailable. Try again."
    if check is None or check.fingerprint != source_fingerprint(
        application, client_id, provider, document
    ):
        return (
            f"Test the connection to {descriptor.label}: it has not been tested "
            "since it was last changed."
        )
    if not check.ok:
        return f"The last connection test of {descriptor.label} failed. Fix it, then test again."
    return None


@contextlib.contextmanager
def switch_available() -> Iterator[None]:
    """Every read or write of the agent switch answers the same 503 when its store is down or
    unreadable, whichever store it is, and names nothing of the cause."""
    try:
        yield
    except (AgentStateStoreCorruptError, AgentStateStoreUnavailableError):
        raise HTTPException(503, "the agent switch is temporarily unavailable") from None


def _responses(checks: tuple[_Check, ...]) -> list[CheckResponse]:
    return [CheckResponse(id=c.id, ok=c.ok, message=None if c.ok else c.message) for c in checks]


def create_client_agent_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    # ---- settings -------------------------------------------------------------------------

    def settings_response(client_id: str) -> SettingsResponse:
        version, part, record = read_client_settings(application, client_id)
        brain = part.pop("brain", None)
        if brain is not None:
            part["commercial"] = brain["settings"]
        return SettingsResponse(
            version=version,
            settings=part,
            updated_at=record.created_at if record is not None else None,
            updated_by=(
                None
                if record is None
                else "client"
                if record.author == f"client:{client_id}"
                else "administrator"
            ),
        )

    @router.get("/settings", response_model=SettingsResponse)
    def get_settings(client: ClientProfile = Depends(get_current_client)) -> SettingsResponse:
        try:
            return settings_response(client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    @router.put("/settings", response_model=SettingsResponse)
    def put_settings(
        body: UpdateSettingsRequest, client: ClientProfile = Depends(get_current_client)
    ) -> SettingsResponse:
        changes = body.settings.model_dump(exclude_none=True)
        if "commercial" in changes:
            changes["brain"] = {"settings": changes.pop("commercial")}
        if not changes:
            raise HTTPException(422, "no setting to change")
        try:
            publish_client_settings(
                application,
                client.client_id,
                changes,
                expected_version=body.expected_version,
                author=f"client:{client.client_id}",
            )
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if application.registry.get_config(client.client_id) is not None:
            # The running agent takes the new settings without waiting for this request; if the
            # refresh fails, the Admin's runtime status says so and the old settings stay in force.
            with contextlib.suppress(Exception):
                application.runtime_manager.refresh_client(client.client_id)
        return settings_response(client.client_id)

    # ---- readiness and the agent ----------------------------------------------------------

    @router.get("/readiness", response_model=ReadinessResponse)
    def readiness(client: ClientProfile = Depends(get_current_client)) -> ReadinessResponse:
        checks = evaluate_readiness(application, client)
        return ReadinessResponse(ready=all(c.ok for c in checks), checks=_responses(checks))

    def agent_response(client: ClientProfile) -> AgentResponse:
        stored = application.agent_state.get(client.client_id)
        checks = evaluate_readiness(application, client)
        ready = all(c.ok for c in checks)
        state = "not_started" if stored is None else "running" if stored.running else "paused"
        return AgentResponse(
            state=state,
            active=state == "running" and ready,
            ready=ready,
            changed_at=stored.changed_at if stored is not None else None,
            reasons=[c for c in _responses(checks) if not c.ok],
        )

    @router.get("/agent", response_model=AgentResponse)
    def get_agent(client: ClientProfile = Depends(get_current_client)) -> AgentResponse:
        with switch_available():
            return agent_response(client)

    @router.post("/agent/start", response_model=AgentResponse)
    def start_agent(client: ClientProfile = Depends(get_current_client)) -> AgentResponse:
        with switch_available():
            return _start(client)

    def _start(client: ClientProfile) -> AgentResponse:
        stored = application.agent_state.get(client.client_id)
        if stored is not None and stored.running:
            return agent_response(client)  # already started: nothing to check or change
        checks = evaluate_readiness(application, client)
        if not all(c.ok for c in checks):
            raise HTTPException(
                409,
                detail={
                    "message": "your agent cannot start yet",
                    "reasons": [c.model_dump() for c in _responses(checks) if not c.ok],
                },
            )
        application.agent_state.set(
            client.client_id,
            running=True,
            by=f"client:{client.client_id}",
            at=application.clock.now(),
        )
        return agent_response(client)

    @router.post("/agent/pause", response_model=AgentResponse)
    def pause_agent(client: ClientProfile = Depends(get_current_client)) -> AgentResponse:
        with switch_available():
            return _pause(client)

    def _pause(client: ClientProfile) -> AgentResponse:
        stored = application.agent_state.get(client.client_id)
        if stored is not None and stored.running:  # a pause of what never ran changes nothing
            application.agent_state.set(
                client.client_id,
                running=False,
                by=f"client:{client.client_id}",
                at=application.clock.now(),
            )
        return agent_response(client)

    return router
