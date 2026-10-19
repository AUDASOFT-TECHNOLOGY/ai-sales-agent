"""Admin API: one client's WhatsApp account and message templates, and the platform's Meta setup.

The Admin owns everything technical about WhatsApp. Reading is open to any admin who can reach the
client; changing anything (asking Meta, submitting or deleting a template, disconnecting) is the
OWNER's. Nothing here returns a token, a PIN or the app secret: only non-sensitive identifiers
(account id, phone number id), Meta's own words about templates, and counters.

- `GET  /admin/v1/whatsapp/platform`                       what the platform still lacks, and the
  addresses Meta must be given (webhook, deauthorisation, data deletion);
- `GET  /admin/v1/clients/{id}/whatsapp`                   the client's account as stored;
- `POST /admin/v1/clients/{id}/whatsapp/refresh`           ask Meta now (quality, registration,
  webhook subscription); a refused token marks the client as to reconnect;
- `GET|POST /admin/v1/clients/{id}/whatsapp/templates`     list / submit a template for review;
- `POST /admin/v1/clients/{id}/whatsapp/templates/sync`    read the templates from Meta;
- `DELETE /admin/v1/clients/{id}/whatsapp/templates/{name}`;
- `PUT  /admin/v1/clients/{id}/whatsapp/templates/opening` choose the approved template that
  carries the agent's message outside the 24-hour window;
- `POST /admin/v1/clients/{id}/whatsapp/disconnect`        the steps taken at Meta, then forgotten.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_integrations import _client_exists
from sales_app.bootstrap import Application
from sales_app.whatsapp_service import (
    WhatsAppConnectionError,
    WhatsAppService,
    template_problem,
)
from sales_integrations.whatsapp import GraphError

Name = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class PlatformResponse(BaseModel):
    ready: bool
    missing: list[str]
    webhook_url: str | None
    deauthorize_url: str | None
    data_deletion_url: str | None
    api_version: str


class TemplateResponse(BaseModel):
    name: str
    language: str
    category: str
    status: str
    body: str
    parameters: list[str]
    reason: str
    opening: bool
    usable_as_opening: bool  # approved and with exactly one variable


class DeliveryResponse(BaseModel):
    message_id: str
    status: str
    at: datetime
    error_code: str
    error_title: str


class AccountResponse(BaseModel):
    state: str
    waba_id: str | None
    phone_number_id: str | None
    display_number: str | None
    verified_name: str | None
    quality: str | None
    connected_at: str | None
    opening_template: str | None
    templates: dict[str, int]  # status -> how many
    deliveries_7d: dict[str, int]
    recent_failures: list[DeliveryResponse]


class RefreshResponse(AccountResponse):
    pass


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    language: Annotated[str, StringConstraints(min_length=2, max_length=8)]
    category: Annotated[str, StringConstraints(min_length=1, max_length=20)] = "UTILITY"
    body: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    examples: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list, max_length=10
    )


class OpeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    language: Annotated[str, StringConstraints(min_length=2, max_length=8)]


class DisconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_number: bool = False  # also deregister the number from the Cloud API


class DisconnectResponse(BaseModel):
    steps: dict[str, str]  # step -> done | skipped | failed


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def create_admin_whatsapp_router(application: Application) -> APIRouter | None:
    service = application.whatsapp
    if service is None:
        return None
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(_no_store)])

    def reach(admin: AdminUser, client_id: str) -> None:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if not _client_exists(application, client_id):
            raise HTTPException(404, "no such client")

    def account(client_id: str) -> AccountResponse:
        held = service.status(client_id)
        since = application.clock.now() - timedelta(days=7)
        try:
            templates = service.templates(client_id)
            counts = service.state.delivery_counts(client_id, since)
            failures = service.state.recent_failures(client_id, 10)
        except Exception:  # noqa: BLE001 - the registers being down must not hide the account
            templates, counts, failures = (), {}, ()
        by_status: dict[str, int] = {}
        for t in templates:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        opening = next((t.name for t in templates if t.is_opening and t.sendable), None)
        return AccountResponse(
            state=held.state,
            waba_id=held.waba_id,
            phone_number_id=held.phone_number_id,
            display_number=held.display_number,
            verified_name=held.verified_name,
            quality=held.quality,
            connected_at=held.connected_at,
            opening_template=opening,
            templates=by_status,
            deliveries_7d=counts,
            recent_failures=[
                DeliveryResponse(
                    message_id=f.message_id,
                    status=f.status,
                    at=f.at,
                    error_code=f.error_code,
                    error_title=f.error_title,
                )
                for f in failures
            ],
        )

    def meta_error(error: Exception) -> HTTPException:
        """Meta's own sentence for the Admin (it carries no credential); a fixed one otherwise."""
        if isinstance(error, WhatsAppConnectionError):
            return HTTPException(409, "this client has no connected WhatsApp account")
        assert isinstance(error, GraphError)
        status = 503 if error.transient else (401 if error.auth else 400)
        return HTTPException(status, f"Meta refused: {error}" if str(error) else "Meta refused")

    def template_rows(client_id: str) -> list[TemplateResponse]:
        return [
            TemplateResponse(
                name=t.name,
                language=t.language,
                category=t.category,
                status=t.status,
                body=t.body,
                parameters=list(t.parameter_names),
                reason=t.reason,
                opening=t.is_opening,
                usable_as_opening=t.sendable and len(t.parameter_names) == 1,
            )
            for t in service.templates(client_id)
        ]

    @router.get("/whatsapp/platform", response_model=PlatformResponse)
    def platform(admin: AdminUser = Depends(get_current_admin)) -> PlatformResponse:
        hook = service.webhook_url()
        base = hook.removesuffix("/webhooks/whatsapp") if hook else None
        return PlatformResponse(
            ready=service.configured(),
            missing=service.missing(),
            webhook_url=hook,
            deauthorize_url=f"{base}/webhooks/whatsapp/deauthorize" if base else None,
            data_deletion_url=f"{base}/webhooks/whatsapp/data-deletion" if base else None,
            api_version=service._version(),
        )

    @router.get("/clients/{client_id}/whatsapp", response_model=AccountResponse)
    def get_account(client_id: str, admin: AdminUser = Depends(get_current_admin)):
        reach(admin, client_id)
        return account(client_id)

    @router.post("/clients/{client_id}/whatsapp/refresh", response_model=RefreshResponse)
    async def refresh(client_id: str, admin: AdminUser = Depends(require_owner)):
        reach(admin, client_id)
        try:
            await run_in_threadpool(service.refresh_account, client_id)
        except (GraphError, WhatsAppConnectionError) as error:
            raise meta_error(error) from None
        return account(client_id)

    @router.get("/clients/{client_id}/whatsapp/templates", response_model=list[TemplateResponse])
    def list_templates(client_id: str, admin: AdminUser = Depends(get_current_admin)):
        reach(admin, client_id)
        return template_rows(client_id)

    @router.post(
        "/clients/{client_id}/whatsapp/templates/sync", response_model=list[TemplateResponse]
    )
    async def sync(client_id: str, admin: AdminUser = Depends(require_owner)):
        reach(admin, client_id)
        try:
            await run_in_threadpool(service.sync_templates, client_id)
        except (GraphError, WhatsAppConnectionError) as error:
            raise meta_error(error) from None
        return template_rows(client_id)

    @router.post("/clients/{client_id}/whatsapp/templates", response_model=TemplateResponse)
    async def create(
        client_id: str, body: CreateTemplateRequest, admin: AdminUser = Depends(require_owner)
    ):
        reach(admin, client_id)
        problem = template_problem(
            body.name, body.language, body.category, body.body, body.examples
        )
        if problem is not None:
            raise HTTPException(422, problem)
        try:
            record = await run_in_threadpool(
                lambda: service.create_template(
                    client_id,
                    name=body.name,
                    language=body.language,
                    category=body.category,
                    body=body.body,
                    examples=body.examples,
                )
            )
        except (GraphError, WhatsAppConnectionError) as error:
            raise meta_error(error) from None
        return next(
            t
            for t in template_rows(client_id)
            if (t.name, t.language) == (record.name, record.language)
        )

    @router.delete("/clients/{client_id}/whatsapp/templates/{name}", status_code=204)
    async def delete(client_id: str, name: str, admin: AdminUser = Depends(require_owner)):
        reach(admin, client_id)
        try:
            await run_in_threadpool(service.delete_template, client_id, name)
        except (GraphError, WhatsAppConnectionError) as error:
            raise meta_error(error) from None
        return Response(status_code=204)

    @router.put("/clients/{client_id}/whatsapp/templates/opening", response_model=AccountResponse)
    def set_opening(
        client_id: str, body: OpeningRequest, admin: AdminUser = Depends(require_owner)
    ):
        reach(admin, client_id)
        if not service.choose_opening(client_id, body.name, body.language):
            raise HTTPException(
                409, "the template must be approved and have exactly one variable ({{1}})"
            )
        return account(client_id)

    @router.post("/clients/{client_id}/whatsapp/disconnect", response_model=DisconnectResponse)
    async def disconnect(
        client_id: str,
        body: DisconnectRequest | None = None,
        admin: AdminUser = Depends(require_owner),
    ):
        reach(admin, client_id)
        release = bool(body and body.release_number)
        report = await run_in_threadpool(
            lambda: service.disconnect(client_id, release_number=release)
        )
        return DisconnectResponse(steps=report.steps)

    return router


__all__ = ["create_admin_whatsapp_router"]

_ = (Any, WhatsAppService)
