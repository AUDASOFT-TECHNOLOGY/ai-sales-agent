"""`ClientRuntime.lease` / `stage`: a replacement Engine is installed only when nothing is using the
current one, so a client never has two Engines working at once, and work that has started always
finishes on the Engine it started with."""

import random
import threading
import time
from dataclasses import dataclass

import pytest

from sales_app.errors import ClientNotReadyError
from sales_app.wiring import ClientRuntime


@dataclass
class Config:
    client_id: str = "alpha"
    missing_credentials: tuple[str, ...] = ()


class Tracker:
    """Records every moment two different Engines were in use at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_use: dict[str, int] = {}
        self.overlaps = 0

    def enter(self, name: str) -> None:
        with self._lock:
            self._in_use[name] = self._in_use.get(name, 0) + 1
            if sum(1 for n in self._in_use.values() if n) > 1:
                self.overlaps += 1

    def leave(self, name: str) -> None:
        with self._lock:
            self._in_use[name] -= 1


class Engine:
    def __init__(self, name: str, tracker: Tracker | None = None) -> None:
        self.name = name
        self.tracker = tracker

    def work(self, seconds: float = 0.0) -> str:
        if self.tracker:
            self.tracker.enter(self.name)
        try:
            time.sleep(seconds)
            return self.name
        finally:
            if self.tracker:
                self.tracker.leave(self.name)


def runtime(engine=None, config=None) -> ClientRuntime:
    return ClientRuntime(config=config or Config(), engine=engine or Engine("old"))


def in_thread(fn):
    box: dict = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as error:  # noqa: BLE001
            box["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


class TestIdle:
    def test_a_lease_yields_the_current_engine(self):
        old = Engine("old")
        with runtime(old).lease() as engine:
            assert engine is old

    def test_staging_on_an_idle_runtime_installs_at_once(self):
        r, called = runtime(), []
        new_config = Config(client_id="alpha")
        assert r.stage(new_config, Engine("new"), lambda: called.append(1)) is True
        assert r.engine.name == "new" and r.config is new_config
        assert called == [1] and not r.replacement_pending
        with r.lease() as engine:
            assert engine.name == "new"

    def test_state_and_health_survive_a_replacement(self):
        r = runtime()
        state, health = r.state, r.health
        r.stage(Config(), Engine("new"))
        assert r.state is state and r.health is health

    def test_a_failing_callback_does_not_break_the_install(self):
        r = runtime()

        def boom():
            raise RuntimeError("listener failed")

        assert r.stage(Config(), Engine("new"), boom) is True
        assert r.engine.name == "new"


class TestWhileWorkIsRunning:
    def test_the_running_work_finishes_on_the_old_engine_then_the_new_one_installs(self):
        r, called = runtime(), []
        started, release = threading.Event(), threading.Event()
        used = []

        def job():
            with r.lease() as engine:
                started.set()
                release.wait(5)
                used.append(engine.name)

        thread, _ = in_thread(job)
        assert started.wait(5)

        assert r.stage(Config(), Engine("new"), lambda: called.append(1)) is False
        assert r.engine.name == "old" and r.replacement_pending and called == []

        release.set()
        thread.join(5)
        assert used == ["old"]  # it finished on the Engine it started with
        assert r.engine.name == "new" and called == [1]

    def test_new_work_waits_while_draining_and_then_gets_the_new_engine(self):
        r = runtime()
        started, release = threading.Event(), threading.Event()

        def holder():
            with r.lease():
                started.set()
                release.wait(5)

        holder_thread, _ = in_thread(holder)
        assert started.wait(5)
        r.stage(Config(), Engine("new"))

        got = []

        def newcomer():
            with r.lease() as engine:
                got.append(engine.name)

        waiter, _ = in_thread(newcomer)
        time.sleep(0.2)
        assert got == []  # still held back: the old work has not ended
        release.set()
        holder_thread.join(5)
        waiter.join(5)
        assert got == ["new"]

    def test_after_relax_new_work_goes_on_with_the_old_engine_and_the_swap_stays_queued(self):
        r, called = runtime(), []
        started, release = threading.Event(), threading.Event()

        def holder():
            with r.lease():
                started.set()
                release.wait(5)

        holder_thread, _ = in_thread(holder)
        assert started.wait(5)
        r.stage(Config(), Engine("new"), lambda: called.append(1))
        r.relax()

        with r.lease() as engine:  # not held back any more
            assert engine.name == "old"
        assert r.replacement_pending and called == []

        release.set()
        holder_thread.join(5)
        assert r.engine.name == "new" and called == [1] and not r.replacement_pending

    def test_a_queued_swap_is_installed_by_the_next_lease_if_the_runtime_is_idle(self):
        r = runtime()
        gate = threading.Event()

        def holder():
            with r.lease():
                gate.wait(5)

        t, _ = in_thread(holder)
        time.sleep(0.1)
        r.stage(Config(), Engine("new"))
        r.relax()
        gate.set()
        t.join(5)
        assert r.engine.name == "new"

    def test_a_newer_replacement_supersedes_an_older_one(self):
        r = runtime()
        release, started = threading.Event(), threading.Event()

        def holder():
            with r.lease():
                started.set()
                release.wait(5)

        t, _ = in_thread(holder)
        started.wait(5)
        first, second = [], []
        r.stage(Config(), Engine("first"), lambda: first.append(1))
        r.stage(Config(), Engine("second"), lambda: second.append(1))
        release.set()
        t.join(5)
        assert r.engine.name == "second" and first == [] and second == [1]


class TestNeverTwoEngines:
    def test_many_threads_and_many_swaps_never_overlap_two_engines(self):
        tracker = Tracker()
        r = runtime(Engine("e0", tracker))
        stop = threading.Event()
        errors = []

        def worker():
            while not stop.is_set():
                try:
                    with r.lease() as engine:
                        engine.work(random.random() * 0.003)
                except Exception as error:  # noqa: BLE001
                    errors.append(error)

        workers = [threading.Thread(target=worker) for _ in range(8)]
        for w in workers:
            w.start()
        for i in range(1, 40):
            r.stage(Config(), Engine(f"e{i}", tracker))
            time.sleep(0.004)
            if i % 3 == 0:
                r.relax()  # sometimes give up draining, as a timed-out refresh does
        stop.set()
        for w in workers:
            w.join(10)
        assert not any(w.is_alive() for w in workers)
        assert errors == []
        assert tracker.overlaps == 0


class TestNotReady:
    def test_a_suspended_client_takes_no_new_work(self):
        r = runtime()
        r.suspend(Config(missing_credentials=("OpenAI API key is not configured",)))
        assert r.suspended and r.missing == ("OpenAI API key is not configured",)
        with pytest.raises(ClientNotReadyError, match="OpenAI API key"), r.lease():
            pass

    def test_running_work_finishes_after_a_suspension(self):
        r = runtime()
        started, release = threading.Event(), threading.Event()
        used = []

        def job():
            with r.lease() as engine:
                started.set()
                release.wait(5)
                used.append(engine.name)

        t, _ = in_thread(job)
        started.wait(5)
        r.suspend(Config(missing_credentials=("x",)))
        release.set()
        t.join(5)
        assert used == ["old"]

    def test_staging_a_ready_replacement_resumes_the_client(self):
        r = runtime()
        r.suspend(Config(missing_credentials=("x",)))
        r.stage(Config(), Engine("new"))
        assert not r.suspended
        with r.lease() as engine:
            assert engine.name == "new"

    def test_suspending_drops_a_queued_replacement(self):
        r = runtime()
        started, release = threading.Event(), threading.Event()

        def holder():
            with r.lease():
                started.set()
                release.wait(5)

        t, _ = in_thread(holder)
        started.wait(5)
        r.stage(Config(), Engine("new"))
        r.suspend(Config(missing_credentials=("x",)))
        assert not r.replacement_pending
        release.set()
        t.join(5)
        assert r.engine.name == "old" and r.suspended

    def test_an_exception_inside_a_lease_still_releases_it(self):
        r = runtime()
        with pytest.raises(ValueError), r.lease():
            raise ValueError("job failed")
        assert r.stage(Config(), Engine("new")) is True  # nothing is left holding it
