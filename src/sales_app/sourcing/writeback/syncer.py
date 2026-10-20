"""Bringing a source in line with what Revansa decided and did, one pass at a time.

`run_once()` reads the source and Revansa's own state, plans the writes that **tighten** a status
(see `planning`), holds back those a recent attempt makes unwise, and sends the rest. What to write is
recomputed on every pass from what the source holds now, so nothing depends on a previous pass having
finished: a pass interrupted anywhere is simply followed by another.

- **Off by default.** An empty policy sends nothing and asks nothing: not even a read.
- **Never loosens, never overwrites.** Every write names the value the source held when it was read;
  the source refuses it if a person changed it since (`SourceConflictError`), and nothing is written.
  What may be written, field by field, is decided in `planning`.
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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

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
from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.identifiers import to_public_id
from sales_app.sourcing.ports import (
    OperationalStore,
    ProspectChange,
    ProspectSource,
    WriteBackReadable,
)
from sales_app.sourcing.writeback.ledger import (
    FailureCode,
    Outcome,
    WriteAttempt,
    WriteBackLedger,
)
from sales_app.sourcing.writeback.planning import (
    PlannedWrite,
    SkipReason,
    plan_field_writes,
    value_text,
)
from sales_app.sourcing.writeback.policy import WriteBackPolicy, WriteBackPolicyError
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
        if policy.fields - {ProspectField.STATUS} and not isinstance(source, WriteBackReadable):
            raise WriteBackPolicyError("the source cannot say what its cells hold")
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

    def run_once(self, *, retry_held: bool = False) -> SyncReport:
        """One pass. `retry_held` (an Admin resuming failed synchronisations) lifts the waiting that
        follows a failure that waiting cures (`deferred`) or does not (`failed`): those writes are
        tried again now. What a person overruled (a conflict) or undid stays held back, and nothing
        is ever written twice: every write is recomputed from what the source holds now and names the
        value it expects to replace."""
        if not self._policy.enabled:
            return SyncReport(dry_run=self._dry_run)
        try:
            records = list(self._source.list_prospects())
            values = self._read_values()
            states = self._store.list_states()
        except SourceError as error:
            return SyncReport(dry_run=self._dry_run, stopped=_code_of(error))
        except StoreError:
            return SyncReport(dry_run=self._dry_run, stopped=FailureCode.UNAVAILABLE)

        run = self._new_run_id()
        recovered = 0 if self._dry_run else self._confirm_recovered(run, records, values)

        if self._policy.propagate_opt_out:
            snapshot = self._consent.snapshot()
            healthy, is_blocked = snapshot.healthy, snapshot.blocks
        else:  # the registry is not read at all
            healthy, is_blocked = False, _nobody
        plan = plan_field_writes(
            records,
            states,
            values,
            capabilities=self._source.capabilities,
            policy=self._policy,
            is_blocked=is_blocked,
            registry_healthy=healthy,
            last_written=self._last_written,
        )

        now = self._clock()
        due = [(i, w) for i, w in enumerate(plan.writes) if not self._held_back(w, now, retry_held)]
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

    # ---- reading --------------------------------------------------------------------------

    def _read_values(self) -> Mapping[str, Mapping[ProspectField, Any]]:
        """What the source holds in the fields other than the status; nothing is asked if the
        policy does not allow any."""
        if not self._policy.fields - {ProspectField.STATUS}:
            return {}
        assert isinstance(self._source, WriteBackReadable)  # checked at construction
        return self._source.read_writeback_values()

    def _last_written(self, public_id: str, field: ProspectField) -> str | None:
        """What Revansa last wrote (and the source accepted) in this field of this prospect."""
        for entry in reversed(self._ledger.entries(ProspectId(public_id))):
            if entry.field is field and entry.outcome is Outcome.APPLIED:
                return str(entry.new)
        return None

    # ---- holding back ---------------------------------------------------------------------

    def _held_back(self, write: PlannedWrite, now: datetime, retry_held: bool = False) -> bool:
        """Whether a recent attempt on this field of this prospect makes another one unwise."""
        public_id = ProspectId(write.public_id)
        latest = self._ledger.latest(public_id, write.field)
        if latest is None:
            return False
        if retry_held and latest.outcome in (Outcome.DEFERRED, Outcome.FAILED):
            return False
        if latest.outcome is Outcome.DEFERRED:
            if latest.not_before is not None:
                return now < latest.not_before
            streak = 0
            for entry in reversed(self._ledger.entries(public_id)):
                if entry.field is not write.field:
                    continue
                if entry.outcome is not Outcome.DEFERRED:
                    break
                streak += 1
            delay = min(self._backoff_base * 2 ** (streak - 1), self._backoff_cap)
            return now < latest.at + delay
        # applied (and a person undid it), conflicted or failed: not the same write again at once
        return latest.new == write.ledger_new and now < latest.at + self._cooldown

    # ---- sending --------------------------------------------------------------------------

    def _send(
        self, run: str, batch: Sequence[tuple[int, PlannedWrite]], tally: _Tally
    ) -> FailureCode | None:
        """Send a batch. Returns why the pass must stop, or None."""
        try:
            self._source.update_prospects(_changes(write for _, write in batch))
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
                    new=write.ledger_new,
                    expected=write.ledger_expected,
                    outcome=outcome,
                    at=now,
                    field=write.field,
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

    def _confirm_recovered(
        self, run: str, records: Sequence, values: Mapping[str, Mapping[ProspectField, Any]]
    ) -> int:
        """A write recorded as deferred whose value the source now holds did happen: say so."""
        found = []
        for index, record in enumerate(records):
            try:
                public = ProspectId(to_public_id(self._namespace, record.prospect_id))
            except InvalidNativeIdError:
                continue
            held = values.get(record.prospect_id, {})
            for each in self._policy.fields:
                latest = self._ledger.latest(public, each)
                if latest is None or latest.outcome is not Outcome.DEFERRED:
                    continue
                if each is ProspectField.STATUS:
                    current = record.status
                elif each in held:
                    current = value_text(each, held[each])
                else:
                    continue
                if current == latest.new:
                    found.append(
                        WriteAttempt(
                            client_id=self._ledger.client_id,
                            attempt_id=f"{run}-r{index}-{each.value}",
                            prospect_id=public,
                            new=latest.new,
                            expected=latest.expected,
                            outcome=Outcome.APPLIED,
                            at=self._clock(),
                            field=each,
                        )
                    )
        if found:
            self._ledger.append(found)
        return len(found)


def _changes(writes: Iterable[PlannedWrite]) -> list[ProspectChange]:
    """One change per prospect, however many of its fields are written in this batch."""
    merged: dict[str, tuple[dict, dict]] = {}
    for write in writes:
        new, expected = merged.setdefault(write.prospect_id, ({}, {}))
        new[write.field] = write.new
        expected[write.field] = write.expected
    return [ProspectChange(prospect_id=p, new=n, expected=e) for p, (n, e) in merged.items()]


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
