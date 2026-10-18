"""E7: the sources and channels of a client's lanes, judged one by one.

Covers: a Google Sheets lane tested and ready; a HubSpot lane whose mapping is the Admin's (the
client gives only the token, never the mapping; an absent or invalid mapping blocks and publishes
nothing); readiness and the connection test in lane mode (every source on its own settings, every
channel on its own requirements, a result that stops counting when a source changes); the historical
`/source` routes for a client that runs lanes; and that nothing one client or lane does reaches
another.

Both outside services are doubles (HubSpot over `httpx.MockTransport`, Google Sheets through a fake
probe): nothing here reaches a real account.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_client_source_api)

import pytest
from test_client_agent_api import READINESS
from test_client_lanes_api import GLOBAL, LANES, adopt, document_of, lane, sheet, standard
from test_client_source_api import (  # noqa: F401 - fixtures
    CREDENTIALS,
    SOURCE,
    TEST,
    TOKEN,
    alpha,
    beta,
    give_token,
    history,
    stored_token,
    world,
)

ADMIN_SOURCE = "/admin/v1/clients/alpha/source"
CHANNEL = "/client/v1/channel"
MAPPING = {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"}
SETTINGS = {"status_property": "hs_lead_status", "status_mapping": MAPPING}
MAIL = {
    "from_address": "sales@example.org",
    "username": "sales@example.org",
    "smtp_host": "smtp.example.org",
    "imap_host": "imap.example.org",
}


@pytest.fixture
def lanes_world(world):
    standard(world, "alpha", "x")
    standard(world, "beta", "y")
    return world


def checks(client):
    return {c["id"]: c for c in client.get(READINESS).json()["checks"]}


def source_message(client):
    return checks(client)["source_connected"]["message"]


def version(client):
    return client.get(LANES).json()["version"]


def hubspot_lane(client, lane_id="crm", source_id="x_crm", products=("c",)):
    return client.post(
        LANES,
        json={
            "expected_version": version(client),
            "id": lane_id,
            "channel": "whatsapp",
            "products": list(products),
            "source": {"id": source_id, "provider": "hubspot"},
        },
    )


def allow_email(world):
    assert world.admin().put(GLOBAL, json={"allowed": ["whatsapp", "email"]}).status_code == 200


# ---- Google Sheets lanes ---------------------------------------------------------------------


def test_a_lane_client_is_not_blocked_by_the_historical_source(lanes_world, alpha):
    # the client's legacy test (made in the World) no longer says anything: its sources changed
    assert checks(alpha)["source_connected"]["ok"] is False
    assert "not been tested" in source_message(alpha)
    assert alpha.post(TEST).json()["ok"] is True
    assert checks(alpha)["source_connected"]["ok"] is True
    assert alpha.get(READINESS).json()["ready"] is True


def test_the_test_reads_every_source_with_its_own_settings(lanes_world, alpha):
    body = alpha.post(TEST).json()
    assert body["source"] == "lanes" and body["ok"] is True
    assert [(r["lane"], r["ok"]) for r in body["lanes"]] == [("main", True), ("second", True)]
    assert lanes_world.net.sheets_calls == ["sheet-alpha-1", "sheet-alpha-2"]
    assert lanes_world.net.hubspot_requests == []


def test_one_failing_source_fails_the_test_and_names_its_lane_only(lanes_world, alpha):
    alpha.put(
        f"{LANES}/second",
        json={"expected_version": version(alpha), "google_sheets": {"prospects_tab": "Gone"}},
    )
    body = alpha.post(TEST).json()
    assert body["ok"] is False and body["message"].endswith("second")
    assert [(r["lane"], r["ok"]) for r in body["lanes"]] == [("main", True), ("second", False)]
    assert checks(alpha)["source_connected"]["ok"] is False
    assert "failed" in source_message(alpha)
    assert alpha.get(READINESS).json()["ready"] is False


def test_a_source_changed_after_the_test_must_be_tested_again(lanes_world, alpha):
    assert alpha.post(TEST).json()["ok"] is True
    assert alpha.get(SOURCE).json()["last_test"]["current"] is True
    alpha.put(
        f"{LANES}/main",
        json={"expected_version": version(alpha), "google_sheets": {"spreadsheet": "other"}},
    )
    assert alpha.get(SOURCE).json()["last_test"]["current"] is False
    assert "not been tested" in source_message(alpha)
    assert alpha.post(TEST).json()["ok"] is True
    assert alpha.get(READINESS).json()["ready"] is True


def test_a_change_of_products_keeps_the_test_valid(lanes_world, alpha):
    assert alpha.post(TEST).json()["ok"] is True
    alpha.put(f"{LANES}/main", json={"expected_version": version(alpha), "products": ["c"]})
    assert alpha.get(READINESS).json()["ready"] is True


def test_a_lane_without_a_spreadsheet_is_not_ready_and_says_which(lanes_world, alpha):
    adopt(
        lanes_world,
        "alpha",
        [{"id": "x_one", "provider": "google_sheets"}, sheet("x_two", "s2")],
        [lane("main", "x_one"), lane("second", "x_two")],
    )
    assert "'main'" in source_message(alpha) and "spreadsheet" in source_message(alpha)


def test_the_test_is_not_throttled_across_clients_and_each_keeps_its_own(lanes_world, alpha, beta):
    assert alpha.post(TEST).json()["ok"] is True
    assert "not been tested" in source_message(beta)  # alpha's test says nothing of beta's lanes
    assert beta.post(TEST).json()["ok"] is True
    assert lanes_world.net.sheets_calls == [
        "sheet-alpha-1",
        "sheet-alpha-2",
        "sheet-beta-1",
        "sheet-beta-2",
    ]


# ---- HubSpot lanes: the mapping is the Admin's -----------------------------------------------


def test_a_hubspot_lane_waits_for_its_token_then_for_the_admin(lanes_world, alpha):
    assert hubspot_lane(alpha).status_code == 201
    assert "'crm'" in source_message(alpha)
    assert "being connected by the platform" in source_message(alpha)
    assert "token" not in source_message(alpha).lower()  # nothing the client has to type
    give_token(lanes_world)  # the platform connects the CRM account
    assert stored_token(lanes_world) == TOKEN
    assert "platform side" in source_message(alpha)
    assert "administrator" in source_message(alpha)


def test_the_admin_settles_the_mapping_of_the_lane_that_uses_hubspot(lanes_world, alpha):
    assert hubspot_lane(alpha).status_code == 201
    before = document_of(lanes_world)
    admin = lanes_world.admin()
    assert admin.get(ADMIN_SOURCE).json() == {
        "provider": "hubspot",
        "configured": False,
        "settings": None,
    }
    response = admin.put(ADMIN_SOURCE, json=SETTINGS)
    assert response.status_code == 200, response.text
    after = document_of(lanes_world)
    crm = next(s for s in after["sources"] if s["id"] == "x_crm")
    assert crm["settings"]["status_mapping"] == MAPPING
    # only that source's settings moved: every other source, lane, and the quota stay
    assert [s for s in after["sources"] if s["id"] != "x_crm"] == [
        s for s in before["sources"] if s["id"] != "x_crm"
    ]
    assert after["lanes"] == before["lanes"] and after["llm"] == before["llm"]
    assert after["daily_action_limit"] == before["daily_action_limit"]
    assert lanes_world.config_store.current("alpha").author == "admin:root"
    assert admin.get(ADMIN_SOURCE).json()["configured"] is True
    assert "source" not in after  # no legacy section is written


def test_a_lane_with_hubspot_set_up_is_tested_and_ready(lanes_world, alpha):
    assert hubspot_lane(alpha).status_code == 201
    give_token(lanes_world)
    assert lanes_world.admin().put(ADMIN_SOURCE, json=SETTINGS).status_code == 200
    assert "not been tested" in source_message(alpha)
    body = alpha.post(TEST).json()
    assert body["ok"] is True and {r["lane"] for r in body["lanes"]} == {"main", "second", "crm"}
    assert len(lanes_world.net.hubspot_requests) == 1
    assert TOKEN not in str(body) and TOKEN not in alpha.get(SOURCE).text
    # the fake builders wire no HubSpot connector: `platform_connections` is the one unmet check
    unmet = {k for k, v in checks(alpha).items() if not v["ok"]}
    assert unmet <= {"platform_connections"}, unmet


def test_a_refused_hubspot_token_fails_that_lane_only(lanes_world, alpha):
    import httpx

    lanes_world.net.hubspot = lambda request: httpx.Response(401, json={})
    hubspot_lane(alpha)
    give_token(lanes_world)
    lanes_world.admin().put(ADMIN_SOURCE, json=SETTINGS)
    body = alpha.post(TEST).json()
    assert body["ok"] is False
    assert {r["lane"]: r["ok"] for r in body["lanes"]} == {
        "main": True,
        "second": True,
        "crm": False,
    }
    assert alpha.get(READINESS).json()["ready"] is False


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"status_mapping": {}},
        {"status_mapping": {"NEW": "active"}},
        {"status_mapping": MAPPING, "unknown_key": "x"},
        {"status_mapping": MAPPING, "status_property": "Bad Name"},
    ],
)
def test_an_invalid_mapping_publishes_nothing_and_leaves_the_lane_unset(
    lanes_world, alpha, settings
):
    hubspot_lane(alpha)
    before = len(history(lanes_world))
    assert lanes_world.admin().put(ADMIN_SOURCE, json=settings).status_code == 422
    assert len(history(lanes_world)) == before
    assert lanes_world.admin().get(ADMIN_SOURCE).json()["configured"] is False


def test_the_mapping_cannot_be_set_for_a_client_that_has_no_hubspot_lane(lanes_world):
    before = len(history(lanes_world))
    response = lanes_world.admin().put(ADMIN_SOURCE, json=SETTINGS)
    assert response.status_code == 409 and len(history(lanes_world)) == before


def test_the_client_can_never_write_a_mapping(lanes_world, alpha):
    body = {
        "expected_version": version(alpha),
        "id": "crm",
        "channel": "whatsapp",
        "products": ["c"],
        "source": {"id": "x_crm", "provider": "hubspot", "settings": SETTINGS},
    }
    before = len(history(lanes_world))
    assert alpha.post(LANES, json=body).status_code == 422  # no such field for a client
    assert hubspot_lane(alpha).status_code == 201
    forged = {"expected_version": version(alpha), "status_mapping": MAPPING}
    assert alpha.put(f"{LANES}/crm", json=forged).status_code == 422
    sheets = {"expected_version": version(alpha), "google_sheets": {"prospects_tab": "T"}}
    assert alpha.put(f"{LANES}/crm", json=sheets).status_code == 422
    assert len(history(lanes_world)) == before + 1  # only the lane's own creation
    assert "settings" not in next(
        s for s in document_of(lanes_world)["sources"] if s["id"] == "x_crm"
    )


def test_a_client_holds_one_hubspot_account(lanes_world, alpha):
    assert hubspot_lane(alpha).status_code == 201
    second = hubspot_lane(alpha, lane_id="crm2", source_id="x_crm2")
    assert second.status_code == 422
    assert "one account per client" in str(second.json())


def test_changing_another_lane_keeps_the_admins_mapping(lanes_world, alpha):
    hubspot_lane(alpha)
    lanes_world.admin().put(ADMIN_SOURCE, json=SETTINGS)
    alpha.put(f"{LANES}/main", json={"expected_version": version(alpha), "products": ["b"]})
    alpha.put(f"{LANES}/crm", json={"expected_version": version(alpha), "products": ["a", "b"]})
    crm = next(s for s in document_of(lanes_world)["sources"] if s["id"] == "x_crm")
    assert crm["settings"]["status_mapping"] == MAPPING


def test_one_clients_hubspot_never_reaches_another(lanes_world, alpha, beta):
    before = document_of(lanes_world, "beta")
    hubspot_lane(alpha)
    give_token(lanes_world)
    lanes_world.admin().put(ADMIN_SOURCE, json=SETTINGS)
    assert document_of(lanes_world, "beta") == before
    assert beta.get(SOURCE).json()["access"] is None
    assert stored_token(lanes_world, "beta") is None
    assert lanes_world.admin().get("/admin/v1/clients/beta/source").json()["provider"] == ""


# ---- the historical source routes for a client that runs lanes -------------------------------


def test_a_lane_client_has_no_token_to_give_whatever_its_sources(lanes_world, alpha):
    assert alpha.put(CREDENTIALS, json={"access_token": TOKEN}).status_code in (404, 405)
    assert stored_token(lanes_world) is None
    assert alpha.get(SOURCE).json()["access"] is None


def test_the_historical_source_route_reads_a_lane_client_but_does_not_write_it(lanes_world, alpha):
    body = alpha.get(SOURCE).json()
    assert body["google_sheets"] is None and body["runnable"] is True
    before = document_of(lanes_world)
    response = alpha.put(SOURCE, json={"expected_version": body["version"], "source": "hubspot"})
    assert response.status_code == 409 and document_of(lanes_world) == before


# ---- channels --------------------------------------------------------------------------------


def test_whatsapp_only_lanes_need_only_the_clients_number(lanes_world, alpha):
    ids = set(checks(alpha))
    assert "whatsapp_number" in ids and "channel_ready" not in ids


def test_every_channel_of_the_lanes_is_judged_on_its_own(lanes_world, alpha):
    allow_email(lanes_world)
    created = alpha.post(
        LANES,
        json={
            "expected_version": version(alpha),
            "id": "mail",
            "channel": "email",
            "products": ["c"],
            "source": {
                "id": "x_mail",
                "provider": "google_sheets",
                "google_sheets": {"spreadsheet": "sheet-mail"},
            },
        },
    )
    assert created.status_code == 201, created.text
    alpha.post(TEST)
    shown = checks(alpha)
    assert shown["whatsapp_number"]["ok"] is True  # the WhatsApp lanes are fine
    assert (
        shown["channel_ready"]["ok"] is False
        and "email settings" in shown["channel_ready"]["message"]
    )
    assert alpha.get(READINESS).json()["ready"] is False

    put = alpha.put(
        CHANNEL, json={"expected_version": version(alpha), "channel": "email", "email": MAIL}
    )
    assert put.status_code == 200, put.text
    assert "password is missing" in checks(alpha)["channel_ready"]["message"]
    assert (
        alpha.put(f"{CHANNEL}/credentials", json={"password": "S3cret-mail-pass!"}).status_code
        == 200
    )
    alpha.post(TEST)
    assert checks(alpha)["channel_ready"]["ok"] is True
    assert alpha.get(READINESS).json()["ready"] is True


def test_a_channel_the_admin_took_away_blocks_the_lane_client(lanes_world, alpha):
    allow_email(lanes_world)
    alpha.put(f"{LANES}/second", json={"expected_version": version(alpha), "channel": "email"})
    lanes_world.admin().put(GLOBAL, json={"allowed": ["whatsapp"]})
    shown = checks(alpha)
    assert (
        shown["channel_ready"]["ok"] is False
        and "not authorized" in shown["channel_ready"]["message"]
    )


def test_readiness_of_a_client_without_lanes_is_what_it_was(world):
    gamma = world.browser("gamma")
    assert set(checks(gamma)) == {
        "enabled_by_administrator",
        "loaded_by_platform",
        "model_assigned",
        "brain_assigned",
        "settings_valid",
        "source_connected",
        "platform_connections",
        "whatsapp_number",
    }
    assert gamma.post(TEST).json()["source"] == "google_sheets"
    assert "lanes" not in gamma.post(TEST).json() or gamma.post(TEST).json()["lanes"] is None
