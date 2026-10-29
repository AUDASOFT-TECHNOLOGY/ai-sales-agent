"""The HubSpot adapter, against a mock transport: nothing here reaches the network.

What is proved: it only reads; it never returns a partial list; every failure is a fixed sentence
that carries neither the token nor anything HubSpot said; and the token is only ever in the
`Authorization` header.
"""

import json

import httpx
import pytest

from sales_integrations.hubspot import (
    HubSpotAuthError,
    HubSpotClient,
    HubSpotContact,
    HubSpotDataError,
    HubSpotRateLimitedError,
    HubSpotUnavailableError,
)

TOKEN = "pat-eu1-" + "12345678-1234-1234-1234-123456789abc"
PROPS = ("firstname", "email")
SECRET_WORDS = ("Leak Person", "leak@example.com", "internal-detail")


def contact(identifier="1", **properties):
    return {"id": identifier, "properties": {"firstname": "Ada", "email": "a@x.org", **properties}}


class Hub:
    """A scripted HubSpot: answers come from `script`, every request is kept."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(self, **kwargs) -> HubSpotClient:
        transport = httpx.MockTransport(self)
        return HubSpotClient(TOKEN, http=lambda: httpx.Client(transport=transport), **kwargs)


def reply(body, status=200, **headers):
    return httpx.Response(status, json=body, headers=headers)


class TestReading:
    def test_a_list_follows_the_cursor_to_the_end(self):
        hub = Hub(
            reply({"results": [contact("1")], "paging": {"next": {"after": "c1"}}}),
            reply({"results": [contact("2")], "paging": {"next": {"after": "c2"}}}),
            reply({"results": [contact("3")]}),
        )
        found = hub.client(page_size=1).list_contacts(PROPS)
        assert [c.id for c in found] == ["1", "2", "3"]
        assert [r.url.params.get("after") for r in hub.requests] == [None, "c1", "c2"]
        assert all(r.url.params["properties"] == "firstname,email" for r in hub.requests)

    def test_a_page_that_fails_fails_the_whole_read_never_a_partial_list(self):
        hub = Hub(
            reply({"results": [contact("1")], "paging": {"next": {"after": "c1"}}}),
            reply({}, 500),
        )
        with pytest.raises(HubSpotUnavailableError):
            hub.client().list_contacts(PROPS)

    def test_a_cursor_that_does_not_move_is_an_error_not_an_endless_read(self):
        hub = Hub(reply({"results": [], "paging": {"next": {"after": "same"}}}))
        with pytest.raises(HubSpotDataError):
            hub.client().list_contacts(PROPS)
        assert len(hub.requests) == 2

    def test_a_list_that_never_ends_is_stopped(self):
        pages = [reply({"results": [], "paging": {"next": {"after": str(i)}}}) for i in range(5)]
        with pytest.raises(HubSpotDataError):
            Hub(*pages).client(max_pages=3).list_contacts(PROPS)

    def test_one_contact_by_id(self):
        hub = Hub(reply(contact("42", firstname="Grace")))
        found = hub.client().get_contact("42", PROPS)
        assert found.id == "42" and found.properties["firstname"] == "Grace"
        assert hub.requests[0].url.path == "/crm/v3/objects/contacts/42"

    def test_an_unknown_contact_is_none(self):
        assert Hub(reply({}, 404)).client().get_contact("42", PROPS) is None

    @pytest.mark.parametrize("bad", ["", "abc", "1/../2", "1?x=y", "٤٢", " 1", "1" * 21])
    def test_an_id_that_is_not_hubspots_never_reaches_the_network(self, bad):
        hub = Hub(reply(contact()))
        assert hub.client().get_contact(bad, PROPS) is None
        assert hub.requests == []

    def test_a_search_by_email_is_the_only_post_and_it_reads(self):
        hub = Hub(reply({"results": [contact("7")]}))
        found = hub.client().find_by_email(" Ada@X.org ", PROPS)
        assert [c.id for c in found] == ["7"]
        request = hub.requests[0]
        assert (request.method, request.url.path) == ("POST", "/crm/v3/objects/contacts/search")
        sent = json.loads(request.content)
        assert sent["filterGroups"][0]["filters"][0] == {
            "propertyName": "email",
            "operator": "EQ",
            "value": "Ada@X.org",
        }

    def test_verify_reads_one_contact_id(self):
        hub = Hub(reply({"results": []}))
        hub.client().verify()
        assert hub.requests[0].url.params["limit"] == "1"

    def test_properties_that_are_not_text_become_none(self):
        hub = Hub(reply({"results": [contact("1", email=None, phone=5)]}))
        (found,) = hub.client().list_contacts(PROPS)
        assert found.properties["email"] is None and found.properties["phone"] is None

    def test_a_numeric_id_is_text(self):
        (found,) = Hub(reply({"results": [{"id": 9, "properties": {}}]})).client().list_contacts(())
        assert found.id == "9"


class TestItOnlyReads:
    def test_the_only_way_to_write_is_one_bounded_update(self):
        public = {n for n in dir(HubSpotClient) if not n.startswith("_")}
        assert public == {
            "list_contacts",
            "get_contact",
            "find_by_email",
            "verify",
            "get_property",
            "update_contact",
        }

    def test_a_client_built_without_writable_properties_cannot_write(self):
        hub = Hub(reply({}))
        with pytest.raises(ValueError):
            hub.client().update_contact("1", {"hs_lead_status": "UNQUALIFIED"})
        assert hub.requests == []  # refused before any request

    def test_every_request_is_a_get_but_the_search(self):
        listing = reply({"results": [contact("1")]})
        hub = Hub(listing, reply(contact("1")), reply({"results": []}), listing)
        client = hub.client()
        client.list_contacts(PROPS)
        client.get_contact("1", PROPS)
        client.verify()
        client.find_by_email("a@x.org", PROPS)
        assert [r.method for r in hub.requests] == ["GET", "GET", "GET", "POST"]


class TestFailuresAreFixedSentences:
    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (401, HubSpotAuthError),
            (403, HubSpotAuthError),
            (429, HubSpotRateLimitedError),
            (500, HubSpotUnavailableError),
            (503, HubSpotUnavailableError),
            (400, HubSpotDataError),
            (409, HubSpotDataError),
        ],
    )
    def test_each_status_is_its_own_error(self, status, error):
        body = {"message": "internal-detail", "email": "leak@example.com"}
        with pytest.raises(error) as caught:
            Hub(reply(body, status)).client().verify()
        assert not any(word in str(caught.value) for word in (*SECRET_WORDS, TOKEN))

    def test_the_rate_limit_says_how_long_to_wait(self):
        with pytest.raises(HubSpotRateLimitedError) as caught:
            Hub(reply({}, 429, **{"Retry-After": "12"})).client().verify()
        assert caught.value.retry_after == 12.0

    @pytest.mark.parametrize("value", ["soon", "-1", "999999"])
    def test_a_retry_after_that_makes_no_sense_is_ignored(self, value):
        with pytest.raises(HubSpotRateLimitedError) as caught:
            Hub(reply({}, 429, **{"Retry-After": value})).client().verify()
        assert caught.value.retry_after is None

    @pytest.mark.parametrize(
        "failure", [httpx.ConnectTimeout("t"), httpx.ConnectError("refused by leak@example.com")]
    )
    def test_a_network_failure_never_repeats_what_it_said(self, failure):
        with pytest.raises(HubSpotUnavailableError) as caught:
            Hub(failure).client().verify()
        assert "leak@example.com" not in str(caught.value) and caught.value.__cause__ is None
        assert caught.value.__suppress_context__

    @pytest.mark.parametrize(
        "answer",
        [
            httpx.Response(200, text="<html>not json</html>"),
            reply(["not", "an", "object"]),
            reply({"results": "nope"}),
            reply({"results": ["nope"]}),
            reply({"results": [{"id": "", "properties": {}}]}),
            reply({"results": [{"id": "1", "properties": []}]}),
            reply({"results": [], "paging": "nope"}),
            reply({"results": [], "paging": {"next": {"after": 5}}}),
            reply({"results": [], "paging": {"next": "nope"}}),
        ],
    )
    def test_an_answer_that_is_not_the_documented_shape_is_refused(self, answer):
        with pytest.raises(HubSpotDataError):
            Hub(answer).client().list_contacts(PROPS)


class TestTheTokenStaysInTheHeader:
    def test_it_is_sent_as_a_bearer_token_and_nowhere_else(self):
        hub = Hub(reply({"results": [contact("1")]}))
        client = hub.client()
        client.list_contacts(PROPS)
        client.find_by_email("a@x.org", PROPS)
        for request in hub.requests:
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            assert TOKEN not in str(request.url)
            assert TOKEN.encode() not in request.content

    def test_it_is_not_in_a_repr(self):
        client = HubSpotClient(TOKEN)
        assert TOKEN not in repr(client) and TOKEN not in str(client)

    def test_a_blank_token_is_refused_without_echoing_it(self):
        with pytest.raises(ValueError) as caught:
            HubSpotClient("   ")
        assert "   " not in str(caught.value).replace("a HubSpot access token is required", "")

    def test_a_contact_shows_only_its_id(self):
        shown = repr(HubSpotContact("5", {"email": "leak@example.com", "firstname": "Leak Person"}))
        assert shown == "HubSpotContact(id='5')"
