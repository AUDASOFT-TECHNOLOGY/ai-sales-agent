"""In-process scheduler: decides WHEN the Engine runs, never WHAT it does.

It only compares the clock with each client's schedule and calls `recover_pending_replies`,
`prepare` and `dispatch`. Who gets contacted, and how many, is the Planner's decision; what is
said is the Brain's. Every job is idempotent in the Core, so a restart (which forgets this
state) can at worst repeat work that the Core turns into a no-op.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from sales_app.config import ClientConfig
from sales_app.errors import ClientNotReadyError
from sales_app.inbox import InboxPoller, PositionStore, poll_inbox
from sales_app.logs import client_logger
from sales_app.registry import ClientRegistry
from sales_app.wiring import ClientRuntime
from sales_core.ports import Clock

log = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 900
INBOX_EVERY_MINUTES = 2  # how often a mailbox is read; not tied to the outbound window
SYNC_EVERY_MINUTES = 10  # how often results are written back into the sources
SYNC_BACKOFF_BASE_SECONDS = 60
SYNC_BACKOFF_MAX_SECONDS = 1800


class Job(StrEnum):
    DAILY = "daily"  # recover replies, prepare (plan + Brain), dispatch approved actions
    PREPARE = "prepare"  # another planning pass: new prospects, newly due follow-ups
    DISPATCH = "dispatch"  # send what humans approved since the last run
    RECOVER = "recover"  # process stored replies that were never handled
    INBOX = "inbox"  # fetch the replies of a channel that does not call us (a mailbox)
    SYNC = "sync"  # write results back into the sources that are allowed to receive them


class InboxSupport(Protocol):
    """Which clients have an inbox to poll, and how to open it (`IntegrationBuilders`)."""

    def polls_inbox(self, config: ClientConfig) -> bool: ...

    def open_inbox(self, config: ClientConfig) -> InboxPoller: ...


@dataclass(frozen=True, slots=True)
class JobResult:
    client_id: str
    job: Job
    ok: bool
    errors: tuple[str, ...] = ()  # problems reported by the Engine or the exception raised


class Scheduler:
    def __init__(
        self,
        registry: ClientRegistry,
        clock: Clock,
        *,
        executor: Executor | None = None,
        may_run: Callable[[str], bool] | None = None,
        inbox: InboxSupport | None = None,
        inbox_every_minutes: int = INBOX_EVERY_MINUTES,
        sync_every_minutes: int = SYNC_EVERY_MINUTES,
        mail_cursors: Callable[[str], PositionStore] | None = None,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._may_run = may_run  # the client's own switch; None: nothing to ask
        self._inbox = inbox
        self._mail_cursors = mail_cursors  # one client's positions; None: no database
        self._inbox_every = inbox_every_minutes
        self._sync_every = sync_every_minutes
        self._executor = executor
        self._owns_executor = executor is None

    # ---- what is due ---------------------------------------------------------------------

    def due_jobs(self, runtime: ClientRuntime, now: datetime) -> list[Job]:
        """Jobs that should start now for this client. Pure: no side effects.

        The write-back comes last and on its own clock: it is due whatever the outbound window, and
        a client whose sending is backing off is still written back (and the reverse)."""
        jobs = self._due_work(runtime, now)
        if self._sync_due(runtime, now):
            jobs = [*jobs, Job.SYNC]
        return jobs

    def _sync_due(self, runtime: ClientRuntime, now: datetime) -> bool:
        if runtime.writeback is None or runtime.suspended:
            return False
        state = runtime.state
        if state.sync_blocked_until is not None and now < state.sync_blocked_until:
            return False
        return _elapsed(state.last_sync_at, now, self._sync_every)

    def _due_work(self, runtime: ClientRuntime, now: datetime) -> list[Job]:
        state = runtime.state
        if state.blocked_until is not None and now < state.blocked_until:
            return []
        spec = runtime.config.spec
        schedule = spec.schedule
        zone = ZoneInfo(spec.timezone)
        local = now.astimezone(zone)

        # Outbound work (the daily cycle and dispatching approved actions) happens on the
        # configured weekdays, from `daily_at` on. Recovery only handles inbound replies.
        opens_at = (
            datetime.combine(local.date(), schedule.daily_time, tzinfo=zone) + runtime.stagger
        )
        outbound_open = local.weekday() in schedule.weekdays and local >= opens_at

        # Replies are read whenever they are due, outbound window or not, and before the cycle
        # that answers them.
        jobs: list[Job] = [Job.INBOX] if self._inbox_due(runtime, now) else []
        if outbound_open and state.last_daily_date != local.date():
            return [*jobs, Job.DAILY]  # includes recovery and dispatch
        if _elapsed(state.last_recover_at, now, schedule.recover_every_minutes):
            jobs.append(Job.RECOVER)
        if outbound_open and _elapsed(state.last_prepare_at, now, schedule.prepare_every_minutes):
            jobs.append(Job.PREPARE)
        if outbound_open and _elapsed(state.last_dispatch_at, now, schedule.dispatch_every_minutes):
            jobs.append(Job.DISPATCH)
        return jobs

    def _inbox_due(self, runtime: ClientRuntime, now: datetime) -> bool:
        if self._inbox is None or not self._inbox.polls_inbox(runtime.config):
            return False
        state = runtime.state
        if state.inbox_blocked_until is not None and now < state.inbox_blocked_until:
            return False
        return _elapsed(state.last_inbox_at, now, self._inbox_every)

    # ---- running -------------------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> list[Future[list[JobResult]]]:
        """Start whatever is due, one worker per client, and return without waiting."""
        moment = now if now is not None else self._clock.now()
        futures: list[Future[list[JobResult]]] = []
        for runtime in self._registry.runtimes():
            if not self._allowed(runtime.client_id):
                continue
            jobs = self.due_jobs(runtime, moment)
            if jobs and runtime.state.try_begin():  # never overlap two jobs of one client
                futures.append(self._pool().submit(self._run_jobs, runtime, jobs, moment))
        return futures

    def run_job_now(
        self, runtime: ClientRuntime, job: Job, now: datetime | None = None
    ) -> JobResult:
        """Run one job immediately in this thread (used by the CLI)."""
        return self._execute(runtime, job, now if now is not None else self._clock.now())

    async def run(self, stop: asyncio.Event, interval_seconds: float) -> None:
        """Tick until `stop` is set. Ticking only submits work, so it never blocks the loop."""
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass

    def _allowed(self, client_id: str) -> bool:
        """Whether the client's own switch lets its agent work. Fails closed: if the switch cannot
        be read, nothing starts for that client this tick (a job already running is not stopped)."""
        if self._may_run is None:
            return True
        try:
            return self._may_run(client_id)
        except Exception:  # noqa: BLE001 - an unreadable switch must never mean "go"
            client_logger(__name__, client_id).warning(
                "the agent switch is unreadable; not starting"
            )
            return False

    def shutdown(self) -> None:
        """Wait for running jobs to finish."""
        if self._executor is not None and self._owns_executor:
            self._executor.shutdown(wait=True)

    # ---- internals -----------------------------------------------------------------------

    def _pool(self) -> Executor:
        if self._executor is None:
            workers = max(2, len(self._registry.runtimes()))
            self._executor = ThreadPoolExecutor(workers, thread_name_prefix="sales-job")
        return self._executor

    def _run_jobs(
        self, runtime: ClientRuntime, jobs: Sequence[Job], now: datetime
    ) -> list[JobResult]:
        results: list[JobResult] = []
        try:
            for job in jobs:
                result = self._execute(runtime, job, now)
                results.append(result)
                if not result.ok and job not in (Job.INBOX, Job.SYNC):
                    break  # a failing client waits for its backoff before anything else
        finally:
            runtime.state.end()
        return results

    def _execute(self, runtime: ClientRuntime, job: Job, now: datetime) -> JobResult:
        logger = client_logger(__name__, runtime.client_id)
        try:
            with runtime.lease() as engine:  # one Engine for the whole job, even across a refresh
                if job is Job.DAILY:
                    reports = [
                        engine.recover_pending_replies(),
                        engine.prepare(),
                        engine.dispatch(),
                    ]
                elif job is Job.PREPARE:
                    reports = [engine.prepare()]
                elif job is Job.DISPATCH:
                    reports = [engine.dispatch()]
                elif job is Job.INBOX:
                    reports = [self._poll(runtime, engine)]
                elif job is Job.SYNC:
                    return self._sync(runtime, now)
                else:
                    reports = [engine.recover_pending_replies()]
        except ClientNotReadyError as error:
            # It lost a credential after the tick chose it: not a failure, nothing to back off from.
            logger.warning("job %s skipped: %s", job.value, error)
            return JobResult(runtime.client_id, job, ok=False, errors=(str(error),))
        except Exception as error:
            if job is Job.SYNC:  # the type only: the message of what failed may carry a value
                logger.error("job %s failed (%s)", job.value, type(error).__name__)
            else:
                logger.exception("job %s failed", job.value)
            if job is Job.INBOX:
                self._block_inbox(runtime, now)
            elif job is Job.SYNC:  # counted only: what it raised may carry a value
                self._block_sync(runtime, now)
                runtime.health.record_writeback_failure(now)
                return JobResult(runtime.client_id, job, ok=False, errors=("write-back failed",))
            else:
                self._block(runtime, now)
            runtime.health.record_failure(job.value, now, f"{type(error).__name__}: {error}")
            return JobResult(runtime.client_id, job, ok=False, errors=(str(error),))

        errors = tuple(e for report in reports for e in report.errors)
        for message in errors:
            logger.error("job %s: %s", job.value, message)
        self._mark_done(runtime, job, now)
        runtime.health.record_success(job.value, now, errors)
        logger.info("job %s finished (%d reported problems)", job.value, len(errors))
        return JobResult(runtime.client_id, job, ok=True, errors=errors)

    def _sync(self, runtime: ClientRuntime, now: datetime) -> JobResult:
        """One write-back run. Called inside the lease, so the syncers are the ones of the Engine in
        force. Each source runs on its own; what comes back is counters and fixed sentences."""
        writeback = runtime.writeback
        if writeback is None:
            return JobResult(runtime.client_id, Job.SYNC, ok=True)
        summary = writeback.run()
        runtime.state.last_sync_at = now
        if summary.all_failed:
            self._block_sync(runtime, now)
        else:
            runtime.state.sync_blocked_until = None
            runtime.state.sync_failures = 0
        runtime.health.record_writeback(now, summary, failed=summary.all_failed)
        return JobResult(
            runtime.client_id, Job.SYNC, ok=not summary.all_failed, errors=summary.errors
        )

    @staticmethod
    def _block_sync(runtime: ClientRuntime, now: datetime) -> None:
        state = runtime.state
        state.last_sync_at = now
        state.sync_failures += 1
        delay = min(
            SYNC_BACKOFF_BASE_SECONDS * 2 ** (state.sync_failures - 1), SYNC_BACKOFF_MAX_SECONDS
        )
        state.sync_blocked_until = now + timedelta(seconds=delay)

    def _poll(self, runtime: ClientRuntime, engine):
        """One poll of the client's inbox, with the config in force for this lease."""
        assert self._inbox is not None
        poller = self._inbox.open_inbox(runtime.config)
        try:
            if getattr(poller, "needs_cursor", False):
                # A deployment with no database keeps no position: the reader then refuses to read
                # (it would lose messages or read them twice), and the job fails with that reason.
                poller.use_cursor(  # type: ignore[attr-defined]
                    self._mail_cursors(runtime.config.client_id) if self._mail_cursors else None
                )
            return poll_inbox(engine, poller)
        finally:
            poller.close()

    @staticmethod
    def _block_inbox(runtime: ClientRuntime, now: datetime) -> None:
        state = runtime.state
        state.inbox_failures += 1
        delay = min(BACKOFF_BASE_SECONDS * 2 ** (state.inbox_failures - 1), BACKOFF_MAX_SECONDS)
        state.inbox_blocked_until = now + timedelta(seconds=delay)

    @staticmethod
    def _mark_done(runtime: ClientRuntime, job: Job, now: datetime) -> None:
        state = runtime.state
        if job is Job.INBOX:  # its own bookkeeping: it never clears another job's backoff
            state.last_inbox_at = now
            state.inbox_blocked_until = None
            state.inbox_failures = 0
            return
        state.blocked_until = None
        if job is Job.DAILY:
            zone = ZoneInfo(runtime.config.spec.timezone)
            state.last_daily_date = now.astimezone(zone).date()
            state.last_prepare_at = state.last_dispatch_at = state.last_recover_at = now
        elif job is Job.PREPARE:
            state.last_prepare_at = now
        elif job is Job.DISPATCH:
            state.last_dispatch_at = now
        else:
            state.last_recover_at = now

    @staticmethod
    def _block(runtime: ClientRuntime, now: datetime) -> None:
        failures = int(runtime.health.snapshot()["consecutive_failures"]) + 1  # type: ignore[call-overload]
        delay = min(BACKOFF_BASE_SECONDS * 2 ** (failures - 1), BACKOFF_MAX_SECONDS)
        runtime.state.blocked_until = now + timedelta(seconds=delay)


def _elapsed(last: datetime | None, now: datetime, every_minutes: int) -> bool:
    return last is None or now - last >= timedelta(minutes=every_minutes)
