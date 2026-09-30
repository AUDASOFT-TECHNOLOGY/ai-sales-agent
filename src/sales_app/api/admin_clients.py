"""Admin API: read-only client configuration (list and detail).

Not a second source of truth. Active clients are read straight from the already-validated
`ClientRegistry` built at startup (`Application.registry`) -- the same object `/health/clients`
reads. A disabled client is deliberately excluded from that registry before it is even parsed (see
`sales_app.config.loader.resolve_client_config`), so it is shown here from its raw `client.yaml`
via `read_wiring`, the exact first step the loader itself uses -- never a parallel parser, never
re-validated.

No endpoint here can return a secret: `client.yaml` only ever names the environment variable that
holds one (the `*_env` fields), never a value, and `ClientConfig.secrets` / `phone_number_id` are
never touched by this module.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sales_app.admin_auth import AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency
from sales_app.bootstrap import Application
from sales_app.config import ClientConfig, read_wiring
from sales_app.config_store.models import CLIENT_ID_PATTERN

ClientStatus = Literal["active", "disabled"]


class ClientProvidersResponse(BaseModel):
    sheets: str | None = None
    llm: str | None = None
    whatsapp: str | None = None


class ClientSummaryResponse(BaseModel):
    client_id: str
    display_name: str
    status: ClientStatus
    mode: str | None = None
    timezone: str | None = None
    daily_action_limit: int | None = None
    providers: ClientProvidersResponse


class ClientSheetsResponse(BaseModel):
    provider: str | None = None
    spreadsheet_id: str | None = None
    prospects_tab: str | None = None
    actions_tab: str | None = None
    messages_tab: str | None = None
    log_tab: str | None = None
    credentials_env: str | None = None  # the env var NAME, never its value


class ClientLLMResponse(BaseModel):
    provider: str | None = None
    model: str | None = None
    api_key_env: str | None = None  # the env var NAME, never its value


class ClientWhatsAppResponse(BaseModel):
    provider: str | None = None
    api_version: str | None = None
    token_env: str | None = None  # the env var NAME, never its value
    phone_number_id_env: str | None = None  # the env var NAME, never the resolved phone id
    app_secret_env: str | None = None  # the env var NAME, never its value


class ClientBrainResponse(BaseModel):
    factory: str | None = None
    settings_version: int | None = None


class ClientScheduleResponse(BaseModel):
    daily_at: str | None = None
    days: list[str] = []
    prepare_every_minutes: int | None = None
    dispatch_every_minutes: int | None = None
    recover_every_minutes: int | None = None


class ClientDetailResponse(ClientSummaryResponse):
    max_attempts: int | None = None
    opt_out_keywords: list[str] = []
    sheets: ClientSheetsResponse
    llm: ClientLLMResponse
    whatsapp: ClientWhatsAppResponse
    brain: ClientBrainResponse
    schedule: ClientScheduleResponse


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_list(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _providers_from_raw(raw: dict) -> ClientProvidersResponse:
    return ClientProvidersResponse(
        sheets=_str_or_none(_section(raw, "sheets").get("provider")),
        llm=_str_or_none(_section(raw, "llm").get("provider")),
        whatsapp=_str_or_none(_section(raw, "whatsapp").get("provider")),
    )


def _summary_from_config(config: ClientConfig) -> ClientSummaryResponse:
    spec = config.spec
    return ClientSummaryResponse(
        client_id=config.client_id,
        display_name=spec.display_name,
        status="active",
        mode=str(config.effective_mode),
        timezone=spec.timezone,
        daily_action_limit=spec.daily_action_limit,
        providers=ClientProvidersResponse(
            sheets=spec.sheets.provider, llm=spec.llm.provider, whatsapp=spec.whatsapp.provider
        ),
    )


def _summary_from_raw(client_id: str, raw: dict) -> ClientSummaryResponse:
    return ClientSummaryResponse(
        client_id=client_id,
        display_name=_str_or_none(raw.get("display_name")) or client_id,
        status="disabled",
        mode=_str_or_none(raw.get("mode")),
        timezone=_str_or_none(raw.get("timezone")),
        daily_action_limit=_int_or_none(raw.get("daily_action_limit")),
        providers=_providers_from_raw(raw),
    )


def _detail_from_config(config: ClientConfig) -> ClientDetailResponse:
    spec = config.spec
    return ClientDetailResponse(
        **_summary_from_config(config).model_dump(),
        max_attempts=spec.max_attempts,
        opt_out_keywords=list(spec.opt_out_keywords),
        sheets=ClientSheetsResponse(
            provider=spec.sheets.provider,
            spreadsheet_id=spec.sheets.spreadsheet_id,
            prospects_tab=spec.sheets.prospects_tab,
            actions_tab=spec.sheets.actions_tab,
            messages_tab=spec.sheets.messages_tab,
            log_tab=spec.sheets.log_tab,
            credentials_env=spec.sheets.credentials_env,
        ),
        llm=ClientLLMResponse(
            provider=spec.llm.provider, model=spec.llm.model, api_key_env=spec.llm.api_key_env
        ),
        whatsapp=ClientWhatsAppResponse(
            provider=spec.whatsapp.provider,
            api_version=spec.whatsapp.api_version,
            token_env=spec.whatsapp.token_env,
            phone_number_id_env=spec.whatsapp.phone_number_id_env,
            app_secret_env=spec.whatsapp.app_secret_env,
        ),
        brain=ClientBrainResponse(
            factory=spec.brain.factory, settings_version=spec.brain.settings_version
        ),
        schedule=ClientScheduleResponse(
            daily_at=spec.schedule.daily_at,
            days=list(spec.schedule.days),
            prepare_every_minutes=spec.schedule.prepare_every_minutes,
            dispatch_every_minutes=spec.schedule.dispatch_every_minutes,
            recover_every_minutes=spec.schedule.recover_every_minutes,
        ),
    )


def _detail_from_raw(client_id: str, raw: dict) -> ClientDetailResponse:
    sheets, llm, whatsapp = _section(raw, "sheets"), _section(raw, "llm"), _section(raw, "whatsapp")
    brain, schedule = _section(raw, "brain"), _section(raw, "schedule")
    return ClientDetailResponse(
        **_summary_from_raw(client_id, raw).model_dump(),
        max_attempts=_int_or_none(raw.get("max_attempts")),
        opt_out_keywords=_str_list(raw.get("opt_out_keywords")),
        sheets=ClientSheetsResponse(
            provider=_str_or_none(sheets.get("provider")),
            spreadsheet_id=_str_or_none(sheets.get("spreadsheet_id")),
            prospects_tab=_str_or_none(sheets.get("prospects_tab")),
            actions_tab=_str_or_none(sheets.get("actions_tab")),
            messages_tab=_str_or_none(sheets.get("messages_tab")),
            log_tab=_str_or_none(sheets.get("log_tab")),
            credentials_env=_str_or_none(sheets.get("credentials_env")),
        ),
        llm=ClientLLMResponse(
            provider=_str_or_none(llm.get("provider")),
            model=_str_or_none(llm.get("model")),
            api_key_env=_str_or_none(llm.get("api_key_env")),
        ),
        whatsapp=ClientWhatsAppResponse(
            provider=_str_or_none(whatsapp.get("provider")),
            api_version=_str_or_none(whatsapp.get("api_version")),
            token_env=_str_or_none(whatsapp.get("token_env")),
            phone_number_id_env=_str_or_none(whatsapp.get("phone_number_id_env")),
            app_secret_env=_str_or_none(whatsapp.get("app_secret_env")),
        ),
        brain=ClientBrainResponse(
            factory=_str_or_none(brain.get("factory")),
            settings_version=_int_or_none(brain.get("settings_version")),
        ),
        schedule=ClientScheduleResponse(
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


def _all_summaries(application: Application) -> list[ClientSummaryResponse]:
    summaries = [
        _summary_from_config(runtime.config) for runtime in application.registry.runtimes()
    ]
    for client_id in _disabled_client_ids(application):
        raw, _problem = read_wiring(application.settings.clients_dir / client_id)
        summaries.append(_summary_from_raw(client_id, raw or {}))
    return sorted(summaries, key=lambda s: s.client_id)


def _find_detail(application: Application, client_id: str) -> ClientDetailResponse | None:
    runtime = application.registry.get(client_id)
    if runtime is not None:
        return _detail_from_config(runtime.config)
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    directory = application.settings.clients_dir / client_id
    if not directory.is_dir():
        return None
    raw, _problem = read_wiring(directory)
    if raw is None or raw.get("enabled", True) is not False:
        return None  # anything that isn't an active runtime or a genuinely disabled client: 404
    return _detail_from_raw(client_id, raw)


def create_admin_clients_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/clients", response_model=list[ClientSummaryResponse])
    def list_clients(admin: AdminUser = Depends(get_current_admin)) -> list[ClientSummaryResponse]:
        return [summary for summary in _all_summaries(application) if admin.can_reach(summary.client_id)]

    @router.get("/clients/{client_id}", response_model=ClientDetailResponse)
    def get_client(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> ClientDetailResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        detail = _find_detail(application, client_id)
        if detail is None:
            raise HTTPException(404, "no such client")
        return detail

    return router
