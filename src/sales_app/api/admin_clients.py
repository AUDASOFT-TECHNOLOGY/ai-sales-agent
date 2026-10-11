"""Admin API: client configuration -- read-only list/detail, plus the one writable field so far:
a client's LLM provider and model.

Not a second source of truth. Active clients are read straight from the already-validated
`ClientRegistry` built at startup (`Application.registry`) -- the same object `/health/clients`
reads. A disabled client is deliberately excluded from that registry before it is even parsed (see
`sales_app.config.loader.resolve_client_config`), so it is shown here from its raw `client.yaml`
via `read_wiring`, the exact first step the loader itself uses -- never a parallel parser, never
re-validated.

No endpoint here can return a secret: `client.yaml` only ever names the environment variable that
holds one (the `*_env` fields), never a value, and `ClientConfig.secrets` / `phone_number_id` are
never touched by this module.

The LLM update below writes through `ConfigStore.publish` (the same versioned store the CLI's
`sales-agent config publish` uses), never a raw file write, and is gated by the same guardrails
`resolve_client_config` enforces at load time: `provider` must be wired (`Application.llm_providers`)
and `provider`/`model` must be allowed by the deployment's `LLMRegistry`. Every settings change
published here (the model, the name, the quota) is applied to the running client at once, through the
same `RuntimeManager` refresh a changed credential triggers; a client with no runtime yet (disabled,
or not started) is shown from its published settings and picks them up when it starts.
"""

import base64
import binascii
import contextlib
import re
import shutil
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.admin_auth.passwords import hash_password
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.client_profiles import LOGIN_PATTERN, ClientProfile, normalize_login
from sales_app.config import (
    ClientConfig,
    build_llm_registry,
    extract_document,
    import_document,
    read_wiring,
    validate_settings_document,
)
from sales_app.config.published import published_document, published_name, published_view
from sales_app.config.resolution import (
    SETTINGS_SCHEMA_VERSION,
    SETTINGS_SECTION_KEYS,
    SETTINGS_TOP_KEYS,
)
from sales_app.config_store import ConfigConflictError, ConfigVersion
from sales_app.config_store.models import CLIENT_ID_PATTERN, thaw_document
from sales_app.llm_models import check_model
from sales_app.logs import client_logger
from sales_app.secret_store import client_scope
from sales_app.wiring import ClientRuntime
from sales_app.wiring.providers import ProviderKind

# How long a request waits for the running client to take a published change. The refresh goes on
# in the background after that; `GET /clients/{id}/runtime-status` says how it ended.
REFRESH_WAIT_SECONDS = 5.0

ClientStatus = Literal["active", "disabled"]


class ClientProvidersResponse(BaseModel):
    sheets: str | None = None
    llm: str | None = None
    whatsapp: str | None = None


class ClientHealthResponse(BaseModel):
    """What `ClientRegistry.health()` already reports for an active client -- nothing computed
    here. Absent (None) for a disabled client: it has no runtime, hence nothing to report."""

    status: str  # "ready" or "degraded"
    last_error: str | None = None
    last_error_at: str | None = None
    consecutive_failures: int = 0
    last_runs: dict[str, str] = {}


class ClientSummaryResponse(BaseModel):
    client_id: str
    display_name: str
    status: ClientStatus
    mode: str | None = None
    timezone: str | None = None
    daily_action_limit: int | None = None
    providers: ClientProvidersResponse
    llm_model: str | None = None  # the selected provider model id -- never a secret
    health: ClientHealthResponse | None = None  # active clients only


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


class UpdateClientLLMRequest(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


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


class ClientAccountResponse(BaseModel):
    """The client's account profile (see `sales_app.client_profiles`). Never carries the password
    or its hash. `whatsapp_number` is the business number the client's sales agent writes from; it
    is NOT Meta's phone number id."""

    login: str
    whatsapp_number: str | None = None
    logo: str | None = None
    must_change_password: bool = True


class ClientMetaResponse(BaseModel):
    """Where this client's number stands with Meta. Only ever `not_connected` or
    `credentials_stored`: there is no official Meta onboarding in Revansa yet, so nothing here can
    say "connected" (that would need a verified onboarding). Booleans only -- never a token or a
    Meta id."""

    status: Literal["not_connected", "credentials_stored"] = "not_connected"
    credentials_stored: bool = False


class CreateClientRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    logo: str | None = None  # optional; a small png/jpeg/webp `data:` URL
    login: str
    temporary_password: str = Field(min_length=8, max_length=128)
    llm_provider: str = Field(min_length=1)
    llm_model: str = Field(min_length=1)
    status: Literal["active", "inactive"]
    whatsapp_number: str = Field(min_length=1, max_length=40)


class UpdateClientRequest(BaseModel):
    """A partial edit: only the fields sent are changed. `logo` may be sent as null to remove it.
    `new_password` is optional: left out, the current password is kept. There is deliberately no
    field for any Meta credential."""

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    logo: str | None = None
    login: str | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=128)
    whatsapp_number: str | None = Field(default=None, min_length=1, max_length=40)
    llm_provider: str | None = Field(default=None, min_length=1)
    llm_model: str | None = Field(default=None, min_length=1)
    daily_action_limit: int | None = Field(default=None, ge=1, le=1000)
    status: Literal["active", "inactive"] | None = None


class ClientDetailResponse(ClientSummaryResponse):
    # True when the stored status (client.yaml `enabled`) differs from what is running: a client's
    # runtime is only started or stopped at application start.
    restart_required: bool = False
    account: ClientAccountResponse | None = None  # None: no profile on record for this client
    meta: ClientMetaResponse = ClientMetaResponse()
    revenue: float | None = None  # always None for now: no revenue data source exists yet
    prospects_contacted: int | None = None  # None: not readable (disabled client, or Sheet error)
    max_attempts: int | None = None
    opt_out_keywords: list[str] = []
    sheets: ClientSheetsResponse
    llm: ClientLLMResponse
    whatsapp: ClientWhatsAppResponse
    brain: ClientBrainResponse
    schedule: ClientScheduleResponse


MAX_LOGO_BYTES = 256 * 1024
_LOGO_PATTERN = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$")
_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}


def _check_logo(logo: str) -> None:
    """A logo is stored as a data URL, so it must be a small, genuine raster image. SVG is refused
    on purpose: it can carry script."""
    match = _LOGO_PATTERN.match(logo)
    if match is None:
        raise HTTPException(400, "logo must be a png, jpeg or webp image")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except binascii.Error:
        raise HTTPException(400, "logo is not valid base64") from None
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(400, f"logo must be at most {MAX_LOGO_BYTES // 1024} KB")
    if not data.startswith(_MAGIC[match.group(1)]):
        raise HTTPException(400, "logo content does not match its image type")


_WHATSAPP_NUMBER_PATTERN = re.compile(r"^\+?[0-9][0-9 ().-]*$")


def _clean_whatsapp_number(value: str) -> str:
    """The business number as entered, whitespace collapsed. Only its shape is checked (8-15
    digits, the E.164 range): entering a number is not Meta authorising it."""
    number = " ".join(value.split())
    digits = _digits(number)
    if not _WHATSAPP_NUMBER_PATTERN.match(number) or not 8 <= len(digits) <= 15:
        raise HTTPException(
            400, "WhatsApp number must be a phone number with 8 to 15 digits, e.g. +216 22 768 307"
        )
    return number


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def _meta_status(application: Application, client_id: str) -> ClientMetaResponse:
    """`credentials_stored` only if the secret store holds both Meta values for this client. That
    is a fact about storage, not about Meta: nothing verifies it, so it is never "connected"."""
    stored = {
        s.key for s in application.credentials.describe(client_scope(client_id)) if s.configured
    }
    complete = {"whatsapp.access_token", "whatsapp.phone_number_id"} <= stored
    return ClientMetaResponse(
        status="credentials_stored" if complete else "not_connected", credentials_stored=complete
    )


def _slug(display_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")


def _free_client_id(application: Application, base: str) -> str:
    taken = {p.client_id for p in application.client_profiles.list()}
    candidate, n = base, 2
    while candidate in taken or (application.settings.clients_dir / candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _new_client_yaml(client_id: str, display_name: str, provider: str, model: str) -> dict:
    """A new client always starts disabled: it has no Brain, Sheet or WhatsApp wiring yet, and an
    enabled client that fails validation would stop the whole application at its next start."""
    return {
        "client_id": client_id,
        "display_name": display_name,
        "enabled": False,
        "mode": "dry_run",
        "timezone": "UTC",
        "daily_action_limit": 20,
        "max_attempts": 3,
        "opt_out_keywords": ["stop"],
        "brain": {"factory": "TODO", "settings": {}},
        "sheets": {
            "provider": "google_sheets",
            "spreadsheet_id": "TODO",
            "prospects_tab": "Prospects",
            "actions_tab": "Actions",
            "messages_tab": "Messages",
            "log_tab": "Log",
            "column_mapping": {},
            "credentials_env": "GOOGLE_APPLICATION_CREDENTIALS",
        },
        "llm": {"provider": provider, "api_key_env": "OPENAI_API_KEY", "model": model},
        # No credential names, and not the client's WhatsApp number (that is profile data): Meta's
        # access token and phone number id come from the WhatsApp onboarding and live in the
        # credential store (see sales_app.secret_store).
        "whatsapp": {"provider": "whatsapp"},
        "schedule": {
            "daily_at": "09:00",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
            "prepare_every_minutes": 60,
            "dispatch_every_minutes": 5,
            "recover_every_minutes": 15,
        },
    }


def _prospects_contacted(runtime: ClientRuntime) -> int | None:
    """Distinct prospects that received at least one sent message (the Core's own count). Read-only; None if the client's
    data cannot be read right now (the page then says so instead of showing a made-up number)."""
    try:
        with runtime.lease() as engine:
            return engine.prospects_contacted()
    except Exception:  # noqa: BLE001 - an unreachable Sheet (or a client not ready) must not break the page
        return None


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


def _health_from_runtime(runtime: ClientRuntime) -> ClientHealthResponse:
    snapshot = runtime.health.snapshot()
    return ClientHealthResponse(
        status=str(snapshot["status"]),
        last_error=snapshot["last_error"],  # type: ignore[arg-type]
        last_error_at=snapshot["last_error_at"],  # type: ignore[arg-type]
        consecutive_failures=int(snapshot["consecutive_failures"]),  # type: ignore[call-overload]
        last_runs=dict(snapshot["last_runs"]),  # type: ignore[call-overload]
    )


def _health_for(application: Application, config: ClientConfig) -> ClientHealthResponse | None:
    """A client's health; one that lacks credentials reports `not_ready` and what is missing."""
    missing = application.registry.missing(config.client_id)
    if missing:
        return ClientHealthResponse(
            status="not_ready", last_error=f"missing credentials: {'; '.join(missing)}"
        )
    runtime = application.registry.get(config.client_id)
    return _health_from_runtime(runtime) if runtime is not None else None


def _summary_from_config(
    config: ClientConfig, health: ClientHealthResponse | None = None
) -> ClientSummaryResponse:
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
        llm_model=spec.llm.model,
        health=health,
    )


def _summary_from_raw(client_id: str, raw: dict) -> ClientSummaryResponse:
    return ClientSummaryResponse(
        client_id=client_id,
        display_name=_str_or_none(raw.get("display_name")) or client_id,
        status="disabled" if raw.get("enabled", True) is False else "active",
        mode=_str_or_none(raw.get("mode")),
        timezone=_str_or_none(raw.get("timezone")),
        daily_action_limit=_int_or_none(raw.get("daily_action_limit")),
        providers=_providers_from_raw(raw),
        llm_model=_str_or_none(_section(raw, "llm").get("model")),
    )


def _detail_from_config(
    config: ClientConfig, health: ClientHealthResponse | None = None
) -> ClientDetailResponse:
    spec = config.spec
    return ClientDetailResponse(
        **_summary_from_config(config, health).model_dump(),
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


def _with_published_name[M: (ClientSummaryResponse, ClientDetailResponse)](
    application: Application, shown: M
) -> M:
    """`shown` for a running client, with the newest published name. Everything else shown is
    what runs; the name is the one thing a client changes for itself without its agent being
    rebuilt (`rename_client`), and the Admin must see it at once, as the client does."""
    name = published_name(application.config_store, shown.client_id)
    if name is None or name == shown.display_name:
        return shown
    return shown.model_copy(update={"display_name": name})


def _all_summaries(application: Application) -> list[ClientSummaryResponse]:
    summaries = [
        _with_published_name(
            application, _summary_from_config(config, _health_for(application, config))
        )
        for config in application.registry.configs()
    ]
    for client_id in _disabled_client_ids(application):
        raw, _problem = read_wiring(application.settings.clients_dir / client_id)
        summaries.append(_summary_from_raw(client_id, _published_view(application, client_id, raw)))
    return sorted(summaries, key=lambda s: s.client_id)


def _find_detail(application: Application, client_id: str) -> ClientDetailResponse | None:
    config = application.registry.get_config(client_id)
    if config is not None:
        detail = _detail_from_config(config, _health_for(application, config))
        return _with_published_name(application, detail)
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    directory = application.settings.clients_dir / client_id
    if not directory.is_dir():
        return None
    raw, _problem = read_wiring(directory)
    if raw is None:
        return None  # not a client directory: 404
    # Disabled, or activated and awaiting a restart: what it will run on is its published settings.
    return _detail_from_raw(client_id, _published_view(application, client_id, raw))


def _wiring_and_base_document(application: Application, client_id: str) -> tuple[dict, dict] | None:
    """The raw `client.yaml` plus the current settings document to build a new version on top of
    (so every other setting survives), for any client the Admin API can see (same existence rules
    as `_find_detail`). None if there is no such client.

    The document is the newest one published for the client; only with none published is it read
    from the running configuration (or, with no runtime, from `client.yaml`). A running client can
    lag behind what was published (a refresh still going, or one that failed), and a new version
    built from it would silently undo the change that has not caught up: the model the Admin
    assigned, above all, whoever makes the next change (the Admin's PATCH, or the client renaming
    itself)."""
    config = application.registry.get_config(client_id)
    if config is not None:
        raw, _problem = read_wiring(config.directory)
        if raw is None:
            return None
        published = published_document(application.config_store, client_id)
        return raw, published if published is not None else extract_document(config.spec)
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    directory = application.settings.clients_dir / client_id
    if not directory.is_dir():
        return None
    raw, _problem = read_wiring(directory)
    if raw is None:
        return None
    published = published_document(application.config_store, client_id)
    if published is not None:
        return raw, published
    seeded = import_document(client_id, raw)
    if seeded.errors or seeded.document is None:
        return None  # a disabled client whose client.yaml is itself invalid
    return raw, seeded.document


def _published_view(application: Application, client_id: str, raw: dict | None) -> dict:
    """What a client without a runtime will run on: its `client.yaml` with the settings version
    published for it laid over (see `sales_app.config.published`)."""
    return published_view(application.config_store, client_id, raw)


def _provider_problem(application: Application, provider: str) -> str | None:
    """Why the Admin may not give a client this provider, or None: it must be wired here."""
    if provider in application.llm_providers:
        return None
    known = application.provider_registry.get(provider)
    note = (
        " (it is known, but no adapter can run it yet)"
        if known is not None and known.plays(ProviderKind.LLM)
        else ""
    )
    return f"llm.provider {provider!r} is not wired in this deployment{note}"


def _model_problem(application: Application, provider: str, model: str) -> str | None:
    """Why the Admin may not give a client this model under a wired provider, or None: the
    deployment's allow-list, then the models configured in Settings (see `check_model`)."""
    return check_model(
        build_llm_registry(application.settings.allowed_llm_models),
        application.credentials,
        application.provider_registry,
        provider,
        model,
    )


def _assignment_problem(application: Application, provider: str, model: str) -> str | None:
    """The one rule for every route that assigns a model (creation, `PATCH`, `PUT .../llm`): the
    provider must be wired here, then the model must be one the Admin may assign."""
    return _provider_problem(application, provider) or _model_problem(application, provider, model)


def _apply_now(application: Application, client_id: str) -> None:
    """Have the running client take the settings just published for it, without a restart.

    The same refresh a changed credential triggers: the client is resolved again, a new Engine is
    built beside the running one, and it replaces it once no job is using it. Waits a few seconds so
    the change is usually in force when the request returns; if not, it finishes in the background.
    Never fails the request: the settings are already published, and `runtime-status` reports how
    the refresh ended. A client with no runtime and no config in the registry (disabled, or not
    started) has nothing to refresh."""
    if application.registry.get_config(client_id) is None:
        return
    future = application.runtime_manager.refresh_client(client_id)
    with contextlib.suppress(Exception):  # still applying, or failed: the status says which
        future.result(timeout=REFRESH_WAIT_SECONDS)


_ENABLED_LINE = re.compile(r"^enabled:.*$", re.MULTILINE)


def _stored_enabled(application: Application, client_id: str) -> bool | None:
    """`enabled` as written in the client's `client.yaml` (None: no readable client.yaml)."""
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    raw, _problem = read_wiring(application.settings.clients_dir / client_id)
    return None if raw is None else raw.get("enabled", True) is not False


def _pause_client_switch(application: Application, client_id: str, by: str) -> None:
    """Stop the client's own switch (it never touches `enabled`): called whenever the Admin changes
    a client's status, so that a client the Admin disabled and enabled again is not started by an
    old decision. A client that never started its agent has nothing to pause."""
    state = application.agent_state.get(client_id)
    if state is not None and state.running:
        application.agent_state.set(client_id, running=False, by=by, at=application.clock.now())


def _write_enabled(application: Application, client_id: str, enabled: bool) -> None:
    """Flip only the `enabled` line, so the operator's comments and layout in client.yaml survive."""
    path = application.settings.clients_dir / client_id / "client.yaml"
    text = path.read_text(encoding="utf-8")
    line = f"enabled: {'true' if enabled else 'false'}"
    if _ENABLED_LINE.search(text):
        text = _ENABLED_LINE.sub(line, text, count=1)
    else:
        text = f"{line}\n{text}"
    path.write_text(text, encoding="utf-8")


def _unvalidated_wiring(application: Application, client_id: str) -> dict | None:
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    directory = application.settings.clients_dir / client_id
    if not directory.is_dir():
        return None
    return read_wiring(directory)[0]


def _write_skeleton_settings(
    application: Application, client_id: str, raw: dict, changes: dict
) -> None:
    updated = {**raw}
    for key in ("display_name", "daily_action_limit"):
        if key in changes:
            updated[key] = changes[key]
    if "llm" in changes:
        updated["llm"] = {**_section(raw, "llm"), **changes["llm"]}
    path = application.settings.clients_dir / client_id / "client.yaml"
    path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")


def _with_stored_status(
    application: Application, summary: ClientSummaryResponse
) -> ClientSummaryResponse:
    stored = _stored_enabled(application, summary.client_id)
    if stored is None:
        return summary
    return summary.model_copy(update={"status": "active" if stored else "disabled"})


def rename_client(application: Application, client_id: str, name: str, *, author: str) -> None:
    """Change a client's business name where every other writer changes it (the versioned settings,
    or the skeleton `client.yaml` of a client without a Brain yet), so there is never a second copy
    of the name. Also used by the Client API when a client renames itself: this module stays the
    only API layer that publishes settings.

    It does not refresh the running client. A rename from the Client API is the client's own and
    could be repeated at will, each time rebuilding an Engine on the one worker every client's
    refresh shares; the Client API reads the published name, so the client sees it at once. The
    running agent takes the new name at the next refresh of that client (any change the Admin
    publishes or a credential change) or at the next start."""
    found = _wiring_and_base_document(application, client_id)
    raw = found[0] if found is not None else _unvalidated_wiring(application, client_id)
    if raw is None:
        raise HTTPException(404, "no such client")
    if found is None:
        _write_skeleton_settings(application, client_id, raw, {"display_name": name})
        return
    check = validate_settings_document(client_id, raw, {**found[1], "display_name": name})
    if check.errors or check.document is None:
        raise HTTPException(400, "; ".join(check.errors) or "invalid name")
    current = application.config_store.current(client_id)
    try:
        application.config_store.publish(
            client_id,
            check.document,
            author=author,
            note="rename client",
            expected_parent=current.version if current is not None else None,
        )
    except ConfigConflictError as error:
        raise HTTPException(409, str(error)) from error


# ---- the settings a client may edit itself -------------------------------------------------------

# A stored settings document holds more than a client may touch: the model (the Admin's), the source
# (its own route), the mode, the quota and the inbound policy (the platform's). The Client API reads and writes
# exactly this part and nothing else; anything outside it is refused before it reaches a document.
CLIENT_SETTINGS_TOP_KEYS = ("timezone", "opt_out_keywords")
CLIENT_SETTINGS_SECTIONS: dict[str, tuple[str, ...]] = {
    "schedule": ("daily_at", "days"),
    "brain": ("settings",),
}


def _skeleton_document(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a `client.yaml` holds, as a document, without judging them: the starting point
    of a client whose Brain settings are not written yet (a skeleton), which no validation of the
    file alone could accept. The first complete settings it publishes replace it."""
    document: dict[str, Any] = {"schema_version": SETTINGS_SCHEMA_VERSION}
    for key in SETTINGS_TOP_KEYS:
        if key in raw:
            document[key] = thaw_document(raw[key])
    for section, keys in SETTINGS_SECTION_KEYS.items():
        found = raw.get(section)
        if isinstance(found, Mapping):
            kept = {k: thaw_document(found[k]) for k in keys if k in found}
            if kept:
                document[section] = kept
    return document


def client_wiring_and_document(
    application: Application, client_id: str
) -> tuple[dict, dict] | None:
    """The operator wiring and the settings document a client's change is built on: what
    `_wiring_and_base_document` gives, or, for a skeleton client whose `client.yaml` does not
    validate yet, the settings that file holds. None only if there is no such client."""
    found = _wiring_and_base_document(application, client_id)
    if found is not None:
        return found
    raw = _unvalidated_wiring(application, client_id)
    return None if raw is None else (raw, _skeleton_document(raw))


def client_settings_part(document: Mapping[str, Any]) -> dict[str, Any]:
    """What of a settings document a client may see and edit."""
    part: dict[str, Any] = {k: document[k] for k in CLIENT_SETTINGS_TOP_KEYS if k in document}
    for section, keys in CLIENT_SETTINGS_SECTIONS.items():
        found = document.get(section)
        if isinstance(found, Mapping):
            kept = {k: found[k] for k in keys if k in found}
            if kept:
                part[section] = kept
    return thaw_document(part)


def read_client_settings(
    application: Application, client_id: str
) -> tuple[int | None, dict[str, Any], ConfigVersion | None]:
    """The version in force (None: nothing published yet), the part a client may edit and the
    version record. 404 for a client that does not exist."""
    found = client_wiring_and_document(application, client_id)
    if found is None:
        raise HTTPException(404, "no such client")
    current = application.config_store.current(client_id)
    return (
        current.version if current is not None else None,
        client_settings_part(found[1]),
        current,
    )


def publish_client_settings(
    application: Application,
    client_id: str,
    changes: Mapping[str, Any],
    *,
    expected_version: int | None,
    author: str,
) -> ConfigVersion:
    """Publish a client's own change on top of the settings in force, as a new version.

    `changes` holds only keys a client may edit (the caller's model refuses the others); a missing
    key keeps its value, so no other setting (the model, the source, the quota) can be lost. The
    result is validated like every other publication, the Brain's rules included, and **nothing is
    written** if it is not valid (422 with every problem) or if the settings moved since the client
    loaded them (409). It does not rebuild the running agent: the caller decides when."""
    found = client_wiring_and_document(application, client_id)
    if found is None:
        raise HTTPException(404, "no such client")
    raw, base = found
    current = application.config_store.current(client_id)
    if (current.version if current is not None else None) != expected_version:
        raise HTTPException(409, "the settings changed since they were loaded; reload and retry")
    document = thaw_document(base)
    for key in CLIENT_SETTINGS_TOP_KEYS:
        if key in changes:
            document[key] = changes[key]
    for section, keys in CLIENT_SETTINGS_SECTIONS.items():
        if section in changes:
            merged = dict(document.get(section) or {})
            merged.update({k: changes[section][k] for k in keys if k in changes[section]})
            document[section] = merged
    check = validate_settings_document(client_id, raw, document)
    if check.errors or check.document is None:
        raise HTTPException(422, detail=list(check.errors) or ["invalid settings"])
    try:
        return application.config_store.publish(
            client_id,
            check.document,
            author=author,
            note="client settings",
            expected_parent=current.version if current is not None else None,
        )
    except ConfigConflictError as error:
        raise HTTPException(
            409, "the settings changed since they were loaded; reload and retry"
        ) from error


def create_admin_clients_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/clients", response_model=list[ClientSummaryResponse])
    def list_clients(admin: AdminUser = Depends(get_current_admin)) -> list[ClientSummaryResponse]:
        return [
            _with_stored_status(application, summary)
            for summary in _all_summaries(application)
            if admin.can_reach(summary.client_id)
        ]

    def detail_response(client_id: str) -> ClientDetailResponse:
        detail = _find_detail(application, client_id)
        if detail is None:
            raise HTTPException(404, "no such client")
        profile = application.client_profiles.get(client_id)
        runtime = application.registry.get(client_id)
        stored = _stored_enabled(application, client_id)
        running = application.registry.get_config(client_id) is not None
        return detail.model_copy(
            update={
                "status": detail.status if stored is None else ("active" if stored else "disabled"),
                "restart_required": stored is not None and stored != running,
                "account": (
                    ClientAccountResponse(
                        login=profile.login,
                        whatsapp_number=profile.whatsapp_number,
                        logo=profile.logo,
                        must_change_password=profile.must_change_password,
                    )
                    if profile is not None
                    else None
                ),
                "meta": _meta_status(application, client_id),
                "prospects_contacted": _prospects_contacted(runtime) if runtime else None,
            }
        )

    @router.get("/clients/{client_id}", response_model=ClientDetailResponse)
    def get_client(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> ClientDetailResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        return detail_response(client_id)

    @router.patch("/clients/{client_id}", response_model=ClientDetailResponse)
    def update_client(
        client_id: str,
        body: UpdateClientRequest,
        admin: AdminUser = Depends(require_owner),
    ) -> ClientDetailResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        found = _wiring_and_base_document(application, client_id)
        profile = application.client_profiles.get(client_id)
        # A client that only exists as the skeleton `client.yaml` its creation wrote (no Brain yet)
        # has no valid settings document: its name, quota and model are edited in that file.
        raw = found[0] if found is not None else _unvalidated_wiring(application, client_id)
        if raw is None:
            raise HTTPException(404, "no such client")
        base_document = found[1] if found is not None else None
        sent = body.model_fields_set
        others = [p for p in application.client_profiles.list() if p.client_id != client_id]

        changes: dict = {}  # profile fields
        document_changes: dict = {}  # settings-document fields
        if body.display_name is not None:
            display_name = body.display_name.strip()
            if not display_name:
                raise HTTPException(400, "client name must not be empty")
            document_changes["display_name"] = display_name
        if body.login is not None:
            login = normalize_login(body.login)
            if not LOGIN_PATTERN.match(login):
                raise HTTPException(
                    400, "login must be 3-64 characters: letters, digits and . _ @ + - only"
                )
            holder = application.client_profiles.get_by_login(login)
            if holder is not None and holder.client_id != client_id:
                raise HTTPException(409, "this login is already used")
            changes["login"] = login
        if body.new_password is not None:
            # An admin-set password is a temporary one, as at creation: the client must replace it.
            changes["password_hash"] = hash_password(body.new_password)
            changes["must_change_password"] = True
        if "logo" in sent:
            if body.logo is not None:
                _check_logo(body.logo)
            changes["logo"] = body.logo
        if body.whatsapp_number is not None:
            whatsapp_number = _clean_whatsapp_number(body.whatsapp_number)
            if any(
                p.whatsapp_number and _digits(p.whatsapp_number) == _digits(whatsapp_number)
                for p in others
            ):
                raise HTTPException(409, "this WhatsApp number already belongs to another client")
            changes["whatsapp_number"] = whatsapp_number
        if (body.llm_provider is None) != (body.llm_model is None):
            raise HTTPException(400, "llm_provider and llm_model must be sent together")
        if body.llm_provider is not None and body.llm_model is not None:
            provider, model = body.llm_provider.strip(), body.llm_model.strip()
            problem = _assignment_problem(application, provider, model)
            if problem is not None:
                raise HTTPException(400, problem)
            document_changes["llm"] = {"provider": provider, "model": model}
        if body.daily_action_limit is not None:
            document_changes["daily_action_limit"] = body.daily_action_limit
        if body.status == "active" and _stored_enabled(application, client_id) is False:
            # Same guard as creation: an enabled client that fails validation would stop the whole
            # application at its next start.
            check = import_document(client_id, raw)
            if check.errors:
                raise HTTPException(
                    400,
                    "this client cannot be activated yet: " + "; ".join(check.errors),
                )

        if (
            changes
            and profile is None
            and not {"login", "password_hash", "whatsapp_number"} <= (changes.keys())
        ):
            raise HTTPException(
                400,
                "this client has no account profile yet: send login, new_password and "
                "whatsapp_number together to create it",
            )
        if document_changes and base_document is None:
            _write_skeleton_settings(application, client_id, raw, document_changes)
        elif document_changes and base_document is not None:
            check = validate_settings_document(
                client_id, raw, {**base_document, **document_changes}
            )
            if check.errors or check.document is None:
                raise HTTPException(400, "; ".join(check.errors) or "invalid configuration")
            current = application.config_store.current(client_id)
            try:
                application.config_store.publish(
                    client_id,
                    check.document,
                    author=f"admin:{admin.username}",
                    note="update client",
                    expected_parent=current.version if current is not None else None,
                )
            except ConfigConflictError as error:
                raise HTTPException(409, str(error)) from error
            _apply_now(application, client_id)
        if changes:
            try:
                if profile is None:  # first account for a client that only had a client.yaml
                    application.client_profiles.create(
                        ClientProfile(
                            client_id=client_id,
                            created_at=application.clock.now(),
                            created_by=f"admin:{admin.username}",
                            **changes,
                        )
                    )
                else:
                    application.client_profiles.update(replace(profile, **changes))
            except ValueError as error:  # e.g. a concurrent request took the same login
                raise HTTPException(409, str(error)) from error
        if body.status is not None:
            enabled = body.status == "active"
            if _stored_enabled(application, client_id) is not enabled:
                # Disabled then enabled again, an agent restarts only when its client says so.
                by = f"admin:{admin.username}"
                if enabled:
                    # Pause first: if the switch cannot be written, the client must not come back
                    # on an old "started" decision, so the change is refused.
                    try:
                        _pause_client_switch(application, client_id, by)
                    except Exception:  # noqa: BLE001 - any store failure means the same
                        raise HTTPException(503, "the agent switch is unavailable") from None
                    _write_enabled(application, client_id, enabled)
                else:
                    # Disabling never depends on the client's switch: `enabled` alone stops the
                    # work, so it is written first and a store failure cannot get in the way.
                    _write_enabled(application, client_id, enabled)
                    try:
                        _pause_client_switch(application, client_id, by)
                    except Exception:  # noqa: BLE001 - the client is disabled whatever happens here
                        client_logger(__name__, client_id).warning(
                            "the agent switch could not be paused; the client is disabled anyway"
                        )
            else:
                _write_enabled(application, client_id, enabled)

        return detail_response(client_id)

    @router.post("/clients", response_model=ClientSummaryResponse, status_code=201)
    def create_client(
        body: CreateClientRequest, admin: AdminUser = Depends(require_owner)
    ) -> ClientSummaryResponse:
        display_name = body.display_name.strip()
        login = normalize_login(body.login)
        provider, model = body.llm_provider.strip(), body.llm_model.strip()
        if not display_name:
            raise HTTPException(400, "client name must not be empty")
        if not LOGIN_PATTERN.match(login):
            raise HTTPException(
                400, "login must be 3-64 characters: letters, digits and . _ @ + - only"
            )
        if body.status == "active":
            raise HTTPException(
                400,
                "a new client cannot be active yet: its Brain, Google Sheet and WhatsApp are "
                "not configured. Create it as inactive.",
            )
        problem = _assignment_problem(application, provider, model)
        if problem is not None:
            raise HTTPException(400, problem)
        if body.logo is not None:
            _check_logo(body.logo)
        whatsapp_number = _clean_whatsapp_number(body.whatsapp_number)
        if any(
            p.whatsapp_number and _digits(p.whatsapp_number) == _digits(whatsapp_number)
            for p in application.client_profiles.list()
        ):
            raise HTTPException(409, "this WhatsApp number already belongs to another client")
        base = _slug(display_name)
        if not base or not CLIENT_ID_PATTERN.match(base):
            raise HTTPException(400, "client name must contain letters or digits")
        if application.client_profiles.get_by_login(login) is not None:
            raise HTTPException(409, "this login is already used")

        client_id = _free_client_id(application, base)
        directory = application.settings.clients_dir / client_id
        directory.mkdir(parents=True)
        try:
            raw = _new_client_yaml(client_id, display_name, provider, model)
            (directory / "client.yaml").write_text(
                yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
            )
            application.client_profiles.create(
                ClientProfile(
                    client_id=client_id,
                    login=login,
                    password_hash=hash_password(body.temporary_password),
                    logo=body.logo,
                    whatsapp_number=whatsapp_number,
                    created_at=application.clock.now(),
                    created_by=f"admin:{admin.username}",
                )
            )
        except ValueError as error:  # e.g. a concurrent request took the same login
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(409, str(error)) from error
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        application.registry.add_disabled(client_id)
        return _summary_from_raw(client_id, raw)

    @router.put("/clients/{client_id}/llm", response_model=ClientLLMResponse)
    def update_client_llm(
        client_id: str,
        body: UpdateClientLLMRequest,
        admin: AdminUser = Depends(require_owner),  # only an owner assigns a model
    ) -> ClientLLMResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        provider, model = body.provider.strip(), body.model.strip()

        problem = _provider_problem(application, provider)
        if problem is not None:
            raise HTTPException(400, problem)

        found = _wiring_and_base_document(application, client_id)
        if found is None:
            raise HTTPException(404, "no such client")  # before the model: the URL comes first
        problem = _model_problem(application, provider, model)
        if problem is not None:
            raise HTTPException(400, problem)
        raw, base_document = found
        document = {**base_document, "llm": {"provider": provider, "model": model}}

        check = validate_settings_document(client_id, raw, document)
        if check.errors or check.document is None or check.spec is None:
            raise HTTPException(400, "; ".join(check.errors) or "invalid configuration")

        current = application.config_store.current(client_id)
        try:
            application.config_store.publish(
                client_id,
                check.document,
                author=f"admin:{admin.username}",
                note="update LLM provider/model",
                expected_parent=current.version if current is not None else None,
            )
        except ConfigConflictError as error:
            raise HTTPException(409, str(error)) from error
        _apply_now(application, client_id)

        return ClientLLMResponse(
            provider=check.spec.llm.provider,
            model=check.spec.llm.model,
            api_key_env=check.spec.llm.api_key_env,
        )

    return router
