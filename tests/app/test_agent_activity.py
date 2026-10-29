"""The agent's live activity: migration 0027, the SQL and in-memory stores, the recorder, the Core's
`doing`, and how a stale step is shown."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect

from sales_app.agent_activity import (
    ABANDONED,
    DONE,
    FAILED,
    RUNNING,
    STALE_AFTER,
    ActivityRecorder,
    InMemoryActivityStore,
    settled,
)
from sales_app.api.client_live import live_response
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.agent_activity_rows import SqlActivityStore
from sales_app.persistence.schema import current_revision
from sales_core.ports import activity, doing

AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
def engine():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return engine


def stores(engine):
    return [InMemoryActivityStore("alpha"), SqlActivityStore(engine, "alpha")]


def test_0027_adds_the_activity_table_and_nothing_else():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0026")
    before = set(inspect(engine).get_table_names())
    assert "op_agent_activity" not in before
    upgrade_database(engine, "0027")
    assert current_revision(engine) == "0027"
    assert set(inspect(engine).get_table_names()) - before == {"op_agent_activity"}


def test_a_step_runs_then_ends(engine):
    for store in stores(engine):
        seq = store.begin("thinking", prospect_id="p1", name="Ana", detail=None, at=AT)
        [running] = store.latest(10)
        assert (running.state, running.name, running.ended_at) == (RUNNING, "Ana", None)
        store.end(seq, ok=True, at=AT + timedelta(seconds=3))
        [done] = store.latest(10)
        assert (done.state, done.ended_at) == (DONE, AT + timedelta(seconds=3))


def test_a_failed_step_is_kept_as_failed(engine):
    for store in stores(engine):
        seq = store.begin("sending", prospect_id=None, name=None, detail="email", at=AT)
        store.end(seq, ok=False, at=AT)
        assert store.latest(1)[0].state == FAILED


def test_newest_first_and_limited(engine):
    for store in stores(engine):
        for kind in ("planning", "thinking", "sending"):
            store.begin(kind, prospect_id=None, name=None, detail=None, at=AT)
        assert [s.kind for s in store.latest(2)] == ["sending", "thinking"]


def test_a_client_never_sees_another_clients_steps(engine):
    mine, other = SqlActivityStore(engine, "alpha"), SqlActivityStore(engine, "beta")
    mine.begin("thinking", prospect_id=None, name=None, detail=None, at=AT)
    assert other.latest(10) == []


def test_old_steps_are_forgotten_on_the_next_begin(engine):
    store = SqlActivityStore(engine, "alpha")
    store.begin("planning", prospect_id=None, name=None, detail=None, at=AT)
    store.begin("thinking", prospect_id=None, name=None, detail=None, at=AT + timedelta(days=3))
    assert [s.kind for s in store.latest(10)] == ["thinking"]


def test_a_step_nobody_finished_is_shown_abandoned():
    store = InMemoryActivityStore("alpha")
    store.begin("thinking", prospect_id=None, name=None, detail=None, at=AT)
    [step] = store.latest(1)
    assert settled(step, AT + STALE_AFTER - timedelta(seconds=1)).state == RUNNING
    assert settled(step, AT + STALE_AFTER + timedelta(seconds=1)).state == ABANDONED
    answer = live_response([step], AT + timedelta(seconds=5))
    assert [s.id for s in answer.now] == [step.seq]
    assert live_response([step], AT + STALE_AFTER * 2).now == []


def test_doing_tells_the_start_and_the_end_and_lets_an_error_through():
    store = InMemoryActivityStore("alpha")
    reporter = ActivityRecorder(store)
    with doing(reporter, activity.THINKING, prospect_id="p1", name="Ana"):
        assert store.latest(1)[0].state == RUNNING
    assert store.latest(1)[0].state == DONE
    with pytest.raises(ValueError), doing(reporter, activity.SENDING):
        raise ValueError("boom")
    assert store.latest(1)[0].state == FAILED


def test_a_reporter_that_fails_never_changes_the_work():
    class Broken:
        def begin(self, kind, **_):
            raise RuntimeError("down")

        def end(self, step, **_):
            raise RuntimeError("down")

    with doing(Broken(), activity.PLANNING):
        result = 1 + 1
    assert result == 2
