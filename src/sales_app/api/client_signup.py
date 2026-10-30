"""Public side of the Client API: the plans a visitor can see, the free sign-up, and the upgrade.

- `GET /client/v1/public/plans`: the active plans as the Admin configured them (nothing hard-coded).
- `POST /client/v1/auth/signup`: a visitor creates a Free account. It goes through the same parts as
  an Admin-created client (profile with a hashed password, client record, the channels new clients
  get), is given `FREE_CREDITS` once, and is logged in. A Free account may prepare its agent; every
  commercial entry point refuses it (`require_commercial`).
- `GET /client/v1/account/access`: whether the caller is on the Free tier and its free credits.
- `GET /client/v1/public/pages[/{key}]`: the Contact, Privacy and Terms pages the Admin wrote.
(Paying for a plan offline is in `client_plan_payment`.)
"""

import logging
import re
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from sales_app.admin_auth import InvalidCredentialsError, TooManyAttemptsError
from sales_app.admin_auth.passwords import hash_password
from sales_app.admin_auth.service import _RateLimiter
from sales_app.api.admin_clients import _free_client_id, _grant_new_client_channels, _slug
from sales_app.api.client import COOKIE_NAME, COOKIE_PATH, make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_auth import ClientInactiveError
from sales_app.client_profiles import LOGIN_PATTERN, ClientProfile, normalize_login
from sales_app.client_records import DEFAULT_ENGINE, ClientRecord
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.free_accounts import FREE_CREDITS, UPGRADE_MESSAGE, UPGRADE_REQUIRED, FreeAccount
from sales_app.site_content import PUBLIC_PAGES, read_page
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SIGNUPS_PER_HOUR = 5  # per caller address; a blunt guard, like the login's
UNAVAILABLE = "your account status is temporarily unavailable"


class PublicPlan(BaseModel):
    plan_id: str
    name: str
    monthly_price: str
    currency: str
    billing_period: str
    included_credits: int
    daily_quota: int


class PublicPlans(BaseModel):
    plans: list[PublicPlan]


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    # A honeypot: a person never fills it, a script usually does.
    website: str = Field(default="", max_length=200)


class AccessResponse(BaseModel):
    tier: Literal["free", "paid"]
    free_credits: int | None = None
    credits_remaining: int | None = None


class PublicPage(BaseModel):
    key: str
    locale: str
    title: str
    body: str


class PublicPageList(BaseModel):
    pages: list[str]


def require_commercial(application: Application, client_id: str) -> None:
    """Refuses (403 `upgrade_required`) a client that may not act commercially, 503 if that cannot
    be known. Called by every client route that starts or triggers selling."""
    try:
        blocked = application.commercial_access.blocked(client_id)
    except Exception:  # noqa: BLE001 - an unreadable rule never means "go"
        raise HTTPException(503, UNAVAILABLE) from None
    if blocked:
        raise HTTPException(403, detail={"code": UPGRADE_REQUIRED, "message": UPGRADE_MESSAGE})


def create_client_signup_router(application: Application) -> APIRouter:
    auth = application.client_auth
    get_current_client = make_current_client_dependency(auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])
    limiter = _RateLimiter(max_attempts=SIGNUPS_PER_HOUR, window=timedelta(hours=1))

    @router.get("/public/plans", response_model=PublicPlans)
    def public_plans(response: Response) -> PublicPlans:
        response.headers["Cache-Control"] = "no-store"
        if application.plans is None:
            raise HTTPException(503, "plans are unavailable")
        try:
            found = application.plans.list_plans()
        except StoreUnavailableError:
            raise HTTPException(503, "plans are unavailable") from None
        return PublicPlans(
            plans=[
                PublicPlan(
                    plan_id=p.plan_id,
                    name=p.name,
                    monthly_price=format(p.monthly_price, "f"),
                    currency=p.currency,
                    billing_period=p.billing_period,
                    included_credits=p.included_credits,
                    daily_quota=p.daily_quota,
                )
                for p in sorted(found, key=lambda p: p.monthly_price)
                if p.active
            ]
        )

    @router.post("/auth/signup", status_code=201)
    def signup(body: SignupRequest, request: Request, response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "no-store"
        now = application.clock.now()
        caller = request.client.host if request.client else "unknown"
        key = f"signup:{caller}"
        try:
            limiter.check(key, now)
        except TooManyAttemptsError:
            raise HTTPException(429, "too many attempts; try again later") from None
        limiter.record_failure(key, now)  # every attempt counts, successful or not
        if body.website.strip():  # the honeypot: answer like a success, create nothing
            return {"status": "created"}
        free_accounts = application.commercial_access.free
        if application.client_records is None or free_accounts is None:
            raise HTTPException(503, "sign-up needs the database")

        display_name = " ".join(body.name.split())
        login = normalize_login(body.email)
        if not display_name:
            raise HTTPException(400, "name must not be empty")
        if not _EMAIL.match(login) or not LOGIN_PATTERN.match(login):
            raise HTTPException(400, "enter a valid email address (64 characters at most)")
        base = _slug(display_name)
        if not base or not CLIENT_ID_PATTERN.match(base):
            raise HTTPException(400, "name must contain letters or digits")
        if application.client_profiles.get_by_login(login) is not None:
            raise HTTPException(409, "an account already exists with this email")

        client_id = _free_client_id(application, base)
        by = f"signup:{client_id}"
        profile = ClientProfile(
            client_id=client_id,
            login=login,
            password_hash=hash_password(body.password),
            must_change_password=False,  # the client chose this password itself
            created_at=now,
            created_by=by,
        )
        try:
            application.client_profiles.create(profile)
        except ValueError:  # a concurrent sign-up took the same email
            raise HTTPException(409, "an account already exists with this email") from None
        application.client_records.save(
            ClientRecord(client_id, display_name, True, DEFAULT_ENGINE), at=now, by=by
        )
        try:
            free_accounts.add(FreeAccount(client_id, FREE_CREDITS, now))
        except Exception:  # noqa: BLE001 - without the record the account would not be Free
            log.error("could not record the free account of %s", client_id)
            raise HTTPException(503, "sign-up is temporarily unavailable") from None
        application.registry.add_disabled(client_id)
        _grant_new_client_channels(application, client_id, "signup")

        try:
            result = auth.login(login, body.password)
        except (InvalidCredentialsError, ClientInactiveError, TooManyAttemptsError):
            return {"status": "created"}  # the account exists: the client signs in by hand
        response.set_cookie(
            COOKIE_NAME,
            result.token,
            max_age=int((result.session.expires_at - result.session.created_at).total_seconds()),
            httponly=True,
            secure=application.settings.admin_cookie_secure,
            samesite="lax",
            path=COOKIE_PATH,
        )
        return {"status": "created"}

    @router.get("/account/access", response_model=AccessResponse)
    def access(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> AccessResponse:
        response.headers["Cache-Control"] = "no-store"
        try:
            if not application.commercial_access.is_free(client.client_id):
                return AccessResponse(tier="paid")
            quota = (
                application.quotas.status(client.client_id)
                if application.quotas is not None
                else None
            )
        except Exception:  # noqa: BLE001
            raise HTTPException(503, UNAVAILABLE) from None
        return AccessResponse(
            tier="free",
            free_credits=FREE_CREDITS,
            credits_remaining=quota.credits_remaining if quota is not None else None,
        )

    @router.get("/public/pages", response_model=PublicPageList)
    def public_pages(response: Response) -> PublicPageList:
        """Which public pages the Admin has written (so the landing links only to what exists)."""
        response.headers["Cache-Control"] = "no-store"
        store = application.site_pages
        if store is None:
            return PublicPageList(pages=[])
        try:
            found = store.list_all()
        except StoreUnavailableError:
            return PublicPageList(pages=[])
        return PublicPageList(pages=sorted({p.key for p in found if p.key in PUBLIC_PAGES}))

    @router.get("/public/pages/{key}", response_model=PublicPage)
    def public_page(key: str, response: Response, locale: str = "en") -> PublicPage:
        response.headers["Cache-Control"] = "no-store"
        if key not in PUBLIC_PAGES or application.site_pages is None:
            raise HTTPException(404, "no such page")
        try:
            page = read_page(application.site_pages, key, locale)
        except StoreUnavailableError:
            raise HTTPException(503, "this page is unavailable") from None
        if page is None:
            raise HTTPException(404, "no such page")
        return PublicPage(key=key, locale=page.locale, title=page.title, body=page.body)

    return router
