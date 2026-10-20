"""The SQL half of the commercial plans (`sales_app.plans`).

Plans are global; an assignment belongs to one client and every statement about it filters on that
client. Assigning is one transaction (the client is registered first, so the foreign key holds): it
ends the current assignment and starts the new one, and the database itself refuses a second current
assignment for a client, so two racing requests cannot both win. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence.
"""

from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_plan_assignments as assignments
from sales_app.persistence.tables import plans
from sales_app.plans import (
    InvalidPlanError,
    Plan,
    PlanAssignment,
    PlanInactiveError,
    PlanNotFoundError,
    snapshot,
)
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the plans are unavailable"
_TAKEN = "a plan with this name already exists"


def _plan(row) -> Plan:
    return Plan(
        plan_id=row.plan_id,
        name=row.name,
        monthly_price=row.monthly_price,
        currency=row.currency,
        included_credits=row.included_credits,
        daily_quota=row.daily_quota,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        billing_period=row.billing_period or "monthly",
    )


def _assignment(row) -> PlanAssignment:
    return PlanAssignment(
        client_id=row.client_id,
        plan_id=row.plan_id,
        plan_name=row.plan_name,
        monthly_price=row.monthly_price,
        currency=row.currency,
        included_credits=row.included_credits,
        daily_quota=row.daily_quota,
        started_at=row.started_at,
        ended_at=row.ended_at,
        assigned_by=row.assigned_by,
    )


class SqlPlanStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- plans -----------------------------------------------------------------------------

    def list_plans(self) -> list[Plan]:
        query = select(plans).order_by(func.lower(plans.c.name), plans.c.plan_id)
        try:
            with self._engine.connect() as connection:
                return [_plan(r) for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def get_plan(self, plan_id: str) -> Plan | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(select(plans).where(plans.c.plan_id == plan_id)).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return None if row is None else _plan(row)

    def create_plan(self, plan: Plan) -> None:
        values = _values(plan)
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(plans).values(plan_id=plan.plan_id, created_at=plan.created_at, **values)
                )
        except IntegrityError:
            raise InvalidPlanError(_TAKEN) from None
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def update_plan(self, plan: Plan) -> None:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(plans).where(plans.c.plan_id == plan.plan_id).values(**_values(plan))
                )
                if result.rowcount == 0:
                    raise PlanNotFoundError(plan.plan_id)
        except IntegrityError:
            raise InvalidPlanError(_TAKEN) from None
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    # ---- assignments -----------------------------------------------------------------------

    def current_assignment(self, client_id: str) -> PlanAssignment | None:
        try:
            with self._engine.connect() as connection:
                return _current(connection, client_id)
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def assignment_history(self, client_id: str) -> list[PlanAssignment]:
        query = (
            select(assignments)
            .where(assignments.c.client_id == client_id)
            .order_by(assignments.c.seq.desc())
        )
        try:
            with self._engine.connect() as connection:
                return [_assignment(r) for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def assign(
        self, client_id: str, plan_id: str | None, *, at: datetime, by: str
    ) -> PlanAssignment | None:
        for attempt in range(2):
            try:
                return self._assign_once(client_id, plan_id, at=at, by=by)
            except IntegrityError:  # another request became current first: look again, once
                if attempt:
                    raise StoreUnavailableError(_UNAVAILABLE) from None
            except SQLAlchemyError:
                raise StoreUnavailableError(_UNAVAILABLE) from None
        raise StoreUnavailableError(_UNAVAILABLE)  # pragma: no cover

    def _assign_once(
        self, client_id: str, plan_id: str | None, *, at: datetime, by: str
    ) -> PlanAssignment | None:
        with self._engine.begin() as connection:
            plan = None
            if plan_id is not None:
                row = connection.execute(select(plans).where(plans.c.plan_id == plan_id)).first()
                if row is None:
                    raise PlanNotFoundError(plan_id)
                plan = _plan(row)
            ensure_client(connection, client_id, created_at=datetime.now(UTC), created_by=None)
            current = _current(connection, client_id)
            if plan is None and current is None:
                return None
            if plan is not None and current is not None and current.plan_id == plan.plan_id:
                return current
            if plan is not None and not plan.active:
                raise PlanInactiveError(plan.plan_id)
            if current is not None:
                connection.execute(
                    update(assignments)
                    .where(assignments.c.client_id == client_id, assignments.c.ended_at.is_(None))
                    .values(ended_at=at)
                )
            if plan is None:
                return None
            created = snapshot(plan, client_id, at=at, by=by)
            connection.execute(
                insert(assignments).values(
                    client_id=client_id,
                    plan_id=created.plan_id,
                    plan_name=created.plan_name,
                    monthly_price=created.monthly_price,
                    currency=created.currency,
                    included_credits=created.included_credits,
                    daily_quota=created.daily_quota,
                    started_at=created.started_at,
                    assigned_by=created.assigned_by,
                )
            )
            return created

    def assignments_between(self, start: datetime, end: datetime) -> list[PlanAssignment]:
        query = (
            select(assignments)
            .where(
                assignments.c.started_at < end,
                or_(assignments.c.ended_at.is_(None), assignments.c.ended_at > start),
            )
            .order_by(assignments.c.client_id, assignments.c.seq)
        )
        try:
            with self._engine.connect() as connection:
                return [_assignment(r) for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def clients_on(self, plan_id: str) -> list[str]:
        query = (
            select(assignments.c.client_id)
            .where(assignments.c.plan_id == plan_id, assignments.c.ended_at.is_(None))
            .order_by(assignments.c.client_id)
        )
        try:
            with self._engine.connect() as connection:
                return [r.client_id for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None


def _values(plan: Plan) -> dict[str, object]:
    return {
        "name": plan.name,
        "name_key": plan.name.lower(),
        "monthly_price": plan.monthly_price,
        "currency": plan.currency,
        "included_credits": plan.included_credits,
        "daily_quota": plan.daily_quota,
        "active": plan.active,
        "updated_at": plan.updated_at,
        "updated_by": plan.updated_by,
        "billing_period": plan.billing_period,
    }


def _current(connection: Connection, client_id: str) -> PlanAssignment | None:
    row = connection.execute(
        select(assignments).where(
            assignments.c.client_id == client_id, assignments.c.ended_at.is_(None)
        )
    ).first()
    return None if row is None else _assignment(row)
