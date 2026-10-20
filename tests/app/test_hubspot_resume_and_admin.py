# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Resuming failed synchronisations without duplicates, and the Admin's HubSpot screen API.

Resumption: a write that failed waits out a back-off; the Admin's resume lifts that wait for failures
(never for a person's overruling), and since every write is recomputed from the source and names the
value it replaces, a resume after success, or twice in a row, writes nothing again. Doubles only: no
real HubSpot."""

from test_client_source_api import alpha, choose, world  # noqa: F401 - fixtures
from test_writeback_integration import PHONE_1, A, Client, database  # noqa: F401

from sales_app.sourcing import SourceUnavailableError
from sales_app.sourcing.writeback import Outcome
from sales_core.contracts import ProspectId, ProspectStatus

S = ProspectStatus
MAPPING = {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"}
SETTINGS = {"status_property": "hs_lead_status", "status_mapping": MAPPING}
BASE = "/admin/v1/clients/alpha/hubspot"


def make(database):
    tmp_path, url = database
    return Client(tmp_path, url, A)


def test_resume_retries_a_deferred_write_at_once_and_only_once(database):
    client = make(database)
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.CLOSED)
    client.source.fail_next("update_prospects", SourceUnavailableError("down"))
    assert client.syncer.run_once().deferred == 1
    assert client.syncer.run_once().held_back == 1  # waiting out the back-off
    resumed = client.syncer.run_once(retry_held=True)
    assert resumed.applied == 1 and client.in_source("1") is S.CLOSED
    again = client.syncer.run_once(retry_held=True)  # resuming twice: nothing is written again
    assert again.planned == 0 and len(client.writes_sent()) == 1
    assert client.ledger.latest(ProspectId(pid)).outcome is Outcome.APPLIED
    client.engine.dispose()


def test_resume_does_not_insist_against_a_person_who_changed_the_value(database):
    from dataclasses import replace

    client = make(database)
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.PAUSED)
    client.source.human_edit(replace(client.source.records["1"], status=S.CLOSED))
    client.syncer.run_once(retry_held=True)
    assert client.in_source("1") is S.CLOSED and client.writes_sent() == []
    client.engine.dispose()


# ---- the Admin API ----------------------------------------------------------------------------


def test_it_needs_an_admin_session(world):
    anonymous = world.anonymous()
    assert anonymous.get(BASE + "/health").status_code == 401
    assert anonymous.post(BASE + "/resume-sync").status_code == 401
    assert anonymous.post(BASE + "/status-mapping/check", json=SETTINGS).status_code == 401


def test_health_says_a_client_without_hubspot_has_nothing_to_show(world, alpha):
    assert world.admin().get(BASE + "/health").json()["uses_hubspot"] is False


def test_health_tells_what_is_missing_and_who_acts(world, alpha):
    choose(alpha, "hubspot")
    body = world.admin().get(BASE + "/health")
    assert body.headers["cache-control"] == "no-store"
    data = body.json()
    assert data["uses_hubspot"] is True
    assert data["statuses"]["configured"] is False
    assert data["connection"]["state"] in {"not_connected", "unavailable"}
    assert data["can_resume"] is False
    assert world.admin().put("/admin/v1/clients/alpha/source", json=SETTINGS).status_code == 200
    statuses = world.admin().get(BASE + "/health").json()["statuses"]
    assert statuses["configured"] is True
    assert statuses["values"]["closed"] == ["UNQUALIFIED"]
    assert statuses["values"]["paused"] == ["BAD_TIMING"]


def test_a_revoked_authorisation_is_shown_with_the_guided_reconnection(world, alpha):
    from sales_app.config.secrets import HUBSPOT_REAUTH_REQUIRED
    from sales_app.secret_store import client_scope

    choose(alpha, "hubspot")
    access = world.application.hubspot_access
    access.grants.save("alpha", "refresh-token", "acct-1")
    access.grants.mark_reauth_required("alpha")
    data = world.admin().get(BASE + "/health").json()["connection"]
    assert data["state"] == "reauth_required" and data["action"] == "ask_client_to_reconnect"
    assert data["steps"] and data["client_path"].startswith("/client/")
    assert "refresh-token" not in str(data)
    assert world.application.credentials is not None and HUBSPOT_REAUTH_REQUIRED and client_scope
    refused = world.admin().post(BASE + "/resume-sync")
    assert refused.status_code == 409 and "Reconnect" in refused.json()["detail"]


def test_the_mapping_is_checked_by_the_servers_own_rules_without_saving(world, alpha):
    choose(alpha, "hubspot")
    ok = world.admin().post(BASE + "/status-mapping/check", json=SETTINGS).json()
    assert ok["ok"] is True
    bad = world.admin().post(
        BASE + "/status-mapping/check", json={"status_mapping": {"NEW": "active"}}
    ).json()
    assert bad["ok"] is False and bad["problems"]
    nothing_new = world.admin().post(
        BASE + "/status-mapping/check", json={"status_mapping": {"X": "closed"}}
    ).json()
    assert nothing_new["ok"] is False
    assert world.config_store.current("alpha").document.get("source", {}).get("settings") is None


def test_the_client_has_no_route_to_the_mapping_or_the_resume(world, alpha):
    for path in (BASE + "/health", BASE + "/resume-sync"):
        assert alpha.get(path).status_code in (401, 403, 404, 405)
    assert alpha.post(BASE + "/status-mapping/check", json=SETTINGS).status_code in (401, 403, 404)
