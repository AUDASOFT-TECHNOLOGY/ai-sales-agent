"""Client API: connect the client's own Google Sheets with Google OAuth (no file, no service account).

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, and every grant lives under that client alone. The client pastes the link of their
spreadsheet (saved through `PUT /client/v1/source`), clicks "Authorise Revansa", agrees on Google's own
page and is sent back; Revansa keeps only the refresh token, encrypted, in that client's scope.

- `GET  /client/v1/sheets-access`              where the connection stands;
- `POST /client/v1/sheets-access/start`        the Google consent URL (`{authorize_url}`);
- `GET  /client/v1/sheets-access/callback`     Google sends the browser back here; always a 302 to
  the sources page with `?google_sheets=connected|denied|scope|invalid|unavailable|error`, never a
  token, code or address in the URL;
- `POST /client/v1/sheets-access/disconnect`   forget the grant and ask Google to revoke it.

`state` is `not_connected | connected | reauth_required | unavailable` (`unavailable`: the Admin has
not configured the platform's Google application, so the client has nothing to do but wait).
Nothing here proves that Google accepts the app (see docs/google-oauth-channel.md).
"""

from collections.abc import Callable
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
_PLACEHOLDER = "TODO"

SheetsAccessState = Literal["not_connected", "connected", "reauth_required", "unavailable"]


class SheetsAccessResponse(BaseModel):
    state: SheetsAccessState
    address: str | None = None  # the client's own Google address, shown to that client only
    spreadsheet_saved: bool = False  # the link is saved: the client can authorise
    message: str


class StartResponse(BaseModel):
    authorize_url: str


_MESSAGES: dict[str, str] = {
    "not_connected": "Authorise Revansa to read and update your Google Sheet.",
    "connected": "Your Google Sheet is connected.",
    "reauth_required": "Google needs you to authorise Revansa again.",
    "unavailable": "Google Sheets is not available yet. Revansa has to finish setting it up.",
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def create_client_sheets_oauth_router(
    application: Any, *, spreadsheet_saved: Callable[[str], bool] | None = None
) -> APIRouter | None:
    """The routes over the real `Application`; None when it has no Sheets access service."""
    access = application.sheets_access
    if access is None:
        return None
    connection = access.connection
    auth = application.client_auth
    get_current_client = make_current_client_dependency(auth)
    router = APIRouter(
        prefix="/client/v1/sheets-access", tags=["client"], dependencies=[Depends(_no_store)]
    )

    def saved_link(client_id: str) -> bool:
        if spreadsheet_saved is not None:
            return spreadsheet_saved(client_id)
        from sales_app.api.admin_clients import client_wiring_and_document
        from sales_app.api.client_agent import sheets_of

        found = client_wiring_and_document(application, client_id)
        if found is None:
            return False
        spreadsheet_id, _ = sheets_of(found[1])
        return spreadsheet_id.strip() not in ("", _PLACEHOLDER)

    def status_of(client_id: str) -> SheetsAccessResponse:
        saved = saved_link(client_id)
        if not access.configured():
            return SheetsAccessResponse(
                state="unavailable", spreadsheet_saved=saved, message=_MESSAGES["unavailable"]
            )
        held = connection.status(client_id)
        return SheetsAccessResponse(
            state=held.state,  # type: ignore[arg-type]  # one of the four states
            address=held.address,
            spreadsheet_saved=saved,
            message=_MESSAGES[held.state],
        )

    def refresh(client_id: str) -> None:
        try:
            if application.registry.get_config(client_id) is not None:
                application.runtime_manager.refresh_client(client_id)
        except Exception:  # noqa: BLE001, S110 - a refresh that fails must not fail the request
            pass

    @router.get("", response_model=SheetsAccessResponse)
    def get_status(client: ClientProfile = Depends(get_current_client)) -> SheetsAccessResponse:
        return status_of(client.client_id)

    @router.post("/start", response_model=StartResponse)
    def start(client: ClientProfile = Depends(get_current_client)) -> StartResponse:
        # No request body is read: the link was saved with the source; there is nothing to type.
        if not access.configured():
            raise HTTPException(503, "Google Sheets is not available yet")
        if not saved_link(client.client_id):
            raise HTTPException(409, "save the link of your Google Sheet first")
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

    @router.post("/disconnect", response_model=SheetsAccessResponse)
    def disconnect(client: ClientProfile = Depends(get_current_client)) -> SheetsAccessResponse:
        # Locally forgotten whatever Google answers; the next read finds no grant.
        connection.disconnect(client.client_id)
        refresh(client.client_id)
        return status_of(client.client_id)

    return router


def _back(outcome: str) -> RedirectResponse:
    return RedirectResponse(f"{SOURCES_PAGE}?google_sheets={outcome}", status_code=302)


__all__ = ["CONNECTED", "SheetsAccessResponse", "create_client_sheets_oauth_router"]
