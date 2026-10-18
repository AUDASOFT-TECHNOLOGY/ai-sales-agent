"""Admin API: a client's daily e-mail cap (see `sales_app.send_cap`).

`PUT/GET/DELETE /admin/v1/clients/{client_id}/send-cap`. The cap is the Admin's alone: a client never
sets it. There is no default and no "unlimited": a client without a cap cannot send any e-mail.
What really limits the client is the *effective* cap, `min(cap, daily quota of its current plan)`,
which the response shows together with today's use (UTC day), so the Admin sees what blocks.

Reading is open to any admin who can reach the client; setting or removing is the owner's (like
assigning a model or a plan). A client the admin cannot reach is a 404, like everywhere else. The
value is a whole number from 1 to `MAX_SEND_CAP`; a string, a float, a boolean or a number out of
range is a 422. Who set it and when are kept with the cap (`updated_by`, `updated_at`).
"""

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, StrictInt

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.bootstrap import Application
from sales_app.send_cap import MAX_SEND_CAP, InvalidSendCapError, utc_day
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

EMAIL = "email"


class SendCapBody(BaseModel):
    daily_cap: StrictInt  # range checked below so that the answer is a 422 with a clear sentence


class SendCapResponse(BaseModel):
    client_id: str
    channel: str = EMAIL
    daily_cap: int | None  # the Admin's cap; None: not set, nothing is sent
    plan_daily_quota: int | None  # the current plan's quota; None: no plan
    effective_cap: int | None  # min of the two; None: nothing is sent
    used_today: int | None  # e-mails counted today (UTC day), all kinds
    remaining_today: int | None
    day_utc: str
    status: Literal["ok", "not_configured", "limit_reached"]
    max_cap: int = MAX_SEND_CAP


def create_admin_send_cap_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def parts():
        if application.send_caps is None or application.send_limits is None:
            raise HTTPException(
                503, "the send cap needs the database (set SALES_AGENT_DATABASE_URL)"
            )
        return application.send_caps, application.send_limits

    def existing_client(admin: AdminUser, client_id: str) -> None:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if client_wiring_and_document(application, client_id) is None:
            raise HTTPException(404, "no such client")

    def respond(client_id: str) -> SendCapResponse:
        _, limits = parts()
        day: date = utc_day(application.clock.now())
        figures = limits.cap_of(client_id)
        used = limits.used(client_id, EMAIL, day)
        effective = figures.effective
        if effective is None:
            status = "not_configured"
        else:
            status = "limit_reached" if used >= effective else "ok"
        return SendCapResponse(
            client_id=client_id,
            daily_cap=figures.admin_cap,
            plan_daily_quota=figures.plan_quota,
            effective_cap=effective,
            used_today=used,
            remaining_today=max(effective - used, 0) if effective is not None else None,
            day_utc=day.isoformat(),
            status=status,
        )

    @router.get("/clients/{client_id}/send-cap", response_model=SendCapResponse)
    async def get_send_cap(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> SendCapResponse:
        existing_client(admin, client_id)
        try:
            return await run_in_threadpool(respond, client_id)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None

    @router.put("/clients/{client_id}/send-cap", response_model=SendCapResponse)
    async def put_send_cap(
        client_id: str, body: SendCapBody, admin: AdminUser = Depends(require_owner)
    ) -> SendCapResponse:
        existing_client(admin, client_id)
        caps, _ = parts()
        now = application.clock.now()

        def write() -> SendCapResponse:
            caps.set(client_id, body.daily_cap, by=admin.username, at=now)
            return respond(client_id)

        try:
            response = await run_in_threadpool(write)
        except InvalidSendCapError as error:
            raise HTTPException(422, str(error)) from None
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        log.info("send cap of %s set to %s by %s", client_id, body.daily_cap, admin.username)
        return response

    @router.delete("/clients/{client_id}/send-cap", response_model=SendCapResponse)
    async def delete_send_cap(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> SendCapResponse:
        existing_client(admin, client_id)
        caps, _ = parts()

        def remove() -> SendCapResponse:
            caps.remove(client_id)
            return respond(client_id)

        try:
            response = await run_in_threadpool(remove)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        log.info("send cap of %s removed by %s", client_id, admin.username)
        return response

    return router
