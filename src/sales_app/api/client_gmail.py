"""Client API: connect the client's own Gmail with Google OAuth (the client types no password).

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, and every grant lives under that client alone. The client only clicks "Connect": Google shows
its own consent page and the client's Gmail password never reaches Revansa.

- `GET  /client/v1/channel/google`              where the connection stands;
- `POST /client/v1/channel/google/start`        the Google consent URL (`{authorize_url}`);
- `GET  /client/v1/channel/google/callback`     Google sends the browser back here; always a 302 to
  the channel page with `?google=connected|denied|scope|invalid|in_use|unavailable|error`, never a token,
  code or address in the URL;
- `POST /client/v1/channel/google/disconnect`   forget the grant and ask Google to revoke it.

`state` is `not_connected | connected | reauth_required | unavailable` (`unavailable`: the Admin has
not configured the platform's Google application, so the client has nothing to do but wait).
`send_ready` is true only when the mailbox is connected AND the Admin configured the mandatory daily
send cap: Revansa never sends before both are true. Nothing here proves that Google accepts the app
(see docs/google-oauth-channel.md).

The router is built from `GmailContext` (plain callables) so that it knows nothing of the secret
store or the wiring; `application_gmail_context` builds the real one from the `Application`.
It is registered in `app.py` through `create_application_gmail_router`, which gives it the real
`Application`'s Gmail service (credentials of the Admin resolved at each call, grants in the secret
store).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from sales_app.api.client import COOKIE_NAME, make_current_client_dependency
from sales_app.client_profiles import ClientProfile
from sales_app.google_mailbox import (
    CONNECTED,
    AddressAlreadyConnectedError,
    GoogleMailboxConnection,
    InvalidGoogleStateError,
)
from sales_integrations.google_oauth import (
    GoogleAccessDeniedError,
    GoogleInsufficientScopeError,
    GoogleOAuthError,
    GoogleTransientError,
)

CHANNEL_PAGE = "/client/channel"

GmailState = Literal["not_connected", "connected", "reauth_required", "unavailable"]


class AuthPort(Protocol):
    def authenticate(self, token: str) -> ClientProfile | None: ...


@dataclass(frozen=True)
class GmailContext:
    """What the routes need from the rest of the application, as plain callables."""

    # Has the Admin configured the platform's Google application (id, secret, state key)?
    google_configured: Callable[[], bool]
    # None when this client may connect Gmail now; else (http status, fixed sentence):
    # 403 channel not allowed, 409 channel is not e-mail, 409 a dedicated mailbox is assigned.
    start_refusal: Callable[[str], tuple[int, str] | None]
    # Is the mandatory daily send cap configured for this client (Admin)?
    send_cap_configured: Callable[[str], bool]
    # Called after a connection change (refresh the client's runtime). Errors are swallowed.
    changed: Callable[[str], None] = lambda client_id: None
    # How reading the replies goes: ("not_started" | "ok" | "failing", the last read, ISO 8601).
    sync_of: Callable[[str], tuple[str, str | None]] = lambda client_id: ("not_started", None)
    # The Gmail address the client said it wants to connect (suggested on Google's page), if any.
    hint_of: Callable[[str], str | None] = lambda client_id: None


class GmailStatusResponse(BaseModel):
    state: GmailState
    address: str | None = None  # the client's own Gmail address, shown to that client only
    send_cap_configured: bool = False
    send_ready: bool = False  # connected AND cap configured: the only state in which mail leaves
    message: str
    # Reading the replies: `not_started` (nothing read yet), `ok`, or `failing` (try again or
    # reconnect). Nothing technical, no error text.
    mailbox_sync: Literal["not_started", "ok", "failing"] = "not_started"
    last_sync_at: str | None = None


class StartResponse(BaseModel):
    authorize_url: str


_MESSAGES: dict[str, str] = {
    "not_connected": "Connect your Gmail so Revansa can write to your prospects.",
    "connected": "Your Gmail is connected.",
    "reauth_required": "Google needs you to authorise Revansa again. Reconnect your Gmail.",
    "unavailable": "Gmail is not available yet. Revansa has to finish setting it up.",
}
_CAP_MESSAGE = "Your Gmail is connected. Sending starts once Revansa sets your daily sending limit."


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def create_client_gmail_router(
    auth: AuthPort,
    connection: GoogleMailboxConnection | None,
    context: GmailContext,
) -> APIRouter:
    """`connection` is None when the platform has no Google application configured."""
    get_current_client = make_current_client_dependency(auth)  # type: ignore[arg-type]
    router = APIRouter(
        prefix="/client/v1/channel/google", tags=["client"], dependencies=[Depends(_no_store)]
    )

    def status_of(client_id: str) -> GmailStatusResponse:
        if connection is None or not context.google_configured():
            return GmailStatusResponse(state="unavailable", message=_MESSAGES["unavailable"])
        held = connection.status(client_id)
        cap = context.send_cap_configured(client_id)
        ready = held.state == CONNECTED and cap
        message = _CAP_MESSAGE if held.state == CONNECTED and not cap else _MESSAGES[held.state]
        sync, last = (
            context.sync_of(client_id) if held.state == CONNECTED else ("not_started", None)
        )
        return GmailStatusResponse(
            state=held.state,  # type: ignore[arg-type]  # one of the four states
            address=held.address,
            send_cap_configured=cap,
            send_ready=ready,
            message=message,
            mailbox_sync=sync,  # type: ignore[arg-type]
            last_sync_at=last,
        )

    def refresh(client_id: str) -> None:
        try:
            context.changed(client_id)
        except Exception:  # noqa: BLE001, S110 - a refresh that fails must not fail the request
            pass

    @router.get("", response_model=GmailStatusResponse)
    def get_status(client: ClientProfile = Depends(get_current_client)) -> GmailStatusResponse:
        return status_of(client.client_id)

    @router.post("/start", response_model=StartResponse)
    def start(client: ClientProfile = Depends(get_current_client)) -> StartResponse:
        # No request body is read: there is nothing for the client to type.
        if connection is None or not context.google_configured():
            raise HTTPException(503, "Gmail is not available yet")
        refusal = context.start_refusal(client.client_id)
        if refusal is not None:
            raise HTTPException(refusal[0], refusal[1])
        return StartResponse(
            authorize_url=connection.start(
                client.client_id, login_hint=context.hint_of(client.client_id)
            )
        )

    @router.get("/callback")
    async def callback(
        state: str | None = Query(default=None, max_length=2048),
        code: str | None = Query(default=None, max_length=2048),
        error: str | None = Query(default=None, max_length=256),
        session_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> RedirectResponse:
        profile = auth.authenticate(session_token) if session_token else None
        if profile is None or connection is None:
            return _back("error" if connection is None else "invalid")
        outcome = await run_in_threadpool(_finish, profile.client_id, state, code, error)
        return _back(outcome)

    def _finish(client_id: str, state: Any, code: Any, error: Any) -> str:
        try:
            connection.callback(  # type: ignore[union-attr]
                state=state or "", client_id=client_id, code=code, error=error
            )
        except InvalidGoogleStateError:
            return "invalid"
        except AddressAlreadyConnectedError:
            return "in_use"
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

    @router.post("/disconnect", response_model=GmailStatusResponse)
    def disconnect(client: ClientProfile = Depends(get_current_client)) -> GmailStatusResponse:
        if connection is None:
            raise HTTPException(503, "Gmail is not available yet")
        # Locally forgotten whatever Google answers; the next send and poll find no grant.
        connection.disconnect(client.client_id)
        refresh(client.client_id)
        return status_of(client.client_id)

    return router


def _back(outcome: str) -> RedirectResponse:
    return RedirectResponse(f"{CHANNEL_PAGE}?google={outcome}", status_code=302)


# ---- the real context ---------------------------------------------------------------------------


def application_gmail_context(
    application: Any,
    *,
    google_configured: Callable[[], bool],
    send_cap_of: Callable[[str], int | None],
    platform_mailbox_assigned: Callable[[str], bool],
) -> GmailContext:
    """The context over the real `Application`. `send_cap_of` and `platform_mailbox_assigned` are
    given by the integrator (they read the client's settings and secrets, which are shared files)."""
    from sales_app.api.admin_clients import client_wiring_and_document
    from sales_app.channel_status import (
        EMAIL,
        allowed_channels,
        channel_provider_of,
        channel_settings_of,
    )
    from sales_app.config.email_settings import (
        CUSTOM_MODE,
        GMAIL_MODE,
        email_mode_of,
    )

    def start_refusal(client_id: str) -> tuple[int, str] | None:
        if EMAIL not in allowed_channels(application, client_id):
            return 403, "this channel is not available for your account"
        found = client_wiring_and_document(application, client_id)
        if found is None:
            return 404, "no such client"
        if channel_provider_of(found[0], found[1]) != EMAIL:
            # The client connects its Gmail from the Connections screen whatever its channel is
            # today: the grant is kept, and choosing Email later uses it. The mailbox rules below
            # concern an account that is on Email now.
            return None
        if platform_mailbox_assigned(client_id):
            return 409, "a Revansa mailbox is already assigned to your account"
        mode = email_mode_of(channel_settings_of(found[1]))
        if mode == CUSTOM_MODE and channel_settings_of(found[1]).get("smtp_host"):
            return 409, "a mailbox is already set up for your account"
        if mode != GMAIL_MODE:
            return 409, "choose Gmail as your email first"
        return None

    def cap_configured(client_id: str) -> bool:
        cap = send_cap_of(client_id)
        return isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1

    def changed(client_id: str) -> None:
        if application.registry.get_config(client_id) is not None:
            application.runtime_manager.refresh_client(client_id)

    def sync_of(client_id: str) -> tuple[str, str | None]:
        runtime = application.registry.get(client_id)
        if runtime is None:
            return "not_started", None
        snapshot = runtime.health.snapshot()
        runs = snapshot.get("last_runs")
        last = runs.get("inbox") if isinstance(runs, dict) else None
        if snapshot.get("inbox_failures"):
            return "failing", last
        return ("ok", last) if last else ("not_started", None)

    def hint_of(client_id: str) -> str | None:
        hints = getattr(application, "connection_hints", None)
        try:
            return hints.get(client_id, "gmail") if hints is not None else None
        except Exception:  # noqa: BLE001 - a hint that cannot be read is simply not suggested
            return None

    return GmailContext(
        sync_of=sync_of,
        google_configured=google_configured,
        start_refusal=start_refusal,
        send_cap_configured=cap_configured,
        changed=changed,
        hint_of=hint_of,
    )


def create_application_gmail_router(application: Any) -> APIRouter | None:
    """The Gmail routes over the real `Application`; None when it has no Gmail service."""
    from sales_app.channel_status import credential_stored
    from sales_app.config.secrets import EMAIL_PLATFORM_MAILBOX

    gmail = application.gmail
    if gmail is None:
        return None

    def send_cap_of(client_id: str) -> int | None:
        limits = application.send_limits
        if limits is None:
            return None
        try:
            return limits.effective(client_id)
        except Exception:  # noqa: BLE001 - an unreadable limit is shown as unknown, never guessed
            return None

    def mailbox_assigned(client_id: str) -> bool:
        if credential_stored(application, client_id, EMAIL_PLATFORM_MAILBOX):
            return True
        record = getattr(application.email_activation, "get", lambda _c: None)(client_id)
        return record is not None and record.status in ("in_progress", "active")

    context = application_gmail_context(
        application,
        google_configured=gmail.configured,
        send_cap_of=send_cap_of,
        platform_mailbox_assigned=mailbox_assigned,
    )
    return create_client_gmail_router(application.client_auth, gmail.connection, context)


__all__ = [
    "GmailContext",
    "GmailStatusResponse",
    "application_gmail_context",
    "create_application_gmail_router",
    "create_client_gmail_router",
]
