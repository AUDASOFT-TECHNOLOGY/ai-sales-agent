"""`GET/PUT /admin/v1/clients/{client_id}/source`: what the operator decides about a client's HubSpot
account (the CRM's status property and what each of its values means), and the readiness it gates.

All of it runs on doubles (an in-memory config store, a fake HubSpot over `httpx.MockTransport`,
fake builders): nothing here reaches a real HubSpot account or a real database. The fake builders
wire no HubSpot connector, so `platform_connections` stays unmet in this world; what is proved is
that the operator's settings are validated, published and applied, and that a client is told who
must act before it can start, not that a real HubSpot agent runs.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_client_source_api)

import pytest
from test_client_agent_api import READINESS, START, check_ids
from test_client_source_api import (  # noqa: F401 - fixtures
    TEST,
    TOKEN,
    alpha,
    choose,
    give_token,
    history,
    world,
)

SOURCE = "/admin/v1/clients/alpha/source"
MAPPING = {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"}
SETTINGS = {"status_property": "hs_lead_status", "status_mapping": MAPPING}


def source_message(client):
    checks = client.get(READINESS).json()["checks"]
    return next(c["message"] for c in checks if c["id"] == "source_connected")


def test_it_needs_an_admin_session(world):
    anonymous = world.anonymous()
    assert anonymous.get(SOURCE).status_code == 401
    assert anonymous.put(SOURCE, json=SETTINGS).status_code == 401


def test_a_client_that_chose_hubspot_has_nothing_decided_yet(world, alpha):
    choose(alpha, "hubspot")
    body = world.admin().get(SOURCE).json()
    assert body == {"provider": "hubspot", "configured": False, "settings": None}


def test_the_operator_settles_the_mapping_and_the_clients_choice_survives(world, alpha):
    choose(alpha, "hubspot")
    response = world.admin().put(SOURCE, json=SETTINGS)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert response.json()["settings"]["status_mapping"] == MAPPING
    document = dict(world.config_store.current("alpha").document)
    assert document["source"]["provider"] == "hubspot"
    assert document["source"]["settings"]["status_mapping"] == MAPPING
    assert document["llm"]["model"] == "test-model"  # nothing else moved
    assert world.config_store.current("alpha").author == "admin:root"
    assert world.admin().get(SOURCE).json()["configured"] is True


def test_it_is_refused_while_the_client_has_not_chosen_hubspot(world):
    response = world.admin().put(SOURCE, json=SETTINGS)
    assert response.status_code == 409 and not history(world)


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"status_mapping": {}},
        {"status_mapping": {"NEW": "active"}},
        {"status_mapping": {"X": "closed"}},  # nothing maps to new
        {"status_mapping": MAPPING, "unknown_key": "x"},
        {"status_mapping": MAPPING, "status_property": "Bad Name"},
    ],
)
def test_a_mapping_that_is_not_acceptable_publishes_nothing(world, alpha, settings):
    choose(alpha, "hubspot")
    response = world.admin().put(SOURCE, json=settings)
    assert response.status_code == 422
    assert len(history(world)) == 1  # only the client's own choice


def test_an_unknown_client_is_a_404(world):
    assert world.admin().put("/admin/v1/clients/nobody/source", json=SETTINGS).status_code == 404


def test_the_client_is_told_the_administrator_must_act_until_the_mapping_exists(world, alpha):
    choose(alpha, "hubspot")
    give_token(world)
    assert alpha.post(TEST).json()["ok"] is True
    body = alpha.get(READINESS).json()
    assert "source_connected" in check_ids(body, False)
    assert "Contact your administrator" in source_message(alpha)
    assert TOKEN not in str(body)
    assert alpha.post(START).status_code == 409


def test_once_the_mapping_exists_the_source_check_passes_but_not_what_is_simulated(world, alpha):
    choose(alpha, "hubspot")
    give_token(world)
    world.admin().put(SOURCE, json=SETTINGS)
    # settings changed the document, not the connection: the client's test still counts
    assert alpha.post(TEST).json()["ok"] is True
    failing = check_ids(alpha.get(READINESS).json(), False)
    assert "source_connected" not in failing
    assert failing == {"platform_connections"}  # no HubSpot connector in the fake builders
