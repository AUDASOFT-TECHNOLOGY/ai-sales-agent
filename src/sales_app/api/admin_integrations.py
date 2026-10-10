"""Admin API: credentials for the services Revansa talks to. Write-only.

A credential can be saved, replaced or removed, and its status read (`configured`, when, by whom).
No response ever carries a sensitive value; only the non-sensitive ones (a phone number id, an API
version) are shown. Only the OWNER role may change anything. Reading is open to any admin who can
reach the scope (global, or that client).

Request bodies are read by hand rather than through a Pydantic model: FastAPI's default 422 answer
echoes the rejected input, which here would be the secret. Errors below describe the rule that was
broken, never the value, and responses are marked `no-store`.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.config.resolution import CONFIG_FILENAME
from sales_app.config.secrets import OPENAI_MODELS
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.connection_tests import (
    INFERENCE_MAX_TOKENS,
    ConnectionResult,
    HttpClientFactory,
    check_openai,
    check_openai_inference,
    check_whatsapp_client,
    default_http_client,
)
from sales_app.llm_models import ModelTest, foreign_model_keys, record_test, stored_models
from sales_app.model_usage import impact_of_credential_write
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    CredentialStatus,
    DuplicateSecretError,
    InvalidCredentialError,
    ScopeKind,
    SecretStoreError,
    SecretStoreUnavailableError,
    UnknownCredentialError,
    client_scope,
)
from sales_app.secret_store.catalog import normalize_value, spec_for
from sales_integrations.whatsapp.channel import DEFAULT_API_VERSION

_BODY_SCHEMA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string", "writeOnly": True}},
                    "additionalProperties": False,
                }
            }
        },
    }
}


class CredentialResponse(BaseModel):
    key: str
    label: str
    scope: ScopeKind
    sensitive: bool
    configured: bool
    value: str | None = None  # only ever set for a non-sensitive credential
    updated_at: datetime | None = None
    updated_by: str | None = None


class RuntimeStatusResponse(BaseModel):
    """Whether a client is running on its current credentials, and how the last change fared.
    Nothing here is a credential: only names of what is missing and the reasons a change failed."""

    client_id: str
    readiness: str  # "ready" | "not_ready"
    missing_credentials: list[str] = []
    apply_state: str  # "idle" | "applying" | "applied" | "pending" | "not_ready" | "failed"
    apply_at: datetime | None = None
    apply_errors: list[str] = []


class ConnectionTestResponse(BaseModel):
    """The outcome of a real call to the service. Never carries a credential."""

    ok: bool
    message: str
    details: dict[str, str] = {}


class ModelTestResponse(BaseModel):
    """The outcome of a real request to one model. Never carries a credential."""

    ok: bool  # the model answered: it is usable with the stored credentials
    outcome: str  # usable | no_api_key | invalid_api_key | model_not_found | invalid_parameters |
    #               quota_exceeded | timeout | unreachable | service_error | unexpected_response
    message: str
    model_id: str  # exactly the Model ID that was sent
    details: dict[str, str] = {}  # the service's error code, tokens used: never a credential
    request_sent: bool  # a real request went out (it may be billed); False: none did
    max_tokens: int  # the most the request could generate
    tested_at: str | None = None  # None: nothing was sent, so nothing was tested
    recorded: bool = False  # whether the outcome is kept with the model (the catalog shows it)


def _test_response(result: ConnectionResult) -> ConnectionTestResponse:
    return ConnectionTestResponse(ok=result.ok, message=result.message, details=result.details)


def _response(status: CredentialStatus, scope: ScopeKind) -> CredentialResponse:
    return CredentialResponse(
        key=status.key,
        label=status.label,
        scope=scope,
        sensitive=status.sensitive,
        configured=status.configured,
        value=None if status.sensitive else status.value,  # belt and braces
        updated_at=status.updated_at,
        updated_by=status.updated_by,
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _read_value(request: Request) -> str:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(body, dict) or set(body) != {"value"} or not isinstance(body["value"], str):
        raise HTTPException(422, 'body must be {"value": "<text>"}')
    return body["value"]


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, UnknownCredentialError):
        return HTTPException(404, "no such credential")
    if isinstance(error, InvalidCredentialError):
        return HTTPException(422, str(error))
    if isinstance(error, DuplicateSecretError):
        holder = error.holder_scope.removeprefix("client:")
        return HTTPException(409, f"this value is already used by client {holder!r}")
    if isinstance(error, SecretStoreUnavailableError):
        return HTTPException(503, str(error))
    return HTTPException(500, "credential storage failed")  # never echo store internals


def _client_exists(application: Application, client_id: str) -> bool:
    return bool(
        CLIENT_ID_PATTERN.match(client_id)
        and (application.settings.clients_dir / client_id / CONFIG_FILENAME).is_file()
    )


def create_admin_integrations_router(
    application: Application, http: HttpClientFactory = default_http_client
) -> APIRouter:
    credentials = application.credentials
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(_no_store)])

    def read_scope(scope: str, kind: ScopeKind) -> list[CredentialResponse]:
        return [_response(s, kind) for s in credentials.describe(scope)]

    def save(
        scope: str, kind: ScopeKind, key: str, value: str, admin: AdminUser
    ) -> CredentialResponse:
        try:
            return _response(credentials.save(scope, key, value, actor=admin.username), kind)
        except (SecretStoreError, UnknownCredentialError, InvalidCredentialError) as error:
            raise _http_error(error) from None

    def remove(scope: str, kind: ScopeKind, key: str, admin: AdminUser) -> CredentialResponse:
        try:
            return _response(credentials.remove(scope, key, actor=admin.username), kind)
        except (SecretStoreError, UnknownCredentialError) as error:
            raise _http_error(error) from None

    def guard_model_lists(key: str, value: str | None) -> None:
        """A model list is changed through its own routes, which tell who is affected. Writing it
        here must not take a model away from the clients that use it, nor switch it off: refused
        with 409, naming them. Any other credential is not concerned."""
        if value is not None:
            try:
                value = normalize_value(spec_for(GLOBAL_SCOPE, key), value)
            except (UnknownCredentialError, InvalidCredentialError):
                return  # the save below reports it, as it always has
        effect = impact_of_credential_write(application, key, value)
        if effect.removed or effect.deactivated:
            affected = {**effect.removed, **effect.deactivated}
            raise HTTPException(
                409,
                {
                    "message": (
                        "clients use a model this change would remove or switch off: change the "
                        "model list through its own routes, after giving them another model"
                    ),
                    "client_ids": sorted({c for clients in affected.values() for c in clients}),
                    "models": {model: list(clients) for model, clients in affected.items()},
                },
            )

    def save_global(key: str, value: str, admin: AdminUser) -> CredentialResponse:
        guard_model_lists(key, value)
        return save(GLOBAL_SCOPE, ScopeKind.GLOBAL, key, value, admin)

    def remove_global(key: str, admin: AdminUser) -> CredentialResponse:
        guard_model_lists(key, None)
        return remove(GLOBAL_SCOPE, ScopeKind.GLOBAL, key, admin)

    def require_confirmation(confirm: bool) -> None:
        if not confirm:
            raise HTTPException(400, "removal must be confirmed: pass confirm=true")

    # ---- Revansa (global) ----------------------------------------------------------------

    @router.get("/settings/integrations", response_model=list[CredentialResponse])
    def list_global(_admin: AdminUser = Depends(get_current_admin)) -> list[CredentialResponse]:
        return read_scope(GLOBAL_SCOPE, ScopeKind.GLOBAL)

    @router.put(
        "/settings/integrations/{key}",
        response_model=CredentialResponse,
        openapi_extra=_BODY_SCHEMA,
    )
    async def put_global(
        key: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        value = await _read_value(request)
        return await run_in_threadpool(save_global, key, value, admin)

    @router.delete("/settings/integrations/{key}", response_model=CredentialResponse)
    def delete_global(
        key: str, confirm: bool = False, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        require_confirmation(confirm)
        return remove_global(key, admin)

    def stored(scope: str, key: str) -> str | None:
        return credentials.reveal(scope, key)

    @router.post("/settings/integrations/openai/test", response_model=ConnectionTestResponse)
    def test_openai(_admin: AdminUser = Depends(require_owner)) -> ConnectionTestResponse:
        """Calls the real OpenAI API with the stored key, organization, project and base URL."""
        return _test_response(
            check_openai(
                api_key=stored(GLOBAL_SCOPE, "openai.api_key"),
                organization=stored(GLOBAL_SCOPE, "openai.organization"),
                project=stored(GLOBAL_SCOPE, "openai.project"),
                base_url=stored(GLOBAL_SCOPE, "openai.base_url"),
                models={m.name: m.id for m in stored_models(credentials) or [] if m.active},
                http=http,
            )
        )

    @router.post("/settings/openai/models/{model_id}/test", response_model=ModelTestResponse)
    def test_openai_model(
        model_id: str, confirm: bool = False, admin: AdminUser = Depends(require_owner)
    ) -> ModelTestResponse:
        """Asks one model of the Settings list, by its exact Model ID, for a few tokens.

        Unlike the connection test (`/settings/integrations/openai/test`, which only lists what the
        account sees and is free) this is a real request that the account may be billed for, so it
        needs `confirm=true`. The model may be switched off: testing it does not switch it on. How
        the test ended is kept with the model, and the catalog of `/settings/llm` shows it."""
        if all(m.id != model_id for m in stored_models(credentials) or []):
            raise HTTPException(404, "no such model")
        if not confirm:
            raise HTTPException(
                400,
                "this sends a real request to OpenAI (at most "
                f"{INFERENCE_MAX_TOKENS} tokens of output) and the account may be billed for it: "
                "pass confirm=true to go ahead",
            )
        result = check_openai_inference(
            api_key=stored(GLOBAL_SCOPE, "openai.api_key"),
            organization=stored(GLOBAL_SCOPE, "openai.organization"),
            project=stored(GLOBAL_SCOPE, "openai.project"),
            base_url=stored(GLOBAL_SCOPE, "openai.base_url"),
            model_id=model_id,
            http=http,
        )
        tested_at = None
        recorded = False
        if result.sent_a_request:
            tested_at = application.clock.now().astimezone(UTC).isoformat(timespec="seconds")
            recorded = record_test(
                credentials,
                model_id,
                ModelTest(result.outcome.value, tested_at),
                actor=admin.username,
                reserved=foreign_model_keys(application.provider_registry, OPENAI_MODELS),
            )
        return ModelTestResponse(
            ok=result.ok,
            outcome=result.outcome.value,
            message=result.message,
            model_id=model_id,
            details=result.details,
            request_sent=result.sent_a_request,
            max_tokens=INFERENCE_MAX_TOKENS,
            tested_at=tested_at,
            recorded=recorded,
        )

    # ---- are the clients running on them -----------------------------------------------

    def runtime_status(client_id: str) -> RuntimeStatusResponse:
        missing = application.registry.missing(client_id)
        applied = application.runtime_manager.status(client_id)
        return RuntimeStatusResponse(
            client_id=client_id,
            readiness="not_ready" if missing else "ready",
            missing_credentials=list(missing),
            apply_state=applied.state,
            apply_at=applied.at,
            apply_errors=list(applied.errors),
        )

    @router.get("/settings/runtime-status", response_model=list[RuntimeStatusResponse])
    def list_runtime_status(
        admin: AdminUser = Depends(get_current_admin),
    ) -> list[RuntimeStatusResponse]:
        ids = sorted(c.client_id for c in application.registry.configs())
        return [runtime_status(cid) for cid in ids if admin.can_reach(cid)]

    # ---- one client ----------------------------------------------------------------------

    @router.get("/clients/{client_id}/runtime-status", response_model=RuntimeStatusResponse)
    def get_runtime_status(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> RuntimeStatusResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if application.registry.get_config(client_id) is None:
            raise HTTPException(404, "no such client")
        return runtime_status(client_id)

    def client_scope_for(admin: AdminUser, client_id: str) -> str:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if not _client_exists(application, client_id):
            raise HTTPException(404, "no such client")
        return client_scope(client_id)

    @router.get("/clients/{client_id}/integrations", response_model=list[CredentialResponse])
    def list_client(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> list[CredentialResponse]:
        return read_scope(client_scope_for(admin, client_id), ScopeKind.CLIENT)

    @router.put(
        "/clients/{client_id}/integrations/{key}",
        response_model=CredentialResponse,
        openapi_extra=_BODY_SCHEMA,
    )
    async def put_client(
        client_id: str, key: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        scope = client_scope_for(admin, client_id)
        value = await _read_value(request)
        return await run_in_threadpool(save, scope, ScopeKind.CLIENT, key, value, admin)

    @router.delete("/clients/{client_id}/integrations/{key}", response_model=CredentialResponse)
    def delete_client(
        client_id: str,
        key: str,
        confirm: bool = False,
        admin: AdminUser = Depends(require_owner),
    ) -> CredentialResponse:
        scope = client_scope_for(admin, client_id)
        require_confirmation(confirm)
        return remove(scope, ScopeKind.CLIENT, key, admin)

    @router.post(
        "/clients/{client_id}/integrations/whatsapp/test", response_model=ConnectionTestResponse
    )
    def test_client_whatsapp(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ConnectionTestResponse:
        """Asks Meta for this client's phone number with its stored access token."""
        scope = client_scope_for(admin, client_id)
        return _test_response(
            check_whatsapp_client(
                access_token=stored(scope, "whatsapp.access_token"),
                phone_number_id=stored(scope, "whatsapp.phone_number_id"),
                api_version=stored(GLOBAL_SCOPE, "whatsapp.api_version") or DEFAULT_API_VERSION,
                http=http,
            )
        )

    return router
