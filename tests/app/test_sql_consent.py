"""The consent registry in the database: isolation, integrity, ordering under concurrency, an import
that loses nothing, and a strict mode that never touches a file. SQLite stands in for PostgreSQL."""

import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app_support import make_settings
from consent_support import CLIENT, OTHER, PHONE, SECOND_PHONE, contact
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from sales_app.consent_import import import_log, read_file_events
from sales_app.consent_wiring import ConsentImportPendingError, open_consent_registry
from sales_app.errors import ConfigurationError
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.consent_rows import SqlConsentLog
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, generate_master_key
from sales_app.sourcing.consent import (
    ConsentConflictError,
    ConsentRegistry,
    ConsentSecrets,
    FileConsentLog,
    Health,
    Origin,
    ReasonCode,
)

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


def secrets_for(store, client=CLIENT):
    return ConsentSecrets(store, InMemorySecretAudit(), client)


def registry(log, store, client=CLIENT):
    reg = ConsentRegistry(client, log, secrets_for(store, client))
    reg.initialise(actor="test")
    return reg


def opt_out(reg, phone=PHONE):
    return reg.record_opt_out(
        contact(phone), origin=Origin.OPERATOR, reason=ReasonCode.OPERATOR_REQUEST, actor="test"
    )


class TestIsolationAndIntegrity:
    def test_one_clients_events_are_never_another_clients(self, engine):
        store = InMemorySecretStore()
        a = registry(SqlConsentLog(engine, CLIENT), store)
        b = registry(SqlConsentLog(engine, OTHER), store, OTHER)
        opt_out(a)
        assert a.report().events == 1 and b.report().events == 0
        assert not b.snapshot().blocks(contact(PHONE))  # a's refusal is not b's
        assert a.snapshot().blocks(contact(PHONE))
        assert SqlConsentLog(engine, OTHER).read().events == ()

    def test_the_chain_verifies_and_an_edit_in_the_database_is_caught(self, engine):
        store = InMemorySecretStore()
        reg = registry(SqlConsentLog(engine, CLIENT), store)
        opt_out(reg)
        opt_out(reg, SECOND_PHONE)
        assert reg.report().health is Health.OK and reg.report().events == 2
        # Rows can never be changed or removed once written ...
        with engine.begin() as c, pytest.raises(DBAPIError, match="append-only"):
            c.execute(text("update consent_events set actor = 'someone' where seq = 1"))
        with engine.begin() as c, pytest.raises(DBAPIError, match="append-only"):
            c.execute(text("delete from consent_events where seq = 2"))
        assert reg.report().events == 2

    def test_a_row_forged_around_the_triggers_breaks_the_chain(self, engine):
        store = InMemorySecretStore()
        reg = registry(SqlConsentLog(engine, CLIENT), store)
        opt_out(reg)
        with engine.begin() as c:  # a tamperer who removes the trigger first
            c.execute(text("drop trigger consent_events_no_update"))
            c.execute(text("update consent_events set actor = 'forged' where seq = 1"))
        assert reg.report().health is Health.CORRUPT
        assert reg.snapshot().healthy is False  # unhealthy blocks everyone


class TestOrderUnderConcurrency:
    def test_two_writers_for_the_same_position_cannot_both_succeed(self, engine):
        store = InMemorySecretStore()
        reg = registry(SqlConsentLog(engine, CLIENT), store)
        opt_out(reg)
        log = SqlConsentLog(engine, CLIENT)
        first = log.read().events[0]
        clone = replace(first, seq=2, event_id="b" * 32)
        other = replace(first, seq=2, event_id="c" * 32)
        log.append(clone, expected_last_seq=1)
        with pytest.raises(ConsentConflictError):
            log.append(other, expected_last_seq=1)  # the log moved on: refused, nothing written
        with pytest.raises(ConsentConflictError):
            log.append(replace(first, seq=5, event_id="d" * 32), expected_last_seq=2)  # a gap
        assert [e.seq for e in log.read().events] == [1, 2]

    def test_racing_threads_leave_a_gapless_ordered_log(self, engine):
        store = InMemorySecretStore()
        base = registry(SqlConsentLog(engine, CLIENT), store)
        opt_out(base, "+216 99 000 000")
        log = SqlConsentLog(engine, CLIENT)
        template = log.read().events[0]
        wins, losses = [], []

        def writer(n):
            try:
                event = replace(template, seq=2, event_id=f"{n:032x}")
                log.append(event, expected_last_seq=1)
                wins.append(n)
            except ConsentConflictError:
                losses.append(n)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(1, 9)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(wins) == 1 and len(losses) == 7  # exactly one takes the position
        assert [e.seq for e in log.read().events] == [1, 2]


class TestImport:
    def make_file(self, tmp_path, store, count=3):
        path = tmp_path / "consent" / f"{CLIENT}.jsonl"
        reg = registry(FileConsentLog(path), store)
        for n in range(count):
            opt_out(reg, f"+216 99 000 0{n:02d}")
        return path

    def test_the_report_writes_nothing_and_the_import_copies_every_event_unchanged(
        self, engine, tmp_path
    ):
        store = InMemorySecretStore()
        path = self.make_file(tmp_path, store)
        before = path.read_bytes()
        listing = sorted(p.name for p in path.parent.iterdir())
        target = SqlConsentLog(engine, CLIENT)

        report = import_log(CLIENT, path, target, secrets_for(store), apply=False)
        assert report.ok and (report.file_events, report.to_import) == (3, 3)
        assert target.read().events == ()  # a report changes nothing
        assert sorted(p.name for p in path.parent.iterdir()) == listing  # no lock file either

        done = import_log(CLIENT, path, target, secrets_for(store), apply=True)
        assert done.ok and done.imported == 3
        events, _ = read_file_events(path)
        assert target.read().events == events  # identical, in order
        assert ConsentRegistry(CLIENT, target, secrets_for(store)).report().health is Health.OK
        assert path.read_bytes() == before  # the original is preserved, byte for byte

        again = import_log(CLIENT, path, target, secrets_for(store), apply=True)
        assert again.ok and again.imported == 0 and again.already == 3  # repeatable

    def test_an_interrupted_import_is_completed_by_the_next_one(self, engine, tmp_path):
        store = InMemorySecretStore()
        path = self.make_file(tmp_path, store)
        events, _ = read_file_events(path)
        target = SqlConsentLog(engine, CLIENT)
        target.append(events[0], expected_last_seq=0)  # as if it stopped after the first
        done = import_log(CLIENT, path, target, secrets_for(store), apply=True)
        assert done.ok and done.already == 1 and done.imported == 2
        assert target.read().events == events

    def test_a_file_whose_chain_does_not_verify_is_never_imported(self, engine, tmp_path):
        store = InMemorySecretStore()
        path = self.make_file(tmp_path, store)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"actor":"test"', '"actor":"mallory"')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        target = SqlConsentLog(engine, CLIENT)
        result = import_log(CLIENT, path, target, secrets_for(store), apply=True)
        assert not result.ok and "does not verify" in result.problems[0]
        assert target.read().events == ()  # nothing was written

    def test_a_database_that_disagrees_with_the_file_is_left_alone(self, engine, tmp_path):
        store = InMemorySecretStore()
        path = self.make_file(tmp_path, store)
        events, _ = read_file_events(path)
        target = SqlConsentLog(engine, CLIENT)
        target.append(replace(events[0], event_id="f" * 32), expected_last_seq=0)
        result = import_log(CLIENT, path, target, secrets_for(store), apply=True)
        assert not result.ok and len(target.read().events) == 1


class TestStrictSqlMode:
    def settings(self, tmp_path, **kw):
        return make_settings(
            tmp_path / "clients",
            secrets_dir=tmp_path / "s",
            consent_store="sql",
            secrets_key=generate_master_key(),
            **kw,
        )

    def test_sql_mode_without_a_database_refuses_to_start(self, tmp_path):
        from sales_app.consent_wiring import check_consent_mode

        with pytest.raises(ConfigurationError, match="never kept in files"):
            check_consent_mode(self.settings(tmp_path), None)

    def test_a_client_still_in_a_file_is_refused_not_started_on_an_empty_registry(
        self, engine, tmp_path
    ):
        settings = self.settings(tmp_path)
        store = InMemorySecretStore()
        TestImport().make_file(tmp_path / "s", store)  # the old file, under secrets_dir/consent
        with pytest.raises(ConsentImportPendingError, match="consent import-log"):
            open_consent_registry(settings, engine, store, InMemorySecretAudit(), CLIENT)

    def test_in_sql_mode_no_consent_file_is_read_or_written_or_locked(
        self, engine, tmp_path, monkeypatch
    ):
        settings = self.settings(tmp_path)
        store = InMemorySecretStore()

        def forbidden(*args, **kwargs):
            raise AssertionError("the file system was used")

        monkeypatch.setattr(FileConsentLog, "__init__", forbidden)
        monkeypatch.setattr(Path, "open", forbidden)
        monkeypatch.setattr(Path, "write_bytes", forbidden)
        monkeypatch.setattr(Path, "mkdir", forbidden)
        reg = open_consent_registry(settings, engine, store, InMemorySecretAudit(), CLIENT)
        reg.initialise(actor="test")
        assert opt_out(reg) == 1
        assert reg.snapshot().blocks(contact(PHONE))
        assert not (tmp_path / "s").exists()  # nothing was created on disk


class TestConflictsAreRetriedWithoutLoss:
    def test_a_write_that_loses_its_position_starts_over_from_what_the_log_holds_now(self, engine):
        store = InMemorySecretStore()
        inner = SqlConsentLog(engine, CLIENT)
        rival = registry(inner, store)  # another process, on the same log

        class Racy:
            """The first append finds that a rival took the position just before it."""

            def __init__(self):
                self.exclusive = inner.exclusive
                self.raced = False

            def exists(self):
                return inner.exists()

            def read(self):
                return inner.read()

            def append(self, event, *, expected_last_seq):
                if not self.raced:
                    self.raced = True
                    opt_out(rival, "+216 99 555 555")  # takes position 1 first
                inner.append(event, expected_last_seq=expected_last_seq)

        reg = ConsentRegistry(CLIENT, Racy(), secrets_for(store))
        assert opt_out(reg, SECOND_PHONE) == 1  # retried once, written once
        events = inner.read().events
        assert [e.seq for e in events] == [1, 2]  # the rival's first, ours second: order kept
        assert events[1].prev_mac == events[0].mac
        assert reg.report().health is Health.OK
        assert reg.snapshot().blocks(contact("+216 99 555 555"))
        assert reg.snapshot().blocks(contact(SECOND_PHONE))

    def test_the_same_refusal_recorded_by_two_writers_is_written_once(self, engine):
        store = InMemorySecretStore()
        inner = SqlConsentLog(engine, CLIENT)
        rival = registry(inner, store)

        class Racy:
            def __init__(self):
                self.exclusive = inner.exclusive
                self.raced = False

            def exists(self):
                return inner.exists()

            def read(self):
                return inner.read()

            def append(self, event, *, expected_last_seq):
                if not self.raced:
                    self.raced = True
                    opt_out(rival)  # the rival blocks the very same contact first
                inner.append(event, expected_last_seq=expected_last_seq)

        reg = ConsentRegistry(CLIENT, Racy(), secrets_for(store))
        assert opt_out(reg) == 0  # already blocked on the retry: nothing is written twice
        assert len(inner.read().events) == 1

    def test_writers_racing_on_one_client_all_land_in_a_gapless_verified_chain(self, engine):
        store = InMemorySecretStore()
        registry(SqlConsentLog(engine, CLIENT), store)  # set the client up once
        errors, results = [], []

        def writer(n):
            try:
                reg = ConsentRegistry(CLIENT, SqlConsentLog(engine, CLIENT), secrets_for(store))
                results.append(opt_out(reg, f"+216 98 000 {n:03d}"))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors and results == [1] * 5
        reg = ConsentRegistry(CLIENT, SqlConsentLog(engine, CLIENT), secrets_for(store))
        assert reg.report().health is Health.OK and reg.report().events == 5
        assert [e.seq for e in reg.events()] == [1, 2, 3, 4, 5]
