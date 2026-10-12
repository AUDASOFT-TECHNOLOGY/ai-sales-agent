"""Real, read-only connection checks for the two prospect sources a client can choose.

The services are replaced by doubles that answer every status the check must tell apart. What is
proved: a success needs a real answer of the documented shape (never a guessed one), a failure is a
fixed sentence, only reads are made, and no token, and nothing the service or the library said,
ever reaches the result.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from sales_app.connection_tests import check_google_sheets, check_hubspot

TOKEN = "pat-eu1-12345678-1234-1234-1234-123456789abc"


def hubspot_http(handler):
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return (lambda: httpx.Client(transport=httpx.MockTransport(respond))), seen


def everything_said(result) -> str:
    return json.dumps([result.ok, result.message, result.details])


class TestHubSpot:
    def test_a_token_that_may_read_contacts_is_accepted(self):
        http, seen = hubspot_http(lambda r: httpx.Response(200, json={"results": []}))
        result = check_hubspot(access_token=TOKEN, http=http)
        assert result.ok and "accepted" in result.message
        assert len(seen) == 1
        request = seen[0]
        assert request.method == "GET"  # read-only: nothing is created, changed or deleted
        assert request.url.host == "api.hubapi.com"
        assert request.url.path == "/crm/v3/objects/contacts"
        assert dict(request.url.params) == {"limit": "1", "properties": "hs_object_id"}
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in everything_said(result)

    def test_what_hubspot_returned_is_not_kept(self):
        body = {"results": [{"id": "1", "properties": {"email": "someone@example.com"}}]}
        http, _ = hubspot_http(lambda r: httpx.Response(200, json=body))
        result = check_hubspot(access_token=TOKEN, http=http)
        assert result.ok and "someone@example.com" not in everything_said(result)

    @pytest.mark.parametrize(
        ("status", "words"),
        [
            (401, "refused this access token"),
            (403, "may not read contacts"),
            (404, "answered 404"),
            (429, "rate limit"),
            (500, "answered with an error"),
            (503, "answered with an error"),
            (418, "answered 418"),
        ],
    )
    def test_every_refusal_is_a_failure_in_a_fixed_sentence(self, status, words):
        echo = {"message": f"bad token {TOKEN}", "category": "INVALID_AUTHENTICATION"}
        http, _ = hubspot_http(lambda r: httpx.Response(status, json=echo))
        result = check_hubspot(access_token=TOKEN, http=http)
        assert not result.ok and words in result.message
        assert result.details["status"] == str(status)
        assert result.details["code"] == "INVALID_AUTHENTICATION"
        assert TOKEN not in everything_said(result)  # the service's own words are never relayed

    def test_a_category_that_is_not_a_plain_code_is_dropped(self):
        http, _ = hubspot_http(lambda r: httpx.Response(401, json={"category": f"x {TOKEN}"}))
        result = check_hubspot(access_token=TOKEN, http=http)
        assert "code" not in result.details and TOKEN not in everything_said(result)

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="<html>maintenance</html>"),
            httpx.Response(200, json={"results": "none"}),
            httpx.Response(200, json={"other": []}),
            httpx.Response(200, json=[1, 2]),
        ],
        ids=["not json", "results not a list", "no results", "a list"],
    )
    def test_a_success_status_without_the_documented_shape_is_not_a_success(self, response):
        http, _ = hubspot_http(lambda r: response)
        result = check_hubspot(access_token=TOKEN, http=http)
        assert not result.ok and "not with a contact list" in result.message

    def test_a_timeout_is_a_failure(self):
        def slow(request):
            raise httpx.ReadTimeout(f"timed out for {TOKEN}")

        http, _ = hubspot_http(slow)
        result = check_hubspot(access_token=TOKEN, http=http)
        assert not result.ok and "did not answer in time" in result.message
        assert TOKEN not in everything_said(result)

    def test_an_unreachable_service_is_a_failure_that_names_only_the_kind_of_error(self):
        def down(request):
            raise httpx.ConnectError(f"cannot connect with {TOKEN}")

        http, _ = hubspot_http(down)
        result = check_hubspot(access_token=TOKEN, http=http)
        assert not result.ok and "Could not reach HubSpot (ConnectError)" in result.message
        assert TOKEN not in everything_said(result)

    @pytest.mark.parametrize("token", [None, ""])
    def test_without_a_token_nothing_is_sent(self, token):
        http, seen = hubspot_http(lambda r: httpx.Response(200, json={"results": []}))
        result = check_hubspot(access_token=token, http=http)
        assert not result.ok and "not configured" in result.message and seen == []


class SheetsError(Exception):
    """What the Sheets adapter raises: a message that may carry anything, and a status."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Probe:
    def __init__(self, titles=("Prospects", "Actions"), raises=None, meta=None):
        self.calls: list[str] = []
        self._titles, self._raises, self._meta = titles, raises, meta

    def metadata(self, spreadsheet_id):
        self.calls.append(spreadsheet_id)
        if self._raises is not None:
            raise self._raises
        return self._meta if self._meta is not None else SimpleNamespace(titles=self._titles)


SECRET_PATH = "/srv/secrets/service-account-9f3.json"


class TestGoogleSheets:
    def test_a_shared_spreadsheet_with_its_prospects_tab_is_accepted(self):
        probe = Probe()
        result = check_google_sheets(api=probe, spreadsheet_id="sheet-1", prospects_tab="Prospects")
        assert result.ok and probe.calls == ["sheet-1"]  # the tab list only: no cell, no write

    def test_a_missing_prospects_tab_is_a_failure_that_says_which(self):
        result = check_google_sheets(api=Probe(), spreadsheet_id="s", prospects_tab="Leads")
        assert not result.ok and "prospects tab was not found" in result.message
        assert result.details == {"missing_tab": "Leads"}

    @pytest.mark.parametrize(
        ("status", "words"),
        [
            (400, "did not accept this spreadsheet id"),
            (401, "refused the platform's access"),
            (403, "not shared with the platform's address"),
            (404, "Spreadsheet not found"),
            (429, "rate limit"),
            (500, "answered with an error"),
            (503, "answered with an error"),
            (418, "answered 418"),
        ],
    )
    def test_every_refusal_is_a_failure_in_a_fixed_sentence(self, status, words):
        error = SheetsError(f"Google Sheets API error {status}: {SECRET_PATH}", status=status)
        result = check_google_sheets(
            api=Probe(raises=error), spreadsheet_id="s", prospects_tab="Prospects"
        )
        assert not result.ok and words in result.message
        assert result.details == {"status": str(status)}
        assert SECRET_PATH not in everything_said(result)

    def test_a_timeout_is_a_failure(self):
        result = check_google_sheets(
            api=Probe(raises=TimeoutError(SECRET_PATH)), spreadsheet_id="s", prospects_tab="P"
        )
        assert not result.ok and "did not answer in time" in result.message
        assert SECRET_PATH not in everything_said(result)

    @pytest.mark.parametrize("error", [OSError(SECRET_PATH), RuntimeError(SECRET_PATH)])
    def test_any_other_failure_names_only_its_kind(self, error):
        result = check_google_sheets(
            api=Probe(raises=error), spreadsheet_id="s", prospects_tab="Prospects"
        )
        assert not result.ok and f"({type(error).__name__})" in result.message
        assert SECRET_PATH not in everything_said(result)

    @pytest.mark.parametrize("meta", [SimpleNamespace(), SimpleNamespace(titles="Prospects")])
    def test_an_answer_without_a_tab_list_is_not_a_success(self, meta):
        result = check_google_sheets(
            api=Probe(meta=meta), spreadsheet_id="s", prospects_tab="Prospects"
        )
        assert not result.ok and "not with a list of tabs" in result.message

    def test_without_the_platforms_access_or_a_spreadsheet_nothing_is_read(self):
        probe = Probe()
        no_access = check_google_sheets(api=None, spreadsheet_id="s", prospects_tab="P")
        no_sheet = check_google_sheets(api=probe, spreadsheet_id="", prospects_tab="P")
        assert not no_access.ok and "contact your administrator" in no_access.message
        assert not no_sheet.ok and probe.calls == []
