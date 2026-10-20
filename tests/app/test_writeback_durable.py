"""What the SQL write-back ledger adds to the contract it already meets (see
`test_writeback_ledger.py`, which runs both ledgers through the same tests): it survives a restart,
it is shared by every process of a client without a gap in its numbering, it keeps clients apart,
and its table, added by migration 0005, can hold nothing personal.
"""

import os
import random
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, text

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.schema import current_revision, head_revision
from sales_app.persistence.tables import metadata
from sales_app.persistence.writeback_ledger_rows import SqlWriteBackLedgerRows
from sales_app.sourcing import ProspectField, StoreError
from sales_app.sourcing.writeback import (
    DurableWriteBackLedger,
    FailureCode,
    Outcome,
    WriteAttempt,
    WriteBackLedger,
)
from sales_core.contracts import ProspectId, ProspectStatus

S = ProspectStatus
F = ProspectField
A, B = "client-a", "client-b"
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def attempt(attempt_id="a-1", client=A, pid="sheet_a:p1", **overrides):
    values = {
        "client_id": client,
        "attempt_id": attempt_id,
        "prospect_id": ProspectId(pid),
        "new": S.PAUSED,
        "expected": S.NEW,
        "outcome": Outcome.APPLIED,
        "at": T0,
    }
    return WriteAttempt(**(values | overrides))


def count(n=3, field=F.FOLLOW_UP_COUNT, **kw):
    return attempt(new=str(n), expected=str(n - 1), field=field, **kw)


@pytest.fixture
def engine(tmp_path):
    made = create_database_engine(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    upgrade_database(made)
    yield made
    made.dispose()


def open_ledger(engine, client=A):
    return DurableWriteBackLedger(SqlWriteBackLedgerRows(engine, client))


# ---- it survives a restart ---------------------------------------------------------------------------


def test_everything_it_recorded_is_there_after_a_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
    first = create_database_engine(url)
    upgrade_database(first)
    ledger = open_ledger(first)
    ledger.append(
        [
            attempt("a-1"),
            attempt(
                "a-2",
                pid="sheet_a:p2",
                outcome=Outcome.DEFERRED,
                code=FailureCode.RATE_LIMITED,
                not_before=T0 + timedelta(minutes=9),
            ),
            count(4, attempt_id="a-3"),
        ]
    )
    first.dispose()
    second = create_database_engine(url)
    reborn = open_ledger(second)
    assert isinstance(reborn, WriteBackLedger)
    entries = reborn.entries()
    assert [(e.attempt_id, e.sequence) for e in entries] == [("a-1", 1), ("a-2", 2), ("a-3", 3)]
    deferred = reborn.latest(ProspectId("sheet_a:p2"))
    assert deferred.outcome is Outcome.DEFERRED and deferred.code is FailureCode.RATE_LIMITED
    assert deferred.not_before == T0 + timedelta(minutes=9) and deferred.at == T0
    kept = reborn.latest(ProspectId("sheet_a:p1"), F.FOLLOW_UP_COUNT)
    assert (kept.new, kept.expected, kept.field) == ("4", "3", F.FOLLOW_UP_COUNT)
    assert reborn.latest(ProspectId("sheet_a:p1")).new is S.PAUSED  # a status comes back as one
    second.dispose()


def test_a_retry_after_a_restart_is_recorded_once_and_the_numbering_goes_on(tmp_path):
    url = f"sqlite:///{(tmp_path / 'again.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    open_ledger(engine).append([attempt("a-1")])
    engine.dispose()
    engine = create_database_engine(url)
    ledger = open_ledger(engine)
    (again,) = ledger.append([attempt("a-1")])
    (next_one,) = ledger.append([attempt("a-2", pid="sheet_a:p2")])
    assert (again.sequence, next_one.sequence) == (1, 2) and len(ledger.entries()) == 2
    with pytest.raises(ValueError):  # the same id with another content is refused, even now
        ledger.append([attempt("a-1", outcome=Outcome.CONFLICT)])
    assert len(ledger.entries()) == 2
    engine.dispose()


# ---- two processes of one client ---------------------------------------------------------------------


@pytest.fixture
def shared_engine(engine):
    """The database two processes share. SQLite (default) serialises writers and can answer "locked"
    under CPU load, which the test absorbs by trying again. If SALES_AGENT_TEST_DATABASE_URL names a
    PostgreSQL database (already migrated, disposable), the same test runs on it with a client id
    of its own, which it removes afterwards."""
    url = os.environ.get("SALES_AGENT_TEST_DATABASE_URL", "").strip()
    if not url:
        yield engine, A
        return
    made = create_database_engine(url)
    client = f"race-{uuid.uuid4().hex[:10]}"
    try:
        yield made, client
    finally:
        with made.begin() as connection:
            connection.execute(
                text("DELETE FROM op_writeback_ledger WHERE client_id = :c"), {"c": client}
            )
            connection.execute(text("DELETE FROM clients WHERE client_id = :c"), {"c": client})
        made.dispose()


def test_two_writers_over_one_database_never_repeat_or_skip_a_number(shared_engine):
    engine, client = shared_engine
    ledgers = [open_ledger(engine, client), open_ledger(engine, client)]  # two processes: two locks
    failures: list[str] = []

    def work(worker):
        for n in range(15):
            row = [attempt(f"w{worker}-{n}", client=client, pid=f"sheet_a:p{worker}")]
            deadline = time.monotonic() + 30
            while True:  # the ledger gives up visibly after a few lost races (or "database is
                try:  # locked" on SQLite); appending is idempotent by attempt id, so retrying is safe
                    ledgers[worker % 2].append(row)
                    break
                except StoreError:
                    if time.monotonic() > deadline:
                        failures.append(f"w{worker}-{n}")
                        break
                    time.sleep(0.01 + random.random() * 0.03)

    threads = [threading.Thread(target=work, args=(w,)) for w in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []  # every attempt was recorded in the end, none given up on
    entries = ledgers[0].entries()
    assert [e.sequence for e in entries] == list(range(1, 61))
    assert len({e.attempt_id for e in entries}) == 60


# ---- clients are kept apart -----------------------------------------------------------------------------


def test_two_clients_in_one_database_share_nothing_not_even_an_attempt_id_or_a_number(engine):
    mine, theirs = open_ledger(engine, A), open_ledger(engine, B)
    mine.append([attempt("run1-0"), attempt("run1-1", pid="sheet_a:p2")])
    theirs.append([attempt("run1-0", client=B, outcome=Outcome.CONFLICT)])
    assert [e.sequence for e in mine.entries()] == [1, 2]
    assert [(e.sequence, e.outcome, e.client_id) for e in theirs.entries()] == [
        (1, Outcome.CONFLICT, B)
    ]
    assert theirs.latest(ProspectId("sheet_a:p2")) is None
    assert theirs.entries(ProspectId("sheet_a:p2")) == ()
    with pytest.raises(ValueError):
        mine.append([attempt("x", client=B)])
    assert len(mine.entries()) == 2 and len(theirs.entries()) == 1


def test_a_ledger_cannot_be_opened_without_a_client(engine):
    with pytest.raises(ValueError):
        SqlWriteBackLedgerRows(engine, "")


# ---- what is stored -------------------------------------------------------------------------------------


def test_the_table_has_no_column_that_could_hold_a_name_a_contact_or_a_message(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("op_writeback_ledger")}
    assert columns == {
        "client_id", "sequence", "attempt_id", "prospect_id", "field", "new_value",
        "expected_value", "outcome", "code", "at", "not_before",
    }  # fmt: skip


def test_a_row_that_cannot_be_read_back_is_an_error_not_a_guess(engine):
    ledger = open_ledger(engine)
    ledger.append([attempt("a-1")])
    with engine.begin() as connection:
        connection.execute(text("update op_writeback_ledger set outcome = 'sideways'"))
    with pytest.raises(StoreError) as caught:
        ledger.entries()
    assert "sideways" not in str(caught.value)


def test_it_says_whether_its_table_is_there(engine):
    assert open_ledger(engine).verify() == []
    with engine.begin() as connection:
        connection.execute(text("drop table op_writeback_ledger"))
    assert open_ledger(engine).verify() != []


def test_a_database_that_cannot_be_reached_is_a_store_error_with_a_fixed_sentence(engine):
    ledger = open_ledger(engine)
    with engine.begin() as connection:
        connection.execute(text("drop table op_writeback_ledger"))
    with pytest.raises(StoreError) as caught:
        ledger.append([attempt("a-1", pid="sheet_a:p-secret")])
    assert "secret" not in str(caught.value)


# ---- migration 0005 -------------------------------------------------------------------------------------


class TestMigration:
    def test_0005_adds_the_ledger_and_nothing_else(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0004")
        before = set(inspect(engine).get_table_names())
        assert "op_writeback_ledger" not in before
        upgrade_database(engine, "0005")
        assert current_revision(engine) == "0005"
        after = set(inspect(engine).get_table_names())
        assert after - before == {"op_writeback_ledger"}
        upgrade_database(engine)
        assert head_revision() == current_revision(engine)
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set(metadata.tables)

    def test_the_upgrade_keeps_what_was_there(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0004")
        with engine.begin() as connection:
            connection.execute(
                text("insert into clients(client_id, created_at) values ('old', '2026-01-01')")
            )
        upgrade_database(engine)
        with engine.connect() as connection:
            assert connection.execute(text("select client_id from clients")).scalars().all() == [
                "old"
            ]

    def test_the_table_keeps_the_two_promises_the_ledger_makes(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine)
        inspector = inspect(engine)
        unique = [
            u["column_names"] for u in inspector.get_unique_constraints("op_writeback_ledger")
        ]
        assert ["client_id", "attempt_id"] in unique
        assert inspector.get_pk_constraint("op_writeback_ledger")["constrained_columns"] == [
            "client_id",
            "sequence",
        ]
        [fk] = inspector.get_foreign_keys("op_writeback_ledger")
        assert fk["referred_table"] == "clients"
