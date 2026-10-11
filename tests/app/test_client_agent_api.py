"""`/client/v1/settings`, `/readiness` and `/agent`: a client's own settings, what its agent still
needs, and its own switch to start and pause it (lot A of closing step 11).

Covers: authentication and isolation between clients; the versioned settings (validation, conflicts,
what a client may and may not edit, what an Admin's other settings keep); readiness and its
reasons; start refused until ready; pause; the Admin's authority over the client's switch; the
scheduler honouring the switch; and the switch surviving a restart, in memory, in a file and in SQL.
"""

from concurrent.futures import Executor, Future
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml
from app_support import (
    InlineExecutor,
    app_secret,
    client_yaml,
    env_for,
    fake_builders,
    make_settings,
    phone_id,
    write_client,
)
from engine_harness import phone_for, prospect
from fastapi import HTTPException
from fastapi.testclient import TestClient
from kit_support import minimal
from test_admin_clients_api import NOW, PASSWORD, owner
from test_app_api import payload, post

from sales_app.admin_auth import InMemoryAdminSessionStore, InMemoryAdminUserStore
from sales_app.admin_auth.passwords import hash_password
from sales_app.agent_state import (
    AgentStateStore,
    AgentStateStoreCorruptError,
    FileAgentStateStore,
    InMemoryAgentStateStore,
)
from sales_app.api import create_app
from sales_app.api.admin_clients import (
    _wiring_and_base_document,
    publish_client_settings,
    read_client_settings,
)
from sales_app.api.client_agent import evaluate_readiness
from sales_app.bootstrap import build_application
from sales_app.client_profiles import ClientProfile, InMemoryClientProfileStore
from sales_app.config_store import InMemoryConfigStore
from sales_app.persistence import SqlAgentStateStore, create_database_engine, upgrade_database
from sales_app.scheduler import Job
from sales_core.contracts import ActionStatus, ClientId, Direction
from sales_integrations.fakes import FakeClock

SPEC_FACTORY = "app_spec_brains:create_brain"
LOGIN = "/client/v1/auth/login"
SETTINGS = "/client/v1/settings"
READINESS = "/client/v1/readiness"
AGENT = "/client/v1/agent"
START = "/client/v1/agent/start"
PAUSE = "/client/v1/agent/pause"
PASSWORDS = {"alpha": "alpha-secret-123", "beta": "beta-secret-456", "gamma": "gamma-secret-789"}
MONDAY_NINE = datetime(2026, 3, 2, 9, 0, tzinfo=ZoneInfo("Africa/Tunis"))


def write_spec_client(root, cid, **overrides):
    """A client whose Brain declares settings rules, with valid settings."""
    data = client_yaml(cid, **overrides)
    data["brain"] = {"factory": SPEC_FACTORY, "settings": minimal()}
    write_client(root, cid, raw=yaml.safe_dump(data))


def give_profile(application, cid, number, **kw):
    application.client_profiles.create(
        ClientProfile(
            client_id=cid,
            login=f"{cid}.login",
            password_hash=hash_password(PASSWORDS.get(cid, "naga-secret-789")),
            whatsapp_number=number,
            created_at=NOW,
            **kw,
        )
    )


class World:
    """alpha and beta are ready; gamma lacks its platform credentials; naga is disabled."""

    def __init__(self, tmp_path, *, agent_state=None, config_store=None, refresh_executor=None):
        self.tmp_path = tmp_path
        self.clock = FakeClock(NOW)
        self.users = InMemoryAdminUserStore()
        self.users.create(owner())
        self.agent_state = agent_state if agent_state is not None else InMemoryAgentStateStore()
        self.config_store = config_store if config_store is not None else InMemoryConfigStore()
        for cid in ("alpha", "beta", "gamma"):
            write_spec_client(tmp_path, cid)
        write_client(tmp_path, "naga", enabled=False, llm={"model": "TODO"}, brain={"settings": {}})
        builders, self.made = fake_builders(self.clock)
        self.application = build_application(
            make_settings(tmp_path),
            env=env_for("alpha", "beta"),  # gamma has no credentials: valid, but not ready
            builders=builders,
            clock=self.clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=InMemoryAdminSessionStore(),
            client_profile_store=InMemoryClientProfileStore(),
            client_session_store=InMemoryAdminSessionStore(),
            config_store=self.config_store,
            agent_state_store=self.agent_state,
            refresh_executor=refresh_executor if refresh_executor is not None else InlineExecutor(),
        )
        for index, cid in enumerate(("alpha", "beta", "gamma", "naga")):
            give_profile(self.application, cid, f"+216 20 000 00{index + 1}")

    def browser(self, cid) -> TestClient:
        client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )
        response = client.post(LOGIN, json={"login": f"{cid}.login", "password": PASSWORDS[cid]})
        assert response.status_code == 200, response.text
        return client

    def admin(self) -> TestClient:
        client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )
        response = client.post(
            "/admin/v1/auth/login", json={"username": "root", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return client

    def anonymous(self) -> TestClient:
        return TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.application.close()


@pytest.fixture
def alpha(world):
    return world.browser("alpha")


@pytest.fixture
def beta(world):
    return world.browser("beta")


def new_settings(name):
    settings = minimal()
    settings["business"]["name"] = name
    return settings


def put(client, expected, **settings):
    return client.put(SETTINGS, json={"expected_version": expected, "settings": settings})


def versions(world, cid="alpha"):
    return world.config_store.history(cid)


# ---- authentication and isolation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", SETTINGS),
        ("put", SETTINGS),
        ("get", READINESS),
        ("get", AGENT),
        ("post", START),
        ("post", PAUSE),
    ],
)
def test_every_route_needs_a_client_session(world, method, url):
    response = getattr(world.anonymous(), method)(url, **({"json": {}} if method == "put" else {}))
    assert response.status_code == 401


def test_an_admin_session_does_not_open_the_client_routes(world):
    browser = world.anonymous()
    assert browser.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})
    for url in (SETTINGS, READINESS, AGENT):
        assert browser.get(url).status_code == 401
    assert browser.post(START).status_code == 401


def test_no_route_takes_a_client_id(world, alpha):
    # who is asking comes from the session: another client's id in the path is simply not a route
    assert alpha.get("/client/v1/beta/settings").status_code == 404
    assert alpha.get(f"{SETTINGS}?client_id=beta").json()["version"] is None


def test_one_client_never_sees_or_changes_another(world, alpha, beta):
    assert put(alpha, None, commercial=new_settings("Alpha Ltd")).status_code == 200
    assert alpha.post(START).status_code == 200
    assert beta.get(SETTINGS).json()["version"] is None
    assert beta.get(AGENT).json()["state"] == "not_started"
    assert versions(world, "beta") == ()
    assert world.agent_state.get("beta") is None
    assert beta.get(SETTINGS).json()["settings"]["commercial"]["business"]["name"] == "Acme Academy"


# ---- settings -------------------------------------------------------------------------------


def test_the_client_reads_only_the_part_it_may_edit(world, alpha):
    body = alpha.get(SETTINGS).json()
    assert body["version"] is None and body["updated_at"] is None
    assert set(body["settings"]) <= {
        "timezone",
        "mode",
        "opt_out_keywords",
        "schedule",
        "commercial",
    }
    assert "brain" not in body["settings"]
    assert body["settings"]["commercial"]["business"]["name"] == "Acme Academy"
    text = str(body)
    for private in ("sheet-alpha", "test-model", "spreadsheet_id", "api_key"):
        assert private not in text


def test_a_save_publishes_a_new_version_validated_and_kept_for_the_client(world, alpha):
    response = put(alpha, None, commercial=new_settings("Alpha Ltd"))
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1 and body["updated_by"] == "client"
    assert body["settings"]["commercial"]["business"]["name"] == "Alpha Ltd"
    (stored,) = versions(world)
    assert stored.author == "client:alpha" and stored.version == 1
    assert alpha.get(SETTINGS).json() == body


def test_a_save_keeps_every_setting_a_client_does_not_own(world, alpha):
    put(alpha, None, timezone="Europe/Paris")
    document = world.config_store.current("alpha").document
    assert document["llm"]["model"] == "test-model"
    assert document["sheets"]["spreadsheet_id"] == "sheet-alpha"
    assert document["timezone"] == "Europe/Paris"
    assert document["brain"]["settings"]["business"]["name"] == "Acme Academy"


def test_a_section_is_merged_not_replaced(world, alpha):
    put(alpha, None, schedule={"days": ["mon", "tue"]})
    schedule = world.config_store.current("alpha").document["schedule"]
    assert list(schedule["days"]) == ["mon", "tue"]
    assert schedule["daily_at"] == "09:00"  # untouched
    put(alpha, 1, schedule={"daily_at": "10:30"})
    schedule = world.config_store.current("alpha").document["schedule"]
    assert schedule["daily_at"] == "10:30" and list(schedule["days"]) == ["mon", "tue"]


def test_what_an_admin_published_in_between_is_kept(world, alpha):
    assert put(alpha, None, timezone="Africa/Tunis").json()["version"] == 1
    admin_doc = {**world.config_store.current("alpha").document}
    admin_doc["llm"] = {**admin_doc["llm"], "model": "model-the-admin-assigned"}
    world.config_store.publish("alpha", admin_doc, author="admin:root", expected_parent=1)
    # the client saves on top of version 2 and cannot undo the model the Admin assigned
    response = put(alpha, 2, opt_out_keywords=["stop", "arrêt"])
    assert response.status_code == 200 and response.json()["version"] == 3
    document = world.config_store.current("alpha").document
    assert document["llm"]["model"] == "model-the-admin-assigned"
    assert list(document["opt_out_keywords"]) == ["stop", "arrêt"]


def test_a_stale_version_is_refused_and_nothing_is_written(world, alpha):
    assert put(alpha, None, timezone="Europe/Paris").status_code == 200
    stale = put(alpha, None, timezone="Asia/Tokyo")
    assert stale.status_code == 409 and "reload" in stale.json()["detail"]
    assert put(alpha, 7, timezone="Asia/Tokyo").status_code == 409
    assert [v.version for v in versions(world)] == [1]
    assert alpha.get(SETTINGS).json()["settings"]["timezone"] == "Europe/Paris"


def test_two_saves_on_the_same_version_cannot_both_win(world, alpha):
    first = put(alpha, None, timezone="Europe/Paris")
    second = put(alpha, None, timezone="Asia/Tokyo")
    assert (first.status_code, second.status_code) == (200, 409)


def test_invalid_brain_settings_are_refused_with_every_problem(world, alpha):
    bad = {"business": {"name": "x"}}  # no communication.languages
    response = put(alpha, None, commercial=bad)
    assert response.status_code == 422
    problems = response.json()["detail"]
    assert isinstance(problems, list) and any("brain.settings.communication" in p for p in problems)
    assert versions(world) == ()
    unknown = new_settings("Alpha")
    unknown["no_such_section"] = {}
    assert put(alpha, None, commercial=unknown).status_code == 422
    assert versions(world) == ()


@pytest.mark.parametrize(
    "settings",
    [
        {"timezone": "Not/AZone"},
        {"schedule": {"daily_at": "9am"}},
        {"schedule": {"days": ["someday"]}},
        {"opt_out_keywords": "stop"},
    ],
)
def test_a_value_the_schema_rejects_is_refused_and_nothing_is_written(world, alpha, settings):
    assert put(alpha, None, **settings).status_code in (422,)
    assert versions(world) == ()


@pytest.mark.parametrize(
    "settings",
    [
        {"llm": {"model": "gpt-sneaky"}},
        {"sheets": {"spreadsheet_id": "someone-elses"}},
        {"inbound": {"spontaneous_conversations": {"enabled": True}}},
        {"daily_action_limit": 100000},
        {"display_name": "Renamed"},
        {"enabled": True},
        {"mode": "autonomous"},  # the mode stays under the platform's control
        {"mode": "dry_run"},
        {"client_id": "beta"},
        {"brain": {"factory": "evil:brain", "settings": minimal()}},
        {"schedule": {"prepare_every_minutes": 5}},
    ],
)
def test_what_a_client_does_not_own_is_refused_not_ignored(world, alpha, settings):
    response = put(alpha, None, **settings)
    assert response.status_code == 422
    assert versions(world) == ()
    assert world.config_store.current("alpha") is None


def test_a_write_must_say_which_version_it_is_based_on(world, alpha):
    assert alpha.put(SETTINGS, json={"settings": {"timezone": "Europe/Paris"}}).status_code == 422
    assert alpha.put(SETTINGS, json={"expected_version": None, "settings": {}}).status_code == 422
    assert (
        alpha.put(
            SETTINGS,
            json={"expected_version": None, "settings": {"timezone": "Europe/Paris"}, "x": 1},
        ).status_code
        == 422
    )
    assert versions(world) == ()


def test_the_running_agent_takes_the_saved_settings(world, alpha):
    assert world.application.registry.get_config("alpha").config_version is None
    put(alpha, None, commercial=new_settings("Alpha Ltd"))
    config = world.application.registry.get_config("alpha")
    assert config.config_version == 1


def test_a_client_without_a_model_yet_reads_its_settings_but_cannot_publish(world):
    naga = world.application  # naga is disabled with the placeholder model: no runtime yet
    assert naga.registry.get_config("naga") is None
    version, part, record = read_client_settings(naga, "naga")
    assert (version, record) == (None, None) and part["brain"]["settings"] == {"tone": "naga"}
    with pytest.raises(HTTPException) as refused:
        publish_client_settings(
            naga, "naga", {"timezone": "Europe/Paris"}, expected_version=None, author="client:naga"
        )
    assert refused.value.status_code == 422
    assert any("llm.model" in problem for problem in refused.value.detail)
    assert world.config_store.history("naga") == ()
    # the Admin assigns a model (the order of the journey): the same save now goes through
    write_client(world.tmp_path, "naga", enabled=False, brain={"settings": {}})
    publish_client_settings(
        naga, "naga", {"timezone": "Europe/Paris"}, expected_version=None, author="client:naga"
    )
    assert read_client_settings(naga, "naga")[0] == 1


def test_a_skeleton_client_publishes_its_first_settings_complete_or_not_at_all(world):
    data = client_yaml("delta", enabled=False)
    data["brain"] = {"factory": SPEC_FACTORY, "settings": {}}  # no Brain settings written yet
    write_client(world.tmp_path, "delta", raw=yaml.safe_dump(data))
    application = world.application
    version, part, _ = read_client_settings(application, "delta")
    assert version is None and part["brain"]["settings"] == {}
    with pytest.raises(HTTPException) as partial:
        publish_client_settings(
            application,
            "delta",
            {"timezone": "Europe/Paris"},
            expected_version=None,
            author="client:delta",
        )
    assert partial.value.status_code == 422
    assert any("brain.settings" in problem for problem in partial.value.detail)
    assert world.config_store.history("delta") == ()
    publish_client_settings(
        application,
        "delta",
        {"brain": {"settings": new_settings("Delta")}},
        expected_version=None,
        author="client:delta",
    )
    version, part, record = read_client_settings(application, "delta")
    assert version == 1 and part["brain"]["settings"]["business"]["name"] == "Delta"
    assert record.author == "client:delta"


# ---- readiness ------------------------------------------------------------------------------


def check_ids(body, ok):
    return {c["id"] for c in body["checks"] if c["ok"] is ok}


def test_a_complete_client_is_ready(world, alpha):
    body = alpha.get(READINESS).json()
    assert body["ready"] is True and check_ids(body, False) == set()
    assert check_ids(body, True) == {
        "enabled_by_administrator",
        "loaded_by_platform",
        "model_assigned",
        "settings_valid",
        "platform_connections",
        "whatsapp_number",
    }
    assert all(c["message"] is None for c in body["checks"])


def test_what_is_missing_is_named_in_words_a_client_can_act_on(world):
    gamma = world.browser("gamma")
    body = gamma.get(READINESS).json()
    assert body["ready"] is False and check_ids(body, False) == {"platform_connections"}
    (message,) = [c["message"] for c in body["checks"] if not c["ok"]]
    assert "administrator" in message
    for secret in ("GAMMA_TOKEN", "token", "env", "credential", "openai"):
        assert secret.lower() not in message.lower()


def test_a_disabled_client_without_a_model_or_settings_lists_every_reason(world):
    profile = world.application.client_profiles.get("naga")
    checks = {c.id: c.ok for c in evaluate_readiness(world.application, profile)}
    assert checks["enabled_by_administrator"] is False
    assert checks["loaded_by_platform"] is False
    assert checks["model_assigned"] is False  # the placeholder "TODO" is not a model
    assert checks["settings_valid"] is False
    assert checks["whatsapp_number"] is True


def test_a_changed_whatsapp_number_not_yet_connected_blocks_the_start(world, alpha):
    profile = world.application.client_profiles.get("alpha")
    world.application.client_profiles.update(replace(profile, whatsapp_reconnect_required=True))
    body = alpha.get(READINESS).json()
    assert check_ids(body, False) == {"whatsapp_number"}
    assert alpha.post(START).status_code == 409


def test_settings_that_became_invalid_make_the_client_not_ready(world, alpha):
    # a version the Brain rejects, published behind the API's back (an operator's command)
    _raw, base = _wiring_and_base_document(world.application, "alpha")
    broken = {**base, "brain": {**base["brain"], "settings": {"business": {"name": "x"}}}}
    world.config_store.publish("alpha", broken, author="cli", expected_parent=None)
    assert check_ids(alpha.get(READINESS).json(), False) == {"settings_valid"}
    assert alpha.post(START).status_code == 409


# ---- the agent: start, pause ----------------------------------------------------------------


def test_a_new_client_has_not_started(world, alpha):
    body = alpha.get(AGENT).json()
    assert body == {
        "state": "not_started",
        "active": False,
        "ready": True,
        "changed_at": None,
        "reasons": [],
    }


def test_start_then_pause_then_start_again(world, alpha):
    started = alpha.post(START)
    assert started.status_code == 200
    assert (started.json()["state"], started.json()["active"]) == ("running", True)
    stored = world.agent_state.get("alpha")
    assert stored.running is True and stored.changed_by == "client:alpha"
    assert stored.changed_at == NOW

    paused = alpha.post(PAUSE).json()
    assert (paused["state"], paused["active"]) == ("paused", False)
    assert world.agent_state.get("alpha").running is False
    assert alpha.get(AGENT).json()["state"] == "paused"

    again = alpha.post(START).json()
    assert (again["state"], again["active"]) == ("running", True)


def test_start_and_pause_are_idempotent(world, alpha):
    alpha.post(START)
    first = world.agent_state.get("alpha")
    world.clock.advance(timedelta(minutes=5))
    assert alpha.post(START).json()["state"] == "running"
    assert world.agent_state.get("alpha") == first  # nothing rewritten
    alpha.post(PAUSE)
    paused = world.agent_state.get("alpha")
    assert alpha.post(PAUSE).json()["state"] == "paused"
    assert world.agent_state.get("alpha") == paused


def test_pausing_an_agent_that_never_started_changes_nothing(world, alpha):
    assert alpha.post(PAUSE).json()["state"] == "not_started"
    assert world.agent_state.get("alpha") is None


def test_start_is_refused_with_every_reason_until_the_client_is_ready(world):
    gamma = world.browser("gamma")
    response = gamma.post(START)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == "your agent cannot start yet"
    assert [r["id"] for r in detail["reasons"]] == ["platform_connections"]
    assert detail["reasons"][0]["ok"] is False and detail["reasons"][0]["message"]
    assert world.agent_state.get("gamma") is None
    assert gamma.get(AGENT).json()["state"] == "not_started"
    assert gamma.get(AGENT).json()["reasons"][0]["id"] == "platform_connections"


def test_a_paused_agent_that_is_no_longer_ready_cannot_be_resumed(world):
    world.application.agent_state.set("gamma", running=False, by="client:gamma", at=NOW)
    gamma = world.browser("gamma")
    assert gamma.post(START).status_code == 409
    assert world.agent_state.get("gamma").running is False


def test_a_running_agent_that_stopped_being_ready_is_not_active(world, alpha):
    alpha.post(START)
    profile = world.application.client_profiles.get("alpha")
    world.application.client_profiles.update(replace(profile, whatsapp_reconnect_required=True))
    body = alpha.get(AGENT).json()
    assert (body["state"], body["active"], body["ready"]) == ("running", False, False)


# ---- the Admin's authority ------------------------------------------------------------------


def test_a_client_cannot_switch_on_what_the_admin_switched_off(world, alpha):
    alpha.post(START)
    # the Admin disables the client: its sessions end at once and it cannot log in again
    write_spec_client(world.tmp_path, "alpha", enabled=False)
    assert alpha.post(START).status_code == 401
    assert alpha.get(AGENT).status_code == 401
    assert alpha.post(PAUSE).status_code == 401
    assert (
        world.anonymous()
        .post(LOGIN, json={"login": "alpha.login", "password": PASSWORDS["alpha"]})
        .status_code
        == 403
    )
    # and the stored switch, whatever it says, is not what enables the client
    profile = world.application.client_profiles.get("alpha")
    checks = {c.id: c.ok for c in evaluate_readiness(world.application, profile)}
    assert checks["enabled_by_administrator"] is False
    assert world.agent_state.get("alpha").running is True


def test_the_client_switch_never_writes_the_admins_enabled_flag(world, alpha):
    before = (world.tmp_path / "alpha" / "client.yaml").read_text(encoding="utf-8")
    alpha.post(START)
    alpha.post(PAUSE)
    alpha.post(START)
    assert (world.tmp_path / "alpha" / "client.yaml").read_text(encoding="utf-8") == before


def test_the_admin_stored_state_is_what_the_client_sees_as_disabled(world):
    profile = world.application.client_profiles.get("naga")
    world.application.agent_state.set("naga", running=True, by="client:naga", at=NOW)
    assert world.application.registry.get_config("naga") is None  # no runtime for a disabled client
    assert not all(c.ok for c in evaluate_readiness(world.application, profile))


# ---- the scheduler honours the switch -------------------------------------------------------


def jobs_started(world, at):
    return world.application.scheduler.tick(at)


def test_the_scheduler_works_only_for_a_client_that_started_and_did_not_pause(world, alpha):
    assert jobs_started(world, MONDAY_NINE) == []  # never started
    alpha.post(START)
    started = jobs_started(world, MONDAY_NINE)
    assert len(started) == 1
    for future in started:
        future.result()
    alpha.post(PAUSE)
    later = MONDAY_NINE.replace(hour=11)
    assert jobs_started(world, later) == []  # paused: the very next tick starts nothing
    alpha.post(START)
    assert len(jobs_started(world, later)) >= 1


def test_the_switch_of_one_client_does_not_start_another(world, alpha):
    alpha.post(START)
    futures = jobs_started(world, MONDAY_NINE)
    clients = {r.client_id for f in futures for r in f.result()}
    assert clients == {"alpha"}


def test_an_unreadable_switch_starts_nothing(world, alpha):
    alpha.post(START)

    class Broken:
        def get(self, client_id):
            raise AgentStateStoreCorruptError("unreadable")

    world.application.scheduler._may_run = lambda cid: Broken().get(cid).running
    assert jobs_started(world, MONDAY_NINE) == []


# ---- the Admin's decisions take effect without a restart ----------------------------------------


def set_status(admin, cid, status):
    response = admin.patch(f"/admin/v1/clients/{cid}", json={"status": status})
    assert response.status_code == 200, response.text
    return response


def test_an_admin_disabling_a_client_stops_its_work_without_a_restart(world, alpha):
    alpha.post(START)
    for future in jobs_started(world, MONDAY_NINE):
        future.result()
    set_status(world.admin(), "alpha", "inactive")
    assert world.application.registry.get_config("alpha") is not None  # still loaded: no restart
    assert jobs_started(world, MONDAY_NINE.replace(hour=11)) == []
    stored = world.agent_state.get("alpha")
    assert stored.running is False and stored.changed_by == "admin:root"


def test_a_client_the_admin_enabled_again_must_start_its_agent_again(world, alpha):
    alpha.post(START)
    admin = world.admin()
    set_status(admin, "alpha", "inactive")
    set_status(admin, "alpha", "active")
    later = MONDAY_NINE.replace(hour=11)
    assert jobs_started(world, later) == []  # enabled again, but nobody started it
    client = world.browser("alpha")
    body = client.get(AGENT).json()
    assert (body["state"], body["active"]) == ("paused", False)
    assert client.post(START).json()["state"] == "running"
    assert len(jobs_started(world, later)) >= 1


def test_an_admin_status_that_changes_nothing_leaves_the_clients_switch_alone(world, alpha):
    alpha.post(START)
    set_status(world.admin(), "alpha", "active")  # already active
    assert world.agent_state.get("alpha").running is True
    assert world.agent_state.get("alpha").changed_by == "client:alpha"


def test_enabling_a_client_that_never_started_does_not_invent_a_switch(world):
    admin = world.admin()
    set_status(admin, "beta", "inactive")
    set_status(admin, "beta", "active")
    assert world.agent_state.get("beta") is None


def test_the_clients_switch_does_not_switch_on_a_client_the_admin_disabled(world, alpha):
    alpha.post(START)
    write_spec_client(world.tmp_path, "alpha", enabled=False)  # disabled by hand, no restart
    assert jobs_started(world, MONDAY_NINE) == []
    assert world.agent_state.get("alpha").running is True  # the client's own decision is kept


class Inbound:
    """A prospect writes to alpha, through the real webhook."""

    def __init__(self, world):
        self.world = world
        self.repo = world.made.repositories["alpha"]
        self.repo.add_prospects(prospect("p1", client_id=ClientId("alpha")))
        self.http = TestClient(create_app(world.application, run_scheduler=False))

    def send(self, text="Tell me more", wamid="wamid.1"):
        body = payload(phone_id("alpha"), phone_for("p1").lstrip("+"), text, wamid)
        return post(self.http, body, secret=app_secret("alpha"))


def test_a_reply_is_not_processed_while_the_client_is_paused_but_the_message_is_kept(world, alpha):
    inbound = Inbound(world)
    world.application.agent_state.set("alpha", running=False, by="client:alpha", at=NOW)
    assert inbound.send().json() == {"received": 1}
    assert [m.direction for m in inbound.repo.messages] == [Direction.INBOUND]  # stored
    assert inbound.repo.list_actions() == []  # but no reply was prepared
    # started again: the stored reply is picked up by the recovery job
    alpha.post(START)
    runtime = world.application.registry.get("alpha")
    world.application.scheduler.run_job_now(runtime, Job.RECOVER, MONDAY_NINE)
    (action,) = inbound.repo.list_actions()
    assert action.status is ActionStatus.PROPOSED


def test_a_started_client_gets_its_reply_prepared_at_once(world, alpha):
    inbound = Inbound(world)
    alpha.post(START)
    inbound.send()
    assert len(inbound.repo.list_actions()) == 1


def test_a_reply_is_not_processed_for_a_client_the_admin_disabled(world, alpha):
    inbound = Inbound(world)
    alpha.post(START)
    write_spec_client(world.tmp_path, "alpha", enabled=False)
    assert inbound.send().status_code == 200
    assert len(inbound.repo.messages) == 1 and inbound.repo.list_actions() == []


def test_an_unreadable_switch_means_no_reply(world, alpha):
    inbound = Inbound(world)
    alpha.post(START)

    class Broken:
        def get(self, client_id):
            raise AgentStateStoreCorruptError("unreadable")

    world.application.agent_state = Broken()
    assert inbound.send().status_code == 200  # stored, acknowledged
    assert len(inbound.repo.messages) == 1 and inbound.repo.list_actions() == []


# ---- refreshes of the running agent ----------------------------------------------------------


class ManualExecutor(Executor):
    """Keeps what is submitted until the test runs it."""

    def __init__(self):
        self.tasks = []

    def submit(self, fn, /, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))
        return Future()

    def run_all(self):
        while self.tasks:
            fn, args, kwargs = self.tasks.pop(0)
            fn(*args, **kwargs)


def test_refreshes_asked_while_one_is_queued_are_one_refresh(tmp_path):
    executor = ManualExecutor()
    world = World(tmp_path, refresh_executor=executor)
    try:
        executor.tasks.clear()
        manager = world.application.runtime_manager
        first = manager.refresh_client("alpha")
        assert [manager.refresh_client("alpha") for _ in range(4)] == [first] * 4
        assert len(executor.tasks) == 1
        other = manager.refresh_client("beta")  # another client is its own refresh
        assert other is not first and len(executor.tasks) == 2
        executor.run_all()
        assert first.done() and first.exception() is None and other.done()
        again = manager.refresh_client("alpha")  # nothing queued any more: a new one
        assert again is not first and len(executor.tasks) == 1
        executor.run_all()
        assert again.done()
    finally:
        world.application.close()


def test_a_refresh_asked_while_one_is_running_is_queued_not_lost(tmp_path):
    executor = ManualExecutor()
    world = World(tmp_path, refresh_executor=executor)
    try:
        executor.tasks.clear()
        manager = world.application.runtime_manager
        asked_during = []
        real = manager._refresh

        def refresh_and_ask(client_ids):
            asked_during.append(manager.refresh_client("alpha"))  # settings changed meanwhile
            return real(client_ids)

        manager._refresh = refresh_and_ask
        running = manager.refresh_client("alpha")
        fn, args, kwargs = executor.tasks.pop(0)
        fn(*args, **kwargs)  # runs the first refresh only
        (queued,) = asked_during
        assert queued is not running and running.done() and not queued.done()
        assert len(executor.tasks) >= 1  # the new one waits its turn: the change is not lost
    finally:
        world.application.close()


def test_several_saves_in_a_row_leave_one_refresh_that_applies_the_last(tmp_path):
    executor = ManualExecutor()
    world = World(tmp_path, refresh_executor=executor)
    try:
        client = world.browser("alpha")
        executor.tasks.clear()
        for index, zone in enumerate(("Europe/Paris", "Asia/Tokyo", "Europe/Rome")):
            assert put(client, index or None, timezone=zone).status_code == 200
        assert len(executor.tasks) == 1  # one rebuild is queued, not three
        executor.run_all()
        config = world.application.registry.get_config("alpha")
        assert config.config_version == 3 and config.spec.timezone == "Europe/Rome"
    finally:
        world.application.close()


# ---- the switch survives a restart ----------------------------------------------------------


@pytest.fixture(params=["memory", "file", "sql"])
def agent_store(request, tmp_path):
    if request.param == "memory":
        shared = InMemoryAgentStateStore()
        yield lambda: shared
        return
    if request.param == "file":
        yield lambda: FileAgentStateStore(tmp_path / "state" / "agent_state.json")
        return
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'state.db').as_posix()}")
    upgrade_database(engine)
    yield lambda: SqlAgentStateStore(engine)
    engine.dispose()


def test_a_store_remembers_the_switch_of_each_client(agent_store, request):
    store = agent_store()
    assert isinstance(store, AgentStateStore)
    assert store.get("alpha") is None
    at = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    saved = store.set("alpha", running=True, by="client:alpha", at=at)
    store.set("beta", running=False, by="client:beta", at=at)
    assert store.get("alpha") == saved
    assert (store.get("alpha").running, store.get("beta").running) == (True, False)
    store.set("alpha", running=False, by="client:alpha", at=at.replace(hour=10))
    assert store.get("alpha").running is False and store.get("alpha").changed_at.hour == 10
    assert store.get("beta").changed_at.hour == 9 and store.get("gamma") is None
    if request.node.callspec.params["agent_store"] != "memory":
        again = agent_store()  # a new object over the same file or database: a restart
        assert again.get("alpha").running is False and again.get("beta").running is False
        assert again.get("alpha").changed_at.tzinfo is not None


def test_a_file_that_cannot_be_trusted_is_reported_not_guessed(tmp_path):
    path = tmp_path / "agent_state.json"
    path.write_text('{"format": 1, "states": [{"client_id": "a"}]}', encoding="utf-8")
    with pytest.raises(AgentStateStoreCorruptError):
        FileAgentStateStore(path).get("a")
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(AgentStateStoreCorruptError):
        FileAgentStateStore(path).get("a")
    path.write_text('{"format": 9, "states": []}', encoding="utf-8")
    with pytest.raises(AgentStateStoreCorruptError):
        FileAgentStateStore(path).get("a")
    path.write_text(
        '{"format": 1, "states": [{"client_id": "a", "running": "yes",'
        ' "changed_at": "2026-01-01T00:00:00+00:00", "changed_by": "x"}]}',
        encoding="utf-8",
    )
    with pytest.raises(AgentStateStoreCorruptError):
        FileAgentStateStore(path).get("a")


def boot(tmp_path, **settings):
    for cid in ("alpha",):
        write_spec_client(tmp_path, cid)
    builders, _made = fake_builders(FakeClock(NOW))
    return build_application(
        make_settings(tmp_path, **settings),
        env=env_for("alpha"),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
    )


def test_the_started_agent_is_still_started_after_a_restart_with_files(tmp_path):
    folders = {
        "admin_dir": tmp_path / "_admin",
        "profiles_dir": tmp_path / "_profiles",
        "config_dir": tmp_path / "_config",
    }
    first = boot(tmp_path, **folders)
    try:
        give_profile(first, "alpha", "+216 20 000 001")
        client = TestClient(create_app(first, run_scheduler=False), base_url="https://testserver")
        client.post(LOGIN, json={"login": "alpha.login", "password": PASSWORDS["alpha"]})
        assert client.post(START).status_code == 200
        assert (
            client.put(
                SETTINGS,
                json={"expected_version": None, "settings": {"timezone": "Europe/Paris"}},
            ).status_code
            == 200
        )
    finally:
        first.close()
    second = boot(tmp_path, **folders)
    try:
        client = TestClient(create_app(second, run_scheduler=False), base_url="https://testserver")
        client.post(LOGIN, json={"login": "alpha.login", "password": PASSWORDS["alpha"]})
        assert client.get(AGENT).json()["state"] == "running"
        assert client.get(SETTINGS).json()["settings"]["timezone"] == "Europe/Paris"
        assert client.get(SETTINGS).json()["version"] == 1
    finally:
        second.close()


def test_the_started_agent_is_still_started_after_a_restart_with_the_database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    engine.dispose()
    first = boot(tmp_path, database_url=url)
    try:
        give_profile(first, "alpha", "+216 20 000 001")
        client = TestClient(create_app(first, run_scheduler=False), base_url="https://testserver")
        client.post(LOGIN, json={"login": "alpha.login", "password": PASSWORDS["alpha"]})
        assert client.post(START).status_code == 200
        assert client.post(PAUSE).json()["state"] == "paused"
        assert client.post(START).json()["state"] == "running"
    finally:
        first.close()
    second = boot(tmp_path, database_url=url)
    try:
        assert second.agent_state.get("alpha").running is True
        client = TestClient(create_app(second, run_scheduler=False), base_url="https://testserver")
        client.post(LOGIN, json={"login": "alpha.login", "password": PASSWORDS["alpha"]})
        assert client.get(AGENT).json()["state"] == "running"
    finally:
        second.close()


def test_the_default_switch_is_off_so_a_restart_never_starts_a_client_by_itself(tmp_path):
    application = boot(
        tmp_path,
        admin_dir=tmp_path / "_a",
        profiles_dir=tmp_path / "_p",
        config_dir=tmp_path / "_c",
    )
    try:
        assert application.agent_state.get("alpha") is None
        assert application.scheduler.tick(MONDAY_NINE) == []
    finally:
        application.close()


# ---- nothing private in a response ----------------------------------------------------------


def test_no_response_carries_a_secret_a_credential_name_or_another_clients_data(world, alpha):
    put(alpha, None, commercial=new_settings("Alpha Ltd"))
    alpha.post(START)
    text = " ".join(str(alpha.get(url).json()) for url in (SETTINGS, READINESS, AGENT)) + str(
        alpha.post(START).json()
    )
    for private in ("ALPHA_TOKEN", "token-alpha", "app_secret", "password", "beta", "Beta"):
        assert private not in text


# ---- a failing switch store: 503 for the Client, never a block on the Admin ----------------------


@pytest.fixture(params=["corrupt_file", "sql_table_missing"])
def broken_store(request, tmp_path):
    if request.param == "corrupt_file":
        path = tmp_path / "broken" / "agent_state.json"
        path.parent.mkdir()
        path.write_text("{not json", encoding="utf-8")
        yield FileAgentStateStore(path)
        return
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'broken.db').as_posix()}")
    upgrade_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE client_agent_state")
    yield SqlAgentStateStore(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ("method", "url"),
    [("get", AGENT), ("post", START), ("post", PAUSE)],
    ids=["get", "start", "pause"],
)
def test_an_unavailable_switch_store_answers_503_on_the_agent_routes(
    world, alpha, broken_store, method, url
):
    world.application.agent_state = broken_store
    response = getattr(alpha, method)(url)
    assert response.status_code == 503
    assert response.json() == {"detail": "the agent switch is temporarily unavailable"}


def test_an_unavailable_switch_store_stops_all_work_but_not_the_other_client_routes(
    world, alpha, broken_store
):
    world.application.agent_state = broken_store
    assert alpha.get(SETTINGS).status_code == 200
    assert alpha.get(READINESS).status_code == 200
    assert world.application.agent_may_work("alpha") is False


def test_the_admin_can_disable_a_client_whose_switch_store_is_unavailable(
    world, alpha, broken_store
):
    world.application.agent_state = broken_store
    set_status(world.admin(), "alpha", "inactive")  # 200: not blocked by the store
    assert (
        yaml.safe_load((world.tmp_path / "alpha" / "client.yaml").read_text("utf-8"))["enabled"]
        is False
    )
    assert world.application.agent_may_work("alpha") is False


def test_the_admin_cannot_enable_again_on_a_switch_store_that_cannot_be_paused(
    world, alpha, broken_store
):
    admin = world.admin()
    set_status(admin, "alpha", "inactive")
    world.application.agent_state = broken_store
    response = admin.patch("/admin/v1/clients/alpha", json={"status": "active"})
    assert response.status_code == 503  # an old "started" decision must not come back to life
    assert (
        yaml.safe_load((world.tmp_path / "alpha" / "client.yaml").read_text("utf-8"))["enabled"]
        is False
    )
