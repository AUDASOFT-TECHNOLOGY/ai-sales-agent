"""Client API: connect the client's own WhatsApp Business account with Meta's Embedded Signup.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, and everything lives under that client alone. The client types no token and no identifier:
it clicks "Connect", signs in to Meta in Meta's own window and chooses its business, its WhatsApp
Business account and its number there. Meta gives the browser a one-time code; the server exchanges
it with the platform's Meta app, so no token ever reaches the browser.

- `GET  /client/v1/channel/whatsapp`             where the connection stands (no identifier, no
  token: the client's own number, the name Meta verified, a sentence);
- `POST /client/v1/channel/whatsapp/start`       what the browser needs to open Meta's window
  (the platform app's public ids) and a signed, single-use `state`;
- `POST /client/v1/channel/whatsapp/complete`    `{state, code}` (and the ids Meta's window reports,
  which are hints Meta confirms); answers the new status, or a fixed `detail` cause;
- `POST /client/v1/channel/whatsapp/disconnect`  asks Meta to stop the webhooks and withdraw the
  permissions, and forgets the account here.

`state` is `not_connected | connected | reauth_required | unavailable` (`unavailable`: the Admin has
not set up the platform's Meta app, so the client has nothing to do but wait). Nothing here proves
that Meta accepts the app or that the account can send: see docs/whatsapp-channel.md.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.channel_status import (
    WHATSAPP,
    allowed_channels,
    channel_provider_of,
    channel_status,
)
from sales_app.client_profiles import ClientProfile
from sales_app.whatsapp_service import (
    DENIED,
    ERROR,
    IN_USE,
    INVALID,
    NUMBER_MISMATCH,
    NUMBER_UNCLEAR,
    UNAVAILABLE_CAUSE,
    WhatsAppConnectionError,
)

_CAUSE_STATUS = {
    INVALID: 400,
    DENIED: 403,
    NUMBER_MISMATCH: 409,
    NUMBER_UNCLEAR: 409,
    IN_USE: 409,
    UNAVAILABLE_CAUSE: 503,
    ERROR: 502,
}
Digits = Annotated[str, StringConstraints(pattern=r"^\d{5,32}$")]


class WhatsAppStatusResponse(BaseModel):
    state: Literal["not_connected", "connected", "reauth_required", "unavailable"]
    number: str | None = None  # the client's own WhatsApp number
    verified_name: str | None = None  # the business name Meta verified for it
    status: Literal["ready", "waiting_platform", "action_required"]
    message: str | None = None
    can_start_conversations: bool = False  # Revansa can write first (an approved opening message)
    delivered_7d: int = 0
    failed_7d: int = 0


class StartResponse(BaseModel):
    app_id: str
    config_id: str
    api_version: str
    state: str


class CompleteRequest(BaseModel):
    """`extra="forbid"`: nothing but what Meta's window reports. The ids are hints: the server asks
    Meta which account the one-time code really grants and uses only that."""

    model_config = ConfigDict(extra="forbid")

    state: Annotated[str, StringConstraints(min_length=10, max_length=512)]
    code: Annotated[str, StringConstraints(min_length=4, max_length=2048)]
    waba_id: Digits | None = None
    phone_number_id: Digits | None = None


def create_client_whatsapp_router(application: Application) -> APIRouter | None:
    service = application.whatsapp
    if service is None:
        return None
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(
        prefix="/client/v1/channel/whatsapp",
        tags=["client"],
        dependencies=[Depends(_no_store)],
    )

    def status_of(client: ClientProfile) -> WhatsAppStatusResponse:
        held = service.status(client.client_id)
        found = client_wiring_and_document(application, client.client_id)
        document = found[1] if found is not None else {}
        status, message = channel_status(application, client.client_id, WHATSAPP, document)
        since = application.clock.now() - timedelta(days=7)
        counts = _safe_counts(service, client.client_id, since)
        opening = _opening_ready(service, client.client_id)
        connected = held.state == "connected"
        return WhatsAppStatusResponse(
            state=held.state,  # type: ignore[arg-type]
            number=client.whatsapp_number,
            verified_name=held.verified_name if connected else None,
            status=status,  # type: ignore[arg-type]
            message=message,
            can_start_conversations=connected and opening,
            delivered_7d=counts.get("delivered", 0) + counts.get("read", 0),
            failed_7d=counts.get("failed", 0),
        )

    def refusal(client: ClientProfile) -> tuple[int, str] | None:
        if WHATSAPP not in allowed_channels(application, client.client_id):
            return 403, "this channel is not available for your account"
        found = client_wiring_and_document(application, client.client_id)
        if found is None:
            return 404, "no such client"
        if channel_provider_of(found[0], found[1]) != WHATSAPP:
            return 409, "your channel is not WhatsApp"
        if not client.whatsapp_number:
            return 409, "enter your WhatsApp number first"
        return None

    @router.get("", response_model=WhatsAppStatusResponse)
    def get_status(client: ClientProfile = Depends(get_current_client)) -> WhatsAppStatusResponse:
        return status_of(client)

    @router.post("/start", response_model=StartResponse)
    def start(client: ClientProfile = Depends(get_current_client)) -> StartResponse:
        # No request body is read: there is nothing for the client to type.
        problem = refusal(client)
        if problem is not None:
            raise HTTPException(problem[0], problem[1])
        try:
            info = service.start(client.client_id)
        except WhatsAppConnectionError:
            raise HTTPException(503, "WhatsApp is not available yet") from None
        return StartResponse(
            app_id=info.app_id,
            config_id=info.config_id,
            api_version=info.api_version,
            state=info.state,
        )

    @router.post(
        "/complete",
        response_model=WhatsAppStatusResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "state": {"type": "string"},
                                "code": {"type": "string"},
                                "waba_id": {"type": "string"},
                                "phone_number_id": {"type": "string"},
                            },
                            "additionalProperties": False,
                        }
                    }
                },
            }
        },
    )
    async def complete(
        request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> WhatsAppStatusResponse:
        # Read by hand: FastAPI's own 422 would repeat what was sent, and a field that does not
        # belong here (a token someone typed) must never be echoed back.
        try:
            body = CompleteRequest.model_validate(await request.json())
        except (ValueError, ValidationError):
            raise HTTPException(422, "the request is not valid") from None
        problem = refusal(client)
        if problem is not None:
            raise HTTPException(problem[0], problem[1])
        try:
            await run_in_threadpool(
                service.complete,
                client.client_id,
                state=body.state,
                code=body.code,
                expected_number=client.whatsapp_number,
                waba_hint=body.waba_id,
                phone_hint=body.phone_number_id,
            )
        except WhatsAppConnectionError as error:
            # A fixed word, never Meta's text and never an identifier.
            raise HTTPException(_CAUSE_STATUS.get(error.cause, 502), error.cause) from None
        _clear_reconnect_flag(application, client.client_id)
        return status_of(_profile(application, client))

    @router.post("/disconnect", response_model=WhatsAppStatusResponse)
    async def disconnect(
        client: ClientProfile = Depends(get_current_client),
    ) -> WhatsAppStatusResponse:
        # Locally forgotten whatever Meta answers; the report of the remote steps is the Admin's.
        await run_in_threadpool(service.disconnect, client.client_id)
        return status_of(client)

    return router


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _profile(application: Application, client: ClientProfile) -> ClientProfile:
    return application.client_profiles.get(client.client_id) or client


def _clear_reconnect_flag(application: Application, client_id: str) -> None:
    """The account just connected is the one of the number the client declared (the service
    checked it), so the "number changed, reconnect" mark is over."""
    profile = application.client_profiles.get(client_id)
    if profile is not None and profile.whatsapp_reconnect_required:
        application.client_profiles.update(replace(profile, whatsapp_reconnect_required=False))


def _safe_counts(service, client_id: str, since) -> dict[str, int]:
    try:
        return service.state.delivery_counts(client_id, since)
    except Exception:  # noqa: BLE001 - statistics never break the page
        return {}


def _opening_ready(service, client_id: str) -> bool:
    try:
        return service.state.opening_template(client_id) is not None
    except Exception:  # noqa: BLE001
        return False


__all__ = ["WhatsAppStatusResponse", "create_client_whatsapp_router"]
