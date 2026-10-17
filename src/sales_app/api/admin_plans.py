"""Admin API for commercial plans and the plan each client is on (see `sales_app.plans`).

Plans are Revansa's own: any admin may read them, only an owner creates, changes, deactivates or
assigns. A plan is deactivated (`active: false`), never deleted. How many clients are on a plan is
counted among the clients the asking admin can reach, so a scoped admin learns nothing about the
others. A client's plan is read and set through the same client access rule as every other route
that takes a `client_id` (404 for one the admin cannot reach).

Amounts travel as decimal strings, never floats. Nothing here charges or invoices anyone.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.bootstrap import Application
from sales_app.plans import (
    NAME_MAX,
    InvalidPlanError,
    Plan,
    PlanAssignment,
    PlanInactiveError,
    PlanNotFoundError,
    PlanStore,
    clean_terms,
    slugify,
)
from sales_app.sourcing.errors import StoreUnavailableError


class PlanBody(BaseModel):
    name: str = Field(max_length=NAME_MAX * 2)
    monthly_price: Decimal
    currency: str = Field(max_length=3)
    included_credits: int
    daily_quota: int
    active: bool = True


class PlanResponse(BaseModel):
    plan_id: str
    name: str
    monthly_price: str
    currency: str
    included_credits: int
    daily_quota: int
    active: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str
    client_count: int  # clients currently on it, among those the asking admin can reach


class PlansResponse(BaseModel):
    plans: list[PlanResponse]


class AssignmentResponse(BaseModel):
    """The terms as they were when the client was put on the plan, not as the plan is today."""

    plan_id: str
    plan_name: str
    monthly_price: str
    currency: str
    included_credits: int
    daily_quota: int
    started_at: datetime
    ended_at: datetime | None
    assigned_by: str


class ClientPlanResponse(BaseModel):
    client_id: str
    current: AssignmentResponse | None
    history: list[AssignmentResponse]  # newest first, the current one included


class AssignBody(BaseModel):
    plan_id: str | None  # null: take the client off any plan


def _assignment_response(a: PlanAssignment) -> AssignmentResponse:
    return AssignmentResponse(
        plan_id=a.plan_id,
        plan_name=a.plan_name,
        monthly_price=format(a.monthly_price, "f"),
        currency=a.currency,
        included_credits=a.included_credits,
        daily_quota=a.daily_quota,
        started_at=a.started_at,
        ended_at=a.ended_at,
        assigned_by=a.assigned_by,
    )


def create_admin_plans_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def store() -> PlanStore:
        if application.plans is None:
            raise HTTPException(503, "plans need the database (set SALES_AGENT_DATABASE_URL)")
        return application.plans

    def respond(plan: Plan, admin: AdminUser, plans: PlanStore) -> PlanResponse:
        reachable = [c for c in plans.clients_on(plan.plan_id) if admin.can_reach(c)]
        return PlanResponse(
            plan_id=plan.plan_id,
            name=plan.name,
            monthly_price=format(plan.monthly_price, "f"),
            currency=plan.currency,
            included_credits=plan.included_credits,
            daily_quota=plan.daily_quota,
            active=plan.active,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            updated_by=plan.updated_by,
            client_count=len(reachable),
        )

    def terms(body: PlanBody):
        try:
            return clean_terms(
                body.name,
                body.monthly_price,
                body.currency,
                body.included_credits,
                body.daily_quota,
            )
        except InvalidPlanError as error:
            raise HTTPException(422, str(error)) from None

    @router.get("/plans", response_model=PlansResponse)
    def list_plans(admin: AdminUser = Depends(get_current_admin)) -> PlansResponse:
        plans = store()
        try:
            return PlansResponse(plans=[respond(p, admin, plans) for p in plans.list_plans()])
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None

    @router.post("/plans", response_model=PlanResponse, status_code=201)
    async def create_plan(
        body: PlanBody, admin: AdminUser = Depends(require_owner)
    ) -> PlanResponse:
        name, price, currency, credits, quota = terms(body)
        plans = store()

        def create() -> Plan:
            base = slugify(name)
            plan_id, n = base, 1
            while plans.get_plan(plan_id) is not None:
                n += 1
                plan_id = f"{base}-{n}"
            now = application.clock.now()
            plan = Plan(
                plan_id,
                name,
                price,
                currency,
                credits,
                quota,
                body.active,
                now,
                now,
                admin.username,
            )
            plans.create_plan(plan)
            return plan

        try:
            plan = await run_in_threadpool(create)
        except InvalidPlanError as error:
            raise HTTPException(409, str(error)) from None
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return respond(plan, admin, plans)

    @router.get("/plans/{plan_id}", response_model=PlanResponse)
    def get_plan(plan_id: str, admin: AdminUser = Depends(get_current_admin)) -> PlanResponse:
        plans = store()
        try:
            plan = plans.get_plan(plan_id)
            if plan is None:
                raise HTTPException(404, "no such plan")
            return respond(plan, admin, plans)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None

    @router.put("/plans/{plan_id}", response_model=PlanResponse)
    async def update_plan(
        plan_id: str, body: PlanBody, admin: AdminUser = Depends(require_owner)
    ) -> PlanResponse:
        name, price, currency, credits, quota = terms(body)
        plans = store()

        def update() -> Plan:
            current = plans.get_plan(plan_id)
            if current is None:
                raise PlanNotFoundError(plan_id)
            plan = Plan(
                plan_id,
                name,
                price,
                currency,
                credits,
                quota,
                body.active,
                current.created_at,
                application.clock.now(),
                admin.username,
            )
            plans.update_plan(plan)
            return plan

        try:
            plan = await run_in_threadpool(update)
        except PlanNotFoundError:
            raise HTTPException(404, "no such plan") from None
        except InvalidPlanError as error:
            raise HTTPException(409, str(error)) from None
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return respond(plan, admin, plans)

    # ---- a client's plan ---------------------------------------------------------------------

    def existing_client(admin: AdminUser, client_id: str) -> None:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if client_wiring_and_document(application, client_id) is None:
            raise HTTPException(404, "no such client")

    def client_plan(client_id: str, plans: PlanStore) -> ClientPlanResponse:
        history = plans.assignment_history(client_id)
        current = next((a for a in history if a.ended_at is None), None)
        return ClientPlanResponse(
            client_id=client_id,
            current=_assignment_response(current) if current else None,
            history=[_assignment_response(a) for a in history],
        )

    @router.get("/clients/{client_id}/plan", response_model=ClientPlanResponse)
    def get_client_plan(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> ClientPlanResponse:
        existing_client(admin, client_id)
        plans = store()
        try:
            return client_plan(client_id, plans)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None

    @router.put("/clients/{client_id}/plan", response_model=ClientPlanResponse)
    async def set_client_plan(
        client_id: str, body: AssignBody, admin: AdminUser = Depends(require_owner)
    ) -> ClientPlanResponse:
        existing_client(admin, client_id)
        plans = store()

        def assign() -> ClientPlanResponse:
            plans.assign(client_id, body.plan_id, at=application.clock.now(), by=admin.username)
            return client_plan(client_id, plans)

        try:
            return await run_in_threadpool(assign)
        except PlanNotFoundError:
            raise HTTPException(404, "no such plan") from None
        except PlanInactiveError:
            raise HTTPException(409, "this plan is deactivated: choose an active plan") from None
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None

    return router
