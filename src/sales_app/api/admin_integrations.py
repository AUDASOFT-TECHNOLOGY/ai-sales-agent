"""Admin API: credentials for the services Revansa talks to. Write-only.

A credential can be saved, replaced or removed, and its status read (`configured`, when, by whom).
No response ever carries a sensitive value; only the non-sensitive ones (a phone number id, an API
version) are shown. Only the OWNER role may change anything. Reading is open to any admin who can
reach the scope (global, or that client).

Request bodies are read by hand rather than through a Pydantic model: FastAPI's default 422 answer
echoes the rejected input, which here would be the secret. Errors below describe the rule that was
broken, never the value, and responses are marked `no-store`.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.config.resolution import CONFIG_FILENAME
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.connection_tests import (
    ConnectionResult,
    HttpClientFactory,
    check_openai,
    check_whatsapp_client,
    default_http_client,
)
from sales_app.llm_models import stored_models
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
        return await run_in_threadpool(save, GLOBAL_SCOPE, ScopeKind.GLOBAL, key, value, admin)

    @router.delete("/settings/integrations/{key}", response_model=CredentialResponse)
    def delete_global(
        key: str, confirm: bool = False, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        require_confirmation(confirm)
        return remove(GLOBAL_SCOPE, ScopeKind.GLOBAL, key, admin)

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
