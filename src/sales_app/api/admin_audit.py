"""Admin API: the central audit log and the Admin's notifications.

`GET /admin/v1/audit` (filters: client, action, actor, period; paginated) is for the owner and the
operator; an operator sees only the entries of the clients they can reach (and none without a client).
`GET /admin/v1/audit/secrets` is the owner's view of the specialised credential register (who changed
which credential: never a value). Notifications: `GET /admin/v1/notifications` lists the `admin`
notifications of the clients the admin can reach (the owner also sees the platform's own), and
`POST .../{id}/read` / `POST .../read-all` mark them read. A client the admin cannot reach is a 404.
No response here carries a secret: the audit documents were redacted before they were kept.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.audit_log import AuditEntry, AuditQuery
from sales_app.bootstrap import Application
from sales_app.notifications import ADMIN, Notification, NotificationQuery
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

MAX_PAGE = 200


class AuditItem(BaseModel):
    id: int | None
    at: datetime
    actor: str
    actor_role: str
    client_id: str | None
    action: str
    target: str
    outcome: str
    before: Any = None
    after: Any = None


class AuditPage(BaseModel):
    items: list[AuditItem]
    total: int
    limit: int
    offset: int


class SecretAuditItem(BaseModel):
    at: datetime
    actor: str
    action: str
    scope: str
    key: str
    outcome: str
    reason: str | None = None


class NotificationItem(BaseModel):
    id: int
    created_at: datetime
    kind: str
    severity: str
    client_id: str | None
    title: str
    message: str
    action: str
    read_at: datetime | None
    resolved_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationItem]
    total: int
    unread_count: int


def notification_item(n: Notification) -> NotificationItem:
    assert n.id is not None
    return NotificationItem(
        id=n.id,
        created_at=n.created_at,
        kind=n.kind,
        severity=n.severity,
        client_id=n.client_id,
        title=n.title,
        message=n.message,
        action=n.action,
        read_at=n.read_at,
        resolved_at=n.resolved_at,
    )


def _audit_item(e: AuditEntry) -> AuditItem:
    return AuditItem(
        id=e.id,
        at=e.at,
        actor=e.actor,
        actor_role=e.actor_role,
        client_id=e.client_id,
        action=e.action,
        target=e.target,
        outcome=e.outcome,
        before=e.before,
        after=e.after,
    )


def _visible_clients(admin: AdminUser) -> frozenset[str] | None:
    """None: every client (the owner); otherwise exactly the admin's own list."""
    return None if admin.role is AdminRole.OWNER else frozenset(admin.client_ids)


def create_admin_audit_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    may_audit = require_role(get_current_admin, AdminRole.OWNER, AdminRole.OPERATOR)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/audit", response_model=AuditPage)
    async def audit(
        client_id: str | None = Query(default=None, max_length=64),
        action: str | None = Query(default=None, max_length=96),
        actor: str | None = Query(default=None, max_length=128),
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE),
        offset: int = Query(default=0, ge=0),
        admin: AdminUser = Depends(may_audit),
    ) -> AuditPage:
        if client_id is not None:
            ensure_client_access(admin, client_id)
        query = AuditQuery(
            client_ids=_visible_clients(admin),
            client_id=client_id,
            action=action,
            actor=actor,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
        try:
            entries, total = await run_in_threadpool(application.audit.search, query)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return AuditPage(
            items=[_audit_item(e) for e in entries], total=total, limit=limit, offset=offset
        )

    @router.get("/audit/secrets", response_model=list[SecretAuditItem])
    def secrets_audit(
        limit: int = Query(default=100, ge=1, le=MAX_PAGE),
        _admin: AdminUser = Depends(require_owner),
    ) -> list[SecretAuditItem]:
        register = application.secret_audit
        if register is None:
            return []
        return [
            SecretAuditItem(
                at=e.at,
                actor=e.actor,
                action=e.action,
                scope=e.scope,
                key=e.key,
                outcome=e.outcome,
                reason=e.reason,
            )
            for e in register.recent(limit)
        ]

    # ---- notifications --------------------------------------------------------------------

    def scope_query(admin: AdminUser, **more: Any) -> NotificationQuery:
        return NotificationQuery(audience=ADMIN, client_ids=_visible_clients(admin), **more)

    @router.get("/notifications", response_model=NotificationPage)
    async def notifications(
        client_id: str | None = Query(default=None, max_length=64),
        unread: bool = False,
        include_resolved: bool = True,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE),
        offset: int = Query(default=0, ge=0),
        admin: AdminUser = Depends(get_current_admin),
    ) -> NotificationPage:
        if client_id is not None:
            ensure_client_access(admin, client_id)
        query = scope_query(
            admin,
            client_id=client_id,
            unread_only=unread,
            include_resolved=include_resolved,
            limit=limit,
            offset=offset,
        )
        service = application.notifications
        try:
            found, total = await run_in_threadpool(service.search, query)
            unread_count = await run_in_threadpool(service.unread_count, scope_query(admin))
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return NotificationPage(
            items=[notification_item(n) for n in found], total=total, unread_count=unread_count
        )

    @router.post("/notifications/read-all")
    async def read_all(admin: AdminUser = Depends(get_current_admin)) -> dict[str, int]:
        try:
            count = await run_in_threadpool(
                application.notifications.mark_all_read, scope_query(admin)
            )
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return {"marked": count}

    @router.post("/notifications/{notification_id}/read")
    async def read_one(
        notification_id: int, admin: AdminUser = Depends(get_current_admin)
    ) -> dict[str, str]:
        try:
            found = await run_in_threadpool(
                application.notifications.mark_read, scope_query(admin), notification_id
            )
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        if not found:
            raise HTTPException(404, "no such notification")
        return {"status": "read"}

    return router
