"""Automatic, idempotent invoicing, one invoice per billing cycle (spec 13.1).

The cycles start at the client's current plan assignment (`sales_app.billing`): an invoice dated the
assignment day, then one on the same day of every following month (day clamped to the month's last
day), always at the plan's price. `AutoBilling.run(now)` issues every cycle that is due (cycle date
<= today) and not invoiced yet, for every client that has a current plan; `issue_for_client` does it
for one client and is called right after a plan assignment, so the first invoice exists at once. It
keeps no state of its own: *what is missing* is recomputed from the assignment and the invoices on
every run, so a process that was down for days catches up on its next run, and running it twice, or
from two processes, can never create a second invoice (the store refuses one: unique key on client
and cycle date, plus the lease below).

- **What an invoice contains** is `sales_app.billing.build_invoice`: one line, the plan at its price.
  No prorata exists. The tax is not decided (`docs/open-decisions.md`).
- **Only the current assignment is billed.** A client with no plan, or whose plan was withdrawn or
  replaced, is not billed for the time after (a replaced plan's cycles stop; the new assignment is
  a new anchor).
- **Periodicity.** Only plans billed "monthly" are invoiced. A client on an "annual" plan is
  recognised and left out of the run, reported as skipped, until its terms are decided.
- **Errors.** A client that cannot be invoiced (an unreadable store, anything unexpected) opens a
  `BILLING_ERROR` notification for the Admin (once per client and cycle while it is open, closed again
  when the invoice is finally made) and is audited. One client's error never stops the others.
- **Single run.** `BillingLoop` runs `AutoBilling` periodically behind a database lease
  (`platform` / `billing`), so several processes do not run it at once.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sales_app.audit_log import (
    OUTCOME_ERROR,
    OUTCOME_REJECTED,
    OUTCOME_SUCCESS,
    AuditService,
)
from sales_app.billing import Invoice, InvoiceFilter, InvoiceStore, build_invoice, cycle_dates
from sales_app.notifications import ADMIN, BILLING_ERROR, NotificationService
from sales_app.plans import PlanAssignment, PlanStore
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

ACTOR = "system:billing"
ROLE = "system"
LEASE_CLIENT, LEASE_JOB = "platform", "billing"
DEFAULT_EVERY_SECONDS = 3600.0
MONTHLY = "monthly"
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Skipped:
    client_id: str
    period: str | None
    reason: str


@dataclass(slots=True)
class BillingRunReport:
    created: int = 0
    already_invoiced: int = 0
    errors: int = 0
    skipped: list[Skipped] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)  # the invoices created by this run


def last_closed_periods(now: datetime, count: int) -> list[str]:
    """The `count` most recent months that have ended at `now`, oldest first."""
    year, month = now.year, now.month
    out: list[str] = []
    for _ in range(max(count, 0)):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append(f"{year:04d}-{month:02d}")
    return out[::-1]


def recent_months(now: datetime, count: int) -> list[str]:
    """The `count` latest months up to and including the current one, oldest first."""
    year, month = now.year, now.month
    out: list[str] = []
    for _ in range(max(count, 0)):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out[::-1]


class AutoBilling:
    def __init__(
        self,
        plans: PlanStore,
        invoices: InvoiceStore,
        notifications: NotificationService,
        audit: AuditService,
    ) -> None:
        self._plans = plans
        self._invoices = invoices
        self._notifications = notifications
        self._audit = audit

    def __repr__(self) -> str:
        return "AutoBilling()"

    def run(self, now: datetime, *, created_by: str = ACTOR) -> BillingRunReport:
        """Issues every due cycle of every client on a plan."""
        report = BillingRunReport()
        try:
            horizon = now + timedelta(days=1)
            current = [
                a
                for a in self._plans.assignments_between(_EPOCH, horizon)
                if a.ended_at is None
            ]
            for assignment in sorted(current, key=lambda a: a.client_id):
                self._client(assignment, now, report, created_by)
        except StoreUnavailableError:
            self._store_failed(report)
        else:
            self._notifications.resolve(ADMIN, None, "billing:store")
        return report

    def issue_for_client(
        self, client_id: str, now: datetime, *, created_by: str = ACTOR
    ) -> BillingRunReport:
        """Issues the due cycles of one client now (called right after a plan assignment). Never
        raises: a failure is reported, audited and notified, and the periodic run retries."""
        report = BillingRunReport()
        try:
            assignment = self._plans.current_assignment(client_id)
            if assignment is not None:
                self._client(assignment, now, report, created_by)
        except StoreUnavailableError:
            self._store_failed(report)
        return report

    def _store_failed(self, report: BillingRunReport) -> None:
        report.errors += 1
        self._notifications.notify(
            BILLING_ERROR,
            ADMIN,
            None,
            message="Automatic invoicing could not read or write the database.",
            dedupe_key="billing:store",
        )
        self._audit.record(
            actor=ACTOR,
            actor_role=ROLE,
            action="billing.auto_run",
            target="store",
            outcome=OUTCOME_ERROR,
        )

    def _client(
        self, assignment: PlanAssignment, now: datetime, report: BillingRunReport, created_by: str
    ) -> None:
        plan = self._plans.get_plan(assignment.plan_id)
        due = cycle_dates(assignment.started_at, assignment.ended_at, now)
        if not due:
            return
        if plan is not None and plan.billing_period != MONTHLY:
            report.skipped.append(
                Skipped(
                    assignment.client_id,
                    due[0].isoformat(),
                    "annual plan: not invoiced automatically yet",
                )
            )
            return
        covered = self._legacy_months_covered(assignment.client_id)
        for cycle in due:
            if covered is not None and cycle.isoformat()[:7] <= covered:
                continue  # that month already has a calendar-month invoice (the former rule)
            self._one(assignment, cycle, now, report, created_by)

    def _legacy_months_covered(self, client_id: str) -> str | None:
        """The latest `YYYY-MM` of the client's invoices issued under the former calendar-month rule
        (their `period` has no day): cycles up to that month are not invoiced a second time."""
        legacy = [
            i.period
            for i in self._invoices.list_invoices(InvoiceFilter(client_id=client_id))
            if len(i.period) == 7
        ]
        return max(legacy) if legacy else None

    def _one(
        self,
        assignment: PlanAssignment,
        cycle: date,
        now: datetime,
        report: BillingRunReport,
        created_by: str,
    ) -> None:
        client_id, period = assignment.client_id, cycle.isoformat()
        key = f"billing:{client_id}:{period}"
        try:
            kept = self._invoices.create(
                build_invoice(assignment, cycle, now=now, created_by=created_by)
            )
        except StoreUnavailableError:
            raise
        except Exception as error:  # noqa: BLE001 - one client's problem never stops the others
            log.error("automatic invoicing failed for a client (%s)", type(error).__name__)
            self._failed(client_id, period, key, type(error).__name__, report)
            return
        if kept is None:
            report.already_invoiced += 1
            return
        report.created += 1
        report.invoices.append(kept)
        self._notifications.resolve(ADMIN, client_id, key)
        self._audit.record(
            actor=created_by,
            actor_role=ROLE,
            action="billing.invoice.auto_generated",
            target=kept.number,
            client_id=client_id,
            outcome=OUTCOME_SUCCESS,
            after={"period": period, "currency": kept.currency, "amount": format(kept.amount, "f")},
        )

    def _failed(
        self, client_id: str, period: str, key: str, why: str, report: BillingRunReport
    ) -> None:
        report.errors += 1
        report.skipped.append(Skipped(client_id, period, why))
        self._notifications.notify(
            BILLING_ERROR,
            ADMIN,
            client_id,
            message=f"The invoice of {period} could not be generated: {why}.",
            dedupe_key=key,
        )
        self._audit.record(
            actor=ACTOR,
            actor_role=ROLE,
            action="billing.invoice.auto_generated",
            target=f"INV-{period}-{client_id}",
            client_id=client_id,
            outcome=OUTCOME_REJECTED,
            after={"period": period, "reason": why},
        )


class Leases(Protocol):
    def try_acquire(self, client_id: str, job: str, now: datetime) -> bool: ...

    def release(self, client_id: str, job: str) -> None: ...


class BillingLoop:
    """Runs `AutoBilling` every `every_seconds` while the application lives, under a lease."""

    def __init__(
        self,
        billing: AutoBilling,
        now: Callable[[], datetime],
        *,
        leases: Leases | None = None,
        every_seconds: float = DEFAULT_EVERY_SECONDS,
    ) -> None:
        self._billing = billing
        self._now = now
        self._leases = leases
        self._every = every_seconds

    def run_once(self) -> BillingRunReport | None:
        """One pass. None when another process holds the lease (nothing was done here)."""
        moment = self._now()
        if self._leases is not None and not self._leases.try_acquire(
            LEASE_CLIENT, LEASE_JOB, moment
        ):
            return None
        try:
            return self._billing.run(moment)
        finally:
            if self._leases is not None:
                self._leases.release(LEASE_CLIENT, LEASE_JOB)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                log.error("automatic invoicing pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._every)
            except TimeoutError:
                pass
