"""The scheduler decides WHEN the Engine runs, never what it does."""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app_support import InlineExecutor, SpyEngine, build_test_application
from factories import NOW

from sales_app.scheduler import BACKOFF_MAX_SECONDS, Job, Scheduler
from sales_integrations.fakes import FakeClock

TUNIS = ZoneInfo("Africa/Tunis")  # UTC+1, no daylight saving
THURSDAY = datetime(2026, 1, 15, tzinfo=TUNIS)


def at(day_offset: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A moment in Tunis local time, counted from Thursday 15 January 2026."""
    day = THURSDAY + timedelta(days=day_offset)
    return day.replace(hour=hour, minute=minute, second=second)


def setup(tmp_path, *ids, **overrides):
    application, _, clock = build_test_application(tmp_path, *ids, **overrides)
    spies = {}
    for runtime in application.registry.runtimes():
        spies[runtime.client_id] = runtime.engine = SpyEngine()
    return application, spies, clock


def tick(scheduler: Scheduler, now: datetime):
    return [result for future in scheduler.tick(now) for result in future.result()]


def jobs_of(results, cid="alpha"):
    return [r.job for r in results if r.client_id == cid]


class TestDailyCycle:
    def test_runs_at_the_configured_local_time_not_before(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        assert Job.DAILY not in jobs_of(tick(app.scheduler, at(0, 8, 59, 59)))
        assert jobs_of(tick(app.scheduler, at(0, 9, 0))) == [Job.DAILY]

    def test_the_daily_cycle_recovers_then_prepares_then_dispatches(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        tick(app.scheduler, at(0, 9, 0))
        assert spies["alpha"].calls == ["recover", "prepare", "dispatch"]

    def test_runs_once_per_local_day(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha", schedule={"prepare_every_minutes": 1440})
        tick(app.scheduler, at(0, 9, 0))
        for hour in (9, 12, 18, 23):
            assert Job.DAILY not in jobs_of(tick(app.scheduler, at(0, hour, 59)))
        assert spies["alpha"].calls.count("prepare") == 1
        assert Job.DAILY in jobs_of(tick(app.scheduler, at(1, 9, 0)))  # Friday
        assert spies["alpha"].calls.count("prepare") == 2

    def test_a_late_start_catches_up_the_same_day(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        assert jobs_of(tick(app.scheduler, at(0, 15, 30))) == [Job.DAILY]

    def test_the_client_timezone_decides_the_day_boundary(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        tick(app.scheduler, at(0, 9, 0))
        # 23:30 UTC on Thursday is already 00:30 Friday in Tunis: a new day, before 09:00
        late_utc = datetime(2026, 1, 15, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert Job.DAILY not in jobs_of(tick(app.scheduler, late_utc))
        assert Job.DAILY in jobs_of(tick(app.scheduler, at(1, 9, 0)))

    def test_the_daily_time_is_per_client(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha", schedule={"daily_at": "07:00"})
        assert jobs_of(tick(app.scheduler, at(0, 7, 0))) == [Job.DAILY]


class TestDays:
    def test_sunday_is_not_scheduled_by_default(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        sunday = at(3, 10)  # 18 January 2026
        assert sunday.weekday() == 6
        results = tick(app.scheduler, sunday)
        assert Job.DAILY not in jobs_of(results) and Job.DISPATCH not in jobs_of(results)
        assert "prepare" not in spies["alpha"].calls and "dispatch" not in spies["alpha"].calls

    def test_monday_through_saturday_run(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        # Thursday 15 Jan .. Saturday 17 Jan, then Monday 19 Jan
        for offset in (0, 1, 2, 4):
            assert Job.DAILY in jobs_of(tick(app.scheduler, at(offset, 9, 0))), offset

    def test_recovering_replies_still_happens_on_sunday(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        assert jobs_of(tick(app.scheduler, at(3, 10))) == [Job.RECOVER]

    def test_days_are_configurable_per_client(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha", schedule={"days": ["sun"]})
        assert Job.DAILY not in jobs_of(tick(app.scheduler, at(0, 9, 0)))  # Thursday
        assert Job.DAILY in jobs_of(tick(app.scheduler, at(3, 9, 0)))  # Sunday


class TestIntervals:
    def test_dispatch_and_recovery_follow_their_own_intervals(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        tick(app.scheduler, at(0, 9, 0))  # daily cycle: also counts as a dispatch and a recovery
        assert tick(app.scheduler, at(0, 9, 4, 59)) == []
        assert jobs_of(tick(app.scheduler, at(0, 9, 5))) == [Job.DISPATCH]
        assert jobs_of(tick(app.scheduler, at(0, 9, 15))) == [Job.RECOVER, Job.DISPATCH]

    def test_dispatch_waits_for_the_working_day_to_open(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        assert jobs_of(tick(app.scheduler, at(0, 6, 0))) == [Job.RECOVER]  # before 09:00
        assert tick(app.scheduler, at(0, 6, 5)) == []
        assert "dispatch" not in spies["alpha"].calls

    def test_intervals_are_per_client(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha", "beta")
        tick(app.scheduler, at(0, 10, 0))
        assert spies["alpha"].calls == spies["beta"].calls == ["recover", "prepare", "dispatch"]


class TestIntradayPreparation:
    """Later planning passes pick up prospects added to the sheet and follow-ups falling due."""

    def test_prepares_again_an_hour_after_the_daily_cycle(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        tick(app.scheduler, at(0, 9, 0))
        assert Job.PREPARE not in jobs_of(tick(app.scheduler, at(0, 9, 59)))
        # the 09:59 tick just ran recovery and dispatch, so only the planning pass is due
        assert jobs_of(tick(app.scheduler, at(0, 10, 0))) == [Job.PREPARE]
        assert spies["alpha"].calls.count("prepare") == 2

    def test_a_planning_pass_only_plans(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        app.scheduler.run_job_now(app.registry.get("alpha"), Job.PREPARE, at(0, 11, 0))
        assert spies["alpha"].calls == ["prepare"]

    def test_passes_are_spaced_by_the_configured_interval(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha", schedule={"prepare_every_minutes": 30})
        tick(app.scheduler, at(0, 9, 0))
        assert Job.PREPARE in jobs_of(tick(app.scheduler, at(0, 9, 30)))
        assert Job.PREPARE not in jobs_of(tick(app.scheduler, at(0, 9, 59)))
        assert Job.PREPARE in jobs_of(tick(app.scheduler, at(0, 10, 0)))

    def test_never_on_a_day_off_or_before_the_working_day_opens(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        assert Job.PREPARE not in jobs_of(tick(app.scheduler, at(0, 6, 0)))
        assert Job.PREPARE not in jobs_of(tick(app.scheduler, at(3, 12, 0)))  # Sunday
        assert "prepare" not in spies["alpha"].calls

    def test_a_failing_pass_backs_off_and_does_not_redo_the_daily_cycle(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        tick(app.scheduler, at(0, 9, 0))
        spies["alpha"].fail_on.add("prepare")
        results = tick(app.scheduler, at(0, 10, 0))
        assert [r.ok for r in results if r.job is Job.PREPARE] == [False]
        assert Job.DAILY not in jobs_of(tick(app.scheduler, at(0, 10, 1)))
        assert app.registry.get("alpha").health.snapshot()["status"] == "degraded"

    def test_the_scheduler_only_ever_asks_the_engine(self, tmp_path):
        """What a pass selects is the Planner's decision, however often the scheduler asks."""
        app, spies, _ = setup(tmp_path, "alpha")
        for hour in range(9, 18):
            tick(app.scheduler, at(0, hour, 0))
        assert set(spies["alpha"].calls) == {"recover", "prepare", "dispatch"}


class TestOverlapAndStagger:
    def test_a_client_is_never_run_twice_at_once(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha")
        runtime = app.registry.get("alpha")
        assert runtime.state.try_begin()  # a previous job is still running
        assert tick(app.scheduler, at(0, 9, 0)) == []
        runtime.state.end()
        assert jobs_of(tick(app.scheduler, at(0, 9, 0))) == [Job.DAILY]

    def test_the_busy_flag_is_released_even_when_the_job_fails(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        spies["alpha"].fail_on.add("prepare")
        tick(app.scheduler, at(0, 9, 0))
        assert app.registry.get("alpha").state.try_begin()

    def test_clients_open_a_few_seconds_apart_to_spare_api_quotas(self, tmp_path):
        app, _spies, _ = setup(tmp_path, "alpha", "beta")
        first = tick(app.scheduler, at(0, 9, 0))
        assert jobs_of(first, "alpha") == [Job.DAILY]
        assert jobs_of(first, "beta") == [Job.RECOVER]  # only the daily opening is staggered
        second = tick(app.scheduler, at(0, 9, 0, 5))
        assert jobs_of(second, "beta") == [Job.DAILY] and jobs_of(second, "alpha") == []


class TestFailureIsolation:
    def test_one_clients_failure_does_not_stop_the_others(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha", "beta")
        spies["alpha"].fail_on.add("prepare")
        tick(app.scheduler, at(0, 9, 0))
        results = tick(app.scheduler, at(0, 9, 0, 5))
        assert [(r.client_id, r.ok) for r in results] == [("beta", True)]
        assert spies["beta"].calls[-3:] == ["recover", "prepare", "dispatch"]
        health = {e["client_id"]: e for e in app.registry.health()}
        assert health["alpha"]["status"] == "degraded" and health["beta"]["status"] == "ready"

    def test_a_failing_job_is_recorded_and_the_client_backs_off(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        spies["alpha"].fail_on.add("prepare")
        (result,) = tick(app.scheduler, at(0, 9, 0))
        assert not result.ok and "prepare exploded" in result.errors[0]
        snapshot = app.registry.get("alpha").health.snapshot()
        assert snapshot["status"] == "degraded" and snapshot["consecutive_failures"] == 1
        assert "prepare exploded" in snapshot["last_error"]
        assert tick(app.scheduler, at(0, 9, 0, 29)) == []  # still backing off
        assert jobs_of(tick(app.scheduler, at(0, 9, 0, 30))) == [Job.DAILY]  # retried

    def test_the_daily_cycle_is_retried_until_it_succeeds_then_not_again(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        spies["alpha"].fail_on.add("prepare")
        tick(app.scheduler, at(0, 9, 0))
        spies["alpha"].fail_on.clear()
        (result,) = tick(app.scheduler, at(0, 9, 1))
        assert result.ok
        assert app.registry.get("alpha").health.snapshot()["status"] == "ready"
        assert app.registry.get("alpha").health.snapshot()["consecutive_failures"] == 0
        assert Job.DAILY not in jobs_of(tick(app.scheduler, at(0, 12, 0)))

    def test_backoff_doubles_up_to_a_cap(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        spies["alpha"].fail_on.add("prepare")
        moment, delays = at(0, 9, 0), []
        for _ in range(9):
            tick(app.scheduler, moment)
            blocked = app.registry.get("alpha").state.blocked_until
            delays.append(int((blocked - moment).total_seconds()))
            moment = blocked
        assert delays == [30, 60, 120, 240, 480, 900, 900, 900, 900]
        assert max(delays) == BACKOFF_MAX_SECONDS

    def test_engine_reported_problems_are_surfaced_but_are_not_a_failure(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        spies["alpha"].problems["prepare"] = ("p2: model refused",)
        (result,) = tick(app.scheduler, at(0, 9, 0))
        assert result.ok and result.errors == ("p2: model refused",)
        snapshot = app.registry.get("alpha").health.snapshot()
        assert snapshot["status"] == "ready" and snapshot["last_error"] == "p2: model refused"
        assert Job.DAILY not in jobs_of(
            tick(app.scheduler, at(0, 12, 0))
        )  # the day still counts as done

    def test_failures_are_logged_with_the_client_id(self, tmp_path, caplog):
        app, spies, _ = setup(tmp_path, "alpha")
        spies["alpha"].fail_on.add("prepare")
        with caplog.at_level("ERROR"):
            tick(app.scheduler, at(0, 9, 0))
        assert any(getattr(r, "client_id", None) == "alpha" for r in caplog.records)


class TestSchedulerOnlyTriggers:
    def test_it_asks_the_engine_and_nothing_else(self, tmp_path):
        """SpyEngine has only recover/prepare/dispatch: choosing prospects is impossible from here."""
        app, spies, _ = setup(tmp_path, "alpha")
        tick(app.scheduler, at(0, 9, 0))
        tick(app.scheduler, at(0, 9, 15))
        assert set(spies["alpha"].calls) == {"recover", "prepare", "dispatch"}

    def test_modes_do_not_change_what_the_scheduler_triggers(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha", mode="dry_run")
        tick(app.scheduler, at(0, 9, 0))
        assert spies["alpha"].calls == [
            "recover",
            "prepare",
            "dispatch",
        ]  # the Engine handles dry_run

    def test_run_job_now_executes_synchronously(self, tmp_path):
        app, spies, _ = setup(tmp_path, "alpha")
        runtime = app.registry.get("alpha")
        result = app.scheduler.run_job_now(runtime, Job.DISPATCH, at(0, 9, 0))
        assert result.ok and spies["alpha"].calls == ["dispatch"]


class TestExecution:
    def test_the_default_executor_runs_clients_on_worker_threads(self, tmp_path):
        app, _spies, clock = setup(tmp_path, "alpha", "beta")
        scheduler = Scheduler(app.registry, clock)  # no executor injected: a real thread pool
        futures = scheduler.tick(at(0, 10, 0))
        assert len(futures) == 2
        assert all(r.ok for future in futures for r in future.result(timeout=5))
        scheduler.shutdown()

    def test_the_loop_ticks_until_stopped(self, tmp_path):
        app, spies, clock = setup(tmp_path, "alpha")
        clock.set(at(0, 10, 0))
        scheduler = Scheduler(app.registry, clock, executor=InlineExecutor())

        async def go():
            stop = asyncio.Event()
            task = asyncio.create_task(scheduler.run(stop, 0.01))
            await asyncio.sleep(0.1)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(go())
        assert spies["alpha"].calls[:3] == ["recover", "prepare", "dispatch"]

    def test_a_crashing_tick_does_not_kill_the_loop(self, tmp_path):
        app, _spies, clock = setup(tmp_path, "alpha")
        scheduler = Scheduler(app.registry, clock, executor=InlineExecutor())
        ticks = []

        def flaky_tick(now=None):
            ticks.append(1)
            raise RuntimeError("clock exploded")

        scheduler.tick = flaky_tick  # type: ignore[method-assign]

        async def go():
            stop = asyncio.Event()
            task = asyncio.create_task(scheduler.run(stop, 0.01))
            await asyncio.sleep(0.1)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(go())
        assert len(ticks) > 1


def test_scheduler_uses_the_injected_clock_when_no_time_is_given(tmp_path):
    app, _spies, _ = setup(tmp_path, "alpha")
    clock = FakeClock(NOW)  # 09:00 UTC = 10:00 in Tunis on a Thursday
    scheduler = Scheduler(app.registry, clock, executor=InlineExecutor())
    (future,) = scheduler.tick()
    assert future.result()[0].job is Job.DAILY
