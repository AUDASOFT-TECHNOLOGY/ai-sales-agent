"""Client API: the independent mailbox (e-mail Mode B), as the client sees it.

`GET /client/v1/channel/mailbox` says where the mailbox stands (`not_requested`, `pending`,
`in_progress`, `active`, `error`, `rejected`), in words. Once it is active, it also gives the address
the client writes from and, only if the Admin decided to communicate it (`credentials_shared`), the
login to use. The password is never in that answer: `POST /client/v1/channel/mailbox/reveal-password`
shows it, on the client's explicit request (`{"confirm": true}`), a few times an hour, and records
that it was shown (who, when, never the value).

What is never here: the servers, the ports, the security mode, another client's mailbox, a password
in any log or error. Every answer is `no-store`. A request for a mailbox the client has not been
given, or that the Admin keeps back, is refused with the same sentence whichever it is.

The statuses reuse the activation request's (`pending`, `in_progress`, `rejected`, `active`) and add
`error`: the request was approved, but the mailbox cannot be used (no address, no password, or the
platform's servers are no longer set). `not_requested` is a client that never asked.
"""

import contextlib
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StrictBool

from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.channel_status import EMAIL, activation_of, channel_provider_of, channel_settings_of
from sales_app.client_profiles import ClientProfile
from sales_app.config.email_activation import ACTIVE
from sales_app.config.email_settings import PLATFORM_MODE, email_mode_of
from sales_app.config.secrets import EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX
from sales_app.mailbox_proof import NOT_ASSIGNED, PASSWORD_MISSING, mailbox_state
from sales_app.secret_store import SecretStoreError, client_scope

REVEALS_PER_WINDOW = 3
REVEAL_WINDOW = timedelta(hours=1)

MailboxStatus = Literal["not_requested", "pending", "in_progress", "active", "error", "rejected"]

_MESSAGES: dict[str, str] = {
    "not_requested": "Ask for your mailbox when you are ready; Revansa creates it for you.",
    "pending": "Your request has been received. Revansa will start on it shortly.",
    "in_progress": "Revansa is creating your mailbox. There is nothing for you to do.",
    "active": "Your mailbox is active.",
    "error": "Your mailbox cannot be used right now. Revansa has been told and will fix it.",
    "rejected": "Your request was not accepted. See the reason below.",
}
_REFUSED = "this mailbox login is not available to you"


class MailboxResponse(BaseModel):
    status: MailboxStatus
    message: str
    reason: str | None = None  # the Admin's, when the request was rejected
    address: str | None = None  # the client's own mailbox, once it is active
    login: str | None = None  # only if the Admin decided to communicate it
    credentials_shared: bool = False
    password_revealable: bool = False  # the password can be asked for (POST reveal-password)
    updated_at: datetime | None = None


class RevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: StrictBool


class RevealResponse(BaseModel):
    password: str
    shown_for_seconds: int = 60  # a hint for the screen: hide it again after this long


class RevealLimiter:
    """At most `REVEALS_PER_WINDOW` shown passwords per client per window, counted in this process
    (the record of each one is in the credential audit, which every process writes)."""

    def __init__(self) -> None:
        self._times: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_id: str, now: datetime) -> bool:
        with self._lock:
            times = self._times[client_id]
            while times and now - times[0] >= REVEAL_WINDOW:
                times.popleft()
            if len(times) >= REVEALS_PER_WINDOW:
                return False
            times.append(now)
            return True


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def create_client_mailbox_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(
        prefix="/client/v1/channel", tags=["client"], dependencies=[Depends(_no_store)]
    )
    limiter = RevealLimiter()

    def is_platform_mailbox(client_id: str) -> bool:
        found = client_wiring_and_document(application, client_id)
        if found is None:
            return False
        raw, document = found
        if channel_provider_of(raw, document) != EMAIL:
            return False
        return email_mode_of(channel_settings_of(document)) == PLATFORM_MODE

    def shared(client_id: str) -> bool:
        service = application.email_activation
        if service is None:
            return False
        try:
            return service.credentials_shared(client_id)
        except SecretStoreError:
            return False

    def stored(client_id: str, key: str) -> str | None:
        try:
            return application.credentials.reveal(client_scope(client_id), key)
        except SecretStoreError:
            return None

    def describe(client_id: str) -> MailboxResponse:
        record = activation_of(application, client_id)
        if record is None:
            return MailboxResponse(status="not_requested", message=_MESSAGES["not_requested"])
        status: MailboxStatus = record.status  # type: ignore[assignment]  # one of the four
        if record.status == ACTIVE:
            state = mailbox_state(application, client_id)
            if state in (NOT_ASSIGNED, PASSWORD_MISSING, "missing_configuration"):
                status = "error"
        base = {"status": status, "message": _MESSAGES[status], "updated_at": record.updated_at}
        if status == "rejected":
            return MailboxResponse(**base, reason=record.reason)
        if status != "active":
            return MailboxResponse(**base)
        address = stored(client_id, EMAIL_PLATFORM_MAILBOX)
        allowed = shared(client_id)
        return MailboxResponse(
            **base,
            address=address,
            credentials_shared=allowed,
            login=address if allowed else None,  # the login is the full address
            password_revealable=allowed and bool(stored(client_id, EMAIL_PASSWORD)),
        )

    @router.get("/mailbox", response_model=MailboxResponse)
    def get_mailbox(client: ClientProfile = Depends(get_current_client)) -> MailboxResponse:
        if not is_platform_mailbox(client.client_id):
            return MailboxResponse(status="not_requested", message=_MESSAGES["not_requested"])
        return describe(client.client_id)

    @router.post("/mailbox/reveal-password", response_model=RevealResponse)
    def reveal_password(
        body: RevealRequest, client: ClientProfile = Depends(get_current_client)
    ) -> RevealResponse:
        client_id = client.client_id
        actor = f"client:{client_id}"
        now = application.clock.now()
        if not body.confirm:
            raise HTTPException(422, "confirm that you want the password to be shown")
        if not is_platform_mailbox(client_id):
            raise HTTPException(403, _REFUSED)
        record = activation_of(application, client_id)
        if record is None or record.status != ACTIVE or not shared(client_id):
            _record_refusal(client_id, actor, "not_shared", now)
            raise HTTPException(403, _REFUSED)
        if not limiter.allow(client_id, now):
            _record_refusal(client_id, actor, "rate_limited", now)
            raise HTTPException(429, "too many requests; try again later")
        try:
            password = application.credentials.reveal_audited(
                client_scope(client_id), EMAIL_PASSWORD, actor=actor, reason="client_request"
            )
        except SecretStoreError:
            raise HTTPException(503, "this is temporarily unavailable") from None
        if not password:
            raise HTTPException(409, "your mailbox has no password yet")
        return RevealResponse(password=password)

    def _record_refusal(client_id: str, actor: str, reason: str, now: datetime) -> None:
        with contextlib.suppress(Exception):  # the refusal stands even if it is not written down
            application.credentials.record_refused_reveal(
                client_scope(client_id), EMAIL_PASSWORD, actor=actor, reason=reason
            )

    return router
