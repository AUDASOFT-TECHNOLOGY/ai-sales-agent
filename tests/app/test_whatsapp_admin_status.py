"""`GET /admin/v1/clients/{id}/integrations/whatsapp/status`: what is stored for a client's
WhatsApp, read-only. Meta is never called here, so nothing says "connected".
"""

import dataclasses

import pytest
from test_client_agent_api import World

from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, client_scope
from sales_app.secret_store.catalog import GLOBAL_SCOPE

URL = "/admin/v1/clients/alpha/integrations/whatsapp/status"
PHONE_ID = "123456789012345"
TOKEN = "EAAG-whatsapp-token-marker-xyz"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=InMemorySecretAudit())
    yield w
    w.application.close()


def save(world, scope, key, value):
    world.application.credentials.save(scope, key, value, actor="test")


def set_number(world, number, reconnect=False):
    store = world.application.client_profiles
    profile = store.get("alpha")
    store.update(
        dataclasses.replace(profile, whatsapp_number=number, whatsapp_reconnect_required=reconnect)
    )


def fill_platform(world):
    save(world, GLOBAL_SCOPE, "whatsapp.app_secret", "app-secret-marker")
    save(world, GLOBAL_SCOPE, "whatsapp.verify_token", "verify-marker")
    save(world, client_scope("alpha"), "whatsapp.access_token", TOKEN)
    save(world, client_scope("alpha"), "whatsapp.phone_number_id", PHONE_ID)


def test_nothing_stored_waits_for_the_platform_and_names_only_keys(world):
    body = world.admin().get(URL).json()
    assert body["status"] == "waiting_platform" and body["verified_with_meta"] is False
    assert set(body["missing_platform"]) == {
        "whatsapp.app_secret",
        "whatsapp.verify_token",
        "whatsapp.access_token",
        "whatsapp.phone_number_id",
    }


def test_credentials_without_the_clients_number_need_the_client(world):
    fill_platform(world)
    set_number(world, None)
    body = world.admin().get(URL).json()
    assert body["status"] == "action_required" and body["whatsapp_number_given"] is False


def test_a_changed_number_needs_reconnecting(world):
    fill_platform(world)
    set_number(world, "+216 22 768 307", reconnect=True)
    assert world.admin().get(URL).json()["status"] == "action_required"


def test_everything_stored_is_ready_but_never_claimed_connected(world):
    fill_platform(world)
    set_number(world, "+216 22 768 307")
    response = world.admin().get(URL)
    body = response.json()
    assert body["status"] == "ready" and body["verified_with_meta"] is False
    assert body["missing_platform"] == []
    assert TOKEN not in response.text and PHONE_ID not in response.text


def test_a_client_cannot_read_it_and_an_unknown_client_is_not_found(world):
    assert world.browser("alpha").get(URL).status_code == 401
    assert world.admin().get(URL.replace("alpha", "nobody")).status_code == 404


def whatsapp_channel_status(world):
    from sales_app.channel_status import channel_status

    return channel_status(world.application, "alpha", "whatsapp", {})


def test_the_clients_channel_is_never_ready_without_the_meta_credentials(world):
    from sales_app.channel_status import WAITING_PLATFORM

    assert whatsapp_channel_status(world)[0] == WAITING_PLATFORM


@pytest.mark.parametrize(
    ("scope", "key"),
    [
        (GLOBAL_SCOPE, "whatsapp.app_secret"),
        (GLOBAL_SCOPE, "whatsapp.verify_token"),
        (client_scope("alpha"), "whatsapp.access_token"),
        (client_scope("alpha"), "whatsapp.phone_number_id"),
    ],
)
def test_one_missing_credential_keeps_the_channel_waiting(world, scope, key):
    from sales_app.channel_status import WAITING_PLATFORM

    fill_platform(world)
    world.application.credentials.remove(scope, key, actor="test")
    assert whatsapp_channel_status(world)[0] == WAITING_PLATFORM


def test_every_credential_stored_makes_the_channel_ready(world):
    from sales_app.channel_status import READY

    fill_platform(world)
    assert whatsapp_channel_status(world) == (READY, None)
