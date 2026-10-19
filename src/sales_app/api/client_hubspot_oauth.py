"""Client API: connect the client's own HubSpot account with HubSpot OAuth (no token to copy).

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, and every grant lives under that client alone. The client clicks "Connect HubSpot", agrees on
HubSpot's own page and is sent back; Revansa keeps only the refresh token, encrypted, in that client's
scope.

- `GET  /client/v1/hubspot-access`              where the connection stands;
- `POST /client/v1/hubspot-access/start`        the HubSpot consent URL (`{authorize_url}`);
- `GET  /client/v1/hubspot-access/callback`     HubSpot sends the browser back here; always a 302 to
  the sources page with `?hubspot=connected|denied|scope|invalid|unavailable|error`, never a token,
  code or account in the URL;
- `POST /client/v1/hubspot-access/disconnect`   forget the grant and ask HubSpot to revoke it.

`state` is `not_connected | connected | reauth_required | unavailable` (`unavailable`: the Admin has
not configured the platform's HubSpot application, so the client has nothing to do but wait).
Nothing here proves that HubSpot accepts the application: only a real consent does.
"""

from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from sales_app.api.client import COOKIE_NAME, make_current_client_dependency
from sales_app.client_profiles import ClientProfile
from sales_app.google_mailbox import CONNECTED, InvalidGoogleStateError
from sales_integrations.google_oauth import (
    GoogleAccessDeniedError,
    GoogleInsufficientScopeError,
    GoogleOAuthError,
    GoogleTransientError,
)

SOURCES_PAGE = "/client/connections/sources"

HubSpotAccessState = Literal["not_connected", "connected", "reauth_required", "unavailable"]


class HubSpotAccessResponse(BaseModel):
    state: HubSpotAccessState
    account: str | None = None  # the client's own HubSpot account, shown to that client only
    message: str


class StartResponse(BaseModel):
    authorize_url: str


_MESSAGES: dict[str, str] = {
    "not_connected": "Connect your HubSpot account so Revansa can read your contacts.",
    "connected": "Your HubSpot account is connected.",
    "reauth_required": "HubSpot needs you to authorise Revansa again.",
    "unavailable": "HubSpot is not available yet. Revansa has to finish setting it up.",
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def create_client_hubspot_oauth_router(application: Any) -> APIRouter | None:
    """The routes over the real `Application`; None when it has no HubSpot access service."""
    access = application.hubspot_access
    if access is None:
        return None
    connection = access.connection
    auth = application.client_auth
    get_current_client = make_current_client_dependency(auth)
    router = APIRouter(
        prefix="/client/v1/hubspot-access", tags=["client"], dependencies=[Depends(_no_store)]
    )

    def status_of(client_id: str) -> HubSpotAccessResponse:
        if not access.configured():
            return HubSpotAccessResponse(state="unavailable", message=_MESSAGES["unavailable"])
        held = connection.status(client_id)
        state = held.state if held.state in _MESSAGES else "unavailable"
        return HubSpotAccessResponse(
            state=state,  # type: ignore[arg-type]  # one of the four states
            account=held.address,
            message=_MESSAGES[state],
        )

    def refresh(client_id: str) -> None:
        try:
            if application.registry.get_config(client_id) is not None:
                application.runtime_manager.refresh_client(client_id)
        except Exception:  # noqa: BLE001, S110 - a refresh that fails must not fail the request
            pass

    @router.get("", response_model=HubSpotAccessResponse)
    def get_status(client: ClientProfile = Depends(get_current_client)) -> HubSpotAccessResponse:
        return status_of(client.client_id)

    @router.post("/start", response_model=StartResponse)
    def start(client: ClientProfile = Depends(get_current_client)) -> StartResponse:
        if not access.configured():
            raise HTTPException(503, "HubSpot is not available yet")
        return StartResponse(authorize_url=connection.start(client.client_id))

    @router.get("/callback")
    async def callback(
        state: str | None = Query(default=None, max_length=2048),
        code: str | None = Query(default=None, max_length=2048),
        error: str | None = Query(default=None, max_length=256),
        session_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> RedirectResponse:
        profile = auth.authenticate(session_token) if session_token else None
        if profile is None:
            return _back("invalid")
        outcome = await run_in_threadpool(_finish, profile.client_id, state, code, error)
        return _back(outcome)

    def _finish(client_id: str, state: Any, code: Any, error: Any) -> str:
        try:
            connection.callback(state=state or "", client_id=client_id, code=code, error=error)
        except InvalidGoogleStateError:
            return "invalid"
        except GoogleAccessDeniedError:
            return "denied"
        except GoogleInsufficientScopeError:
            return "scope"
        except GoogleTransientError:
            return "unavailable"
        except GoogleOAuthError:
            return "error"
        refresh(client_id)
        return "connected"

    @router.post("/disconnect", response_model=HubSpotAccessResponse)
    def disconnect(client: ClientProfile = Depends(get_current_client)) -> HubSpotAccessResponse:
        # Locally forgotten whatever HubSpot answers; the next read finds no grant.
        connection.disconnect(client.client_id)
        refresh(client.client_id)
        return status_of(client.client_id)

    return router


def _back(outcome: str) -> RedirectResponse:
    return RedirectResponse(f"{SOURCES_PAGE}?hubspot={outcome}", status_code=302)


__all__ = ["CONNECTED", "HubSpotAccessResponse", "create_client_hubspot_oauth_router"]
