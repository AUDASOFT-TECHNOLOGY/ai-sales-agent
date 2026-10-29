"""What the agent learned, through the Client API, and the scheduled run that learns it.

SIMULATED: the messaging channel, the model, the login, the repository (all in memory).
REAL: the application, the routes, the scheduler's job, the Brain and its rules."""

from dataclasses import replace
from datetime import timedelta

import pytest
import test_client_agent_api as base
from brain_support import settings_doc
from test_client_agent_api import World

from sales_app.learning_store import InMemoryLearningStore, StoredNote, StoredReview
from sales_app.scheduler import Job
from sales_brain_kit import ConsultativeSalesBrain, parse_settings
from sales_integrations.fakes import FakeLLMProvider

URL = "/client/v1/learning"


@pytest.fixture
def stores():
    return {}


@pytest.fixture
def world(tmp_path, monkeypatch, stores):
    real = base.fake_builders

    def with_learning(clock=None):
        builders, made = real(clock)
        opened = lambda cid: stores.setdefault(cid, InMemoryLearningStore(cid))
        return replace(builders, learning=opened), made

    monkeypatch.setattr(base, "fake_builders", with_learning)
    w = World(tmp_path)
    yield w
    w.application.close()


def seed(stores, cid="alpha"):
    store = stores.setdefault(cid, InMemoryLearningStore(cid))
    store.replace_notes(
        [
            StoredNote(note_id="short_messages", code="short_messages", kind="do", theme="general",
                       state="active", evidence="medium", cases=6),
            StoredNote(note_id="doubt_ignored", code="doubt_ignored", kind="avoid", theme="think",
                       state="watching", evidence="low", cases=3),
        ]
    )  # fmt: skip
    return store


def test_every_route_needs_a_client_session(world):
    anonymous = world.anonymous()
    assert anonymous.get(URL).status_code == 401
    assert (
        anonymous.post(f"{URL}/short_messages/decision", json={"action": "off"}).status_code == 401
    )


def test_a_client_with_nothing_learned_is_told_how_many_conversations_it_takes(world):
    response = world.browser("alpha").get(URL)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "counts": {"studied": 0, "achieved": 0, "unfavourable": 0, "needed": 5},
        "notes": [],
    }


def test_what_was_learned_is_listed_active_first_and_holds_nothing_personal(world, stores):
    seed(stores)
    body = world.browser("alpha").get(URL).json()
    assert [n["id"] for n in body["notes"]] == ["short_messages", "doubt_ignored"]
    first = body["notes"][0]
    assert set(first) == {
        "id", "code", "kind", "state", "evidence", "cases", "against", "product", "confirmed",
        "off_by", "reason",
    }  # fmt: skip
    assert first["kind"] == "do" and first["cases"] == 6 and first["state"] == "active"


def test_a_client_sees_only_its_own(world, stores):
    seed(stores, "alpha")
    assert world.browser("beta").get(URL).json()["notes"] == []


def test_the_client_can_stop_follow_confirm_and_hand_back_a_point(world, stores):
    seed(stores)
    alpha = world.browser("alpha")
    off = alpha.post(f"{URL}/short_messages/decision", json={"action": "off"})
    assert (
        off.status_code == 200 and off.json()["state"] == "off" and off.json()["off_by"] == "client"
    )
    confirmed = alpha.post(f"{URL}/doubt_ignored/decision", json={"action": "confirm"}).json()
    assert confirmed["state"] == "active" and confirmed["confirmed"] is True
    back = alpha.post(f"{URL}/short_messages/decision", json={"action": "on"}).json()
    assert back["state"] == "watching" and back["off_by"] is None
    states = {n["id"]: n["state"] for n in alpha.get(URL).json()["notes"]}
    assert states == {"short_messages": "watching", "doubt_ignored": "active"}


def test_an_unknown_point_or_choice_is_refused(world, stores):
    seed(stores)
    alpha = world.browser("alpha")
    assert alpha.post(f"{URL}/nothing/decision", json={"action": "off"}).status_code == 404
    assert (
        alpha.post(f"{URL}/short_messages/decision", json={"action": "delete"}).status_code == 422
    )


def test_a_client_cannot_change_another_clients_point(world, stores):
    seed(stores, "alpha")
    assert (
        world.browser("beta")
        .post(f"{URL}/short_messages/decision", json={"action": "off"})
        .status_code
        == 404
    )
    assert world.browser("alpha").get(URL).json()["notes"][0]["state"] == "active"


def test_the_scheduled_run_turns_studied_conversations_into_rules(world, stores):
    store = stores.setdefault("alpha", InMemoryLearningStore("alpha"))
    ended = base.NOW - timedelta(days=3)
    for i in range(5):
        store.save_review(
            StoredReview(case_ref=f"w{i}", result="achieved", ended_at=ended, reviewed_at=ended,
                         confidence="high", practices=("acknowledge_first",))
        )  # fmt: skip
    for i in range(6):
        store.save_review(
            StoredReview(case_ref=f"l{i}", result="declined", ended_at=ended, reviewed_at=ended,
                         confidence="high", causes=("doubt_ignored",), practices=("doubt_ignored",))
        )  # fmt: skip
    runtime = world.application.registry.get("alpha")
    # The test double of a Brain cannot learn; give this client the real one, over a fake model.
    brain = ConsultativeSalesBrain(FakeLLMProvider(default="{}"), parse_settings(settings_doc()))
    runtime.engine.reflector = lambda: brain
    result = world.application.scheduler.run_job_now(runtime, Job.LEARN, base.NOW)
    assert result.ok
    shown = world.browser("alpha").get(URL).json()
    notes = {n["id"]: n for n in shown["notes"]}
    assert notes["doubt_ignored"]["state"] == "active" and notes["doubt_ignored"]["cases"] == 6
    assert shown["counts"] == {"studied": 11, "achieved": 5, "unfavourable": 6, "needed": 5}


def test_a_brain_that_cannot_learn_is_simply_skipped(world, stores):
    seed(stores)
    runtime = world.application.registry.get("alpha")
    result = world.application.scheduler.run_job_now(runtime, Job.LEARN, base.NOW)
    assert result.ok
    assert [n["id"] for n in world.browser("alpha").get(URL).json()["notes"]] == [
        "short_messages",
        "doubt_ignored",
    ]  # what was there stays


def test_learning_never_runs_ahead_of_the_work_that_talks_to_people(world):
    scheduler = world.application.scheduler
    runtime = world.application.registry.get("alpha")
    due = scheduler.due_jobs(runtime, base.NOW)
    assert Job.LEARN not in due or due == [Job.LEARN]
    runtime.state.last_learn_at = base.NOW
    assert Job.LEARN not in scheduler.due_jobs(runtime, base.NOW + timedelta(minutes=30))


def test_a_run_that_fails_only_delays_the_next_one_and_never_stops_the_client(world, stores):
    class Broken(InMemoryLearningStore):
        def purge(self, now):
            raise RuntimeError("disk full")

    stores["alpha"] = Broken("alpha")
    runtime = world.application.registry.get("alpha")
    result = world.application.scheduler.run_job_now(runtime, Job.LEARN, base.NOW)
    assert not result.ok and result.errors == ("learning failed",)
    assert runtime.state.last_learn_at == base.NOW and runtime.state.blocked_until is None
