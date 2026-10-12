"""A read-only HTTP client for the contacts of one HubSpot account (a Private App token).

It is bound to one token, so to one client's account. It **only reads**: there is no method that
creates, changes or deletes anything, so a read-only source is read-only by construction. Its one
`POST` is the contact search, which HubSpot makes a POST but which changes nothing.

It never returns a partial list: a page that fails fails the whole read. The token travels only in
the `Authorization` header; it is not in a URL, a `repr`, an error message or a log (nothing here
logs). A failure is a fixed sentence from `errors`, never what HubSpot or the HTTP library said.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from sales_integrations.hubspot.errors import (
    HubSpotAuthError,
    HubSpotDataError,
    HubSpotRateLimitedError,
    HubSpotUnavailableError,
)

BASE_URL = "https://api.hubapi.com"
CONTACTS_PATH = "/crm/v3/objects/contacts"
SEARCH_PATH = f"{CONTACTS_PATH}/search"
DEFAULT_TIMEOUT_SECONDS = 15.0
PAGE_SIZE = 100  # the most a listing page may hold
MAX_PAGES = 2000  # a runaway cursor is an error, not an endless read
MAX_RETRY_AFTER_SECONDS = 3600.0

_CONTACT_ID = re.compile(r"[0-9]{1,20}")

HttpClientFactory = Callable[[], httpx.Client]


@dataclass(frozen=True, slots=True, repr=False)
class HubSpotContact:
    """One contact as HubSpot holds it: its id and the properties that were asked for (text or
    None). It is personal data, so its `repr` shows nothing but the id."""

    id: str
    properties: Mapping[str, str | None]

    def __repr__(self) -> str:
        return f"HubSpotContact(id={self.id!r})"


class HubSpotClient:
    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http: HttpClientFactory | None = None,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
    ) -> None:
        if not access_token or not access_token.strip():
            raise ValueError("a HubSpot access token is required")
        if not 1 <= page_size <= PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {PAGE_SIZE}")
        self._headers = {"Authorization": f"Bearer {access_token.strip()}"}
        self._base_url = base_url.rstrip("/")
        self._http = http or (lambda: httpx.Client(timeout=timeout_seconds))
        self._page_size = page_size
        self._max_pages = max_pages

    def __repr__(self) -> str:
        return "HubSpotClient()"

    # ---- reading -------------------------------------------------------------------------

    def list_contacts(self, properties: Sequence[str]) -> list[HubSpotContact]:
        """Every contact (archived ones are not returned), page after page. All of it or an error."""
        found: list[HubSpotContact] = []
        after: str | None = None
        for _ in range(self._max_pages):
            params: dict[str, Any] = {"limit": self._page_size, "properties": ",".join(properties)}
            if after is not None:
                params["after"] = after
            body = self._request("GET", CONTACTS_PATH, params=params)
            found.extend(_contacts(body.get("results")))
            following = _next_cursor(body)
            if following is None:
                return found
            if following == after:  # a cursor that does not move would never end
                raise HubSpotDataError("HubSpot's contact list did not end")
            after = following
        raise HubSpotDataError("HubSpot's contact list did not end")

    def get_contact(self, contact_id: str, properties: Sequence[str]) -> HubSpotContact | None:
        """The contact with this id, or None if there is none (or the id is not one of HubSpot's)."""
        if not _CONTACT_ID.fullmatch(contact_id):
            return None
        body = self._request(
            "GET",
            f"{CONTACTS_PATH}/{contact_id}",
            params={"properties": ",".join(properties)},
            missing_ok=True,
        )
        if body is None:
            return None
        found = _contacts([body])
        return found[0] if found else None

    def find_by_email(self, email: str, properties: Sequence[str]) -> list[HubSpotContact]:
        """The contacts whose e-mail address is this one (HubSpot compares them without case)."""
        body = self._request(
            "POST",
            SEARCH_PATH,
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "email", "operator": "EQ", "value": email.strip()}
                        ]
                    }
                ],
                "properties": list(properties),
                "limit": 100,
            },
        )
        return _contacts(body.get("results"))

    def verify(self) -> None:
        """One contact id, read: succeeds only if the token is valid and may read contacts."""
        body = self._request(
            "GET", CONTACTS_PATH, params={"limit": 1, "properties": "hs_object_id"}
        )
        if not isinstance(body.get("results"), list):
            raise HubSpotDataError("HubSpot did not answer with a contact list")

    # ---- the one place a request is made ---------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        missing_ok: bool = False,
    ) -> Any:
        try:
            with self._http() as client:
                response = client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json,
                    headers=self._headers,
                )
        except httpx.HTTPError:  # a timeout, a refused connection...: never repeat what it said
            raise HubSpotUnavailableError("HubSpot could not be reached") from None
        status = response.status_code
        if status == 404 and missing_ok:
            return None
        if status in (401, 403):
            raise HubSpotAuthError("HubSpot refused the access token, or it may not read contacts")
        if status == 429:
            raise HubSpotRateLimitedError(retry_after=_retry_after(response))
        if status >= 500:
            raise HubSpotUnavailableError("HubSpot answered with an error")
        if not response.is_success:
            raise HubSpotDataError("HubSpot refused the request")
        try:
            body = response.json()
        except ValueError:
            raise HubSpotDataError("HubSpot did not answer with JSON") from None
        if not isinstance(body, dict):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        return body


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS else None


def _next_cursor(body: Mapping[str, Any]) -> str | None:
    paging = body.get("paging")
    if paging is None:
        return None
    if not isinstance(paging, dict):
        raise HubSpotDataError("HubSpot did not answer with the documented shape")
    follow = paging.get("next")
    if follow is None:
        return None
    cursor = follow.get("after") if isinstance(follow, dict) else None
    if not isinstance(cursor, str) or not cursor:
        raise HubSpotDataError("HubSpot did not answer with the documented shape")
    return cursor


def _contacts(items: Any) -> list[HubSpotContact]:
    """The contacts of a `results` list. One malformed item fails the whole read: it is never
    guessed at or skipped."""
    if not isinstance(items, list):
        raise HubSpotDataError("HubSpot did not answer with the documented shape")
    found = []
    for item in items:
        if not isinstance(item, dict):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        identifier = item.get("id")
        raw = item.get("properties")
        if isinstance(identifier, int) and not isinstance(identifier, bool):
            identifier = str(identifier)
        if not isinstance(identifier, str) or not identifier or not isinstance(raw, dict):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        found.append(
            HubSpotContact(
                identifier,
                {
                    k: v if isinstance(v, str) else None
                    for k, v in raw.items()
                    if isinstance(k, str)
                },
            )
        )
    return found
