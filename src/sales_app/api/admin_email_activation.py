"""Admin API: the clients' requests to have their e-mail activated, worked through by hand.

Revansa creates no mailbox: the Admin creates it at the provider, then records it here. The flow:

1. The client asks (`POST /client/v1/channel/email-activation`): `pending`.
2. The Admin takes it (`start`): `in_progress`. Or refuses it (`reject`, with a reason the client reads).
3. The Admin gives the address and the password of the mailbox they created (`activate`). Both logins
   are really tried first, with the platform's servers; only if both are accepted are the address and
   the password stored (write-only, encrypted) and the request made `active`. Otherwise nothing is
   stored and the request stays `in_progress`, with an answer that says which login failed.
4. `deactivate` ends an active request (with a reason) and removes the mailbox from the client.

The password is read from the body by hand, so no error answer ever repeats it, and it is never in any
response. Only the OWNER role may change anything; reading is open to any admin who can reach the
client. Responses are `no-store`.
"""

import contextlib
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, ValidationError

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.bootstrap import Application
from sales_app.channel_status import (
    EMAIL,
    allowed_channels,
    channel_provider_of,
    channel_settings_of,
)
from sales_app.config.email_activation import ActivationRecord
from sales_app.config.email_settings import PlatformEmailSettings, parse_email_settings
from sales_app.config.secrets import EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX
from sales_app.email_activation import (
    ActivationConflictError,
    EmailActivationService,
    InvalidReasonError,
)
from sales_app.email_platform import (
    MailLoginProbe,
    MailLoginProbeFactory,
    read_profile,
    run_login_check,
)
from sales_app.mailbox_access import platform_access, sender_name
from sales_app.mailbox_proof import NOT_AUTHENTICATED, ClientMailbox, mailbox_state
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    DuplicateSecretError,
    InvalidCredentialError,
    SecretStoreError,
    client_scope,
)
from sales_app.secret_store.catalog import normalize_value, spec_for


class ActivationItem(BaseModel):
    """One request. `sender_name` is the name the client confirmed, as the messages will carry it
    (null: none confirmed yet, so there is nothing to create a mailbox for). It is not an address.
    `mailbox` says where the mailbox stands, never what it is: no address, no password."""

    client_id: str
    client_name: str | None = None
    sender_name: str | None = None
    status: Literal["pending", "in_progress", "rejected", "active"]
    requested_at: datetime
    updated_at: datetime
    updated_by: str
    reason: str | None = None
    email_allowed: bool  # whether the Admin lets this client use Email at all (a separate decision)
    mailbox: str  # the states of `sales_app.mailbox_proof`


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    address: str
    password: str


_ACTIVATE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["address", "password"],
                    "properties": {
                        "address": {"type": "string"},
                        "password": {"type": "string", "writeOnly": True},
                    },
                    "additionalProperties": False,
                }
            }
        },
    }
}
_REJECT_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["reason"],
                    "properties": {"reason": {"type": "string"}},
                    "additionalProperties": False,
                }
            }
        },
    }
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _read_body[M: BaseModel](request: Request, model: type[M]) -> M:
    """The body, validated, with errors that say where and why but never *what*: the framework's own
    422 repeats the offending input, which here may be a password."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise HTTPException(
            422,
            [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in error.errors()],
        ) from None


def create_admin_email_activation_router(
    application: Application,
    mail_login_probe: MailLoginProbeFactory = MailLoginProbe,
) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(_no_store)])
    credentials = application.credentials

    def service() -> EmailActivationService:
        found = application.email_activation
        if found is None:
            raise HTTPException(503, "email activation is not available")
        return found

    def client_exists(client_id: str) -> bool:
        return client_wiring_and_document(application, client_id) is not None

    def reach(admin: AdminUser, client_id: str) -> None:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if not client_exists(client_id):
            raise HTTPException(404, "no such client")

    def item(client_id: str, record: ActivationRecord) -> ActivationItem:
        found = client_wiring_and_document(application, client_id)
        raw, document = found if found is not None else ({}, {})
        policy = application.channel_policy
        return ActivationItem(
            client_id=client_id,
            client_name=raw.get("display_name")
            if isinstance(raw.get("display_name"), str)
            else None,
            sender_name=sender_name(channel_settings_of(document).get("from_name")),
            status=record.status,  # type: ignore[arg-type]  # one of the four statuses
            requested_at=record.requested_at,
            updated_at=record.updated_at,
            updated_by=record.updated_by,
            reason=record.reason,
            email_allowed=policy is not None and EMAIL in policy.effective(client_id),
            mailbox=mailbox_state(application, client_id),
        )

    def guarded[T](action) -> T:
        try:
            return action()
        except ActivationConflictError as conflict:
            raise HTTPException(409, str(conflict)) from None
        except InvalidReasonError as invalid:
            raise HTTPException(422, str(invalid)) from None
        except SecretStoreError:
            raise HTTPException(503, "request storage is temporarily unavailable") from None

    def refresh(client_id: str) -> None:
        try:
            if application.registry.get_config(client_id) is not None:
                application.runtime_manager.refresh_client(client_id)
        except Exception:  # noqa: BLE001 - the runtime status says how it ended
            return

    # ---- reading --------------------------------------------------------------------------

    @router.get("/email-activations", response_model=list[ActivationItem])
    def list_requests(
        status: Literal["open", "all"] = "open", admin: AdminUser = Depends(get_current_admin)
    ) -> list[ActivationItem]:
        """The requests of the clients this admin can reach, oldest first. `open` (the default) is
        what waits for the Admin: `pending` and `in_progress`."""
        found: list[ActivationItem] = []
        for profile in application.client_profiles.list():
            if not admin.can_reach(profile.client_id):
                continue
            try:
                record = service().get(profile.client_id)
            except SecretStoreError:
                raise HTTPException(503, "request storage is temporarily unavailable") from None
            if record is None or (
                status == "open" and record.status not in ("pending", "in_progress")
            ):
                continue
            found.append(item(profile.client_id, record))
        return sorted(found, key=lambda i: (i.requested_at, i.client_id))

    @router.get("/clients/{client_id}/email-activation", response_model=ActivationItem | None)
    def get_request(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> ActivationItem | None:
        reach(admin, client_id)
        record = guarded(lambda: service().get(client_id))
        return None if record is None else item(client_id, record)

    # ---- moving a request -----------------------------------------------------------------

    @router.post("/clients/{client_id}/email-activation/start", response_model=ActivationItem)
    def start(client_id: str, admin: AdminUser = Depends(require_owner)) -> ActivationItem:
        """`pending` to `in_progress`."""
        reach(admin, client_id)
        return item(client_id, guarded(lambda: service().start(client_id, actor=admin.username)))

    @router.post(
        "/clients/{client_id}/email-activation/reject",
        response_model=ActivationItem,
        openapi_extra=_REJECT_BODY,
    )
    async def reject(
        client_id: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> ActivationItem:
        """Refuse a request. The reason is required and is shown to the client."""
        reach(admin, client_id)
        body = await _read_body(request, RejectRequest)
        record = guarded(lambda: service().reject(client_id, body.reason, actor=admin.username))
        return item(client_id, record)

    @router.post(
        "/clients/{client_id}/email-activation/deactivate",
        response_model=ActivationItem,
        openapi_extra=_REJECT_BODY,
    )
    async def deactivate(
        client_id: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> ActivationItem:
        """End an active request: the mailbox is taken off the client, which sees the reason and
        may ask again."""
        reach(admin, client_id)
        body = await _read_body(request, RejectRequest)

        def end() -> ActivationRecord:
            record = service().deactivate(client_id, body.reason, actor=admin.username)
            scope = client_scope(client_id)
            for key in (EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX):
                credentials.remove(scope, key, actor=admin.username)
            return record

        record = await run_in_threadpool(guarded, end)
        refresh(client_id)
        return item(client_id, record)

    # ---- activating -----------------------------------------------------------------------

    def prerequisites(client_id: str) -> None:
        """What must hold before a mailbox is tried. Each answer names one thing to fix."""
        found = client_wiring_and_document(application, client_id)
        assert found is not None
        raw, document = found
        if EMAIL not in allowed_channels(application, client_id):
            raise HTTPException(409, "Email is not allowed for this client: allow it first")
        if channel_provider_of(raw, document) != EMAIL:
            raise HTTPException(409, "this client's channel is not email")
        try:
            settings = parse_email_settings(channel_settings_of(document))
        except ValueError:
            raise HTTPException(409, "this client's email settings are incomplete") from None
        if not isinstance(settings, PlatformEmailSettings):
            raise HTTPException(409, "this client's email is not a mailbox Revansa provides")
        if not sender_name(settings.from_name):
            raise HTTPException(409, "this client has not confirmed a Sender Name")

    def probe(client_id: str, address: str, password: str) -> list[str]:
        """Try both logins with the platform's servers. Returns the failures as fixed sentences (an
        empty list: both accepted). The outcome is remembered as the proof of this mailbox."""
        profile, _ = read_profile(lambda key: _stored_global(key))
        if profile is None:
            raise HTTPException(409, "the platform mail servers are not configured: see Settings")
        access = platform_access(profile, address, password)
        mailbox = ClientMailbox(NOT_AUTHENTICATED, access, None)
        checker = mail_login_probe(access.smtp, access.imap)
        failures = []
        for protocol in ("smtp", "imap"):
            result = run_login_check(
                protocol,
                checker,
                application.mail_auth,
                f"client:{client_id}:{protocol}",
                mailbox.print_,
                protocol.upper(),
            )
            if not result.ok:
                failures.append(result.message)
        return failures

    def _stored_global(key: str) -> str | None:
        try:
            return credentials.reveal(GLOBAL_SCOPE, key)
        except SecretStoreError:
            return None

    def store_mailbox(
        client_id: str, address: str, password: str, actor: str
    ) -> dict[str, str | None]:
        """Save the address then the password, or leave the client's mailbox exactly as it was.
        Returns what was there before, so a later failure can put it back."""
        scope = client_scope(client_id)
        before = {key: _revealed(scope, key) for key in (EMAIL_PLATFORM_MAILBOX, EMAIL_PASSWORD)}
        try:
            credentials.save(scope, EMAIL_PLATFORM_MAILBOX, address, actor=actor, apply=False)
            credentials.save(scope, EMAIL_PASSWORD, password, actor=actor, apply=False)
        except Exception:
            _restore(scope, before, actor)
            raise
        return before

    def _revealed(scope: str, key: str) -> str | None:
        try:
            return credentials.reveal(scope, key)
        except SecretStoreError:
            return None

    def _restore(scope: str, before: dict[str, str | None], actor: str) -> None:
        for key, value in before.items():
            with contextlib.suppress(Exception):  # restoring must try every key
                if value is None:
                    credentials.remove(scope, key, actor=actor)
                else:
                    credentials.save(scope, key, value, actor=actor, apply=False)

    @router.post(
        "/clients/{client_id}/email-activation/activate",
        response_model=ActivationItem,
        openapi_extra=_ACTIVATE_BODY,
    )
    async def activate(
        client_id: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> ActivationItem:
        """Activate a request with the mailbox the Admin created. The request must be `in_progress`.
        Both logins are tried before anything is stored: if either fails the answer is 422 with the
        reason, nothing is stored and the request stays as it was."""
        reach(admin, client_id)
        body = await _read_body(request, ActivateRequest)
        record = await run_in_threadpool(_activate, client_id, body, admin.username)
        refresh(client_id)
        return item(client_id, record)

    def _activate(client_id: str, body: ActivateRequest, actor: str) -> ActivationRecord:
        current = guarded(lambda: service().get(client_id))
        if current is None:
            raise HTTPException(409, "this client has no activation request")
        if current.status != "in_progress":
            raise HTTPException(409, "start the request before activating it")
        prerequisites(client_id)
        scope = client_scope(client_id)
        try:
            address = normalize_value(spec_for(scope, EMAIL_PLATFORM_MAILBOX), body.address)
            password = normalize_value(spec_for(scope, EMAIL_PASSWORD), body.password)
        except InvalidCredentialError as invalid:
            raise HTTPException(422, str(invalid)) from None
        failures = probe(client_id, address, password)
        if failures:
            raise HTTPException(
                422,
                {
                    "message": "the mailbox could not be authenticated: nothing was saved",
                    "problems": failures,
                },
            )
        try:
            before = store_mailbox(client_id, address, password, actor)
        except DuplicateSecretError:
            raise HTTPException(409, "this address is already assigned to another client") from None
        except InvalidCredentialError as invalid:
            raise HTTPException(422, str(invalid)) from None
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        try:
            return service().activate(client_id, actor=actor)
        except ActivationConflictError as conflict:
            _restore(scope, before, actor)
            raise HTTPException(409, str(conflict)) from None
        except SecretStoreError:
            _restore(scope, before, actor)
            raise HTTPException(503, "request storage is temporarily unavailable") from None

    return router
