"""Two processes over one database: a pending consent is used once, by whichever process gets the
callback; a job of a client runs in one process at a time. Migration 0020 adds the two tables and
nothing else. SQLite stands in for PostgreSQL (see docs/real-integration-test-procedures.md)."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect

from sales_app.google_mailbox import PendingAuthorization
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.multiprocess_rows import SqlJobLeases, SqlPendingConsents
from sales_app.persistence.schema import current_revision, head_revision
from sales_app.scheduler import Job, JobResult, Scheduler, lease_name
from sales_app.shared_state import JobLeaseGuard, SharedPendingAuthorizations

T0 = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path):
    made = create_database_engine(f"sqlite:///{(tmp_path / 'shared.db').as_posix()}")
    upgrade_database(made)
    yield made
    made.dispose()


def test_migration_0020_adds_only_two_tables():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0018")
    before = set(inspect(engine).get_table_names())
    upgrade_database(engine, "0020")
    after = set(inspect(engine).get_table_names())
    assert after - before == {"oauth_pending_consents", "job_leases"}
    assert current_revision(engine) == "0020"


# ---- pending consents -----------------------------------------------------------------------


def pending(client="alpha", verifier="v" * 43, minutes=10):
    return PendingAuthorization(client, verifier, T0 + timedelta(minutes=minutes))


def test_a_consent_started_by_one_process_is_finished_by_another(engine):
    first = SharedPendingAuthorizations(SqlPendingConsents(engine), "hubspot")
    second = SharedPendingAuthorizations(SqlPendingConsents(engine), "hubspot")
    first.put("sess-1", pending())
    got = second.take("sess-1")
    assert got is not None and got.client_id == "alpha" and got.code_verifier == "v" * 43
    assert got.expires_at == T0 + timedelta(minutes=10)


def test_a_consent_is_single_use_even_when_two_processes_race(engine):
    rows = [SqlPendingConsents(engine), SqlPendingConsents(engine)]
    SharedPendingAuthorizations(rows[0], "gmail").put("s", pending())
    won: list[object] = []

    def take(i):
        won.append(SharedPendingAuthorizations(rows[i % 2], "gmail").take("s"))

    threads = [threading.Thread(target=take, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for w in won if w is not None) == 1


def test_a_consent_does_not_cross_kinds_and_expired_ones_are_forgotten(engine):
    rows = SqlPendingConsents(engine)
    SharedPendingAuthorizations(rows, "sheets").put("s", pending())
    assert SharedPendingAuthorizations(rows, "gmail").take("s") is None  # another consent kind
    assert SharedPendingAuthorizations(rows, "sheets").take("s") is not None
    old = PendingAuthorization("alpha", "x", datetime(2020, 1, 1, tzinfo=UTC))
    SharedPendingAuthorizations(rows, "sheets").put("old", old)
    SharedPendingAuthorizations(rows, "sheets").put("new", pending())  # purges the expired
    assert rows.take("sheets", "old") is None


def test_an_unreadable_store_proves_nothing():
    class Broken:
        def take(self, kind, session_id):
            raise RuntimeError("down")

    assert SharedPendingAuthorizations(Broken(), "gmail").take("s") is None


# ---- job leases -----------------------------------------------------------------------------


def test_only_one_holder_per_client_and_job(engine):
    one = JobLeaseGuard(SqlJobLeases(engine), holder="p1")
    two = JobLeaseGuard(SqlJobLeases(engine), holder="p2")
    assert one.try_acquire("alpha", "engine", T0)
    assert not two.try_acquire("alpha", "engine", T0)
    assert two.try_acquire("beta", "engine", T0)  # another client is free
    assert two.try_acquire("alpha", "sync", T0)  # another job is free
    one.release("alpha", "engine")
    assert two.try_acquire("alpha", "engine", T0)


def test_a_crashed_holder_loses_its_lease_at_the_ttl(engine):
    one = JobLeaseGuard(SqlJobLeases(engine), holder="p1", ttl=timedelta(minutes=5))
    two = JobLeaseGuard(SqlJobLeases(engine), holder="p2")
    assert one.try_acquire("alpha", "engine", T0)
    assert not two.try_acquire("alpha", "engine", T0 + timedelta(minutes=4))
    assert two.try_acquire("alpha", "engine", T0 + timedelta(minutes=6))
    one.release("alpha", "engine")  # too late: it is no longer p1's, so this frees nothing
    assert not one.try_acquire("alpha", "engine", T0 + timedelta(minutes=7))


def test_racing_processes_never_both_get_the_lease(engine):
    guards = [JobLeaseGuard(SqlJobLeases(engine), holder=f"p{i}") for i in range(4)]
    got: list[bool] = []

    def go(g):
        got.append(g.try_acquire("alpha", "engine", T0))

    threads = [threading.Thread(target=go, args=(g,)) for g in guards]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert got.count(True) == 1


def test_an_unreachable_database_fails_closed():
    class Broken:
        def acquire(self, *a):
            raise RuntimeError("down")

        def release(self, *a):
            raise RuntimeError("down")

    guard = JobLeaseGuard(Broken())
    assert guard.try_acquire("alpha", "engine", T0) is False
    guard.release("alpha", "engine")  # never raises


def test_engine_jobs_share_a_lease_and_the_others_have_their_own():
    assert {lease_name(j) for j in (Job.DAILY, Job.PREPARE, Job.DISPATCH, Job.RECOVER)} == {"engine"}
    assert lease_name(Job.SYNC) == "sync" and lease_name(Job.INBOX) == "inbox"


# ---- the scheduler -------------------------------------------------------------------------


class Denying:
    def __init__(self, allow):
        self.allow, self.released = allow, []

    def try_acquire(self, client_id, job, now):
        return self.allow

    def release(self, client_id, job):
        self.released.append((client_id, job))


def make_scheduler(leases):
    scheduler = Scheduler.__new__(Scheduler)
    scheduler._leases = leases
    ran = []
    scheduler._execute_leased = lambda runtime, job, now: (
        ran.append(job) or JobResult("alpha", job, ok=True)
    )
    return scheduler, ran


class Runtime:
    client_id = "alpha"


def test_a_job_held_by_another_process_is_skipped_not_failed():
    leases = Denying(False)
    scheduler, ran = make_scheduler(leases)
    result = scheduler._execute(Runtime(), Job.DAILY, T0)
    assert result.skipped and result.ok and ran == [] and leases.released == []


def test_a_job_with_the_lease_runs_and_frees_it():
    leases = Denying(True)
    scheduler, ran = make_scheduler(leases)
    result = scheduler._execute(Runtime(), Job.SYNC, T0)
    assert result.ok and not result.skipped and ran == [Job.SYNC]
    assert leases.released == [("alpha", "sync")]


def test_without_leases_one_process_runs_as_before():
    scheduler, ran = make_scheduler(None)
    assert scheduler._execute(Runtime(), Job.DAILY, T0).ok and ran == [Job.DAILY]
