"""The Google Sheets API wrapper, tested at the HTTP level (no network, no credentials)."""

import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

from sales_integrations.google_sheets import GoogleSheetsApi, SheetMeta, SheetsApi, SheetsApiError

SID = "sheet-1"


class FlakyHttp(HttpMockSequence):
    """Raises the queued exceptions (a dropped connection) before answering normally."""

    def __init__(self, responses, failures):
        super().__init__(responses)
        self.failures = list(failures)

    def request(self, *args, **kwargs):
        if self.failures:
            raise self.failures.pop(0)
        return super().request(*args, **kwargs)


def ok(body=None):
    return ({"status": "200"}, json.dumps(body if body is not None else {}))


def failure(status, message="nope"):
    return ({"status": str(status)}, json.dumps({"error": {"code": status, "message": message}}))


def make(*responses, failures=()):
    http = FlakyHttp(list(responses), failures)
    service = build("sheets", "v4", http=http, static_discovery=True)
    sleeps: list[float] = []
    return GoogleSheetsApi(service, sleep=sleeps.append), http, sleeps


def requests_of(http):
    """(method, path, query, json body) for each request the wrapper sent."""
    seen = []
    for uri, method, body, _ in http.request_sequence:
        parsed = urlparse(uri)
        seen.append(
            (
                method,
                unquote(parsed.path),
                parse_qs(parsed.query),
                json.loads(body) if body else None,
            )
        )
    return seen


def test_satisfies_the_interface():
    api, _, _ = make(ok())
    assert isinstance(api, SheetsApi)


class TestRequests:
    def test_read_asks_for_unformatted_values_and_serial_dates(self):
        api, http, _ = make(ok({"valueRanges": [{"values": [["a", 1]]}, {}]}))
        assert api.read(SID, ["'Prospects'", "'Log'!1:1"]) == [[["a", 1]], []]
        ((method, path, query, _),) = requests_of(http)
        assert method == "GET" and path.endswith(f"/spreadsheets/{SID}/values:batchGet")
        assert query["ranges"] == ["'Prospects'", "'Log'!1:1"]
        assert query["valueRenderOption"] == ["UNFORMATTED_VALUE"]
        assert query["dateTimeRenderOption"] == ["SERIAL_NUMBER"]

    def test_write_stores_values_raw_in_one_batch(self):
        api, http, _ = make(ok({}))
        api.write(SID, [("'Actions'!F2", [["approved"]]), ("'Actions'!A5:B5", [["x", "=1+1"]])])
        ((method, path, _, body),) = requests_of(http)
        assert method == "POST" and path.endswith(f"/spreadsheets/{SID}/values:batchUpdate")
        assert body["valueInputOption"] == "RAW"
        assert [d["range"] for d in body["data"]] == ["'Actions'!F2", "'Actions'!A5:B5"]
        assert body["data"][1]["values"] == [["x", "=1+1"]]

    def test_append_inserts_rows_and_stores_them_raw(self):
        api, http, _ = make(ok({}))
        api.append(SID, "'Messages'", [["m-1", "=SUM(1)"]])
        ((method, path, query, body),) = requests_of(http)
        assert method == "POST" and path.endswith(":append")
        assert query["valueInputOption"] == ["RAW"] and query["insertDataOption"] == ["INSERT_ROWS"]
        assert body["values"] == [["m-1", "=SUM(1)"]]

    def test_empty_writes_and_appends_send_nothing(self):
        api, http, _ = make()
        api.write(SID, [])
        api.append(SID, "'Log'", [])
        api.add_tabs(SID, [])
        assert http.request_sequence == []

    def test_metadata_reads_titles_and_time_zone(self):
        body = {
            "properties": {"timeZone": "Africa/Tunis"},
            "sheets": [{"properties": {"title": "A"}}, {"properties": {"title": "B"}}],
        }
        api, http, _ = make(ok(body))
        assert api.metadata(SID) == SheetMeta(("A", "B"), "Africa/Tunis")
        ((_, _, query, _),) = requests_of(http)
        assert "properties.timeZone" in query["fields"][0]

    def test_add_tabs_creates_sheets(self):
        api, http, _ = make(ok({}))
        api.add_tabs(SID, ["Actions", "Log"])
        ((_, path, _, body),) = requests_of(http)
        assert path.endswith(f"/spreadsheets/{SID}:batchUpdate")
        assert [r["addSheet"]["properties"]["title"] for r in body["requests"]] == [
            "Actions",
            "Log",
        ]


class TestRetries:
    def test_reads_are_retried_on_rate_limits_and_server_errors_with_growing_backoff(self):
        api, http, sleeps = make(
            failure(429), failure(503), ok({"valueRanges": [{"values": [["x"]]}]})
        )
        assert api.read(SID, ["'A'"]) == [[["x"]]]
        assert len(http.request_sequence) == 3 and sleeps == [1.0, 2.0]

    def test_reads_are_retried_after_a_dropped_connection(self):
        api, _http, sleeps = make(
            ok({"valueRanges": [{}]}), failures=[ConnectionResetError("reset")]
        )
        assert api.read(SID, ["'A'"]) == [[]]
        assert sleeps == [1.0]

    def test_giving_up_raises_a_readable_error_with_the_status(self):
        api, http, sleeps = make(*[failure(503)] * 5)
        with pytest.raises(SheetsApiError) as raised:
            api.read(SID, ["'A'"])
        assert raised.value.status == 503 and len(http.request_sequence) == 5 and len(sleeps) == 4

    def test_writes_to_explicit_ranges_are_idempotent_so_they_are_retried(self):
        api, http, _ = make(failure(500), ok({}))
        api.write(SID, [("'A'!A1", [["v"]])])
        assert len(http.request_sequence) == 2

    def test_an_append_is_retried_only_when_it_was_refused_with_429(self):
        api, http, sleeps = make(failure(429), ok({}))
        api.append(SID, "'Log'", [["row"]])
        assert len(http.request_sequence) == 2 and sleeps == [1.0]

    def test_an_append_that_may_have_been_applied_is_never_retried(self):
        """A retry after a lost success would add the rows twice."""
        api, http, _ = make(failure(500), ok({}))
        with pytest.raises(SheetsApiError):
            api.append(SID, "'Log'", [["row"]])
        assert len(http.request_sequence) == 1

    def test_a_dropped_connection_during_an_append_is_raised_not_retried(self):
        api, http, _ = make(ok({}), failures=[TimeoutError("read timed out")])
        with pytest.raises(TimeoutError):
            api.append(SID, "'Log'", [["row"]])
        assert http.request_sequence == []

    def test_creating_tabs_is_not_retried_on_server_errors(self):
        api, http, _ = make(failure(500), ok({}))
        with pytest.raises(SheetsApiError):
            api.add_tabs(SID, ["X"])
        assert len(http.request_sequence) == 1


class TestErrorsAreExplained:
    @pytest.mark.parametrize(
        ("status", "hint"),
        [
            (403, "shared with the service account"),
            (404, "check the spreadsheet id"),
            (400, "may not exist"),
        ],
    )
    def test_common_setup_mistakes_say_what_to_fix(self, status, hint):
        api, _, _ = make(failure(status, "reason text"))
        with pytest.raises(SheetsApiError, match=hint) as raised:
            api.read(SID, ["'A'"])
        assert raised.value.status == status and "reason text" in str(raised.value)


def test_a_missing_credentials_file_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        GoogleSheetsApi.from_service_account_file(str(tmp_path / "nope.json"))
