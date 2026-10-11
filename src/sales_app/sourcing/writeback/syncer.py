"""Bringing a source's statuses in line with Revansa's, one pass at a time.

`run_once()` reads the source and Revansa's own state, plans the writes that **tighten** a status
(see `planning`), holds back those a recent attempt makes unwise, and sends the rest. What to write is
recomputed on every pass from what the source holds now, so nothing depends on a previous pass having
finished: a pass interrupted anywhere is simply followed by another.

- **Off by default.** An empty policy sends nothing and asks nothing: not even a read.
- **Never loosens, never overwrites.** Every write names the status the source held when it was read;
  the source refuses it if a person changed it since (`SourceConflictError`), and nothing is written.
- **A refused batch is not a lost batch.** Writes go out in batches; if one is refused the same
  writes are tried one at a time, so one person's edit blocks only that prospect.
- **Failures are told apart.** A source that cannot be reached or asks to wait is tried again later
  and the pass stops (it is not hammered); credentials that are refused, a source that is read-only
  and anything unexpected stop the pass too, and are held back for a while. A conflict is not
  retried at once either: the person who edited it may be right.
- **A lost answer is recovered.** A write whose answer never came back is recorded as deferred; if
  the next pass finds the source already holding that status, it records it as applied and sends
  nothing again.
- **Dry run** plans and reports, and neither writes to the source nor records anything.
- **The registry is consulted only to propagate an opt-out, and only if it is healthy.** An unhealthy
  registry blocks everyone; that must never become "everyone refused" in a client's CRM.

Nothing here logs or prints, and the report, the ledger and the errors carry only counts, public ids
and statuses: no contact, no value read from the source other than its status, no free text. The
ledger is a journal: if it cannot be written after a write was sent, the next pass finds the status
already applied and nothing is sent twice.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType

from sales_app.sourcing.consent import ConsentWriter
from sales_app.sourcing.errors import (
    InvalidNativeIdError,
    SourceAuthError,
    SourceConflictError,
    SourceError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
    StoreError,
)
from sales_app.sourcing.identifiers import to_public_id
from sales_app.sourcing.ports import OperationalStore, ProspectSource
from sales_app.sourcing.writeback.ledger import (
    FailureCode,
    Outcome,
    WriteAttempt,
    WriteBackLedger,
)
from sales_app.sourcing.writeback.planning import PlannedWrite, SkipReason, plan_status_writes
from sales_app.sourcing.writeback.policy import WriteBackPolicy
from sales_core.contracts import ProspectId


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What one pass did, as counts. Never an id, a contact or a value."""

    planned: int = 0  # writes the plan asked for, after what is held back
    applied: int = 0
    conflicts: int = 0
    deferred: int = 0
    failed: int = 0
    held_back: int = 0  # kept for later because of a recent attempt
    recovered: int = 0  # writes whose answer was lost and whose result was found applied
    not_attempted: int = 0  # planned, but the pass stopped before they were sent
    skipped: Mapping[SkipReason, int] = field(default_factory=dict)
    dry_run: bool = False
    stopped: FailureCode | None = None  # why the pass ended early, if it did

    def __post_init__(self) -> None:
        object.__setattr__(self, "skipped", MappingProxyType(dict(self.skipped)))


class _Tally:
    def __init__(self) -> None:
        self.applied = self.conflicts = self.deferred = self.failed = self.attempted = 0


class WriteBackSyncer:
    def __init__(
        self,
        source: ProspectSource,
        store: OperationalStore,
        consent: ConsentWriter,
        ledger: WriteBackLedger,
        policy: WriteBackPolicy,
        *,
        clock: Callable[[], datetime],
        new_run_id: Callable[[], str] | None = None,
        dry_run: bool = False,
        batch_size: int = 50,
        cooldown: timedelta = timedelta(hours=1),
        backoff_base: timedelta = timedelta(minutes=5),
        backoff_cap: timedelta = timedelta(hours=1),
    ) -> None:
        if not source.client_id == store.client_id == ledger.client_id:
            raise ValueError("the source, the store and the ledger belong to different clients")
        if batch_size < 1:
            raise ValueError("a batch holds at least one write")
        if min(cooldown, backoff_base, backoff_cap) <= timedelta(0):
            raise ValueError("delays must be positive")
        policy.validate_for(source.capabilities)  # a policy the source cannot honour fails now
        self._source = source
        self._store = store
        self._consent = consent
        self._ledger = ledger
        self._policy = policy
        self._clock = clock
        self._new_run_id = new_run_id or (lambda: uuid.uuid4().hex[:12])
        self._dry_run = dry_run
        self._batch_size = batch_size
        self._cooldown = cooldown
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._namespace = source.capabilities.id_namespace

    def __repr__(self) -> str:
        return f"WriteBackSyncer(client={self._ledger.client_id!r}, dry_run={self._dry_run})"

    def run_once(self) -> SyncReport:
        if not self._policy.enabled:
            return SyncReport(dry_run=self._dry_run)
        try:
            records = list(self._source.list_prospects())
            states = self._store.list_states()
        except SourceError as error:
            return SyncReport(dry_run=self._dry_run, stopped=_code_of(error))
        except StoreError:
            return SyncReport(dry_run=self._dry_run, stopped=FailureCode.UNAVAILABLE)

        run = self._new_run_id()
        recovered = 0 if self._dry_run else self._confirm_recovered(run, records)

        if self._policy.propagate_opt_out:
            snapshot = self._consent.snapshot()
            healthy, is_blocked = snapshot.healthy, snapshot.blocks
        else:  # the registry is not read at all
            healthy, is_blocked = False, _nobody
        plan = plan_status_writes(
            records,
            states,
            capabilities=self._source.capabilities,
            policy=self._policy,
            is_blocked=is_blocked,
            registry_healthy=healthy,
        )

        now = self._clock()
        due = [(i, w) for i, w in enumerate(plan.writes) if not self._held_back(w, now)]
        held = len(plan.writes) - len(due)
        if self._dry_run:
            return SyncReport(planned=len(due), held_back=held, skipped=plan.skipped, dry_run=True)

        tally = _Tally()
        stopped = None
        for start in range(0, len(due), self._batch_size):
            stopped = self._send(run, due[start : start + self._batch_size], tally)
            if stopped is not None:
                break
        return SyncReport(
            planned=len(due),
            applied=tally.applied,
            conflicts=tally.conflicts,
            deferred=tally.deferred,
            failed=tally.failed,
            held_back=held,
            recovered=recovered,
            not_attempted=len(due) - tally.attempted,
            skipped=plan.skipped,
            stopped=stopped,
        )

    # ---- holding back ---------------------------------------------------------------------

    def _held_back(self, write: PlannedWrite, now: datetime) -> bool:
        """Whether a recent attempt on this prospect makes another one unwise for now."""
        public_id = ProspectId(write.public_id)
        latest = self._ledger.latest(public_id)
        if latest is None:
            return False
        if latest.outcome is Outcome.DEFERRED:
            if latest.not_before is not None:
                return now < latest.not_before
            streak = 0
            for entry in reversed(self._ledger.entries(public_id)):
                if entry.outcome is not Outcome.DEFERRED:
                    break
                streak += 1
            delay = min(self._backoff_base * 2 ** (streak - 1), self._backoff_cap)
            return now < latest.at + delay
        # applied (and a person undid it), conflicted or failed: not the same write again at once
        return latest.new is write.new and now < latest.at + self._cooldown

    # ---- sending --------------------------------------------------------------------------

    def _send(
        self, run: str, batch: Sequence[tuple[int, PlannedWrite]], tally: _Tally
    ) -> FailureCode | None:
        """Send a batch. Returns why the pass must stop, or None."""
        try:
            self._source.update_prospects([write.to_change() for _, write in batch])
        except (SourceConflictError, KeyError) as error:
            if len(batch) == 1:
                is_conflict = isinstance(error, SourceConflictError)
                self._record(
                    run,
                    batch,
                    tally,
                    Outcome.CONFLICT if is_conflict else Outcome.FAILED,
                    None if is_conflict else FailureCode.UNEXPECTED,
                )
                return None
            for item in batch:  # a refused batch writes nothing: one at a time, then
                stopped = self._send(run, [item], tally)
                if stopped is not None:
                    return stopped
            return None
        except SourceError as error:
            code = _code_of(error)
            outcome = (
                Outcome.DEFERRED
                if code in (FailureCode.UNAVAILABLE, FailureCode.RATE_LIMITED)
                else Outcome.FAILED
            )
            self._record(run, batch, tally, outcome, code, not_before=self._wait_of(error))
            return code
        self._record(run, batch, tally, Outcome.APPLIED, None)
        return None

    def _wait_of(self, error: SourceError) -> datetime | None:
        if isinstance(error, SourceRateLimitedError) and error.retry_after:
            return self._clock() + timedelta(seconds=error.retry_after)
        return None

    def _record(
        self,
        run: str,
        batch: Sequence[tuple[int, PlannedWrite]],
        tally: _Tally,
        outcome: Outcome,
        code: FailureCode | None,
        *,
        not_before: datetime | None = None,
    ) -> None:
        now = self._clock()
        self._ledger.append(
            [
                WriteAttempt(
                    client_id=self._ledger.client_id,
                    attempt_id=f"{run}-{index}",  # a run and a counter: never from a person's data
                    prospect_id=ProspectId(write.public_id),
                    new=write.new,
                    expected=write.expected,
                    outcome=outcome,
                    at=now,
                    code=code,
                    not_before=not_before,
                )
                for index, write in batch
            ]
        )
        tally.attempted += len(batch)
        match outcome:
            case Outcome.APPLIED:
                tally.applied += len(batch)
            case Outcome.CONFLICT:
                tally.conflicts += len(batch)
            case Outcome.DEFERRED:
                tally.deferred += len(batch)
            case _:
                tally.failed += len(batch)

    # ---- recovering a lost answer -----------------------------------------------------------

    def _confirm_recovered(self, run: str, records: Sequence) -> int:
        """A write recorded as deferred whose status the source now holds did happen: say so."""
        found = []
        for index, record in enumerate(records):
            try:
                public = ProspectId(to_public_id(self._namespace, record.prospect_id))
            except InvalidNativeIdError:
                continue
            latest = self._ledger.latest(public)
            if (
                latest is not None
                and latest.outcome is Outcome.DEFERRED
                and record.status is latest.new
            ):
                found.append(
                    WriteAttempt(
                        client_id=self._ledger.client_id,
                        attempt_id=f"{run}-r{index}",
                        prospect_id=public,
                        new=latest.new,
                        expected=latest.expected,
                        outcome=Outcome.APPLIED,
                        at=self._clock(),
                    )
                )
        if found:
            self._ledger.append(found)
        return len(found)


def _nobody(contact) -> bool:
    return False


def _code_of(error: Exception) -> FailureCode:
    if isinstance(error, SourceUnavailableError):
        return FailureCode.UNAVAILABLE
    if isinstance(error, SourceRateLimitedError):
        return FailureCode.RATE_LIMITED
    if isinstance(error, SourceAuthError):
        return FailureCode.AUTH
    if isinstance(error, SourceReadOnlyError):
        return FailureCode.READ_ONLY
    return FailureCode.UNEXPECTED
