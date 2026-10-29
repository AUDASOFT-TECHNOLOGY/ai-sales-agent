"""The Engine tells what it is doing as it does it (`sales_core.ports.activity`)."""

from engine_harness import ScriptedBrain, build, prospect, send
from factories import make_client_context

from sales_core.approval import ApprovalMode
from sales_core.engine import Engine, EngineConfig


class Journal:
    """A reporter that remembers each step and what was running when it began."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def begin(self, kind, *, prospect_id=None, name=None, detail=None):
        self.steps.append(
            {"kind": kind, "prospect_id": prospect_id, "name": name, "detail": detail, "ended": None}
        )
        return len(self.steps) - 1

    def end(self, step, *, ok=True, detail=None):
        self.steps[step]["ended"] = "ok" if ok else "failed"

    def kinds(self):
        return [s["kind"] for s in self.steps]


def engine_with(journal: Journal, brain: ScriptedBrain, mode=ApprovalMode.AUTONOMOUS):
    h = build(prospects=[prospect("p1")], brain=brain, mode=mode)
    engine = Engine(
        client=make_client_context(),
        repository=h.repo,
        brain=brain,
        channel=h.channel,
        clock=h.clock,
        config=EngineConfig(mode=mode),
        activity_reporter=journal,
    )
    return engine, h


def test_a_cycle_tells_reading_planning_thinking_and_sending_in_order():
    journal = Journal()
    engine, _ = engine_with(journal, ScriptedBrain(lambda r: send("Hi!")))
    engine.run_cycle()
    assert journal.kinds() == [
        "reading_prospects",
        "planning",
        "checking",
        "reading_history",
        "thinking",
        "sending",
    ]
    thinking = journal.steps[4]
    assert thinking["prospect_id"] == "p1" and thinking["name"]
    assert all(s["ended"] == "ok" for s in journal.steps)


def test_the_brain_is_asked_while_the_step_is_still_running():
    journal = Journal()
    seen = []

    def ask(request):
        seen.append(journal.steps[-1]["kind"] + ":" + str(journal.steps[-1]["ended"]))
        return send("Hi!")

    engine, _ = engine_with(journal, ScriptedBrain(ask))
    engine.prepare()
    assert seen == ["thinking:None"]


def test_a_brain_that_fails_leaves_a_failed_step_and_the_engine_goes_on():
    journal = Journal()

    def boom(request):
        raise RuntimeError("model down")

    engine, _ = engine_with(journal, ScriptedBrain(boom))
    report = engine.prepare()
    assert report.errors
    assert journal.steps[-1]["kind"] == "thinking" and journal.steps[-1]["ended"] == "failed"


def test_a_reporter_that_fails_changes_nothing():
    class Broken:
        def begin(self, kind, **_):
            raise RuntimeError("down")

        def end(self, step, **_):
            raise RuntimeError("down")

    engine, h = engine_with(Broken(), ScriptedBrain(lambda r: send("Hi!")))  # type: ignore[arg-type]
    assert engine.run_cycle().ok
    assert len(h.channel.sent) == 1
