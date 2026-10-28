"""Testing the agent on a person of the client's choice, through the Client API.

SIMULATED: the messaging channel (a fake that delivers nothing), the Brain, the login, the repository
(in memory). REAL: the application, the routes and the Engine, with its safety checks and its quota."""

import pytest
from test_client_agent_api import World

TESTS = "/client/v1/agent-test"
PHONE = "+21655123456"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.application.close()


def channel_of(world, cid="alpha"):
    return world.made.channels[cid]


class TestAccess:
    def test_every_route_needs_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.post(TESTS, json={"contact": PHONE}).status_code == 401
        assert anonymous.get(TESTS).status_code == 401
        assert anonymous.get(f"{TESTS}/inbound-test-1").status_code == 401

    def test_a_prospect_that_is_not_a_test_is_not_found(self, world):
        response = world.browser("alpha").get(f"{TESTS}/p1")
        assert response.status_code == 404


class TestStarting:
    def test_the_agent_writes_to_the_contact_now_as_it_would_to_a_prospect(self, world):
        alpha = world.browser("alpha")
        response = alpha.post(TESTS, json={"contact": PHONE, "name": "Amel"})
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["state"] == "sent" and body["prospect_id"].startswith("inbound-test-")
        (sent,) = channel_of(world).sent
        assert sent.recipient == PHONE
        shown = alpha.get(f"{TESTS}/{body['prospect_id']}").json()
        assert [m["direction"] for m in shown["messages"]] == ["outbound"]
        assert shown["test"]["name"] == "Amel" and shown["test"]["messages"] == 1

    def test_the_test_is_listed(self, world):
        alpha = world.browser("alpha")
        alpha.post(TESTS, json={"contact": PHONE})
        (listed,) = alpha.get(TESTS).json()["tests"]
        assert listed["contact"] == PHONE and listed["replies"] == 0

    def test_the_same_contact_cannot_be_tested_twice(self, world):
        alpha = world.browser("alpha")
        alpha.post(TESTS, json={"contact": PHONE})
        again = alpha.post(TESTS, json={"contact": PHONE})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "contact_in_use"
        assert len(channel_of(world).sent) == 1

    @pytest.mark.parametrize("contact", ["abc", "123", "a@b", "+" + "9" * 20])
    def test_a_contact_the_channel_cannot_use_is_refused_before_anything_is_sent(
        self, world, contact
    ):
        response = world.browser("alpha").post(TESTS, json={"contact": contact})
        assert response.status_code == 422 or (
            response.status_code == 409 and response.json()["detail"]["code"] == "invalid_contact"
        )
        assert channel_of(world).sent == ()

    def test_it_uses_the_days_quota(self, world):
        alpha = world.browser("alpha")
        with world.application.registry.get("alpha").lease() as engine:
            limit = engine._config.daily_action_limit
        for n in range(limit):
            alpha.post(TESTS, json={"contact": f"+2165512{n:04d}"})
        over = alpha.post(TESTS, json={"contact": "+21699999999"})
        assert over.status_code == 201 and over.json()["state"] == "waiting"
        assert len(channel_of(world).sent) == limit

    def test_a_bad_request_is_refused(self, world):
        alpha = world.browser("alpha")
        assert alpha.post(TESTS, json={}).status_code == 422
        assert alpha.post(TESTS, json={"contact": PHONE, "channel": "email"}).status_code == 422
