"""Client API: a Revansa client logs in, reads its own profile and changes its password.

Every route is `/client/v1/...` and none takes a client id: who the caller is comes only from the
session cookie (`ClientAuthService.authenticate`), so no id supplied by the browser can reach
another client's data. The cookie is a different one from the Admin's (and scoped to a different
path), so a Client session never opens `/admin/v1`, and an Admin session never opens this.

No response carries a password, a hash, a Meta credential or any other secret: the profile is
name, login, WhatsApp number and status, nothing else.
"""

from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from sales_app.admin_auth import InvalidCredentialsError, TooManyAttemptsError
from sales_app.api.admin import _audit_refused_login
from sales_app.api.admin_clients import _clean_whatsapp_number, _digits, rename_client
from sales_app.bootstrap import Application
from sales_app.client_auth import (
    ClientAuthService,
    ClientInactiveError,
    ClientLoginResult,
    WrongCurrentPasswordError,
    is_client_active,
    stored_display_name,
)
from sales_app.client_profiles import LOGIN_PATTERN, ClientProfile, normalize_login

COOKIE_NAME = "revansa_client_session"
COOKIE_PATH = "/client/v1"


class ClientLoginRequest(BaseModel):
    login: str
    password: str


class ClientProfileResponse(BaseModel):
    name: str
    login: str
    whatsapp_number: str | None = None
    status: Literal["active", "inactive"]
    must_change_password: bool
    # True after the client changed its number: the new one is not connected to Meta yet. Says
    # nothing else about Meta -- no id, token or secret is ever part of this response.
    whatsapp_reconnect_required: bool = False


class UpdateProfileRequest(BaseModel):
    """What a client may edit about itself. `status` (or anything else) is refused, not ignored:
    `extra="forbid"` answers 422, so a forged field never silently half-works."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    login: str | None = Field(default=None, min_length=1, max_length=64)
    whatsapp_number: str | None = Field(default=None, min_length=1, max_length=40)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(max_length=128)


def make_current_client_dependency(auth: ClientAuthService):
    """One `get_current_client` dependency per `ClientAuthService`: reads the session cookie and
    resolves it to the logged-in client's profile, or raises 401."""

    def get_current_client(
        session_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> ClientProfile:
        if session_token is None:
            raise HTTPException(401, "not authenticated")
        profile = auth.authenticate(session_token)
        if profile is None:
            raise HTTPException(401, "session expired or invalid")
        return profile

    return get_current_client


def create_client_router(application: Application) -> APIRouter:
    auth = application.client_auth
    get_current_client = make_current_client_dependency(auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])
    clients_dir = application.settings.client_files_dir

    def current_name(client_id: str) -> str | None:
        """The newest name wins: a published settings version is read before the running config,
        which only catches up at the next runtime refresh."""
        published = application.config_store.current(client_id)
        if published is not None and isinstance(published.document.get("display_name"), str):
            return published.document["display_name"]
        config = application.registry.get_config(client_id)
        if config is not None:
            return config.spec.display_name
        return stored_display_name(clients_dir, client_id, application.client_records)

    def profile_response(profile: ClientProfile) -> ClientProfileResponse:
        name = current_name(profile.client_id)
        return ClientProfileResponse(
            name=name or profile.client_id,
            login=profile.login,
            whatsapp_number=profile.whatsapp_number,
            status="active"
            if is_client_active(clients_dir, profile.client_id, application.client_records)
            else "inactive",
            must_change_password=profile.must_change_password,
            whatsapp_reconnect_required=profile.whatsapp_reconnect_required,
        )

    def set_session_cookie(response: Response, result: ClientLoginResult) -> None:
        response.set_cookie(
            COOKIE_NAME,
            result.token,
            max_age=int((result.session.expires_at - result.session.created_at).total_seconds()),
            httponly=True,
            secure=application.settings.admin_cookie_secure,
            samesite="lax",
            path=COOKIE_PATH,
        )

    @router.post("/auth/login", response_model=ClientProfileResponse)
    def login(body: ClientLoginRequest, response: Response) -> ClientProfileResponse:
        try:
            result = auth.login(body.login, body.password)
        except TooManyAttemptsError:
            _audit_refused_login(application, "client", body.login, "too_many_attempts")
            raise HTTPException(429, "too many attempts; try again later") from None
        except InvalidCredentialsError:
            _audit_refused_login(application, "client", body.login, "invalid_credentials")
            raise HTTPException(401, "invalid login or password") from None
        except ClientInactiveError:
            _audit_refused_login(application, "client", body.login, "inactive_account")
            raise HTTPException(403, "this account is inactive") from None
        set_session_cookie(response, result)
        return profile_response(result.profile)

    @router.post("/auth/logout")
    def logout(
        response: Response,
        session_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
        _client: ClientProfile = Depends(get_current_client),
    ) -> dict[str, str]:
        if session_token is not None:
            auth.logout(session_token)
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
        return {"status": "ok"}

    @router.get("/auth/me", response_model=ClientProfileResponse)
    def me(client: ClientProfile = Depends(get_current_client)) -> ClientProfileResponse:
        return profile_response(client)

    @router.get("/profile", response_model=ClientProfileResponse)
    def profile(client: ClientProfile = Depends(get_current_client)) -> ClientProfileResponse:
        return profile_response(client)

    @router.patch("/profile", response_model=ClientProfileResponse)
    def update_profile(
        body: UpdateProfileRequest, client: ClientProfile = Depends(get_current_client)
    ) -> ClientProfileResponse:
        # `client` comes from the session: this can only ever edit the caller's own profile.
        changes: dict = {}
        name: str | None = None
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(400, "name must not be empty")
        if body.login is not None:
            login = normalize_login(body.login)
            if not LOGIN_PATTERN.match(login):
                raise HTTPException(
                    400, "login must be 3-64 characters: letters, digits and . _ @ + - only"
                )
            holder = application.client_profiles.get_by_login(login)
            if holder is not None and holder.client_id != client.client_id:
                raise HTTPException(409, "this login is already used")
            changes["login"] = login
        if body.whatsapp_number is not None:
            number = _clean_whatsapp_number(body.whatsapp_number)
            if any(
                p.whatsapp_number and _digits(p.whatsapp_number) == _digits(number)
                for p in application.client_profiles.list()
                if p.client_id != client.client_id
            ):
                raise HTTPException(409, "this WhatsApp number already belongs to another client")
            changes["whatsapp_number"] = number
            if _digits(number) != _digits(client.whatsapp_number or ""):
                # A different number is not connected to Meta: say so, never pretend otherwise.
                changes["whatsapp_reconnect_required"] = True
        updated = client
        if changes:
            updated = replace(client, **changes)
            try:  # first: a login race fails here, before anything else is written
                application.client_profiles.update(updated)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        if name is not None:
            rename_client(application, client.client_id, name, author=f"client:{client.client_id}")
        return profile_response(updated)

    @router.post("/profile/password", response_model=ClientProfileResponse)
    def change_password(
        body: ChangePasswordRequest,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> ClientProfileResponse:
        if body.new_password != body.confirm_password:
            raise HTTPException(400, "the new password and its confirmation do not match")
        try:
            result = auth.change_password(
                client.client_id, body.current_password, body.new_password
            )
        except TooManyAttemptsError:
            raise HTTPException(429, "too many attempts; try again later") from None
        except WrongCurrentPasswordError:
            raise HTTPException(400, "the current password is incorrect") from None
        set_session_cookie(response, result)  # every older session of this client just ended
        return profile_response(result.profile)

    return router
