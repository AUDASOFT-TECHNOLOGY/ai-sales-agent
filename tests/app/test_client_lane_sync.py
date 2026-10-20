"""`/client/v1/lanes/{lane_id}/sync`: the client chooses where the results are copied back.

Settings only: nothing here reaches a spreadsheet or HubSpot. What is proved: nothing is on until the
client names columns/properties one by one; a block saved while the deployment's switch is off is
kept and says it writes nothing; invalid choices are refused with nothing published; a change copies
results and never pauses the agent; one client's choice never reaches another's; HubSpot sources
never let the client write what the CRM's statuses mean; the connection test is invalidated when
the choice changes. Not proved: that a real Google Sheet or HubSpot account accepts the writes.
"""

import pytest
from test_client_agent_api import mark_source_tested
from test_client_lanes_api import LANES, adopt, document_of, lane, sheet, world  # noqa: F401

from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.config_store.models import thaw_document

from sales_app.api.client_agent import current_source_fingerprint

SHEET_COLUMNS = {
    "status": "Statut",
    "last_contacted_at": "Dernier contact",
    "last_inbound_at": "Derniere reponse",
}
CRM = {
    "id": "x_crm",
    "provider": "hubspot",
    "settings": {"status_mapping": {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"}},
}


def sync_url(lane_id="main"):
    return f"{LANES}/{lane_id}/sync"


@pytest.fixture
def alpha(world):  # noqa: F811
    adopt(
        world,
        "alpha",
        [sheet("x_one", "sheet-alpha-1"), CRM],
        [lane("main", "x_one", products=("a", "b")), lane("crm", "x_crm")],
    )
    return world.browser("alpha")


@pytest.fixture
def beta(world):  # noqa: F811
    return world.browser("beta")


def version(client):
    return client.get(LANES).json()["version"]


def put(client, lane_id="main", **body):
    return client.put(sync_url(lane_id), json={"expected_version": version(client), **body})


# ---- Google Sheets ------------------------------------------------------------------------------


def test_nothing_is_copied_until_the_client_names_columns(alpha):
    body = alpha.get(sync_url()).json()
    assert body["state"] == "off" and body["supported"] is True
    assert body["google_sheets"] is None
    assert {r["key"] for r in body["results"]} >= {"status", "last_contacted_at", "next_action_type"}


def test_columns_are_saved_in_the_source_and_copying_is_permanent(world, alpha):
    response = put(alpha, google_sheets={"columns": SHEET_COLUMNS})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "configured" and "deployment_enabled" not in body
    assert body["google_sheets"]["columns"] == SHEET_COLUMNS
    stored = next(s for s in document_of(world)["sources"] if s["id"] == "x_one")
    assert stored["settings"]["writeback"] == {"columns": SHEET_COLUMNS}
    assert stored["settings"]["spreadsheet_id"] == "sheet-alpha-1"


def test_the_client_has_no_way_to_switch_copying_off(world, alpha):
    put(alpha, google_sheets={"columns": SHEET_COLUMNS})
    assert put(alpha, enabled=False).status_code == 422  # no such option any more
    stored = next(s for s in document_of(world)["sources"] if s["id"] == "x_one")
    assert stored["settings"]["writeback"] == {"columns": SHEET_COLUMNS}


@pytest.mark.parametrize(
    "columns",
    [
        {"next_action_type": "Action"},  # the next action is named whole or not at all
        {"status": "Statut", "last_contacted_at": "Statut"},  # one column for two results
    ],
)
def test_an_incoherent_choice_is_refused_and_nothing_is_published(world, alpha, columns):
    before = len(world.config_store.history("alpha"))
    response = put(alpha, google_sheets={"columns": columns})
    assert response.status_code == 422
    assert len(world.config_store.history("alpha")) == before


@pytest.mark.parametrize(
    "body",
    [
        {"google_sheets": {"columns": {}}},  # at least one result
        {"google_sheets": {"columns": {"email": "Email"}}},  # not a result Revansa copies
        {"google_sheets": {"columns": {"status": "S"}}, "hubspot": {"status": True}},
        {"hubspot": {"status": True}},  # not this source's block
        {},  # nothing to save
    ],
)
def test_a_malformed_or_foreign_request_is_refused(alpha, body):
    assert put(alpha, **body).status_code == 422


def test_a_stale_version_is_refused(alpha):
    stale = version(alpha)
    assert put(alpha, google_sheets={"columns": SHEET_COLUMNS}).status_code == 200
    r = alpha.put(
        sync_url(), json={"expected_version": stale, "google_sheets": {"columns": SHEET_COLUMNS}}
    )
    assert r.status_code == 409


def test_copying_results_never_pauses_a_running_agent(world, alpha):
    world.application.agent_state.set(
        "alpha", running=True, by="client:alpha", at=world.clock.now()
    )
    assert put(alpha, google_sheets={"columns": SHEET_COLUMNS}).status_code == 200
    assert alpha.get("/client/v1/agent").json()["state"] == "running"


def test_changing_the_columns_invalidates_the_connection_test(world, alpha):
    mark_source_tested(world.application, "alpha")
    before = current_source_fingerprint(world.application, "alpha")
    put(alpha, google_sheets={"columns": SHEET_COLUMNS})
    assert current_source_fingerprint(world.application, "alpha") != before


def test_one_clients_choice_never_reaches_another(world, alpha, beta):
    put(alpha, google_sheets={"columns": SHEET_COLUMNS})
    other = beta.get(sync_url()).json()
    assert other["state"] == "off" and other["google_sheets"] is None
    stored = next(s for s in document_of(world, "beta")["sources"] if s["id"] == "y_one")
    assert "writeback" not in stored["settings"]


def test_an_unknown_lane_is_a_404_and_needs_a_session(world, alpha):
    assert alpha.get(sync_url("nope")).status_code == 404
    assert put(alpha, "nope", google_sheets={"columns": SHEET_COLUMNS}).status_code == 404
    anonymous = world.admin()
    anonymous.cookies.clear()
    assert anonymous.get(sync_url()).status_code == 401


# ---- HubSpot -------------------------------------------------------------------------------------


def test_a_hubspot_source_shows_its_status_words_before_any_copy(alpha):
    body = alpha.get(sync_url("crm")).json()
    assert body["source"] == "hubspot" and body["state"] == "off"
    assert "status_mapping" not in body["hubspot"]
    assert body["hubspot"]["status"] is False and body["hubspot"]["properties"] == {}


def test_hubspot_properties_and_status_are_saved_as_the_providers_block(world, alpha):
    response = put(
        alpha,
        "crm",
        hubspot={
            "status": True,
            "properties": {"last_contacted_at": "revansa_last_contact"},
            "propagate_opt_out": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured"
    stored = next(s for s in document_of(world)["sources"] if s["id"] == "x_crm")
    assert stored["settings"]["writeback"] == {
        "status": True,
        "properties": {"last_contacted_at": "revansa_last_contact"},
    }
    assert stored["settings"]["status_mapping"] == CRM["settings"]["status_mapping"]


def test_the_client_can_never_write_what_its_hubspot_statuses_mean(world, alpha):
    before = len(world.config_store.history("alpha"))
    for forged in (
        {"status_mapping": {"NEW": "new"}},
        {"status_property": "hs_lead_status"},
    ):
        assert put(alpha, "crm", hubspot=forged).status_code == 422
    assert len(world.config_store.history("alpha")) == before


@pytest.mark.parametrize(
    "hubspot",
    [
        {"properties": {"last_contacted_at": "email"}},  # an identity property is never written
        {"properties": {"next_action_type": "revansa_next"}},  # the next action is whole or nothing
        {"status": True, "properties": {"follow_up_count": "hs_lead_status"}},  # the status property
        {"status": True, "propagate_opt_out": True},  # no HubSpot word for an opt-out
        {"properties": {"last_contacted_at": "same", "last_inbound_at": "same"}},
    ],
)
def test_incoherent_hubspot_choices_are_refused_and_nothing_is_published(world, alpha, hubspot):
    before = len(world.config_store.history("alpha"))
    assert put(alpha, "crm", hubspot=hubspot).status_code == 422
    assert len(world.config_store.history("alpha")) == before


def test_status_copy_is_refused_when_the_admins_mapping_has_no_single_word_for_a_status(
    world, alpha
):
    raw, base = client_wiring_and_document(world.application, "alpha")
    document = thaw_document(base)
    crm = next(x for x in document["sources"] if x["id"] == "x_crm")
    crm["settings"]["status_mapping"] = {
        "NEW": "new",
        "BAD_TIMING": "paused",
        "ON_HOLD": "paused",
        "UNQUALIFIED": "closed",
    }
    current = world.config_store.current("alpha")
    world.config_store.publish(
        "alpha", document, author="admin:root", note="x", expected_parent=current.version
    )
    assert put(alpha, "crm", hubspot={"status": True}).status_code == 422


def test_hubspot_settings_do_not_apply_to_a_spreadsheet_lane(alpha):
    assert put(alpha, "main", hubspot={"status": True}).status_code == 422
    assert put(alpha, "crm", google_sheets={"columns": SHEET_COLUMNS}).status_code == 422


# ---- what the client saves is what the runtime reads ---------------------------------------------


def test_the_saved_block_is_exactly_what_the_runtime_providers_read(world, alpha):
    from sales_app.sourcing import ProspectField
    from sales_app.wiring.providers import google_sheets, hubspot

    put(alpha, google_sheets={"columns": SHEET_COLUMNS, "propagate_opt_out": True})
    put(
        alpha,
        "crm",
        hubspot={"status": True, "properties": {"follow_up_count": "revansa_followups"}},
    )
    sources = {s["id"]: s["settings"] for s in document_of(world)["sources"]}
    fields, opt_out = google_sheets.writeback_of(sources["x_one"])
    assert fields == {
        ProspectField.STATUS,
        ProspectField.LAST_CONTACTED_AT,
        ProspectField.LAST_INBOUND_AT,
    }
    assert opt_out is True
    fields, opt_out = hubspot.writeback_of(sources["x_crm"])
    assert fields == {ProspectField.STATUS, ProspectField.FOLLOW_UP_COUNT} and opt_out is False
