"""Admin API: the platform's write-back switch (see `sales_app.writeback_switch`).

`GET /admin/v1/settings/writeback` tells any admin whether results are copied back into the clients'
Google Sheets / HubSpot now, and who decided (`admin` or the `deployment`). `PUT` is the owner's:
`{"enabled": true|false}` sets the decision, `{"enabled": null}` follows the deployment default again.
Turning it on writes nothing by itself: a source is written only when its client named the columns or
properties (Client → Sources → Results).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.secret_store import SecretStoreError


class WritebackResponse(BaseModel):
    enabled: bool  # results are copied back now (for sources whose client configured them)
    decided_by: Literal["admin", "deployment"]
    deployment_default: bool  # what SALES_AGENT_WRITEBACK_ENABLED gives
    needs_database: bool  # True: no database, so no source can be written back whatever this says
    note: str


class UpdateWritebackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool | None  # None: follow the deployment default


def create_admin_writeback_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def respond() -> WritebackResponse:
        switch = application.writeback_switch
        if switch is None:
            raise HTTPException(503, "the write-back switch needs the credential store")
        database = application.send_caps is not None  # present exactly when there is a database
        enabled = switch.enabled()
        return WritebackResponse(
            enabled=enabled,
            decided_by=switch.source(),
            deployment_default=switch.deployment_default,
            needs_database=not database,
            note=(
                "Results are copied into the sources whose client named columns or properties."
                if enabled
                else "Nothing is copied back to any client's source, whatever the clients chose."
            ),
        )

    @router.get("/settings/writeback", response_model=WritebackResponse)
    def get_writeback(_admin: AdminUser = Depends(get_current_admin)) -> WritebackResponse:
        return respond()

    @router.put("/settings/writeback", response_model=WritebackResponse)
    def put_writeback(
        body: UpdateWritebackRequest, admin: AdminUser = Depends(require_owner)
    ) -> WritebackResponse:
        switch = application.writeback_switch
        if switch is None:
            raise HTTPException(503, "the write-back switch needs the credential store")
        try:
            switch.set(body.enabled, actor=f"admin:{admin.username}")
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return respond()

    return router
