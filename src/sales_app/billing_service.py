"""Automatic, idempotent monthly invoicing (spec 13.1).

`AutoBilling.run(now)` invoices every closed month that is due and not invoiced yet, for every client
that was on a plan: from the month of the client's first plan assignment (its sign-up for billing
purposes) up to the last month that has ended, looking back at most `lookback_months`. It keeps no
state of its own: *what is missing* is recomputed from the plan assignments and the invoices on every
run, so a process that was down for days simply catches up on its next run, and running it twice,
or from two processes, can never create a second invoice (the store refuses one: unique key on client
and period, plus the lease below).

- **What an invoice contains** is `sales_app.billing.build_invoice` with the `ProrationPolicy` in
  force. The prorata and the tax are *not decided* (`docs/open-decisions.md`): this module applies no
  commercial rule of its own.
- **Periodicity.** Only plans billed "monthly" are invoiced. A client on an "annual" plan is
  recognised and left out of the run, reported as skipped, until its terms are decided.
- **Errors.** A client or a period that cannot be invoiced (plans in different currencies, an
  unreadable store, anything unexpected) opens a `BILLING_ERROR` notification for the Admin (once per
  client and period while it is open, closed again when the invoice is finally made) and is audited.
  One client's error never stops the others. Notifications and audit carry the invoice number and
  fixed reasons, never a secret.
- **Single run.** `BillingLoop` runs `AutoBilling` periodically behind a database lease
  (`platform` / `billing`), so several processes do not run it at once.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sales_app.audit_log import (
    OUTCOME_ERROR,
    OUTCOME_REJECTED,
    OUTCOME_SUCCESS,
    AuditService,
)
from sales_app.billing import (
    DEFAULT_PRORATION,
    InvalidPeriodError,
    InvoiceStore,
    MixedCurrenciesError,
    ProrationPolicy,
    build_invoice,
    parse_period,
)
from sales_app.notifications import ADMIN, BILLING_ERROR, NotificationService
from sales_app.plans import PlanAssignment, PlanStore
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

ACTOR = "system:billing"
ROLE = "system"
LEASE_CLIENT, LEASE_JOB = "platform", "billing"
DEFAULT_LOOKBACK_MONTHS = 12
DEFAULT_EVERY_SECONDS = 3600.0
MONTHLY = "monthly"


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
    periods: tuple[str, ...] = ()


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


class AutoBilling:
    def __init__(
        self,
        plans: PlanStore,
        invoices: InvoiceStore,
        notifications: NotificationService,
        audit: AuditService,
        *,
        proration: ProrationPolicy = DEFAULT_PRORATION,
        lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    ) -> None:
        self._plans = plans
        self._invoices = invoices
        self._notifications = notifications
        self._audit = audit
        self._proration = proration
        self._lookback = lookback_months

    def __repr__(self) -> str:
        return "AutoBilling()"

    def run(self, now: datetime) -> BillingRunReport:
        report = BillingRunReport()
        try:
            self._run(now, report)
        except StoreUnavailableError:
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
        else:
            self._notifications.resolve(ADMIN, None, "billing:store")
        return report

    def _run(self, now: datetime, report: BillingRunReport) -> None:
        window = last_closed_periods(now, self._lookback)
        if not window:
            return
        report.periods = tuple(window)
        assignments = self._plans.assignments_between(
            parse_period(window[0])[0], parse_period(window[-1])[1]
        )
        annual: dict[str, bool] = {}

        def is_annual(plan_id: str) -> bool:
            if plan_id not in annual:
                plan = self._plans.get_plan(plan_id)
                annual[plan_id] = plan is not None and plan.billing_period != MONTHLY
            return annual[plan_id]

        by_client: dict[str, list[PlanAssignment]] = {}
        for a in assignments:
            by_client.setdefault(a.client_id, []).append(a)
        for client_id in sorted(by_client):
            mine = by_client[client_id]
            annual_terms = [a for a in mine if is_annual(a.plan_id)]
            monthly_terms = [a for a in mine if not is_annual(a.plan_id)]
            first = min(a.started_at for a in mine)
            for period in window:
                p_start, p_end = parse_period(period)
                if p_end <= first:
                    continue  # the client was not there yet
                if any(
                    a.started_at < p_end and (a.ended_at is None or a.ended_at > p_start)
                    for a in annual_terms
                ):
                    report.skipped.append(
                        Skipped(client_id, period, "annual plan: not invoiced automatically yet")
                    )
                    continue
                self._one(client_id, period, monthly_terms, now, report)

    def _one(
        self,
        client_id: str,
        period: str,
        assignments: list[PlanAssignment],
        now: datetime,
        report: BillingRunReport,
    ) -> None:
        key = f"billing:{client_id}:{period}"
        try:
            invoice = build_invoice(
                client_id,
                period,
                assignments,
                now=now,
                created_by=ACTOR,
                proration=self._proration,
            )
            if invoice is None:
                return
            kept = self._invoices.create(invoice)
        except StoreUnavailableError:
            raise
        except MixedCurrenciesError:
            self._failed(client_id, period, key, "plans in different currencies", report)
            return
        except InvalidPeriodError:  # a month not closed: nothing to do yet
            return
        except Exception as error:  # noqa: BLE001 - one client's problem never stops the others
            log.error("automatic invoicing failed for a client (%s)", type(error).__name__)
            self._failed(client_id, period, key, type(error).__name__, report)
            return
        if kept is None:
            report.already_invoiced += 1
            return
        report.created += 1
        self._notifications.resolve(ADMIN, client_id, key)
        self._audit.record(
            actor=ACTOR,
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
