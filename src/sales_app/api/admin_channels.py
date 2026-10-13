"""Admin API: which messaging channels clients may use.

The Admin decides, a client only chooses among what it is allowed (`client_channel.py`):

- **The global list** (`GET/PUT /admin/v1/settings/channels`): the channels every client may use.
  Until it is set, only WhatsApp is allowed. Changing it is the owner's.
- **A client's exceptions** (`GET/PUT /admin/v1/clients/{id}/channels`): channels added for that
  client or taken away from it. A refusal wins over a permission, wherever both appear.

A channel an Admin takes away stops working for the clients that had already set it up, at once:
each is rebuilt, is no longer ready and takes no new work. Nothing here reads or writes a secret.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.channel_policy import ChannelPolicyService, InvalidChannelPolicyError
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.secret_store import SecretStoreError
from sales_app.wiring.providers import ProviderKind


class ChannelRow(BaseModel):
    key: str
    label: str
    allowed: bool


class GlobalChannelsResponse(BaseModel):
    channels: list[ChannelRow]
    allowed: list[str]


class UpdateGlobalChannelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str]


class ClientChannelsResponse(BaseModel):
    client_id: str
    global_allowed: list[str]
    added: list[str]  # allowed for this client although not globally
    removed: list[str]  # forbidden for this client although globally allowed
    effective: list[str]  # what this client may use now


class UpdateClientChannelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: list[str] = []
    removed: list[str] = []


def create_admin_channels_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def policy() -> ChannelPolicyService:
        if application.channel_policy is None:
            raise HTTPException(503, "channel authorizations are not available")
        return application.channel_policy

    def known() -> dict[str, str]:
        return {
            d.key: d.label
            for d in application.provider_registry.list(ProviderKind.CHANNEL)
            if d.kind is ProviderKind.CHANNEL and d.runnable
        }

    def global_response() -> GlobalChannelsResponse:
        allowed = policy().global_allowed()
        return GlobalChannelsResponse(
            channels=[
                ChannelRow(key=key, label=label, allowed=key in allowed)
                for key, label in sorted(known().items())
            ],
            allowed=sorted(allowed),
        )

    def client_response(client_id: str) -> ClientChannelsResponse:
        service = policy()
        added, removed = service.client_access(client_id)
        return ClientChannelsResponse(
            client_id=client_id,
            global_allowed=sorted(service.global_allowed()),
            added=sorted(added),
            removed=sorted(removed),
            effective=sorted(service.effective(client_id)),
        )

    @router.get("/settings/channels", response_model=GlobalChannelsResponse)
    def get_global(_admin: AdminUser = Depends(get_current_admin)) -> GlobalChannelsResponse:
        try:
            return global_response()
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None

    @router.put("/settings/channels", response_model=GlobalChannelsResponse)
    def put_global(
        body: UpdateGlobalChannelsRequest, admin: AdminUser = Depends(require_owner)
    ) -> GlobalChannelsResponse:
        try:
            policy().set_global(body.allowed, actor=admin.username, known=known())
        except InvalidChannelPolicyError as error:
            raise HTTPException(422, str(error)) from None
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return global_response()

    @router.get("/clients/{client_id}/channels", response_model=ClientChannelsResponse)
    def get_client(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> ClientChannelsResponse:
        ensure_known_client(admin, client_id)
        try:
            return client_response(client_id)
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None

    @router.put("/clients/{client_id}/channels", response_model=ClientChannelsResponse)
    def put_client(
        client_id: str,
        body: UpdateClientChannelsRequest,
        admin: AdminUser = Depends(require_owner),
    ) -> ClientChannelsResponse:
        ensure_known_client(admin, client_id)
        if set(body.added) & set(body.removed):
            raise HTTPException(422, "a channel cannot be both added and removed")
        try:
            policy().set_client_access(
                client_id, body.added, body.removed, actor=admin.username, known=known()
            )
        except InvalidChannelPolicyError as error:
            raise HTTPException(422, str(error)) from None
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return client_response(client_id)

    def ensure_known_client(admin: AdminUser, client_id: str) -> None:
        if not CLIENT_ID_PATTERN.match(client_id):
            raise HTTPException(404, "no such client")
        ensure_client_access(admin, client_id)
        if (
            client_id not in {c.client_id for c in application.registry.configs()}
            and not (application.settings.clients_dir / client_id).is_dir()
        ):
            raise HTTPException(404, "no such client")

    return router
