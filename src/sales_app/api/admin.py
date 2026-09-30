"""Admin API: authentication and the building blocks every other admin route depends on.

This module owns only identity: who is logged in, with which role, for which clients. It knows
nothing about a client's data or configuration -- that belongs to the routers built on top of it
(Configuration, CRM, Dashboard: not built yet, see docs/web-admin-product-design.md).
"""

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from sales_app.admin_auth import (
    AdminAuthService,
    AdminRole,
    AdminUser,
    InvalidCredentialsError,
    TooManyAttemptsError,
)
from sales_app.bootstrap import Application

COOKIE_NAME = "revansa_admin_session"
COOKIE_PATH = "/admin/v1"


class LoginRequest(BaseModel):
    username: str
    password: str


class SessionResponse(BaseModel):
    username: str
    role: AdminRole
    client_ids: list[str]


def make_current_admin_dependency(auth: AdminAuthService):
    """One `get_current_admin` dependency per `AdminAuthService`: reads the session cookie and
    resolves it to an `AdminUser`, or raises 401."""

    def get_current_admin(
        session_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> AdminUser:
        if session_token is None:
            raise HTTPException(401, "not authenticated")
        user = auth.authenticate(session_token)
        if user is None:
            raise HTTPException(401, "session expired or invalid")
        return user

    return get_current_admin


def require_role(get_current_admin, *roles: AdminRole):
    """A dependency that additionally demands one of `roles`, built on `get_current_admin`
    (itself built by `make_current_admin_dependency`). For routers added later."""

    def dependency(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if admin.role not in roles:
            raise HTTPException(403, "not allowed for this role")
        return admin

    return dependency


def ensure_client_access(admin: AdminUser, client_id: str) -> None:
    """404, not 403: a client an admin cannot reach must not even appear to exist to them. Every
    future route that takes a `client_id` calls this before touching that client's data."""
    if not admin.can_reach(client_id):
        raise HTTPException(404, "no such client")


def create_admin_router(application: Application) -> APIRouter:
    auth = application.admin_auth
    get_current_admin = make_current_admin_dependency(auth)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.post("/auth/login", response_model=SessionResponse)
    def login(body: LoginRequest, response: Response) -> SessionResponse:
        try:
            result = auth.login(body.username, body.password)
        except TooManyAttemptsError:
            raise HTTPException(429, "too many attempts; try again later") from None
        except InvalidCredentialsError:
            raise HTTPException(401, "invalid username or password") from None
        response.set_cookie(
            COOKIE_NAME,
            result.token,
            max_age=int((result.session.expires_at - result.session.created_at).total_seconds()),
            httponly=True,
            secure=application.settings.admin_cookie_secure,
            samesite="lax",
            path=COOKIE_PATH,
        )
        return SessionResponse(
            username=result.user.username,
            role=result.user.role,
            client_ids=sorted(result.user.client_ids),
        )

    @router.post("/auth/logout")
    def logout(
        response: Response,
        session_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
        _admin: AdminUser = Depends(get_current_admin),
    ) -> dict[str, str]:
        if session_token is not None:
            auth.logout(session_token)
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
        return {"status": "ok"}

    @router.get("/auth/me", response_model=SessionResponse)
    def me(admin: AdminUser = Depends(get_current_admin)) -> SessionResponse:
        return SessionResponse(
            username=admin.username, role=admin.role, client_ids=sorted(admin.client_ids)
        )

    return router
