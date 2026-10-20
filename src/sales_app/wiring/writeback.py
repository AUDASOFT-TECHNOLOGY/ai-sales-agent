"""Writing a client's results back into its spreadsheets: the one door between the runtime and the
write-back package.

Only this module imports `sales_app.sourcing.writeback` (an architecture test keeps that list). The
runtime and the scheduler see a `ClientWriteback` that runs and answers with counters.

**Always on, never a switch.** Write-back is part of Revansa (spec 6.3, 6.4, 17.1): there is no
Admin or Client switch and no deployment flag. What keeps it safe is technical and not exposed: each
source must carry its own `writeback` block naming the columns (see the Google Sheets provider),
the attempts are kept in a durable ledger (idempotence), and a source that is not authorised, not
verified or failing is a visible error, never a silent skip. Nothing here enables a source that
does not ask. A client in the global `DRY_RUN` mode gets no write-back at all: no
syncer is built, so the source is neither read nor written. (The syncer's own `dry_run` is another
thing: a simulation that reads and plans but does not write; it is not used here.)

**One syncer per source, run independently.** Each source has its own syncer over the client's own
store, consent registry and ledger. A source that is not ready, unreachable or failing stops only
its own pass: the others, the sending and the replies go on. Before every pass the source is
verified and a source with any blocking issue (a mapped column that is gone, because the
spreadsheet was changed) is not read for values and not written to.

**What leaves this module is counters and fixed sentences**: never a contact, a name, a message
or the value of a cell. The one thing it logs is that a pass raised, and the type of what it raised.

Google Sheets offers no atomic compare-and-set: the check before a write narrows the window in
which a person's concurrent edit can be overwritten, it does not close it.
"""

import logging
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from sales_app.config import ClientConfig
from sales_app.sourcing import ProspectField, ProspectSource
from sales_app.sourcing.consent import ConsentWriter
from sales_app.sourcing.ports import OperationalStore
from sales_app.sourcing.writeback import (
    DurableWriteBackLedger,
    SyncReport,
    WriteBackLedger,
    WriteBackPolicy,
    WriteBackPolicyError,
    WriteBackSyncer,
)
from sales_app.wiring.providers import google_sheets, hubspot
from sales_core.approval import ApprovalMode
from sales_core.ports import Clock

log = logging.getLogger(__name__)

# provider key -> (the operator's settings of one source) -> (fields to write, propagate opt-out)
PolicyOf = Callable[[Mapping[str, Any]], tuple[Collection[ProspectField], bool]]


@dataclass(frozen=True, slots=True)
class WritebackSupport:
    """What a deployment offers for write-back: the ledger and the providers' policies."""

    open_ledger: Callable[[str], WriteBackLedger] | None = None  # one client's ledger
    policies: Mapping[str, PolicyOf] = field(default_factory=dict)


def default_policies() -> dict[str, PolicyOf]:
    """The providers that can be written back to: Google Sheets and HubSpot."""
    return {google_sheets.KEY: google_sheets.writeback_of, hubspot.KEY: hubspot.writeback_of}


def sql_ledgers(engine: Any) -> Callable[[str], WriteBackLedger]:
    """One client's durable ledger in the deployment's database (`engine` is its SQLAlchemy engine; SQL stays
    in the persistence package)."""
    from sales_app.persistence.writeback_ledger_rows import SqlWriteBackLedgerRows

    return lambda client_id: DurableWriteBackLedger(SqlWriteBackLedgerRows(engine, client_id))


@dataclass(frozen=True, slots=True)
class WritebackSummary:
    """What one run did, as counts and fixed sentences. Nothing else."""

    sources: int = 0
    not_ready: int = 0  # verified and found not ready: neither read for values nor written to
    errored: int = 0  # a pass that raised
    stopped: int = 0  # a pass that ended early (unreachable, refused, rate limited...)
    planned: int = 0
    applied: int = 0
    conflicts: int = 0
    deferred: int = 0
    failed: int = 0
    held_back: int = 0
    recovered: int = 0
    not_attempted: int = 0
    errors: tuple[str, ...] = ()

    @property
    def all_failed(self) -> bool:
        """Every source of the client failed to run: the job backs off."""
        return self.sources > 0 and self.not_ready + self.errored + self.stopped >= self.sources

    def counters(self) -> dict[str, int]:
        return {
            name: getattr(self, name)
            for name in (
                "sources",
                "not_ready",
                "errored",
                "stopped",
                "planned",
                "applied",
                "conflicts",
                "deferred",
                "failed",
                "held_back",
                "recovered",
                "not_attempted",
            )
        }


class ClientWriteback:
    """The syncers of one client, one per source that is allowed to be written."""

    def __init__(self, syncers: Mapping[str, tuple[ProspectSource, WriteBackSyncer]]) -> None:
        self._syncers = dict(syncers)

    def __repr__(self) -> str:
        return f"ClientWriteback(sources={sorted(self._syncers)})"

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self._syncers)

    def run(self, *, retry_held: bool = False) -> WritebackSummary:
        totals = dict.fromkeys(
            (
                "planned",
                "applied",
                "conflicts",
                "deferred",
                "failed",
                "held_back",
                "recovered",
                "not_attempted",
            ),
            0,
        )
        not_ready = errored = stopped = 0
        errors: list[str] = []
        for source_id, (source, syncer) in self._syncers.items():
            if not _ready(source):
                not_ready += 1
                errors.append(f"write-back {source_id}: the source is not ready")
                continue
            try:
                report: SyncReport = syncer.run_once(retry_held=retry_held)
            except Exception as error:  # noqa: BLE001 - one source failing must never stop the others
                # A fixed message and the type of what was raised, nothing of its content: it could
                # carry a name, a contact or the value of a cell. The source is named by the sentence
                # in `errors`, which the scheduler logs with the client.
                log.error("a write-back pass raised %s", type(error).__name__)
                errored += 1
                errors.append(f"write-back {source_id}: the pass failed unexpectedly")
                continue
            for name in totals:
                totals[name] += getattr(report, name)
            if report.stopped is not None:
                stopped += 1
                errors.append(f"write-back {source_id}: stopped ({report.stopped.value})")
        return WritebackSummary(
            sources=len(self._syncers),
            not_ready=not_ready,
            errored=errored,
            stopped=stopped,
            errors=tuple(errors),
            **totals,
        )


def _ready(source: ProspectSource) -> bool:
    """Whether the source passes its own verification, right now. A warning does not block; a
    problem, or a verification that cannot be made, does."""
    try:
        return not [issue for issue in source.verify() if not issue.startswith("warning:")]
    except Exception:  # noqa: BLE001 - a source that cannot even be verified is not ready
        return False


def build_client_writeback(
    config: ClientConfig,
    sources: Mapping[str, ProspectSource],
    store: OperationalStore,
    consent: ConsentWriter,
    support: WritebackSupport | None,
    clock: Clock,
) -> tuple[ClientWriteback | None, list[str]]:
    """The client's write-back, or None when there is none to run, and the problems found.

    None, with no problem, when the deployment offers no write-back support, when the client is in
    the global `DRY_RUN` mode, or when no source asks for it. Problems (a block that does not fit
    its source, no ledger to keep the attempts in) are words for `RuntimeBuildError`: a source that
    asked to be written back never silently is not.
    """
    if support is None or config.effective_mode is ApprovalMode.DRY_RUN:
        return None, []
    problems: list[str] = []
    wanted: dict[str, WriteBackPolicy] = {}
    for instance in config.spec.sources or ():
        policy_of = support.policies.get(instance.provider)
        if policy_of is None:
            continue
        try:
            fields, opt_out = policy_of(instance.settings)
            policy = WriteBackPolicy(fields=frozenset(fields), propagate_opt_out=opt_out)
        except ValueError as error:
            problems.append(f"source {instance.id!r}: write-back: {_words(error)}")
            continue
        if policy.enabled and instance.id in sources:
            wanted[instance.id] = policy
    if not wanted:
        return None, problems
    if support.open_ledger is None:
        return None, [*problems, "write-back needs the database, and this deployment has none"]
    ledger = support.open_ledger(config.client_id)
    unusable = [issue for issue in ledger.verify() if not issue.startswith("warning:")]
    if unusable:
        return None, [*problems, "write-back: the ledger cannot be used"]
    syncers: dict[str, tuple[ProspectSource, WriteBackSyncer]] = {}
    for source_id, policy in wanted.items():
        source = sources[source_id]
        try:
            syncers[source_id] = (
                source,
                WriteBackSyncer(source, store, consent, ledger, policy, clock=clock.now),
            )
        except (ValueError, WriteBackPolicyError) as error:
            problems.append(f"source {source_id!r}: write-back: {_words(error)}")
    if problems:
        return None, problems
    return ClientWriteback(syncers), []


def _words(error: Exception) -> str:
    """A configuration error in words, without the values that were given."""
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            return "; ".join(
                ".".join(str(part) for part in e["loc"]) + f": {e['msg']}" for e in errors()
            )
        except Exception:  # noqa: BLE001
            return "the settings are not acceptable"
    return str(error)
