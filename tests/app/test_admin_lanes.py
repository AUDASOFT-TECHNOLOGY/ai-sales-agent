"""`POST /admin/v1/clients/{id}/lanes/adopt`: the step that lets a client run lanes (per-source channel,
products and results copied back). Settings only; nothing reaches a spreadsheet or HubSpot."""

import pytest
from test_client_agent_api import World
from test_client_lanes_api import LANES, adopt, document_of, lane, sheet

from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore

ADOPT = "/admin/v1/clients/{}/lanes/adopt"
MAPPING = {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=InMemorySecretAudit())
    yield w
    w.application.close()


def version(world, cid="alpha"):
    return world.config_store.current(cid).version if world.config_store.current(cid) else None


def test_the_current_source_and_channel_become_the_first_lane(world):
    alpha = world.browser("alpha")
    assert alpha.get(LANES).json()["editable"] is False
    response = world.admin().post(ADOPT.format("alpha"), json={"expected_version": version(world)})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lane"] == "main" and body["source"] == "google_sheets"
    document = document_of(world)
    assert [s["id"] for s in document["sources"]] == ["main"]
    assert document["lanes"][0]["products"] == ["ALL"] and "source" not in document
    lanes = alpha.get(LANES).json()
    assert lanes["editable"] is True and [lane_["id"] for lane_ in lanes["lanes"]] == ["main"]
    # ... and the client can now choose where results go
    assert alpha.get(f"{LANES}/main/sync").json()["supported"] is True


def test_it_is_done_once_and_only_for_the_client_named(world):
    admin = world.admin()
    assert admin.post(ADOPT.format("alpha"), json={"expected_version": version(world)}).status_code == 201
    again = admin.post(ADOPT.format("alpha"), json={"expected_version": version(world)})
    assert again.status_code == 409
    assert world.config_store.current("beta") is None  # another client is untouched
    assert admin.post(ADOPT.format("nobody"), json={"expected_version": None}).status_code == 404


def test_a_stale_version_or_an_unknown_channel_changes_nothing(world):
    admin = world.admin()
    before = len(world.config_store.history("alpha"))
    assert admin.post(ADOPT.format("alpha"), json={"expected_version": 99}).status_code == 409
    bad = admin.post(ADOPT.format("alpha"), json={"expected_version": version(world), "channel": "nope"})
    assert bad.status_code == 422
    assert len(world.config_store.history("alpha")) == before


def test_a_client_that_has_run_needs_an_explicit_confirmation_and_is_paused(world):
    world.application.agent_state.set("alpha", running=True, by="client:alpha", at=world.clock.now())
    admin = world.admin()
    refused = admin.post(ADOPT.format("alpha"), json={"expected_version": version(world)})
    assert refused.status_code == 409 and "new ids" in refused.text
    ok = admin.post(
        ADOPT.format("alpha"), json={"expected_version": version(world), "confirm_new_ids": True}
    )
    assert ok.status_code == 201
    assert world.browser("alpha").get("/client/v1/agent").json()["state"] != "running"


def test_a_client_session_cannot_do_it(world):
    alpha = world.browser("alpha")
    assert alpha.post(ADOPT.format("alpha"), json={"expected_version": None}).status_code == 401


def test_saving_the_hubspot_mapping_keeps_what_the_client_chose_to_copy_back(world):
    crm = {"id": "x_crm", "provider": "hubspot", "settings": {"status_mapping": MAPPING}}
    adopt(world, "alpha", [sheet("x_one", "s1"), crm], [lane("main", "x_one"), lane("crm", "x_crm")])
    alpha = world.browser("alpha")
    v = alpha.get(LANES).json()["version"]
    put = alpha.put(
        f"{LANES}/crm/sync",
        json={"expected_version": v, "hubspot": {"status": True, "properties": {"follow_up_count": "revansa_followups"}}},
    )
    assert put.status_code == 200, put.text
    wider = {"status_mapping": {**MAPPING, "OPEN": "new"}}
    saved = world.admin().put("/admin/v1/clients/alpha/source", json=wider)
    assert saved.status_code == 200, saved.text
    settings = next(s for s in document_of(world)["sources"] if s["id"] == "x_crm")["settings"]
    assert settings["status_mapping"]["OPEN"] == "new"
    assert settings["writeback"] == {"status": True, "properties": {"follow_up_count": "revansa_followups"}}
