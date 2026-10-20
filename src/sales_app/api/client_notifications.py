"""Client API: the client's own notifications.

`GET /client/v1/notifications`, `POST /client/v1/notifications/{id}/read`, `POST .../read-all`. None
takes a client id: the session decides it, and the query is always built with `audience=client` and
that client, so another client's notification (or an Admin's) is a 404, never a leak. Texts are fixed
sentences with a suggested action; no secret is ever part of a notification.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.notifications import CLIENT, Notification, NotificationQuery
from sales_app.sourcing.errors import StoreUnavailableError


class ClientNotification(BaseModel):
    id: int
    created_at: datetime
    kind: str
    severity: str
    title: str
    message: str
    action: str
    read_at: datetime | None
    resolved_at: datetime | None


class ClientNotificationPage(BaseModel):
    items: list[ClientNotification]
    total: int
    unread_count: int


def _item(n: Notification) -> ClientNotification:
    assert n.id is not None
    return ClientNotification(
        id=n.id,
        created_at=n.created_at,
        kind=n.kind,
        severity=n.severity,
        title=n.title,
        message=n.message,
        action=n.action,
        read_at=n.read_at,
        resolved_at=n.resolved_at,
    )


def create_client_notifications_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def mine(client: ClientProfile, **more) -> NotificationQuery:
        return NotificationQuery(audience=CLIENT, client_id=client.client_id, **more)

    @router.get("/notifications", response_model=ClientNotificationPage)
    async def list_notifications(
        unread: bool = False,
        include_resolved: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        client: ClientProfile = Depends(get_current_client),
    ) -> ClientNotificationPage:
        service = application.notifications
        query = mine(
            client,
            unread_only=unread,
            include_resolved=include_resolved,
            limit=limit,
            offset=offset,
        )
        try:
            found, total = await run_in_threadpool(service.search, query)
            unread_count = await run_in_threadpool(service.unread_count, mine(client))
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return ClientNotificationPage(
            items=[_item(n) for n in found], total=total, unread_count=unread_count
        )

    @router.post("/notifications/read-all")
    async def read_all(client: ClientProfile = Depends(get_current_client)) -> dict[str, int]:
        try:
            count = await run_in_threadpool(application.notifications.mark_all_read, mine(client))
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return {"marked": count}

    @router.post("/notifications/{notification_id}/read")
    async def read_one(
        notification_id: int, client: ClientProfile = Depends(get_current_client)
    ) -> dict[str, str]:
        try:
            found = await run_in_threadpool(
                application.notifications.mark_read, mine(client), notification_id
            )
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        if not found:
            raise HTTPException(404, "no such notification")
        return {"status": "read"}

    return router
