"""Admin API: read-only agent configuration (list and detail).

Today's architecture has exactly one Brain per client (see `sales_app.wiring.brains.load_brain`
and `ClientFile.brain` in `sales_app.config.schema`) -- there is no independent `Agent` entity,
registry, or storage. This module presents that Brain as "the client's agent": `agent_id` is the
client's own `client_id`, so the shape below is deliberately keyed on both `agent_id` and
`client_id` (equal for now) rather than on `client_id` alone, so that a future move to several
agents per client only has to stop treating them as equal -- the response shape does not need to
change. See `sales_app.api.admin_clients` for the sibling module this one mirrors.

Not a second source of truth: same data, same read path as Admin Clients (`ClientRegistry` for
active clients, `read_wiring` for disabled ones -- never a parallel parser, never re-validated).

No endpoint here can return a secret: only provider names and the Brain's factory path/settings
version are shown, never `*_env` values, never `ClientConfig.secrets`/`phone_number_id`.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sales_app.admin_auth import AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency
from sales_app.bootstrap import Application
from sales_app.config import ClientConfig
from sales_app.config.published import published_name
from sales_app.config_store.models import CLIENT_ID_PATTERN

AgentStatus = Literal["active", "disabled"]


class AgentBrainResponse(BaseModel):
    factory: str | None = None
    settings_version: int | None = None


class AgentLLMResponse(BaseModel):
    provider: str | None = None
    model: str | None = None


class AgentSummaryResponse(BaseModel):
    agent_id: str  # == client_id today; see module docstring
    client_id: str
    display_name: str
    status: AgentStatus
    mode: str | None = None
    brain: AgentBrainResponse
    llm: AgentLLMResponse


class AgentChannelsResponse(BaseModel):
    sheets: str | None = None  # provider name only, e.g. "google_sheets"
    whatsapp: str | None = None  # provider name only, e.g. "whatsapp"


class AgentScheduleResponse(BaseModel):
    daily_at: str | None = None
    days: list[str] = []
    prepare_every_minutes: int | None = None
    dispatch_every_minutes: int | None = None
    recover_every_minutes: int | None = None


class AgentDetailResponse(AgentSummaryResponse):
    timezone: str | None = None
    daily_action_limit: int | None = None
    max_attempts: int | None = None
    channels: AgentChannelsResponse
    schedule: AgentScheduleResponse


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_list(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _summary_from_config(config: ClientConfig) -> AgentSummaryResponse:
    spec = config.spec
    return AgentSummaryResponse(
        agent_id=config.client_id,
        client_id=config.client_id,
        display_name=spec.display_name,
        status="active",
        mode=str(config.effective_mode),
        brain=AgentBrainResponse(
            factory=spec.brain.factory, settings_version=spec.brain.settings_version
        ),
        llm=AgentLLMResponse(provider=spec.llm.provider, model=spec.llm.model),
    )


def _summary_from_raw(client_id: str, raw: dict) -> AgentSummaryResponse:
    brain, llm = _section(raw, "brain"), _section(raw, "llm")
    return AgentSummaryResponse(
        agent_id=client_id,
        client_id=client_id,
        display_name=_str_or_none(raw.get("display_name")) or client_id,
        status="disabled",
        mode=_str_or_none(raw.get("mode")),
        brain=AgentBrainResponse(
            factory=_str_or_none(brain.get("factory")),
            settings_version=_int_or_none(brain.get("settings_version")),
        ),
        llm=AgentLLMResponse(
            provider=_str_or_none(llm.get("provider")), model=_str_or_none(llm.get("model"))
        ),
    )


def _detail_from_config(config: ClientConfig) -> AgentDetailResponse:
    spec = config.spec
    return AgentDetailResponse(
        **_summary_from_config(config).model_dump(),
        timezone=spec.timezone,
        daily_action_limit=spec.daily_action_limit,
        max_attempts=spec.max_attempts,
        channels=AgentChannelsResponse(
            sheets=spec.sheets.provider, whatsapp=spec.whatsapp.provider
        ),
        schedule=AgentScheduleResponse(
            daily_at=spec.schedule.daily_at,
            days=list(spec.schedule.days),
            prepare_every_minutes=spec.schedule.prepare_every_minutes,
            dispatch_every_minutes=spec.schedule.dispatch_every_minutes,
            recover_every_minutes=spec.schedule.recover_every_minutes,
        ),
    )


def _detail_from_raw(client_id: str, raw: dict) -> AgentDetailResponse:
    sheets, whatsapp, schedule = (
        _section(raw, "sheets"),
        _section(raw, "whatsapp"),
        _section(raw, "schedule"),
    )
    return AgentDetailResponse(
        **_summary_from_raw(client_id, raw).model_dump(),
        timezone=_str_or_none(raw.get("timezone")),
        daily_action_limit=_int_or_none(raw.get("daily_action_limit")),
        max_attempts=_int_or_none(raw.get("max_attempts")),
        channels=AgentChannelsResponse(
            sheets=_str_or_none(sheets.get("provider")),
            whatsapp=_str_or_none(whatsapp.get("provider")),
        ),
        schedule=AgentScheduleResponse(
            daily_at=_str_or_none(schedule.get("daily_at")),
            days=_str_list(schedule.get("days")),
            prepare_every_minutes=_int_or_none(schedule.get("prepare_every_minutes")),
            dispatch_every_minutes=_int_or_none(schedule.get("dispatch_every_minutes")),
            recover_every_minutes=_int_or_none(schedule.get("recover_every_minutes")),
        ),
    )


def _disabled_client_ids(application: Application) -> list[str]:
    return sorted(
        str(entry["client_id"])
        for entry in application.registry.health()
        if entry.get("status") == "disabled"
    )


def _with_published_name[M: (AgentSummaryResponse, AgentDetailResponse)](
    application: Application, shown: M
) -> M:
    """`shown` with the newest published name: a client renames itself without its agent being
    rebuilt, and the Admin sees the name the client sees (as in `sales_app.api.admin_clients`)."""
    name = published_name(application.config_store, shown.client_id)
    if name is None or name == shown.display_name:
        return shown
    return shown.model_copy(update={"display_name": name})


def _all_summaries(application: Application) -> list[AgentSummaryResponse]:
    summaries = [
        _with_published_name(application, _summary_from_config(config))
        for config in application.registry.configs()
    ]
    for client_id in _disabled_client_ids(application):
        raw, _problem = application.client_wiring(client_id)
        summaries.append(_with_published_name(application, _summary_from_raw(client_id, raw or {})))
    return sorted(summaries, key=lambda s: s.agent_id)


def _find_detail(application: Application, agent_id: str) -> AgentDetailResponse | None:
    # agent_id == client_id today (see module docstring).
    config = application.registry.get_config(agent_id)
    if config is not None:
        return _with_published_name(application, _detail_from_config(config))
    if not CLIENT_ID_PATTERN.match(agent_id):
        return None
    raw, _problem = application.client_wiring(agent_id)
    if raw is None or raw.get("enabled", True) is not False:
        return None  # anything that isn't an active runtime or a genuinely disabled client: 404
    return _with_published_name(application, _detail_from_raw(agent_id, raw))


def create_admin_agents_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/agents", response_model=list[AgentSummaryResponse])
    def list_agents(admin: AdminUser = Depends(get_current_admin)) -> list[AgentSummaryResponse]:
        return [
            summary for summary in _all_summaries(application) if admin.can_reach(summary.client_id)
        ]

    @router.get("/agents/{agent_id}", response_model=AgentDetailResponse)
    def get_agent(
        agent_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> AgentDetailResponse:
        ensure_client_access(admin, agent_id)  # 404 before existence is even checked
        detail = _find_detail(application, agent_id)
        if detail is None:
            raise HTTPException(404, "no such agent")
        return detail

    return router
