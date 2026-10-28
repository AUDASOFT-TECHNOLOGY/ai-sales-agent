"""Client API: what the client says about the accounts it wants to connect, and what it can choose today.

Like the other client routes, none takes a client id: who is asking comes only from the session cookie.

A connection has two sides. The client's is stated here at any time, even before Revansa has finished
configuring the platform's application (Google, Telegram, HubSpot): which Gmail, which Telegram number,
which Google account, which HubSpot account. The platform's is Revansa's. A connection works only when
both are done, and the client is told which one is missing.

- `GET    /client/v1/connections`                    the hints the client gave and, for each channel
  and source, whether it can be chosen on a circuit today (`selectable`);
- `PUT    /client/v1/connections/hints/{kind}`        `{value}`: keep the hint (`gmail`, `sheets`,
  `hubspot`: an e-mail address; `telegram`: a phone number with its country code);
- `DELETE /client/v1/connections/hints/{kind}`        forget it.

`selectable` is the platform's side only. A channel the Admin has not opened, or whose application is not
configured yet, is shown to the client but cannot be chosen. A hint proves nothing and grants nothing:
"connected" still only follows the client's own authorisation on the provider's page.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StringConstraints

from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_source import sheets_credentials_file
from sales_app.channel_status import EMAIL, TELEGRAM, WHATSAPP, allowed_channels, credential_stored
from sales_app.client_profiles import ClientProfile
from sales_app.config.secrets import TELEGRAM_BOT_TOKEN
from sales_app.connection_hints import KINDS, InvalidHintError
from sales_app.secret_store import SecretStoreError
from sales_app.wiring.providers import google_sheets


class HintsResponse(BaseModel):
    gmail: str | None = None
    sheets: str | None = None
    hubspot: str | None = None
    telegram: str | None = None


class SelectableResponse(BaseModel):
    channels: dict[str, bool]
    sources: dict[str, bool]


class ConnectionsResponse(BaseModel):
    hints: HintsResponse
    selectable: SelectableResponse


class HintBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def create_client_connections_router(application: Any) -> APIRouter | None:
    hints = application.connection_hints
    if hints is None:
        return None
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1/connections", tags=["client"], dependencies=[Depends(_no_store)])

    def configured(service: Any) -> bool:
        return service is not None and bool(service.configured())

    def selectable(client_id: str) -> SelectableResponse:
        allowed = allowed_channels(application, client_id)
        telegram_ready = configured(application.telegram_accounts) or credential_stored(
            application, client_id, TELEGRAM_BOT_TOKEN
        )
        sheets_ready = configured(application.sheets_access) or bool(
            google_sheets.service_account_email(sheets_credentials_file(application, client_id))
        )
        return SelectableResponse(
            channels={
                EMAIL: EMAIL in allowed,
                WHATSAPP: WHATSAPP in allowed and configured(application.whatsapp),
                TELEGRAM: TELEGRAM in allowed and telegram_ready,
            },
            sources={
                "google_sheets": sheets_ready,
                "hubspot": configured(application.hubspot_access),
            },
        )

    def answer(client_id: str) -> ConnectionsResponse:
        try:
            held = hints.all(client_id)
        except SecretStoreError:
            raise HTTPException(503, "this is temporarily unavailable") from None
        return ConnectionsResponse(hints=HintsResponse(**held), selectable=selectable(client_id))

    def known(kind: str) -> None:
        if kind not in KINDS:
            raise HTTPException(404, "unknown connection")

    @router.get("", response_model=ConnectionsResponse)
    async def get_connections(client: ClientProfile = Depends(get_current_client)) -> ConnectionsResponse:
        return await run_in_threadpool(answer, client.client_id)

    @router.put("/hints/{kind}", response_model=ConnectionsResponse)
    async def put_hint(kind: str, body: HintBody, client: ClientProfile = Depends(get_current_client)) -> ConnectionsResponse:
        known(kind)

        def save() -> ConnectionsResponse:
            try:
                hints.set(client.client_id, kind, body.value)
            except InvalidHintError as error:
                raise HTTPException(422, str(error)) from None
            except SecretStoreError:
                raise HTTPException(503, "this is temporarily unavailable") from None
            return answer(client.client_id)

        return await run_in_threadpool(save)

    @router.delete("/hints/{kind}", response_model=ConnectionsResponse)
    async def delete_hint(kind: str, client: ClientProfile = Depends(get_current_client)) -> ConnectionsResponse:
        known(kind)

        def forget() -> ConnectionsResponse:
            try:
                hints.clear(client.client_id, kind)
            except SecretStoreError:
                raise HTTPException(503, "this is temporarily unavailable") from None
            return answer(client.client_id)

        return await run_in_threadpool(forget)

    return router
