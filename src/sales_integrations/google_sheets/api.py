"""The Google Sheets API, behind a tiny interface. The ONLY module that imports Google libraries.

`SheetsApi` is what the repository needs and nothing more, so tests can replace Google with an
in-memory sheet, and the Google specifics (rate limits, retries, error mapping) live here.

Retry policy is deliberate. Writes to explicit ranges are idempotent, so they may be retried after
any transient failure. An *append* is not (a retry after an unseen success would add the rows
twice), so it is retried only on 429 (the request was refused, never applied).
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from google.auth.credentials import Credentials
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from sales_integrations.google_sheets.errors import SheetsApiError

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
_TRANSIENT = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0


class AccessTokenSource(Protocol):
    """Fresh OAuth access tokens for one client (`GoogleTokenSource`); raises when the person's
    authorisation is gone."""

    def access_token(self) -> str: ...


_TOKEN_LIFETIME = timedelta(minutes=5)  # how long this object trusts a token; the source caches


class _TokenSourceCredentials(Credentials):  # type: ignore[misc]
    """google-auth credentials that take every token from a source: nothing is stored here."""

    def __init__(self, source: AccessTokenSource) -> None:
        super().__init__()
        self._source = source

    def refresh(self, request: Any) -> None:
        try:
            self.token = self._source.access_token()
        except Exception as error:  # the source's own errors never carry a token
            raise RefreshError("the Google authorisation is not usable") from error
        self.expiry = datetime.now(UTC).replace(tzinfo=None) + _TOKEN_LIFETIME


@dataclass(frozen=True, slots=True)
class SheetMeta:
    titles: tuple[str, ...]
    timezone: str | None


@runtime_checkable
class SheetsApi(Protocol):
    """Everything the repository needs from a spreadsheet service."""

    def read(self, spreadsheet_id: str, ranges: Sequence[str]) -> list[list[list[Any]]]:
        """Values for each A1 range (dates as serial numbers, numbers unformatted)."""
        ...

    def write(self, spreadsheet_id: str, updates: Sequence[tuple[str, list[list[str]]]]) -> None:
        """Set values, stored as given (never parsed as formulas, dates or numbers)."""
        ...

    def append(self, spreadsheet_id: str, range_: str, rows: list[list[str]]) -> None:
        """Add rows after the last used row of the range's tab, stored as given."""
        ...

    def metadata(self, spreadsheet_id: str) -> SheetMeta: ...

    def add_tabs(self, spreadsheet_id: str, titles: Sequence[str]) -> None: ...


class GoogleSheetsApi:
    """`SheetsApi` on the Google Sheets v4 REST API."""

    def __init__(
        self,
        service: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._service = service
        self._sleep = sleep
        self._max_attempts = max_attempts

    @classmethod
    def from_service_account_file(
        cls,
        path: str,
        *,
        timeout_seconds: float | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> "GoogleSheetsApi":
        """`timeout_seconds` bounds every request (without it, the library's own 60 seconds apply),
        and `max_attempts` the tries of a transient failure: a caller that waits for the answer,
        such as a connection check, asks for a short timeout and one try."""
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            path, scopes=list(SCOPES)
        )
        if timeout_seconds is None:
            service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        else:
            import google_auth_httplib2
            import httplib2

            http = google_auth_httplib2.AuthorizedHttp(  # type: ignore[no-untyped-call]
                credentials, http=httplib2.Http(timeout=timeout_seconds)
            )
            service = build("sheets", "v4", http=http, cache_discovery=False)
        return cls(service, max_attempts=max_attempts)

    @classmethod
    def from_token_source(
        cls,
        source: AccessTokenSource,
        *,
        timeout_seconds: float | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> "GoogleSheetsApi":
        """The person's own authorisation (OAuth) instead of a service-account file."""
        credentials = _TokenSourceCredentials(source)
        if timeout_seconds is None:
            service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        else:
            import google_auth_httplib2
            import httplib2

            http = google_auth_httplib2.AuthorizedHttp(  # type: ignore[no-untyped-call]
                credentials, http=httplib2.Http(timeout=timeout_seconds)
            )
            service = build("sheets", "v4", http=http, cache_discovery=False)
        return cls(service, max_attempts=max_attempts)

    # ---- SheetsApi ------------------------------------------------------------------------

    def read(self, spreadsheet_id: str, ranges: Sequence[str]) -> list[list[list[Any]]]:
        def request() -> Any:
            return (
                self._service.spreadsheets()
                .values()
                .batchGet(
                    spreadsheetId=spreadsheet_id,
                    ranges=list(ranges),
                    valueRenderOption="UNFORMATTED_VALUE",
                    dateTimeRenderOption="SERIAL_NUMBER",
                )
            )

        response = self._execute(request, statuses=_TRANSIENT, network=True)
        return [vr.get("values", []) for vr in response.get("valueRanges", [])]

    def write(self, spreadsheet_id: str, updates: Sequence[tuple[str, list[list[str]]]]) -> None:
        if not updates:
            return
        body = {
            "valueInputOption": "RAW",
            "data": [{"range": r, "values": v, "majorDimension": "ROWS"} for r, v in updates],
        }

        def request() -> Any:
            return (
                self._service.spreadsheets()
                .values()
                .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            )

        self._execute(request, statuses=_TRANSIENT, network=True)

    def append(self, spreadsheet_id: str, range_: str, rows: list[list[str]]) -> None:
        if not rows:
            return

        def request() -> Any:
            return (
                self._service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=range_,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": rows, "majorDimension": "ROWS"},
                )
            )

        self._execute(request, statuses=frozenset({429}), network=False)

    def metadata(self, spreadsheet_id: str) -> SheetMeta:
        def request() -> Any:
            return self._service.spreadsheets().get(
                spreadsheetId=spreadsheet_id, fields="properties.timeZone,sheets.properties.title"
            )

        response = self._execute(request, statuses=_TRANSIENT, network=True)
        titles = tuple(s["properties"]["title"] for s in response.get("sheets", []))
        return SheetMeta(titles, response.get("properties", {}).get("timeZone"))

    def add_tabs(self, spreadsheet_id: str, titles: Sequence[str]) -> None:
        if not titles:
            return
        body = {"requests": [{"addSheet": {"properties": {"title": t}}} for t in titles]}

        def request() -> Any:
            return self._service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body)

        self._execute(request, statuses=frozenset({429}), network=False)

    # ---- internals ------------------------------------------------------------------------

    def _execute(
        self, request: Callable[[], Any], *, statuses: frozenset[int], network: bool
    ) -> Any:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return request().execute(num_retries=0)
            except HttpError as error:
                status = int(error.resp.status)
                if status in statuses and attempt < self._max_attempts:
                    self._sleep(_backoff(attempt))
                    continue
                raise SheetsApiError(_explain(status, error), status=status) from error
            except RefreshError:
                raise SheetsApiError(
                    "Google Sheets access must be authorised again", status=401
                ) from None
            except (OSError, HttpLib2Error):
                if network and attempt < self._max_attempts:
                    self._sleep(_backoff(attempt))
                    continue
                raise  # outcome unknown for a write: the caller must not assume it failed
        raise AssertionError("unreachable")  # pragma: no cover


def _backoff(attempt: int) -> float:
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)


def _explain(status: int, error: HttpError) -> str:
    reason = getattr(error, "reason", None) or str(error)
    hints = {
        403: "the spreadsheet is not shared with the service account, or the API is disabled",
        400: "the range is invalid; a tab in the configuration may not exist",
        404: "spreadsheet or tab not found (check the spreadsheet id and tab names)",
        429: "Google Sheets rate limit reached",
    }
    hint = f" ({hints[status]})" if status in hints else ""
    return f"Google Sheets API error {status}{hint}: {reason}"
