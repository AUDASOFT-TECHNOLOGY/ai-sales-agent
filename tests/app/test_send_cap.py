"""The mandatory daily send cap, steps 1 and 2: the shared control, the SQL counter, the stored cap
and migration 0011. The channels (Gmail, the dedicated mailbox) are not wired to it yet.

Everything is a double: SQLite in a temporary file (separate engines stand for separate processes)
or in memory. Nothing here proves that PostgreSQL behaves the same way.
"""

import threading
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.schema import current_revision
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.persistence.tables import metadata
from sales_app.send_cap import (
    CAP_NOT_CONFIGURED,
    CAP_REACHED,
    CAP_UNAVAILABLE,
    DailySendGuard,
    InMemorySendCapStore,
    InMemorySendCounter,
    InvalidSendCapError,
    Refusal,
    Reservation,
    SendCapChannel,
)
from sales_core.contracts import Channel, SendResult, SendStatus

DAY = date(2026, 10, 17)
AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
NEW_TABLES = {"client_send_caps", "op_send_counter"}


class Clock:
    def __init__(self, now=AT):
        self.value = now

    def now(self):
        return self.value


def migrated(url):
    engine = create_database_engine(url)
    upgrade_database(engine)
    return engine


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'ops.db'}"


@pytest.fixture(params=["sql", "memory"])
def counter(request, db_url):
    return SqlSendCounter(migrated(db_url)) if request.param == "sql" else InMemorySendCounter()


@pytest.fixture(params=["sql", "memory"])
def cap_store(request, db_url):
    return SqlSendCapStore(migrated(db_url)) if request.param == "sql" else InMemorySendCapStore()


def guard(counter, limit=3, client="alpha", channel="email", clock=None):
    return DailySendGuard(client, channel, lambda: limit, clock or Clock(), counter)


# ---- migration 0011 ---------------------------------------------------------------------------


class TestMigration0011:
    def test_it_adds_the_two_tables_and_nothing_else_on_top_of_0010(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0010")
        before = set(inspect(engine).get_table_names())
        upgrade_database(engine, "0011")
        assert current_revision(engine) == "0011"
        after = set(inspect(engine).get_table_names())
        assert after - before == NEW_TABLES
        assert after - {"alembic_version"} <= set(metadata.tables)

    def test_it_follows_0010_and_the_earlier_revisions_are_untouched_in_the_chain(self):
        from alembic.script import ScriptDirectory

        from sales_app.persistence.schema import _config

        script = ScriptDirectory.from_config(_config())
        chain = [script.get_revision(r).down_revision for r in ("0011", "0010", "0009")]
        assert chain == ["0010", "0009", "0008"]

    def test_downgrade_removes_only_its_tables_and_upgrade_brings_them_back(self):
        from alembic import command

        from sales_app.persistence.schema import _config

        engine = create_database_engine("sqlite://")
        upgrade_database(engine)
        config = _config()
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0010")
        remaining = set(inspect(engine).get_table_names())
        assert not remaining & NEW_TABLES and "op_mail_cursor" in remaining
        assert current_revision(engine) == "0010"
        upgrade_database(engine)
        assert NEW_TABLES <= set(inspect(engine).get_table_names())

    def test_the_columns_hold_a_count_and_a_cap_and_nothing_of_any_message(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine)
        counter_columns = {c["name"] for c in inspect(engine).get_columns("op_send_counter")}
        cap_columns = {c["name"] for c in inspect(engine).get_columns("client_send_caps")}
        assert counter_columns == {"client_id", "channel", "day", "used", "updated_at"}
        assert cap_columns == {"client_id", "daily_cap", "updated_at", "updated_by"}
        assert inspect(engine).get_pk_constraint("op_send_counter")["constrained_columns"] == [
            "client_id",
            "channel",
            "day",
        ]


# ---- the counter: reservation, cap, isolation, day --------------------------------------------


class TestCounter:
    def test_places_are_taken_up_to_the_cap_then_refused(self, counter):
        assert [counter.try_increment("alpha", "email", DAY, 3) for _ in range(5)] == [
            True,
            True,
            True,
            False,
            False,
        ]
        assert counter.used("alpha", "email", DAY) == 3

    def test_a_place_given_back_can_be_taken_again_and_never_goes_below_zero(self, counter):
        counter.try_increment("alpha", "email", DAY, 1)
        counter.decrement("alpha", "email", DAY)
        assert counter.used("alpha", "email", DAY) == 0
        counter.decrement("alpha", "email", DAY)
        counter.decrement("alpha", "email", DAY)
        assert counter.used("alpha", "email", DAY) == 0
        assert counter.try_increment("alpha", "email", DAY, 1) is True

    def test_clients_channels_and_days_never_share_a_count(self, counter):
        assert counter.try_increment("alpha", "email", DAY, 1)
        assert not counter.try_increment("alpha", "email", DAY, 1)
        assert counter.try_increment("beta", "email", DAY, 1)
        assert counter.try_increment("alpha", "telegram", DAY, 1)
        assert counter.try_increment("alpha", "email", DAY + timedelta(days=1), 1)
        assert counter.used("beta", "email", DAY) == 1
        assert counter.used("alpha", "email", DAY) == 1

    def test_an_invalid_limit_takes_nothing(self, counter):
        for limit in (0, -1, True, "5", 2.0, None):
            assert counter.try_increment("alpha", "email", DAY, limit) is False
        assert counter.used("alpha", "email", DAY) == 0


class TestAtomicReservation:
    CAP = 7

    def run(self, counters, workers=12, attempts=6):
        results, lock, barrier = [], threading.Lock(), threading.Barrier(workers)

        def work(index):
            mine = counters[index % len(counters)]
            barrier.wait()
            outcome = [mine.try_increment("alpha", "email", DAY, self.CAP) for _ in range(attempts)]
            with lock:
                results.extend(outcome)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results

    def test_concurrent_threads_never_take_more_places_than_the_cap(self, db_url):
        counters = [SqlSendCounter(migrated(db_url))]
        results = self.run(counters)
        assert results.count(True) == self.CAP
        assert counters[0].used("alpha", "email", DAY) == self.CAP

    def test_separate_engines_on_one_database_share_the_cap_like_separate_processes(self, db_url):
        first = SqlSendCounter(migrated(db_url))
        second = SqlSendCounter(create_database_engine(db_url))
        results = self.run([first, second])
        assert results.count(True) == self.CAP
        assert second.used("alpha", "email", DAY) == self.CAP

    def test_the_in_memory_double_holds_the_same_guarantee(self):
        results = self.run([InMemorySendCounter()])
        assert results.count(True) == self.CAP


class TestPersistence:
    def test_the_count_survives_closing_and_reopening_the_store(self, db_url):
        first_engine = migrated(db_url)
        first = SqlSendCounter(first_engine)
        first.try_increment("alpha", "email", DAY, 2)
        first.try_increment("alpha", "email", DAY, 2)
        first_engine.dispose()
        reopened = SqlSendCounter(create_database_engine(db_url))
        assert reopened.used("alpha", "email", DAY) == 2
        assert reopened.try_increment("alpha", "email", DAY, 2) is False

    def test_the_cap_survives_closing_and_reopening_the_store(self, db_url):
        engine = migrated(db_url)
        SqlSendCapStore(engine).set("alpha", 40, by="admin:root", at=AT)
        engine.dispose()
        assert SqlSendCapStore(create_database_engine(db_url)).get("alpha") == 40


# ---- the guard: UTC day, no cap, store down ---------------------------------------------------


class TestGuard:
    def test_the_count_starts_again_at_midnight_utc(self, counter):
        clock = Clock(datetime(2026, 10, 17, 23, 59, tzinfo=UTC))
        g = guard(counter, limit=1, clock=clock)
        assert isinstance(g.reserve(), Reservation)
        assert g.reserve() == Refusal(CAP_REACHED)
        clock.value = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
        assert g.reserve() == Reservation(date(2026, 10, 18))

    def test_the_day_is_the_utc_day_whatever_the_zone_of_the_clock(self, counter):
        plus_one = timezone(timedelta(hours=1))
        late_utc = Clock(datetime(2026, 10, 18, 0, 30, tzinfo=plus_one))  # 23:30 UTC on the 17th
        assert guard(counter, clock=late_utc).reserve() == Reservation(date(2026, 10, 17))

    @pytest.mark.parametrize("value", [None, 0, -3, True, "10", 2.5])
    def test_no_valid_cap_refuses_and_takes_nothing(self, counter, value):
        g = guard(counter, limit=value)
        assert g.reserve() == Refusal(CAP_NOT_CONFIGURED)
        assert counter.used("alpha", "email", DAY) == 0

    def test_a_cap_that_cannot_be_read_refuses(self, counter):
        def broken():
            raise RuntimeError("store down")

        g = DailySendGuard("alpha", "email", broken, Clock(), counter)
        assert g.reserve() == Refusal(CAP_UNAVAILABLE)

    def test_a_database_that_cannot_be_reached_refuses_and_says_nothing_of_it(self, tmp_path):
        engine = create_database_engine(f"sqlite:///{tmp_path / 'missing' / 'ops.db'}")
        g = guard(SqlSendCounter(engine))
        assert g.reserve() == Refusal(CAP_UNAVAILABLE)

    def test_the_store_error_is_a_fixed_sentence(self, tmp_path):
        from sales_app.sourcing.errors import StoreUnavailableError

        engine = create_database_engine(f"sqlite:///{tmp_path / 'missing' / 'ops.db'}")
        with pytest.raises(StoreUnavailableError) as error:
            SqlSendCounter(engine).try_increment("alpha", "email", DAY, 5)
        assert "alpha" not in str(error.value) and "missing" not in str(error.value)
        with pytest.raises(StoreUnavailableError):
            SqlSendCapStore(engine).get("alpha")


# ---- the channel wrapper ----------------------------------------------------------------------


class Inner:
    channel = Channel.EMAIL

    def __init__(self, outcome="ok"):
        self.outcome = outcome
        self.calls = 0

    def send(self, message):
        self.calls += 1
        if self.outcome == "raise":
            raise TimeoutError("the connection dropped after the message was handed over")
        status = SendStatus.ACCEPTED if self.outcome == "ok" else SendStatus.FAILED_TRANSIENT
        return SendResult(
            status=status, timestamp=AT, error_message=None if status.name == "ACCEPTED" else "x"
        )


MESSAGE = SimpleNamespace()


class TestSendCapChannel:
    def test_a_send_within_the_cap_goes_through_and_uses_a_place(self, counter):
        inner = Inner()
        channel = SendCapChannel(inner, guard(counter))
        assert channel.send(MESSAGE).ok and inner.calls == 1
        assert channel.channel is Channel.EMAIL
        assert counter.used("alpha", "email", DAY) == 1

    def test_nothing_reaches_the_inner_channel_without_a_valid_cap(self, counter):
        inner = Inner()
        result = SendCapChannel(inner, guard(counter, limit=None)).send(MESSAGE)
        assert not result.ok and result.error_code == CAP_NOT_CONFIGURED and inner.calls == 0

    def test_nothing_reaches_the_inner_channel_once_the_cap_is_reached(self, counter):
        inner = Inner()
        channel = SendCapChannel(inner, guard(counter, limit=1))
        assert channel.send(MESSAGE).ok
        refused = channel.send(MESSAGE)
        assert refused.error_code == CAP_REACHED and inner.calls == 1
        assert refused.retryable

    def test_nothing_is_sent_when_the_database_is_down(self, tmp_path):
        inner = Inner()
        engine = create_database_engine(f"sqlite:///{tmp_path / 'missing' / 'ops.db'}")
        result = SendCapChannel(inner, guard(SqlSendCounter(engine))).send(MESSAGE)
        assert result.error_code == CAP_UNAVAILABLE and inner.calls == 0

    def test_a_certain_failure_gives_the_place_back(self, counter):
        channel = SendCapChannel(Inner("failed"), guard(counter, limit=1))
        assert not channel.send(MESSAGE).ok
        assert counter.used("alpha", "email", DAY) == 0

    def test_an_ambiguous_failure_keeps_the_place_and_still_raises(self, counter):
        channel = SendCapChannel(Inner("raise"), guard(counter, limit=1))
        with pytest.raises(TimeoutError):
            channel.send(MESSAGE)
        assert counter.used("alpha", "email", DAY) == 1

    def test_a_place_that_cannot_be_given_back_stays_used_without_hiding_the_failure(self):
        class FlakyCounter(InMemorySendCounter):
            def decrement(self, client_id, channel, day):
                raise RuntimeError("store down")

        flaky = FlakyCounter()
        result = SendCapChannel(Inner("failed"), guard(flaky, limit=2)).send(MESSAGE)
        assert not result.ok and flaky.used("alpha", "email", DAY) == 1

    def test_two_wrappers_of_one_client_share_the_same_count(self, counter):
        first = SendCapChannel(Inner(), guard(counter, limit=2))
        second = SendCapChannel(Inner(), guard(counter, limit=2))
        assert first.send(MESSAGE).ok and second.send(MESSAGE).ok
        assert first.send(MESSAGE).error_code == CAP_REACHED
        assert second.send(MESSAGE).error_code == CAP_REACHED


# ---- the stored cap ---------------------------------------------------------------------------


class TestCapStore:
    def test_there_is_no_cap_until_one_is_set(self, cap_store):
        assert cap_store.get("alpha") is None

    def test_a_cap_is_set_replaced_and_removed(self, cap_store):
        cap_store.set("alpha", 20, by="admin:root", at=AT)
        assert cap_store.get("alpha") == 20
        cap_store.set("alpha", 35, by="admin:root", at=AT)
        assert cap_store.get("alpha") == 35
        assert cap_store.remove("alpha") is True and cap_store.get("alpha") is None
        assert cap_store.remove("alpha") is False

    def test_clients_have_their_own_cap(self, cap_store):
        cap_store.set("alpha", 20, by="admin:root", at=AT)
        assert cap_store.get("beta") is None
        cap_store.set("beta", 5, by="admin:root", at=AT)
        assert (cap_store.get("alpha"), cap_store.get("beta")) == (20, 5)

    @pytest.mark.parametrize("value", [0, -1, True, "10", 2.5, None])
    def test_only_a_whole_number_of_at_least_one_is_stored(self, cap_store, value):
        with pytest.raises(InvalidSendCapError):
            cap_store.set("alpha", value, by="admin:root", at=AT)
        assert cap_store.get("alpha") is None

    def test_the_stored_cap_feeds_the_guard(self, cap_store, counter):
        cap_store.set("alpha", 1, by="admin:root", at=AT)
        g = DailySendGuard("alpha", "email", lambda: cap_store.get("alpha"), Clock(), counter)
        assert isinstance(g.reserve(), Reservation)
        assert g.reserve() == Refusal(CAP_REACHED)
        assert DailySendGuard(
            "beta", "email", lambda: cap_store.get("beta"), Clock(), counter
        ).reserve() == Refusal(CAP_NOT_CONFIGURED)
