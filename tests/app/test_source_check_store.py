"""The last connection test of each client, kept by the three stores (memory, file, SQL).

What is proved: one record per client, replaced by a newer test; a naive time is refused; an
unreadable file or a database that is down says so in domain words (never the cause); the SQL
schema comes from the migration, and the file fallback is what a deployment without a database gets.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect

from sales_app.persistence import (
    SqlSourceCheckStore,
    build_account_stores,
    create_database_engine,
    upgrade_database,
)
from sales_app.settings import Settings
from sales_app.source_checks import (
    FileSourceCheckStore,
    InMemorySourceCheckStore,
    SourceCheck,
    SourceCheckStore,
    SourceCheckStoreCorruptError,
    SourceCheckStoreUnavailableError,
)

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture(params=["memory", "file", "sql"])
def make_store(request, tmp_path):
    if request.param == "memory":
        shared = InMemorySourceCheckStore()
        yield lambda: shared
        return
    if request.param == "file":
        yield lambda: FileSourceCheckStore(tmp_path / "state" / "source_checks.json")
        return
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'checks.db').as_posix()}")
    upgrade_database(engine)
    yield lambda: SqlSourceCheckStore(engine)
    engine.dispose()


def test_a_store_keeps_the_last_test_of_each_client(make_store):
    store = make_store()
    assert isinstance(store, SourceCheckStore)
    assert store.get("alpha") is None
    saved = store.set("alpha", fingerprint="f1", ok=True, at=AT)
    assert saved == SourceCheck("alpha", "f1", True, AT)
    assert store.get("alpha") == saved
    assert store.get("beta") is None  # one client's test is never another's


def test_a_newer_test_replaces_the_previous_one(make_store):
    store = make_store()
    store.set("alpha", fingerprint="f1", ok=True, at=AT)
    later = store.set("alpha", fingerprint="f2", ok=False, at=AT + timedelta(minutes=5))
    assert store.get("alpha") == later and later.fingerprint == "f2" and later.ok is False


def test_what_was_kept_is_what_a_second_store_on_the_same_data_reads(make_store):
    first = make_store()
    first.set("alpha", fingerprint="f1", ok=True, at=AT)
    second = make_store()
    assert second.get("alpha") == SourceCheck("alpha", "f1", True, AT)


@pytest.mark.parametrize(
    ("client_id", "fingerprint", "at"),
    [("", "f", AT), ("alpha", "", AT), ("alpha", "f", datetime(2026, 3, 2, 9, 0))],  # noqa: DTZ001 - naive on purpose
    ids=["no client", "no fingerprint", "naive time"],
)
def test_a_record_that_makes_no_sense_is_refused(make_store, client_id, fingerprint, at):
    with pytest.raises(ValueError):
        make_store().set(client_id, fingerprint=fingerprint, ok=True, at=at)


def test_an_unreadable_file_is_corrupt_and_never_guessed_at(tmp_path):
    path = tmp_path / "source_checks.json"
    for content in ("{not json", '{"format": 2, "checks": []}', '{"format": 1, "checks": [{}]}'):
        path.write_text(content, encoding="utf-8")
        store = FileSourceCheckStore(path)
        with pytest.raises(SourceCheckStoreCorruptError) as caught:
            store.get("alpha")
        assert "source_checks.json" in str(caught.value) and "{" not in str(caught.value)
        with pytest.raises(SourceCheckStoreCorruptError):
            store.set("alpha", fingerprint="f", ok=True, at=AT)


def test_a_database_that_lacks_the_table_is_unavailable_and_says_nothing_of_why(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    try:
        store = SqlSourceCheckStore(engine)
        for call in (
            lambda: store.get("alpha"),
            lambda: store.set("alpha", fingerprint="f", ok=True, at=AT),
        ):
            with pytest.raises(SourceCheckStoreUnavailableError) as caught:
                call()
            assert "sqlite" not in str(caught.value).lower()
    finally:
        engine.dispose()


def test_the_migration_creates_the_table(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    try:
        upgrade_database(engine)
        columns = {c["name"] for c in inspect(engine).get_columns("client_source_check")}
        assert columns == {"client_id", "fingerprint", "ok", "checked_at"}
    finally:
        engine.dispose()


def test_a_deployment_without_a_database_gets_the_file_fallback(tmp_path):
    settings = Settings(clients_dir=tmp_path / "clients", profiles_dir=tmp_path / "profiles")
    stores = build_account_stores(settings)
    try:
        assert isinstance(stores.source_checks, FileSourceCheckStore)
        stores.source_checks.set("alpha", fingerprint="f", ok=True, at=AT)
        assert (tmp_path / "profiles" / "source_checks.json").is_file()
    finally:
        stores.close()
