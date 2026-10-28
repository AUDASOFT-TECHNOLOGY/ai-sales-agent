"""What a client says about the accounts it wants to connect, before Revansa has finished its side.

The client keeps its Gmail, Telegram number, Google account and HubSpot account at any time; the
channels and sources it can choose on a circuit follow Revansa's side only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from test_client_agent_api import World
from test_telegram_channel_api import allow_telegram

from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore

BASE = "/client/v1/connections"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=InMemorySecretAudit())
    yield w
    w.application.close()


def test_a_session_is_required(world):
    anonymous = world.anonymous()
    assert anonymous.get(BASE).status_code == 401
    assert anonymous.put(f"{BASE}/hints/gmail", json={"value": "a@b.co"}).status_code == 401
    assert anonymous.delete(f"{BASE}/hints/gmail").status_code == 401


def test_the_client_keeps_what_it_says_before_revansa_is_ready(world):
    alpha = world.browser("alpha")
    assert alpha.get(BASE).json()["hints"] == {
        "gmail": None,
        "sheets": None,
        "hubspot": None,
        "telegram": None,
    }
    put = alpha.put(f"{BASE}/hints/gmail", json={"value": "  Sales@Naga.TN "})
    assert put.status_code == 200 and put.json()["hints"]["gmail"] == "sales@naga.tn"
    phone = alpha.put(f"{BASE}/hints/telegram", json={"value": "+216 20 123 456"})
    assert phone.json()["hints"]["telegram"] == "+21620123456"
    assert alpha.get(BASE).json()["hints"]["gmail"] == "sales@naga.tn"  # kept across requests


def test_a_hint_can_be_changed_and_forgotten(world):
    alpha = world.browser("alpha")
    alpha.put(f"{BASE}/hints/hubspot", json={"value": "a@naga.tn"})
    assert alpha.put(f"{BASE}/hints/hubspot", json={"value": "b@naga.tn"}).json()["hints"]["hubspot"] == "b@naga.tn"
    assert alpha.delete(f"{BASE}/hints/hubspot").json()["hints"]["hubspot"] is None


def test_what_is_not_an_address_or_a_number_is_refused_without_being_repeated(world):
    alpha = world.browser("alpha")
    refused = alpha.put(f"{BASE}/hints/gmail", json={"value": "not-an-address"})
    assert refused.status_code == 422 and "not-an-address" not in refused.text
    assert alpha.put(f"{BASE}/hints/telegram", json={"value": "hello"}).status_code == 422
    assert alpha.put(f"{BASE}/hints/unknown", json={"value": "a@b.co"}).status_code == 404
    assert alpha.put(f"{BASE}/hints/gmail", json={"value": "a@b.co", "token": "x"}).status_code == 422


def test_two_clients_never_see_each_others_hints(world):
    world.browser("alpha").put(f"{BASE}/hints/gmail", json={"value": "alpha@naga.tn"})
    assert world.browser("beta").get(BASE).json()["hints"]["gmail"] is None


def test_a_channel_revansa_has_not_finished_cannot_be_chosen_yet(world):
    alpha = world.browser("alpha")
    channels = alpha.get(BASE).json()["selectable"]["channels"]
    assert channels["whatsapp"] is False  # the platform's Meta application is not configured
    assert channels["telegram"] is False  # not opened to this client, nothing configured
    assert set(channels) == {"email", "whatsapp", "telegram"}


def test_a_channel_the_admin_opens_but_has_not_configured_stays_unavailable(world):
    allow_telegram(world.admin())
    channels = world.browser("alpha").get(BASE).json()["selectable"]["channels"]
    assert channels["telegram"] is False


def test_the_sources_follow_the_platforms_applications(world):
    sources = world.browser("alpha").get(BASE).json()["selectable"]["sources"]
    assert sources == {"google_sheets": False, "hubspot": False}
