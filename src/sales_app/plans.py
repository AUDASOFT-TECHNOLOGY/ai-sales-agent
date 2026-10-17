"""Commercial plans and which plan each client is on.

A plan has a name, a monthly price in one currency, the credits it includes and a maximum number of
actions per day. Plans are Revansa's own (global); a client is on at most one plan at a time.

**Prepared for billing, not billing.** Every assignment is a row that is never rewritten: it keeps a
snapshot of the plan's terms as they were when the client was put on it (price, currency, credits,
quota) and the instant it started and ended. Editing a plan afterwards does not change what an
earlier assignment recorded, so a future invoice can say which terms applied, and when. The client's
*current* assignment is the one with no end. Nothing here charges, invoices or enforces a quota.

A plan is deactivated, never deleted: clients and past assignments refer to it. An inactive plan
cannot be given to a client, and clients already on it stay on it until the Admin moves them.
"""

import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

NAME_MAX = 80
MAX_PRICE = Decimal(1_000_000)
MAX_CREDITS = 1_000_000_000
MAX_DAILY_QUOTA = 100_000
_PLACES = Decimal("0.01")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SLUG = re.compile(r"[^a-z0-9]+")


class InvalidPlanError(ValueError):
    """A plan that may not be kept. The message is a fixed sentence, safe to show."""


class PlanNotFoundError(LookupError):
    """No such plan."""


class PlanInactiveError(ValueError):
    """The plan exists but is deactivated: it cannot be given to a client."""


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    name: str
    monthly_price: Decimal
    currency: str
    included_credits: int
    daily_quota: int
    active: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True, slots=True)
class PlanAssignment:
    """A client on a plan, with the terms as they were when it started. `ended_at` None: current."""

    client_id: str
    plan_id: str
    plan_name: str
    monthly_price: Decimal
    currency: str
    included_credits: int
    daily_quota: int
    started_at: datetime
    assigned_by: str
    ended_at: datetime | None = None


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-")[:48]


def clean_terms(
    name: str, monthly_price: object, currency: str, included_credits: int, daily_quota: int
) -> tuple[str, Decimal, str, int, int]:
    """The terms of a plan, checked and normalised, or `InvalidPlanError`."""
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > NAME_MAX:
        raise InvalidPlanError(f"the plan name must be 1 to {NAME_MAX} characters")
    if not slugify(cleaned):
        raise InvalidPlanError("the plan name must contain letters or digits")
    code = currency.strip().upper()
    if not _CURRENCY.match(code):
        raise InvalidPlanError("the currency must be a 3-letter code such as USD")
    if isinstance(monthly_price, bool):
        raise InvalidPlanError("the monthly price must be a number")
    try:
        price = Decimal(str(monthly_price))
    except (InvalidOperation, ValueError):
        raise InvalidPlanError("the monthly price must be a number") from None
    if not price.is_finite() or price < 0 or price > MAX_PRICE:
        raise InvalidPlanError(f"the monthly price must be between 0 and {MAX_PRICE}")
    if not 0 <= included_credits <= MAX_CREDITS:
        raise InvalidPlanError(f"the included credits must be between 0 and {MAX_CREDITS}")
    if not 1 <= daily_quota <= MAX_DAILY_QUOTA:
        raise InvalidPlanError(f"the daily quota must be between 1 and {MAX_DAILY_QUOTA}")
    return cleaned, price.quantize(_PLACES), code, included_credits, daily_quota


def snapshot(plan: Plan, client_id: str, *, at: datetime, by: str) -> PlanAssignment:
    return PlanAssignment(
        client_id=client_id,
        plan_id=plan.plan_id,
        plan_name=plan.name,
        monthly_price=plan.monthly_price,
        currency=plan.currency,
        included_credits=plan.included_credits,
        daily_quota=plan.daily_quota,
        started_at=at,
        assigned_by=by,
    )


class PlanStore(Protocol):
    def list_plans(self) -> list[Plan]:
        """Every plan, active or not, by name."""
        ...

    def get_plan(self, plan_id: str) -> Plan | None: ...

    def create_plan(self, plan: Plan) -> None:
        """`InvalidPlanError` if the id or the name is taken (names are unique, ignoring case)."""
        ...

    def update_plan(self, plan: Plan) -> None:
        """Replaces the plan's terms, name and `active`. `PlanNotFoundError`; `InvalidPlanError` if
        the new name is taken. Assignments already made keep the terms they recorded."""
        ...

    def current_assignment(self, client_id: str) -> PlanAssignment | None: ...

    def assignment_history(self, client_id: str) -> list[PlanAssignment]:
        """This client's assignments, newest first."""
        ...

    def assign(
        self, client_id: str, plan_id: str | None, *, at: datetime, by: str
    ) -> PlanAssignment | None:
        """Puts the client on the plan (ending its current assignment, if any); None takes it off
        any plan. Putting a client on the plan it is already on changes nothing. Returns the
        current assignment afterwards. `PlanNotFoundError`, `PlanInactiveError`."""
        ...

    def clients_on(self, plan_id: str) -> list[str]:
        """The clients whose current assignment is this plan."""
        ...

    def assignments_between(self, start: datetime, end: datetime) -> list[PlanAssignment]:
        """Every client's assignments in force at some instant of [start, end), by client then
        start. Billing reads this; it is the only read that spans clients."""
        ...


class InMemoryPlanStore:
    """For tests: nothing survives the process."""

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self._assignments: list[PlanAssignment] = []
        self._lock = threading.Lock()

    def list_plans(self) -> list[Plan]:
        with self._lock:
            return sorted(self._plans.values(), key=lambda p: (p.name.lower(), p.plan_id))

    def get_plan(self, plan_id: str) -> Plan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def _name_taken(self, plan: Plan) -> bool:
        return any(
            p.name.lower() == plan.name.lower() and p.plan_id != plan.plan_id
            for p in self._plans.values()
        )

    def create_plan(self, plan: Plan) -> None:
        with self._lock:
            if plan.plan_id in self._plans or self._name_taken(plan):
                raise InvalidPlanError("a plan with this name already exists")
            self._plans[plan.plan_id] = plan

    def update_plan(self, plan: Plan) -> None:
        with self._lock:
            if plan.plan_id not in self._plans:
                raise PlanNotFoundError(plan.plan_id)
            if self._name_taken(plan):
                raise InvalidPlanError("a plan with this name already exists")
            self._plans[plan.plan_id] = replace(
                plan, created_at=self._plans[plan.plan_id].created_at
            )

    def current_assignment(self, client_id: str) -> PlanAssignment | None:
        with self._lock:
            return self._current(client_id)

    def _current(self, client_id: str) -> PlanAssignment | None:
        return next(
            (a for a in self._assignments if a.client_id == client_id and a.ended_at is None), None
        )

    def assignment_history(self, client_id: str) -> list[PlanAssignment]:
        with self._lock:
            found = [a for a in self._assignments if a.client_id == client_id]
        return found[::-1]  # newest first, by order of assignment (as the SQL store's sequence)

    def assign(
        self, client_id: str, plan_id: str | None, *, at: datetime, by: str
    ) -> PlanAssignment | None:
        with self._lock:
            plan = None
            if plan_id is not None:
                plan = self._plans.get(plan_id)
                if plan is None:
                    raise PlanNotFoundError(plan_id)
            current = self._current(client_id)
            if plan is None and current is None:
                return None
            if plan is not None and current is not None and current.plan_id == plan.plan_id:
                return current
            if plan is not None and not plan.active:
                raise PlanInactiveError(plan.plan_id)
            if current is not None:
                index = self._assignments.index(current)
                self._assignments[index] = replace(current, ended_at=at)
            if plan is None:
                return None
            created = snapshot(plan, client_id, at=at, by=by)
            self._assignments.append(created)
            return created

    def clients_on(self, plan_id: str) -> list[str]:
        with self._lock:
            return sorted(
                a.client_id
                for a in self._assignments
                if a.plan_id == plan_id and a.ended_at is None
            )

    def assignments_between(self, start: datetime, end: datetime) -> list[PlanAssignment]:
        with self._lock:
            found = [
                a
                for a in self._assignments
                if a.started_at < end and (a.ended_at is None or a.ended_at > start)
            ]
        return sorted(found, key=lambda a: (a.client_id, a.started_at))
